# 7 · Index views

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> An index is not the memory. It is a view, and a view that breaks is rebuilt, not repaired.

This page covers lifecycle section 7 of the [Production memory](../../README.md) track: Index.
A view is a read structure built from the records. The search index, the current-state profile,
and the linked wiki pages are all views, and every one of them derives from the same ledger.

By now a record has a type, a timeline, and a consolidation history. It is still just a row.
Different questions want that row in completely different shapes. "What do I know about deploys"
wants ranked text. "What changed last week" wants a time window. "Who is this user" wants no search at all,
just current state. "Show me how these facts connect" wants pages and links.

Each shape needs its own data structure, so one index forced to answer every question
makes the system slow and wrong at once.

---

## Mechanism

The simple version: one write path, several read views, and the only authoritative copy is the ledger.

```text
raw event log    SQLite, append only, never rebuilt
  → records      typed, resolved, consolidated (sections 4 to 6)
    → sparse     FTS5 / bm25            exact names and dates
    → temporal   recorded_at ranges     what changed in a window
    → profile    one line per claim     current state, no search
```

Live systems add a dense view (embeddings), a wiki, an entity graph, and a procedure view for skills and runbooks.
The shape of the list matters more than its length: every arrow points away from the ledger,
and no arrow points back. That one-way flow is the rule this section exists for.
A corrupted index is not an incident, it is a rebuild:

```python
def rebuild(self, scope, records) -> int:
    con.execute(f"DELETE FROM memory_index WHERE {_SCOPE}", _args(scope))
    con.executemany("INSERT INTO memory_index VALUES (?, ?, ?, ?, ?, ?)", ...)
```

Rebuild deletes before it inserts, and the scope column decides how far the delete reaches.
Without the column, rebuilding for one tenant clears the whole table, then inserts only that tenant's rows back.
From section 8 on every write ends in a rebuild, so this is not a rare accident: whoever writes last
is the only tenant left in the table. Everyone else gets empty results, not an error, so nobody notices.
Every read and rebuild on this table takes a scope, the same rule the ledger follows.

A schema migration, a corrupted index, a new tokenizer: the fix for all three is the same call to `rebuild`.
There is nothing to repair: every row in the index can be recomputed from the records.

Not every view is stored. Search needs an inverted index, so the sparse and temporal views live in SQLite.
The profile view is computed on read, because it is small and materializing it would add
a second thing to invalidate:

```python
def profile(records) -> dict:
    """Current state, one entry per claim key, newest wins."""
```

The profile view is where section 5's claim key (the stable name a fact updates) pays off a second time.
Without a key there is nothing to project onto, and "current state" degrades into "most recent few memories".

Data flow: records go in, views come out, and the engine can rebuild any of them at any time.
`reindex` takes one scope's active records and rewrites the sparse view from scratch.
This section's src only provides the verb: nothing calls `reindex` on its own.
From section 8 on the write path calls it: every pass that changes records reindexes its scope,
and `retrieve` only reads the table. That is the three-path split a live system runs.
The hot path answers queries and never rebuilds.
The warm path maintains the index as records change; here that is a full scope rebuild per write,
a live system inserts a row per new record and deletes the rows of closed ones,
so the cost follows the rows that changed, not the table.
The full `rebuild` stays on the cold path, for the three cases above: schema change, new tokenizer, corruption.
On every path the table itself is not optional: bm25 ranking needs an inverted index to run on.
Nothing calls the model, and nothing here decides what is relevant.

### Other Views

The wiki and the graph are the two view families that come up most often.

| | Wiki view | Graph view |
| --- | --- | --- |
| **Form** | Linked markdown pages, one per topic. | Entity nodes and typed relation edges. |
| **Precondition** | Markdown is the store, edited with ordinary file tools. | Multi-hop questions arrive often enough to pay for extraction. |
| **Connections** | Links written into the page text as pages are edited. | Edges extracted from every event by a pipeline. |
| **Answers** | Agentic research, human audit, browsing. | Relational, temporal, and multi-hop questions in one pass. |
| **Breaks when** | Nobody writes links, so every page is an island. | Edges are wrong, so every walk misroutes. |
| **Named systems** | [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), Claude Code auto memory. | [HippoRAG](https://arxiv.org/abs/2405.14831), [Graphiti](https://arxiv.org/abs/2501.13956). |

Since neither precondition holds, this section builds neither.

### What Changed

Compared with section 6, indexing and retrieval are now separate concerns.
This section builds channels that return candidates. Section 8 decides how to rank across them,
which is a different problem with a different failure mode,
and merging the two is how a retrieval bug becomes an indexing bug.

---

## Per system

| | Claude Code auto memory | HippoRAG |
| --- | --- | --- |
| **Pros** | No infrastructure. A human can read the store to audit it, and edit it in a text editor. | Multi-hop questions resolve in one retrieval pass, not several rounds. |
| **Cons** | Search is whatever the file tools offer, so recall drops as the directory grows. | An extraction pipeline and a graph to keep current. Wrong edges misroute the walk. |
| **Why** | Memory the user cannot inspect is memory the user cannot correct. | Answers that span several facts should not need several retrieval rounds. |
| **How: unit** | Markdown files with an index file listing them. | Entity nodes and relation edges built from passages. |
| **How: connections** | Plain markdown links inside the page text. | Typed edges, walked by Personalized PageRank from the query's entities. |
| **How: rebuild** | Rewrite the files; the directory is small enough to regenerate. | Re-extract and re-index the corpus. |

---

## Failure modes

- **The index becomes the source of truth.** Something is written to the index and nowhere else,
  so a rebuild silently loses it. Every view derives from records, which derive from the ledger.
  If a rebuild loses data, the write path is the bug.
- **Stale views.** Consolidation closes records and the index still returns them, so retrieval surfaces
  memories that no longer exist. Reindex after any pass that changes records, not on a timer.
- **Everything materialized.** Five stored views mean five things to invalidate and five ways to disagree.
  Store what search needs; compute the small ones on read.
- **One shared index across tenants.** A view without scope columns leaks across the boundary at query time,
  which is worse than a store leak because it looks like relevance, and it makes every rebuild destructive:
  one scope's rebuild wipes another's rows. The columns are in the schema and every read filters on them.
- **Wiki links nobody manages.** Links nobody creates leave every page an island, so the view adds nothing
  over a plain listing, and links into closed records send the agent into nothing.
  A wiki view needs a link producer, and links rendered only to records that are still active.
- **Graph built for questions nobody asks.** Extraction and indexing cost real money per event,
  and a graph nobody queries multi-hop is a wiki with extra steps.
- **Index used to decide relevance.** A channel returns candidates; it does not rank the final answer.
  Fusion is section 8, and folding it in here makes both sections untestable.

---

## Runnable

[`src/`](src/) carries 05 forward and adds:

- [`index.py`](src/index.py): `MemoryIndex` with `rebuild`, `keyword`, `recent`, `between`, and `count`,
  each taking a scope, plus the `profile` view computed on read.
- [`engine.py`](src/engine.py): `reindex` rebuilds a scope's sparse view from records,
  `between` reads its temporal window, and `profile` projects the same records a second way.
- [`test.py`](src/test.py): keyword and temporal queries, a wipe followed by a rebuild that loses nothing,
  profile keyed by claim with superseded records excluded,
  and a scope whose reindex returns another tenant nothing.

```bash
python sections/07-index-views/src/test.py   # offline checks, no key
```

This section never calls the model, so there is no `demo.py`.

---

## Sources

- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): markdown wiki compiled from immutable sources.
- [HippoRAG](https://arxiv.org/abs/2405.14831): Personalized PageRank over an entity graph for multi-hop retrieval.
- [Graphiti](https://arxiv.org/abs/2501.13956): incrementally updated temporal knowledge graph.
- [Production memory track](../../README.md): the lifecycle this section belongs to.
