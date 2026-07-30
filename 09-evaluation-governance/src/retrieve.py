"""Hybrid retrieval (production memory track, stage 8: Retrieve).

One ranker is never enough. Embeddings miss exact names, dates, and
negations; keywords miss paraphrase; recency misses old but relevant facts.
So retrieval runs several cheap candidate channels and fuses their rankings:
  keyword : bm25 over the sparse view. exact names and dates.
  recent  : newest records first. what just happened.
  (live: dense embeddings, graph, and temporal channels join the same fusion)

The channels themselves belong to stage 7 (index.py). This module only
decides how to combine rankings, which keeps two failures apart: a missing
candidate is an index bug, a badly ordered one is a fusion bug.

Fusion is reciprocal rank fusion (RRF): each channel contributes
1 / (RRF_K + rank) per item. Ranks are comparable across channels even when
their raw scores are not, so there is nothing to normalize or tune.
"""
from __future__ import annotations

from index import TOP_K

RRF_K = 60          # the standard RRF constant; larger flattens rank differences


def fuse(channels, k=TOP_K) -> list[tuple]:
    """channels: {name: [memory_id, ...]} in rank order. Returns
    (memory_id, score, channel_names) tuples, best first."""
    scores, seen_in = {}, {}
    for name, ranking in channels.items():
        for rank, mid in enumerate(ranking):
            scores[mid] = scores.get(mid, 0.0) + 1 / (RRF_K + rank + 1)
            seen_in.setdefault(mid, []).append(name)
    best = sorted(scores, key=scores.get, reverse=True)[:k]
    return [(mid, scores[mid], tuple(seen_in[mid])) for mid in best]


def retrieve(index, scope, query, k=TOP_K) -> list[tuple]:
    """The fused pipeline over stage 7's views.

    Channels degrade independently, so a channel that breaks narrows the
    candidate pool instead of emptying it. A channel that runs and matches
    nothing is different: `recent` ranks by time and never consults the query,
    so on its own it is not evidence of relevance. Fusing it anyway meant an
    off-topic turn still got the newest memories injected, spending the budget
    and contradicting stage 9's rule of injecting nothing rather than noise.
    """
    keyword = index.keyword(scope, query, k)
    if not keyword:
        return []
    return fuse({"keyword": keyword, "recent": index.recent(scope, k)}, k)
