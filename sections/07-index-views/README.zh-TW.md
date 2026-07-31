# 7 · Index views

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> index 不是 memory 本身。它只是一種 view，view 壞了就重建，不用修。

這一章講 [Production memory](../../README.zh-TW.md) track 的第 7 章（Index）。
view 指的是從紀錄整理出來的讀取結構，搜尋用的 index、看當下狀態的 profile、
互相連結的 wiki 頁面都是。每一種都從同一份 ledger 衍生出來。

走到這一章，一筆紀錄已經有類型、有時間軸、也有合併的歷史，但它終究只是一列資料。
不同的問題，要的答案長得完全不一樣。
「關於部署我知道什麼」要的是排好序的搜尋結果，「上禮拜改了什麼」要的是一段時間區間，
「這個使用者是誰」根本不用搜尋，直接要當下狀態，「這些事實怎麼串起來」要的是互相連結的頁面。

每一種答案都需要自己的資料結構。硬逼所有問題走同一個 index，系統會同時變慢又變錯。

---

## 機制

最簡單的版本：寫入只有一條路，讀取的 view 有好幾種，正本永遠只有 ledger 那一份。

```text
raw event log    SQLite, append only, never rebuilt
  → records      typed, resolved, consolidated (sections 4 to 6)
    → sparse     FTS5 / bm25            exact names and dates
    → temporal   recorded_at ranges     what changed in a window
    → profile    one line per claim     current state, no search
```

實際上線的系統還會加 dense view（embedding）、wiki、entity graph，和放 skill、runbook 的 procedure view。
這張清單的重點不在長度，在方向：每個箭頭都從 ledger 往外指，沒有箭頭指回去。
這個單向流動就是這一章要守的規則。index 壞掉不算事故，重建就好：

```python
def rebuild(self, scope, records) -> int:
    con.execute(f"DELETE FROM memory_index WHERE {_SCOPE}", _args(scope))
    con.executemany("INSERT INTO memory_index VALUES (?, ?, ?, ?, ?, ?)", ...)
```

rebuild 先刪再插，scope 欄位決定刪的範圍。
表上要是沒有這個欄位，替一個租戶重建，就會先把整張表清空，再插回自己那一份，其他租戶的資料就沒了。
第 8 章起每次寫入跑完都會重建一次，所以這不是偶發事故：誰最後寫，表裡就只剩誰的資料。
其他租戶查到的是空結果，不是錯誤，沒有人會發現。
所以這張表的每個讀取和重建都要帶 scope，跟 ledger 是同一條規則。

schema 要改、index 壞掉、tokenizer 換了，三件事的解法都是同一個：再呼叫一次 `rebuild`。
沒有東西需要修：index 裡的每一列都能從紀錄重新算出來。

不是每種 view 都要存下來。搜尋需要 inverted index，所以 sparse 和 temporal 存在 SQLite 裡。
profile 讀的時候現算，因為它很小，存起來只是多一個要跟著失效的東西：

```python
def profile(records) -> dict:
    """Current state, one entry per claim key, newest wins."""
```

profile 是第 5 章的 claim key（一個事實更新時固定用的那個名字）第二次派上用場的地方。
沒有 key 就沒有投影的目標，「當下狀態」就會退化成「最近那幾筆 memory」。

資料的流向：紀錄進去，view 出來，engine 隨時可以重建任何一種。
`reindex` 拿一個 scope 還生效的紀錄，把 sparse view 從頭重寫一次。
這一章的 src 只給出 `reindex` 這個動作，沒有任何路徑會自動呼叫它。
到了第 8 章，換寫入路徑來呼叫：任何改動紀錄的流程跑完就 reindex 自己的 scope，retrieve 只讀表。
這就是上線系統的三路拆法。hot path 負責回答查詢，從不重建。
warm path 跟著寫入維護 index，這裡的版本是每次寫入就重建整個 scope。
上線的系統做增量：新紀錄插一列，被關掉的紀錄刪一列，成本跟著改動的筆數走，不是整張表。
全量的 `rebuild` 留在 cold path，就是前面說的 schema 改、tokenizer 換、index 壞掉那三件事。
不管維護放在哪條路徑，這張表都省不掉：bm25 的排序要靠 inverted index 才算得出來。
這裡完全不呼叫模型，也不決定什麼算相關。

### Other Views

wiki 和 graph 是最常被討論的兩種 view。

| | wiki view | graph view |
| --- | --- | --- |
| **長相** | 互相連結的 markdown 頁面，一個主題一頁。 | 實體節點，加上有類型的關係邊。 |
| **成立的前提** | markdown 是正本，agent 和人都用一般檔案工具改頁面。 | 多跳、關聯類的問題多到值得付抽取的成本。 |
| **連結怎麼來** | 改頁面的時候，順手寫進頁面文字裡。 | 一條抽取流程從每筆 event 建出邊。 |
| **回答什麼** | agentic research、人工稽核、翻閱。 | 關聯、時間、多跳的問題，一趟收齊。 |
| **怎麼壞** | 沒人寫連結，每一頁都是孤島。 | 邊連錯，每次走訪都走偏。 |
| **代表系統** | [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)、Claude Code auto memory。 | [HippoRAG](https://arxiv.org/abs/2405.14831)、[Graphiti](https://arxiv.org/abs/2501.13956)。 |

這兩個前提都不成立，所以這一章兩種都沒做。

### What Changed

和第 6 章相比，建 index 和做檢索分成兩件事。
這一章建出回傳 candidate 的各個通道，第 8 章才決定怎麼跨通道排序。
那是另一個問題、另一種錯法，兩件事混在一起，檢索的 bug 就會變成 index 的 bug。

---

## 各系統做法

|                     | Claude Code auto memory                                          | HippoRAG                                                 |
| ------------------- | ---------------------------------------------------------------- | -------------------------------------------------------- |
| **Pros**      | 什麼都不用架。人可以直接讀儲存的檔案來稽核，也能用文字編輯器改。 | 要跨好幾跳的問題，一次檢索就解決，不用來回好幾輪。       |
| **Cons**      | 搜尋能力受限於檔案工具，目錄一大，找得回來的東西就變少。             | 要維護一條抽取流程和一張圖。邊連錯，走訪就跟著走偏。     |
| **Why**       | 使用者看不到的 memory，就是使用者改不了的 memory。               | 跨好幾筆事實的答案，不該花好幾輪檢索。                   |
| **How: 單位** | markdown 檔案，加一個列出它們的 index 檔。                       | 從段落建出來的實體節點和關係邊。                         |
| **How: 連結** | 頁面文字裡的 markdown link。                                     | 有類型的邊，從問題裡的實體出發跑 Personalized PageRank。 |
| **How: 重建** | 把檔案重寫一次，目錄夠小，整個重新生成也不費事。                 | 整份語料重新抽取、重新建 index。                         |

---

## 哪裡會出錯

- **index 變成正本：**某個東西只寫進 index、沒進 ledger，重建一次就悄悄不見。
  每個 view 都從紀錄衍生，紀錄又從 ledger 衍生。重建會掉資料，就是寫入路徑有 bug。
- **view 過期：**consolidation 已經把紀錄關掉，index 還搜得到，檢索就會撈出不存在的 memory。
  任何改動紀錄的流程跑完就 reindex，不要靠定時。
- **每種 view 都存起來：**存五種 view 就有五個要失效的東西、五種互相對不上的方式。
  搜尋需要的才存，小的讀時現算。
- **所有租戶共用一個 index：**沒有 scope 欄位的 view 會在查詢時跨界，這比儲存層洩漏更糟，
  因為結果看起來就像「相關」，而且重建也變成破壞性操作：一個 scope 重建就清掉另一個的資料。
  scope 欄位寫在 schema 裡，每個讀取都照它過濾。
- **wiki 的連結沒人管：**沒人建連結，每一頁都是孤島，wiki 就跟直接列出紀錄沒有差別；
  連結指到被關掉的紀錄，agent 點進去又是空的。
  要做這個 view，得先有東西負責產生連結，而且連結只指向還生效的紀錄。
- **蓋了一張沒人問的圖：**抽取和建 index 每筆 event 都要花錢，沒人拿來問多跳問題的圖，
  效果和 wiki 一樣，成本卻高很多。
- **拿 index 決定相關性：**通道只回傳 candidate，不排最終答案。
  融合排序是第 8 章的事，塞進這裡，兩章都沒辦法分開測。

---

## 可執行程式

[`src/`](src/) 承接 05，加入：

- [`index.py`](src/index.py)：`MemoryIndex`，提供 `rebuild`、`keyword`、`recent`、`between`、`count`，
  每個都要帶 scope，另外有讀取時現算的 `profile`。
- [`engine.py`](src/engine.py)：`reindex` 從紀錄重建一個 scope 的 sparse view，
  `between` 讀時間區間，`profile` 把同一批紀錄投影成另一種 view。
- [`test.py`](src/test.py)：關鍵字和時間區間查詢、清空後重建而且一筆不少、
  profile 按 claim key 收斂並排除被取代的紀錄、
  一個 scope reindex 之後，另一個租戶什麼都查不到。

```bash
python sections/07-index-views/src/test.py   # offline checks, no key
```

這一章完全不呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：
  從不可變的原始來源整理出來的 markdown wiki。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：在實體圖上跑 Personalized PageRank 做多跳檢索。
- [Graphiti](https://arxiv.org/abs/2501.13956)：增量更新的 temporal knowledge graph。
- [Production memory track](../../README.zh-TW.md)：這一章所在的完整 lifecycle。
