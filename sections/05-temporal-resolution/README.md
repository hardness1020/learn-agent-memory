# 5 · Temporal resolution

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> A fact that stopped being true is not a wrong fact. Close it, do not overwrite it.

This page covers lifecycle section 5 of the [Production memory](../../README.md) track, Resolve: identity, conflict, and time.

Section 4 gave every record a type and a source. It did not say what happens when a new record disagrees with an old one.
A store without an answer picks one of two bad ones. Overwrite, and the old claim is gone, so nobody can audit
what the system believed last month or why it changed. Keep both, and retrieval returns two live contradictions
and lets the model guess.

Three questions have to be separated:

1. Identity. Is "Marcus" the same subject as "Ming-Siang"?
2. Conflict. Is "lives in San Diego" against "lives in San Francisco" a contradiction, or two facts from different times?
3. Time. Which of the two is true now, and which was true then?

---

## Mechanism

The simple version: keep two clocks per record instead of one, and never delete.

```text
recorded_at ─────────── superseded_at      when the system believed it
valid_from  ─────────── valid_to           when it was true in the world
```

That is bitemporal modeling. Two clocks because the questions differ. "What does the system think Marcus's address is"
reads the `recorded_at` pair. "Where did Marcus live in March" reads the `valid_from` pair.
One clock cannot hold a record entered today about last year: store last year and lose when
the system learned it, store today and lose when it was true. Two clocks hold both.
[Graphiti and Zep](https://arxiv.org/abs/2501.13956) build a temporal knowledge graph on the same split: old edges close, they do not vanish.

Conflict needs a way to tell competing claims from merely similar ones. A record carries a claim key:
the subject and predicate, with the content as the object.

```text
claim_key "marcus:lives_in"   content "Marcus lives in San Diego"      status superseded  valid_to 2025-06
claim_key "marcus:lives_in"   content "Marcus lives in San Francisco"  status active      valid_from 2025-06
```

Same key, different content, both active means one of them stopped being true.
An empty key means the record makes a claim nothing else competes with, so it never auto-supersedes.

Every operation is non-destructive. There is no free-form `UPDATE` and no `DELETE`, here or in the event ledger (section 2):

```text
ADD        a new record enters, active
SUPERSEDE  the old record closes, the new one opens
RETRACT    the record is marked wrong, and stays readable
ABSTRACT   many records feed one higher-level record (section 6 emits these)
```

To update a fact, add the new record, then SUPERSEDE the old one. Strictly, closing writes to the
old record too, but it is the only write allowed: it stamps the end fields (status, superseded_at,
valid_to), one way, once. The claim text never changes, and the old record stays readable.

`supersede` and `retract` look alike and mean different things. Superseded means it used to be true.
Retracted means it never was, usually a bad extraction. Both stay readable, because a retracted record
is the evidence that the extractor needs fixing.

The write path adds one step. `resolve` places a validated record on the timeline and closes what it contradicts:

```python
def resolve(store, record, at=None) -> list:
    ops = []
    for old in conflicts(record, same_scope(record.scope, store.records)):
        closed, op = supersede(old, at, cause_id=record.id)
        store.records[store.records.index(old)] = closed
        ops.append(op)
    store.add(replace(record, valid_from=record.valid_from or at))
    return ops + [Operation(ADD, record.id, "new claim", at)]
```

Scope isolation is a resolution rule, not only a read filter. A supersede is a write, so one tenant's claim
must never close another tenant's identically worded claim. `same_scope` enforces that before conflicts are computed.

Reading the two clocks gives two different queries:

```python
def as_of(records, when):     # what the system believed at a past moment
    return [r for r in records if r.recorded_at <= when
            and (r.superseded_at is None or r.superseded_at > when)]

def valid_at(records, when):  # what was true in the world at that moment
    return [r for r in records if (r.valid_from is None or r.valid_from <= when)
            and (r.valid_to is None or r.valid_to > when)]
```

Data flow: the write gate (section 3) passes a candidate, section 4 constructs the typed record,
and this section places it on the timeline and returns the operations performed.
The engine writes each operation back to the ledger as an event, so the timeline is itself replayable.
Retrieval (section 8) filters to active records, and assembly (section 9) reads the conflicts this section recorded
and prints both sides rather than silently picking one.

### Two bugs found at integration

Both bugs live in the write gate (section 3), and both stayed invisible until this section arrived
and real corrections started reaching the gate. They broke the first version of this section.
Per this track's convention the fix lands in the copy of the code this section carries forward,
and the earlier folders keep their version, so the diff shows what integration changed.

**Bug 1: a correction deferred as a near-duplicate.** The gate judges similarity by word overlap
and defers candidates that resemble an existing memory. But a correction resembles what it corrects:
"Marcus lives in San Diego" and "Marcus lives in San Francisco" share most of their words.
So corrections sat deferred, resolution never saw them, and the stale claim stayed active:
the gate was deferring exactly the case this section exists to resolve.
The fix: a keyed candidate is never measured for overlap. It is a duplicate only if its wording
is identical, and anything else is a correction that resolution decides.

```python
def _duplicate(c, existing):
    if c.claim_key:
        # a keyed candidate skips overlap: identical wording is a duplicate,
        # anything else is a correction for resolution to judge
        return Decision(IGNORE, "already known", 0.9) if c.content in existing else None
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

Overlap thresholds cannot separate "the same claim" from "the opposite claim" when both
sentences differ by one word.

**Bug 2: sensitivity ran last.** The same near-duplicate band also broke rule order.
The gate stops at the first rule that decides, and sensitivity sat last in the list,
so any candidate that first matched the near-duplicate band never reached it.
A trust boundary is checked first or not at all, so sensitivity now runs before every rule
that can end in a store:

```python
RULES = (_no_evidence, _sensitive, _derivable, _duplicate, _vague)
```

### What Changed

In section 4, a record was typed, validated, and then never touched.
It now has a start and an end: it can be corrected and superseded.
`status` stopped being decoration and became the field supersede writes.

---

## Per system

| | Graphiti / Zep | Claude Code auto memory |
| --- | --- | --- |
| **Pros** | Old facts stay queryable, so an answer can be dated. Contradictions resolve at write time. | Nothing to model. A correction is one file edit the user can undo. |
| **Cons** | Two clocks per edge is more schema, and the extractor has to set them. | No history: the previous claim is gone once the file is rewritten. |
| **Why** | Agent memory is a stream of corrections, so invalidation must be first class. | Assumes a human reviews the store, so the file itself is the audit trail. |
| **How: conflict** | New edges invalidate contradicted ones through the graph. | The model rewrites the affected memory file in place. |
| **How: time** | Bitemporal: event time and ingestion time both stored per edge. | One implicit clock, the file's own state. |
| **How: recovery** | The graph rebuilds from episodes, which stay whole. | Whatever version control the user keeps around the directory. |

---

## Failure modes

- **Overwrite in place.** The old claim is gone, so "why did it change" has no answer and a bad correction
  is unrecoverable. Supersede writes a timestamp instead of dropping the row.
- **Both claims stay active.** Retrieval returns two contradictions and the model picks arbitrarily.
  Conflict detection runs at write time, not at read time, so only one claim per key is ever active.
- **Supersede used for a wrong extraction.** The bad record closes as if it had once been true, which
  corrupts every `valid_at` query. Retract is a separate operation for a reason.
- **A correction blocked as a near-duplicate.** The write gate sees overlapping words and drops the
  correction. A keyed candidate is judged on exact wording instead, because a correction and the claim it
  corrects differ by one word and overlap says nothing useful about them.
- **A trust check placed after store rules.** The gate stops at its first match, and sensitivity ran last,
  so a sensitive candidate that first looked like a near-duplicate never reached it. Rules that can end
  in a store come last.
- **Cross-tenant supersede.** Two tenants store the same sentence and one closes the other's claim.
  Scope is checked before conflicts are computed, not after.
- **Claim keys drift.** `marcus:lives_in` and `marcus:location` never compete, so both stay active and
  the contradiction is invisible. The key comes from a controlled vocabulary, not from free text.
- **Identity unresolved.** "Marcus" and "Ming-Siang" get different keys and never conflict. Identity
  resolution has to run before conflict detection, or conflict detection is checking the wrong pairs.

---

## Runnable

[`src/`](src/) carries 03 forward and adds:

- [`resolve.py`](src/resolve.py): the operation vocabulary, `conflicts`, `supersede`, `retract`,
  `resolve`, and the two clock queries `as_of` and `valid_at`.
- [`records.py`](src/records.py): bitemporal fields, `claim_key`, and validation that rejects
  an impossible timeline.
- [`policy.py`](src/policy.py): a keyed claim is judged on exact wording, not overlap, and sensitivity is
  checked before any rule that can store. `words` drops stopwords, so a query cannot match on "the" alone.
- [`engine.py`](src/engine.py): stored records land on the timeline, every operation is logged to the
  ledger, and `believed_at` and `true_at` read the two clocks.
- [`test.py`](src/test.py): conflict detection and scope isolation, supersede against retract,
  the two clocks diverging on a backdated record, rejected timelines, and a correction closing a claim end to end.

```bash
python sections/05-temporal-resolution/src/test.py   # offline checks, no key
```

This section never calls the model, so there is no `demo.py`.

---

## Sources

- [Zep / Graphiti](https://arxiv.org/abs/2501.13956): bitemporal knowledge graph, edge invalidation instead of deletion.
- [A-Mem](https://arxiv.org/html/2502.12110v1): linked notes that evolve as new memories arrive.
- [Production memory track](../../README.md): the lifecycle this section belongs to.
