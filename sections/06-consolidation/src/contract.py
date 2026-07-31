"""Memory contract (production memory track, section 1: Scope, plus the engine surface).

The root of the runnable chain. Every later section carries this file forward:
01 gives observe() its storage, 02 gates it, 03 types it, 07 indexes it,
08 completes recall(). consolidate() arrives with section 05.

Two things are contractual before any mechanism exists:
  scope  : whose memory this is. every write stamps it, every read filters
           by it. without scope, perfect retrieval can still leak one
           user's facts into another user's turn.
  engine : the whole subsystem behind three verbs. observe (raw evidence in),
           recall (evidence-backed context out), consolidate (background
           maintenance). callers never see the ten sections behind them.

And one rule the chain never breaks: raw events are never dropped, and
derived memories can always be rebuilt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Scope:
    """Whose memory this is. Frozen: a scope is an identity, not a mutable bag."""
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None


@runtime_checkable
class MemoryEngine(Protocol):
    """The engine surface. Conformance is structural: any object with these
    three methods is an engine, no inheritance required."""

    def observe(self, scope: Scope, event_type: str, content: str) -> str: ...

    def recall(self, scope: Scope, query: str) -> str: ...

    def consolidate(self, scope: Scope) -> dict: ...
