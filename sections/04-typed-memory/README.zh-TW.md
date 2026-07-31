# 4 · Typed memory

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 一個純文字桶回答不了三種不同的問題。memory 要照「它回答什麼」分型，也要照「系統怎麼知道的」分型。

這一章講 [Production memory](../../README.zh-TW.md) track 裡 lifecycle 的 Encode：
把通過 write gate（第 3 章）的 candidate，變成有類型、驗證過的紀錄。

不分型的儲存區會把「上週二部署失敗了」、「Marcus 主要寫 Python」、
「跑 schema migration 前先檢查 migration lock」全部當成同一種東西：一段字串。
但這三句回答的問題不同，老化的方式不同，檢索的方式也不同。
儲存區要是分不清「觀察到的」和「模型自己猜的」，遲早會把猜測講成事實。

檔案式的 agent memory，分類標準通常是「什麼值得留」（user、feedback、project、reference）。
這一章改用「怎麼用」來分：

1. 每筆紀錄歸進一種功能 kind：發生過什麼、現在相信什麼、下次該怎麼做。
2. 另外記 epistemic type：這句是觀察到的、驗證過的、使用者的偏好、系統推論的，還是主觀意見。
3. 建構時就驗證。沒有證據或類型不合法的紀錄，根本存不進來。
4. 保留出處：每筆紀錄都指得回它來自哪些 source event。

---

## 機制

最簡單的版本：兩組 enum，加一個會驗證的建構函式。

三種功能 kind：

```text
episodic     發生過什麼。「部署卡在 migration lock，最後用 rollback 解掉。」
             時間、環境、結果都留著。它過時是變成歷史，不是變成錯的。
semantic     現在相信什麼。「Marcus 主要寫 Python。」
             從 episode 蒸餾出來，可以修：新證據能取代它。
procedural   下次該怎麼做。「跑 schema migration 前先檢查 migration lock。」
             workflow、runbook、gotcha。下次遇到類似情況就用得上。
```

epistemic type 是第二組標籤，跟 kind 各管各的。kind 回答「要拿來做什麼」，epistemic type 回答「我們怎麼知道的」：

```text
evidence     原始觀察，直接來自 ledger
fact         驗證過的，或使用者親口說的
preference   使用者想要什麼；記的是偏好，不是事實
inference    系統自己的猜測，可以修，而且要標明是猜的
opinion      主觀看法，系統永遠不會悄悄把它升級成事實
```

紀錄把 kind、epistemic type 和出處綁在一起。
`make_record` 是唯一的建構入口，所以只要紀錄存在，就一定驗證過：

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: Scope
    kind: str                  # episodic / semantic / procedural
    epistemic_type: str        # evidence / fact / preference / inference / opinion
    content: str
    source_event_ids: tuple    # the evidence, from section 2
    confidence: float
    recorded_at: str
    status: str = "active"     # active / superseded / retracted
    tags: tuple = ()

def make_record(scope, kind, epistemic_type, content, source_event_ids,
                confidence, tags=()) -> MemoryRecord:
    return validate(MemoryRecord(...))
```

驗證跟第 3 章是同一套模式：規則寫死的檢查，一出錯就直接拋例外。
沒有 source event 的紀錄直接退回，因為講不出證據的 memory 就是謠言：

```python
def validate(record) -> MemoryRecord:
    if record.kind not in KINDS:
        raise ValueError(f"unknown kind: {record.kind}")
    ...
    if not record.source_event_ids:
        raise ValueError("a record without source events cannot cite its evidence")
```

儲存區照 kind 分流，照 scope 和 status 過濾。`grounded` 把知識和猜測切開：

```python
def current(self, scope, kind) -> list[MemoryRecord]:
    """Active records of one kind for one scope, newest first."""

def grounded(records) -> list[MemoryRecord]:
    return [r for r in records if r.epistemic_type in ("evidence", "fact")]
```

資料這樣流：write gate（第 3 章）放行的 candidate 進到這一章，蓋上 kind 和 epistemic type，變成正式的紀錄。
之後 resolution（第 5 章）補上 bitemporal 欄位（事情何時為真、何時記下），supersede 時改寫 `status`。
retrieval（第 8 章）按 kind 分路查詢，context assembly（第 9 章）把 epistemic 標籤印在內容旁邊，
讓模型看得出哪些是觀察、哪些是系統自己的推論。

### What Changed

跟那套分類比，變的是問題本身：四種檔案類型回答「值不值得留」，
kind 回答「要拿來做什麼」，epistemic type 回答「我們怎麼知道的」。
「值不值得留」的判斷已經搬去第 3 章，這一章只管紀錄長什麼樣子。
[Hindsight](https://arxiv.org/html/2512.12818v1) 劃的是同一條線：發生過的事，永遠不跟 agent 對它的想法混在一起。

---

## 各系統做法

| | LangMem | Hindsight |
| --- | --- | --- |
| **Pros** | 寫入當下就照 app 自訂的 schema 驗證。 | 發生過的事不會跟 agent 的想法混在一起。信念可以修，經驗留著不動。 |
| **Cons** | 類型幫得上多少忙，取決於 app 定義的 schema 好不好。 | 庫分成四個，分流就變多；一分錯，條目就落在錯的庫裡。 |
| **Why** | 每個 app 要存的 memory 都長得不一樣，所以 schema 由 app 提供。 | 分不清觀察和意見的 agent，會把猜測講成事實。 |
| **How: 類型** | semantic、episodic、procedural 三種 memory 類型。 | world fact、experience、observation、opinion 分成四個庫。 |
| **How: 單位** | 每個使用者一份 profile 文件，或一批照 schema 驗證的紀錄。 | 各庫裡分了型的條目，由 reflection 這道程序寫入。 |
| **How: 更新** | profile 就地修補；collection 新增或更新紀錄。 | reflection 讀新證據、修訂信念，並引用它讀過的東西。 |

---

## 哪裡會出錯

- **什麼都變成 semantic：**一桶裝所有東西的儲存區又悄悄回來了。寫入時就要分流：
  帶時間戳的結果是 episodic，帶觸發條件的指示是 procedural。
- **把推論存成事實：**系統會把模型的猜測當成事實講出去。
  所以 epistemic type 在建構時必填，assembly（第 9 章）還會把它印在內容旁邊。
- **有類型但沒出處：**引用不了任何證據的紀錄，沒辦法查證、沒辦法取代，也不能信。
  只要 `source_event_ids` 是空的，驗證一律退回，沒有例外。
- **schema 一直改：**每加一個欄位或新 kind，舊紀錄就對不上新 schema。
  衍生紀錄都能從 ledger（第 2 章）重建，所以 schema 遷移只是重放一次，不用搬資料。
- **procedure 丟了觸發條件：**「檢查 migration lock」少了「跑 schema migration 前」，
  系統就不知道什麼時候該用它，這條指令永遠派不上用場。
  procedural 紀錄要連條件一起留，不是只留指令。

---

## 可執行程式

[`src/`](src/) 接手 02 的整條程式鏈，再加入：

- [`records.py`](src/records.py)：兩組 enum、`MemoryRecord`、`make_record`、`validate`、`grounded` 和 `TypedStore`。
- [`engine.py`](src/engine.py)：通過 gate 的 candidate 在這裡變成驗證過的 typed record。
  `observe()` 這條路到這裡就完整了：event、gate、紀錄。
- [`test.py`](src/test.py)：驗證退回壞的 kind、epistemic type、信心值和沒出處的紀錄；
  kind 分流加 scope 隔離；grounded 的切分；status 控制哪些紀錄查得到；
  engine 把過關的 candidate 存成 typed record，信心值沿用 gate 的決定。

```bash
python sections/04-typed-memory/src/test.py   # offline checks, no key
```

這一章完全不會呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [Hindsight](https://arxiv.org/html/2512.12818v1)：fact、experience、observation、opinion 的 epistemic 切分。
- [LangMem](https://github.com/langchain-ai/langmem)：照 schema 驗證的 memory，profile 與 collection。
- [Production memory track](../../README.zh-TW.md)：這一章所在的 lifecycle。
