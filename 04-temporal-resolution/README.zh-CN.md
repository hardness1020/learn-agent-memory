# 4 · Temporal resolution

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 一件事不再成立，不代表它当初是错的。把它关掉，不要盖掉。

这一章是 [Production memory](../README.zh-CN.md) track 的第五章：
lifecycle 的阶段 5（Resolve），处理身份、冲突和时间。

阶段 4 让每笔记录有了类型和出处，但没有回答一个问题：新记录跟旧记录讲的不一样时，该怎么办。
没有答案的存储区只剩两条路，而且都不好。
盖掉，旧主张就消失了，没人查得出系统上个月相信什么、又为什么改。
两笔都留，检索就会返回两个都还在生效的矛盾，让模型自己猜。

所以要拆成三个问题分开处理：

1. 身份。「Marcus」和「Ming-Siang」讲的是不是同一个人？
2. 冲突。「住在 San Diego」和「住在 San Francisco」是矛盾，还是不同时间各自成立的两件事？
3. 时间。哪一笔现在成立，哪一笔是当时成立？

---

## 机制

最简单的版本：每笔记录记两组时间，不是一组，而且永远不删。

```text
recorded_at ─────────── superseded_at      系统相信它的那段期间：从记下来到被取代
valid_from  ─────────── valid_to           它在真实世界成立的那段期间
```

这就是 bitemporal 模型。要两组时间，是因为问题本来就有两种：
「系统现在认为 Marcus 住哪」问的是系统相信什么，看 recorded_at 那组。
「Marcus 三月住在哪」问的是真实世界，看 valid_from 那组。
一组时间写不下这种记录：今天才记下来、讲的却是去年的事。
只记去年，看不出系统今天才知道；只记今天，事情何时成立就丢了。两组都记才放得下。
[Graphiti 和 Zep](https://arxiv.org/abs/2501.13956) 的 temporal knowledge graph 用同一种切法：旧的边会关闭，不会消失。

要判断冲突，得先分得出哪些主张在互相竞争，哪些只是长得像。所以每笔记录带一个 claim key：
主语加谓语当 key，内容就是宾语。

```text
claim_key "marcus:lives_in"   content "Marcus lives in San Diego"      status superseded  valid_to 2025-06
claim_key "marcus:lives_in"   content "Marcus lives in San Francisco"  status active      valid_from 2025-06
```

同一个 key、内容不同、两笔都在生效，代表其中一笔已经不成立了。
key 空着，代表这笔主张没有竞争对手，所以永远不会被自动取代。

所有操作都不破坏数据。没有想改哪就改哪的 `UPDATE`，也没有 `DELETE`，event ledger（阶段 2）那边同样没有：

```text
ADD        新记录进来，生效中
SUPERSEDE  旧的关闭，新的开始
RETRACT    标记这笔是错的，但还读得到
ABSTRACT   多笔记录浓缩成一笔更高层的记录（阶段 6 会产生）
```

要更新一个事实，就是加一笔新记录，再用 SUPERSEDE 把旧的关掉。
严格说，关掉也是对旧记录的一次写入，但它是唯一允许的一种：
盖上收尾的字段（status、superseded_at、valid_to），单向、盖一次就定了。
主张内容一个字不改，旧记录随时读得回来。

`supersede` 和 `retract` 长得像，意思不一样。
supersede 是说它以前成立过，retract 是说它从来就不成立，通常是抽取抽错了。
两种都保持可读，因为一笔被 retract 的记录，正好就是「抽取逻辑要修」的证据。

写入路径多了一步。`resolve` 把验证过的记录放上时间轴，并关掉跟它冲突的旧记录：

```python
def resolve(store, record, at=None) -> list:
    ops = []
    for old in conflicts(record, same_scope(record.scope, store.records)):
        closed, op = supersede(old, at, cause_id=record.id)
        store.records[store.records.index(old)] = closed
        ops.append(op)
    store.add(replace(record, valid_from=record.valid_from or at))
    return ops + [Operation(ADD, record.id, "new claim", at)]
```

scope 隔离不只做在读取的过滤条件上，写入时也要守。
supersede 是一次写入，所以 A 租户的主张绝对不能关掉 B 租户字面相同的那句。
`same_scope` 在计算冲突之前，就先把别的租户的记录排除掉。

两组时间读出来就是两种查询：

```python
def as_of(records, when):     # what the system believed at a past moment
    return [r for r in records if r.recorded_at <= when
            and (r.superseded_at is None or r.superseded_at > when)]

def valid_at(records, when):  # what was true in the world at that moment
    return [r for r in records if (r.valid_from is None or r.valid_from <= when)
            and (r.valid_to is None or r.valid_to > when)]
```

数据怎么流：write gate（阶段 3）放行 candidate，阶段 4 把它做成有类型的记录，
这个阶段把记录放上时间轴，并返回做了哪些操作。
engine 把每个操作写回 ledger 变成 event，所以时间轴本身也能重放。
retrieval（阶段 8）只捞生效中的记录；context assembly（阶段 9）会读这个阶段记下的冲突，
把两边都打出来，不会偷偷选一边。

### 整合时发现的两个 bug

这两个 bug 都出在 write gate（阶段 3），但要等这个阶段接上来、更正真的送进 gate，才看得出来。
这个阶段的第一版就是这样坏掉的。
照这条 track 的惯例，修正落在这个阶段带着走的那份 code，前面的文件夹保留当时的版本，对照着看就知道整合改了什么。

**Bug 1：更正被当成疑似重复。**gate 用字面重叠判断像不像，跟现有 memory 太像的 candidate 会被搁着延后处理。
可是更正和被更正的那句，字面本来就很像：
「Marcus lives in San Diego」和「Marcus lives in San Francisco」大部分的字都相同。
结果更正被搁着，resolution 根本收不到，过时的旧主张就一直生效：
gate 搁下的，正好是这个阶段要处理的 candidate。
修法是带 claim key 的 candidate 不量重叠：字面完全相同才算重复，
其他一律当成更正，交给 resolution 判断。

```python
def _duplicate(c, existing):
    if c.claim_key:
        # 带 claim key 的 candidate 不量重叠：字面完全相同才算重复，
        # 其他一律当成更正，交给 resolution 判断
        return Decision(IGNORE, "already known", 0.9) if c.content in existing else None
    best = max((_overlap(c.content, m) for m in existing), default=0.0)
    if best >= DUPLICATE_AT:
        return Decision(IGNORE, "already known", 0.9)
    if best >= SIMILAR_AT:
        return Decision(DEFER, "similar memory exists, merge at consolidation", 0.6)
```

两句只差一个字的时候，重叠阈值本来就分不出「同一个主张」和「相反的主张」。

**Bug 2：敏感检查排太后面。**同一条疑似重复的规则，还踩出另一个问题：规则的顺序。
gate 是一条一条往下试，哪条先做出决定就停在哪。
检查敏感数据的规则本来排在最后，candidate 只要先被判成疑似重复，就根本轮不到它。
这种检查就是要挡住不该存的东西，不排最前面就等于没有。现在它排在所有会存东西的规则前面：

```python
RULES = (_no_evidence, _sensitive, _derivable, _duplicate, _vague)
```

### What Changed

跟阶段 4 比：以前一笔记录写好就定了，是一笔有类型、不会再动的数据。
现在它有生效的起点和终点，会被更正、被取代。
`status` 以前只是跟着存、没人动的字段，现在 supersede 改的就是它。

---

## 各系统做法

| | Graphiti / Zep | Claude Code auto memory |
| --- | --- | --- |
| **Pros** | 旧事实查得到，答案讲得出日期。矛盾在写入时就解决掉。 | 不用建模。一次更正就是改一个文件，用户还原得回来。 |
| **Cons** | 每条边两组时间，schema 变重，抽取那一步得把时间填对。 | 没有历史：文件一改写，前一个主张就没了。 |
| **Why** | agent memory 本来就是一连串更正，所以「让旧的失效」得直接做进数据模型。 | 假设有人会看存储区，所以文件本身就是审计记录。 |
| **How: 冲突** | 新的边通过 graph 让跟它矛盾的旧边失效。 | 模型直接改写受影响的 memory 文件。 |
| **How: 时间** | bitemporal：每条边同时存事件时间和写入时间。 | 一组隐含的时间，就是文件当下的状态。 |
| **How: 恢复** | graph 从 episode 重建，episode 本身完整留着。 | 看用户自己在那个目录外面套了什么版本控制。 |

---

## 哪里会出错

- **就地盖掉：**旧主张消失，「为什么改了」查不到答案，改错了也救不回来。
  supersede 是补一个时间戳，不是删掉那一行。
- **两笔都在生效：**检索返回两个矛盾，模型只能乱选。
  冲突检测做在写入时，不是读取时，所以同一个 key 永远只有一笔生效中。
- **抽错的记录用 supersede 关掉：**这笔错的记录从此被当成「曾经成立过」，之后每一次 `valid_at` 查询都被它污染。
  retract 独立成一个操作就是为了这种情况。
- **更正被当成疑似重复挡下：**write gate 看到字面重叠就把更正丢掉。
  带 claim key 的 candidate 改成只看字面是否完全相同，因为更正跟它要更正的那句往往只差一个字，
  重叠比例在这里说明不了任何事。
- **敏感检查排太后面：**gate 哪条规则先做出决定就停在哪，这条检查以前排在最后，
  candidate 只要先被判成疑似重复，就根本轮不到它。会存东西的规则一律排在它后面。
- **跨租户 supersede：**两个租户存了同一句话，其中一个关掉了另一个的主张。
  scope 在计算冲突之前就检查，不是算完才补。
- **claim key 各写各的：**`marcus:lives_in` 和 `marcus:location` 永远不会互相竞争，两笔都留着，矛盾就看不见。
  key 要来自受控的词汇表，不能自由发挥。
- **身份没有先解析：**「Marcus」和「Ming-Siang」拿到不同的 key，永远不会冲突。
  身份解析要跑在冲突检测前面，不然冲突检测比对的是错的组合。

---

## 可执行程序

[`src/`](src/) 承接 03 的代码，再加上：

- [`resolve.py`](src/resolve.py)：操作的词汇表、`conflicts`、`supersede`、`retract`、
  `resolve`，以及读两组时间的 `as_of` 和 `valid_at`。
- [`records.py`](src/records.py)：bitemporal 字段、`claim_key`，加上会挡掉不可能时间轴的验证。
- [`policy.py`](src/policy.py)：带 claim key 的 candidate 只看字面是否完全相同，不看重叠；
  敏感检查排在所有会存东西的规则前面。`words` 会滤掉 stopword，查询不会只靠「the」就命中。
- [`engine.py`](src/engine.py)：存下来的记录落在时间轴上，每个操作都写回 ledger，
  `believed_at` 和 `true_at` 分别读两组时间。
- [`test.py`](src/test.py)：冲突检测和 scope 隔离、supersede 与 retract 的差别、
  补记的记录让两组时间分岔、被挡掉的不可能时间轴，还有一次更正从头到尾关掉旧主张。

```bash
python tracks/production-memory/04-temporal-resolution/src/test.py   # offline checks, no key
```

这个阶段完全不调用模型，所以没有 `demo.py`。

---

## 来源

- [Zep / Graphiti](https://arxiv.org/abs/2501.13956)：bitemporal knowledge graph，让边失效而不是删掉。
- [A-Mem](https://arxiv.org/html/2502.12110v1)：linked note，会随新 memory 进来持续演化。
- [第 9 章 · Memory](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/README.zh-CN.md)：只记一组时间的存储区，这个阶段把它扩充成两组。
- [Production memory track](../README.zh-CN.md)：这个阶段所属的 lifecycle。
