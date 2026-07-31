# 1 · Memory contract

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 先做两个决定，再谈机制：这份 memory 是谁的？用什么接口把背后的运作藏起来？

这一章是 [Production memory](../../README.zh-CN.md) track 的第一章：
lifecycle 的 Scope，加上核心抽象里的 engine 接口。
这里写的 `contract.py`，后面每一章都会继续沿用。

memory 系统最严重的事故不是检索出错，而是 scope 出错：
A 用户的个人数据，出现在 B 用户的对话里。
这种问题靠调检索没有用：内容抓得越准，泄露就越严重。

第二个决定是接口。memory 背后有十章的机制在跑，但 harness 用起来只该有三个动词。
调用方一旦直接查 index，这个 index 就动不了了：一改就会弄坏调用方，从此重建不了也换不掉，
track 的铁律（事件永远留着，view 随时可重建）也就失守了。

所以先定两条约定，再谈机制：

1. 每笔数据都标 scope，每次读取都用 scope 过滤。
2. 读写一律走 `observe`、`recall`、`consolidate` 三个动词。

---

## 机制

最简单的版本只有两样东西：一个 frozen dataclass，一个 protocol。

Scope 回答的是「这笔 memory 属于谁」。它声明成 frozen，因为 scope 本身就是一种身份：
可以当 dict 的 key，也不怕传到一半被改掉：

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
```

三个字段一层套一层：tenant 底下有很多 user，一个 user 可能跑好几个 agent。
留 `None` 是放宽范围（比如整个 tenant 一起读），填了值就是收窄。只有 tenant 必填。

engine 接口就是三个动词，用 `Protocol` 定义。
判定看结构：有这三个方法的对象就是 engine，不用继承任何东西：

```python
@runtime_checkable
class MemoryEngine(Protocol):
    def observe(self, scope: Scope, event_type: str, content: str) -> str: ...
    def recall(self, scope: Scope, query: str) -> str: ...
    def consolidate(self, scope: Scope) -> dict: ...
```

每个动词各管 lifecycle 的一段：

```text
observe      raw evidence in            capture, gate, encode      (sections 2-4)
recall       evidence-backed text out   index, retrieve, assemble  (sections 7-9)
consolidate  background maintenance     resolve, consolidate       (sections 5-6, cold clock)
```

接口刻意做小，守的就是那条铁律。调用方只看得到动词，动词后面的东西全都可以换：
index 坏了重建、存储区搬家、resolver 重写，调用方一行代码都不用改。

每一章的文件夹都带着这个文件和之前的所有代码，在同一个 `engine.py` 上一章一章往下长。
diff 相邻两章的 `src/`，看到的就是那一章的机制。

---

## 各系统做法

Claude Code 和 Hermes 都是本机的单租户工具，所以 tenant 字段在它们身上看起来可有可无。
track 的合约把它列为必填：production 是一套系统同时服务很多租户。

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **Pros** | memory 照项目分开存，repo 之间不会互相污染。 | 一份 profile 跟着用户走，在哪个渠道讲话都认得你。 |
| **Cons** | 同一个用户跨项目的事实被拆散在不同存储区。 | 没有照项目分开：用户做的所有事都装在同一个桶里。 |
| **Why** | memory 服务的是工作目录：context 就是项目。 | memory 服务的是关系：context 就是这个人。 |
| **How: scope 单位** | 每个用户、每个项目目录各一份。 | 每个用户一份，跨渠道共用。 |
| **How: 隔离** | 照项目路径分开的 memory 目录。 | 本机数据库里按用户分开的状态。 |
| **How: 租户** | 单一租户：这台机器。 | 单一租户：架设者自己的部署。 |

---

## 哪里会出错

- **完全没有 scope：**最经典的事故：A 用户的数据出现在 B 用户的对话里。
  scope 是每个动词的必填参数，不是谁想到才加的过滤条件。
- **scope 写在正文里：**「Marcus 的 staging key 是⋯」存进去了，却没挂在任何 user 底下。
  过滤器读不懂正文。scope 是结构化字段，写入当下就要标好。
- **scope 切太粗：**只切到 tenant，同一个 tenant 的用户就会看到彼此的数据。
  user 和 agent 字段第一天就要放进去，就算暂时都是 `None`。
- **调用方绕过接口：**某个 dashboard 直接查 index，从此 index 一重建，dashboard 就跟着坏。
  所有读写都走那三个动词。
- **只有接口、没有保证：**三个动词本身什么都不保证。
  合约真正的内容是那条铁律（事件留着、view 可重建），后面每一章的测试就是在逐一验它。

---

## 可执行程序

[`src/`](src/) 是整条链的起点，后面每一章都会带着它往下走：

- [`contract.py`](src/contract.py)：`Scope` 和 `MemoryEngine` protocol。
- [`test.py`](src/test.py)：验 scope 可以当 key、建好后不可改，用玩具 engine 验结构兼容，
  再用迷你例子验按 scope 过滤的 recall。

```bash
python sections/01-memory-contract/src/test.py   # offline checks, no key
```

这一章完全不会调用模型，所以没有 `demo.py`。

---

## 来源

- [MemMachine](https://arxiv.org/abs/2604.04853)：把 memory 当成一个子系统，用同一个接口服务多个用户和 agent。
- [Claude Code memory](https://docs.claude.com/en/docs/claude-code/memory)：按项目和按用户两种 scope 的文件式 memory。
- [Production memory track](../../README.zh-CN.md)：这一章实现的第 1 章和核心抽象。
