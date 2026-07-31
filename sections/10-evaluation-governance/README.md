# 10 · Evaluation and governance

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> Nothing that was not logged can be measured, and a number with no threshold attached changes nothing.

This page closes the [Production memory](../../README.md) track: lifecycle section 10, Feedback and evaluation.
The first nine sections write, resolve, merge, index, rank, and inject, and none of them ever finds out
whether it was right. This section adds the feedback that closes that loop.

The gap is easy to miss, because memory fails quietly. A gate that stores everything and a gate that stores nothing
both look fine from inside the gate. Retrieval that returns plausible wrong memories looks exactly like
retrieval that works, right up until an answer is wrong and nobody can say which memory caused it.

Memory is also the one subsystem that gets worse on its own. Records accumulate, claims go stale,
consolidation drifts, and the index falls behind the store. A system with no measurements does not stay
at yesterday's quality: it degrades, and the first person to notice is a user.

---

## Mechanism

The simplest version: log two more event types, compute every metric from the ledger on demand,
and attach an action to every threshold. No new storage.

Evaluation stores nothing of its own. Every metric is computed on read, from section 2's ledger and the records,
under the same rule section 7 applies to indexes: drop the numbers, recompute them, and nothing is lost.
The contract Protocol stays at three verbs.

### The two new event types

```text
recall   →  memory_injected    which ids, what it cost, what conflicted
answer   →  memory_outcome     which carried it, which misled it, was it corrected
              ↓
          the ledger, unchanged: append only, no evaluation store of its own
              ↓
   write · retrieval · context · end to end        four layers, computed on read
              ↓
          gates: a threshold with a consequence attached
```

Reporting an outcome does not add a fourth verb to the contract. An outcome is an observation
about the turn, so it enters the ledger through `observe` like any other event:

```python
@dataclass(frozen=True)
class Outcome:
    query: str
    injected: tuple = ()
    used: tuple = ()          # the subset the answer leaned on
    misled: tuple = ()        # the subset it leaned on and got wrong
    abstained: bool = False   # the answer declined to rely on memory
    corrected: bool = False   # the user fixed the result
```

`used` and `misled` are judgments, and they have to come from outside this section.
A live system gets them from two signals: it asks the model to cite the memory ids it relied on,
and it treats a user correction as the `misled` label. Nothing here infers them from the answer text,
because a metric that grades its own homework measures nothing.

Data flow through the section: `recall` logs what it selected before it returns the block, the caller reports
an outcome after the answer, and `evaluate` reads both back out of the ledger with the records
and returns four layers plus whichever gates are currently failing.

### The four metric layers

These are `evaluate`'s output: numbers computed from the ledger on demand, in four layers,
one per segment of the pipeline. Each metric is computable only because an earlier section logged something:

| Layer | Metrics | What it measures | Computed from | Notes |
| --- | --- | --- | --- | --- |
| write | decisions · store rate · duplicate rate · approval queue | what the gate decided, and the review backlog | section 3's decision log, with the rule that decided | |
| | write precision | how many stored memories proved good | the same log, plus outcomes | judged writes only: counting an unused memory would punish the gate for storing early |
| | unsupported memory rate | cites evidence the ledger lacks | section 4's source event ids | not *unsupported claims*: that is about the answer, not measured here (see below) |
| | wrong update rate | stored memories later retracted as wrong | section 5's retract and supersede operations | |
| | consolidation precision | how many merge proposals survived validation | section 6's returned rejections | |
| retrieval | evidence precision · misled rate | how many retrieved memories helped or misled | live outcomes | |
| | contradiction coverage | did both sides of a conflict go in | section 9's conflict groups | group-atomic: a turn is 1.0 or 0.0; under 1.0 a conflict was dropped whole |
| | recall@k | how many of the right memories came back in the top k | a labeled case set, or not at all | |
| context | injected tokens · memories per turn | how much prompt space memory takes per turn | section 9's per turn injection log | |
| | injection hit rate | instruction-shaped memories injected | the same log, at injection time | not an exploit (the guard frames data): the write gate skipped a review |
| | no inject rate | how often a turn goes without memory | the injection log's empty rows | |
| | stale rate | injected memories that were already closed | section 5's superseded_at, read through as_of | checkable because the row was stamped, not deleted |
| end to end | turns · correction rate · abstention rate | turns, user corrections, answers declining memory | outcomes | |

### Deliberately not measured

Some things this section deliberately does not measure. Not oversights: each is missing one input
this page cannot supply:

| Not measured | What it would need |
| --- | --- |
| temporal correctness · unsupported claims | a judgment about the answer itself, not about the log |
| LongMemEval's end-to-end task families | a labeled corpus; `Case` carries only a query and the expected ids |
| LongMemEval-V2's agent-experience task families | the same corpus, which this page does not ship |

The family lists come from the benchmarks: extraction, multi-session reasoning, temporal reasoning,
and knowledge updates from LongMemEval; static state, dynamic state, workflow knowledge,
environment gotchas, and premise awareness from LongMemEval-V2.

### Abstention, split in two

Abstention needed splitting into two metrics, because two different things shared the name:

| | no_inject_rate | abstention_rate |
| --- | --- | --- |
| **What happened** | retrieval injected nothing this turn | the answer declined to rely on memory |
| **Whose property** | retrieval's: not worth the budget, section 9 working as designed | the model's reasoning, reported in the outcome like any other |

Either way, the empty injection is still logged, or "we chose not to inject" and "recall never ran"
become the same row.

### Missing is not zero

A denominator of zero returns `None`, never `0.0`:

```python
def _share(part, whole):
    return part / whole if whole else None
```

An unmeasured metric and a metric measured at zero mean different things. Display missing as 0,
and a pipeline that stopped reporting shows zero errors, which reads as a perfect score. The same rule covers `recall@k`: with no labeled cases it is
missing, not perfect, and no amount of live traffic produces it.

### Gates: a threshold with an action

Governance is the gate's `action` field. A threshold on its own is a dashboard, a number people glance at,
nobody owns, and no release waits for:

```python
@dataclass(frozen=True)
class Gate:
    metric: str          # "layer.name"
    limit: float
    action: str          # what happens when it trips
    floor: bool = False  # the limit is a minimum, not a maximum

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
```

A gate naming a metric no layer reports raises rather than returning nothing. A typo that resolves to
"not measured" is a gate that quietly never fires again, which is the same failure as reading a missing
metric as zero, one level up.

The last gate is the track's fifth rule (evaluate before trust) doing real work. A learned write policy trains on outcomes,
so a system whose outcomes are wrong one time in five will train on that noise and get worse confidently.
Evaluate before trust applies to the memory system's own upgrades first.

### Closing the loop: retract

The loop only closes if something can act on it. An outcome that reports a memory misled the answer
has one operation waiting for it, `retract`, which is section 5's vocabulary and not a new verb: the record
is marked wrong and stays readable, because retracted means it never was true while superseded means
the world moved on. It is the one state change this section makes, and without a caller the metric that
counts it could only ever read zero.

### One append on the hot path

The metrics and the gates all run cold: over any window, recomputed at will, disposable.
One thing is on the hot path. `recall` appends one row per query, before it returns the block:
a single insert with no read. That row buys the entire context layer,
because an injection nobody recorded is a turn nobody can score.

### What Changed

Compared with section 9, `assemble` now hands back what it chose along with what it rendered,
so the engine can log the ids and the token cost without redoing the work.
Decisions log their action **and the rule that decided** as named fields rather than a prefix on the message,
since a count that parses a log line breaks silently the day someone rewords it.
Both event types are written and read through one constructor each, so no metric indexes a raw dictionary:
a metric coupled to five string keys fails the same way, just more quietly.
Log the fields you intend to measure, and read them back as the type you wrote.

---

## Per system

| | LongMemEval | Memory-R1 |
| --- | --- | --- |
| **Pros** | Comparable across systems. A failing task family names the broken ability. | Improves the policy with nobody labeling anything. Learns from real traffic. |
| **Cons** | A fixed set goes stale, and passing it is not the same as working in production. | The reward is the final answer, so a wrong memory that got lucky is reinforced. |
| **Why** | Memory abilities are separable, so they should be scored separately. | The only judgment that counts is whether the answer came out right. |
| **How: signal** | Labeled sessions with known supporting evidence, scored per task family. | Answer correctness at run end, fed back as reward over store, edit, and forget. |
| **How: timing** | Offline, on a fixed set, before shipping. | Online, over live outcomes, continuously. |
| **How: coverage** | The task families it defines, and nothing outside them. | Whatever the traffic happens to exercise, and nothing it does not. |

---

## Failure modes

- **A metric with no consequence.** Numbers get charted, nobody owns them, and no release waits for them.
  Every gate here carries the action it triggers; a threshold without one is decoration.
- **Grading your own homework.** The model that produced the answer also decides whether memory helped.
  Labels come from outside: user corrections, cited ids, a human-reviewed set.
- **Missing read as zero.** A pipeline that stopped emitting outcomes shows a perfect correction rate.
  Distinguish absent from zero at the arithmetic, not in the dashboard.
- **Only end-to-end numbers.** One score that moves for ten reasons cannot name the section that broke.
  Score the layers separately, and let the failing layer point at its section.
- **A metric nothing can produce.** A rate whose numerator no code path emits reads as a permanent
  clean zero, which is indistinguishable from a system doing well. Every counted category needs a caller,
  or the metric is decoration with a denominator.
- **Evaluation on the hot path.** Scoring a turn while answering it adds latency to every query for a number
  nobody reads until tomorrow. Record on the hot path, one append; compute cold.
- **One word, two metrics.** Abstention meant both "retrieval injected nothing" and "the answer declined",
  which silently averaged a retrieval property with a reasoning one. Name them apart or measure neither.
- **Benchmark instead of production.** A fixed set is a floor, not a proof. Live evidence precision
  and stale rate keep measuring after the benchmark passes.
- **An approval queue nobody drains.** `require_approval` becomes a slow `ignore` if the backlog only grows,
  and the write gate quietly stops storing a whole category. Count the queue, not just the decisions.
- **Erasure treated as a design bug.** Append-only meets a legal delete eventually. Handle it as one audited,
  scoped operation with a record of its own, not as a `DELETE` verb any section may call.

---

## Runnable

[`src/`](src/) carries 08 forward and adds:

- [`evaluate.py`](src/evaluate.py): `Outcome`, `Injection`, `Case`, `Gate`, the four layer functions, `report`,
  `failing`, and one writer and one reader per event type, so no metric indexes a raw dictionary.
  Missing metrics return `None`, never `0.0`.
- [`engine.py`](src/engine.py): `recall` logs what it injected including the empty turns,
  `feedback` records an outcome as an ordinary observation, `last_injected` tells a caller which
  memories to report on, `retract` marks a memory wrong through section 5's operation, and `evaluate`
  scores the chain from the log.
- [`policy.py`](src/policy.py): a decision now carries the rule that made it, stamped in one place
  rather than in every rule, so duplicate rate counts a category instead of matching on a reason string.
- [`assemble.py`](src/assemble.py): `assemble` returns the block and the selection, so the rule that an
  empty selection means an empty block has one owner, and the token estimate is the selector's own.
- [`test.py`](src/test.py): missing versus zero on a fresh system, decisions counted by action and by rule,
  write precision over judged writes only, unsupported provenance, injected cost, the two abstentions kept
  apart, contradiction coverage falling when a budget drops a conflict group whole, a retraction moving
  wrong update rate off zero, an instruction-shaped memory tripping its gate, a gate naming a metric
  nobody reports failing loudly instead of silently, recall@k appearing only with labels, stale rate before and after a correction, and the gates.

```bash
python sections/10-evaluation-governance/src/test.py   # offline checks, no key
```

This section never calls the model, so there is no `demo.py`.

---

## Sources

- [LongMemEval](https://arxiv.org/abs/2410.10813): the end-to-end task families, abstention scored as an ability.
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493): the same idea extended to agent experience and premise awareness.
- [Memory-R1](https://arxiv.org/html/2508.19828v2): outcome-driven RL over the memory operations, which is what these metrics feed.
- [AgeMem](https://arxiv.org/abs/2601.01885): learned policies for when to store, edit, and forget.
- [Production memory track](../../README.md): the lifecycle this section belongs to.
