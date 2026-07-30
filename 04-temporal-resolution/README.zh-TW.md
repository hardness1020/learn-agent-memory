# 4 · Temporal resolution

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 一件事不再成立，不代表它當初是錯的。把它關掉，不要蓋掉。

這一章是 [Production memory](../README.zh-TW.md) track 的第五章：
lifecycle 的階段 5（Resolve），處理身分、衝突和時間。

階段 4 讓每筆紀錄有了類型和出處，但沒有回答一個問題：新紀錄跟舊紀錄講的不一樣時，該怎麼辦。
沒有答案的儲存區只剩兩條路，而且都不好。
蓋掉，舊主張就消失了，沒人查得出系統上個月相信什麼、又為什麼改。
兩筆都留，檢索就會回傳兩個都還在生效的矛盾，讓模型自己猜。

所以要拆成三個問題分開處理：

1. 身分。「Marcus」和「Ming-Siang」講的是不是同一個人？
2. 衝突。「住在 San Diego」和「住在 San Francisco」是矛盾，還是不同時間各自成立的兩件事？
3. 時間。哪一筆現在成立，哪一筆是當時成立？

---

## 機制

最簡單的版本：每筆紀錄記兩組時間，不是一組，而且永遠不刪。

```text
recorded_at ─────────── superseded_at      系統相信它的那段期間：從記下來到被取代
valid_from  ─────────── valid_to           它在真實世界成立的那段期間
```

這就是 bitemporal 模型。要兩組時間，是因為問題本來就有兩種：
「系統現在認為 Marcus 住哪」問的是系統相信什麼，看 recorded_at 那組。
「Marcus 三月住在哪」問的是真實世界，看 valid_from 那組。
一組時間寫不下這種紀錄：今天才記下來、講的卻是去年的事。
只記去年，看不出系統今天才知道；只記今天，事情何時成立就丟了。兩組都記才放得下。
[Graphiti 和 Zep](https://arxiv.org/abs/2501.13956) 的 temporal knowledge graph 用同一種切法：舊的邊會關閉，不會消失。

要判斷衝突，得先分得出哪些主張在互相競爭，哪些只是長得像。所以每筆紀錄帶一個 claim key：
主詞加述詞當 key，內容就是受詞。

```text
claim_key "marcus:lives_in"   content "Marcus lives in San Diego"      status superseded  valid_to 2025-06
claim_key "marcus:lives_in"   content "Marcus lives in San Francisco"  status active      valid_from 2025-06
```

同一個 key、內容不同、兩筆都在生效，代表其中一筆已經不成立了。
key 空著，代表這筆主張沒有競爭對手，所以永遠不會被自動取代。

所有操作都不破壞資料。沒有想改哪就改哪的 `UPDATE`，也沒有 `DELETE`，event ledger（階段 2）那邊同樣沒有：

```text
ADD        新紀錄進來，生效中
SUPERSEDE  舊的關閉，新的開始
RETRACT    標記這筆是錯的，但還讀得到
ABSTRACT   多筆紀錄濃縮成一筆更高層的紀錄（階段 6 會產生）
```

要更新一個事實，就是加一筆新紀錄，再用 SUPERSEDE 把舊的關掉。
嚴格說，關掉也是對舊紀錄的一次寫入，但它是唯一允許的一種：
蓋上收尾的欄位（status、superseded_at、valid_to），單向、蓋一次就定了。
主張內容一個字不改，舊紀錄隨時讀得回來。

`supersede` 和 `retract` 長得像，意思不一樣。
supersede 是說它以前成立過，retract 是說它從來就不成立，通常是抽取抽錯了。
兩種都保持可讀，因為一筆被 retract 的紀錄，正好就是「抽取邏輯要修」的證據。

寫入路徑多了一步。`resolve` 把驗證過的紀錄放上時間軸，並關掉跟它衝突的舊紀錄：

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

scope 隔離不只做在讀取的過濾條件上，寫入時也要守。
supersede 是一次寫入，所以 A 租戶的主張絕對不能關掉 B 租戶字面相同的那句。
`same_scope` 在計算衝突之前，就先把別的租戶的紀錄排除掉。

兩組時間讀出來就是兩種查詢：

```python
def as_of(records, when):     # what the system believed at a past moment
    return [r for r in records if r.recorded_at <= when
            and (r.superseded_at is None or r.superseded_at > when)]

def valid_at(records, when):  # what was true in the world at that moment
    return [r for r in records if (r.valid_from is None or r.valid_from <= when)
            and (r.valid_to is None or r.valid_to > when)]
```

資料怎麼流：write gate（階段 3）放行 candidate，階段 4 把它做成有類型的紀錄，
這個階段把紀錄放上時間軸，並回傳做了哪些操作。
engine 把每個操作寫回 ledger 變成 event，所以時間軸本身也能重放。
retrieval（階段 8）只撈生效中的紀錄；context assembly（階段 9）會讀這個階段記下的衝突，
把兩邊都印出來，不會偷偷選一邊。

### 整合時發現的兩個 bug

這兩個 bug 都出在 write gate（階段 3），但要等這個階段接上來、更正真的送進 gate，才看得出來。
這個階段的第一版就是這樣壞掉的。
照這條 track 的慣例，修正落在這個階段帶著走的那份 code，前面的資料夾保留當時的版本，對照著看就知道整合改了什麼。

**Bug 1：更正被當成疑似重複。**gate 用字面重疊判斷像不像，跟現有 memory 太像的 candidate 會被擱著延後處理。
可是更正和被更正的那句，字面本來就很像：
「Marcus lives in San Diego」和「Marcus lives in San Francisco」大部分的字都相同。
結果更正被擱著，resolution 根本收不到，過時的舊主張就一直生效：
gate 擱下的，正好是這個階段要處理的 candidate。
修法是帶 claim key 的 candidate 不量重疊：字面完全相同才算重複，
其他一律當成更正，交給 resolution 判斷。

```python
def _duplicate(c, existing):
    if c.claim_key:
        # 帶 claim key 的 candidate 不量重疊：字面完全相同才算重複，
        # 其他一律當成更正，交給 resolution 判斷
        return Decision(IGNORE, "already known", 0.9) if c.content in existing else None
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

兩句只差一個字的時候，重疊門檻本來就分不出「同一個主張」和「相反的主張」。

**Bug 2：敏感檢查排太後面。**同一條疑似重複的規則，還踩出另一個問題：規則的順序。
gate 是一條一條往下試，哪條先做出決定就停在哪。
檢查敏感資料的規則本來排在最後，candidate 只要先被判成疑似重複，就根本輪不到它。
這種檢查就是要擋住不該存的東西，不排最前面就等於沒有。現在它排在所有會存東西的規則前面：

```python
RULES = (_no_evidence, _sensitive, _derivable, _duplicate, _vague)
```

### What Changed

跟階段 4 比：以前一筆紀錄寫好就定了，是一筆有類型、不會再動的資料。
現在它有生效的起點和終點，會被更正、被取代。
`status` 以前只是跟著存、沒人動的欄位，現在 supersede 改的就是它。

---

## 各系統做法

|                     | Graphiti / Zep                                                       | Claude Code auto memory                              |
| ------------------- | -------------------------------------------------------------------- | ---------------------------------------------------- |
| **Pros**      | 舊事實查得到，答案講得出日期。矛盾在寫入時就解決掉。                 | 不用建模。一次更正就是改一個檔案，使用者還原得回來。 |
| **Cons**      | 每條邊兩組時間，schema 變重，抽取那一步得把時間填對。                  | 沒有歷史：檔案一改寫，前一個主張就沒了。             |
| **Why**       | agent memory 本來就是一連串更正，所以「讓舊的失效」得直接做進資料模型。 | 假設有人會看儲存區，所以檔案本身就是稽核紀錄。       |
| **How: 衝突** | 新的邊透過 graph 讓跟它矛盾的舊邊失效。                              | 模型直接改寫受影響的 memory 檔案。                         |
| **How: 時間** | bitemporal：每條邊同時存事件時間和寫入時間。                         | 一組隱含的時間，就是檔案當下的狀態。                 |
| **How: 復原** | graph 從 episode 重建，episode 本身完整留著。                        | 看使用者自己在那個目錄外面套了什麼版本控制。         |

---

## 哪裡會出錯

- **就地蓋掉：**舊主張消失，「為什麼改了」查不到答案，改錯了也救不回來。
  supersede 是補一個時間戳，不是刪掉那一列。
- **兩筆都在生效：**檢索回傳兩個矛盾，模型只能亂選。
  衝突偵測做在寫入時，不是讀取時，所以同一個 key 永遠只有一筆生效中。
- **抽錯的紀錄用 supersede 關掉：**這筆錯的紀錄從此被當成「曾經成立過」，之後每一次 `valid_at` 查詢都被它汙染。
  retract 獨立成一個操作就是為了這種情況。
- **更正被當成疑似重複擋下：**write gate 看到字面重疊就把更正丟掉。
  帶 claim key 的 candidate 改成只看字面是否完全相同，因為更正跟它要更正的那句往往只差一個字，
  重疊比例在這裡說明不了任何事。
- **敏感檢查排太後面：**gate 哪條規則先做出決定就停在哪，這條檢查以前排在最後，
  candidate 只要先被判成疑似重複，就根本輪不到它。會存東西的規則一律排在它後面。
- **跨租戶 supersede：**兩個租戶存了同一句話，其中一個關掉了另一個的主張。
  scope 在計算衝突之前就檢查，不是算完才補。
- **claim key 各寫各的：**`marcus:lives_in` 和 `marcus:location` 永遠不會互相競爭，兩筆都留著，矛盾就看不見。
  key 要來自受控的詞彙表，不能自由發揮。
- **身分沒有先解析：**「Marcus」和「Ming-Siang」拿到不同的 key，永遠不會衝突。
  身分解析要跑在衝突偵測前面，不然衝突偵測比對的是錯的組合。

---

## 可執行程式

[`src/`](src/) 承接 03 的程式，再加上：

- [`resolve.py`](src/resolve.py)：操作的詞彙表、`conflicts`、`supersede`、`retract`、
  `resolve`，以及讀兩組時間的 `as_of` 和 `valid_at`。
- [`records.py`](src/records.py)：bitemporal 欄位、`claim_key`，加上會擋掉不可能時間軸的驗證。
- [`policy.py`](src/policy.py)：帶 claim key 的 candidate 只看字面是否完全相同，不看重疊；
  敏感檢查排在所有會存東西的規則前面。`words` 會濾掉 stopword，查詢不會只靠「the」就命中。
- [`engine.py`](src/engine.py)：存下來的紀錄落在時間軸上，每個操作都寫回 ledger，
  `believed_at` 和 `true_at` 分別讀兩組時間。
- [`test.py`](src/test.py)：衝突偵測和 scope 隔離、supersede 與 retract 的差別、
  補記的紀錄讓兩組時間分岔、被擋掉的不可能時間軸，還有一次更正從頭到尾關掉舊主張。

```bash
python tracks/production-memory/04-temporal-resolution/src/test.py   # offline checks, no key
```

這個階段完全不呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [Zep / Graphiti](https://arxiv.org/abs/2501.13956)：bitemporal knowledge graph，讓邊失效而不是刪掉。
- [A-Mem](https://arxiv.org/html/2502.12110v1)：linked note，會隨新 memory 進來持續演化。
- [第 9 章 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-TW.md)：只記一組時間的儲存區，這個階段把它擴充成兩組。
- [Production memory track](../README.zh-TW.md)：這個階段所屬的 lifecycle。
