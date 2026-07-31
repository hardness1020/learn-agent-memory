# 1 · Memory contract

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 先做兩個決定，再談機制：這份 memory 是誰的？用什麼介面把背後的運作藏起來？

這一章是 [Production memory](../../README.zh-TW.md) track 的第一章：
lifecycle 的 Scope，加上核心抽象裡的 engine 介面。
這裡寫的 `contract.py`，後面每一章都會繼續沿用。

memory 系統最嚴重的事故不是檢索出錯，而是 scope 出錯：
A 使用者的個人資料，出現在 B 使用者的對話裡。
這種問題靠調檢索沒有用：內容抓得越準，洩漏就越嚴重。

第二個決定是介面。memory 背後有十章的機制在跑，但 harness 用起來只該有三個動詞。
呼叫端一旦直接查 index，這個 index 就動不了了：一改就會弄壞呼叫端，從此重建不了也換不掉，
track 的鐵律（事件永遠留著，view 隨時可重建）也就失守了。

所以先定兩條約定，再談機制：

1. 每筆資料都標 scope，每次讀取都用 scope 過濾。
2. 讀寫一律走 `observe`、`recall`、`consolidate` 三個動詞。

---

## 機制

最簡單的版本只有兩樣東西：一個 frozen dataclass，一個 protocol。

Scope 回答的是「這筆 memory 屬於誰」。它宣告成 frozen，因為 scope 本身就是一種身分：
可以當 dict 的 key，也不怕傳到一半被改掉：

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: str
    user_id: str | None = None
    agent_id: str | None = None
```

三個欄位一層包一層：tenant 底下有很多 user，一個 user 可能跑好幾個 agent。
留 `None` 是放寬範圍（例如整個 tenant 一起讀），填了值就是收窄。只有 tenant 必填。

engine 介面就是三個動詞，用 `Protocol` 定義。
判定看結構：有這三個方法的物件就是 engine，不用繼承任何東西：

```python
@runtime_checkable
class MemoryEngine(Protocol):
    def observe(self, scope: Scope, event_type: str, content: str) -> str: ...
    def recall(self, scope: Scope, query: str) -> str: ...
    def consolidate(self, scope: Scope) -> dict: ...
```

每個動詞各管 lifecycle 的一段：

```text
observe      raw evidence in            capture, gate, encode      (sections 2-4)
recall       evidence-backed text out   index, retrieve, assemble  (sections 7-9)
consolidate  background maintenance     resolve, consolidate       (sections 5-6, cold clock)
```

介面刻意做小，守的就是那條鐵律。呼叫端只看得到動詞，動詞後面的東西全部可以換：
index 壞了重建、儲存區搬家、resolver 重寫，呼叫端一行程式都不用改。

每一章的資料夾都帶著這個檔案和之前的所有程式，在同一個 `engine.py` 上一章一章往下長。
diff 相鄰兩章的 `src/`，看到的就是那一章的機制。

---

## 各系統做法

Claude Code 和 Hermes 都是本機的單租戶工具，所以 tenant 欄位在它們身上看起來可有可無。
track 的合約把它列為必填：production 是一套系統同時服務很多租戶。

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **Pros** | memory 照專案分開存，repo 之間不會互相污染。 | 一份 profile 跟著使用者走，在哪個管道講話都認得你。 |
| **Cons** | 同一個使用者跨專案的事實被拆散在不同儲存區。 | 沒有照專案分開：使用者做的所有事都裝在同一個桶子裡。 |
| **Why** | memory 服務的是工作目錄：context 就是專案。 | memory 服務的是關係：context 就是這個人。 |
| **How: scope 單位** | 每個使用者、每個專案目錄各一份。 | 每個使用者一份，跨管道共用。 |
| **How: 隔離** | 照專案路徑分開的 memory 目錄。 | 本機資料庫裡按使用者分開的狀態。 |
| **How: 租戶** | 單一租戶：這台機器。 | 單一租戶：架設者自己的部署。 |

---

## 哪裡會出錯

- **完全沒有 scope：**最經典的事故：A 使用者的資料出現在 B 使用者的對話裡。
  scope 是每個動詞的必填參數，不是誰想到才加的過濾條件。
- **scope 寫在內文裡：**「Marcus 的 staging key 是⋯」存進去了，卻沒掛在任何 user 底下。
  過濾器讀不懂內文。scope 是結構化欄位，寫入當下就要標好。
- **scope 切太粗：**只切到 tenant，同一個 tenant 的使用者就會看到彼此的資料。
  user 和 agent 欄位第一天就要放進去，就算暫時都是 `None`。
- **呼叫端繞過介面：**某個 dashboard 直接查 index，從此 index 一重建，dashboard 就跟著壞。
  所有讀寫都走那三個動詞。
- **只有介面、沒有保證：**三個動詞本身什麼都不保證。
  合約真正的內容是那條鐵律（事件留著、view 可重建），後面每一章的測試就是在逐一驗它。

---

## 可執行程式

[`src/`](src/) 是整條鏈的起點，後面每一章都會帶著它往下走：

- [`contract.py`](src/contract.py)：`Scope` 和 `MemoryEngine` protocol。
- [`test.py`](src/test.py)：驗 scope 可以當 key、建立後不可改，用玩具 engine 驗結構相容，
  再用迷你例子驗按 scope 過濾的 recall。

```bash
python sections/01-memory-contract/src/test.py   # offline checks, no key
```

這一章完全不會呼叫模型，所以沒有 `demo.py`。

---

## 出處

- [MemMachine](https://arxiv.org/abs/2604.04853)：把 memory 當成一個子系統，用同一個介面服務多個使用者和 agent。
- [Claude Code memory](https://docs.claude.com/en/docs/claude-code/memory)：按專案和按使用者兩種 scope 的檔案式 memory。
- [Production memory track](../../README.zh-TW.md)：這一章實作的第 1 章和核心抽象。
