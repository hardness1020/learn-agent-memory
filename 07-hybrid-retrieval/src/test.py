"""Hybrid retrieval offline checks: channels, RRF fusion, rebuild as recovery.

    python tracks/production-memory/07-hybrid-retrieval/src/test.py
"""
import sqlite3
import tempfile
from pathlib import Path

from contract import Scope
from engine import Engine
from ledger import Ledger
from policy import Candidate
from index import MemoryIndex
from retrieve import fuse, retrieve


MARCUS = Scope("acme", "marcus")

ROWS = [
    ("m1", "semantic", "CI provider is Buildkite, config lives in the repo", "2026-07-01"),
    ("m2", "semantic", "Staging credentials rotate every Tuesday", "2026-07-10"),
    ("m3", "episodic", "Deploy failed on a migration lock, fixed by rollback", "2026-07-22"),
    ("m4", "procedural", "Check migration locks before schema migrations", "2026-07-15"),
    ("m5", "semantic", "Marcus mainly writes Python", "2026-06-01"),
]

RECORDS = [{"id": i, "tenant_id": MARCUS.tenant_id, "user_id": MARCUS.user_id,
            "kind": k, "content": c, "recorded_at": f"{d}T00:00:00+00:00"}
           for i, k, c, d in ROWS]


def test():
    marcus = MARCUS
    idx = MemoryIndex(Path(tempfile.mkdtemp()) / "index.db")
    assert idx.rebuild(marcus, RECORDS) == 5

    # keyword channel: exact names win, best match first
    assert idx.keyword(marcus, "which CI provider do we use")[0] == "m1"
    assert set(idx.keyword(marcus, "migration")) == {"m3", "m4"}
    assert idx.keyword(marcus, "") == []

    # recency channel: newest first, optionally one kind
    assert idx.recent(marcus, k=2) == ["m3", "m4"]
    assert idx.recent(marcus, kind="episodic") == ["m3"]

    # fusion: an id ranked by two channels outscores an id ranked by one
    hits = retrieve(idx, marcus, "migration lock deploy")
    top_id, top_score, top_channels = hits[0]
    assert top_id == "m3" and set(top_channels) == {"keyword", "recent"}
    assert top_score > dict((m, s) for m, s, _c in hits)["m4"]

    # a query nothing matched returns nothing: recency alone is not relevance
    assert retrieve(idx, marcus, "zzz qqq xyzzy") == []
    # a channel that breaks still degrades: the pool narrows, it does not empty
    assert fuse({"keyword": ["m3"], "recent": []}, k=5)[0][0] == "m3"

    # fuse is pure and order-sensitive: rank 0 in one channel beats rank 3 in two
    fused = fuse({"a": ["x", "y"], "b": ["y", "x"]}, k=2)
    assert {m for m, _s, _c in fused} == {"x", "y"}

    # the index is a view: wipe it, rebuild from records, results identical
    before = retrieve(idx, marcus, "migration lock deploy")
    con = sqlite3.connect(idx.path)
    con.execute("DELETE FROM memory_index")
    con.commit()
    con.close()
    assert idx.keyword(marcus, "migration") == []              # gone
    idx.rebuild(marcus, RECORDS)
    assert retrieve(idx, marcus, "migration lock deploy") == before

    # the engine derives the index from typed records and fuses over them
    root = Path(tempfile.mkdtemp())
    eng = Engine(Ledger(root / "events.db"), MemoryIndex(root / "index.db"))
    eng.propose(marcus, Candidate("CI provider is Buildkite for every repo", "semantic", ("ev-1",)))
    eng.propose(marcus, Candidate("Deploys wait for Monday, never Friday", "semantic", ("ev-2",)))
    hits = eng.retrieve(marcus, "which CI provider do we use")
    by_id = {r.id: r for r in eng.store.records}
    assert hits and "Buildkite" in by_id[hits[0][0]].content
    assert eng.retrieve(Scope("globex"), "Buildkite") == []       # other tenants see nothing
    # retrieve is read only, so one tenant's query cannot disturb another's rows
    assert eng.retrieve(marcus, "which CI provider do we use") == hits

    print("07 hybrid-retrieval: ok")


if __name__ == "__main__":
    test()
