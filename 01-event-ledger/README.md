# 1 · Event ledger

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> Append every raw observation once. Never edit it. Everything downstream is a rebuildable view.

This page continues the [Production memory](../README.md) track: lifecycle stage 2, Capture,
the append-only event log every later stage reads from.

Extraction is lossy: a model deciding what to remember sometimes picks wrong or drops detail.
If distilled memories are the only copy, every extraction mistake is permanent,
because there is no raw record to check against.

[Section 9](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/) already ships the mini version: `log_run` appends each run's text to SQLite at run end,
and keyword search brings it back. At production scale that log is not enough. The ledger must:

1. Accept every kind of raw event, not just chat.
2. Stamp each row with whose memory it is.
3. Keep world time and system time as two separate fields.
4. Refuse edits at the database layer, not by convention.

---

## Mechanism

The simplest version: one SQLite table that only grows. The only write is `INSERT`.
A correction is a new row. A deletion is a downstream view dropping the row from its output (the row itself stays).

Four moving parts:

- **Scope**: whose memory this is. Tenant, user, and agent columns, stamped at append time, filtered at read time.
- **Event**: one immutable observation, with an id, a type, content, two timestamps, and metadata.
- **Ledger**: the handle with exactly two operations, `append` and `read`.
- **Two triggers**: `UPDATE` and `DELETE` abort inside SQLite, so immutability survives buggy callers.

The record types are plain dataclasses (the track README sketches them as Pydantic models, the runnable version stays stdlib):

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None

@dataclass(frozen=True)
class Event:
    id: str
    scope: Scope
    event_type: str
    content: str
    occurred_at: str | None    # true in the world since; None when unknown
    recorded_at: str           # known to the system since; always set
    metadata: dict
```

Immutability is a schema property, not a code-review hope:

```sql
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
```

`append` is one `INSERT`, no read-modify-write. The ledger stamps `recorded_at` itself,
so the caller cannot backdate what the system knew:

```python
def append(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
    event_id = uuid.uuid4().hex
    con = self._db()
    con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, scope.tenant_id, scope.user_id, scope.agent_id,
                 event_type, content, occurred_at, _now(), json.dumps(metadata or {})))
    con.commit()
    con.close()
    return event_id
```

`read` makes the tenant filter mandatory: every query starts from the tenant clause,
so another tenant's rows never reach a caller. Isolation is a query precondition, not a retrieval-quality feature:

```python
def read(self, scope, event_type=None, since=None) -> list[Event]:
    sql, args = "SELECT * FROM events WHERE tenant_id = ?", [scope.tenant_id]
    for column, value in (("user_id", scope.user_id), ("agent_id", scope.agent_id),
                          ("event_type", event_type)):
        if value is not None:
            sql += f" AND {column} = ?"
            args.append(value)
    ...
```

Data flows through the ledger in one direction:

```text
user turn · tool result · correction · approval · task outcome
        ↓ append (INSERT only)
events table: scoped rows, two timestamps, enforced append-only
        ↓ read (tenant filter mandatory)
write gate (stage 3) · consolidation (stage 6) · index rebuild (stage 7)
```

The harness appends at fixed capture points: a turn ends, a tool returns, the user corrects something, a task finishes.
Three kinds of readers come later in the lifecycle: the write gate reads fresh rows and proposes memories,
consolidation replays history to merge and supersede, and any corrupted index rebuilds itself by re-reading the rows.

### What Changed

Compared with section 9's session log:

- Section 9 keyed rows by session. The ledger keys them by scope, so tenants share one table without sharing rows.
- `event_type` widens capture beyond chat: tool results, corrections, approvals, and outcomes are evidence too.
- `occurred_at` and `recorded_at` split world time from system time, the base for temporal resolution (stage 5).
- Append-only moves from convention into schema. Triggers abort `UPDATE` and `DELETE` for every caller.
- Every row has an id, so derived memories can carry `source_event_ids` and point back at their evidence.

---

## Per system

MemMachine and Hermes both keep raw history behind their derived layers.
They differ on the unit: MemMachine keeps whole conversation episodes, Hermes keeps one chat row per message.

| | MemMachine | Hermes Agent |
| --- | --- | --- |
| **Pros** | A bad profile or index re-derives from episodes. Hits expand into their episode. | One extra table, no new service. Any past message is searchable, no model call. |
| **Cons** | Full episodes for every user cost storage and eventually need archiving. | Chat rows only, one flat log. No event types, no scope columns, no world-time field. |
| **Why** | Treats every derived layer as disposable, so full episodes stay the ground truth. | Assumes extraction misses facts, so raw history stays as the fallback store. |
| **How: unit** | Full conversation episodes, kept whole. | One row per message: session id, role, text. |
| **How: write time** | At ingestion, as conversations arrive. | Run end, one batch append per session. |
| **How: read back** | Contextual retrieval: find a match, then return surrounding context from the episode. | Keyword search with fuzzy ranking, best match first. |
| **How: derived views** | Profiles and indexes layered on top, rebuildable from episodes. | Two curated markdown memory files; the log is the raw backstop behind them. |

---

## Failure modes

- **The ledger grows forever.** Reads slow down and storage costs climb. Partition by scope and time, move cold segments
  to object storage, and keep the archive readable so rebuilds still work. Never silently truncate.
- **Sensitive data becomes immortal.** Append-only collides with deletion requests. Flag sensitivity at capture,
  and route legal deletion through one audited purge job. That job is the only allowed destructive path, and it logs what it removed.
- **Fabricated world time.** Guessing `occurred_at` poisons temporal resolution later (stage 5).
  Leave it empty when unknown. Only `recorded_at` is trusted, and only the ledger writes it.
- **Derived facts leak into the ledger.** A summary stored as an event looks like evidence forever.
  The ledger holds raw observations only; extracted memories live downstream and point back with source event ids.
- **Two writers race.** Concurrent appends can lock the database. Keep append a single `INSERT` with no read-modify-write,
  and enable WAL mode when more than one process writes.

---

## Runnable

[`src/`](src/) carries 00 forward and adds:

- [`ledger.py`](src/ledger.py): `Event`, `Ledger.append`, `Ledger.read`, and the append-only triggers.
- [`engine.py`](src/engine.py): the integration point starts here. `observe()` is a ledger append.
- [`test.py`](src/test.py): scope isolation, the two clocks, database-enforced immutability,
  a view rebuilt per scope, and `observe()` landing in the ledger.

```bash
python tracks/production-memory/01-event-ledger/src/test.py   # offline checks, no key
```

This stage never calls the model, so there is no `demo.py`.

---

## Sources

- [MemMachine](https://arxiv.org/abs/2604.04853): full episodes as ground truth, derived layers rebuild from them.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `hermes_state.py` (`SessionDB`), the session log this page scales up.
- [Section 9 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/): `log_run` and `search_sessions`, the mini ledger.
- [Production memory track](../README.md): the full lifecycle this stage belongs to.
