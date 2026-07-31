# 4 · Typed memory

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 一个纯文本桶回答不了三种不同的问题。memory 要照「它回答什么」分型，也要照「系统怎么知道的」分型。

这一章讲 [Production memory](../../README.zh-CN.md) track 里 lifecycle 的 Encode：
把通过 write gate（第 3 章）的 candidate，变成有类型、验证过的记录。

不分型的存储区会把「上周二部署失败了」、「Marcus 主要写 Python」、
「跑 schema migration 前先检查 migration lock」全部当成同一种东西：一段字符串。
但这三句回答的问题不同，老化的方式不同，检索的方式也不同。
存储区要是分不清「观察到的」和「模型自己猜的」，迟早会把猜测讲成事实。

文件式的 agent memory，分类标准通常是「什么值得留」（user、feedback、project、reference）。
这一章改用「怎么用」来分：

1. 每笔记录归进一种功能 kind：发生过什么、现在相信什么、下次该怎么做。
2. 另外记 epistemic type：这句是观察到的、验证过的、用户的偏好、系统推论的，还是主观意见。
3. 构造时就验证。没有证据或类型不合法的记录，根本存不进来。
4. 保留出处：每笔记录都能指回它来自哪些 source event。

---

## 机制

最简单的版本：两组 enum，加一个会验证的构造函数。

三种功能 kind：

```text
episodic     发生过什么。「部署卡在 migration lock，最后靠 rollback 解决。」
             时间、环境、结果都留着。它过时是变成历史，不是变成错的。
semantic     现在相信什么。「Marcus 主要写 Python。」
             从 episode 蒸馏出来，可以修：新证据能取代它。
procedural   下次该怎么做。「跑 schema migration 前先检查 migration lock。」
             workflow、runbook、gotcha。下次遇到类似情况就用得上。
```

epistemic type 是第二组标签，跟 kind 各管各的。kind 回答「要拿来做什么」，epistemic type 回答「我们怎么知道的」：

```text
evidence     原始观察，直接来自 ledger
fact         验证过的，或用户亲口说的
preference   用户想要什么；记的是偏好，不是事实
inference    系统自己的猜测，可以修，而且要标明是猜的
opinion      主观看法，系统永远不会悄悄把它升级成事实
```

记录把 kind、epistemic type 和出处绑在一起。
`make_record` 是唯一的构造入口，所以只要记录存在，就一定验证过：

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: Scope
    kind: str                  # episodic / semantic / procedural
    epistemic_type: str        # evidence / fact / preference / inference / opinion
    content: str
    source_event_ids: tuple    # the evidence, from section 2
    confidence: float
    recorded_at: str
    status: str = "active"     # active / superseded / retracted
    tags: tuple = ()

def make_record(scope, kind, epistemic_type, content, source_event_ids,
                confidence, tags=()) -> MemoryRecord:
    return validate(MemoryRecord(...))
```

验证跟第 3 章是同一套模式：规则写死的检查，一出错就直接抛异常。
没有 source event 的记录直接退回，因为讲不出证据的 memory 就是谣言：

```python
def validate(record) -> MemoryRecord:
    if record.kind not in KINDS:
        raise ValueError(f"unknown kind: {record.kind}")
    ...
    if not record.source_event_ids:
        raise ValueError("a record without source events cannot cite its evidence")
```

存储区照 kind 分流，照 scope 和 status 过滤。`grounded` 把知识和猜测切开：

```python
def current(self, scope, kind) -> list[MemoryRecord]:
    """Active records of one kind for one scope, newest first."""

def grounded(records) -> list[MemoryRecord]:
    return [r for r in records if r.epistemic_type in ("evidence", "fact")]
```

数据这样流：write gate（第 3 章）放行的 candidate 进到这一章，盖上 kind 和 epistemic type，变成正式的记录。
之后 resolution（第 5 章）补上 bitemporal 字段（事情何时为真、何时记下），supersede 时改写 `status`。
retrieval（第 8 章）按 kind 分路查询，context assembly（第 9 章）把 epistemic 标签印在内容旁边，
让模型看得出哪些是观察、哪些是系统自己的推论。

### What Changed

跟那套分类比，变的是问题本身：四种文件类型回答「值不值得留」，
kind 回答「要拿来做什么」，epistemic type 回答「我们怎么知道的」。
「值不值得留」的判断已经搬去第 3 章，这一章只管记录长什么样子。
[Hindsight](https://arxiv.org/html/2512.12818v1) 划的是同一条线：发生过的事，永远不跟 agent 对它的想法混在一起。

---

## 各系统做法

| | LangMem | Hindsight |
| --- | --- | --- |
| **Pros** | 写入当下就照 app 自定义的 schema 验证。 | 发生过的事不会跟 agent 的想法混在一起。信念可以修，经验留着不动。 |
| **Cons** | 类型帮得上多少忙，取决于 app 定义的 schema 好不好。 | 库分成四个，分流就变多；一分错，条目就落在错的库里。 |
| **Why** | 每个 app 要存的 memory 都长得不一样，所以 schema 由 app 提供。 | 分不清观察和意见的 agent，会把猜测讲成事实。 |
| **How: 类型** | semantic、episodic、procedural 三种 memory 类型。 | world fact、experience、observation、opinion 分成四个库。 |
| **How: 单位** | 每个用户一份 profile 文件，或一批照 schema 验证的记录。 | 各库里分了型的条目，由 reflection 这道流程写入。 |
| **How: 更新** | profile 就地修补；collection 新增或更新记录。 | reflection 读新证据、修订信念，并引用它读过的东西。 |

---

## 哪里会出错

- **什么都变成 semantic：**一桶装所有东西的存储区又悄悄回来了。写入时就要分流：
  带时间戳的结果是 episodic，带触发条件的指示是 procedural。
- **把推论存成事实：**系统会把模型的猜测当成事实讲出去。
  所以 epistemic type 在构造时必填，assembly（第 9 章）还会把它印在内容旁边。
- **有类型但没出处：**引用不了任何证据的记录，没办法查证、没办法取代，也不能信。
  只要 `source_event_ids` 是空的，验证一律退回，没有例外。
- **schema 一直改：**每加一个字段或新 kind，旧记录就对不上新 schema。
  衍生记录都能从 ledger（第 2 章）重建，所以 schema 迁移只是重放一次，不用搬数据。
- **procedure 丢了触发条件：**「检查 migration lock」少了「跑 schema migration 前」，
  系统就不知道什么时候该用它，这条指令永远派不上用场。
  procedural 记录要连条件一起留，不是只留指令。

---

## 可执行程序

[`src/`](src/) 接手 02 的整条代码链，再加入：

- [`records.py`](src/records.py)：两组 enum、`MemoryRecord`、`make_record`、`validate`、`grounded` 和 `TypedStore`。
- [`engine.py`](src/engine.py)：通过 gate 的 candidate 在这里变成验证过的 typed record。
  `observe()` 这条路到这里就完整了：event、gate、记录。
- [`test.py`](src/test.py)：验证退回坏的 kind、epistemic type、置信度和没出处的记录；
  kind 分流加 scope 隔离；grounded 的切分；status 控制哪些记录查得到；
  engine 把过关的 candidate 存成 typed record，置信度沿用 gate 的决定。

```bash
python sections/04-typed-memory/src/test.py   # offline checks, no key
```

这一章完全不会调用模型，所以没有 `demo.py`。

---

## 来源

- [Hindsight](https://arxiv.org/html/2512.12818v1)：fact、experience、observation、opinion 的 epistemic 切分。
- [LangMem](https://github.com/langchain-ai/langmem)：照 schema 验证的 memory，profile 与 collection。
- [Production memory track](../../README.zh-CN.md)：这一章所在的 lifecycle。
