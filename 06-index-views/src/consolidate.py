"""Consolidation (production memory track, stage 6: turn events into knowledge).

Consolidation is not summarization. It covers deduplication, entity
resolution, conflict detection, temporal invalidation, fact merging,
preference induction, workflow extraction, confidence updates, and forgetting.

Three levels, by how far the output travels from the input:
  compression   many similar memories  -> one shorter memory
  abstraction   many episodes          -> one rule or preference
  skill         many successful runs   -> one reusable procedure

The dangerous part is that a model decides what to merge and what to close.
So a model never writes to the store. It proposes, and a deterministic
validator decides:

    proposer (LLM or rule)  ->  Proposal
        -> check(): level, content, source count, sources exist,
                    sources still active, one scope only
        -> apply(): new derived record, sources superseded, ops returned

Coverage is structural rather than checked: apply() closes exactly the
records the proposal cited, and the derived record inherits their event ids.
A memory is never closed by something that forgot where it came from.

Raw evidence is untouchable regardless. Records are derived views over the
ledger (stage 2), so a bad consolidation pass is recoverable by replay.
"""
from __future__ import annotations

from dataclasses import dataclass

from records import make_record
from resolve import ABSTRACT, Operation, supersede

COMPRESSION, ABSTRACTION, SKILL = "compression", "abstraction", "skill"
LEVELS = (COMPRESSION, ABSTRACTION, SKILL)

MERGE_AT = 0.5          # jaccard overlap above which two memories say the same thing
MIN_SOURCES = 2         # an abstraction of one record is just a rewrite


@dataclass(frozen=True)
class Proposal:
    """What a consolidation pass wants to do. An LLM can produce these; only
    a proposal that passes check() ever reaches the store."""
    level: str
    content: str
    source_ids: tuple
    kind: str = "semantic"
    reason: str = ""


class Rejected(ValueError):
    """A proposal the validator refused. Carries the reason, because stage 10
    measures consolidation precision from the rejections too."""


def check(proposal, store) -> list:
    """Deterministic gate between any proposal and the store. Returns the
    source records it resolved, so apply() never looks them up again."""
    if proposal.level not in LEVELS:
        raise Rejected(f"unknown level: {proposal.level}")
    if not proposal.content.strip():
        raise Rejected("an abstraction with no content replaces evidence with nothing")
    if len(set(proposal.source_ids)) < MIN_SOURCES:
        raise Rejected("an abstraction of fewer than two records is a rewrite")

    by_id = {r.id: r for r in store.records}
    missing = [i for i in proposal.source_ids if i not in by_id]
    if missing:
        raise Rejected(f"cites records that do not exist: {','.join(sorted(missing))}")

    sources = [by_id[i] for i in proposal.source_ids]
    if any(r.status != "active" for r in sources):
        raise Rejected("cites a record that is already closed")

    scopes = {(r.scope.tenant_id, r.scope.user_id) for r in sources}
    if len(scopes) > 1:
        raise Rejected("merges records across scopes")
    return sources


def apply(proposal, store, at) -> list:
    """Commit a checked proposal: write the derived record, then close the
    sources it cites. Provenance is the union of the sources' event ids, so
    the new record still points back at raw evidence in the ledger."""
    sources = check(proposal, store)
    events = tuple(sorted({e for r in sources for e in r.source_event_ids}))
    derived = make_record(sources[0].scope, proposal.kind, "inference",
                          proposal.content, events,
                          min(r.confidence for r in sources),
                          tags=("consolidated", proposal.level))
    store.add(derived)
    ops = [Operation(ABSTRACT, derived.id, proposal.reason or proposal.level, at)]
    for old in sources:
        closed, op = supersede(old, at, cause_id=derived.id)
        store.records[store.records.index(old)] = closed
        ops.append(op)
    return ops


def consolidate(store, scope, at, proposer=None) -> dict:
    """Run one pass. Every proposal is checked; the rejected ones are reported,
    not silently dropped, because an unmeasured rejection is an invisible bug.

    `proposer` is the model seam: give it a callable taking the scope's active
    records and returning Proposals. The default is deterministic and offline.
    """
    records = [r for r in store.records
               if r.status == "active" and r.scope.tenant_id == scope.tenant_id
               and (scope.user_id is None or r.scope.user_id == scope.user_id)]
    proposals = (proposer or propose_compression)(records)

    ops, rejected = [], []
    for proposal in proposals:
        try:
            ops += apply(proposal, store, at)
        except Rejected as exc:
            rejected.append((proposal.content, str(exc)))
    return {"proposed": len(proposals), "applied": len(proposals) - len(rejected),
            "rejected": rejected, "operations": ops}


def propose_compression(records) -> list:
    """Level 1 only, and deterministic: group memories that say the same thing
    and propose one record in their place.

    ponytail: word overlap stands in for the real grouping. Abstraction and
    skill formation need a model to read the episodes, and that is exactly
    what the proposer seam is for. The validator does not care which produced
    a proposal, which is the point.
    """
    out, used = [], set()
    for i, record in enumerate(records):
        if record.id in used or record.kind != "semantic":
            continue
        group = [record] + [r for r in records[i + 1:]
                            if r.id not in used and r.kind == record.kind
                            and _overlap(record.content, r.content) >= MERGE_AT]
        if len(group) < MIN_SOURCES:
            continue
        used.update(r.id for r in group)
        out.append(Proposal(COMPRESSION, _shortest(group), tuple(r.id for r in group),
                            record.kind, "same claim stated several times"))
    return out


def _shortest(group) -> str:
    """The most compact wording already present. A real pass would rewrite;
    picking an existing sentence keeps this stage offline and checkable."""
    return min((r.content for r in group), key=len)


def _overlap(text, other) -> float:
    a, b = _words(text), _words(other)
    return len(a & b) / len(a | b) if a | b else 0.0


def _words(text) -> set:
    return {w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(w) > 2}
