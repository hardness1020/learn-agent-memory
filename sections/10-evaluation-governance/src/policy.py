"""Write policy (production memory track, section 3: Write gate).

Nothing becomes memory as a side effect. Every candidate gets an explicit
decision (store / ignore / defer / require_approval), a reason, and a log
entry, including the rejections. Section 10 measures write precision from
that log, so an unlogged decision is an unmeasurable one.

The pipeline is layered by cost and trust:
  rules      : cheap, deterministic, first. evidence, derivability,
               duplicates, vagueness, sensitivity.
  classifier : one model call, only for what rules cannot settle
               (durability: useful next time, or smalltalk?).
  validator  : deterministic, checks every proposal before commit,
               whether a rule or an LLM produced it.

Section 9 gated writes with a type taxonomy inside the extractor prompt.
This section makes the same judgment explicit, typed, and auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

STORE, IGNORE, DEFER, REQUIRE_APPROVAL = "store", "ignore", "defer", "require_approval"
ACTIONS = (STORE, IGNORE, DEFER, REQUIRE_APPROVAL)

DUPLICATE_AT = 0.8      # share of candidate words already in one existing memory
SIMILAR_AT = 0.5        # below duplicate, above this: defer to consolidation
MIN_WORDS = 3           # significant words, so this counts fewer than it used to


@dataclass(frozen=True)
class Candidate:
    """A proposed memory, distilled from fresh ledger events (section 2).
    source_event_ids is its evidence; a candidate without evidence is a rumor."""
    content: str
    kind: str                          # episodic / semantic / procedural
    source_event_ids: tuple = ()
    sensitive: bool = False
    claim_key: str = ""                # what this is a claim about (section 5)


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    confidence: float
    rule: str = ""          # which rule decided, for section 10. "" means the
                            # classifier or the default, neither of which is a rule


def _no_evidence(c, existing):
    if not c.source_event_ids:
        return Decision(IGNORE, "no source events to cite", 1.0)


def _derivable(c, existing):
    # ponytail: a regex stands in for the real check; live systems ask the classifier
    if re.search(r"(^|\s)(def|class|import)\s|\w+/\w+\.\w+", c.content):
        return Decision(IGNORE, "grep or git can answer this again", 0.8)


def _duplicate(c, existing):
    if c.claim_key:
        # A keyed claim competes with whatever currently holds its key, so word
        # overlap says nothing useful about it: "Marcus prefers dark mode in the
        # editor" and the same sentence with "light" overlap 0.875. Identical
        # wording is a duplicate; anything else is a correction for section 5.
        return Decision(IGNORE, "already known", 0.9) if c.content in existing else None
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)


def _vague(c, existing):
    if len(words(c.content)) < MIN_WORDS:
        return Decision(IGNORE, "too vague to retrieve later", 0.8)


def _sensitive(c, existing):
    if c.sensitive:
        return Decision(REQUIRE_APPROVAL, "sensitive: a human confirms before it persists", 1.0)


# Order matters, and sensitivity comes before every rule that can end in a
# store. Sitting last, it was unreachable for any candidate that first matched
# the near-duplicate band, so sensitive content persisted without the approval
# the rule exists to demand. A trust boundary is checked first or not at all.
RULES = (_no_evidence, _sensitive, _derivable, _duplicate, _vague)


def decide(candidate, existing=(), classifier=None) -> Decision:
    """Rules first, in cost order; the classifier only sees what rules cannot
    settle; store is the default when nothing objects. `existing` is the
    contents of already-stored memories, `classifier` the live LLM hook
    returning (action, reason, confidence)."""
    for rule in RULES:
        if decision := rule(candidate, existing):
            # stamped here, once, instead of in every rule: section 10 counts
            # duplicate rate by rule name, and the reason text is prose that
            # someone will reword the moment a metric starts depending on it
            return replace(decision, rule=rule.__name__.lstrip("_"))
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
    (section 2), so gate decisions are themselves replayable evidence."""
    decision = validate(decide(candidate, existing, classifier), candidate)
    if log is not None:
        log(candidate, decision)
    return decision


def resembles(text, other) -> float:
    """How much two memories say the same thing, measured the way this gate
    measures it. Symmetric, because the gate compares a candidate against
    stored text while consolidation (section 6) compares two stored records, and
    the two have to agree on the number or the DEFER band leads nowhere."""
    return max(_overlap(text, other), _overlap(other, text))


def _overlap(text, other) -> float:
    mine = words(text)
    return len(mine & words(other)) / len(mine) if mine else 0.0


# ponytail: a short stopword list, not a real one. Without it "the" matched
# almost every stored memory, so any question at all produced a keyword hit and
# retrieval could never honestly report "nothing matched". A live system uses
# the analyzer its index ships with.
STOPWORDS = frozenset("""the and for but not with that this from was were are
has have had its into than then when what which who whom does did done you
your our their they them here there will would could should about""".split())


def words(text) -> set:
    """Significant words only: short tokens and stopwords carry no signal, and
    matching on them is indistinguishable from matching on nothing."""
    split = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return {w for w in split if len(w) > 2 and w not in STOPWORDS}
