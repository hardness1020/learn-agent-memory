<h1 align="center" style="margin-top: 0;">Learn Agent Memory</h1>

<p align="center">
  <strong>Learn how production agents remember.</strong><br>
</p>

<p align="center">
  <a href="#sections"><img src="https://img.shields.io/badge/Focus-Memory_Engineering-8250df" alt="Focus: Memory Engineering"></a>
  <a href="#systems-under-study"><img src="https://img.shields.io/badge/Systems-10-0969da" alt="Systems"></a>
  <a href="#sections"><img src="https://img.shields.io/badge/Sections-10-2da44e" alt="Sections"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-d29922" alt="License"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.zh-CN.md">简体中文</a>
</p>

Production agent memory is not a vector database. It is a system that keeps raw evidence and derives queryable memory views from it.

Most agents ship the same minimum memory loop: a file store, an index, recall at turn start, extraction at run end, and a raw session log.
That loop serves one user and one agent. Production memory serves many tenants, many agents, and years of history.
At that scale, memory is a subsystem of its own, with its own lifecycle, its own clocks, and its own failure modes.
This repo scales that loop into a full memory subsystem, one section per design decision.

**Contents:** [Pipeline](#the-memory-pipeline) · [Method](#how-to-learn) ·
[Systems](#systems-under-study) · [Sections](#sections) · [Structure](#repository-structure) · [Running](#running-the-checks)

---

## The Memory Pipeline

![Production memory pipeline](assets/production-memory.png)

One rule carries the whole design:

> Raw events are never dropped. Derived memories can always be rebuilt.

Everything after capture is a view: a queryable copy computed from the events, never the only copy.
If extraction, consolidation, or an index goes wrong, the system rebuilds it from the event log.
[MemMachine](https://arxiv.org/abs/2604.04853) takes the same position: keep full episodes as ground truth, then layer profiles, indexes, and contextual retrieval on top.

### Three clocks

The same system runs on three schedules:

|                      | Hot path                                 | Warm path                                        | Cold path                                                        |
| -------------------- | ---------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------- |
| **When**       | every query                              | run end                                          | background or scheduled                                          |
| **Work**       | plan, retrieve, rerank, assemble, inject | append raw event, write gate, extract candidates | consolidate, dedupe, supersede, build profile, reindex, evaluate |
| **Constraint** | low latency, strict token budget         | one extra model call, no long block              | can be slow, must be safe                                        |

The minimum loop already has this shape: recall before the turn, extraction at run end, consolidation in the background.
This track keeps the same three clocks and scales each one.

---

## How to learn

Every section is self-contained and uses the same four-part lens:

1. **Opening.** What problem this stage solves.
2. **Mechanism.** The moving parts and how data moves.
3. **Per system.** How real systems implement it, in one table.
4. **Failure modes.** What breaks and how to mitigate it.

To learn from this repo:

- **Read the sections in order. Each builds on the stage before it**.
- Run a section's offline checks: `python sections/NN-name/src/test.py`. No key needed.
- Diff a section's `src/` against the section before it. The diff is the one mechanism that section adds.

---

## Systems Under Study

Each system is a worked example in the per-system table of the sections listed.

| System                   | Why people use it                                                              | Read it for                                | Sections |
| ------------------------ | ------------------------------------------------------------------------------ | ------------------------------------------ | -------- |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code/memory)** | Frontier coding agent. Its auto memory keeps markdown files per project directory. | Scoped stores, background consolidation | 1, 5 |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Long-term assistant: remembers you, learns workflows, runs anywhere. | Raw session log, approval-gated writes | 1, 2, 3 |
| **[MemMachine](https://arxiv.org/abs/2604.04853)** | Open memory layer that keeps full conversation episodes as ground truth. | Episode ledger, contextual retrieval | 2, 8 |
| **[Mem0](https://arxiv.org/abs/2504.19413)** | Widely used memory layer with a curated store: new facts merge in, not pile on. | The LLM write gate | 3 |
| **[LangMem](https://github.com/langchain-ai/langmem)** | LangChain&#39;s memory SDK: records validate against app schemas at write time. | Typed records and profiles | 4 |
| **[Hindsight](https://arxiv.org/html/2512.12818v1)** | Memory engine that keeps facts, observations, and opinions apart. | Epistemic types, reflection | 4 |
| **[Graphiti / Zep](https://arxiv.org/abs/2501.13956)** | Temporal knowledge graph memory: old facts get closed, not overwritten. | Bitemporal fields, `SUPERSEDE` | 5 |
| **[A-Mem](https://arxiv.org/html/2502.12110v1)** | Agentic memory: new notes link to and evolve existing ones. | Dynamic linking, agentic consolidation | 6 |
| **[Sleep-time Compute](https://arxiv.org/html/2504.13171v1)** | Moves consolidation off the query hot path into background time. | Cold-path consolidation | 6 |
| **[AgentRunbook-C](https://arxiv.org/abs/2605.12493)** | Stores trajectories as files and lets a coding agent search them in a sandbox. | Agentic file retrieval | 8 |

> Sections 7 to 10 compare design patterns (wiki vs graph views, retrieval strategies, assembly policies, metrics) rather than single systems.

---

## Sections

Ten sections, one design decision each. Each row links to one self-contained writeup with runnable code.

| #  | Section                                              | Question                              | Key mechanisms                                             |
| -- | ---------------------------------------------------- | ------------------------------------- | ---------------------------------------------------------- |
|    | **Extraction** | | |
| 1  | [Memory contract](sections/01-memory-contract/)               | Whose memory is this?                 | Scope, tenant and user isolation, retention, sensitivity   |
| 2  | [Event ledger](sections/02-event-ledger/)                     | What counts as evidence?              | Append-only log, `occurred_at` vs `recorded_at`            |
| 3  | [Write policy](sections/03-write-policy/)                     | Is this worth remembering?            | Novelty, durability, explicit write decisions              |
| 4  | [Typed memory](sections/04-typed-memory/)                     | What kind of memory is it?            | Episodic, semantic, procedural, epistemic types            |
|    | **Consolidation** | | |
| 5  | [Temporal resolution](sections/05-temporal-resolution/)       | Conflict, or an update?               | Bitemporal fields, `SUPERSEDE`, non-destructive operations |
| 6  | [Consolidation](sections/06-consolidation/)                   | How do events become knowledge?       | Compression, abstraction, propose-validate-commit          |
| 7  | [Index views](sections/07-index-views/)                       | How is one ledger queried many ways?  | Sparse, dense, temporal, graph, wiki, profile views        |
|    | **Recall** | | |
| 8  | [Hybrid retrieval](sections/08-hybrid-retrieval/)             | How is the right memory found?        | BM25 plus vector plus graph, source expansion, routing     |
| 9  | [Context assembly](sections/09-context-assembly/)             | How is memory injected safely?        | Evidence bundles, token budget, untrusted-data framing     |
| 10 | [Evaluation and governance](sections/10-evaluation-governance/) | Did memory actually help?           | Write, retrieval, context, and end-to-end metrics          |

---

## Repository Structure

```text
learn-agent-memory/
├── README.md                      # track map
├── sections/                      # one folder per section
│   ├── 01-memory-contract/        # README.md per section, runnable chain starts here
│   ├── ...
│   └── 10-evaluation-governance/  # holds the whole engine
└── assets/                        # shared images
```

Each section folder is `NN-name/` and contains `README.md`, `README.zh-TW.md`, and `README.zh-CN.md`, plus a runnable `src/`.
Each section carries the prior section's `src/` forward and adds one mechanism,
so the diff between two adjacent sections is that section's mechanism, and section 10 holds the whole engine.

---

## Running the Checks

Everything is stdlib Python (dataclasses, sqlite3). No third-party dependencies, no API key, no setup.

Each section has `test.py` with offline checks. Run from the repo root:

```bash
python sections/01-memory-contract/src/test.py
```

---

## Contributing

- **Add a system.** Slot a new memory system into a section's per-system table.
- **Deepen a section.** Add a mechanism, clearer diagram, or sharper failure mode.
- **Correct the record.** These pages are educational reconstructions from papers and docs. Sourced corrections are welcome.

Favor named, verifiable mechanisms over speculation. Cite sources.

---

## References

- [MemMachine](https://arxiv.org/abs/2604.04853): ground-truth-preserving memory, contextual retrieval over full episodes.
- [Zep / Graphiti](https://arxiv.org/abs/2501.13956): temporal knowledge graph for agent memory, bitemporal facts.
- [Hindsight](https://arxiv.org/html/2512.12818v1): retain, recall, reflect. Epistemic split between facts, observations, and opinions.
- [A-Mem](https://arxiv.org/html/2502.12110v1): agentic memory, new notes link to and evolve existing ones.
- [Memory-R1](https://arxiv.org/html/2508.19828v2): RL-trained memory manager over `ADD / UPDATE / DELETE / NOOP`.
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1): background consolidation off the query hot path.
- [Karpathy&#39;s LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): idea file. An LLM-maintained markdown wiki compiled over immutable sources.
- [HippoRAG](https://arxiv.org/abs/2405.14831): knowledge graph plus Personalized PageRank, multi-hop evidence in one retrieval step.
- [LongMemEval](https://arxiv.org/abs/2410.10813): long-term interactive memory benchmark, five task families.
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493): agent-experience benchmark, AgentRunbook-C agentic file retrieval.
