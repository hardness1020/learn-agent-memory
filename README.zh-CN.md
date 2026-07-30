# Learn Agent Memory

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> Production 的 agent memory 不是一个向量数据库，而是一套系统：原始证据完整保留，查询用的 memory 都从证据整理出来，随时可以重建。

第 9 章教的是最小可行的 memory loop：一个文件存储区、一份索引、turn 开始时 recall、
run 结束时 extraction，再加一份原始 session log。只管一个用户、一个 agent，那个 loop 就够了。
但 production 的 memory 要面对多个 tenant、多个 agent，还有累积好几年的历史。
到了这个规模，memory 本身就是一个子系统，所以独立成一条 track（跨章节的深入主题），不再塞进第 9 章。

完整的 pipeline：

![Production memory pipeline](assets/production-memory.png)

整个设计最重要的一条规则：

> 原始事件不可丢。整理出来的 memory 随时可以重建。

Capture 之后的每一层都是 view：从事件算出来、给查询用的一份副本，永远不是唯一的一份。
extraction、consolidation 或索引坏掉都没关系，从 event log 重新算一次就好。
[MemMachine](https://arxiv.org/abs/2604.04853) 也是同样的立场：
完整的 episode 留着当 ground truth，profile、索引和 contextual retrieval 都叠在上面。

---

## 生命周期

整个生命周期分成十个阶段，每个阶段都是一个独立的设计决策。

### 1. Scope：这是谁的 memory

每一笔 memory 都要先讲清楚：属于哪个 tenant、哪个用户、哪个 agent、哪个 project 或 task。
还有：谁可以读、要留多久、里面有没有敏感数据。

```python
class MemoryScope(BaseModel):
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
```

没有 scope，就算 retrieval 再准，也可能把一个用户的事实泄漏到另一个用户的 turn 里。

这个阶段有独立的一章，含可执行程序：[0 · Memory contract](00-memory-contract/README.zh-CN.md)。

### 2. Capture：先把原始证据留下来

输入不是只有聊天消息。
用户说了什么、assistant 回了什么、tool 怎么调用、结果长什么样、环境状态、用户的纠正、
task 的成败、批准与拒绝、错误和重试，全部都要收。

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

这一层是 append-only 的 event log：只往后加，不回头改。旧事件不要覆盖：

```text
event-001: User prefers Python
event-002: User selected TypeScript for this project
event-003: User clarified Python is still their primary language
```

这个阶段有独立的一章，含可执行程序：[1 · Event ledger](01-event-ledger/README.zh-CN.md)。

### 3. Write gate：值不值得记

不是每个 turn 都值得变成长期 memory。write policy 帮每一笔 candidate 打分：

```text
Novelty       是否已经存在？
Durability    下次还有用吗？
Specificity   够不够具体？
Confidence    是用户明说的，还是模型猜的？
Sensitivity   适不适合长期保存？
Derivability  grep、git 或数据库能不能重新查到？
```

判断完要留下一个明确的决定和理由，而不是悄悄写进去就算了：

```python
class WriteDecision(BaseModel):
    action: Literal["store", "ignore", "defer", "require_approval"]
    reason: str
    confidence: float
```

Production 的默认做法很朴素：先用规则挡掉明显不用存的，再让一个 LLM 判断相关性，通过 schema 检查才 commit。
最前沿的做法是让模型自己学会这个 gate：[Memory-R1](https://arxiv.org/html/2508.19828v2) 拿任务成败当反馈，
用 RL 学着选 `ADD / UPDATE / DELETE / NOOP`，
[AgeMem](https://arxiv.org/abs/2601.01885) 更把短期 context 和长期 memory 的操作放进同一套 policy。
这条路要有大量任务相关的训练数据才走得通，不适合当第一版。

这个阶段有独立的一章，含可执行程序：[2 · Write policy](02-write-policy/README.zh-CN.md)。

### 4. Encode：帮 memory 分型

不要把所有 memory 都当成一堆纯文本存在一起。功能上至少分三种：

- **Episodic**：发生过什么。“部署卡在 migration lock，最后用 rollback 解决。”时间、环境和结果都留着。
- **Semantic**：目前相信什么。“Marcus 主要写 Python。”通常是从多个 episode 整理出来的。
- **Procedural**：下次该怎么做。“跑 schema migration 前先检查 migration lock。”workflow、runbook 和 gotcha 都算。

分完这三种还不够。同一笔内容还要标它可信到什么程度：
是原始观察、可验证的事实、用户自己说的偏好、系统推论出来的，还是主观意见。

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

[Hindsight](https://arxiv.org/html/2512.12818v1) 就是这样把事实、经验、观察和意见分开存的，
“发生过的事”才不会和“agent 对这件事的看法”混在一起。

这个阶段有独立的一章，含可执行程序：[3 · Typed memory](03-typed-memory/README.zh-CN.md)。

### 5. Resolve：身份、冲突和时间

“Marcus”和“Ming-Siang”是不是同一个人？
“住在 San Diego”和“住在 San Francisco”是矛盾，还是不同时间各自成立的两个事实？

要回答这种问题，每笔记录得同时记两种时间：

```python
class TemporalMetadata(BaseModel):
    valid_from: datetime | None = None   # true in the world since
    valid_to: datetime | None = None
    recorded_at: datetime                # known to the system since
    superseded_at: datetime | None = None
```

`valid_*` 记的是事情在真实世界什么时候成立，`recorded_*` 记的是系统什么时候得知。
这叫 bitemporal。
[Graphiti 和 Zep](https://arxiv.org/abs/2501.13956) 就用这个做 temporal knowledge graph：
旧事实标成过期留着，不会被盖掉。

比起 `UPDATE` 和 `DELETE`，优先用不破坏数据的操作：

```text
ADD · LINK · SUPERSEDE · RETRACT · ABSTRACT
```

```text
Memory A: lives_in San Diego      status: superseded   valid_to: 2025-08
Memory B: lives_in San Francisco  status: active       valid_from: 2025-08   sources: [event-317]
```
这个阶段有独立的一章，含可执行程序：[4 · Temporal resolution](04-temporal-resolution/README.zh-CN.md)。


### 6. Consolidate：把事件变成知识

Consolidation 不是把东西摘要一下就好。
它要做的事包括：合并重复、认出同一个人和事物、找出矛盾、把过期的事实标掉、把相关的事实并成一笔、从多次事件归纳出偏好、
从成功经验整理出 workflow、更新 confidence，还有淘汰没用的 memory。

分三个层级：

```text
Level 1  Compression      多条相似的 memory → 一条较短的 memory
Level 2  Abstraction      多次事件 → 一条规律或偏好
Level 3  Skill formation  多次成功的 run → 一个可重用的流程
```

目前几条主要路线：

- [Graphiti](https://arxiv.org/abs/2501.13956) 用 supersede 和有效期间来管理会变动的事实。
- [A-Mem](https://arxiv.org/html/2502.12110v1) 让新的 note 自己链接旧 note，还会回头更新它们。
- [Hindsight](https://arxiv.org/html/2512.12818v1) 从证据一路整理出 observation 和 belief。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1) 趁没有 query 的时候在后台先整理，把成本移出 hot path。
- [Memory-R1](https://arxiv.org/html/2508.19828v2) 和 [AgeMem](https://arxiv.org/abs/2601.01885) 用 RL 学出何时保存、修改、遗忘。

Production 最安全的做法，是让 LLM 只负责提议，真正动手前先过一层固定规则的检查：

```text
LLM proposes consolidation operations
    ↓
Deterministic validator checks schema, permissions,
source existence, temporal consistency, destructive-operation policy
    ↓
Commit derived views
```

不要让 consolidation model 删掉唯一一份证据。
这个阶段有独立的一章，含可执行程序：[5 · Consolidation](05-consolidation/README.zh-CN.md)。


### 7. Index：一份 ledger，多种 view

view 是借数据库的说法：从原始数据算出来、专门给查询用的一份数据，不是唯一的正本。
同一笔事件可以同时整理成好几种 view：

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

这些 view 全部都是从 event log 算出来的。哪一个坏了，重算就好。

最常被拿出来讨论的有两种 view：

- **Wiki view**：把 memory 写成一页一页的 markdown，页面之间互相链接，agent 用读文件、改文件的工具来维护。
  好处是几乎不用架任何东西，内容人直接看得懂，要 consolidate 就是把那一页重写一遍。
  这个做法是 Karpathy 提出的，叫 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：
  原始来源另外存一份、永远不动，wiki 页面都从它整理出来，跟这条 track 的第一条规则一样。
  页面之间的链接不用另外建 graph：就是正文里的 markdown link，LLM 更新页面时，
  照 schema（教 LLM 怎么维护这个 wiki 的说明文件）里的惯例顺手补上。
  [A-Mem](https://arxiv.org/html/2502.12110v1) 的笔记链接、Claude Code 的 memory 目录，走的都是这条路。
- **Graph view**：把事件里的人、东西和它们的关系抽出来，存成一张图。
  它最擅长这种问题：答案要串好几笔 memory 才拼得出来，或者跟时间先后有关。
  [Graphiti](https://arxiv.org/abs/2501.13956) 每来一笔新事件就顺手更新图，查询时不用再调用 LLM，所以很快。
  [HippoRAG](https://arxiv.org/abs/2405.14831) 则是在图上跑 Personalized PageRank，要串好几步的证据一次查询就拿齐。
  Microsoft GraphRAG 是另一种做法：整批文档先建图、再整理出 community summary。
  它适合不会变的文档问答，不适合一直有新事件进来的 memory，因为建图慢，回答也慢。

两条路的差别主要在成本。wiki 几乎不用架任何东西；graph 要多养一个图数据库，每笔写入还要先跑一次抽取。
选哪条看你要回答什么问题：想让 agent 自己查资料、慢慢研究的，用 wiki 就够；常要问“谁、跟谁、什么时候”的，才值得上 graph。
这个阶段有独立的一章，含可执行程序：[6 · Index views](06-index-views/README.zh-CN.md)。


### 8. Retrieve：不只是 vector search

成熟的 retrieval 不是发一次 vector search 就完事。整条 pipeline 长这样：

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

- **Hybrid retrieval**：只靠 embedding 很容易漏掉确切的名字、日期和否定条件。keyword、dense、graph 和时间信号要一起用。
- **Contextual expansion**：先找到最相关的那一句，再把它前后的对话或 trajectory 一起带出来，
  才不会抽到一句没头没尾的话。
  [MemMachine](https://arxiv.org/abs/2604.04853) 的实验发现，把 retrieval 做深、把呈现格式弄好、把来源前后文展开，
  效果常常比在写入端调 chunking 更大。
- **Adaptive retrieval**：简单的问题直接查。难的再拆成多个 query、一步一步追查，或者走 graph。
- **Agentic file retrieval**：[AgentRunbook-C](https://arxiv.org/abs/2605.12493) 干脆把 trajectory 存成文件，
  让 coding agent 在 sandbox 里自己搜索、自己整理。
  它在 [LongMemEval-V2](https://arxiv.org/abs/2605.12493) 上赢过 RAG baseline，代价是比较慢。
  有些 memory 问题本来就更像在研究一个 repository，不是查一次最近邻就能解决。

这个阶段有独立的一章，含可执行程序：[7 · Hybrid retrieval](07-hybrid-retrieval/README.zh-CN.md)。

### 9. Context assembly：安全地放回 context

Retriever 不要只返回几段文字，要连证据一起带回来：

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

拿到之后，context builder 还有一串决定要做：放哪几笔、每一类 memory 给多少 token、
要不要把矛盾摊开给模型看、要不要请 agent 先不回答、来源和新旧要怎么标。
最重要的一条：recall 回来的内容是不受信任的数据，不是指令，不能变成新的 system instruction。
第 9 章用 `<system-reminder>` 把它包起来，就是这个道理。

这个阶段有独立的一章，含可执行程序：[8 · Context assembly](08-context-assembly/README.zh-CN.md)。

### 10. Feedback 与 evaluation

每次回答完都要记下：这次用了哪些 memory？哪些真的提供了证据？哪些反而误导？用户有没有回头纠正？

评估也要分四层，不能只看最后答对没有：

```text
Write quality       write precision, duplicate rate, unsupported-memory rate, wrong-update rate
Retrieval quality   recall@k, evidence precision, temporal correctness, contradiction coverage
Context quality     injected tokens, stale-memory rate, unsupported claims, prompt-injection detection
End-to-end          extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention
```

End-to-end 那几类任务出自 [LongMemEval](https://arxiv.org/abs/2410.10813)。
[LongMemEval-V2](https://arxiv.org/abs/2605.12493) 再把范围从聊天扩到 agent 的工作经验：
static state、dynamic state、workflow knowledge、environment gotchas 和 premise awareness。

这个阶段有独立的一章，含可执行程序：[9 · Evaluation and governance](09-evaluation-governance/README.zh-CN.md)。

---

## 三条时钟

同一套系统其实跑在三种节奏上：查询当下走 hot path，一次 run 结束走 warm path，后台整理走 cold path。

| | Hot path | Warm path | Cold path |
| --- | --- | --- | --- |
| **时机** | 每个 query | run 结束时 | 后台或调度 |
| **工作** | plan、retrieve、rerank、assemble、inject | 写入原始事件、write gate、抽出 memory candidate | consolidate、去重、supersede、更新 profile、重建索引、评估 |
| **限制** | 低 latency，严格的 token budget | 可以多一次 model call，但不能挡太久 | 可以慢，但必须安全 |

第 9 章已经有这个雏形：turn 前 recall、run 结束 extraction、后台 consolidation（第 13 章）。
这条 track 沿用同样的三条时钟，把每一条放大。

---

## 核心抽象

对外的接口可以只有三个动作：observe 收进一笔事件，recall 查 memory，consolidate 做后台整理。

```python
class MemoryEngine(Protocol):
    async def observe(self, event: MemoryEvent) -> ObservationResult: ...
    async def recall(self, query: MemoryQuery) -> MemoryContext: ...
    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...
```

内部就是十个阶段各一个组件：`EventLedger`、`WritePolicy`、`MemoryExtractor`、`MemoryResolver`、
`Consolidator`、`MemoryIndex`、`QueryPlanner`、`Retriever`、`Reranker`、`ContextAssembler`、`MemoryEvaluator`。

核心 record 把种类、可信程度、时间和出处绑在一起：

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

## 每一层可以参考谁

| 抽象层 | 主要参考 |
| --- | --- |
| Raw evidence ledger | [MemMachine](https://arxiv.org/abs/2604.04853)、[Hermes session log](https://github.com/NousResearch/hermes-agent) |
| Write selection | [Mem0](https://arxiv.org/abs/2504.19413)、[LangMem](https://github.com/langchain-ai/langmem)、[Memory-R1](https://arxiv.org/html/2508.19828v2) |
| Typed facts 与 profile | [LangMem](https://github.com/langchain-ai/langmem)、[Hindsight](https://arxiv.org/html/2512.12818v1) |
| Temporal facts | [Graphiti](https://arxiv.org/abs/2501.13956)、[Zep](https://arxiv.org/abs/2501.13956) |
| Dynamic linking | [A-Mem](https://arxiv.org/html/2502.12110v1) |
| Wiki-style views | [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)、[A-Mem](https://arxiv.org/html/2502.12110v1)、Claude Code auto memory（[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)） |
| Graph views | [Graphiti](https://arxiv.org/abs/2501.13956)、[HippoRAG](https://arxiv.org/abs/2405.14831) |
| Reflection 与 belief | [Hindsight](https://arxiv.org/html/2512.12818v1) |
| Background consolidation | [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)、Claude Code 的后台 consolidation（[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)） |
| Hybrid retrieval | [Mem0](https://arxiv.org/abs/2504.19413)、[Graphiti](https://arxiv.org/abs/2501.13956)、[MemMachine](https://arxiv.org/abs/2604.04853) |
| Agentic deep retrieval | [AgentRunbook-C](https://arxiv.org/abs/2605.12493) |
| Learned memory policy | [Memory-R1](https://arxiv.org/html/2508.19828v2)、[AgeMem](https://arxiv.org/abs/2601.01885)、[Memory-R2](https://arxiv.org/abs/2605.21768) |
| Evaluation | [LongMemEval](https://arxiv.org/abs/2410.10813)、[LongMemEval-V2](https://arxiv.org/abs/2605.12493)、[LoCoMo](https://arxiv.org/abs/2402.17753) |

---

## Roadmap

分三版做，每一版都能单独上线：

- **V1：production-safe baseline：**不可变的 SQLite event log、typed record、
  规则加 LLM 的 write gate、semantic facts、FTS5 retrieval、来源事件引用、tenant 与用户隔离。
  不需要 graph database，也不需要 RL。
- **V2：真正有用的长期 memory：**加上 episodic 和 procedural 两种 memory、bitemporal 字段、
  用 `SUPERSEDE` 取代破坏性的 `UPDATE`、后台 consolidation、BM25 加 vector 的 hybrid retrieval、
  contextual source expansion，和一套 memory eval dataset。
- **V3：frontier experiments：**加上 agentic link generation、sleep-time reflection、
  retrieval agent、用训练学出来的 write 与 retrieval policy，和 multi-agent shared memory。

上面十个阶段，之后可以一个一个写成独立的一章，配上第 9 章那种跑得起来的代码。先做 V1 会用到的阶段。
各阶段的 `src/` 跟 sections 一样一个接一个往前带：把相邻两个阶段 diff 一下，差异就是那个阶段的机制，最后一个阶段就是完整的 engine。

这条 track 浓缩成五条规则：

> Log before extract. Evidence before summary. Supersede before delete. Retrieve before inject. Evaluate before trust.

---

## Sources

- [MemMachine](https://arxiv.org/abs/2604.04853)：主张保留 ground truth 的 memory 系统，retrieval 时带出完整 episode 的前后文。
- [Zep / Graphiti](https://arxiv.org/abs/2501.13956)：用 temporal knowledge graph 做 agent memory，事实带两种时间。
- [Hindsight](https://arxiv.org/html/2512.12818v1)：retain、recall、reflect 三步，把事实、观察和意见分开存。
- [A-Mem](https://arxiv.org/html/2502.12110v1)：agentic memory，新 note 会自己链接并更新旧 note。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用 RL 训练 memory manager，学着选 `ADD / UPDATE / DELETE / NOOP`。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)：趁没有 query 时在后台先整理，省下查询当下的成本。
- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：idea file 原文。
  由 LLM 维护的 markdown wiki，原始来源永远不动，页面都从它整理出来。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：knowledge graph 加 Personalized PageRank，要串好几步的证据一次就查齐。
- [LongMemEval](https://arxiv.org/abs/2410.10813)：长期交互 memory 的 benchmark，五类任务。
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493)：把 benchmark 扩到 agent 的工作经验，AgentRunbook-C 的文件式检索出自这里。
- [第 9 章 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)：这条 track 的起点，最小可行的 memory loop。
