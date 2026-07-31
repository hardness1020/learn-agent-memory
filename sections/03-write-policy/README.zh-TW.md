# 3 · Write policy

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 沒有任何東西會「順手」變成 memory。每一筆 candidate 都要有決定、有理由、有紀錄。

這一章是 [Production memory](../../README.zh-TW.md) track 的第三章：lifecycle 的 Write gate。
一筆原始證據要不要存成 memory，由這道 gate 做出明確的決定。
送進 gate 的東西叫 candidate：從新進的 event 整理出來、等著決定要不要存的一筆內容。

這道 gate 存太多、存太少都會出問題。什麼都存，recall 撈回來的就是一堆雜訊和過期資料，成本也跟著漲。
存得太少，agent 就會一直重問已經回答過的問題。
而且只要寫入是靜悄悄發生的，就沒有人答得出「它為什麼記得這個」、「它為什麼忘了那個」。

迷你版的做法，是把一套類型規則放進 extractor 的 prompt：
只有四種類型、而且推導不回來的事實才存。到 production 規模，這道 gate 得做更多事：

1. 幫 candidate 打分，每個標準各自有名字、分開評，不是一句模糊的「重不重要」。
2. 產出有型別的決定，包含「先擱著」和「要人核准」，不是只有存或不存。
3. 每個決定在寫入前都要驗證，不管是規則做的還是模型做的。
4. 每個決定都留下理由，被拒絕的也要記。

---

## 機制

最簡單的版本是一個函式：收一筆 candidate，回傳一個決定。設計的重點在檢查的先後順序。
規則先跑，因為不用花錢、結果也固定。規則判不了的，才輪到模型。
validator 在任何東西寫入之前，把每個決定再驗一次。

五個零件：

- **Candidate**：一筆提議要存的 memory，從新進的 ledger event 整理出來，帶著來源 event 的 id。
- **規則**：確定性的檢查，照成本排序。看有沒有證據、推不推導得回來、重不重複、夠不夠具體、敏不敏感。
- **classifier**：一次模型呼叫，只處理規則判不了的問題（下次還用得到，還是閒聊？）。
- **Decision**：動作、理由、信心。動作有四種：`store`、`ignore`、`defer`、`require_approval`。
- **validator**：擋在決定和寫入之間的那道確定性檢查。

track README 列的六個打分標準，剛好拆到不同層：

```text
Novelty       規則：跟現有 memory 比字詞重疊
Specificity   規則：太模糊，之後根本搜不回來
Derivability  規則：grep 或 git 能再查到的不用存
Sensitivity   規則：有標記的要人核准
Durability    classifier：下次還用得到，還是閒聊？
Confidence    掛在決定上，誰做的決定誰填
```

兩種紀錄的型別都很小：

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

`decide` 依序走規則。規則都沒意見就交給 classifier，沒接 classifier 就預設存：

```python
def decide(candidate, existing=(), classifier=None) -> Decision:
    for rule in RULES:                 # evidence, derivability, duplicate, vagueness, sensitivity
        if decision := rule(candidate, existing):
            return decision
    if classifier is not None:
        return Decision(*classifier(candidate))
    return Decision(STORE, "novel, specific, evidence-backed", 0.6)
```

檢查重複的那條規則有兩道門檻。重疊很高，代表已經知道了，直接略過。
中等重疊就先擱著：把相近的 memory 合併是 consolidation（第 6 章）的工作，不是這道 gate 的工作：

```python
def _duplicate(c, existing):
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

`validate` 對每個決定都跑，不管是誰做的。模型可以提議，但只有通過驗證的決定才會生效：

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

`gate` 把整條流程串起來，連決定本身也記進 log：

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

怎麼跟其他章節接起來：candidate 來自 ledger 新進的資料（第 2 章），在 warm path 上做（執行結束才跑，不是每次查詢都跑）。
存下來的 candidate 變成有型別的紀錄（第 4 章），擱著的等 consolidation 處理（第 6 章），
要核准的排進佇列等人審，跟 Hermes 暫存寫入等核准是同一個模式。
每個決定也會以 event 的形式寫回 ledger。第 10 章就靠這些可重放的資料，量出 write precision。

最前沿的做法不是把 gate 寫出來，而是訓練出來：[Memory-R1](https://arxiv.org/html/2508.19828v2) 用結果導向的 RL
學 `ADD / UPDATE / DELETE / NOOP`，[AgeMem](https://arxiv.org/abs/2601.01885) 更進一步，
把 memory 操作直接併進 agent 自己的 policy。兩者都需要任務專屬的訓練資料，所以都不適合當第一版。

### What Changed

跟最小可行的 loop 比：以前選擇藏在 extractor 的 prompt 裡，結果不是寫了檔案，就是什麼都沒有。
現在判斷是明確、有型別的：接近重複的先擱著，不會悄悄越堆越多；敏感的寫入等人核准；
每一筆被拒絕的 candidate，都在 log 裡留下理由。

---

## 各系統做法

|                          | Mem0                                                              | Hermes Agent                                                       |
| ------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Pros**           | gate 順便做更新：新事實一趟就取代舊的。                           | 敏感的寫入會等人核准。session 中途就先存，執行結束時什麼都不會漏。 |
| **Cons**           | 每次寫入都要花模型呼叫。update 或 delete 判錯，就是破壞性的修改。 | 核准有摩擦，佇列可能堆著沒人看。規則寫在 prompt 裡。               |
| **Why**            | 把儲存區當成小而精的收藏：新事實要融進去，不是往上堆。            | 信任模型提議，但不讓模型動手寫：最後那筆寫入由人做主。             |
| **How: candidate** | 模型從最新的對話往來裡抽出 candidate。                            | 模型覺得某件事夠持久，session 中途就呼叫 memory tool。             |
| **How: 決定**      | 模型對照相似的現有 memory，選 add、update、delete 或 no-op。         | 直接寫入；開了核准模式就先掛成待審。                               |
| **How: 保險**      | 每筆 memory 留操作歷史，改壞了可以回查。                              | 待審的寫入一筆一筆列出來，逐筆核准或退回。                         |

---

## 哪裡會出錯

- **gate 太嚴：**儲存區一直沒什麼東西，agent 重複問已知的事。
  分規則追蹤 ignore 的比率，邊緣的 candidate 寧可 defer 也不要 ignore，讓 consolidation 有機會再看一次。
- **gate 太鬆：**recall 的雜訊越來越多。這量得出來：write precision 和重複率（第 10 章）。
  先調高重複門檻和具體度門檻，再去動 classifier。
- **決定靜悄悄發生：**ignore 沒留紀錄，「它怎麼不記得 X」就查不下去。
  每個決定都留理由，被拒絕的也一樣。
- **直接相信 classifier 的輸出：**動作名稱不認得、信心超出範圍，都要大聲失敗。
  validator 對每個決定都跑，沒驗證過的東西不落地。
- **敏感判斷全交給模型：**模型漏看一個標記，秘密就存進去了。
  敏感度用確定性的訊號判（標記、pattern、來源管道），classifier 只當第二意見。
- **gate 堵住 hot path：**每一輪都同步打分會拖慢回應。
  gate 放到執行結束再跑（warm path），整趟執行的 candidate 一次批次處理。

---

## 可執行程式

[`src/`](src/) 承接 01 的程式，再加上：

- [`policy.py`](src/policy.py)：`Candidate`、`Decision`、規則鏈、`decide`、`validate` 和 `gate`。
- [`engine.py`](src/engine.py)：`propose()` 讓 candidate 過 gate，每個決定都以 event 寫回 ledger。
- [`test.py`](src/test.py)：每條規則各觸發一次、classifier 只在規則判不了時才被叫到、
  validator 擋下格式錯誤的決定，以及決定以 event 的形式落進 ledger。

```bash
python sections/03-write-policy/src/test.py   # offline checks, no key
```

離線時 classifier 是個 stub。上線時，規則判不了的 candidate，每筆各呼叫模型一次。

---

## 出處

- [Mem0](https://arxiv.org/abs/2504.19413)：先抽取再更新的寫入，對照相似 memory 選 add / update / delete / no-op。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：把 write gate 當成用 RL 訓練出來的 policy。
- [AgeMem](https://arxiv.org/abs/2601.01885)：把 memory 操作併進 agent 的 policy。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：`tools/write_approval.py`，暫存寫入等核准。
- [Production memory track](../../README.zh-TW.md)：這一章所在的完整 lifecycle。
