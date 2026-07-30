# 7 · Hybrid retrieval

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 一个排序器永远不够。让几条轻量的 candidate 通道同时跑，再把它们的排名融合起来。

这一章讲的是 [Production memory](../README.zh-CN.md) track lifecycle 的阶段 8（Retrieve）：
从一堆 memory 里，找出这一轮真正需要的那几笔。

通道（channel）指一种找 candidate 的方法：embedding 相似度、关键字、最近优先，各算一条通道。
只靠一条，一定有盲点：
embedding 抓不到精确的名字、日期和否定句；同一件事换个说法，关键字就搜不到；只看最近的，又旧又相关的事实就漏掉。
丢给 memory 的问题什么样子都有：「我们的 CI 是哪家」（名字）、「上周决定了什么」（时间）、
「以前部署出过什么问题」（模式）。没有一条通道三种都答得了。

[第 9 章](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)已经有两种查法：LLM selector 读 index 挑 memory，
关键字搜原始历史。这个阶段把它推广成一条 pipeline：

1. 几条轻量的 candidate 通道并行跑。
2. 把它们的排名融合起来，不用调跨通道的权重。
3. 要能降级：一条通道坏掉，结果不能跟着变空。
4. 要分得清：通道跑完但一笔都没对上，跟通道坏掉，是两回事。
5. index 要一直可以重建：它是 view（阶段 7），从记录推导出来，记录才是本体。

---

## 机制

最简单的版本：两条通道，加一个排名融合器。

三个零件：

- **通道**：各自独立的 candidate 产生器。离线版有两条：关键字（FTS5，bm25）和最近优先。
  上线后，dense embedding、graph 遍历、时间过滤接的都是同一个接口：回一份排好序的 id 清单。
- **融合**：reciprocal rank fusion（RRF）。每条通道给它排到的每个条目加 `1 / (K + 名次)` 的分数。
- **index**：一张从带类型的记录推导出来的 FTS 表，随时可以砍掉重建。

加一条新通道之所以省事，靠的是 RRF：
各通道的原始分数不能直接比，但名次可以，
所以既不用做分数归一化，权重也不用调：

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

被两条通道排进来的 id，分数会赢过只被一条排到的。hybrid 的效果就这一句：
独立信号之间有共识，本身就是相关性的证据。

两条通道共用同一张推导出来的表：

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

track README 那条完整 pipeline，对到这副骨架长这样：

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

有两步只有上线版才有，但重要到必须点名：

| | Contextual expansion | Agentic retrieval |
| --- | --- | --- |
| **做法** | 命中之后，回 event ledger（阶段 2）把那笔前后的 event 一起捞出来。 | 把执行轨迹存成文件，让 coding agent 在 sandbox 里自己搜。 |
| **证据** | MemMachine：把检索挖深、把来源展开，常常赢过更聪明的 ingestion 切块。 | AgentRunbook-C 在 LongMemEval-V2 上赢过 RAG 基线。 |
| **代价** | 注入的 token 变多。 | 慢。有些问题得把整批文件翻一遍，一次最近邻查询解决不了。 |

跟前后阶段怎么接：index 从带类型的记录（阶段 4）推导出来，记录在写入时就打好了 scope，
所以 scope 过滤在通道开跑之前就做完了。index 的维护留在写入路径（阶段 7 的拆法），查询只读不写。
index 坏了，把 ledger 重放一次就回来（阶段 2 的保证）。
融合出来的命中会送去 context assembly（阶段 9）。
每笔命中连同它的类型、分数、记录时间和来源 event id 一起送过去，不是只送一段文字。

### What Changed

跟第 9 章比：那时候的 recall 一次只走一条路，走哪条看是谁触发的。
现在通道一起跑，结果不一致也没关系；要加新通道，就是多接一份排好序的清单，不用重新设计。

---

## 各系统做法

| | Graphiti | MemMachine |
| --- | --- | --- |
| **Pros** | 关系和时间的问题一次搜索就解决，不用调用 LLM。 | 命中的那笔会展开成它所在的 episode，答案跟上下文一起送到。 |
| **Cons** | 要做实体抽取，graph 存储还得跟数据保持同步。 | 展开会让注入的 token 变多。完整的 episode 必须一直留着。 |
| **Why** | 「谁对谁做了什么、什么时候」要靠 graph 的边来答，最近邻算不出来。 | 答案通常散在命中点的前后，不会刚好塞在同一块里。 |
| **How: 通道** | semantic embedding、关键字、graph 遍历，融合起来。 | profile 查表，加上对完整历史的 episode 搜索。 |
| **How: 时间** | 边上带时间，过滤出查询那一刻仍然有效的事实。 | episode 自带时间线，展开就顺着它走。 |
| **How: 重排** | 把并行搜索的结果做融合排名。 | 调的是深度和排版，不在 ingestion 的切块上较劲。 |

---

## 哪里会出错

- **一条通道独大：**回一大串结果的通道会淹掉融合结果。
  RRF 只看名次不看分数，每条通道的影响力天生有上限；再把每条通道的 `k` 设小一点，而且每条都一样。
- **哪条通道都找不到：**事实明明存在，却没有信号对得上。
  解法是加一条通道（时间、实体），不是硬调现有的；融合接口就是为这件事设计的。
- **index 跟记录渐渐对不上：**删除或 schema 改动留下幽灵数据。
  有疑虑就从记录重建；它是 view，重建的成本本来就设计得很小。
- **k 一大精度就崩：**candidate 越多，下游噪声越多。
  融合时用小 k，最后一刀交给 assembly 的 budget（阶段 9）去切。
- **延迟越积越多：**通道要真的并行跑才算并行。
  每条通道就保持一次走 index 的查询，慢的工作（展开、agentic 搜索）放在路由决策后面才跑。

---

## 可执行程序

[`src/`](src/) 承接 06 的代码，这次加入：

- [`retrieve.py`](src/retrieve.py)：RRF 的 `fuse`，和把阶段 7 那几条通道融合起来的 `retrieve`。
  index 本身放在 [6 · Index views](../06-index-views/README.zh-CN.md)。
- [`engine.py`](src/engine.py)：写入路径改完记录就 reindex 该 scope，
  `retrieve()` 只读不写，在建好的 view 上跑通道融合。
- [`test.py`](src/test.py)：验每条通道各自的行为、融合偏向跨通道共识、没对上就回空、
  一条通道坏掉照样降级、砍掉重建后结果一模一样，以及通过 engine 的 scope 隔离检索。

```bash
python tracks/production-memory/07-hybrid-retrieval/src/test.py   # offline checks, no key
```

离线版的通道是关键字和最近优先。上线后，dense 和 graph 通道接进同一个 `fuse`。

---

## 来源

- [Graphiti / Zep](https://arxiv.org/abs/2501.13956)：增量式的时间知识图谱，融合搜索不调用 LLM。
- [MemMachine](https://arxiv.org/abs/2604.04853)：情境式检索，靠深度和来源展开取胜，不靠 ingestion 的切块技巧。
- [HippoRAG](https://arxiv.org/abs/2405.14831)：答案要串好几笔 memory 才拼得出来的问题，用 Personalized PageRank 一步收齐。
- [LongMemEval-V2 / AgentRunbook-C](https://arxiv.org/abs/2605.12493)：让 agent 自己翻文件，当一条慢但挖得深的通道。
- [Production memory track](../README.zh-CN.md)：这个阶段所属的完整 lifecycle。
