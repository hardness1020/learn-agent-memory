# 10 · Evaluation and governance

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 沒記下來的東西就量不到。數字後面要是沒接門檻，量了也改變不了任何事。

這一章是 [Production memory](../../README.zh-TW.md) track 的最後一章：lifecycle 的 Feedback and evaluation。
前面九章各自寫入、解衝突、合併、建 index、排序、注入，
但沒有一章會知道自己做得對不對。這一章補上回饋，把這個迴圈關起來。

這個缺口很容易一直沒人發現，因為 memory 壞掉的時候很安靜。
一個什麼都存的 gate 跟一個什麼都不存的 gate，從 gate 自己看出去都一切正常。
檢索回來一堆看起來合理、其實錯誤的 memory，跟檢索正常運作也長得一樣，
直到某個答案錯了，而且沒人講得出是哪一筆 memory 害的。

memory 也是唯一一個放著不管就會自己愈變愈差的子系統。
紀錄一直長，主張慢慢過期，consolidation 會漂，index 也會跟不上儲存區。
一個沒在量的系統不會停在昨天的水準：它會退化，而第一個發現的人是使用者。

---

## 機制

最簡單的版本：多記兩種 event type，指標要看的時候再從 ledger 現算，
每個門檻都接一個動作。不新開任何儲存區。

評估自己不存任何資料。每個指標都是讀第 2 章的 ledger 和紀錄、當場算出來的，
規則跟第 7 章對待 index 的那條一樣：把算出來的數字全部丟掉再算一次，什麼都不會少。
contract 也不用動，Protocol 還是 observe、consolidate、recall 三個動詞。

### 兩種新 event type

```text
recall   →  memory_injected    注入了哪幾個 id、花了多少、哪幾筆有衝突
answer   →  memory_outcome     哪幾筆真的有用、哪幾筆帶錯路、使用者有沒有更正
              ↓
          ledger，原封不動：只能追加，評估不會自己開儲存區
              ↓
   write · retrieval · context · end to end        四層，讀的時候現算
              ↓
          gates：門檻，後面接著一個真的會執行的動作
```

回報 outcome 不需要在 contract 上加第四個動詞。一則 outcome 就是對這一輪的一筆觀察，
所以跟其他事件一樣，用 `observe` 寫進 ledger：

```python
@dataclass(frozen=True)
class Outcome:
    query: str
    injected: tuple = ()
    used: tuple = ()          # 答案真的有靠的那幾筆
    misled: tuple = ()        # 靠了、結果答錯的那幾筆
    abstained: bool = False   # 答案有沒有選擇不靠 memory
    corrected: bool = False   # 使用者有沒有把結果改掉
```

`used` 和 `misled` 是判斷，而且得靠這一章以外的訊號來下。
上線的系統靠兩個來源：請模型在回答時列出它引用的 memory id，
以及把使用者的更正直接當成 misled 的標籤。
這一章絕不從答案文字自己推：讓系統自己改自己的考卷，量出來的分數沒有意義。

資料怎麼流：`recall` 在回傳 block 之前，先把選到的東西記下來。呼叫端在答案出來之後回報 outcome。
`evaluate` 再把這兩種事件連同紀錄一起讀回來，回傳四層指標，加上當下沒過的那幾道 gate。

### 四層指標

這些指標就是 `evaluate` 的輸出：要看的時候才從 ledger 現算的數字，照 pipeline 分成四層。
每個指標算得出來，都是因為前面某一章先記了東西：

| 層 | 指標 | 在量什麼 | 從哪裡算出來 | 備註 |
| --- | --- | --- | --- | --- |
| write | decisions · store rate · duplicate rate · approval queue | gate 做了哪些決定，還有多少筆在等人審 | 第 3 章的決策紀錄，連是哪條規則決定的都有記 | |
| | write precision | 存下來的 memory 裡，被證明寫對的比例 | 同一份紀錄，加上 outcome | 只算已判決的：還沒人用過的不計入，不然等於在罰 gate 存得太早 |
| | unsupported memory rate | 引用了 ledger 裡沒有的事件的紀錄 | 第 4 章的 source event id | 跟 *unsupported claims* 是兩回事：那問的是答案本身，這裡不量（見下表） |
| | wrong update rate | 存錯、後來被 retract 掉的比例 | 第 5 章的 retract 和 supersede | |
| | consolidation precision | 合併出來的東西有多少沒被退件 | 第 6 章回報的退件 | |
| retrieval | evidence precision · misled rate | 撈回來的 memory 有多少幫上忙、有多少帶錯路 | 線上的 outcome | |
| | contradiction coverage | 碰到衝突時，是不是兩邊一起注入 | 第 9 章配好的衝突組 | 選取以組為單位，單一輪次只會是 1.0 或 0.0。跨輪次平均低於 1.0，代表有某一輪把衝突整組丟掉，回答時兩邊證據都不在 |
| | recall@k | 該找回來的 memory，前 k 名裡回來了多少 | 標註過的資料集，沒有就是沒有 | |
| context | injected tokens · 每輪注入幾筆 | memory 每輪佔掉多少 prompt 空間 | 第 9 章每輪的注入紀錄 | |
| | injection hit rate | 注入的 memory 裡，讀起來像指令的筆數 | 同一份紀錄，注入當下就評好 | 量到不代表攻擊成功（guard line 已把整塊框成資料），但 write gate 漏掉了一次人工審核 |
| | no inject rate | 有多少輪什麼都沒注入 | 注入紀錄裡的空輪次 | |
| | stale rate | 注入時其實早就被關掉的 memory 有多少 | 第 5 章的 superseded_at，用 as_of 讀 | 事後查得出來，是因為關掉一筆是蓋上時間戳，不是把那一列刪掉 |
| end to end | turns · correction rate · abstention rate | 多少輪、多少次被更正、多少次答案不靠 memory | outcome | |

### 刻意不量的東西

有幾件事這一章刻意不量。不是漏掉，而是每一件都缺一樣東西，這一章給不了：

| 刻意不量                                   | 缺什麼                                              |
| ------------------------------------------ | --------------------------------------------------- |
| temporal correctness · unsupported claims | 要對答案本身下判斷，不是對紀錄                      |
| LongMemEval 的 end-to-end 任務類別         | 標註過的語料。`Case` 只帶一個 query 和該回來的 id |
| LongMemEval-V2 的 agent 經驗任務類別       | 一樣缺語料，這一章點了名，但沒附                    |

類別清單來自 benchmark 本身：LongMemEval 是 extraction、multi-session reasoning、
temporal reasoning、knowledge updates；LongMemEval-V2 是 static state、dynamic state、
workflow knowledge、environment gotchas、premise awareness。

### abstention 拆成兩個

abstention 這個詞得拆成兩個指標，因為有兩件不一樣的事一直共用這個名字：

|                      | no_inject_rate                                 | abstention_rate                         |
| -------------------- | ---------------------------------------------- | --------------------------------------- |
| **發生了什麼** | 這一輪檢索什麼都沒注入                         | 答案自己決定不靠 memory                 |
| **是誰的性質** | 檢索的：不值得花那筆 budget，是第 9 章正常運作 | 推理的：跟其他 outcome 一樣由呼叫端回報 |

不管哪一種，空的注入都要記一列。
不記的話，「決定不注入」跟「recall 根本沒跑」在 ledger 裡會長得一模一樣。

### 「沒量到」不是零

分母是零的時候回傳 `None`，絕對不是 `0.0`：

```python
def _share(part, whole):
    return part / whole if whole else None
```

沒量到的指標，跟量出來是零的指標，意思不一樣。
要是把「沒量到」也顯示成 0，一條停止回報的 pipeline，錯誤率全部變成 0，看起來就像滿分。
`recall@k` 也是同一條規則：沒有標註資料它就是「沒有」，不是「滿分」，再多的線上流量也生不出它來。

### gate：門檻接動作

governance 落在 gate 的 `action` 欄位上。
光有門檻只是一個 dashboard，一堆大家瞄一眼、沒人負責、也不會擋任何一次上線的數字：

```python
@dataclass(frozen=True)
class Gate:
    metric: str          # "layer.name"
    limit: float
    action: str          # 踩到的時候要做什麼
    floor: bool = False  # 這個 limit 是下限，不是上限

GATES = (
    Gate("write.unsupported_rate", 0.0,
         "block the write path: a memory is citing evidence the ledger does not have"),
    Gate("context.stale_rate", 0.05,
         "reindex after consolidation before serving: closed records are reaching prompts"),
    Gate("context.injection_hit_rate", 0.0,
         "review the write gate: instruction-shaped memories are reaching the prompt"),
    Gate("retrieval.misled_rate", 0.20,
         "hold the learned write policy: these outcomes are too noisy to train on"),
    Gate("retrieval.contradiction_coverage", 1.0,
         "raise the budget or the retrieval cap: conflicts are being dropped whole",
         floor=True),
)
```

一道 gate 如果盯著一個沒有任何一層會回報的指標，它會直接拋錯，而不是回傳空值。
因為一個打錯字之後默默變成「沒量到」的 gate，等於從此再也不會觸發，
這跟把沒量到的指標讀成零是同一種錯，只是發生在更上面一層。

最後那道 gate 是 track 第五條規則（evaluate before trust）真的派上用場的地方。
學出來的寫入策略拿 outcome 訓練，所以一個 outcome 每五次就錯一次的系統，
會很有自信地把這些雜訊學進去，然後變得更差。
evaluate before trust 第一個要管的，就是 memory 系統自己的升級。

### 迴圈收尾：retract

量出來的問題要有人能處理，迴圈才算真的關上。
一則 outcome 說某筆 memory 把答案帶錯了，接手的操作是 `retract`：第 5 章就有的操作，不是新動詞。
那筆紀錄會標成錯的，而且還讀得到，因為 retracted 的意思是它從來就不成立，superseded 的意思才是世界變了。
這是這一章唯一一次改動狀態。要是沒有任何地方呼叫它，數它的那個指標永遠只會是零。

### 熱路徑只有一列

指標和 gate 全部跑在冷路徑：想算哪一段時間都行，算完丟掉也沒差。
真正在熱路徑上的只有一件事：`recall` 每次查詢回傳之前，會 append 一列注入紀錄。
純寫入，不用先讀任何東西。這一列換來整個 context 層：
一次沒記下來的注入，就是一輪沒人算得出分數的對話。

### What Changed

跟第 9 章比：`assemble` 現在會把「選了什麼」跟「render 出什麼」一起交回來，
engine 要記 id 和 token 花費時，不用把同一件事再做一次。
決策除了 action，還會把**是哪條規則決定的**寫成具名欄位，不再塞進訊息的前綴，
因為靠 parse log 訊息來數的指標，會在有人改一次措辭那天默默壞掉。
兩種事件也各自只有一個地方負責寫、一個地方負責讀，所以沒有任何指標直接拿字串 key 去讀 dict：
綁在五個字串 key 上的指標，壞法一模一樣，只是更安靜。
你打算量什麼，就把什麼記成欄位，並且用當初寫入的那個型別讀回來。

---

## 各系統做法

|                         | LongMemEval                                                          | Memory-R1                                                           |
| ----------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **Pros**          | 不同系統之間可以直接比。某一類任務掛掉，就直接指出是哪一種能力壞了。 | 不用任何人標資料就能改進策略，學的是真實流量。                      |
| **Cons**          | 固定的資料集會過期，過得了它不代表上線之後沒問題。                   | 獎勵只看最後的答案，所以一筆剛好蒙對的錯 memory，獎勵照樣會強化它。 |
| **Why**           | memory 的各種能力本來就分得開，評分也該分開。                        | 唯一算數的判斷，就是答案有沒有對。                                  |
| **How: 訊號**     | 標註過的 session，附上該用哪些證據，照任務類別分開評分。             | 一趟跑完看答案對不對，回饋成 store、edit、forget 的獎勵。           |
| **How: 時機**     | 離線，跑固定資料集，上線前跑。                                       | 線上，跟著真實 outcome，一直跑。                                    |
| **How: 覆蓋範圍** | 它自己定義的那幾類任務，以外的都不管。                               | 流量剛好練到什麼就涵蓋什麼，沒練到的就沒有。                        |

---

## 哪裡會出錯

- **指標後面沒接動作：**數字畫成圖表，沒人負責，也沒有任何一次上線會等它。
  這裡每一道 gate 都帶著它要觸發的動作，沒有動作的門檻只是裝飾。
- **自己改自己的考卷：**產生答案的那個模型，同時決定 memory 有沒有幫上忙。
  標籤要從外面來：使用者的更正、模型列出來的 id、人審過的資料集。
- **把「沒量到」當成零：**一條停止回報 outcome 的 pipeline，correction rate 會漂亮得不得了。
  「沒有」跟「零」要在算術那一層就分開，不是在 dashboard 上分。
- **只看 end-to-end 的數字：**一個會因為十種原因上下跳的分數，講不出是哪一章壞了。
  分層評分，讓沒過的那一層自己指向它的章。
- **一個根本生不出來的指標：**一個比率，要是分子沒有任何程式路徑會去產生，它就永遠是乾淨的零，
  跟「系統表現很好」長得一模一樣。每一個要數的類別都得有人呼叫，不然那個指標只是帶著分母的裝飾。
- **評估跑在熱路徑上：**一邊回答一邊算這一輪的分數，等於每次查詢都多等一下，換到的還是一個明天才有人看的數字。
  熱路徑只負責記一列，算分留給冷路徑。
- **一個詞配到兩個指標：**abstention 同時代表「檢索什麼都沒注入」和「答案選擇不靠 memory」，
  等於把檢索的性質跟推理的性質平均在一起。要嘛分開命名，要嘛兩個都別量。
- **拿 benchmark 當上線標準：**固定資料集過了只代表底線有守住，不代表上線之後沒問題。
  線上的 evidence precision 和 stale rate 要在 benchmark 過關之後繼續量。
- **沒人清的審核佇列：**積壓只要一直長，`require_approval` 就會變成一個比較慢的 `ignore`，
  write gate 等於悄悄整類都不存了。要量的是佇列長度，不只是決策數。
- **把刪除當成設計上的 bug：**只能追加的系統，總有一天會碰到法律要求的刪除。
  把它做成一次有稽核、有範圍、自己也留下紀錄的操作，而不是一個任何一章都能呼叫的 `DELETE` 動詞。

---

## 可執行程式

[`src/`](src/) 承接 08 並加入：

- [`evaluate.py`](src/evaluate.py)：`Outcome`、`Injection`、`Case`、`Gate`、四層各自的函式、`report`、`failing`，
  以及兩種 event type 各自一個寫入函式、一個讀回函式，所以沒有任何指標直接拿字串 key 去讀 dict。
  沒量到的指標回傳 `None`，不會是 `0.0`。
- [`engine.py`](src/engine.py)：`recall` 會把注入的東西記下來，空的那幾輪也記，
  `feedback` 把 outcome 當成一般觀察寫進去，`last_injected` 讓呼叫端知道要回報哪幾筆 memory，
  `retract` 用第 5 章的操作把一筆 memory 標成錯的，`evaluate` 從紀錄把整條鏈的分數算出來。
- [`policy.py`](src/policy.py)：決策現在會帶著「是哪條規則決定的」，而且只在一個地方蓋上去，
  不用每條規則各寫一次，這樣 duplicate rate 數的是一個類別，不是去比對一串理由文字。
- [`assemble.py`](src/assemble.py)：`assemble` 會把 block 和選到的東西一起回傳，
  「選不到就不給 block」這條規則只有一個地方管，token 估算也就是 selector 自己那一份。
- [`test.py`](src/test.py)：空系統上「沒量到」和「零」的差別、照 action 和照 rule 數的決策、
  只算已有判決的 write precision、引用不存在證據的紀錄、注入成本、兩種 abstention 分得開、
  budget 把一組衝突整組丟掉時 contradiction coverage 會掉、一次 retract 把 wrong update rate 推離零、
  一筆長得像指令的 memory 觸發它那道 gate、一道盯著沒人回報指標的 gate 直接拋錯而不是安靜失效、
  有標註才會出現的 recall@k、更正前後的 stale rate，還有 gates 本身。

```bash
python sections/10-evaluation-governance/src/test.py   # offline checks, no key
```

這一章完全不呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [LongMemEval](https://arxiv.org/abs/2410.10813)：end-to-end 的任務分類，abstention 也當成一種能力來評分。
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493)：同一套想法延伸到 agent 經驗和 premise awareness。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用 outcome 驅動的 RL 學 memory 操作，這一章的指標就是餵給它的東西。
- [AgeMem](https://arxiv.org/abs/2601.01885)：學出來的策略，決定什麼時候存、改、忘。
- [Production memory track](../../README.zh-TW.md)：這一章所屬的完整 lifecycle。
