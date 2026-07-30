# 3 · Typed memory

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> One text bucket cannot answer three different questions. Type the memory by what it answers, and by how the system knows it.

This page covers lifecycle stage 4 of the [Production memory](../README.md) track,
Encode: turning a candidate that survived the write gate (stage 3) into a typed, validated record.

An untyped store treats "the deploy failed last Tuesday", "Marcus mainly writes Python",
and "check migration locks before schema migrations" as the same thing: a string.
But they answer different questions, age differently, and get retrieved differently.
A store that cannot tell an observation from the model's own guess will eventually state guesses as facts.

[Section 9](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/) typed memories by what is worth keeping (user, feedback, project, reference).
This stage types them by how they are used:

1. Route each record into a functional kind: what happened, what is believed, what to do.
2. Record epistemic status: observed, verified, preferred, inferred, or opined.
3. Validate at construction. No record exists without evidence and a legal type.
4. Keep provenance: every record cites the source events it came from.

---

## Mechanism

The simplest version: two enums and a validating constructor.

Three functional kinds:

```text
episodic     what happened.            "Deploy hit a migration lock, fixed by rollback."
             keeps time, environment, outcome. ages by becoming history, not wrong.
semantic     what is currently believed. "Marcus mainly writes Python."
             distilled from episodes. revisable: new evidence can supersede it.
procedural   what to do next time.     "Check migration locks before schema migrations."
             workflows, runbooks, gotchas. pays off on the next similar run.
```

The epistemic axis is separate, because "what kind of claim is this" is independent of "what is it for":

```text
evidence     raw observation, straight from the ledger
fact         verified, or stated directly by the user
preference   what the user wants, not what is true
inference    the system's guess, revisable and marked as such
opinion      a subjective view, never promoted silently
```

The record ties kind, epistemic status, and provenance together.
`make_record` is the only constructor, so no record exists unvalidated:

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: Scope
    kind: str                  # episodic / semantic / procedural
    epistemic_type: str        # evidence / fact / preference / inference / opinion
    content: str
    source_event_ids: tuple    # the evidence, from stage 2
    confidence: float
    recorded_at: str
    status: str = "active"     # active / superseded / retracted
    tags: tuple = ()

def make_record(scope, kind, epistemic_type, content, source_event_ids,
                confidence, tags=()) -> MemoryRecord:
    return validate(MemoryRecord(...))
```

Validation is the same pattern as stage 3: deterministic checks, loud failures.
A record without source events is rejected, because a memory that cannot cite its evidence is a rumor:

```python
def validate(record) -> MemoryRecord:
    if record.kind not in KINDS:
        raise ValueError(f"unknown kind: {record.kind}")
    ...
    if not record.source_event_ids:
        raise ValueError("a record without source events cannot cite its evidence")
```

The store routes by kind and filters by scope and status. `grounded` splits knowledge from guesses:

```python
def current(self, scope, kind) -> list[MemoryRecord]:
    """Active records of one kind for one scope, newest first."""

def grounded(records) -> list[MemoryRecord]:
    return [r for r in records if r.epistemic_type in ("evidence", "fact")]
```

Data flow: the write gate (stage 3) emits stored candidates; this stage constructs the record and stamps kind and epistemic status.
Resolution (stage 5) later adds the bitemporal fields (when true, when recorded) and flips `status` on supersede.
Retrieval (stage 8) routes queries by kind, and context assembly (stage 9) prints the epistemic label,
so the model can see which claims are observations and which are the system's own inferences.

### What Changed

Compared with section 9, the question itself changed: the four file types answered "is this worth keeping".
Kind answers "how will this be used", and the epistemic axis answers "how do we know it".
Selection criteria moved into stage 3; this stage is purely about shape.
[Hindsight](https://arxiv.org/html/2512.12818v1) draws the same line: what happened never blends into what the agent thinks about it.

---

## Per system

| | LangMem | Hindsight |
| --- | --- | --- |
| **Pros** | Records validate against app schemas at write time. | What happened never blends with what the agent thinks. Beliefs revise, experience stays. |
| **Cons** | Types only help as much as the schemas the app defines. | Four banks mean more routing, and a misfiled entry lands in the wrong one. |
| **Why** | Memory shape differs per app, so the store takes the app's schema. | An agent that cannot tell observation from opinion states guesses as facts. |
| **How: kinds** | Semantic, episodic, and procedural memory types. | World facts, experiences, observations, and opinions in separate banks. |
| **How: unit** | A profile document per user, or a collection of schema-validated records. | Typed entries per bank, written by a reflection pass. |
| **How: update** | Profiles patch in place; collections add or update records. | Reflection reads new evidence and revises beliefs, citing what it read. |

---

## Failure modes

- **Everything becomes semantic.** The one-bucket store returns through the back door. Route at write time:
  outcomes with a timestamp are episodic, instructions with a trigger are procedural.
- **An inference is stored as fact.** The model's guess gets recited as truth. The epistemic type is mandatory
  at construction, and assembly (stage 9) prints it next to the content.
- **Typed but sourceless.** A record that cites nothing cannot be checked, superseded, or trusted.
  Validation rejects empty `source_event_ids`, no exceptions.
- **Schema churn.** A new field or kind strands old records. Derived records rebuild from the ledger (stage 2),
  so a schema migration is a replay, not a data migration.
- **Procedures lose their trigger.** "Check migration locks" without "before schema migrations" never fires:
  nothing says when the instruction applies. A procedural record keeps the condition, not just the instruction.

---

## Runnable

[`src/`](src/) carries 02 forward and adds:

- [`records.py`](src/records.py): the two axes, `MemoryRecord`, `make_record`, `validate`, `grounded`, and `TypedStore`.
- [`engine.py`](src/engine.py): gate survivors become validated typed records.
  The `observe()` path is now complete: event, gate, record.
- [`test.py`](src/test.py): validation rejecting bad kinds, types, confidence, and sourceless records;
  kind routing with scope isolation; the grounded split; status gating visibility;
  and the engine storing typed survivors with the decision's confidence.

```bash
python tracks/production-memory/03-typed-memory/src/test.py   # offline checks, no key
```

This stage never calls the model, so there is no `demo.py`.

---

## Sources

- [Hindsight](https://arxiv.org/html/2512.12818v1): the epistemic split between facts, experiences, observations, and opinions.
- [LangMem](https://github.com/langchain-ai/langmem): schema-validated memories, profiles and collections.
- [Section 9 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/): the four keep-worthiness types this stage generalizes.
- [Production memory track](../README.md): the lifecycle this stage belongs to.
