# 10 · Evaluation and governance

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文**

> 没记下来的东西就量不到。数字后面要是没接门槛，量了也改变不了任何事。

这一章是 [Production memory](../../README.zh-CN.md) track 的最后一章：lifecycle 的 Feedback and evaluation。
前面九章各自写入、解冲突、合并、建 index、排序、注入，
但没有一章会知道自己做得对不对。这一章补上反馈，把这个循环关起来。

这个缺口很容易一直没人发现，因为 memory 坏掉的时候很安静。
一个什么都存的 gate 跟一个什么都不存的 gate，从 gate 自己看出去都一切正常。
检索回来一堆看起来合理、其实错误的 memory，跟检索正常运作也长得一样，
直到某个答案错了，而且没人说得出是哪一笔 memory 害的。

memory 也是唯一一个放着不管就会自己越变越差的子系统。
记录一直长，主张慢慢过期，consolidation 会漂，index 也会跟不上存储区。
一个没在量的系统不会停在昨天的水准：它会退化，而第一个发现的人是用户。

---

## 机制

最简单的版本：多记两种 event type，指标要看的时候再从 ledger 现算，
每个门槛都接一个动作。不新开任何存储区。

评估自己不存任何数据。每个指标都是读第 2 章的 ledger 和记录、当场算出来的，
规则跟第 7 章对待 index 的那条一样：把算出来的数字全部丢掉再算一次，什么都不会少。
contract 也不用动，Protocol 还是 observe、consolidate、recall 三个动词。

### 两种新 event type

```text
recall   →  memory_injected    注入了哪几个 id、花了多少、哪几笔有冲突
answer   →  memory_outcome     哪几笔真的有用、哪几笔带错路、用户有没有更正
              ↓
          ledger，原封不动：只能追加，评估不会自己开存储区
              ↓
   write · retrieval · context · end to end        四层，读的时候现算
              ↓
          gates：门槛，后面接着一个真的会执行的动作
```

回报 outcome 不需要在 contract 上加第四个动词。一则 outcome 就是对这一轮的一笔观察，
所以跟其他事件一样，用 `observe` 写进 ledger：

```python
@dataclass(frozen=True)
class Outcome:
    query: str
    injected: tuple = ()
    used: tuple = ()          # 答案真的有靠的那几笔
    misled: tuple = ()        # 靠了、结果答错的那几笔
    abstained: bool = False   # 答案有没有选择不靠 memory
    corrected: bool = False   # 用户有没有把结果改掉
```

`used` 和 `misled` 是判断，而且得靠这一章以外的信号来下。
上线的系统靠两个来源：请模型在回答时列出它引用的 memory id，
以及把用户的更正直接当成 misled 的标签。
这一章绝不从答案文字自己推：让系统自己改自己的考卷，量出来的分数没有意义。

数据怎么流：`recall` 在返回 block 之前，先把选到的东西记下来。调用方在答案出来之后回报 outcome。
`evaluate` 再把这两种事件连同记录一起读回来，返回四层指标，加上当下没过的那几道 gate。

### 四层指标

这些指标就是 `evaluate` 的输出：要看的时候才从 ledger 现算的数字，照 pipeline 分成四层。
每个指标算得出来，都是因为前面某一章先记了东西：

| 层 | 指标 | 在量什么 | 从哪里算出来 | 备注 |
| --- | --- | --- | --- | --- |
| write | decisions · store rate · duplicate rate · approval queue | gate 做了哪些决定，还有多少笔在等人审 | 第 3 章的决策记录，连是哪条规则决定的都有记 | |
| | write precision | 存下来的 memory 里，被证明写对的比例 | 同一份记录，加上 outcome | 只算已判决的：还没人用过的不计入，不然等于在罚 gate 存得太早 |
| | unsupported memory rate | 引用了 ledger 里没有的事件的记录 | 第 4 章的 source event id | 跟 *unsupported claims* 是两回事：那问的是答案本身，这里不量（见下表） |
| | wrong update rate | 存错、后来被 retract 掉的比例 | 第 5 章的 retract 和 supersede | |
| | consolidation precision | 合并出来的东西有多少没被退回 | 第 6 章回报的拒绝项 | |
| retrieval | evidence precision · misled rate | 捞回来的 memory 有多少帮上忙、有多少带错路 | 线上的 outcome | |
| | contradiction coverage | 碰到冲突时，是不是两边一起注入 | 第 9 章配好的冲突组 | 选取以组为单位，单一轮次只会是 1.0 或 0.0。跨轮次平均低于 1.0，代表有某一轮把冲突整组丢掉，回答时两边证据都不在 |
| | recall@k | 该找回来的 memory，前 k 名里回来了多少 | 标注过的数据集，没有就是没有 | |
| context | injected tokens · 每轮注入几笔 | memory 每轮占掉多少 prompt 空间 | 第 9 章每轮的注入记录 | |
| | injection hit rate | 注入的 memory 里，读起来像指令的笔数 | 同一份记录，注入当下就打好分 | 量到不代表攻击成功（guard line 已把整块框成数据），但 write gate 漏掉了一次人工审核 |
| | no inject rate | 有多少轮什么都没注入 | 注入记录里的空轮次 | |
| | stale rate | 注入时其实早就被关掉的 memory 有多少 | 第 5 章的 superseded_at，用 as_of 读 | 事后查得出来，是因为关掉一笔是盖上时间戳，不是把那一行删掉 |
| end to end | turns · correction rate · abstention rate | 多少轮、多少次被更正、多少次答案不靠 memory | outcome | |

### 刻意不量的东西

有几件事这一章刻意不量。不是漏掉，而是每一件都缺一样东西，这一章给不了：

| 刻意不量 | 缺什么 |
| --- | --- |
| temporal correctness · unsupported claims | 要对答案本身下判断，不是对记录 |
| LongMemEval 的 end-to-end 任务类别 | 标注过的语料。`Case` 只带一个 query 和该回来的 id |
| LongMemEval-V2 的 agent 经验任务类别 | 一样缺语料，这一章点了名，但没附 |

类别清单来自 benchmark 本身：LongMemEval 是 extraction、multi-session reasoning、
temporal reasoning、knowledge updates；LongMemEval-V2 是 static state、dynamic state、
workflow knowledge、environment gotchas、premise awareness。

### abstention 拆成两个

abstention 这个词得拆成两个指标，因为有两件不一样的事一直共用这个名字：

| | no_inject_rate | abstention_rate |
| --- | --- | --- |
| **发生了什么** | 这一轮检索什么都没注入 | 答案自己决定不靠 memory |
| **是谁的性质** | 检索的：不值得花那笔 budget，是第 9 章正常运作 | 推理的：跟其他 outcome 一样由调用方回报 |

不管哪一种，空的注入都要记一行。
不记的话，「决定不注入」跟「recall 根本没跑」在 ledger 里会长得一模一样。

### 「没量到」不是零

分母是零的时候返回 `None`，绝对不是 `0.0`：

```python
def _share(part, whole):
    return part / whole if whole else None
```

没量到的指标，跟量出来是零的指标，意思不一样。
要是把「没量到」也显示成 0，一条停止回报的 pipeline，错误率全部变成 0，看起来就像满分。
`recall@k` 也是同一条规则：没有标注数据它就是「没有」，不是「满分」，再多的线上流量也生不出它来。

### gate：门槛接动作

governance 落在 gate 的 `action` 字段上。
光有门槛只是一个 dashboard，一堆大家瞄一眼、没人负责、也不会挡任何一次上线的数字：

```python
@dataclass(frozen=True)
class Gate:
    metric: str          # "layer.name"
    limit: float
    action: str          # 踩到的时候要做什么
    floor: bool = False  # 这个 limit 是下限，不是上限

GATES = (
    Gate("write.unsupported_rate", 0.0,
         "block the write path: a memory is citing evidence the ledger does not have"),
    Gate("context.stale_rate", 0.05,
         "reindex after consolidation before serving: closed records are reaching prompts"),
    Gate("context.injection_hit_rate", 0.0,
         "review the write gate: instruction-shaped memories are reaching the prompt"),
    Gate("retrieval.misled_rate", 0.20,
         "hold the learned write policy: these outcomes are too noisy to train on"),
    Gate("retrieval.contradiction_coverage", 1.0,
         "raise the budget or the retrieval cap: conflicts are being dropped whole",
         floor=True),
)
```

一道 gate 如果盯着一个没有任何一层会回报的指标，它会直接抛错，而不是返回空值。
因为一个打错字之后默默变成「没量到」的 gate，等于从此再也不会触发，
这跟把没量到的指标读成零是同一种错，只是发生在更上面一层。

最后那道 gate 是 track 第五条规则（evaluate before trust）真的派上用场的地方。
学出来的写入策略拿 outcome 训练，所以一个 outcome 每五次就错一次的系统，
会很有自信地把这些噪声学进去，然后变得更差。
evaluate before trust 第一个要管的，就是 memory 系统自己的升级。

### 循环收尾：retract

量出来的问题要有人能处理，循环才算真的关上。
一则 outcome 说某笔 memory 把答案带错了，接手的操作是 `retract`：第 5 章就有的操作，不是新动词。
那笔记录会标成错的，而且还读得到，因为 retracted 的意思是它从来就不成立，superseded 的意思才是世界变了。
这是这一章唯一一次改动状态。要是没有任何地方调用它，数它的那个指标永远只会是零。

### 热路径只有一行

指标和 gate 全部跑在冷路径：想算哪一段时间都行，算完丢掉也没差。
真正在热路径上的只有一件事：`recall` 每次查询返回之前，会 append 一行注入记录。
纯写入，不用先读任何东西。这一行换来整个 context 层：
一次没记下来的注入，就是一轮没人算得出分数的对话。

### What Changed

跟第 9 章比：`assemble` 现在会把「选了什么」跟「render 出什么」一起交回来，
engine 要记 id 和 token 花费时，不用把同一件事再做一次。
决策除了 action，还会把**是哪条规则决定的**写成具名字段，不再塞进消息的前缀，
因为靠 parse log 消息来数的指标，会在有人改一次措辞那天默默坏掉。
两种事件也各自只有一个地方负责写、一个地方负责读，所以没有任何指标直接拿字符串 key 去读 dict：
绑在五个字符串 key 上的指标，坏法一模一样，只是更安静。
你打算量什么，就把什么记成字段，并且用当初写入的那个类型读回来。

---

## 各系统做法

| | LongMemEval | Memory-R1 |
| --- | --- | --- |
| **Pros** | 不同系统之间可以直接比。某一类任务挂掉，就直接指出是哪一种能力坏了。 | 不用任何人标数据就能改进策略，学的是真实流量。 |
| **Cons** | 固定的数据集会过期，过得了它不代表上线之后没问题。 | 奖励只看最后的答案，所以一笔刚好蒙对的错 memory，奖励照样会强化它。 |
| **Why** | memory 的各种能力本来就分得开，评分也该分开。 | 唯一算数的判断，就是答案有没有对。 |
| **How: 信号** | 标注过的 session，附上该用哪些证据，照任务类别分开评分。 | 一趟跑完看答案对不对，反馈成 store、edit、forget 的奖励。 |
| **How: 时机** | 离线，跑固定数据集，上线前跑。 | 线上，跟着真实 outcome，一直跑。 |
| **How: 覆盖范围** | 它自己定义的那几类任务，以外的都不管。 | 流量刚好练到什么就覆盖什么，没练到的就没有。 |

---

## 哪里会出错

- **指标后面没接动作：**数字画成图表，没人负责，也没有任何一次上线会等它。
  这里每一道 gate 都带着它要触发的动作，没有动作的门槛只是装饰。
- **自己改自己的考卷：**产生答案的那个模型，同时决定 memory 有没有帮上忙。
  标签要从外面来：用户的更正、模型列出来的 id、人审过的数据集。
- **把「没量到」当成零：**一条停止回报 outcome 的 pipeline，correction rate 会漂亮得不得了。
  「没有」跟「零」要在算术那一层就分开，不是在 dashboard 上分。
- **只看 end-to-end 的数字：**一个会因为十种原因上下跳的分数，说不出是哪一章坏了。
  分层评分，让没过的那一层自己指向它的章。
- **一个根本生不出来的指标：**一个比率，要是分子没有任何代码路径会去产生，它就永远是干净的零，
  跟「系统表现很好」长得一模一样。每一个要数的类别都得有人调用，不然那个指标只是带着分母的装饰。
- **评估跑在热路径上：**一边回答一边算这一轮的分数，等于每次查询都多等一下，换来的还是一个明天才有人看的数字。
  热路径只负责记一行，算分留给冷路径。
- **一个词配到两个指标：**abstention 同时代表「检索什么都没注入」和「答案选择不靠 memory」，
  等于把检索的性质跟推理的性质平均在一起。要么分开命名，要么两个都别量。
- **拿 benchmark 当上线标准：**固定数据集过了只说明底线守住了，不代表上线之后没问题。
  线上的 evidence precision 和 stale rate 要在 benchmark 过关之后继续量。
- **没人处理的审核队列：**积压只要一直长，`require_approval` 就会变成一个比较慢的 `ignore`，
  write gate 等于悄悄整类都不存了。要量的是队列长度，不只是决策数。
- **把删除当成设计上的 bug：**只能追加的系统，总有一天会碰到法律要求的删除。
  把它做成一次有审计、有范围、自己也留下记录的操作，而不是一个任何一章都能调用的 `DELETE` 动词。

---

## 可执行程序

[`src/`](src/) 承接 08 并加入：

- [`evaluate.py`](src/evaluate.py)：`Outcome`、`Injection`、`Case`、`Gate`、四层各自的函数、`report`、`failing`，
  以及两种 event type 各自一个写入函数、一个读回函数，所以没有任何指标直接拿字符串 key 去读 dict。
  没量到的指标返回 `None`，不会是 `0.0`。
- [`engine.py`](src/engine.py)：`recall` 会把注入的东西记下来，空的那几轮也记，
  `feedback` 把 outcome 当成一般观察写进去，`last_injected` 让调用方知道要回报哪几笔 memory，
  `retract` 用第 5 章的操作把一笔 memory 标成错的，`evaluate` 从记录把整条链的分数算出来。
- [`policy.py`](src/policy.py)：决策现在会带着「是哪条规则决定的」，而且只在一个地方盖上去，
  不用每条规则各写一次，这样 duplicate rate 数的是一个类别，不是去比对一串理由文字。
- [`assemble.py`](src/assemble.py)：`assemble` 会把 block 和选到的东西一起返回，
  「选不到就不给 block」这条规则只有一个地方管，token 估算也就是 selector 自己那一份。
- [`test.py`](src/test.py)：空系统上「没量到」和「零」的差别、照 action 和照 rule 数的决策、
  只算已有判决的 write precision、引用不存在证据的记录、注入成本、两种 abstention 分得开、
  budget 把一组冲突整组丢掉时 contradiction coverage 会掉、一次 retract 把 wrong update rate 推离零、
  一笔长得像指令的 memory 触发它那道 gate、一道盯着没人回报指标的 gate 直接抛错而不是安静失效、
  有标注才会出现的 recall@k、更正前后的 stale rate，还有 gates 本身。

```bash
python sections/10-evaluation-governance/src/test.py   # offline checks, no key
```

这一章完全不调用模型，所以没有 `demo.py`。

---

## 出处

- [LongMemEval](https://arxiv.org/abs/2410.10813)：end-to-end 的任务分类，abstention 也当成一种能力来评分。
- [LongMemEval-V2](https://arxiv.org/abs/2605.12493)：同一套想法延伸到 agent 经验和 premise awareness。
- [Memory-R1](https://arxiv.org/html/2508.19828v2)：用 outcome 驱动的 RL 学 memory 操作，这一章的指标就是喂给它的东西。
- [AgeMem](https://arxiv.org/abs/2601.01885)：学出来的策略，决定什么时候存、改、忘。
- [Production memory track](../../README.zh-CN.md)：这一章所属的完整 lifecycle。
