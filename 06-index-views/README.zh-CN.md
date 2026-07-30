# 6 · Index views

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> index 不是 memory 本身。它只是一种 view，view 坏了就重建，不用修。

这一章讲 [Production memory](../README.zh-CN.md) track 的阶段 7（Index）。
view 指的是从记录整理出来的读取结构，搜索用的 index、看当下状态的 profile、
互相链接的 wiki 页面都是。每一种都从同一份 ledger 衍生出来。

走到这个阶段，一笔记录已经有类型、有时间轴、也有合并的历史，但它终究只是一行数据。
不同的问题，要的答案长得完全不一样。
「关于部署我知道什么」要的是排好序的搜索结果，「上周改了什么」要的是一段时间区间，
「这个用户是谁」根本不用搜索，直接要当下状态，「这些事实怎么串起来」要的是互相链接的页面。

每一种答案都需要自己的数据结构。硬逼所有问题走同一个 index，系统会同时变慢又变错。

---

## 机制

最简单的版本：写入只有一条路，读取的 view 有好几种，正本永远只有 ledger 那一份。

```text
raw event log    SQLite, append only, never rebuilt
  → records      typed, resolved, consolidated (stages 4 to 6)
    → sparse     FTS5 / bm25            exact names and dates
    → temporal   recorded_at ranges     what changed in a window
    → profile    one line per claim     current state, no search
```

实际上线的系统还会加 dense view（embedding）、wiki、entity graph，和放 skill、runbook 的 procedure view。
这张清单的重点不在长度，在方向：每个箭头都从 ledger 往外指，没有箭头指回去。
这个单向流动就是这个阶段要守的规则。index 坏掉不算事故，重建就好：

```python
def rebuild(self, scope, records) -> int:
    con.execute(f"DELETE FROM memory_index WHERE {_SCOPE}", _args(scope))
    con.executemany("INSERT INTO memory_index VALUES (?, ?, ?, ?, ?, ?)", ...)
```

rebuild 先删再插，scope 字段决定删的范围。
表上要是没有这个字段，给一个租户重建，就会先把整张表清空，再插回自己那一份，其他租户的数据就没了。
阶段 8 起每次写入跑完都会重建一次，所以这不是偶发事故：谁最后写，表里就只剩谁的数据。
其他租户查到的是空结果，不是报错，没有人会发现。
所以这张表的每个读取和重建都要带 scope，跟 ledger 是同一条规则。

schema 要改、index 坏掉、tokenizer 换了，三件事的解法都是同一个：再调用一次 `rebuild`。
没有东西需要修：index 里的每一行都能从记录重新算出来。

不是每种 view 都要存下来。搜索需要 inverted index，所以 sparse 和 temporal 存在 SQLite 里。
profile 读的时候现算，因为它很小，存起来只是多一个要跟着失效的东西：

```python
def profile(records) -> dict:
    """Current state, one entry per claim key, newest wins."""
```

profile 是阶段 5 的 claim key（一个事实更新时固定用的那个名字）第二次派上用场的地方。
没有 key 就没有投影的目标，「当下状态」就会退化成「最近那几笔 memory」。

数据的流向：记录进去，view 出来，engine 随时可以重建任何一种。
`reindex` 拿一个 scope 还生效的记录，把 sparse view 从头重写一次。
这个阶段的 src 只给出 `reindex` 这个动作，没有任何路径会自动调用它。
到了阶段 8，换写入路径来调用：任何改动记录的流程跑完就 reindex 自己的 scope，retrieve 只读表。
这就是上线系统的三路拆法。hot path 负责回答查询，从不重建。
warm path 跟着写入维护 index，这里的版本是每次写入就重建整个 scope。
上线的系统做增量：新记录插一行，被关掉的记录删一行，成本跟着改动的笔数走，不是整张表。
全量的 `rebuild` 留在 cold path，就是前面说的 schema 改、tokenizer 换、index 坏掉那三件事。
不管维护放在哪条路径，这张表都省不掉：bm25 的排序要靠 inverted index 才算得出来。
这里完全不调用模型，也不决定什么算相关。

### Other Views

wiki 和 graph 是最常被讨论的两种 view。

| | wiki view | graph view |
| --- | --- | --- |
| **长相** | 互相链接的 markdown 页面，一个主题一页。 | 实体节点，加上有类型的关系边。 |
| **成立的前提** | markdown 是正本，agent 和人都用普通文件工具改页面。 | 多跳、关联类的问题多到值得付抽取的成本。 |
| **链接怎么来** | 改页面的时候，顺手写进页面文字里。 | 一条抽取流程从每笔 event 建出边。 |
| **回答什么** | agentic research、人工审计、翻阅。 | 关联、时间、多跳的问题，一趟收齐。 |
| **怎么坏** | 没人写链接，每一页都是孤岛。 | 边连错，每次遍历都走偏。 |
| **代表系统** | [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)、Claude Code auto memory。 | [HippoRAG](https://arxiv.org/abs/2405.14831)、[Graphiti](https://arxiv.org/abs/2501.13956)。 |

这两个前提都不成立，所以这个阶段两种都没做。

### What Changed

和阶段 6 相比，建 index 和做检索分成两件事。
这个阶段建出返回 candidate 的各个通道，阶段 8 才决定怎么跨通道排序。
那是另一个问题、另一种错法，两件事混在一起，检索的 bug 就会变成 index 的 bug。

---

## 各系统做法

| | Claude Code auto memory | HippoRAG |
| --- | --- | --- |
| **Pros** | 什么都不用搭。人可以直接读存储的文件来审计，也能用文本编辑器改。 | 要跨好几跳的问题，一次检索就解决，不用来回好几轮。 |
| **Cons** | 搜索能力受限于文件工具，目录一大，找得回来的东西就变少。 | 要维护一条抽取流程和一张图。边连错，遍历就跟着走偏。 |
| **Why** | 用户看不到的 memory，就是用户改不了的 memory。 | 跨好几笔事实的答案，不该花好几轮检索。 |
| **How: 单位** | markdown 文件，加一个列出它们的 index 文件。 | 从段落建出来的实体节点和关系边。 |
| **How: 链接** | 页面文字里的 markdown link。 | 有类型的边，从问题里的实体出发跑 Personalized PageRank。 |
| **How: 重建** | 把文件重写一次，目录够小，整个重新生成也不费事。 | 整份语料重新抽取、重新建 index。 |

---

## 哪里会出错

- **index 变成正本：**某个东西只写进 index、没进 ledger，重建一次就悄悄不见。
  每个 view 都从记录衍生，记录又从 ledger 衍生。重建会丢数据，就是写入路径有 bug。
- **view 过期：**consolidation 已经把记录关掉，index 还搜得到，检索就会捞出不存在的 memory。
  任何改动记录的流程跑完就 reindex，不要靠定时。
- **每种 view 都存起来：**存五种 view 就有五个要失效的东西、五种互相对不上的方式。
  搜索需要的才存，小的读时现算。
- **所有租户共用一个 index：**没有 scope 字段的 view 会在查询时跨界，这比存储层泄露更糟，
  因为结果看起来就像「相关」，而且重建也变成破坏性操作：一个 scope 重建就清掉另一个的数据。
  scope 字段写在 schema 里，每个读取都照它过滤。
- **wiki 的链接没人管：**没人建链接，每一页都是孤岛，wiki 就跟直接列出记录没有差别；
  链接指到被关掉的记录，agent 点进去又是空的。
  要做这个 view，得先有东西负责生成链接，而且链接只指向还生效的记录。
- **建了一张没人问的图：**抽取和建 index 每笔 event 都要花钱，没人拿来问多跳问题的图，
  效果和 wiki 一样，成本却高很多。
- **拿 index 决定相关性：**通道只返回 candidate，不排最终答案。
  融合排序是阶段 8 的事，塞进这里，两个阶段都没办法分开测。

---

## 可执行程序

[`src/`](src/) 承接 05，加入：

- [`index.py`](src/index.py)：`MemoryIndex`，提供 `rebuild`、`keyword`、`recent`、`between`、`count`，
  每个都要带 scope，另外有读取时现算的 `profile`。
- [`engine.py`](src/engine.py)：`reindex` 从记录重建一个 scope 的 sparse view，
  `between` 读时间区间，`profile` 把同一批记录投影成另一种 view。
- [`test.py`](src/test.py)：关键字和时间区间查询、清空后重建而且一笔不少、
  profile 按 claim key 收敛并排除被取代的记录、
  一个 scope reindex 之后，另一个租户什么都查不到。

```bash
python tracks/production-memory/06-index-views/src/test.py   # offline checks, no key
```

这个阶段完全不调用模型，所以没有 `demo.py`。

---

## 出处

- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：
  从不可变的原始来源整理出来的 markdown wiki。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：在实体图上跑 Personalized PageRank 做多跳检索。
- [Graphiti](https://arxiv.org/abs/2501.13956)：增量更新的 temporal knowledge graph。
- [第 9 章 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)：那里的单一 index，这个阶段拆成多个通道。
- [Production memory track](../README.zh-CN.md)：这个阶段所在的完整 lifecycle。
