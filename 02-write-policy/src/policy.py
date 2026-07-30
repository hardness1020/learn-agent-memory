"""Write policy (production memory track, stage 3: Write gate).

Nothing becomes memory as a side effect. Every candidate gets an explicit
decision (store / ignore / defer / require_approval), a reason, and a log
entry, including the rejections. Stage 10 measures write precision from
that log, so an unlogged decision is an unmeasurable one.

The pipeline is layered by cost and trust:
  rules      : cheap, deterministic, first. evidence, derivability,
               duplicates, vagueness, sensitivity.
  classifier : one model call, only for what rules cannot settle
               (durability: useful next time, or smalltalk?).
  validator  : deterministic, checks every proposal before commit,
               whether a rule or an LLM produced it.

Section 9 gated writes with a type taxonomy inside the extractor prompt.
This stage makes the same judgment explicit, typed, and auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STORE, IGNORE, DEFER, REQUIRE_APPROVAL = "store", "ignore", "defer", "require_approval"
ACTIONS = (STORE, IGNORE, DEFER, REQUIRE_APPROVAL)

DUPLICATE_AT = 0.8      # share of candidate words already in one existing memory
SIMILAR_AT = 0.5        # below duplicate, above this: defer to consolidation
MIN_WORDS = 4           # anything shorter cannot be retrieved back reliably


@dataclass(frozen=True)
class Candidate:
    """A proposed memory, distilled from fresh ledger events (stage 2).
    source_event_ids is its evidence; a candidate without evidence is a rumor."""
    content: str
    kind: str                          # episodic / semantic / procedural
    source_event_ids: tuple = ()
    sensitive: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    confidence: float


def _no_evidence(c, existing):
    if not c.source_event_ids:
        return Decision(IGNORE, "no source events to cite", 1.0)


def _derivable(c, existing):
    # ponytail: a regex stands in for the real check; live systems ask the classifier
    if re.search(r"(^|\s)(def|class|import)\s|\w+/\w+\.\w+", c.content):
        return Decision(IGNORE, "grep or git can answer this again", 0.8)


def _duplicate(c, existing):
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)


def _vague(c, existing):
    if len(_words(c.content)) < MIN_WORDS:
        return Decision(IGNORE, "too vague to retrieve later", 0.8)


def _sensitive(c, existing):
    if c.sensitive:
        return Decision(REQUIRE_APPROVAL, "sensitive: a human confirms before it persists", 1.0)


RULES = (_no_evidence, _derivable, _duplicate, _vague, _sensitive)


def decide(candidate, existing=(), classifier=None) -> Decision:
    """Rules first, in cost order; the classifier only sees what rules cannot
    settle; store is the default when nothing objects. `existing` is the
    contents of already-stored memories, `classifier` the live LLM hook
    returning (action, reason, confidence)."""
    for rule in RULES:
        if decision := rule(candidate, existing):
            return decision
    if classifier is not None:
        return Decision(*classifier(candidate))
    return Decision(STORE, "novel, specific, evidence-backed", 0.6)


def validate(decision, candidate) -> Decision:
    """The deterministic check between any proposal and commit. An LLM can
    propose a decision; only a decision that passes here takes effect."""
    if decision.action not in ACTIONS:
        raise ValueError(f"unknown action: {decision.action}")
    if not decision.reason:
        raise ValueError("a decision without a reason cannot be audited")
    if not 0 <= decision.confidence <= 1:
        raise ValueError("confidence out of range")
    if decision.action == STORE and not candidate.source_event_ids:
        raise ValueError("store without source events")
    return decision


def gate(candidate, existing=(), classifier=None, log=None) -> Decision:
    """decide → validate → log. Every candidate leaves a logged decision,
    rejections included. In production the log target is the event ledger
    (stage 2), so gate decisions are themselves replayable evidence."""
    decision = validate(decide(candidate, existing, classifier), candidate)
    if log is not None:
        log(candidate, decision)
    return decision


def _overlap(text, other) -> float:
    words = _words(text)
    return len(words & _words(other)) / len(words) if words else 0.0


def _words(text) -> set:
    return {w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(w) > 2}
