# Learn Agent Memory

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> Production 的 agent memory 不是一個向量資料庫，而是一套系統：原始證據完整保留，查詢用的 memory 都從證據整理出來，隨時可以重建。

第 9 章教的是最小可行的 memory loop：一個檔案儲存區、一份索引、turn 開始時 recall、
run 結束時 extraction，再加一份原始 session log。顧一個使用者、一個 agent，那個 loop 就夠了。
但 production 的 memory 要面對多個 tenant、多個 agent，還有累積好幾年的歷史。
到了這個規模，memory 本身就是一個子系統，所以獨立成一條 track（跨章節的深入主題），不再塞進第 9 章。

完整的 pipeline：

![Production memory pipeline](assets/production-memory.png)

整個設計最重要的一條規則：

> 原始事件不可丟。整理出來的 memory 隨時可以重建。

Capture 之後的每一層都是 view：從事件算出來、給查詢用的一份副本，永遠不是唯一的一份。
extraction、consolidation 或索引壞掉都沒關係，從 event log 重新算一次就好。
[MemMachine](https://arxiv.org/abs/2604.04853) 也是同樣的立場：
完整的 episode 留著當 ground truth，profile、索引和 contextual retrieval 都疊在上面。

---

## 生命週期

整個生命週期分成十個階段，每個階段都是一個獨立的設計決策。

### 1. Scope：這是誰的 memory

每一筆 memory 都要先講清楚：屬於哪個 tenant、哪個使用者、哪個 agent、哪個 project 或 task。
還有：誰可以讀、要留多久、裡面有沒有敏感資料。

```python
class MemoryScope(BaseModel):
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
```

沒有 scope，就算 retrieval 再準，也可能把一個使用者的事實洩漏到另一個使用者的 turn 裡。

這個階段有獨立的一章，含可執行程式：[0 · Memory contract](00-memory-contract/README.zh-TW.md)。

### 2. Capture：先把原始證據留下來

輸入不是只有聊天訊息。
使用者說了什麼、assistant 回了什麼、tool 怎麼呼叫、結果長怎樣、環境狀態、使用者的糾正、
task 的成敗、核准與拒絕、錯誤和重試，全部都要收。

```python
class MemoryEvent(BaseModel):
    id: UUID
    scope: MemoryScope
    event_type: str
    content: str
    occurred_at: datetime
    recorded_at: datetime
    metadata: dict[str, Any]
```

這一層是 append-only 的 event log：只往後加，不回頭改。舊事件不要覆蓋：

```text
event-001: User prefers Python
event-002: User selected TypeScript for this project
event-003: User clarified Python is still their primary language
```

這個階段有獨立的一章，含可執行程式：[1 · Event ledger](01-event-ledger/README.zh-TW.md)。

### 3. Write gate：值不值得記

不是每個 turn 都值得變成長期 memory。write policy 幫每一筆 candidate 打分：

```text
Novelty       是否已經存在？
Durability    下次還有用嗎？
Specificity   夠不夠具體？
Confidence    是使用者明說的，還是模型猜的？
Sensitivity   適不適合長期保存？
Derivability  grep、git 或資料庫能不能重新查到？
```

判斷完要留下一個明確的決定和理由，而不是悄悄寫進去就算了：

```python
class WriteDecision(BaseModel):
    action: Literal["store", "ignore", "defer", "require_approval"]
    reason: str
    confidence: float
```

Production 的預設做法很樸素：先用規則擋掉明顯不用存的，再讓一個 LLM 判斷相關性，通過 schema 檢查才 commit。
最前沿的做法是讓模型自己學會這個 gate：[Memory-R1](https://arxiv.org/html/2508.19828v2) 拿任務成敗當回饋，
用 RL 學著選 `ADD / UPDATE / DELETE / NOOP`，
[AgeMem](https://arxiv.org/abs/2601.01885) 更把短期 context 和長期 memory 的操作放進同一套 policy。
這條路要有大量任務相關的訓練資料才走得通，不適合當第一版。

這個階段有獨立的一章，含可執行程式：[2 · Write policy](02-write-policy/README.zh-TW.md)。

### 4. Encode：幫 memory 分型

不要把所有 memory 都當成一堆純文字存在一起。功能上至少分三種：

- **Episodic**：發生過什麼。「部署卡在 migration lock，最後用 rollback 解決。」時間、環境和結果都留著。
- **Semantic**：目前相信什麼。「Marcus 主要寫 Python。」通常是從多個 episode 整理出來的。
- **Procedural**：下次該怎麼做。「跑 schema migration 前先檢查 migration lock。」workflow、runbook 和 gotcha 都算。

分完這三種還不夠。同一筆內容還要標它可信到什麼程度：
是原始觀察、可驗證的事實、使用者自己說的偏好、系統推論出來的，還是主觀意見。

```python
MemoryKind = Literal["episodic", "semantic", "procedural"]

EpistemicType = Literal[
    "evidence",      # raw observation
    "fact",          # verifiable
    "preference",    # user preference
    "inference",     # system guess
    "opinion",       # subjective view
]
```

[Hindsight](https://arxiv.org/html/2512.12818v1) 就是這樣把事實、經驗、觀察和意見分開存的，
「發生過的事」才不會和「agent 對這件事的看法」混在一起。

這個階段有獨立的一章，含可執行程式：[3 · Typed memory](03-typed-memory/README.zh-TW.md)。

### 5. Resolve：身分、衝突和時間

「Marcus」和「Ming-Siang」是不是同一個人？
「住在 San Diego」和「住在 San Francisco」是矛盾，還是不同時間各自成立的兩個事實？

要回答這種問題，每筆記錄得同時記兩種時間：

```python
class TemporalMetadata(BaseModel):
    valid_from: datetime | None = None   # true in the world since
    valid_to: datetime | None = None
    recorded_at: datetime                # known to the system since
    superseded_at: datetime | None = None
```

`valid_*` 記的是事情在真實世界什麼時候成立，`recorded_*` 記的是系統什麼時候得知。
這叫 bitemporal。
[Graphiti 和 Zep](https://arxiv.org/abs/2501.13956) 就用這個做 temporal knowledge graph：
舊事實標成過期留著，不會被蓋掉。

比起 `UPDATE` 和 `DELETE`，優先用不破壞資料的操作：

```text
ADD · LINK · SUPERSEDE · RETRACT · ABSTRACT
```

```text
Memory A: lives_in San Diego      status: superseded   valid_to: 2025-08
Memory B: lives_in San Francisco  status: active       valid_from: 2025-08   sources: [event-317]
```
這個階段有獨立的一章，含可執行程式：[4 · Temporal resolution](04-temporal-resolution/README.zh-TW.md)。


### 6. Consolidate：把事件變成知識

Consolidation 不是把東西摘要一下就好。
它要做的事包括：合併重複、認出同一個人事物、找出矛盾、把過期的事實標掉、把相關的事實併成一筆、從多次事件歸納出偏好、
從成功經驗整理出 workflow、更新 confidence，還有淘汰沒用的 memory。

分三個層級：

```text
Level 1  Compression      多條相似的 memory → 一條較短的 memory
Level 2  Abstraction      多次事件 → 一條規律或偏好
Level 3  Skill formation  多次成功的 run → 一個可重用的流程
```

目前幾條主要路線：

- [Graphiti](https://arxiv.org/abs/2501.13956) 用 supersede 和有效期間來管理會變動的事實。
- [A-Mem](https://arxiv.org/html/2502.12110v1) 讓新的 note 自己連結舊 note，還會回頭更新它們。
- [Hindsight](https://arxiv.org/html/2512.12818v1) 從證據一路整理出 observation 和 belief。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1) 趁沒有 query 的時候在背景先整理，把成本移出 hot path。
- [Memory-R1](https://arxiv.org/html/2508.19828v2) 和 [AgeMem](https://arxiv.org/abs/2601.01885) 用 RL 學出何時保存、修改、遺忘。

Production 最安全的做法，是讓 LLM 只負責提議，真正動手前先過一層固定規則的檢查：

```text
LLM proposes consolidation operations
    ↓
Deterministic validator checks schema, permissions,
source existence, temporal consistency, destructive-operation policy
    ↓
Commit derived views
```

不要讓 consolidation model 刪掉唯一一份證據。
這個階段有獨立的一章，含可執行程式：[5 · Consolidation](05-consolidation/README.zh-TW.md)。


### 7. Index：一份 ledger，多種 view

view 是借資料庫的講法：從原始資料算出來、專門給查詢用的一份資料，不是唯一的正本。
同一筆事件可以同時整理成好幾種 view：

```text
Raw event log     SQLite / object storage
Sparse index      BM25 / FTS
Dense index       embeddings
Temporal index    time ranges
Entity graph      nodes + edges
Wiki view         linked markdown pages
Profile view      current user state
Procedure view    skills / runbooks
```

這些 view 全部都是從 event log 算出來的。哪一個壞了，重算就好。

最常被拿出來討論的有兩種 view：

- **Wiki view**：把 memory 寫成一頁一頁的 markdown，頁面之間互相連結，agent 用讀檔、改檔的工具來維護。
  好處是幾乎不用架任何東西，內容人直接看得懂，要 consolidate 就是把那一頁重寫一次。
  這個做法是 Karpathy 提出的，叫 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：
  原始來源另外存一份、永遠不動，wiki 頁面都從它整理出來，跟這條 track 的第一條規則一樣。
  頁面之間的連結不用另外建 graph：就是內文裡的 markdown link，LLM 更新頁面時，
  照 schema（教 LLM 怎麼維護這個 wiki 的說明檔）裡的慣例順手補上。
  [A-Mem](https://arxiv.org/html/2502.12110v1) 的筆記連結、Claude Code 的 memory 目錄，走的都是這條路。
- **Graph view**：把事件裡的人、東西和它們的關係抽出來，存成一張圖。
  它最擅長這種問題：答案要串好幾筆 memory 才拼得出來，或是跟時間先後有關。
  [Graphiti](https://arxiv.org/abs/2501.13956) 每來一筆新事件就順手更新圖，查詢時不用再呼叫 LLM，所以很快。
  [HippoRAG](https://arxiv.org/abs/2405.14831) 則是在圖上跑 Personalized PageRank，要串好幾步的證據一次查詢就拿齊。
  Microsoft GraphRAG 是另一種做法：整批文件先建圖、再整理出 community summary。
  它適合不會變的文件問答，不適合一直有新事件進來的 memory，因為建圖慢，回答也慢。

兩條路的差別主要在成本。wiki 幾乎不用架任何東西；graph 要多養一個圖資料庫，每筆寫入還要先跑一次抽取。
選哪條看你要回答什麼問題：想讓 agent 自己翻資料、慢慢研究的，用 wiki 就夠；常要問「誰、跟誰、什麼時候」的，才值得上 graph。
這個階段有獨立的一章，含可執行程式：[6 · Index views](06-index-views/README.zh-TW.md)。


### 8. Retrieve：不只是 vector search

成熟的 retrieval 不是發一次 vector search 就完事。整條 pipeline 長這樣：

```text
Query understanding
    ↓
Memory-type routing, query expansion
    ↓
Parallel candidates: BM25 · vector · graph · temporal · recent episodes
    ↓
Merge + dedupe, rerank
    ↓
Source-context expansion
    ↓
Token-budget selection
```

- **Hybrid retrieval**：只靠 embedding 很容易漏掉確切的名字、日期和否定條件。keyword、dense、graph 和時間訊號要一起用。
- **Contextual expansion**：先找到最相關的那一句，再把它前後的對話或 trajectory 一起帶出來，
  才不會抽到一句沒頭沒尾的話。
  [MemMachine](https://arxiv.org/abs/2604.04853) 的實驗發現，把 retrieval 做深、把呈現格式弄好、把來源前後文展開，
  效果常常比在寫入端調 chunking 更大。
- **Adaptive retrieval**：簡單的問題直接查。難的再拆成多個 query、一步一步追查，或是走 graph。
- **Agentic file retrieval**：[AgentRunbook-C](https://arxiv.org/abs/2605.12493) 乾脆把 trajectory 存成檔案，
  讓 coding agent 在 sandbox 裡自己搜尋、自己整理。
  它在 [LongMemEval-V2](https://arxiv.org/abs/2605.12493) 上贏過 RAG baseline，代價是比較慢。
  有些 memory 問題本來就更像在研究一個 repository，不是查一次最近鄰就能解決。

這個階段有獨立的一章，含可執行程式：[7 · Hybrid retrieval](07-hybrid-retrieval/README.zh-TW.md)。

### 9. Context assembly：安全地放回 context

Retriever 不要只回傳幾段文字，要連證據一起帶回來：

```python
class RetrievedMemory(BaseModel):
    memory_id: UUID
    content: str
    score: float
    source_event_ids: list[UUID]
    valid_from: datetime | None
    valid_to: datetime | None
    confidence: float
    contradictions: list[UUID]
```

拿到之後，context builder 還有一串決定要做：放哪幾筆、每一類 memory 給多少 token、
要不要把矛盾攤開給模型看、要不要請 agent 先不回答、來源和新舊要怎麼標。
最重要的一條：recall 回來的內容是不受信任的資料，不是指令，不能變成新的 system instruction。
第 9 章用 `<system-reminder>` 把它包起來，就是這個道理。

這個階段有獨立的一章，含可執行程式：[8 · Context assembly](08-context-assembly/README.zh-TW.md)。

### 10. Feedback 與 evaluation

每次回答完都要記下：這次用了哪些 memory？哪些真的提供了證據？哪些反而誤導？使用者有沒有回頭糾正？

評估也要分四層，不能只看最後答對沒有：

```text
Write quality       write precision, duplicate rate, unsupported-memory rate, wrong-update rate
Retrieval quality   recall@k, evidence precision, temporal correctness, contradiction coverage
Context quality     injected tokens, stale-memory rate, unsupported claims, prompt-injection detection
End-to-end          extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention
```

End-to-end 那幾類任務出自 [LongMemEval](https://arxiv.org/abs/2410.10813)。
[LongMemEval-V2](https://arxiv.org/abs/2605.12493) 再把範圍從聊天擴到 agent 的工作經驗：
static state、dynamic state、workflow knowledge、environment gotchas 和 premise awareness。

這個階段有獨立的一章，含可執行程式：[9 · Evaluation and governance](09-evaluation-governance/README.zh-TW.md)。

---

## 三條時鐘

同一套系統其實跑在三種節奏上：查詢當下走 hot path，一次 run 結束走 warm path，背景整理走 cold path。

| | Hot path | Warm path | Cold path |
| --- | --- | --- | --- |
| **時機** | 每個 query | run 結束時 | 背景或排程 |
| **工作** | plan、retrieve、rerank、assemble、inject | 寫入原始事件、write gate、抽出 memory candidate | consolidate、去重、supersede、更新 profile、重建索引、評估 |
| **限制** | 低 latency，嚴格的 token budget | 可以多一次 model call，但不能擋太久 | 可以慢，但必須安全 |

第 9 章已經有這個雛形：turn 前 recall、run 結束 extraction、背景 consolidation（第 13 章）。
這條 track 沿用同樣的三條時鐘，把每一條放大。

---

## 核心抽象

對外的介面可以只有三個動作：observe 收進一筆事件，recall 查 memory，consolidate 做背景整理。

```python
class MemoryEngine(Protocol):
    async def observe(self, event: MemoryEvent) -> ObservationResult: ...
    async def recall(self, query: MemoryQuery) -> MemoryContext: ...
    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...
```

內部就是十個階段各一個元件：`EventLedger`、`WritePolicy`、`MemoryExtractor`、`MemoryResolver`、
`Consolidator`、`MemoryIndex`、`QueryPlanner`、`Retriever`、`Reranker`、`ContextAssembler`、`MemoryEvaluator`。

核心 record 把種類、可信程度、時間和出處綁在一起：

```python
class MemoryRecord(BaseModel):
    id: UUID
    scope: MemoryScope

    kind: Literal["episodic", "semantic", "procedural"]
    epistemic_type: Literal["evidence", "fact", "preference", "inference", "opinion"]

    content: str

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    recorded_at: datetime
    superseded_at: datetime | None = None

    source_event_ids: list[UUID]
    confidence: float = Field(ge=0, le=1)

    status: Literal["active", "superseded", "retracted"]
    tags: list[str] = []
```

---

## 每一層可以參考誰

| 抽象層 | 主要參考 |
| --- | --- |
| Raw evidence ledger | [MemMachine](https://arxiv.org/abs/2604.04853)、[Hermes session log](https://github.com/NousResearch/hermes-agent) |
| Write selection | [Mem0](https://arxiv.org/abs/2504.19413)、[LangMem](https://github.com/langchain-ai/langmem)、[Memory-R1](https://arxiv.org/html/2508.19828v2) |
| Typed facts 與 profile | [LangMem](https://github.com/langchain-ai/langmem)、[Hindsight](https://arxiv.org/html/2512.12818v1) |
| Temporal facts | [Graphiti](https://arxiv.org/abs/2501.13956)、[Zep](https://arxiv.org/abs/2501.13956) |
| Dynamic linking | [A-Mem](https://arxiv.org/html/2502.12110v1) |
| Wiki-style views | [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)、[A-Mem](https://arxiv.org/html/2502.12110v1)、Claude Code auto memory（[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-TW.md)） |
| Graph views | [Graphiti](https://arxiv.org/abs/2501.13956)、[HippoRAG](https://arxiv.org/abs/2405.14831) |
| Reflection 與 belief | [Hindsight](https://arxiv.org/html/2512.12818v1) |
| Background consolidation | [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)、Claude Code 的背景 consolidation（[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-TW.md)） |
| Hybrid retrieval | [Mem0](https://arxiv.org/abs/2504.19413)、[Graphiti](https://arxiv.org/abs/2501.13956)、[MemMachine](https://arxiv.org/abs/2604.04853) |
| Agentic deep retrieval | [AgentRunbook-C](https://arxiv.org/abs/2605.12493) |
| Learned memory policy | [Memory-R1](https://arxiv.org/html/2508.19828v2)、[AgeMem](https://arxiv.org/abs/2601.01885)、[Memory-R2](https://arxiv.org/abs/2605.21768) |
| Evaluation | [LongMemEval](https://arxiv.org/abs/2410.10813)、[LongMemEval-V2](https://arxiv.org/abs/2605.12493)、[LoCoMo](https://arxiv.org/abs/2402.17753) |

---

## Roadmap

分三版做，每一版都能單獨上線：

- **V1：production-safe baseline：**不可變的 SQLite event log、typed record、
  規則加 LLM 的 write gate、semantic facts、FTS5 retrieval、來源事件引用、tenant 與使用者隔離。
  不需要 graph database，也不需要 RL。
- **V2：真正有用的長期 memory：**加上 episodic 和 procedural 兩種 memory、bitemporal 欄位、
  用 `SUPERSEDE` 取代破壞性的 `UPDATE`、背景 consolidation、BM25 加 vector 的 hybrid retrieval、
  contextual source expansion，和一套 memory eval dataset。
- **V3：frontier experiments：**加上 agentic link generation、sleep-time reflection、
  retrieval agent、用訓練學出來的 write 與 retrieval policy，和 multi-agent shared memory。

上面十個階段，之後可以一個一個寫成獨立的一章，配上第 9 章那種跑得起來的程式碼。先做 V1 會用到的階段。
各階段的 `src/` 跟 sections 一樣一個接一個往前帶：把相鄰兩個階段 diff 一下，差異就是那個階段的機制，最後一個階段就是完整的 engine。

這條 track 濃縮成五條規則：

> Log before extract. Evidence before summary. Supersede before delete. Retrieve before inject. Evaluate before trust.

---

## Sources

- [MemMachine](https://arxiv.org/abs/2604.04853)：主張保留 ground truth 的 memory 系統，retrieval 時帶出完整 episode 的前後文。
- [Zep / Graphiti](https://arxiv.org/abs/2501.13956)：用 temporal knowledge graph 做 agent memory，事實帶兩種時間。
- [Hindsight](https://arxiv.org/html/2512.12818v1)：retain、recall、reflect 三步，把事實、觀察和意見分開存。
- [A-Mem](https://arxiv.org/html/2502.12110v1)：agentic memory，新 note 會自己連結並更新舊 note。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用 RL 訓練 memory manager，學著選 `ADD / UPDATE / DELETE / NOOP`。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)：趁沒有 query 時在背景先整理，省下查詢當下的成本。
- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：idea file 原文。
  由 LLM 維護的 markdown wiki，原始來源永遠不動，頁面都從它整理出來。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：knowledge graph 加 Personalized PageRank，要串好幾步的證據一次就查齊。
- [LongMemEval](https://arxiv.org/abs/2410.10813)：長期互動 memory 的 benchmark，五類任務。
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493)：把 benchmark 擴到 agent 的工作經驗，AgentRunbook-C 的檔案式檢索出自這裡。
- [第 9 章 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-TW.md)：這條 track 的起點，最小可行的 memory loop。
