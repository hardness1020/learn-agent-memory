"""Engine: the integration point of the track. Grows one section at a time.

01 gave observe() its ledger, 02 gated proposals and logged decisions back
as events, 03 typed the survivors. Here (04) a stored record no longer just
appends: it lands on a timeline. A record that contradicts an active claim
closes that claim instead of sitting beside it, and every resolution
operation is logged to the ledger, so the timeline itself is replayable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ledger import Ledger
from policy import STORE, Decision, gate
from records import TypedStore, make_record
from resolve import as_of, resolve, valid_at


@dataclass
class Engine:
    ledger: Ledger
    classifier: Callable | None = None
    store: TypedStore = field(default_factory=TypedStore)

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Section 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)

    def propose(self, scope, candidate, epistemic_type="fact") -> Decision:
        """Sections 3 + 4 + 5: gate the candidate, type the survivor, then place
        it on the timeline. Every decision and every resolution operation
        becomes a ledger event."""
        decision = gate(candidate, existing=[r.content for r in self._records(scope)],
                        classifier=self.classifier,
                        log=lambda c, d: self.ledger.append(
                            scope, "memory_decision", f"{d.action}: {c.content}",
                            metadata={"reason": d.reason}))
        if decision.action == STORE:
            record = make_record(scope, candidate.kind, epistemic_type,
                                 candidate.content, candidate.source_event_ids,
                                 decision.confidence, claim_key=candidate.claim_key)
            for op in resolve(self.store, record):
                self.ledger.append(scope, "memory_operation", f"{op.op}: {op.target_id}",
                                   metadata={"reason": op.reason, "cause": op.cause_id})
        return decision

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
