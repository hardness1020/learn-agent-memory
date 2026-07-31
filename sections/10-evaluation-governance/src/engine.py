"""Engine: the integration point of the track. Grows one section at a time.

01 gave observe() its ledger, 02 gated proposals, 03 typed the survivors,
04 placed them on a timeline, 05 merged what repeated, 06 derived the views,
07 fused them into one ranking, 08 budgeted and framed the block.
Here (09) the loop closes: recall logs what it injected, outcomes come back
as ordinary observations, and evaluate() scores the whole chain from the log.

The contract Protocol stays at three verbs, and section 10 adds none. What it
adds to the engine is one observation (feedback), one read (evaluate), and one
governance operation (retract) that goes through section 5's existing
non-destructive vocabulary rather than inventing a verb of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from assemble import BUDGET, Retrieved, assemble
from consolidate import consolidate as run_consolidation
from evaluate import (DECISION, INJECTED, OPERATION, OUTCOME, REJECTED, failing,
                      injection_metadata, injections, outcome_metadata)
from evaluate import report as scorecard
from index import TOP_K, MemoryIndex, profile
from ledger import Ledger
from policy import DEFER, STORE, Decision, gate
from records import TypedStore, make_record, validate
from resolve import as_of, resolve, valid_at
from resolve import retract as mark_retracted
from retrieve import retrieve as fuse_channels


@dataclass
class Engine:
    ledger: Ledger
    index: MemoryIndex
    classifier: Callable | None = None
    store: TypedStore = field(default_factory=TypedStore)

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Section 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)

    def propose(self, scope, candidate, epistemic_type="fact") -> Decision:
        """Sections 3 + 4 + 5: gate the candidate, type the survivor, then place
        it on the timeline. Every decision and every resolution operation
        becomes a ledger event."""
        # The action is a metadata field, not a prefix on the message. Section 10
        # counts decisions by action, and a count that parses the log line
        # breaks silently the day someone rewords it.
        decision = gate(candidate, existing=[r.content for r in self._records(scope)],
                        classifier=self.classifier,
                        log=lambda c, d: self.ledger.append(
                            scope, DECISION, c.content,
                            metadata={"action": d.action, "rule": d.rule,
                                      "reason": d.reason, "confidence": d.confidence}))
        if decision.action in (STORE, DEFER):
            # DEFER means "a similar memory exists, merge at consolidation", so the
            # candidate has to survive long enough for section 6 to see it. Dropping
            # it here would make the whole merge band unreachable.
            record = make_record(scope, candidate.kind, epistemic_type,
                                 candidate.content, candidate.source_event_ids,
                                 decision.confidence, claim_key=candidate.claim_key,
                                 tags=("deferred",) if decision.action == DEFER else ())
            for op in resolve(self.store, record):
                self._log_op(scope, op)
            self.reindex(scope)
        return decision

    def consolidate(self, scope, proposer=None) -> dict:
        """Section 6, and the contract's third verb. Runs off the query path:
        nothing here is needed to answer a turn, which is why it can afford a
        model call. Applied and rejected proposals both land in the ledger,
        and applied operations end in a reindex, so a record closed here is
        out of the view before the next query reads it."""
        at = datetime.now(timezone.utc).isoformat()
        report = run_consolidation(self.store, scope, at, proposer)
        for op in report["operations"]:
            self._log_op(scope, op)
        for content, reason in report["rejected"]:
            self.ledger.append(scope, REJECTED, content,
                               metadata={"reason": reason})
        if report["operations"]:
            self.reindex(scope)
        return report

    def reindex(self, scope) -> int:
        """Section 7's verb, wired to the write path: every pass that changes
        records calls it before returning, so the index is current before any
        query reads it. A full scope rebuild per write is the toy-scale warm
        path; a live system inserts and deletes only the changed rows.
        Safe to call at any time, because the index owns no original data,
        and it leaves every other scope's rows in place."""
        return self.index.rebuild(scope, [
            {"id": r.id, "tenant_id": r.scope.tenant_id, "user_id": r.scope.user_id,
             "kind": r.kind, "content": r.content, "recorded_at": r.recorded_at}
            for r in self._records(scope)])

    def between(self, scope, start, end) -> list[str]:
        """The temporal view for one scope: what it learned inside a window."""
        return self.index.between(scope, start, end)

    def retrieve(self, scope, query, k=TOP_K) -> list[tuple]:
        """Section 8: the hot path. Reads the view the write path maintains and
        fuses the channels over it. Nothing here writes or rebuilds."""
        return fuse_channels(self.index, scope, query, k)

    def recall(self, scope, query, budget=BUDGET) -> str:
        """Section 9, and the contract's last verb. Fused hits become evidence
        bundles, then one budgeted, labeled, guard-framed block. '' means
        inject nothing, which is the right answer more often than it looks.

        One ledger write happens here, on the hot path, and it is deliberate:
        an injection nobody recorded is a turn section 10 cannot score. It is a
        single append with no read, and it is the only warm work recall does.
        Empty turns are logged too, or "we chose not to inject" and "recall
        never ran" become the same row."""
        by_id = {r.id: r for r in self._records(scope)}
        found = self.retrieve(scope, query)
        competing = _competing([by_id[mid] for mid, _s, _c in found])
        hits = [Retrieved(mid, by_id[mid].content, by_id[mid].kind,
                          by_id[mid].epistemic_type, score, by_id[mid].confidence,
                          by_id[mid].recorded_at, by_id[mid].source_event_ids,
                          competing[mid])
                for mid, score, _channels in found]
        block, selected = assemble(hits, budget)
        self.ledger.append(scope, INJECTED, query,
                           metadata=injection_metadata(hits, selected))
        return block

    def feedback(self, scope, outcome) -> str:
        """Section 10's write side, and it is not a fourth verb: an outcome is an
        observation like any other, so it goes through observe() into the same
        append-only ledger. Nothing about grading memory needs its own store.

        An outcome with no injected ids binds to this scope's last injection.
        ponytail: that assumes one turn in flight per scope. A concurrent
        system passes a turn id through recall and quotes it back here."""
        outcome = replace(outcome, injected=tuple(outcome.injected)
                          or self.last_injected(scope))
        return self.observe(scope, OUTCOME, outcome.query,
                            metadata=outcome_metadata(outcome))

    def retract(self, scope, memory_id, reason) -> str:
        """Mark a memory wrong. Section 5 separates this from supersede: a
        supersede says the world moved on, a retraction says the claim was
        never true, so only this one grades the write that made it.

        This is section 10's one write that changes state, and it is what closes
        the loop: an outcome reports that a memory misled the answer, and this
        is the operation that acts on it. Without it `wrong_update_rate` counts
        a category nothing can produce, which is a metric that reads as a
        clean zero forever."""
        wrong = next((r for r in self._records(scope) if r.id == memory_id), None)
        if wrong is None:
            raise ValueError(f"no active memory to retract in this scope: {memory_id}")
        closed, op = mark_retracted(wrong, datetime.now(timezone.utc).isoformat(), reason)
        self.store.records[self.store.records.index(wrong)] = validate(closed)
        self.reindex(scope)
        return self._log_op(scope, op)

    def evaluate(self, scope, cases=()) -> dict:
        """Section 10's read side: four layers computed from the ledger and the
        records, plus whichever gates are currently failing. Owns no state, so
        it can be run at any time, over any window, and thrown away."""
        cases = [replace(c, found=tuple(mid for mid, _s, _c in self.retrieve(scope, c.query)))
                 for c in cases]
        scores = scorecard(self.ledger.read(scope), self._visible(scope), cases)
        return {**scores, "failing": failing(scores)}

    def profile(self, scope) -> dict:
        """The current-state view: one line per claim, no search."""
        return profile(self._records(scope))

    def believed_at(self, scope, when) -> list:
        """What this scope's memory would have said at a past moment.
        Superseded records come back; nothing was deleted to make room."""
        return as_of(self._visible(scope), when)

    def true_at(self, scope, when) -> list:
        """What was true in the world at a moment, on the other timeline."""
        return valid_at(self._visible(scope), when)

    def _log_op(self, scope, op) -> str:
        """One resolution or consolidation operation, as fields. The op name is
        metadata for the same reason the decision action is: section 10 counts
        them, and counting by string prefix is a metric with a typo in it."""
        return self.ledger.append(scope, OPERATION, op.target_id,
                                  metadata={"op": op.op, "reason": op.reason,
                                            "cause": op.cause_id})

    def last_injected(self, scope) -> tuple:
        """What the last turn put in the prompt. recall returns the block, not
        the ids, so this is how a caller learns which memories to report an
        outcome about. Public for that reason: building an Outcome is the
        normal thing to do next, not a look inside the engine."""
        turns = injections(self.ledger.read(scope, INJECTED))
        return turns[-1].memory_ids if turns else ()

    def _records(self, scope):
        """Active records visible to one scope."""
        return [r for r in self._visible(scope) if r.status == "active"]

    def _visible(self, scope):
        """Every record a scope may read, whatever its status. One filter, used
        by the live reads and by both audit queries: when the audit queries
        filtered on tenant alone, a per-user time-travel view returned every
        other user's memories inside that tenant."""
        return [r for r in self.store.records
                if r.scope.tenant_id == scope.tenant_id
                and (scope.user_id is None or r.scope.user_id == scope.user_id)]


def _competing(records) -> dict:
    """Which retrieved records make a competing claim, by claim key.

    Resolution (section 5) closes same-key conflicts at write time, so this is
    normally empty, and that is the point: it is not empty when a correction
    landed at a different scope level or before a key existed. Without it,
    `contradicts` was always (), so the conflict labels this section renders
    could never appear in real output and only its tests ever saw them."""
    by_key = {}
    for r in records:
        if r.claim_key:
            by_key.setdefault(r.claim_key, []).append(r.id)
    return {r.id: tuple(i for i in by_key.get(r.claim_key, ()) if i != r.id)
            for r in records}
