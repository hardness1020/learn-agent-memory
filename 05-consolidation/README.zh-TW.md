# 5 · Consolidation

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 模型可以決定哪些東西該合併，但永遠不能決定哪些東西該消失。

這一章是 [Production memory](../README.zh-TW.md) track 的第六章：
lifecycle 的階段 6（Consolidate），把 event 變成知識。

Consolidation 不是把東西摘要一下就好。
它要做的事包括：合併重複、認出同一個實體、找出矛盾、把過期的事實標掉、把相關的事實併成一筆、
從多次經歷歸納出偏好、從成功的執行整理出 workflow、更新 confidence，還有淘汰沒用的 memory。
階段 5 只處理了其中很窄的一塊（新主張關掉舊主張），剩下的都在這個階段，
而且這是第一次由模型決定已經存在的 memory 該怎麼處置。

難就難在這裡。在這之前，每個階段都只會新增紀錄。
consolidation 這一趟會關掉紀錄，而提議要關掉什麼的是模型：它可能出錯，可能被 prompt injection，
也可能單純搞混兩筆很像的 memory 裡哪一筆才重要。

---

## 機制

最簡單的版本：proposer 負責提，validator 負責准，儲存區只會看到通過驗證的操作。

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

consolidation 分三個層級，一層比一層離原始輸入更遠：

```text
compression   many similar memories  →  one shorter memory
abstraction   many episodes          →  one rule or preference
skill         many successful runs   →  one reusable procedure
```

compression 只是改寫，來源沒講過的東西，它講不出來。
abstraction 是歸納，會從個案跳到通則，所以它會犯 compression 不會犯的錯：
五次經歷講的都是同一個測試不穩，歸納出來卻變成「整套測試都不能信」。
skill 價值最高，也最容易出事：三次執行都成功可能只是運氣好，
但整理出來的流程不會記得這件事，第四次照樣被拿出來用。

一份 Proposal 只講兩件事：要寫什麼新內容，這筆新內容要換掉哪幾筆舊紀錄。
它指定不了操作，所以模型想收掉一筆紀錄，只能提出新內容來換，
光刪不寫的 Proposal 根本寫不出來：

```python
@dataclass(frozen=True)
class Proposal:
    level: str
    content: str
    source_ids: tuple
    kind: str = "semantic"
    reason: str = ""
```

validator 就是圖裡的 `check`，擋在 Proposal 和儲存區之間。
它是確定性的，退件的理由一共八種：level 不認識、kind 不認識、內容是空的、來源少於兩筆、
引用了不存在的紀錄、引用了已經關掉的紀錄、來源跨了不只一個 scope，還有來源帶著不同的 claim key。
凡是建紀錄時會被退回的問題，這裡也先檢查一遍，因為 `apply` 會動到儲存區：
Proposal 套用到一半失敗，紀錄和 ledger 就對不上了，而這一趟本來就該救得回來。
被退回的 Proposal 會回報出去，不會被吞掉，因為階段 10 要靠這些數字算 consolidation 的精準度。

還有一條保證：舊紀錄被換掉，它的證據不會跟著不見。
每筆紀錄都帶著 source event id，記著自己是從哪幾個 event 來的。
`apply` 只會關掉 Proposal 引用的那幾筆，
衍生的新紀錄則把它們的 source event id 全部接收過來，一筆都不少。
這不用靠檢查，照這個做法走，本來就漏不掉：

```python
events = tuple(sorted({e for r in sources for e in r.source_event_ids}))
derived = make_record(sources[0].scope, proposal.kind, "inference",
                      proposal.content, events,
                      min(r.confidence for r in sources),
                      tags=("consolidated", proposal.level),
                      claim_key=one_key(sources),      # 不帶的話 profile view 會變空
                      valid_from=at)                   # 不填的話它會變成「一直都成立」
```

這段呼叫有三個地方特別重要。知識狀態填 `inference`，不是 `fact`：
合併出來的內容是系統自己推的結論，不是誰觀察到的，
階段 4 那條「推論不會偷偷變成事實」的規則在這裡照樣算數。
confidence 取來源裡的最小值，不是平均：把兩筆合起來，不會讓其中任何一筆變得更確定。
event id 則一路指回 ledger 裡的原始證據，所以每筆衍生紀錄都追得回它來自哪些觀察。

`consolidate` 跑一趟，通過和退件兩邊都回報：

```python
return {"proposed": len(proposals), "applied": len(proposals) - len(rejected),
        "rejected": rejected, "operations": ops}
```

資料怎麼流：engine 撈出這個 scope 生效中的紀錄，交給 proposer，把回來的 Proposal 驗一遍，
通過的才套用，最後把每個操作和每筆退件都寫進 ledger。這一趟不跑在使用者的那一輪裡。
回答眼前這一輪，用不到這一趟的任何一步，所以它可以照排程跑，或趁 session 之間的空檔跑。
沒有人在等它，多花幾秒呼叫一次模型也無所謂（[Sleep-time Compute](https://arxiv.org/html/2504.13171v1) 講的就是這個道理）。

### 整合時發現的兩個 bug

這兩個 bug 都出在更早的階段，但要等這個階段跑起來、跟前面串在一起，才看得出來。
照這條 track 的慣例，修正落在這個階段帶著走的那份 code，
前面的資料夾保留當時的版本，對照著看就知道整合改了什麼。

**Bug 1：`DEFER` 沒有地方可去。**
write gate 會把跟現有 memory 很像的 candidate 延後處理，
理由寫著「已經有相似的 memory，交給 consolidation 合併」。
但被延後的 candidate 根本沒存下來，consolidation 也就永遠看不到它：這個階段合併得動的每一組，gate 早就先丟掉了。
修法在 engine 的寫入路徑：被延後的 candidate 照樣存進儲存區，打上 `deferred` 標記，
merge band（gate 交給 consolidation 處理的那段相似度區間）才真的走得到。

```python
if decision.action in (STORE, DEFER):
    # DEFER 的意思是「交給 consolidation 合併」，
    # 所以 candidate 得活到階段 6 看得到它
    record = make_record(scope, candidate.kind, epistemic_type,
                         candidate.content, candidate.source_event_ids,
                         decision.confidence, claim_key=candidate.claim_key,
                         tags=("deferred",) if decision.action == DEFER else ())
```

`DEFER` 如果沒有一個真的能延後過去的地方，就只是默默丟東西的 `IGNORE`，理由比較好聽而已。

**Bug 2：兩邊對「像不像」各算各的。**光是存下來還不夠。
gate 判斷像不像用一種算法，這個階段分組的時候用的是另一套。
同一對紀錄，gate 算出來夠像，所以把 candidate 延後過來；
這個階段再算一次卻不夠像，達不到合併的門檻。
結果 gate 交過來的東西全部躺在儲存區裡，一筆都沒被合併過。
修法是這個階段直接用 gate 的函式和門檻，兩邊量出來的永遠是同一個數字：

```python
from policy import SIMILAR_AT, resembles

MERGE_AT = SIMILAR_AT          # gate 用多少延後，這裡就用多少合併

# propose_compression 分組時：
resembles(record.content, r.content) >= MERGE_AT
```

兩個階段要交接，得先對「像不像怎麼算」有共識，不然一邊說夠像，另一邊永遠算出不夠像。

### What Changed

跟階段 5 比：`consolidate()` 是 contract 的第三個動詞，現在有實作了。
`observe()` 和 `consolidate()` 都能離線跑完整條路；`recall()` 要等階段 8。

---

## 各系統做法

| | A-Mem | Sleep-time Compute |
| --- | --- | --- |
| **Pros** | 新知識馬上就能用。靠 link 就找得到相關的筆記，不用架 graph store。 | consolidation 的成本永遠不落在使用者那一輪。沒有人在等，再重的處理也付得起。 |
| **Cons** | 每次寫入都要付整理的成本。一條連錯的 link 會擴散，後面的筆記會接到它上面。 | 兩趟之間知識是舊的。現在做的更正，下一趟才生效。 |
| **Why** | memory 應該邊長大邊自己重新整理，像 zettelkasten 的筆記那樣。 | 回答這一輪用不到的東西，就不該在這一輪算。 |
| **How: 觸發** | 寫入時。新筆記會連到它碰到的舊筆記，並順手修訂它們。 | 照排程跑，或利用 session 之間的空檔。 |
| **How: 產出** | 互相連結的筆記，舊的會更新成跟新的一致。 | 預先算好的衍生 context，問題還沒來就準備好了。 |
| **How: 成本** | 每次寫入都付，付在互動延遲裡。 | 批次付，付在查詢路徑外面。 |

---

## 哪裡會出錯

- **模型把唯一一份刪掉：**合併時關掉來源卻沒把內容帶走，證據就沒了。
  衍生紀錄引用來源 event id 的聯集，ledger 裡的原始 event 也一直都在，
  所以就算一趟跑爛了，重放一次就能救回來。
- **例外被合併掉：**四筆說部署沒問題，一筆說星期五會失敗。
  compression 留下多數的講法，星期五那件事就消失了。合併前要看講的是不是同一件事，不能只看字面像不像。
- **consolidation 跑在熱路徑上：**recall 中間插一次模型呼叫，每一輪都多好幾秒。
  這一趟要嘛照排程跑，要嘛在 session 之間跑，就是不能塞進使用者的那一輪。
- **衍生紀錄被當成事實：**合併出來的內容是推論，不是觀察。
  蓋成 `fact`，retrieval 的時候一個猜測就會壓過它當初依據的那些觀察。
- **一直合併會漂掉：**合併過的東西再合併，每次的小改寫疊起來，最後 memory 跟任何一筆證據都對不上。
  每一趟只拿生效中的紀錄當輸入，對一個已經穩定的儲存區跑第二趟，不會提出任何 Proposal。
- **退件沒人看得到：**validator 把爛 Proposal 默默丟掉，等於把壞掉的 proposer 藏起來。
  被退回的 Proposal 會連理由一起回報，並寫進 ledger。
- **跨 scope 合併：**兩個租戶把同一個偏好寫成一模一樣的句子，一次合併就把兩邊併在一起。
  來源跨了不只一個 scope 的 Proposal，validator 直接退回。

---

## 可執行程式

[`src/`](src/) 承接 04 並加入：

- [`consolidate.py`](src/consolidate.py)：三個層級、`Proposal`、`check` validator、
  `apply`、`consolidate` 這一趟，還有確定性的 `propose_compression`。
- [`engine.py`](src/engine.py)：`consolidate()` 實作了 contract 的第三個動詞，
  被延後的 candidate 現在會存下來，merge band 才走得到。
- [`test.py`](src/test.py)：validator 該退的八種理由、衍生紀錄的出處與 confidence、
  proposer 把重複的分成一組而放過單筆的、退件有被回報出來，
  以及完整跑一趟：兩筆 memory 合併了，原始 event 完全沒動。

```bash
python tracks/production-memory/05-consolidation/src/test.py   # offline checks, no key
```

模型要接進來，就是從 proposer 這個位置接。預設的 proposer 是確定性的，所以這個階段沒有 `demo.py`。

---

## 出處

- [A-Mem](https://arxiv.org/html/2502.12110v1)：agentic organization，新筆記會連到舊筆記，並讓它們跟著演化。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)：把 consolidation 的工作移出查詢熱路徑。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用學出來的策略決定什麼時候存、改、忘。
- [Production memory track](../README.zh-TW.md)：這個階段所屬的完整 lifecycle。
