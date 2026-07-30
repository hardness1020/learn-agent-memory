"""Engine: the integration point of the track. Grows one stage at a time,
the way loop.py grows through the sections.

01 gave observe() its ledger, 02 gated proposals, 03 typed the survivors,
04 placed them on a timeline, 05 merged what repeated. Here (06) the engine
gains derived views: a stored sparse index it can rebuild at will, and two
projections computed on read. Nothing in this stage is a new source of
truth, which is why every one of them can be thrown away.

observe() and consolidate() are complete. recall() arrives with 08.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from consolidate import consolidate as run_consolidation
from index import MemoryIndex, profile
from ledger import Ledger
from policy import DEFER, STORE, Decision, gate
from records import TypedStore, make_record
from resolve import as_of, resolve, valid_at


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
        return decision

    def consolidate(self, scope, proposer=None) -> dict:
        """Stage 6, and the contract's third verb. Runs off the query path:
        nothing here is needed to answer a turn, which is why it can afford a
        model call. Applied and rejected proposals both land in the ledger."""
        at = datetime.now(timezone.utc).isoformat()
        report = run_consolidation(self.store, scope, at, proposer)
        for op in report["operations"]:
            self.ledger.append(scope, "memory_operation", f"{op.op}: {op.target_id}",
                               metadata={"reason": op.reason, "cause": op.cause_id})
        for content, reason in report["rejected"]:
            self.ledger.append(scope, "consolidation_rejected", content,
                               metadata={"reason": reason})
        return report

    def reindex(self, scope) -> int:
        """Stage 7: rebuild the sparse view for one scope from records.
        Safe to call at any time, because the index owns no original data,
        and it leaves every other scope's rows in place."""
        return self.index.rebuild(scope, [
            {"id": r.id, "tenant_id": r.scope.tenant_id, "user_id": r.scope.user_id,
             "kind": r.kind, "content": r.content, "recorded_at": r.recorded_at}
            for r in self._records(scope)])

    def between(self, scope, start, end) -> list[str]:
        """The temporal view for one scope: what it learned inside a window."""
        return self.index.between(scope, start, end)

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
