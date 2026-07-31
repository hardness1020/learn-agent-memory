"""Engine: the integration point of the track. Grows one section at a time.

01 gave observe() its ledger, 02 gated proposals and logged decisions back
as events. Here (03) the plain content list becomes a TypedStore: gate
survivors are constructed as validated MemoryRecords, carrying kind,
epistemic status, the decision's confidence, and their source event ids.
The observe() path of the contract is now complete: event → gate → record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ledger import Ledger
from policy import STORE, Decision, gate
from records import TypedStore, make_record


@dataclass
class Engine:
    ledger: Ledger
    classifier: Callable | None = None
    store: TypedStore = field(default_factory=TypedStore)

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Section 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)

    def propose(self, scope, candidate, epistemic_type="fact") -> Decision:
        """Sections 3 + 4: gate the candidate, then type the survivor.
        Every decision, rejections included, becomes a ledger event."""
        decision = gate(candidate, existing=[r.content for r in self._records(scope)],
                        classifier=self.classifier,
                        log=lambda c, d: self.ledger.append(
                            scope, "memory_decision", f"{d.action}: {c.content}",
                            metadata={"reason": d.reason}))
        if decision.action == STORE:
            self.store.add(make_record(scope, candidate.kind, epistemic_type,
                                       candidate.content, candidate.source_event_ids,
                                       decision.confidence))
        return decision

    def _records(self, scope):
        """Active records visible to one scope. Tenant filter is mandatory."""
        return [r for r in self.store.records
                if r.status == "active" and r.scope.tenant_id == scope.tenant_id
                and (scope.user_id is None or r.scope.user_id == scope.user_id)]
