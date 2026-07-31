"""Typed memory (production memory track, section 4: Encode).

One text bucket cannot answer three different questions:
  episodic   : what happened. keeps time and outcome.
  semantic   : what is currently believed. distilled, revisable.
  procedural : what to do next time. workflows, runbooks, gotchas.

A second axis records epistemic status: how the system knows it.
evidence and fact must never blend with inference and opinion, or the
agent states its own guesses as facts (Hindsight's split).

Every record cites source events (section 2). Validation rejects a record
with no evidence, an unknown kind or type, or an out-of-range confidence.
The store here is a list; the point is the type system. Section 7 gives
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


def instant(ts):
    """Parse a timestamp into a comparable moment. Both timelines are stored as
    ISO strings, and comparing those as text is wrong the moment one of them
    carries a non-UTC offset: "2025-06-01T04:00:00+05:00" sorts after
    "2025-06-01T01:00:00+00:00" as a string, but precedes it in time. Every
    comparison in this chain goes through here."""
    return datetime.fromisoformat(ts).astimezone(timezone.utc) if ts is not None else None


@dataclass(frozen=True)
class MemoryRecord:
    """Kind, epistemic status, and provenance travel together.

    Section 5 added the second timeline. recorded_at and superseded_at bound
    when the system believed this; valid_from and valid_to bound when it was
    true in the world. claim_key names what the record is a claim about, so
    two records can be recognised as competing rather than merely similar."""
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
    claim_key: str = ""                  # "marcus:lives_in". empty means unique claim
    valid_from: str | None = None        # true in the world since
    valid_to: str | None = None          # true in the world until
    superseded_at: str | None = None     # believed by the system until


def make_record(scope, kind, epistemic_type, content, source_event_ids,
                confidence, tags=(), claim_key="", valid_from=None) -> MemoryRecord:
    """The only constructor: every record is validated before it exists."""
    if isinstance(source_event_ids, str):
        # tuple("abc123") is ('a','b','c','1','2','3'), which validates fine and
        # prints six fabricated event ids into the prompt. observe() returns one
        # id, so passing it bare is the natural mistake to make.
        raise ValueError("source_event_ids is a sequence of ids, not one id string")
    return validate(MemoryRecord(uuid.uuid4().hex, scope, kind, epistemic_type,
                                 content, tuple(source_event_ids), confidence,
                                 datetime.now(timezone.utc).isoformat(), tags=tuple(tags),
                                 claim_key=claim_key, valid_from=valid_from))


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
    if record.valid_to is not None and record.valid_from is not None \
            and instant(record.valid_to) < instant(record.valid_from):
        raise ValueError("a record cannot stop being true before it started")
    if record.superseded_at is not None \
            and instant(record.superseded_at) < instant(record.recorded_at):
        raise ValueError("a record cannot be superseded before it was recorded")
    if record.status == "active" and record.superseded_at is not None:
        raise ValueError("an active record has no supersede time")
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
        return sorted(hits, key=lambda r: instant(r.recorded_at), reverse=True)
