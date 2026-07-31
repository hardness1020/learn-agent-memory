# 3 · Write policy

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> Nothing becomes memory as a side effect. Every candidate gets a decision, a reason, and a log entry.

This page covers lifecycle section 3 of the [Production memory](../../README.md) track, the Write gate:
the explicit decision between raw evidence and stored memory.
What enters the gate is a candidate: content distilled from fresh events, waiting for a store-or-not decision.

The problem has two failure directions. Store everything and recall turns noisy, stale, and expensive.
Store too little and the agent keeps asking questions it already got answers to.
And when a write happens silently, nobody can answer "why does it remember this" or "why did it forget that".

The mini version gates writes with a type taxonomy inside the extractor prompt:
only non-derivable facts of a few named types get saved. At production scale the gate must do more:

1. Score candidates on named dimensions, not one vague "is this important".
2. Output a typed decision, including defer and require approval, not just yes or no.
3. Validate every proposal before commit, whether a rule or a model produced it.
4. Log every decision with its reason, rejections included.

---

## Mechanism

The simplest version: a function from candidate to decision. The design is in the ordering.
Rules run first because they are cheap and deterministic. The model only sees what rules cannot settle.
A validator checks every proposal before anything commits.

Five moving parts:

- **Candidate**: a proposed memory distilled from fresh ledger events, carrying its source event ids.
- **Rules**: deterministic checks in cost order. Evidence, derivability, duplicates, vagueness, sensitivity.
- **Classifier**: one model call for the judgment rules cannot make (useful next time, or smalltalk?).
- **Decision**: action, reason, confidence. Four actions: `store`, `ignore`, `defer`, `require_approval`.
- **Validator**: the deterministic check between any proposal and commit.

The six scoring dimensions from the track README split cleanly across the layers:

```text
Novelty       rules: word overlap against existing memories
Specificity   rules: too vague to ever retrieve back
Derivability  rules: grep or git can answer this again
Sensitivity   rules: flagged input requires human approval
Durability    classifier: useful next time, or smalltalk?
Confidence    carried on the decision, set by whoever decided
```

The record types stay small:

```python
@dataclass(frozen=True)
class Candidate:
    content: str
    kind: str                          # episodic / semantic / procedural
    source_event_ids: tuple = ()
    sensitive: bool = False

@dataclass(frozen=True)
class Decision:
    action: str                        # store / ignore / defer / require_approval
    reason: str
    confidence: float
```

`decide` walks the rules, falls through to the classifier, and defaults to store when nothing objects:

```python
def decide(candidate, existing=(), classifier=None) -> Decision:
    for rule in RULES:                 # evidence, derivability, duplicate, vagueness, sensitivity
        if decision := rule(candidate, existing):
            return decision
    if classifier is not None:
        return Decision(*classifier(candidate))
    return Decision(STORE, "novel, specific, evidence-backed", 0.6)
```

The duplicate rule has two thresholds. High overlap means ignore. Medium overlap means defer,
because merging near-duplicates is consolidation's job (section 6), not the gate's:

```python
def _duplicate(c, existing):
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

`validate` runs on every proposal, no matter who made it. A model can propose; only a validated decision takes effect:

```python
def validate(decision, candidate) -> Decision:
    if decision.action not in ACTIONS:
        raise ValueError(f"unknown action: {decision.action}")
    if not decision.reason:
        raise ValueError("a decision without a reason cannot be audited")
    if not 0 <= decision.confidence <= 1:
        raise ValueError("confidence out of range")
    if decision.action == STORE and not candidate.source_event_ids:
        raise ValueError("store without source events")
    return decision
```

`gate` ties the flow together and logs the decision itself:

```text
fresh events (section 2)
    ↓ propose candidates
rules: evidence · derivability · duplicate · vagueness · sensitivity
    ↓ only what rules cannot settle
classifier: store / ignore / defer / require_approval
    ↓ every proposal
deterministic validator
    ↓ commit
typed memory (section 4) · the decision logged as an event
```

How it integrates: candidates come from fresh ledger rows (section 2), on the warm path (run end, not per query).
Stored candidates become typed records (section 4), deferred ones wait for consolidation (section 6),
and approval-gated ones sit in a queue, the pattern Hermes uses for staged writes.
Decisions land back in the ledger as events, so section 10 can measure write precision from replayable data.

The frontier trains the gate instead of writing it: [Memory-R1](https://arxiv.org/html/2508.19828v2) learns
`ADD / UPDATE / DELETE / NOOP` with outcome-driven RL, and [AgeMem](https://arxiv.org/abs/2601.01885) folds
memory operations into the agent policy itself. Both need task-specific training data, so neither is a first version.

### What Changed

In the minimum loop, selection lived inside an extractor prompt and returned files or nothing.
Now the judgment is explicit and typed, near-duplicates defer instead of silently piling up,
sensitive writes wait for a human, and every rejection leaves a reason in the log.

---

## Per system

| | Mem0 | Hermes Agent |
| --- | --- | --- |
| **Pros** | The gate also updates: new facts supersede old ones in one pass. | Sensitive writes wait for a human. Saves happen mid-session, so nothing is lost by run end. |
| **Cons** | Every write costs model calls. A wrong update or delete is destructive. | Approval adds friction; the queue can pile up unread. Rules live in a prompt. |
| **Why** | Treats the store as small and curated: new facts merge in, not pile on. | Trusts the model to propose but not to persist: a human owns the final write. |
| **How: candidates** | A model extracts candidate facts from the fresh exchange. | The model calls a memory tool mid-session when something looks durable. |
| **How: decision** | A model picks add, update, delete, or no-op against similar existing memories. | Direct write, or staged as pending when approval mode is on. |
| **How: safeguard** | Operation history per memory, so a bad edit can be traced. | Pending writes are listed and approved or rejected one by one. |

---

## Failure modes

- **The gate is too strict.** The store stays thin and the agent re-asks known things. Track ignore rates per rule,
  and prefer defer over ignore on borderline candidates so consolidation gets a second look.
- **The gate is too loose.** Recall noise climbs. This is measurable: write precision and duplicate rate (section 10).
  Raise the duplicate threshold and the specificity bar before touching the classifier.
- **Decisions happen silently.** An unlogged ignore makes "why doesn't it remember X" undebuggable.
  Log every decision with its reason, rejections included.
- **Classifier output is trusted raw.** A malformed action or an out-of-range confidence must fail loudly.
  The validator runs on every proposal; nothing commits unvalidated.
- **Sensitivity is delegated to the model.** A model that misses a flag stores a secret.
  Keep sensitivity deterministic (flags, patterns, source channel), with the classifier only as a second opinion.
- **The gate blocks the hot path.** Scoring candidates inside every turn adds latency.
  Run the gate at run end (warm path) and batch candidates per run.

---

## Runnable

[`src/`](src/) carries 01 forward and adds:

- [`policy.py`](src/policy.py): `Candidate`, `Decision`, the rule chain, `decide`, `validate`, and `gate`.
- [`engine.py`](src/engine.py): `propose()` gates candidates and logs every decision back into the ledger as an event.
- [`test.py`](src/test.py): each rule firing, the classifier hook running only on unsettled candidates,
  the validator rejecting malformed proposals, and decisions landing in the ledger as events.

```bash
python sections/03-write-policy/src/test.py   # offline checks, no key
```

Offline, the classifier is a stub. Live, it is one model call per unsettled candidate.

---

## Sources

- [Mem0](https://arxiv.org/abs/2504.19413): extract-then-update writes, add / update / delete / no-op against similar memories.
- [Memory-R1](https://arxiv.org/html/2508.19828v2): the write gate as an RL-trained policy.
- [AgeMem](https://arxiv.org/abs/2601.01885): memory operations folded into the agent policy.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/write_approval.py`, staged writes pending approval.
- [Production memory track](../../README.md): the lifecycle this section belongs to.
