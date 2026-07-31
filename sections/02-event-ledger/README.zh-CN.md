# 2 · Event ledger

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 原始观察只记一次，记了就不改。下游所有东西都是可以重建的 view。

这一章是 [Production memory](../../README.zh-CN.md) track 的第二章：
lifecycle 的 Capture，一份只加不改的 event log，后面每一章都从这里读数据。

extraction 一定会漏：模型挑「什么值得记」的时候，会挑错、也会漏掉细节。
如果系统只留提炼过的 memory，错一次就永远错了，因为没有原始记录可以回头核对。

迷你版就是一份 session log：运行结束时把整轮文字存进 SQLite，
之后用关键字搜回来。到 production 规模，这样还不够。ledger 必须做到：

1. 收得下各种原始 event，不只聊天。
2. 每一笔都标清楚是谁的 memory。
3. 世界上什么时候发生、系统什么时候知道，分成两个字段记。
4. 「不能改」由数据库直接挡下来，不是靠大家自律。

---

## 机制

最简单的版本：一张只会变长的 SQLite 表，唯一的写入操作是 `INSERT`。
要更正，就加一笔新的。要删除，就让下游的 view 不再输出那一笔，那一笔本身还在。

四个零件：

- **Scope**：这笔数据是谁的。tenant、user、agent 三个字段，写入时盖上，读取时过滤。
- **Event**：一笔不可变的观察，带 id、类型、内容、两个时间戳和 metadata。
- **Ledger**：操作只有两个，`append` 和 `read`。
- **两个 trigger**：`UPDATE` 和 `DELETE` 在 SQLite 里面直接被中止，调用方写错代码也改不了数据。

记录类型就是普通的 dataclass（track 的 README 拿 Pydantic model 示意，可运行版本只用标准库）：

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

「不可变」是 schema 的性质，不是指望 code review 帮你守住的：

```sql
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
```

`append` 就是一句 `INSERT`，不先读再写。
`recorded_at` 由 ledger 自己盖，所以调用方没办法倒填「系统什么时候知道的」：

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

`read` 强制带 tenant 过滤：每个查询都从 tenant 条件开始，别的 tenant 的数据根本到不了调用方手上。
隔离是查询的前提，不是靠检索质量补救的事：

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

数据只往一个方向流：

```text
user turn · tool result · correction · approval · task outcome
        ↓ append (INSERT only)
events table: scoped rows, two timestamps, enforced append-only
        ↓ read (tenant filter mandatory)
write gate (section 3) · consolidation (section 6) · index rebuild (section 7)
```

harness 在几个固定的点调用 `append`：一轮结束、tool 返回、用户纠正、任务完成。
读的人有三种，都在 lifecycle 的后段。write gate 读刚写入的几笔，提出要记什么。
consolidation 重放整段历史，做合并和取代。index 坏了，就重读这张表把自己重建回来。

### What Changed

跟一份单纯的 session log 比：

- session log 用 session 当 key。ledger 改用 scope 当 key，很多 tenant 共用一张表，但谁也读不到别人那几笔。
- `event_type` 让 ledger 收得下聊天以外的证据：tool 结果、纠正、批准、任务成败都算。
- `occurred_at` 和 `recorded_at` 把世界时间和系统时间分开，第 5 章的时间推理就建在这上面。
- 只加不改从惯例变成 schema：trigger 对每个调用方都挡 `UPDATE` 和 `DELETE`。
- 每一笔都有自己的 id，之后提炼出来的 memory 带着 `source_event_ids`，指得回自己的证据。

---

## 各系统做法

MemMachine 和 Hermes 都在衍生层背后留着一份原始历史。
差别在单位：MemMachine 留整段对话 episode，Hermes 一句消息存一行。

| | MemMachine | Hermes Agent |
| --- | --- | --- |
| **Pros** | profile 或 index 坏了，从 episode 重新算回来就好。命中一笔，还能展开它前后的上下文。 | 现有数据库多一张表就好，不用新服务。过去每句话都搜得到，不用调用模型。 |
| **Cons** | 每个用户都存完整 episode，空间吃得凶，迟早要归档。 | 只收聊天消息，一条扁平的 log。没有 event 类型、没有 scope 字段、没有世界时间。 |
| **Why** | 把所有衍生层都当成可抛弃的，所以完整 episode 就是 ground truth。 | 假设 extraction 会漏，所以原始历史留着当备份。 |
| **How: 单位** | 完整的对话 episode，整段保留。 | 一句消息一行：session id、角色、文字。 |
| **How: 写入时机** | 数据进来的当下，对话来一段写一段。 | 运行结束时，一个 session 批次写一次。 |
| **How: 读回来** | 情境式检索：先找到命中点，再返回 episode 里它前后的内容。 | 关键字搜索加模糊排序，最像的排最前面。 |
| **How: 衍生 view** | profile 和 index 叠在上面，随时能从 episode 重建。 | 另外维护两份 markdown memory 文件，log 是它们背后的原始备份。 |

---

## 哪里会出错

- **ledger 无限长大：**读取变慢，存储变贵。
  照 scope 和时间切段，冷的那几段搬去 object storage，但归档要保持读得到，重建时才用得上。
  绝对不要不声不响地砍旧数据。
- **敏感数据永远留着：**只加不改跟「用户要求删除」会打架。
  capture 时就标记敏感度，法规要求的删除，统一走一个有审计的 purge 作业。
  那是唯一允许的破坏性路径，而且它会记下自己删了什么。
- **世界时间用猜的：**`occurred_at` 乱填，第 5 章的时间推理会被污染。不知道就留空。
  只有 `recorded_at` 可信，而且只有 ledger 自己能写。
- **提炼过的结论混进 ledger：**一段摘要被当成 event 写进来，之后看起来就永远像证据。
  ledger 只收原始观察。提炼的 memory 放在下游，用 source event id 指回来。
- **两个写入端抢同一个文件：**同时 append 可能把数据库锁住。
  append 维持一句 `INSERT` 就好，不先读再写，多个进程同时写就开 WAL 模式。

---

## 可执行程序

[`src/`](src/) 承接 00，加入：

- [`ledger.py`](src/ledger.py)：`Event`、`Ledger.append`、`Ledger.read`，和两个 append-only trigger。
- [`engine.py`](src/engine.py)：整合点从这里开始。`observe()` 就是一次 ledger append。
- [`test.py`](src/test.py)：验 scope 隔离、两个时间戳、数据库层面的不可变、
  按 scope 各自重建的 view，和 `observe()` 有落进 ledger。

```bash
python sections/02-event-ledger/src/test.py   # offline checks, no key
```

这一章完全不会调用模型，所以没有 `demo.py`。

---

## 来源

- [MemMachine](https://arxiv.org/abs/2604.04853)：完整 episode 当 ground truth，衍生层都从它重建。
- [Hermes Agent 源码](https://github.com/NousResearch/hermes-agent)：
  `hermes_state.py`（`SessionDB`），这一章放大的那份 session log。
- [Production memory track](../../README.zh-CN.md)：这一章所属的完整 lifecycle。
