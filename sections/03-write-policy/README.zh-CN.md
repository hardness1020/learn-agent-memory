# 3 · Write policy

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 没有任何东西会「顺手」变成 memory。每一笔 candidate 都要有决定、有理由、有记录。

这一章是 [Production memory](../../README.zh-CN.md) track 的第三章：lifecycle 的 Write gate。
一笔原始证据要不要存成 memory，由这道 gate 做出明确的决定。
送进 gate 的东西叫 candidate：从新进的 event 整理出来、等着决定要不要存的一笔内容。

这道 gate 存太多、存太少都会出问题。什么都存，recall 捞回来的就是一堆噪音和过期数据，成本也跟着涨。
存得太少，agent 就会一直重问已经回答过的问题。
而且只要写入是静悄悄发生的，就没有人答得出「它为什么记得这个」、「它为什么忘了那个」。

迷你版的做法，是把一套类型规则放进 extractor 的 prompt：
只有四种类型、而且推导不回来的事实才存。到 production 规模，这道 gate 得做更多事：

1. 帮 candidate 打分，每个标准各自有名字、分开评，不是一句模糊的「重不重要」。
2. 产出有类型的决定，包含「先搁着」和「要人批准」，不是只有存或不存。
3. 每个决定在写入前都要验证，不管是规则做的还是模型做的。
4. 每个决定都留下理由，被拒绝的也要记。

---

## 机制

最简单的版本是一个函数：收一笔 candidate，返回一个决定。设计的重点在检查的先后顺序。
规则先跑，因为不用花钱、结果也固定。规则判不了的，才轮到模型。
validator 在任何东西写入之前，把每个决定再验一次。

五个零件：

- **Candidate**：一笔提议要存的 memory，从新进的 ledger event 整理出来，带着来源 event 的 id。
- **规则**：确定性的检查，照成本排序。看有没有证据、推不推导得回来、重不重复、够不够具体、敏不敏感。
- **classifier**：一次模型调用，只处理规则判不了的问题（下次还用得到，还是闲聊？）。
- **Decision**：动作、理由、信心。动作有四种：`store`、`ignore`、`defer`、`require_approval`。
- **validator**：挡在决定和写入之间的那道确定性检查。

track README 列的六个打分标准，刚好拆到不同层：

```text
Novelty       规则：跟现有 memory 比字词重叠
Specificity   规则：太模糊，之后根本搜不回来
Derivability  规则：grep 或 git 能再查到的不用存
Sensitivity   规则：有标记的要人批准
Durability    classifier：下次还用得到，还是闲聊？
Confidence    挂在决定上，谁做的决定谁填
```

两种记录的类型都很小：

```python
@dataclass(frozen=True)
class Candidate:
    content: str
    kind: str                          # episodic / semantic / procedural
    source_event_ids: tuple = ()
    sensitive: bool = False

@dataclass(frozen=True)
class Decision:
    action: str                        # store / ignore / defer / require_approval
    reason: str
    confidence: float
```

`decide` 依序走规则。规则都没意见就交给 classifier，没接 classifier 就默认存：

```python
def decide(candidate, existing=(), classifier=None) -> Decision:
    for rule in RULES:                 # evidence, derivability, duplicate, vagueness, sensitivity
        if decision := rule(candidate, existing):
            return decision
    if classifier is not None:
        return Decision(*classifier(candidate))
    return Decision(STORE, "novel, specific, evidence-backed", 0.6)
```

检查重复的那条规则有两道门槛。重叠很高，代表已经知道了，直接略过。
中等重叠就先搁着：把相近的 memory 合并是 consolidation（第 6 章）的工作，不是这道 gate 的工作：

```python
def _duplicate(c, existing):
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

`validate` 对每个决定都跑，不管是谁做的。模型可以提议，但只有通过验证的决定才会生效：

```python
def validate(decision, candidate) -> Decision:
    if decision.action not in ACTIONS:
        raise ValueError(f"unknown action: {decision.action}")
    if not decision.reason:
        raise ValueError("a decision without a reason cannot be audited")
    if not 0 <= decision.confidence <= 1:
        raise ValueError("confidence out of range")
    if decision.action == STORE and not candidate.source_event_ids:
        raise ValueError("store without source events")
    return decision
```

`gate` 把整条流程串起来，连决定本身也记进 log：

```text
fresh events (section 2)
    ↓ propose candidates
rules: evidence · derivability · duplicate · vagueness · sensitivity
    ↓ only what rules cannot settle
classifier: store / ignore / defer / require_approval
    ↓ every proposal
deterministic validator
    ↓ commit
typed memory (section 4) · the decision logged as an event
```

怎么跟其他章节接起来：candidate 来自 ledger 新进的数据（第 2 章），在 warm path 上做（执行结束才跑，不是每次查询都跑）。
存下来的 candidate 变成有类型的记录（第 4 章），搁着的等 consolidation 处理（第 6 章），
要批准的排进队列等人审，跟 Hermes 暂存写入等批准是同一个模式。
每个决定也会以 event 的形式写回 ledger。第 10 章就靠这些可重放的数据，量出 write precision。

最前沿的做法不是把 gate 写出来，而是训练出来：[Memory-R1](https://arxiv.org/html/2508.19828v2) 用结果导向的 RL
学 `ADD / UPDATE / DELETE / NOOP`，[AgeMem](https://arxiv.org/abs/2601.01885) 更进一步，
把 memory 操作直接并进 agent 自己的 policy。两者都需要任务专属的训练数据，所以都不适合当第一版。

### What Changed

跟最小可行的 loop 比：以前选择藏在 extractor 的 prompt 里，结果不是写了文件，就是什么都没有。
现在判断是明确、有类型的：接近重复的先搁着，不会悄悄越堆越多；敏感的写入等人批准；
每一笔被拒绝的 candidate，都在 log 里留下理由。

---

## 各系统做法

| | Mem0 | Hermes Agent |
| --- | --- | --- |
| **Pros** | gate 顺便做更新：新事实一趟就取代旧的。 | 敏感的写入会等人批准。session 中途就先存，执行结束时什么都不会漏。 |
| **Cons** | 每次写入都要花模型调用。update 或 delete 判错，就是破坏性的修改。 | 批准有摩擦，队列可能堆着没人看。规则写在 prompt 里。 |
| **Why** | 把存储区当成小而精的收藏：新事实要融进去，不是往上堆。 | 信任模型提议，但不让模型动手写：最后那笔写入由人做主。 |
| **How: candidate** | 模型从最新的对话往来里抽出 candidate。 | 模型觉得某件事够持久，session 中途就调用 memory tool。 |
| **How: 决定** | 模型对照相似的现有 memory，选 add、update、delete 或 no-op。 | 直接写入；开了批准模式就先挂成待审。 |
| **How: 保险** | 每笔 memory 留操作历史，改坏了可以回查。 | 待审的写入一笔一笔列出来，逐笔批准或退回。 |

---

## 哪里会出错

- **gate 太严：**存储区一直没什么东西，agent 重复问已知的事。
  分规则跟踪 ignore 的比率，边缘的 candidate 宁可 defer 也不要 ignore，让 consolidation 有机会再看一次。
- **gate 太松：**recall 的噪声越来越多。这量得出来：write precision 和重复率（第 10 章）。
  先调高重复门槛和具体度门槛，再去动 classifier。
- **决定静悄悄发生：**ignore 没留记录，「它怎么不记得 X」就查不下去。
  每个决定都留理由，被拒绝的也一样。
- **直接相信 classifier 的输出：**动作名称不认得、信心超出范围，都要大声失败。
  validator 对每个决定都跑，没验证过的东西不落地。
- **敏感判断全交给模型：**模型漏看一个标记，秘密就存进去了。
  敏感度用确定性的信号判（标记、pattern、来源渠道），classifier 只当第二意见。
- **gate 堵住 hot path：**每一轮都同步打分会拖慢响应。
  gate 放到执行结束再跑（warm path），整趟执行的 candidate 一次批量处理。

---

## 可执行程序

[`src/`](src/) 承接 01 的代码，再加上：

- [`policy.py`](src/policy.py)：`Candidate`、`Decision`、规则链、`decide`、`validate` 和 `gate`。
- [`engine.py`](src/engine.py)：`propose()` 让 candidate 过 gate，每个决定都以 event 写回 ledger。
- [`test.py`](src/test.py)：每条规则各触发一次、classifier 只在规则判不了时才被叫到、
  validator 挡下格式错误的决定，以及决定以 event 的形式落进 ledger。

```bash
python sections/03-write-policy/src/test.py   # offline checks, no key
```

离线时 classifier 是个 stub。上线时，规则判不了的 candidate，每笔各调用模型一次。

---

## 来源

- [Mem0](https://arxiv.org/abs/2504.19413)：先抽取再更新的写入，对照相似 memory 选 add / update / delete / no-op。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：把 write gate 当成用 RL 训练出来的 policy。
- [AgeMem](https://arxiv.org/abs/2601.01885)：把 memory 操作并进 agent 的 policy。
- [Hermes Agent 源码](https://github.com/NousResearch/hermes-agent)：`tools/write_approval.py`，暂存写入等批准。
- [Production memory track](../../README.zh-CN.md)：这一章所在的完整 lifecycle。
