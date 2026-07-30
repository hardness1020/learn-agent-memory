# 7 · Hybrid retrieval

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 一個排序器永遠不夠。讓幾條輕量的 candidate 通道同時跑，再把它們的排名融合起來。

這一章講的是 [Production memory](../README.zh-TW.md) track lifecycle 的階段 8（Retrieve）：
從一堆 memory 裡，找出這一輪真正需要的那幾筆。

通道（channel）指一種找 candidate 的方法：embedding 相似度、關鍵字、最近優先，各算一條通道。
只靠一條，一定有盲點：
embedding 抓不到精確的名字、日期和否定句；同一件事換個說法，關鍵字就搜不到；只看最近的，又舊又相關的事實就漏掉。
丟給 memory 的問題什麼樣子都有：「我們的 CI 是哪家」（名字）、「上週決定了什麼」（時間）、
「以前部署出過什麼包」（模式）。沒有一條通道三種都答得了。

[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-TW.md)已經有兩種查法：LLM selector 讀 index 挑 memory，
關鍵字搜原始歷史。這個階段把它推廣成一條 pipeline：

1. 幾條輕量的 candidate 通道並行跑。
2. 把它們的排名融合起來，不用調跨通道的權重。
3. 要能降級：一條通道壞掉，結果不能跟著變空。
4. 要分得清：通道跑完但一筆都沒對上，跟通道壞掉，是兩回事。
5. index 要一直可以重建：它是 view（階段 7），從紀錄推導出來，紀錄才是本體。

---

## 機制

最簡單的版本：兩條通道，加一個排名融合器。

三個零件：

- **通道**：各自獨立的 candidate 產生器。離線版有兩條：關鍵字（FTS5，bm25）和最近優先。
  上線後，dense embedding、graph 遍歷、時間過濾接的都是同一個介面：回一份排好序的 id 清單。
- **融合**：reciprocal rank fusion（RRF）。每條通道替它排到的每個項目加 `1 / (K + 名次)` 的分數。
- **index**：一張從有類型的紀錄推導出來的 FTS 表，隨時可以砍掉重建。

加一條新通道之所以省事，靠的是 RRF：
各通道的原始分數不能直接比，但名次可以，
所以既不用做分數正規化，權重也不用調：

```python
def fuse(channels, k=TOP_K) -> list[tuple]:
    scores, seen_in = {}, {}
    for name, ranking in channels.items():
        for rank, mid in enumerate(ranking):
            scores[mid] = scores.get(mid, 0.0) + 1 / (RRF_K + rank + 1)
            seen_in.setdefault(mid, []).append(name)
    best = sorted(scores, key=scores.get, reverse=True)[:k]
    return [(mid, scores[mid], tuple(seen_in[mid])) for mid in best]
```

被兩條通道排進來的 id，分數會贏過只被一條排到的。hybrid 的效果就這一句：
獨立訊號之間有共識，本身就是相關性的證據。

兩條通道共用同一張推導出來的表：

```python
def keyword(self, query, k=TOP_K) -> list[str]:
    """bm25-ranked ids. The column filter keeps ids and dates out of matching."""
    ...
    rows = con.execute("SELECT memory_id FROM memory_index WHERE memory_index MATCH ? "
                       "ORDER BY rank LIMIT ?",
                       (f"content : ({' OR '.join(sorted(words))})", k)).fetchall()

def recent(self, k=TOP_K, kind=None) -> list[str]:
    """Newest first, optionally one kind (recent episodes are their own channel)."""
```

track README 那條完整 pipeline，對到這副骨架長這樣：

```text
query understanding                  (live: routing, expansion)
    ↓
parallel candidates                  keyword · recent  (live: + dense, graph, temporal)
    ↓
merge + dedupe, rerank               RRF fusion
    ↓
source-context expansion             (live: pull surrounding events, stage 2)
    ↓
token-budget selection               stage 9's job
```

有兩步只有上線版才有，但重要到必須點名：

| | Contextual expansion | Agentic retrieval |
| --- | --- | --- |
| **做法** | 命中之後，回 event ledger（階段 2）把那筆前後的 event 一起撈出來。 | 把執行軌跡存成檔案，讓 coding agent 在 sandbox 裡自己搜。 |
| **證據** | MemMachine：把檢索挖深、把來源展開，常常贏過更聰明的 ingestion 切塊。 | AgentRunbook-C 在 LongMemEval-V2 上贏過 RAG 基線。 |
| **代價** | 注入的 token 變多。 | 慢。有些問題得把整批檔案翻過一遍，一次最近鄰查詢解決不了。 |

跟前後階段怎麼接：index 從有類型的紀錄（階段 4）推導出來，紀錄在寫入時就標好 scope，
所以 scope 過濾在通道開跑之前就做完了。index 的維護留在寫入路徑（階段 7 的拆法），查詢只讀不寫。
index 壞了，把 ledger 重放一次就回來（階段 2 的保證）。
融合出來的命中會送去 context assembly（階段 9）。
每筆命中連同它的類型、分數、記錄時間和來源 event id 一起送過去，不是只送一段文字。

### What Changed

跟第 9 章比：那時候的 recall 一次只走一條路，走哪條看是誰觸發的。
現在通道一起跑，結果不一致也沒關係；要加新通道，就是多接一份排好序的清單，不用重新設計。

---

## 各系統做法

| | Graphiti | MemMachine |
| --- | --- | --- |
| **Pros** | 關係和時間的問題一次搜尋就解掉，不用呼叫 LLM。 | 命中的那筆會展開成它所在的 episode，答案跟上下文一起送到。 |
| **Cons** | 要做實體抽取，graph 儲存還得跟資料保持同步。 | 展開會讓注入的 token 變多。完整的 episode 必須一直留著。 |
| **Why** | 「誰對誰做了什麼、什麼時候」要靠 graph 的邊來答，最近鄰算不出來。 | 答案通常散在命中點的前後，不會剛好塞在同一塊裡。 |
| **How: 通道** | semantic embedding、關鍵字、graph 遍歷，融合起來。 | profile 查表，加上對完整歷史的 episode 搜尋。 |
| **How: 時間** | 邊上帶時間，過濾出查詢那一刻仍然有效的事實。 | episode 自帶時間線，展開就順著它走。 |
| **How: 重排** | 把並行搜尋的結果做融合排名。 | 調的是深度和排版，不在 ingestion 的切塊上較勁。 |

---

## 哪裡會出錯

- **一條通道獨大：**回一大串結果的通道會把融合灌爆。
  RRF 只看名次不看分數，每條通道的影響力天生有上限；再把每條通道的 `k` 設小一點，而且每條都一樣。
- **哪條通道都找不到：**事實明明存在，卻沒有訊號對得上。
  解法是加一條通道（時間、實體），不是硬調現有的；融合介面就是為這件事設計的。
- **index 跟紀錄漸漸對不上：**刪除或 schema 改動留下幽靈資料。
  有疑慮就從紀錄重建；它是 view，重建的成本本來就設計得很小。
- **k 一大精準度就崩：**candidate 越多，下游雜訊越多。
  融合時用小 k，最後一刀交給 assembly 的 budget（階段 9）去切。
- **延遲越積越多：**通道要真的並行跑才算並行。
  每條通道就維持一次走 index 的查詢，慢的工作（展開、agentic 搜尋）放在路由決策後面才跑。

---

## 可執行程式

[`src/`](src/) 承接 06 的程式，這次加入：

- [`retrieve.py`](src/retrieve.py)：RRF 的 `fuse`，和把階段 7 那幾條通道融合起來的 `retrieve`。
  index 本身放在 [6 · Index views](../06-index-views/README.zh-TW.md)。
- [`engine.py`](src/engine.py)：寫入路徑改完紀錄就 reindex 該 scope，
  `retrieve()` 只讀不寫，在建好的 view 上跑通道融合。
- [`test.py`](src/test.py)：驗每條通道各自的行為、融合偏向跨通道共識、沒對上就回空、
  一條通道壞掉照樣降級、砍掉重建後結果一模一樣，以及透過 engine 的 scope 隔離檢索。

```bash
python tracks/production-memory/07-hybrid-retrieval/src/test.py   # offline checks, no key
```

離線版的通道是關鍵字和最近優先。上線後，dense 和 graph 通道接進同一個 `fuse`。

---

## 出處

- [Graphiti / Zep](https://arxiv.org/abs/2501.13956)：增量式的時間知識圖譜，融合搜尋不呼叫 LLM。
- [MemMachine](https://arxiv.org/abs/2604.04853)：情境式檢索，靠深度和來源展開取勝，不靠 ingestion 的切塊技巧。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：答案要串好幾筆 memory 才拼得出來的問題，用 Personalized PageRank 一步收齊。
- [LongMemEval-V2 / AgentRunbook-C](https://arxiv.org/abs/2605.12493)：讓 agent 自己翻檔案，當一條慢但挖得深的通道。
- [Production memory track](../README.zh-TW.md)：這個階段所屬的完整 lifecycle。
