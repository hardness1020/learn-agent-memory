"""Engine: the integration point of the track. Grows one stage at a time,
the way loop.py grows through the sections.

01 gave observe() its ledger, 02 gated proposals, 03 typed the survivors,
04 placed them on a timeline, 05 merged what repeated, 06 derived the views,
07 fused them into one ranking. Here (08) the last contract verb completes:
recall() budgets, labels, and frames what retrieval found, and returns ''
when nothing qualifies.

All three verbs now run end to end offline: observe, consolidate, recall.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from assemble import BUDGET, Retrieved, assemble
from consolidate import consolidate as run_consolidation
from index import TOP_K, MemoryIndex, profile
from ledger import Ledger
from policy import DEFER, STORE, Decision, gate
from records import TypedStore, make_record
from resolve import as_of, resolve, valid_at
from retrieve import retrieve as fuse_channels


@dataclass
class Engine:
    ledger: Ledger
    index: MemoryIndex
    classifier: Callable | None = None
    store: TypedStore = field(default_factory=TypedStore)

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Stage 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)

    def propose(self, scope, candidate, epistemic_type="fact") -> Decision:
        """Stages 3 + 4 + 5: gate the candidate, type the survivor, then place
        it on the timeline. Every decision and every resolution operation
        becomes a ledger event."""
        decision = gate(candidate, existing=[r.content for r in self._records(scope)],
                        classifier=self.classifier,
                        log=lambda c, d: self.ledger.append(
                            scope, "memory_decision", f"{d.action}: {c.content}",
                            metadata={"reason": d.reason}))
        if decision.action in (STORE, DEFER):
            # DEFER means "a similar memory exists, merge at consolidation", so the
            # candidate has to survive long enough for stage 6 to see it. Dropping
            # it here would make the whole merge band unreachable.
            record = make_record(scope, candidate.kind, epistemic_type,
                                 candidate.content, candidate.source_event_ids,
                                 decision.confidence, claim_key=candidate.claim_key,
                                 tags=("deferred",) if decision.action == DEFER else ())
            for op in resolve(self.store, record):
                self.ledger.append(scope, "memory_operation", f"{op.op}: {op.target_id}",
                                   metadata={"reason": op.reason, "cause": op.cause_id})
            self.reindex(scope)
        return decision

    def consolidate(self, scope, proposer=None) -> dict:
        """Stage 6, and the contract's third verb. Runs off the query path:
        nothing here is needed to answer a turn, which is why it can afford a
        model call. Applied and rejected proposals both land in the ledger,
        and applied operations end in a reindex, so a record closed here is
        out of the view before the next query reads it."""
        at = datetime.now(timezone.utc).isoformat()
        report = run_consolidation(self.store, scope, at, proposer)
        for op in report["operations"]:
            self.ledger.append(scope, "memory_operation", f"{op.op}: {op.target_id}",
                               metadata={"reason": op.reason, "cause": op.cause_id})
        for content, reason in report["rejected"]:
            self.ledger.append(scope, "consolidation_rejected", content,
                               metadata={"reason": reason})
        if report["operations"]:
            self.reindex(scope)
        return report

    def reindex(self, scope) -> int:
        """Stage 7's verb, wired to the write path: every pass that changes
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
        """Stage 8: the hot path. Reads the view the write path maintains and
        fuses the channels over it. Nothing here writes or rebuilds."""
        return fuse_channels(self.index, scope, query, k)

    def recall(self, scope, query, budget=BUDGET) -> str:
        """Stage 9, and the contract's last verb. Fused hits become evidence
        bundles, then one budgeted, labeled, guard-framed block. '' means
        inject nothing, which is the right answer more often than it looks."""
        by_id = {r.id: r for r in self._records(scope)}
        found = self.retrieve(scope, query)
        competing = _competing([by_id[mid] for mid, _s, _c in found])
        hits = [Retrieved(mid, by_id[mid].content, by_id[mid].kind,
                          by_id[mid].epistemic_type, score, by_id[mid].confidence,
                          by_id[mid].recorded_at, by_id[mid].source_event_ids,
                          competing[mid])
                for mid, score, _channels in found]
        return assemble(hits, budget)

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

    Resolution (stage 5) closes same-key conflicts at write time, so this is
    normally empty, and that is the point: it is not empty when a correction
    landed at a different scope level or before a key existed. Without it,
    `contradicts` was always (), so the conflict labels this stage renders
    could never appear in real output and only its tests ever saw them."""
    by_key = {}
    for r in records:
        if r.claim_key:
            by_key.setdefault(r.claim_key, []).append(r.id)
    return {r.id: tuple(i for i in by_key.get(r.claim_key, ()) if i != r.id)
            for r in records}
