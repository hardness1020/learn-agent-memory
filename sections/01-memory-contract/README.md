# 1 · Memory contract

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> Two decisions come before any mechanism: whose memory is this, and what surface hides the machinery.

This page opens the [Production memory](../../README.md) track: lifecycle section 1, Scope,
plus the engine surface from the track's core abstractions.
Every later section carries this page's `contract.py` forward.

The worst memory failures are not retrieval failures. They are scope failures:
one user's facts surfacing in another user's turn. Retrieval quality cannot fix that:
the more relevant the answer, the worse the leak.

The second decision is the surface. Ten sections of machinery run behind memory,
but a harness should see three verbs. Once a caller queries an index directly,
that index is stuck: any change breaks the caller, so it can never be rebuilt or replaced,
and the track's one rule (events kept, views rebuildable) dies.

So, two contracts before any mechanism:

1. Every record carries a scope, every read filters by one.
2. All access goes through `observe`, `recall`, and `consolidate`.

---

## Mechanism

The simplest version: one frozen dataclass and one protocol.

Scope says whose memory a thing is. It is frozen because a scope is an identity:
usable as a dict key, never mutated mid-flight:

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
```

The axes nest: a tenant contains users, a user may run several agents.
`None` widens the filter (a tenant-wide read), a value narrows it. Only tenant is required.

The engine surface is three verbs, defined as a `Protocol`.
Conformance is structural: any object with these methods is an engine, no inheritance required:

```python
@runtime_checkable
class MemoryEngine(Protocol):
    def observe(self, scope: Scope, event_type: str, content: str) -> str: ...
    def recall(self, scope: Scope, query: str) -> str: ...
    def consolidate(self, scope: Scope) -> dict: ...
```

Each verb hides a run of the lifecycle:

```text
observe      raw evidence in            capture, gate, encode      (sections 2-4)
recall       evidence-backed text out   index, retrieve, assemble  (sections 7-9)
consolidate  background maintenance     resolve, consolidate       (sections 5-6, cold clock)
```

The small surface is what keeps the rule alive. Callers only see the verbs,
so everything behind them is replaceable: a corrupted index rebuilds, a store migrates,
a resolver gets rewritten, and no caller changes.

Each section folder carries this file plus everything before it, and evolves one `engine.py`.
Diff two adjacent sections' `src/` and the diff is that section's mechanism.

---

## Per system

Claude Code and Hermes are both local, single-tenant tools, so the tenant axis
looks optional in them. The track's contract makes it mandatory:
production serves many tenants from one system.

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **Pros** | Project-scoped stores keep repos from contaminating each other. | One profile follows the user everywhere they talk to the agent. |
| **Cons** | Cross-project facts about the same user split across stores. | No project separation: one bucket for everything the user does. |
| **Why** | Memory serves the working directory: context is the project. | Memory serves the relationship: context is the person. |
| **How: scope unit** | Per user, per project directory. | Per user, across channels. |
| **How: isolation** | Separate memory directories per project path. | Separate state per user in the local database. |
| **How: tenancy** | Single tenant: the local machine. | Single tenant: the operator's deployment. |

---

## Failure modes

- **No scope at all.** The founding failure: one user's facts in another user's turn.
  Scope is a required argument on every verb, not a filter someone remembers to add.
- **Scope lives in the content.** "Marcus's staging key is..." stored under no user.
  Filters cannot see into prose. Scope is structured metadata, stamped at write time.
- **Scope too coarse.** Tenant-only scoping lets one tenant's users see each other.
  Carry the user and agent axes from day one, even while they are `None`.
- **Callers bypass the surface.** A dashboard queries the index directly, and now the index
  cannot be rebuilt without breaking it. Every consumer goes through the three verbs.
- **A surface with no guarantees.** Three verbs alone promise nothing. The contract's rule
  (events kept, views rebuildable) is what later sections test, section by section.

---

## Runnable

[`src/`](src/) starts the chain that every later section carries forward:

- [`contract.py`](src/contract.py): `Scope` and the `MemoryEngine` protocol.
- [`test.py`](src/test.py): scope identity and immutability, structural conformance
  with a toy engine, and scope-filtered recall in miniature.

```bash
python sections/01-memory-contract/src/test.py   # offline checks, no key
```

This section never calls the model, so there is no `demo.py`.

---

## Sources

- [MemMachine](https://arxiv.org/abs/2604.04853): memory as a subsystem serving many users and agents behind one surface.
- [Claude Code memory](https://docs.claude.com/en/docs/claude-code/memory): file-based memory, scoped per project and per user.
- [Production memory track](../../README.md): section 1 and the core abstractions this page implements.
