<h1 align="center" style="margin-top: 0;">Learn Agent Memory</h1>

<p align="center">
  <strong>了解 production 的 agent 是怎么记住东西的</strong><br>
</p>

<p align="center">
  <a href="#各章节"><img src="https://img.shields.io/badge/Focus-Memory_Engineering-8250df" alt="Focus: Memory Engineering"></a>
  <a href="#研究的系统"><img src="https://img.shields.io/badge/Systems-10-0969da" alt="Systems"></a>
  <a href="#各章节"><img src="https://img.shields.io/badge/Sections-10-2da44e" alt="Sections"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-d29922" alt="License"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-TW.md">繁體中文</a> · <strong>简体中文</strong>
</p>

Production 的 agent memory 不是一个向量数据库，而是一套系统：原始证据完整保留，查询用的 memory 都从证据整理出来，随时可以重建。

多数 agent 都跑同一套最小可行的 memory loop：一个文件存储区、一份索引、turn 开始时 recall、
run 结束时 extraction，再加一份原始 session log。只管一个用户、一个 agent，那个 loop 就够了。
但 production 的 memory 要面对多个 tenant、多个 agent，还有累积好几年的历史。
到了这个规模，memory 本身就是一个子系统，有自己的 lifecycle、自己的时钟、自己的失效模式。
这个 repo 把那个 loop 一章一章放大成完整的 memory 子系统，每一章对应一个设计决策。

**目录：** [Memory pipeline](#memory-pipeline) · [学习方法](#学习方法) ·
[研究的系统](#研究的系统) · [各章节](#各章节) · [文件结构](#文件结构) · [运行检查](#运行检查)

---

## Memory pipeline

![Production memory pipeline](assets/production-memory.png)

整个设计最重要的一条规则：

> 原始事件不可丢。整理出来的 memory 随时可以重建。

Capture 之后的每一层都是 view：从事件算出来、给查询用的一份副本，永远不是唯一的一份。
extraction、consolidation 或索引坏掉都没关系，从 event log 重新算一次就好。
[MemMachine](https://arxiv.org/abs/2604.04853) 也是同样的立场：
完整的 episode 留着当 ground truth，profile、索引和 contextual retrieval 都叠在上面。

### 三条时钟

同一套系统其实跑在三种节奏上：查询当下走 hot path，一次 run 结束走 warm path，后台整理走 cold path。

| | Hot path | Warm path | Cold path |
| --- | --- | --- | --- |
| **时机** | 每个 query | run 结束时 | 后台或调度 |
| **工作** | plan、retrieve、rerank、assemble、inject | 写入原始事件、write gate、抽出 memory candidate | consolidate、去重、supersede、更新 profile、重建索引、评估 |
| **限制** | 低 latency，严格的 token budget | 可以多一次 model call，但不能挡太久 | 可以慢，但必须安全 |

最小可行的 loop 已经有这个雏形：turn 前 recall、run 结束 extraction、后台 consolidation。
这条 track 沿用同样的三条时钟，把每一条放大。

---

## 学习方法

每一章都可独立阅读，都用同一组四个面向来看：

1. **开场：** 这一章要解决什么问题。
2. **机制：** 有哪些组件，数据怎么流动。
3. **各系统做法：** 真实系统是怎么实现的，整理成一张表。
4. **哪里会出错：** 常见的出错情况，以及怎么缓解。

怎么从这个 repo 学习：

- **按顺序读各章节。每一章都建立在前一章之上**。
- 每一章都能跑离线检查：`python sections/NN-name/src/test.py`，不需要密钥。
- 把某章的 `src/` 跟前一章对比（diff），这个差异就是这一章新增的那个机制。

---

## 研究的系统

每个系统都是所列章节 per-system 表格里的实现示例。

| 系统 | 大家为什么用它 | 值得看的地方 | 覆盖章节 |
| --- | --- | --- | --- |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code/memory)** | 目前最强的 coding agent，auto memory 以 project 目录为单位存 markdown 文件。 | Scoped store、后台 consolidation | 1、5 |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | 长期助理：记得你、学会你的工作流程，还能跨平台跑任务。 | 原始 session log、需要批准的写入 | 1、2、3 |
| **[MemMachine](https://arxiv.org/abs/2604.04853)** | 开源的 memory 层，完整的对话 episode 留着当 ground truth。 | Episode ledger、contextual retrieval | 2、8 |
| **[Mem0](https://arxiv.org/abs/2504.19413)** | 被广泛使用的 memory 层，store 走精选路线：新事实并进去，不是一直堆。 | LLM write gate | 3 |
| **[LangMem](https://github.com/langchain-ai/langmem)** | LangChain 的 memory SDK，record 写入时会过 app schema 检查。 | Typed record 和 profile | 4 |
| **[Hindsight](https://arxiv.org/html/2512.12818v1)** | 把事实、观察和意见分开存的 memory engine。 | Epistemic type、reflection | 4 |
| **[Graphiti / Zep](https://arxiv.org/abs/2501.13956)** | temporal knowledge graph memory：旧事实标成过期留着，不会被盖掉。 | Bitemporal 字段、`SUPERSEDE` | 5 |
| **[A-Mem](https://arxiv.org/html/2502.12110v1)** | agentic memory：新 note 会自己链接并更新旧 note。 | Dynamic linking、agentic consolidation | 6 |
| **[Sleep-time Compute](https://arxiv.org/html/2504.13171v1)** | 把 consolidation 移出查询的 hot path，改在后台做。 | Cold path 的 consolidation | 6 |
| **[AgentRunbook-C](https://arxiv.org/abs/2605.12493)** | 把 trajectory 存成文件，让 coding agent 在 sandbox 里自己搜索。 | Agentic file retrieval | 8 |

> 第 7 到 10 章比较的是设计模式（wiki 对 graph view、retrieval 策略、assembly 政策、指标），不是单一系统。

---

## 各章节

十章，每一章都是一个独立的设计决策。每一行都连到一篇可独立阅读、附可执行代码的说明。

| #  | 章节                                                                     | 问题                             | 关键机制                                                   |
| -- | ------------------------------------------------------------------------ | -------------------------------- | ---------------------------------------------------------- |
|    | **Extraction** | | |
| 1  | [Memory contract](sections/01-memory-contract/README.zh-CN.md)                     | 这是谁的 memory？                | Scope, tenant and user isolation, retention, sensitivity   |
| 2  | [Event ledger](sections/02-event-ledger/README.zh-CN.md)                           | 什么算是证据？                   | Append-only log, `occurred_at` vs `recorded_at`            |
| 3  | [Write policy](sections/03-write-policy/README.zh-CN.md)                           | 值不值得记？                     | Novelty, durability, explicit write decisions              |
| 4  | [Typed memory](sections/04-typed-memory/README.zh-CN.md)                           | 这是哪一种 memory？              | Episodic, semantic, procedural, epistemic types            |
|    | **Consolidation** | | |
| 5  | [Temporal resolution](sections/05-temporal-resolution/README.zh-CN.md)             | 是矛盾，还是更新？               | Bitemporal fields, `SUPERSEDE`, non-destructive operations |
| 6  | [Consolidation](sections/06-consolidation/README.zh-CN.md)                         | 事件怎么变成知识？               | Compression, abstraction, propose-validate-commit          |
| 7  | [Index views](sections/07-index-views/README.zh-CN.md)                             | 一份 ledger 怎么支撑多种查法？   | Sparse, dense, temporal, graph, wiki, profile views        |
|    | **Recall** | | |
| 8  | [Hybrid retrieval](sections/08-hybrid-retrieval/README.zh-CN.md)                   | 怎么找到对的 memory？            | BM25 plus vector plus graph, source expansion, routing     |
| 9  | [Context assembly](sections/09-context-assembly/README.zh-CN.md)                   | memory 怎么安全放回 context？    | Evidence bundles, token budget, untrusted-data framing     |
| 10 | [Evaluation and governance](sections/10-evaluation-governance/README.zh-CN.md)     | memory 真的帮上忙了吗？          | Write, retrieval, context, and end-to-end metrics          |

---

## 文件结构

```text
learn-agent-memory/
├── README.md                      # 最上层地图
├── sections/                      # 每个章节一个文件夹
│   ├── 01-memory-contract/        # 每章一份 README.md，可执行的代码链从这里开始
│   ├── ...
│   └── 10-evaluation-governance/  # 完整的 engine 在这里
└── assets/                        # 共用图片
```

每个章节文件夹都是 `NN-name/` 格式，里面有 `README.md`、`README.zh-TW.md`、`README.zh-CN.md`，还有可执行的 `src/`。
每一章都把前一章的 `src/` 带过来，再加上一个新机制，
所以相邻两章的 diff 就是那一章的机制，第 10 章就是完整的 engine。

---

## 运行检查

所有代码都是 stdlib Python（dataclasses、sqlite3）。没有第三方依赖，不需要 API key，也不用安装。

每一章都有 `test.py` 做离线检查。从 repo 根目录运行：

```bash
python sections/01-memory-contract/src/test.py
```

---

## 参与贡献

- **新增一个系统。** 把新的 memory 系统放进某一章的 per-system 表格里。
- **深化某一章。** 补上一个机制、更清楚的图，或更精准的出错分析。
- **修正内容。** 这些页面都是从论文和文档重建出来的教学内容。欢迎附上出处的修正。

请优先采用有名字、可查证的机制，而不是臆测。记得引用出处。

---

## 参考资料

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
