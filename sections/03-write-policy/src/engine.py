"""Engine: the integration point of the track. Grows one section at a time.

01 gave observe() its ledger. Here (02) the write gate wires in: propose()
decides store / ignore / defer / require_approval, and the decision itself
goes back into the ledger as an event, so write precision (section 10) is
measurable from replayable data. Accepted contents sit in a plain list for
now; 03 turns them into typed records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ledger import Ledger
from policy import STORE, Decision, gate


@dataclass
class Engine:
    ledger: Ledger
    classifier: Callable | None = None
    stored: list = field(default_factory=list)      # contents only; 03 types them

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Section 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)

    def propose(self, scope, candidate) -> Decision:
        """Section 3: gate the candidate against what is already stored.
        Every decision, rejections included, becomes a ledger event."""
        decision = gate(candidate, existing=self.stored, classifier=self.classifier,
                        log=lambda c, d: self.ledger.append(
                            scope, "memory_decision", f"{d.action}: {c.content}",
                            metadata={"reason": d.reason}))
        if decision.action == STORE:
            self.stored.append(candidate.content)
        return decision
