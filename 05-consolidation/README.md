# 5 · Consolidation

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> A model may decide what to merge. It may never decide what to destroy.

This page covers lifecycle stage 6 of the [Production memory](../README.md) track,
Consolidate: turning events into knowledge.

Consolidation is not summarization. It covers deduplication, entity resolution, conflict detection,
temporal invalidation, fact merging, preference induction, workflow extraction, confidence updates, and forgetting.
Stage 5 handled one narrow case, a new claim closing an old one. This stage handles the rest,
and it is the first stage where a model decides what happens to memories that already exist.

That is the whole difficulty. Everything before this point only added. A consolidation pass closes records,
and the thing proposing the closures is a model that can be wrong, prompt-injected, or simply confused
about which of two similar memories was the important one.

---

## Mechanism

The simple version: a proposer suggests, a validator decides, and the store only ever sees validated operations.

```text
proposer (model or rule)
    ↓  Proposal
check()   level, kind, content, source count, sources exist,
          sources still active, one scope, one claim key
    ↓
apply()   write the derived record, then close the sources it cited
    ↓
Operation list  →  ledger
```

Three levels, sorted by how far the output travels from its input:

```text
compression   many similar memories  →  one shorter memory
abstraction   many episodes          →  one rule or preference
skill         many successful runs   →  one reusable procedure
```

Compression rewrites, so its output cannot say more than its sources did.
Abstraction generalizes past its sources, so it can be wrong in a way compression cannot:
five episodes about one flaky test become "the test suite is unreliable".
Skill formation is the most valuable and the most fragile: three runs can succeed by luck,
and the extracted procedure carries none of that doubt when the fourth run applies it.

A proposal names two things: the new content, and the old records that content replaces.
It cannot name an operation, so the only way a model can close a record is to offer
new content in its place. A delete that writes nothing cannot be expressed:

```python
@dataclass(frozen=True)
class Proposal:
    level: str
    content: str
    source_ids: tuple
    kind: str = "semantic"
    reason: str = ""
```

The validator is the `check` in the diagram, standing between a proposal and the store.
It is deterministic and refuses a proposal for eight reasons: an unknown level, an unknown kind, empty content,
fewer than two sources, a source that does not exist, a source that is already closed, sources from more than
one scope, and sources that hold different claim keys. Everything the record constructor would reject is
checked here as well, because `apply` mutates the store: a proposal that failed halfway through would leave
records and ledger disagreeing, and the pass is supposed to be recoverable.
Rejections are returned, not swallowed, because stage 10 measures consolidation precision from them.

One more guarantee: replacing a record does not lose its evidence.
Every record names the ledger events it came from in its source event ids.
`apply` closes exactly the records the proposal cited, and the derived record
takes over every source event id they held, none dropped.
No check enforces this. The construction leaves no way to drop one:

```python
events = tuple(sorted({e for r in sources for e in r.source_event_ids}))
derived = make_record(sources[0].scope, proposal.kind, "inference",
                      proposal.content, events,
                      min(r.confidence for r in sources),
                      tags=("consolidated", proposal.level),
                      claim_key=one_key(sources),      # or the profile view empties
                      valid_from=at)                   # or it reads as always true
```

Three details in that call carry weight. The epistemic type is `inference`, never `fact`: a merged claim is
the system's own derivation, and stage 4's rule that inferences never silently become facts still holds.
The confidence is the minimum of the sources, not an average, because merging two claims cannot make either
more certain. And the event ids chain back to raw evidence in the ledger, so a derived record can always be
traced to the observations it came from.

`consolidate` runs one pass and reports both halves:

```python
return {"proposed": len(proposals), "applied": len(proposals) - len(rejected),
        "rejected": rejected, "operations": ops}
```

Data flow: the engine gathers this scope's active records, hands them to the proposer, validates whatever
comes back, applies the survivors, and writes every operation and every rejection to the ledger.
The pass never runs inside a user's turn. Answering the turn needs no step of it, so it runs
on a schedule or between sessions, where nobody is waiting and the seconds a model call takes
do not hurt ([Sleep-time Compute](https://arxiv.org/html/2504.13171v1) makes the same argument).

### Two bugs found at integration

Both bugs live in earlier stages, and both stayed invisible until this stage ran against them.
Per this track's convention the fix lands in the copy of the code this stage carries forward,
and the earlier folders keep their version, so the diff shows what integration changed.

**Bug 1: `DEFER` had nowhere to go.** The write gate defers a candidate that resembles an
existing memory, with the reason "similar memory exists, merge at consolidation".
But a deferred candidate was never stored, so consolidation never saw it:
every pair this stage is able to merge is a pair the gate had already dropped.
The fix is in the engine's write path: a deferred candidate is stored and tagged `deferred`,
so the merge band (the similarity range the gate routes to consolidation) is reachable.

```python
if decision.action in (STORE, DEFER):
    # DEFER means "merge at consolidation", so the candidate has to
    # survive long enough for stage 6 to see it
    record = make_record(scope, candidate.kind, epistemic_type,
                         candidate.content, candidate.source_event_ids,
                         decision.confidence, claim_key=candidate.claim_key,
                         tags=("deferred",) if decision.action == DEFER else ())
```

`DEFER` with nowhere to defer to is just a silent `IGNORE` with a friendlier reason.

**Bug 2: two measures of "similar".** Storing the candidate was still not enough.
The gate scored similarity one way and this stage grouped records by another.
The same pair could look similar enough for the gate to defer, then score below this
stage's merge threshold, so everything the gate handed over sat in the store and
nothing was ever merged. The fix is to use the gate's function and threshold here,
so both sides always measure the same number:

```python
from policy import SIMILAR_AT, resembles

MERGE_AT = SIMILAR_AT          # the gate's threshold, measured the gate's way

# when propose_compression groups records:
resembles(record.content, r.content) >= MERGE_AT
```

A handoff between two stages is only real when both sides measure "similar" the same way.

### What Changed

Compared with stage 5, `consolidate()` is the contract's third verb, and it is now implemented.
`observe()` and `consolidate()` both work end to end offline; `recall()` arrives with stage 8.

---

## Per system

| | A-Mem | Sleep-time Compute |
| --- | --- | --- |
| **Pros** | New knowledge is usable at once. Links find related notes without a graph store. | Consolidation cost never lands on a user turn. Heavy passes stay affordable. |
| **Cons** | Every write pays the organization cost. A bad link propagates as later notes attach to it. | Knowledge is stale between passes. A correction made now applies later. |
| **Why** | Memory should reorganize itself as it grows, the way notes in a zettelkasten do. | Anything not needed to answer this turn should not be computed during this turn. |
| **How: trigger** | On write. A new note links to and revises the notes it touches. | On a schedule, or during idle time between sessions. |
| **How: output** | Linked notes, with earlier ones updated to reflect the new one. | Precomputed derived context, ready before the question arrives. |
| **How: cost** | Paid per write, at interactive latency. | Paid in batch, off the query path. |

---

## Failure modes

- **The model deletes the only copy.** A merge that closes its sources without carrying them forward loses
  the evidence. Derived records cite the union of their sources' event ids, and the ledger keeps the raw
  events regardless, so a bad pass is recoverable by replay.
- **The exception is merged away.** Four memories say the deploy is fine and one says it fails on Fridays.
  Compression keeps the majority wording and the Friday case disappears. Merge by claim, not by similarity alone.
- **Consolidation on the hot path.** A model call during recall adds seconds to every turn. The pass runs
  on a schedule or between sessions, never inside a user's turn.
- **Derived records treated as facts.** A merged claim is an inference. Stamping it `fact` lets a guess
  outrank the observations it came from at retrieval.
- **Repeated consolidation drifts.** Merging merged records compounds every small rewrite until the memory
  no longer matches any evidence. Each pass consumes only active records and the second pass over a settled
  store proposes nothing.
- **Rejections are invisible.** A validator that silently drops bad proposals hides a broken proposer.
  Rejections are returned and logged with their reason.
- **Cross-scope merge.** Two tenants phrase the same preference identically and one merge joins them.
  The validator refuses a proposal whose sources span more than one scope.

---

## Runnable

[`src/`](src/) carries 04 forward and adds:

- [`consolidate.py`](src/consolidate.py): the three levels, `Proposal`, the `check` validator,
  `apply`, the `consolidate` pass, and the deterministic `propose_compression` proposer.
- [`engine.py`](src/engine.py): `consolidate()` implements the contract's third verb, and a deferred
  candidate is now stored so the merge band is reachable.
- [`test.py`](src/test.py): every rejection the validator owes, provenance and confidence on the derived
  record, the proposer grouping repeats and leaving singletons alone, a reported rejection,
  and a full pass that merges two memories while the raw events stay untouched.

```bash
python tracks/production-memory/05-consolidation/src/test.py   # offline checks, no key
```

The proposer seam is where a model plugs in. The default one is deterministic, so this stage has no `demo.py`.

---

## Sources

- [A-Mem](https://arxiv.org/html/2502.12110v1): agentic organization, where a new note links to and evolves old ones.
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1): moving consolidation work off the query hot path.
- [Memory-R1](https://arxiv.org/html/2508.19828v2): a learned policy for when to store, edit, and forget.
- [Production memory track](../README.md): the lifecycle this stage belongs to.
