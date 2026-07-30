"""Evaluation and governance (production memory track, stage 10: Feedback).

The loop the first nine stages leave open. They write, resolve, merge, index,
rank, and inject, and none of them ever learns whether any of it was right.

This stage owns no storage. Every number here is a view over the ledger
(stage 2) and the records, the same rule stage 7 applies to indexes: drop it
and recompute it. Nothing is measured that was not logged, which is why the
earlier stages log their rejections, their operations, the rule that decided,
and what they injected.

Two event types close the loop:
  memory_injected  what recall put in the prompt, with ids, cost, conflicts,
                   and how many injected memories read like instructions
  memory_outcome   what happened after the answer: which memories carried it,
                   which misled it, whether the model abstained, whether the
                   user corrected the result

Four layers, coarse to specific:
  write      did the gate keep the right things
  retrieval  did the right memories come back
  context    was the block small, fresh, and honest
  end to end did the answer improve

Both event types are written and read through the constructors here, so the
metrics never index a raw dict. A metric that reads a log by key breaks the
same way a metric that parses a log line breaks, only more quietly.

A denominator of zero returns None, never 0.0. An unmeasured metric and a
metric measured at zero mean opposite things, and a dashboard that renders
them the same way is how a broken pipeline reads as a perfect score.

Not every metric stage 10 owes can be computed from a live log. Temporal
correctness, unsupported claims, and the end-to-end task families need either
a labeled corpus or a judgment about the answer itself. `Case` is where an
offline set starts, but it carries a query and expected ids and nothing else,
so temporal correctness would need more than this page carries. Missing, and
named as missing rather than quietly dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

from assemble import conflicts, tokens
from policy import REQUIRE_APPROVAL, STORE
from resolve import ABSTRACT, RETRACT, SUPERSEDE, as_of

INJECTED, OUTCOME = "memory_injected", "memory_outcome"
DECISION, OPERATION, REJECTED = "memory_decision", "memory_operation", "consolidation_rejected"


@dataclass(frozen=True)
class Outcome:
    """What one answer did with what it was given.

    `used` is the subset the answer actually leaned on, `misled` the subset it
    leaned on and got wrong, `abstained` whether it declined to answer from
    memory at all. These are judgments someone has to make: a live system asks
    the model to cite its memory ids, and takes a user correction as the label.
    Nothing here infers them, because a metric that grades its own homework
    measures nothing."""
    query: str
    injected: tuple = ()
    used: tuple = ()
    misled: tuple = ()
    abstained: bool = False
    corrected: bool = False


@dataclass(frozen=True)
class Injection:
    """What one turn put in the prompt. Read back from the log by `injections`,
    written by `injection_metadata`: the same one-writer, one-reader pairing
    the outcome event gets, so no metric here indexes a raw dictionary."""
    query: str
    recorded_at: str
    memory_ids: tuple = ()
    tokens: int = 0
    conflicts_found: int = 0
    conflicts_shown: int = 0
    injection_hits: int = 0


@dataclass(frozen=True)
class Case:
    """One labeled question for the offline set: which memories should have
    come back. `found` is filled by running retrieval, so a case is a fixture
    plus a result, not a metric."""
    query: str
    expected: tuple
    found: tuple = ()


@dataclass(frozen=True)
class Gate:
    """A threshold with the action it triggers. The third field is the whole
    point: without it this is a dashboard, a number people glance at, nobody
    owns, and no release waits for."""
    metric: str                  # "layer.name"
    limit: float
    action: str
    floor: bool = False          # the limit is a minimum, not a maximum


GATES = (
    Gate("write.unsupported_rate", 0.0,
         "block the write path: a memory is citing evidence the ledger does not have"),
    Gate("context.stale_rate", 0.05,
         "reindex after consolidation before serving: closed records are reaching prompts"),
    Gate("context.injection_hit_rate", 0.0,
         "review the write gate: instruction-shaped memories are reaching the prompt"),
    Gate("retrieval.misled_rate", 0.20,
         "hold the learned write policy: these outcomes are too noisy to train on"),
    Gate("retrieval.contradiction_coverage", 1.0,
         "raise the budget or the retrieval cap: conflicts are being dropped whole",
         floor=True),
)


def report(events, records, cases=()) -> dict:
    """The four layers over one scope's history."""
    return {"write": write_quality(events, records),
            "retrieval": retrieval_quality(events, cases),
            "context": context_quality(events, records),
            "end_to_end": end_to_end(events)}


def write_quality(events, records) -> dict:
    """From stage 3's decision log and stage 6's rejections, both of which
    exist because this function needs them."""
    decisions = [e for e in events if e.event_type == DECISION]
    actions = [e.metadata.get("action") for e in decisions]
    rules = [e.metadata.get("rule") for e in decisions]
    ops = [e.metadata.get("op") for e in events if e.event_type == OPERATION]
    logged = {e.id for e in events}
    unsupported = [r for r in records if not set(r.source_event_ids) <= logged]
    rejected = [e for e in events if e.event_type == REJECTED]
    return {"decisions": {a: actions.count(a) for a in sorted(set(actions)) if a},
            "store_rate": _share(actions.count(STORE), len(actions)),
            "write_precision": _precision(events, records),
            # the gate names the rule that decided, so this counts a category
            # rather than matching on a reason string somebody will reword
            "duplicate_rate": _share(rules.count("duplicate"), len(decisions)),
            # a retraction says the claim was never true, so it grades the
            # write that made it; a supersede only says the world moved on
            "wrong_update_rate": _share(ops.count(RETRACT),
                                        ops.count(RETRACT) + ops.count(SUPERSEDE)),
            "approval_queue": actions.count(REQUIRE_APPROVAL),
            # a memory citing an event the ledger does not have is provenance
            # that cannot be followed, which is worse than none at all
            "unsupported_rate": _share(len(unsupported), len(records)),
            "consolidation_precision": _share(ops.count(ABSTRACT),
                                              ops.count(ABSTRACT) + len(rejected))}


def retrieval_quality(events, cases=()) -> dict:
    """Evidence precision comes free from live outcomes. recall@k does not:
    it needs someone to say which memories the answer deserved."""
    served = [o for o in outcomes(events) if o.injected]
    turns = injections(events)
    found = sum(i.conflicts_found for i in turns)
    shown = sum(i.conflicts_shown for i in turns)
    return {"evidence_precision": _mean(
                [_share(len(set(o.used) & set(o.injected)), len(o.injected)) for o in served]),
            "misled_rate": _share(sum(1 for o in served if o.misled), len(served)),
            # stage 9 takes a conflict group whole or not at all, so per turn
            # this is 1.0 or 0.0: below 1.0 means some turn dropped a conflict
            # entirely, and answered from neither side rather than from one
            "contradiction_coverage": _share(shown, found),
            "recall_at_k": _mean([_share(len(set(c.found) & set(c.expected)),
                                         len(c.expected)) for c in cases])}


def context_quality(events, records) -> dict:
    """What the prompt actually paid, and what it paid for."""
    turns = injections(events)
    stale = shown = 0
    for turn in turns:
        # what the system still believed at the moment it injected. stage 5
        # stamps superseded_at precisely so this question stays answerable
        # after the fact; without it, a stale injection is invisible forever.
        believed = {r.id for r in as_of(records, turn.recorded_at)}
        stale += sum(1 for mid in turn.memory_ids if mid not in believed)
        shown += len(turn.memory_ids)
    return {"injected_tokens": _mean([i.tokens for i in turns]),
            "memories_per_turn": _mean([len(i.memory_ids) for i in turns]),
            "stale_rate": _share(stale, shown),
            "injection_hit_rate": _share(sum(i.injection_hits for i in turns), shown),
            # not abstention: this is retrieval declining to spend the budget,
            # which is stage 9 working. whether the answer abstained is an
            # outcome, and lives in the end-to-end layer.
            "no_inject_rate": _share(sum(1 for i in turns if not i.memory_ids),
                                     len(turns))}


def end_to_end(events) -> dict:
    """The only layer a user would recognise, and the thinnest one here.
    The task families (extraction, multi-session and temporal reasoning,
    knowledge updates) need a labeled corpus; these two come from live turns."""
    answered = outcomes(events)
    return {"turns": len(answered),
            "correction_rate": _share(sum(1 for o in answered if o.corrected), len(answered)),
            "abstention_rate": _share(sum(1 for o in answered if o.abstained), len(answered))}


def failing(scores) -> list[tuple]:
    """Which promises the system is breaking right now, as (gate, value).

    A missing metric never fails a gate. It also never passes one: the caller
    gets an empty list either way, so this is a detector, not a release
    decision, and 'no data' has to be read as 'not measured'."""
    return [(gate, value) for gate in GATES
            if (value := _lookup(scores, gate.metric)) is not None
            and (value < gate.limit if gate.floor else value > gate.limit)]


def injection_metadata(hits, selected) -> dict:
    """The fields stage 10 measures the context layer from, built next to the
    metrics that read them so the two cannot drift apart. Takes both the full
    candidate set and what survived the budget, because contradiction coverage
    is a question about the difference."""
    return {"memory_ids": [h.memory_id for h in selected],
            "tokens": sum(tokens(h.content) for h in selected),
            "conflicts_found": _conflicted(hits),
            "conflicts_shown": _conflicted(selected),
            "injection_hits": sum(1 for h in selected if looks_like_instruction(h.content))}


def outcome_metadata(outcome) -> dict:
    """The write side of an Outcome. Paired with `outcomes` below: one place
    turns the type into a row, one place turns the row back into the type."""
    return {"injected": list(outcome.injected), "used": list(outcome.used),
            "misled": list(outcome.misled), "abstained": outcome.abstained,
            "corrected": outcome.corrected}


# ponytail: substring shapes, not a detector. A live system scores this with a
# model or a trained classifier, and counts the same way. The number existing
# at all is what turns "we frame memory as data" into something falsifiable.
INSTRUCTION_SHAPES = ("ignore all previous", "ignore previous", "disregard the",
                      "you must now", "new instructions", "system:")


def looks_like_instruction(text) -> bool:
    """Stored text that reads like a command rather than a fact. Stage 9 frames
    every memory as data, so a hit is not an exploit. It is the write gate
    having stored something that should have needed a human first."""
    low = text.lower()
    return any(shape in low for shape in INSTRUCTION_SHAPES)


def outcomes(events) -> list[Outcome]:
    """Read back as the type that was written. Indexing the raw metadata dict
    made every metric depend on five string keys, which is the failure this
    stage warns about one paragraph later."""
    return [Outcome(e.content, tuple(e.metadata.get("injected", ())),
                    tuple(e.metadata.get("used", ())), tuple(e.metadata.get("misled", ())),
                    bool(e.metadata.get("abstained")), bool(e.metadata.get("corrected")))
            for e in events if e.event_type == OUTCOME]


def injections(events) -> list[Injection]:
    """The other half of the same rule. This one stayed a raw dict longer,
    which is how the claim above ended up true of one event type and printed
    as if it were true of both."""
    return [Injection(e.content, e.recorded_at,
                      tuple(e.metadata.get("memory_ids", ())), e.metadata.get("tokens", 0),
                      e.metadata.get("conflicts_found", 0),
                      e.metadata.get("conflicts_shown", 0),
                      e.metadata.get("injection_hits", 0))
            for e in events if e.event_type == INJECTED]


def _precision(events, records) -> float | None:
    """Of the memories that got a verdict, how many were worth storing. Only
    judged writes count: a memory nobody has used yet is not a bad write, and
    counting it as one would punish a gate for being early."""
    judged = outcomes(events)
    used = {mid for o in judged for mid in o.used}
    misled = {mid for o in judged for mid in o.misled}
    retracted = {r.id for r in records if r.status == "retracted"}
    return _share(len(used - misled - retracted), len(used | misled | retracted))


def _conflicted(hits) -> int:
    """How many of these memories have a competing claim inside the same set."""
    return sum(1 for competing in conflicts(hits).values() if competing)


def _lookup(scores, path):
    """A gate naming a metric no layer reports is a gate that never fires, and
    a silent None reads as "not measured" rather than as the typo it is.
    Every layer returns every key it owns, so absence here is always a bug."""
    layer, metric = path.split(".")
    if metric not in scores.get(layer, {}):
        raise KeyError(f"gate watches a metric no layer reports: {path}")
    return scores[layer][metric]


def _share(part, whole):
    return part / whole if whole else None


def _mean(values):
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None
