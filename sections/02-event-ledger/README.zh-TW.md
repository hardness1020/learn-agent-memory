# 2 · Event ledger

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 原始觀察只記一次，記了就不改。下游所有東西都是可以重建的 view。

這一章是 [Production memory](../../README.zh-TW.md) track 的第二章：
lifecycle 的 Capture，一份只加不改的 event log，後面每一章都從這裡讀資料。

extraction 一定會漏：模型挑「什麼值得記」的時候，會挑錯、也會漏掉細節。
如果系統只留提煉過的 memory，錯一次就永遠錯了，因為沒有原始紀錄可以回頭核對。

迷你版就是一份 session log：執行結束時把整輪文字存進 SQLite，
之後用關鍵字搜回來。到 production 規模，這樣還不夠。ledger 必須做到：

1. 收得下各種原始 event，不只聊天。
2. 每一筆都標清楚是誰的 memory。
3. 世界上什麼時候發生、系統什麼時候知道，分成兩個欄位記。
4. 「不能改」由資料庫直接擋下來，不是靠大家自律。

---

## 機制

最簡單的版本：一張只會變長的 SQLite 表，唯一的寫入操作是 `INSERT`。
要更正，就加一筆新的。要刪除，就讓下游的 view 不再輸出那一筆，那一筆本身還在。

四個零件：

- **Scope**：這筆資料是誰的。tenant、user、agent 三個欄位，寫入時蓋上，讀取時過濾。
- **Event**：一筆不可變的觀察，帶 id、類型、內容、兩個時間戳和 metadata。
- **Ledger**：操作只有兩個，`append` 和 `read`。
- **兩個 trigger**：`UPDATE` 和 `DELETE` 在 SQLite 裡面直接被中止，呼叫端寫錯程式也改不了資料。

紀錄型別就是普通的 dataclass（track 的 README 拿 Pydantic model 示意，可執行版本只用標準函式庫）：

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

「不可變」是 schema 的性質，不是指望 code review 幫你守住的：

```sql
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
```

`append` 就是一句 `INSERT`，不先讀再寫。
`recorded_at` 由 ledger 自己蓋，所以呼叫端沒辦法倒填「系統什麼時候知道的」：

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

`read` 強制帶 tenant 過濾：每個查詢都從 tenant 條件開始，別的 tenant 的資料根本到不了呼叫端手上。
隔離是查詢的前提，不是靠檢索品質補救的事：

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

資料只往一個方向流：

```text
user turn · tool result · correction · approval · task outcome
        ↓ append (INSERT only)
events table: scoped rows, two timestamps, enforced append-only
        ↓ read (tenant filter mandatory)
write gate (section 3) · consolidation (section 6) · index rebuild (section 7)
```

harness 在幾個固定的點呼叫 `append`：一輪結束、tool 回傳、使用者糾正、任務完成。
讀的人有三種，都在 lifecycle 的後段。write gate 讀剛寫入的幾筆，提出要記什麼。
consolidation 重放整段歷史，做合併和取代。index 壞了，就重讀這張表把自己重建回來。

### What Changed

跟一份單純的 session log 比：

- session log 用 session 當 key。ledger 改用 scope 當 key，很多 tenant 共用一張表，但誰也讀不到別人那幾筆。
- `event_type` 讓 ledger 收得下聊天以外的證據：tool 結果、糾正、核准、任務成敗都算。
- `occurred_at` 和 `recorded_at` 把世界時間和系統時間分開，第 5 章的時間推理就建在這上面。
- 只加不改從慣例變成 schema：trigger 對每個呼叫端都擋 `UPDATE` 和 `DELETE`。
- 每一筆都有自己的 id，之後提煉出來的 memory 帶著 `source_event_ids`，指得回自己的證據。

---

## 各系統做法

MemMachine 和 Hermes 都在衍生層背後留著一份原始歷史。
差別在單位：MemMachine 留整段對話 episode，Hermes 一句訊息存一列。

| | MemMachine | Hermes Agent |
| --- | --- | --- |
| **Pros** | profile 或 index 壞了，從 episode 重新算回來就好。命中一筆，還能展開它前後的上下文。 | 現有資料庫多一張表就好，不用新服務。過去每句話都搜得到，不用呼叫模型。 |
| **Cons** | 每個使用者都存完整 episode，空間吃得兇，遲早要歸檔。 | 只收聊天訊息，一條扁平的 log。沒有 event 類型、沒有 scope 欄位、沒有世界時間。 |
| **Why** | 把所有衍生層都當成可拋棄的，所以完整 episode 就是 ground truth。 | 假設 extraction 會漏，所以原始歷史留著當備援。 |
| **How: 單位** | 完整的對話 episode，整段保留。 | 一句訊息一列：session id、角色、文字。 |
| **How: 寫入時機** | 資料進來的當下，對話來一段寫一段。 | 執行結束時，一個 session 批次寫一次。 |
| **How: 讀回來** | 情境式檢索：先找到命中點，再回傳 episode 裡它前後的內容。 | 關鍵字搜尋加模糊排序，最像的排最前面。 |
| **How: 衍生 view** | profile 和 index 疊在上面，隨時能從 episode 重建。 | 另外維護兩份 markdown memory 檔案，log 是它們背後的原始備援。 |

---

## 哪裡會出錯

- **ledger 無限長大：**讀取變慢，儲存變貴。
  照 scope 和時間切段，冷的那幾段搬去 object storage，但歸檔要保持讀得到，重建時才用得上。
  絕對不要不聲不響地砍舊資料。
- **敏感資料永遠留著：**只加不改跟「使用者要求刪除」會打架。
  capture 時就標記敏感度，法規要求的刪除，統一走一個有稽核的 purge 作業。
  那是唯一允許的破壞性路徑，而且它會記下自己刪了什麼。
- **世界時間用猜的：**`occurred_at` 亂填，第 5 章的時間推理會被污染。不知道就留空。
  只有 `recorded_at` 可信，而且只有 ledger 自己能寫。
- **提煉過的結論混進 ledger：**一段摘要被當成 event 寫進來，之後看起來就永遠像證據。
  ledger 只收原始觀察。提煉的 memory 放在下游，用 source event id 指回來。
- **兩個寫入端搶同一個檔：**同時 append 可能把資料庫鎖住。
  append 維持一句 `INSERT` 就好，不先讀再寫，多個 process 同時寫就開 WAL 模式。

---

## 可執行程式

[`src/`](src/) 承接 00，加入：

- [`ledger.py`](src/ledger.py)：`Event`、`Ledger.append`、`Ledger.read`，和兩個 append-only trigger。
- [`engine.py`](src/engine.py)：整合點從這裡開始。`observe()` 就是一次 ledger append。
- [`test.py`](src/test.py)：驗 scope 隔離、兩個時間戳、資料庫層面的不可變、
  按 scope 各自重建的 view，和 `observe()` 有落進 ledger。

```bash
python sections/02-event-ledger/src/test.py   # offline checks, no key
```

這一章完全不會呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [MemMachine](https://arxiv.org/abs/2604.04853)：完整 episode 當 ground truth，衍生層都從它重建。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：
  `hermes_state.py`（`SessionDB`），這一章放大的那份 session log。
- [Production memory track](../../README.zh-TW.md)：這一章所屬的完整 lifecycle。
