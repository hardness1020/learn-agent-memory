# 6 · Consolidation

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 模型可以决定哪些东西该合并，但永远不能决定哪些东西该消失。

这一章是 [Production memory](../../README.zh-CN.md) track 的第六章：
lifecycle 的 Consolidate，把 event 变成知识。

Consolidation 不是把东西摘要一下就好。
它要做的事包括：合并重复、认出同一个实体、找出矛盾、把过期的事实标掉、把相关的事实并成一笔、
从多次经历归纳出偏好、从成功的执行整理出 workflow、更新 confidence，还有淘汰没用的 memory。
第 5 章只处理了其中很窄的一块（新主张关掉旧主张），剩下的都在这一章，
而且这是第一次由模型决定已经存在的 memory 该怎么处置。

难就难在这里。在这之前，每一章都只会新增记录。
consolidation 这一趟会关掉记录，而提议要关掉什么的是模型：它可能出错，可能被 prompt injection，
也可能单纯搞混两笔很像的 memory 里哪一笔才重要。

---

## 机制

最简单的版本：proposer 负责提，validator 负责准，存储区只会看到通过验证的操作。

```text
proposer (model or rule)
    ↓  Proposal
check()   level, kind, content, source count, sources exist,
          sources still active, one scope, one claim key
    ↓
apply()   write the derived record, then close the sources it cited
    ↓
Operation list  →  ledger
```

consolidation 分三个层级，一层比一层离原始输入更远：

```text
compression   many similar memories  →  one shorter memory
abstraction   many episodes          →  one rule or preference
skill         many successful runs   →  one reusable procedure
```

compression 只是改写，来源没讲过的东西，它讲不出来。
abstraction 是归纳，会从个案跳到通则，所以它会犯 compression 不会犯的错：
五次经历讲的都是同一个测试不稳，归纳出来却变成「整套测试都不能信」。
skill 价值最高，也最容易出事：三次执行都成功可能只是运气好，
但整理出来的流程不会记得这件事，第四次照样被拿出来用。

一份 Proposal 只讲两件事：要写什么新内容，这笔新内容要换掉哪几笔旧记录。
它指定不了操作，所以模型想收掉一笔记录，只能提出新内容来换，
光删不写的 Proposal 根本写不出来：

```python
@dataclass(frozen=True)
class Proposal:
    level: str
    content: str
    source_ids: tuple
    kind: str = "semantic"
    reason: str = ""
```

validator 就是图里的 `check`，挡在 Proposal 和存储区之间。
它是确定性的，退回的理由一共八种：level 不认识、kind 不认识、内容是空的、来源少于两笔、
引用了不存在的记录、引用了已经关掉的记录、来源跨了不只一个 scope，还有来源带着不同的 claim key。
凡是建记录时会被退回的问题，这里也先检查一遍，因为 `apply` 会动到存储区：
Proposal 套用到一半失败，记录和 ledger 就对不上了，而这一趟本来就该救得回来。
被退回的 Proposal 会报告出去，不会被吞掉，因为第 10 章要靠这些数字算 consolidation 的精确度。

还有一条保证：旧记录被换掉，它的证据不会跟着不见。
每笔记录都带着 source event id，记着自己是从哪几个 event 来的。
`apply` 只会关掉 Proposal 引用的那几笔，
衍生的新记录则把它们的 source event id 全部接收过来，一笔都不少。
这不用靠检查，照这个做法走，本来就漏不掉：

```python
events = tuple(sorted({e for r in sources for e in r.source_event_ids}))
derived = make_record(sources[0].scope, proposal.kind, "inference",
                      proposal.content, events,
                      min(r.confidence for r in sources),
                      tags=("consolidated", proposal.level),
                      claim_key=one_key(sources),      # 不带的话 profile view 会变空
                      valid_from=at)                   # 不填的话它会变成「一直都成立」
```

这段调用有三个地方特别重要。知识状态填 `inference`，不是 `fact`：
合并出来的内容是系统自己推的结论，不是谁观察到的，
第 4 章那条「推论不会偷偷变成事实」的规则在这里照样算数。
confidence 取来源里的最小值，不是平均：把两笔合起来，不会让其中任何一笔变得更确定。
event id 则一路指回 ledger 里的原始证据，所以每笔衍生记录都追得回它来自哪些观察。

`consolidate` 跑一趟，通过和退回两边都报告：

```python
return {"proposed": len(proposals), "applied": len(proposals) - len(rejected),
        "rejected": rejected, "operations": ops}
```

数据怎么流：engine 捞出这个 scope 生效中的记录，交给 proposer，把回来的 Proposal 验一遍，
通过的才套用，最后把每个操作和每笔被退回的 Proposal 都写进 ledger。这一趟不跑在用户的那一轮里。
回答眼前这一轮，用不到这一趟的任何一步，所以它可以定时跑，或趁 session 之间的空闲跑。
没有人在等它，多花几秒调用一次模型也无所谓（[Sleep-time Compute](https://arxiv.org/html/2504.13171v1) 讲的就是这个道理）。

### 整合时发现的两个 bug

这两个 bug 都出在更早的章节，但要等这一章跑起来、跟前面串在一起，才看得出来。
照这条 track 的惯例，修正落在这一章带着走的那份 code，
前面的文件夹保留当时的版本，对照着看就知道整合改了什么。

**Bug 1：`DEFER` 没有地方可去。**
write gate 会把跟现有 memory 很像的 candidate 延后处理，
理由写着「已经有相似的 memory，交给 consolidation 合并」。
但被延后的 candidate 根本没存下来，consolidation 也就永远看不到它：这一章合并得动的每一组，gate 早就先丢掉了。
修法在 engine 的写入路径：被延后的 candidate 照样存进存储区，打上 `deferred` 标记，
merge band（gate 交给 consolidation 处理的那段相似度区间）才真的走得到。

```python
if decision.action in (STORE, DEFER):
    # DEFER 的意思是「交给 consolidation 合并」，
    # 所以 candidate 得活到第 6 章看得到它
    record = make_record(scope, candidate.kind, epistemic_type,
                         candidate.content, candidate.source_event_ids,
                         decision.confidence, claim_key=candidate.claim_key,
                         tags=("deferred",) if decision.action == DEFER else ())
```

`DEFER` 如果没有一个真的能延后过去的地方，就只是默默丢东西的 `IGNORE`，理由比较好听而已。

**Bug 2：两边对「像不像」各算各的。**光是存下来还不够。
gate 判断像不像用一种算法，这一章分组的时候用的是另一套。
同一对记录，gate 算出来够像，所以把 candidate 延后过来；
这一章再算一次却不够像，达不到合并的门槛。
结果 gate 交过来的东西全部躺在存储区里，一笔都没被合并过。
修法是这一章直接用 gate 的函数和门槛，两边量出来的永远是同一个数字：

```python
from policy import SIMILAR_AT, resembles

MERGE_AT = SIMILAR_AT          # gate 用多少延后，这里就用多少合并

# propose_compression 分组时：
resembles(record.content, r.content) >= MERGE_AT
```

两章要交接，得先对「像不像怎么算」有共识，不然一边说够像，另一边永远算出不够像。

### What Changed

跟第 5 章比：`consolidate()` 是 contract 的第三个动词，现在有实现了。
`observe()` 和 `consolidate()` 都能离线跑完整条路；`recall()` 要等第 8 章。

---

## 各系统做法

| | A-Mem | Sleep-time Compute |
| --- | --- | --- |
| **Pros** | 新知识马上就能用。靠 link 就找得到相关的笔记，不用架 graph store。 | consolidation 的成本永远不落在用户那一轮。没有人在等，再重的处理也付得起。 |
| **Cons** | 每次写入都要付整理的成本。一条连错的 link 会扩散，后面的笔记会接到它上面。 | 两趟之间知识是旧的。现在做的更正，下一趟才生效。 |
| **Why** | memory 应该边长大边自己重新整理，像 zettelkasten 的笔记那样。 | 回答这一轮用不到的东西，就不该在这一轮算。 |
| **How: 触发** | 写入时。新笔记会连到它碰到的旧笔记，并顺手修订它们。 | 定时跑，或利用 session 之间的空闲时间。 |
| **How: 产出** | 互相连结的笔记，旧的会更新成跟新的一致。 | 预先算好的衍生 context，问题还没来就准备好了。 |
| **How: 成本** | 每次写入都付，付在交互延迟里。 | 批量付，付在查询路径外面。 |

---

## 哪里会出错

- **模型把唯一一份删掉：**合并时关掉来源却没把内容带走，证据就没了。
  衍生记录引用来源 event id 的并集，ledger 里的原始 event 也一直都在，
  所以就算一趟跑砸了，重放一次就能救回来。
- **例外被合并掉：**四笔说部署没问题，一笔说星期五会失败。
  compression 留下多数的说法，星期五那件事就消失了。合并前要看讲的是不是同一件事，不能只看字面像不像。
- **consolidation 跑在热路径上：**recall 中间插一次模型调用，每一轮都多好几秒。
  这一趟要么定时跑，要么在 session 之间跑，就是不能塞进用户的那一轮。
- **衍生记录被当成事实：**合并出来的内容是推论，不是观察。
  盖成 `fact`，retrieval 的时候一个猜测就会压过它当初依据的那些观察。
- **一直合并会漂掉：**合并过的东西再合并，每次的小改写叠起来，最后 memory 跟任何一笔证据都对不上。
  每一趟只拿生效中的记录当输入，对一个已经稳定的存储区跑第二趟，不会提出任何 Proposal。
- **被退回的 Proposal 没人看得到：**validator 把烂 Proposal 默默丢掉，等于把坏掉的 proposer 藏起来。
  被退回的 Proposal 会连理由一起报告，并写进 ledger。
- **跨 scope 合并：**两个租户把同一个偏好写成一模一样的句子，一次合并就把两边并在一起。
  来源跨了不只一个 scope 的 Proposal，validator 直接退回。

---

## 可执行程序

[`src/`](src/) 承接 04 并加入：

- [`consolidate.py`](src/consolidate.py)：三个层级、`Proposal`、`check` validator、
  `apply`、`consolidate` 这一趟，还有确定性的 `propose_compression`。
- [`engine.py`](src/engine.py)：`consolidate()` 实现了 contract 的第三个动词，
  被延后的 candidate 现在会存下来，merge band 才走得到。
- [`test.py`](src/test.py)：validator 该退的八种理由、衍生记录的出处与 confidence、
  proposer 把重复的分成一组而放过单笔的、被退回的 Proposal 有报告出来，
  以及完整跑一趟：两笔 memory 合并了，原始 event 完全没动。

```bash
python sections/06-consolidation/src/test.py   # offline checks, no key
```

模型要接进来，就是从 proposer 这个位置接。默认的 proposer 是确定性的，所以这一章没有 `demo.py`。

---

## 来源

- [A-Mem](https://arxiv.org/html/2502.12110v1)：agentic organization，新笔记会连到旧笔记，并让它们跟着演化。
- [Sleep-time Compute](https://arxiv.org/html/2504.13171v1)：把 consolidation 的工作移出查询热路径。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用学出来的策略决定什么时候存、改、忘。
- [Production memory track](../../README.zh-CN.md)：这一章所属的完整 lifecycle。
