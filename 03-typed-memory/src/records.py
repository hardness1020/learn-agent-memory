"""Typed memory (production memory track, stage 4: Encode).

One text bucket cannot answer three different questions:
  episodic   : what happened. keeps time and outcome.
  semantic   : what is currently believed. distilled, revisable.
  procedural : what to do next time. workflows, runbooks, gotchas.

A second axis records epistemic status: how the system knows it.
evidence and fact must never blend with inference and opinion, or the
agent states its own guesses as facts (Hindsight's split).

Every record cites source events (stage 2). Validation rejects a record
with no evidence, an unknown kind or type, or an out-of-range confidence.
The store here is a list; the point is the type system. Stage 7 gives
each kind its real index.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from contract import Scope

KINDS = ("episodic", "semantic", "procedural")
EPISTEMIC = ("evidence", "fact", "preference", "inference", "opinion")
STATUSES = ("active", "superseded", "retracted")
GROUNDED = ("evidence", "fact")          # observed or verified, not guessed


@dataclass(frozen=True)
class MemoryRecord:
    """Kind, epistemic status, and provenance travel together. The bitemporal
    fields (valid_from, valid_to, superseded_at) arrive with stage 5."""
    id: str
    scope: Scope
    kind: str
    epistemic_type: str
    content: str
    source_event_ids: tuple
    confidence: float
    recorded_at: str
    status: str = "active"
    tags: tuple = ()


def make_record(scope, kind, epistemic_type, content, source_event_ids,
                confidence, tags=()) -> MemoryRecord:
    """The only constructor: every record is validated before it exists."""
    return validate(MemoryRecord(uuid.uuid4().hex, scope, kind, epistemic_type,
                                 content, tuple(source_event_ids), confidence,
                                 datetime.now(timezone.utc).isoformat(), tags=tuple(tags)))


def validate(record) -> MemoryRecord:
    if record.kind not in KINDS:
        raise ValueError(f"unknown kind: {record.kind}")
    if record.epistemic_type not in EPISTEMIC:
        raise ValueError(f"unknown epistemic type: {record.epistemic_type}")
    if record.status not in STATUSES:
        raise ValueError(f"unknown status: {record.status}")
    if not record.source_event_ids:
        raise ValueError("a record without source events cannot cite its evidence")
    if not 0 <= record.confidence <= 1:
        raise ValueError("confidence out of range")
    return record


def grounded(records) -> list[MemoryRecord]:
    """Only what was observed or verified. Inference, preference, and opinion
    stay out, so downstream prompts can separate knowledge from guesses."""
    return [r for r in records if r.epistemic_type in GROUNDED]


@dataclass
class TypedStore:
    """Routing by kind, filtering by scope and status. In memory on purpose."""
    records: list = field(default_factory=list)

    def add(self, record) -> MemoryRecord:
        self.records.append(validate(record))
        return record

    def current(self, scope, kind) -> list[MemoryRecord]:
        """Active records of one kind for one scope, newest first."""
        hits = [r for r in self.records
                if r.status == "active" and r.kind == kind
                and r.scope.tenant_id == scope.tenant_id
                and (scope.user_id is None or r.scope.user_id == scope.user_id)]
        return sorted(hits, key=lambda r: r.recorded_at, reverse=True)
