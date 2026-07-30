"""Temporal resolution (production memory track, stage 5: Resolve).

Two questions this stage answers, and one it refuses to answer destructively:
  identity   : is "Marcus" the same subject as "Ming-Siang"?
  conflict   : is "lives in San Diego" against "lives in San Francisco" a
               contradiction, or two facts from different times?
  never      : whichever is wrong, the loser is not deleted.

Two timelines per record, not one:
  valid_from / valid_to        true in the world during this window
  recorded_at / superseded_at  known to the system during this window

That is bitemporal modeling. Keeping both means "where does Marcus live"
and "where did Marcus live last year" are the same query at two clocks,
and a bad extraction can be closed without erasing what it replaced.

Every operation is non-destructive:
  ADD        a new record enters, active
  SUPERSEDE  the old record closes, the new one opens
  RETRACT    the record is marked wrong, and stays readable
  ABSTRACT   many records feed one higher-level record (stage 6 emits these)

There is no UPDATE and no DELETE, here or in the ledger (stage 2).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from records import instant, validate

ADD, SUPERSEDE, RETRACT, ABSTRACT = "add", "supersede", "retract", "abstract"
OPS = (ADD, SUPERSEDE, RETRACT, ABSTRACT)


@dataclass(frozen=True)
class Operation:
    """What happened to one record, and why. Stage 6 proposes these through a
    validator; here they are produced deterministically and applied directly."""
    op: str
    target_id: str
    reason: str
    at: str
    cause_id: str = ""              # the record that caused it, for supersede


def conflicts(record, existing) -> list:
    """Active records making a different claim about the same thing.

    Two records conflict when they share a claim key and disagree on content.
    The claim key is the subject and predicate ("marcus:lives_in"); the content
    is the object. Same key, different object, both active means one of them
    stopped being true.

    ponytail: the key is set by the caller. Live systems derive it with an
    extractor, and resolve identity ("Marcus" vs "Ming-Siang") before comparing.
    """
    if not record.claim_key:
        return []                   # no key means no automatic conflict
    return [r for r in existing
            if r.claim_key == record.claim_key and r.status == "active"
            and r.id != record.id and r.content != record.content]


def supersede(record, at, cause_id="") -> tuple:
    """Close a record: it was true, it is not any more. valid_to and
    superseded_at both close, because the claim stopped being true in the
    world at the same moment the system learned it. When the two differ,
    the caller passes the world time separately."""
    closed = replace(record, status="superseded", superseded_at=at,
                     valid_to=record.valid_to or at)
    return closed, Operation(SUPERSEDE, record.id, "a newer claim replaces it", at, cause_id)


def retract(record, at, reason) -> tuple:
    """Mark a record wrong. Different from supersede: superseded means it used
    to be true, retracted means it never was. Both stay readable."""
    return (replace(record, status="retracted", superseded_at=at),
            Operation(RETRACT, record.id, reason, at))


def resolve(store, record, at=None) -> list:
    """Add a record and close whatever it contradicts, in one step.

    This is the write path stage 4 hands into: the record is already typed and
    validated, so all that is left is placing it on the timeline. Returns the
    operations performed, oldest first, which the engine logs to the ledger.
    """
    at = at or _now()
    ops = []
    for old in conflicts(record, same_scope(record.scope, store.records)):
        closed, op = supersede(old, at, cause_id=record.id)
        # validate() here, not just in store.add(): a supersede is an in-place
        # replacement, so it is the one write that could otherwise skip the
        # checks and strand a record no time-travel query can reach.
        store.records[store.records.index(old)] = validate(closed)
        ops.append(op)
    store.add(replace(record, valid_from=record.valid_from or at))
    ops.append(Operation(ADD, record.id, "new claim", at))
    return ops


def same_scope(scope, records) -> list:
    """Which records a claim is allowed to close. One tenant's claim never
    closes another tenant's, however identical the wording: scope isolation is
    a resolution rule, not just a read filter, because a supersede is a write.

    A missing user_id is a wildcard, matching how the read path resolves scope.
    If the two disagreed, a tenant-level correction would leave the user-level
    claim active and retrievable, with no supersede event to explain it."""
    return [r for r in records
            if r.scope.tenant_id == scope.tenant_id
            and (scope.user_id is None or r.scope.user_id == scope.user_id)]


def as_of(records, when) -> list:
    """What the system believed at a past moment: recorded by then, not yet
    superseded by then. This is the audit query, and it is why supersede
    writes a timestamp instead of dropping a row."""
    when = instant(when)
    return [r for r in records
            if instant(r.recorded_at) <= when
            and (r.superseded_at is None or instant(r.superseded_at) > when)]


def valid_at(records, when) -> list:
    """What was true in the world at a moment, which is a different question.
    A record recorded today can be valid_from last year."""
    when = instant(when)
    return [r for r in records
            if (r.valid_from is None or instant(r.valid_from) <= when)
            and (r.valid_to is None or instant(r.valid_to) > when)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
