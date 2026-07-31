"""Engine: the integration point of the track. Grows one section at a time.

Here (01) it satisfies one verb of the contract: observe() appends raw
evidence to the ledger, unconditionally. Later sections add the write gate
(02), typed records (03), the derived index (07), and recall (08).
"""
from __future__ import annotations

from dataclasses import dataclass

from ledger import Ledger


@dataclass
class Engine:
    ledger: Ledger

    def observe(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """Section 2: everything raw lands in the ledger before any judgment."""
        return self.ledger.append(scope, event_type, content, occurred_at, metadata)
