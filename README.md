# Learn Agent Memory

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> Production agent memory is not a vector database. It is a system that keeps raw evidence and derives queryable memory views from it.

Section 9 teaches the minimum memory loop: a file store, an index, recall at turn start, extraction at run end, and a raw session log.
That loop serves one user and one agent. Production memory serves many tenants, many agents, and years of history.
At that scale, memory is a subsystem of its own, so it gets its own track instead of growing inside section 9.

The full pipeline:

![Production memory pipeline](assets/production-memory.png)

One rule carries the whole design:

> Raw events are never dropped. Derived memories can always be rebuilt.

Everything after capture is a view: a queryable copy computed from the events, never the only copy.
If extraction, consolidation, or an index goes wrong, the system rebuilds it from the event log.
[MemMachine](https://arxiv.org/abs/2604.04853) takes the same position: keep full episodes as ground truth, then layer profiles, indexes, and contextual retrieval on top.

---

## Lifecycle

Ten stages. Each one is a separate design decision.

### 1. Scope: whose memory is this

Every record answers: which tenant, which user, which agent, which project or task.
Also: who may read it, how long it lives, and whether it holds sensitive data.

```python
class MemoryScope(BaseModel):
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
```

Without scope, even perfect retrieval can leak one user's facts into another user's turn.

This stage has its own page with runnable code: [0 · Memory contract](00-memory-contract/).

### 2. Capture: keep the raw evidence

Input is more than chat. Capture user messages, assistant responses, tool calls and results, trajectories,
environment state, user corrections, task outcomes, approvals and rejections, errors, and retries.

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

This layer is an append-only event log. Never overwrite an old event in place:

```text
event-001: User prefers Python
event-002: User selected TypeScript for this project
event-003: User clarified Python is still their primary language
```

This stage has its own page with runnable code: [1 · Event ledger](01-event-ledger/).

### 3. Write gate: is this worth remembering

Not every turn deserves long-term memory. A write policy scores each candidate:

```text
Novelty       does it already exist?
Durability    useful next time?
Specificity   concrete enough?
Confidence    stated by the user, or guessed by the model?
Sensitivity   safe to keep long term?
Derivability  can grep, git, or a database answer it again?
```

The output is an explicit decision, not a silent side effect:

```python
class WriteDecision(BaseModel):
    action: Literal["store", "ignore", "defer", "require_approval"]
    reason: str
    confidence: float
```

A production default runs rules first, then an LLM relevance classifier, then schema validation, then commit.
The frontier trains the gate itself: [Memory-R1](https://arxiv.org/html/2508.19828v2) uses outcome-driven RL to pick `ADD / UPDATE / DELETE / NOOP`,
and [AgeMem](https://arxiv.org/abs/2601.01885) folds short-term context and long-term memory operations into one agent policy.
That path needs task-specific training data, so it is not a first version.

This stage has its own page with runnable code: [2 · Write policy](02-write-policy/).

### 4. Encode: type the memory

Do not put everything in one text bucket. Three functional kinds:

- **Episodic**: what happened. "Deploy hit a migration lock, fixed by rollback." Keeps time, environment, and outcome.
- **Semantic**: what is currently believed. "Marcus mainly writes Python." Usually distilled from episodes.
- **Procedural**: what to do next time. "Check migration locks before schema migration." Workflows, runbooks, gotchas.

Then a separate axis for epistemic status:

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

[Hindsight](https://arxiv.org/html/2512.12818v1) separates world facts, experiences, observations, and opinions,
so "what happened" never blends into "what the agent thinks about it".

This stage has its own page with runnable code: [3 · Typed memory](03-typed-memory/).

### 5. Resolve: identity, conflict, and time

Is "Marcus" the same person as "Ming-Siang"?
Is "lives in San Diego" against "lives in San Francisco" a contradiction, or two facts from different times?

Keep two timelines per record:

```python
class TemporalMetadata(BaseModel):
    valid_from: datetime | None = None   # true in the world since
    valid_to: datetime | None = None
    recorded_at: datetime                # known to the system since
    superseded_at: datetime | None = None
```

That is bitemporal modeling. [Graphiti and Zep](https://arxiv.org/abs/2501.13956) build a temporal knowledge graph on it: old facts get closed, not overwritten.

Prefer non-destructive operations over `UPDATE` and `DELETE`:

```text
ADD · LINK · SUPERSEDE · RETRACT · ABSTRACT
```

```text
Memory A: lives_in San Diego      status: superseded   valid_to: 2025-08
Memory B: lives_in San Francisco  status: active       valid_from: 2025-08   sources: [event-317]
```
This stage has its own page with runnable code: [4 · Temporal resolution](04-temporal-resolution/).


### 6. Consolidate: turn events into knowledge

Consolidation is not summarization. It covers deduplication, entity resolution, conflict detection,
temporal invalidation, fact merging, preference induction, workflow extraction, confidence updates, and forgetting.

Three levels:

```text
Level 1  Compression      many similar memories → one shorter memory
Level 2  Abstraction      many events → one rule or preference
Level 3  Skill formation  many successful runs → one reusable procedure
```

Current approaches: temporal consolidation with supersede and validity windows ([Graphiti](https://arxiv.org/abs/2501.13956)),
agentic organization where a new note links to and evolves old ones ([A-Mem](https://arxiv.org/html/2502.12110v1)),
structured reflection from evidence to observations and beliefs ([Hindsight](https://arxiv.org/html/2512.12818v1)),
sleep-time consolidation that moves work off the query hot path ([Sleep-time Compute](https://arxiv.org/html/2504.13171v1)),
and learned policies that decide when to store, edit, and forget ([Memory-R1](https://arxiv.org/html/2508.19828v2), [AgeMem](https://arxiv.org/abs/2601.01885)).

The safe production pattern:

```text
LLM proposes consolidation operations
    ↓
Deterministic validator checks schema, permissions,
source existence, temporal consistency, destructive-operation policy
    ↓
Commit derived views
```

Never let the consolidation model delete the only copy of evidence.
This stage has its own page with runnable code: [5 · Consolidation](05-consolidation/).


### 7. Index: many views over one ledger

One event can feed several query-time views:

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

All of these are derived views. A corrupted index rebuilds from the raw events.

Two view families come up most often:

- **Wiki views**: linked markdown pages the agent reads and edits with file tools.
  Almost no infrastructure, human-auditable, and consolidation is just rewriting a page.
  Karpathy proposed this pattern as the [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
  Keep the sources immutable and compile every page from them, the same rule this track starts from.
  Page connections need no graph store: they are plain markdown links in the page text,
  added by the LLM as it updates pages, following conventions in a schema file that tells it how to maintain the wiki.
  [A-Mem](https://arxiv.org/html/2502.12110v1)'s linked notes and Claude Code's memory directory have the same shape.
- **Graph views**: extracted entities and edges, built for multi-hop and temporal questions.
  [Graphiti](https://arxiv.org/abs/2501.13956) updates the graph incrementally and answers without an LLM call.
  [HippoRAG](https://arxiv.org/abs/2405.14831) runs Personalized PageRank over the graph to gather multi-hop evidence in one step.
  Batch corpus graphs with community summaries (Microsoft GraphRAG) fit static document QA, not live memory. Indexing and answering are both slow.

The wiki is the low-infrastructure end. The graph is the high-infrastructure end.
Choose by query shape: agentic research suits the wiki, relational and temporal questions suit the graph.
This stage has its own page with runnable code: [6 · Index views](06-index-views/).


### 8. Retrieve: more than vector search

A mature pipeline:

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

- **Hybrid retrieval.** Embeddings alone miss exact names, dates, and negations. Combine keyword, dense, graph, and temporal signals.
- **Contextual expansion.** Find the nucleus match, then expand into the surrounding conversation or trajectory.
  [MemMachine](https://arxiv.org/abs/2604.04853) reports that retrieval depth, formatting, and source expansion often beat better ingestion chunking.
- **Adaptive retrieval.** Route by query difficulty: direct lookup, parallel decomposition, iterative chain-of-query, or graph traversal.
- **Agentic file retrieval.** [AgentRunbook-C](https://arxiv.org/abs/2605.12493) stores trajectories as files and lets a coding agent search them in a sandbox.
  It beats RAG baselines on [LongMemEval-V2](https://arxiv.org/abs/2605.12493), at higher latency. Some memory questions are repository research, not nearest-neighbor lookup.

This stage has its own page with runnable code: [7 · Hybrid retrieval](07-hybrid-retrieval/).

### 9. Context assembly: inject safely

The retriever returns evidence bundles, not bare strings:

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

The context builder then decides what to include, the token budget per memory kind, whether to surface contradictions,
whether to ask the agent to abstain, and how to mark source and freshness.
Recalled memory is untrusted data, never a new system instruction. Section 9's `<system-reminder>` framing applies here too.

This stage has its own page with runnable code: [8 · Context assembly](08-context-assembly/).

### 10. Feedback and evaluation

After each answer, record which memories were used, which provided real evidence, which misled,
and whether the user corrected the result. Then evaluate in four layers:

```text
Write quality       write precision, duplicate rate, unsupported-memory rate, wrong-update rate
Retrieval quality   recall@k, evidence precision, temporal correctness, contradiction coverage
Context quality     injected tokens, stale-memory rate, unsupported claims, prompt-injection detection
End-to-end          extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention
```

[LongMemEval](https://arxiv.org/abs/2410.10813) defines the end-to-end task families above.
[LongMemEval-V2](https://arxiv.org/abs/2605.12493) extends them to agent experience: static state, dynamic state, workflow knowledge, environment gotchas, and premise awareness.

This stage has its own page with runnable code: [9 · Evaluation and governance](09-evaluation-governance/).

---

## Three clocks

The same system runs on three schedules:

| | Hot path | Warm path | Cold path |
| --- | --- | --- | --- |
| **When** | every query | run end | background or scheduled |
| **Work** | plan, retrieve, rerank, assemble, inject | append raw event, write gate, extract candidates | consolidate, dedupe, supersede, build profile, reindex, evaluate |
| **Constraint** | low latency, strict token budget | one extra model call, no long block | can be slow, must be safe |

Section 9 already has this shape: recall before the turn, extraction at run end, consolidation in the background (section 13).
This track keeps the same three clocks and scales each one.

---

## Core abstractions

The engine surface stays small:

```python
class MemoryEngine(Protocol):
    async def observe(self, event: MemoryEvent) -> ObservationResult: ...
    async def recall(self, query: MemoryQuery) -> MemoryContext: ...
    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...
```

Inside, each lifecycle stage is one component: `EventLedger`, `WritePolicy`, `MemoryExtractor`, `MemoryResolver`,
`Consolidator`, `MemoryIndex`, `QueryPlanner`, `Retriever`, `Reranker`, `ContextAssembler`, `MemoryEvaluator`.

The core record ties kind, epistemic status, time, and provenance together:

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

## Reference systems per layer

| Layer | Reference systems |
| --- | --- |
| Raw evidence ledger | [MemMachine](https://arxiv.org/abs/2604.04853), [Hermes session log](https://github.com/NousResearch/hermes-agent) |
| Write selection | [Mem0](https://arxiv.org/abs/2504.19413), [LangMem](https://github.com/langchain-ai/langmem), [Memory-R1](https://arxiv.org/html/2508.19828v2) |
| Typed facts and profiles | [LangMem](https://github.com/langchain-ai/langmem), [Hindsight](https://arxiv.org/html/2512.12818v1) |
| Temporal facts | [Graphiti](https://arxiv.org/abs/2501.13956), [Zep](https://arxiv.org/abs/2501.13956) |
| Dynamic linking | [A-Mem](https://arxiv.org/html/2502.12110v1) |
| Wiki-style views | [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), [A-Mem](https://arxiv.org/html/2502.12110v1), Claude Code auto memory ([section 9](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/)) |
| Graph views | [Graphiti](https://arxiv.org/abs/2501.13956), [HippoRAG](https://arxiv.org/abs/2405.14831) |
| Reflection and beliefs | [Hindsight](https://arxiv.org/html/2512.12818v1) |
| Background consolidation | [Sleep-time Compute](https://arxiv.org/html/2504.13171v1), Claude Code background consolidation ([section 9](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/)) |
| Hybrid retrieval | [Mem0](https://arxiv.org/abs/2504.19413), [Graphiti](https://arxiv.org/abs/2501.13956), [MemMachine](https://arxiv.org/abs/2604.04853) |
| Agentic deep retrieval | [AgentRunbook-C](https://arxiv.org/abs/2605.12493) |
| Learned memory policy | [Memory-R1](https://arxiv.org/html/2508.19828v2), [AgeMem](https://arxiv.org/abs/2601.01885), [Memory-R2](https://arxiv.org/abs/2605.21768) |
| Evaluation | [LongMemEval](https://arxiv.org/abs/2410.10813), [LongMemEval-V2](https://arxiv.org/abs/2605.12493), [LoCoMo](https://arxiv.org/abs/2402.17753) |

---

## Roadmap

Three versions, each shippable on its own:

- **V1, production-safe baseline.** Immutable SQLite event log, typed records, rule plus LLM write gate,
  semantic facts, FTS5 retrieval, source event citations, tenant and user isolation. No graph database, no RL.
- **V2, useful long-term memory.** Adds episodic and procedural kinds, bitemporal fields,
  `SUPERSEDE` instead of destructive `UPDATE`, background consolidation, BM25 plus vector hybrid retrieval,
  contextual source expansion, and a memory eval dataset.
- **V3, frontier experiments.** Adds agentic link generation, sleep-time reflection, a retrieval agent,
  learned write and retrieval policies, and multi-agent shared memory.

Each lifecycle stage above can grow into its own page with runnable code, in section 9's style. V1 stages come first.
The stage pages carry their `src/` forward stage by stage, the way sections do:
diff two adjacent stages and the diff is that stage's mechanism, and the last page holds the whole engine.

The track in five rules:

> Log before extract. Evidence before summary. Supersede before delete. Retrieve before inject. Evaluate before trust.

---

## Sources

- [MemMachine](https://arxiv.org/abs/2604.04853): ground-truth-preserving memory, contextual retrieval over full episodes.
- [Zep / Graphiti](https://arxiv.org/abs/2501.13956): temporal knowledge graph for agent memory, bitemporal facts.
- [Hindsight](https://arxiv.org/html/2512.12818v1): retain, recall, reflect. Epistemic split between facts, observations, and opinions.
- [A-Mem](https://arxiv.org/html/2502.12110v1): agentic memory, new notes link to and evolve existing ones.
- [Memory-R1](https://arxiv.org/html/2508.19828v2): RL-trained memory manager over `ADD / UPDATE / DELETE / NOOP`.
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1): background consolidation off the query hot path.
- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): idea file. An LLM-maintained markdown wiki compiled over immutable sources.
- [HippoRAG](https://arxiv.org/abs/2405.14831): knowledge graph plus Personalized PageRank, multi-hop evidence in one retrieval step.
- [LongMemEval](https://arxiv.org/abs/2410.10813): long-term interactive memory benchmark, five task families.
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493): agent-experience benchmark, AgentRunbook-C agentic file retrieval.
- [Section 9 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/): the minimum loop this track scales up.
