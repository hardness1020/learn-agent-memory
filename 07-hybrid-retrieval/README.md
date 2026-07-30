# 7 · Hybrid retrieval

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

> One ranker is never enough. Run cheap candidate channels in parallel and fuse their rankings.

This page covers lifecycle stage 8 of the [Production memory](../README.md) track, Retrieve:
finding the few memories a turn actually needs.

A channel is one way of producing candidates: embedding similarity, keyword match, and recency each count as one.
Any single channel has a blind spot. Embeddings miss exact names, dates, and negations.
Keywords miss paraphrase. Recency misses old but relevant facts.
A memory question can be any of "what is our CI provider" (a name), "what did we decide last week" (a time),
or "what went wrong with deploys before" (a pattern). No one channel answers all three.

[Section 9](https://github.com/hardness1020/awesome-agent-architecture/tree/main/sections/09-memory/) retrieved two ways already: an LLM selector over the index,
and keyword search over raw history. This stage generalizes that into a pipeline:

1. Run several cheap candidate channels in parallel.
2. Fuse their rankings without tuning cross-channel weights.
3. Degrade gracefully: a channel that breaks must not empty the result.
4. Stay honest: a channel that ran and matched nothing is not the same as a channel that broke.
5. Keep the index rebuildable, because it is a view (stage 7): derived from the records, never the source of truth.

---

## Mechanism

The simplest version: two channels and a rank fuser.

Three moving parts:

- **Channels**: independent candidate generators. Offline: keyword (FTS5, bm25) and recency.
  Live: dense embeddings, graph traversal, and temporal filters join the same interface, a ranked list of ids.
- **Fusion**: reciprocal rank fusion (RRF). Each channel contributes `1 / (K + rank)` per item.
- **The index**: one FTS table derived from typed records. Dropped and rebuilt at will.

RRF is the reason channels stay cheap to add. Ranks are comparable across channels
even when raw scores are not, so there is no normalization and no weight tuning:

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

An id ranked by two channels outscores an id ranked by one. That is the whole hybrid effect:
agreement between independent signals is evidence of relevance.

The channels share one derived table:

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

The full pipeline from the track README maps onto this skeleton:

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

Two live-only steps matter enough to name:

| | Contextual expansion | Agentic retrieval |
| --- | --- | --- |
| **What it does** | Pull the events around a hit from the event ledger (stage 2). | Store trajectories as files; a coding agent searches them in a sandbox. |
| **Evidence** | MemMachine: retrieval depth and source expansion often beat better ingestion chunking. | AgentRunbook-C beats RAG baselines on LongMemEval-V2. |
| **Cost** | More injected tokens. | Latency. Some questions are repository research, not one nearest-neighbor lookup. |

How it integrates: the index derives from typed records (stage 4), and records carry scope
stamped at write time, so scope filtering happened before any channel runs.
Index maintenance stays on the write path (stage 7's split), so a query only reads.
Rebuilding after corruption is one replay (stage 2's guarantee).
The fused hits go to context assembly (stage 9), each carrying its kind, score,
recorded time, and source event ids, not as bare strings.

### What Changed

In section 9, recall was one channel at a time, chosen by who triggered it.
Here channels run together, disagree safely, and a new channel is one more ranked list, not a redesign.

---

## Per system

| | Graphiti | MemMachine |
| --- | --- | --- |
| **Pros** | Relational and temporal questions resolve in one search, no LLM call. | A nucleus hit expands into its episode: the answer arrives with context. |
| **Cons** | Needs entity extraction and a graph store kept in sync. | Expansion inflates injected tokens. Full episodes must stay retained. |
| **Why** | "Who did what, when" needs edges, not nearest neighbors. | Answers usually live around the hit, not inside one chunk. |
| **How: channels** | Semantic embeddings, keyword, and graph traversal, fused. | Profile lookup plus episodic search over full history. |
| **How: temporal** | Time-aware edges filter facts valid at the queried moment. | Episodes keep their timeline; expansion follows it. |
| **How: rerank** | Fused ranking over the parallel searches. | Depth and formatting tuned over ingestion cleverness. |

---

## Failure modes

- **One channel dominates.** A verbose channel floods the fusion. RRF caps each channel's influence by rank,
  not score; keep per-channel `k` small and equal.
- **No channel finds it.** The fact exists but no signal matches. Add a channel (temporal, entity) instead of
  tuning the existing ones; that is what the fusion interface is for.
- **The index drifts from the records.** Deletes or schema changes leave ghosts. Rebuild from records on any doubt;
  it is a view, and rebuilds are cheap by design.
- **Precision collapses at high k.** More candidates means more noise downstream. Fuse at small k and let
  assembly's budget (stage 9) do the final cut.
- **Latency creeps.** Channels are only parallel if actually run in parallel. Keep each channel one indexed query,
  and push slow work (expansion, agentic search) behind a routing decision.

---

## Runnable

[`src/`](src/) carries 06 forward and adds:

- [`retrieve.py`](src/retrieve.py): RRF `fuse` and the fused `retrieve` over stage 7's channels.
  The index itself now lives in [6 · Index views](../06-index-views/).
- [`engine.py`](src/engine.py): the write path reindexes a scope after changing its records;
  `retrieve()` only reads, fusing the channels over the stored view.
- [`test.py`](src/test.py): channel behavior, fusion favoring cross-channel agreement, a no-match query
  returning nothing while a broken channel still degrades, wipe-then-rebuild returning identical results,
  and scope-isolated retrieval through the engine.

```bash
python tracks/production-memory/07-hybrid-retrieval/src/test.py   # offline checks, no key
```

Offline, the channels are keyword and recency. Live, dense and graph channels join the same `fuse` call.

---

## Sources

- [Graphiti / Zep](https://arxiv.org/abs/2501.13956): incremental temporal knowledge graph, fused search without an LLM call.
- [MemMachine](https://arxiv.org/abs/2604.04853): contextual retrieval, depth and source expansion over ingestion cleverness.
- [HippoRAG](https://arxiv.org/abs/2405.14831): Personalized PageRank as a one-step multi-hop channel.
- [LongMemEval-V2 / AgentRunbook-C](https://arxiv.org/abs/2605.12493): agentic file retrieval as the deep channel.
- [Production memory track](../README.md): the lifecycle this stage belongs to.
