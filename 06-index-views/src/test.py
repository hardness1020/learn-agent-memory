"""Index view offline checks: rebuild, the three views, and what survives a wipe.

    python tracks/production-memory/06-index-views/src/test.py
"""
import tempfile
from dataclasses import replace
from pathlib import Path

from contract import Scope
from engine import Engine
from index import MemoryIndex, profile
from ledger import Ledger
from policy import Candidate
from records import make_record

JAN = "2025-01-01T00:00:00+00:00"
JUN = "2025-06-01T00:00:00+00:00"
DEC = "2025-12-01T00:00:00+00:00"


def rec(content, scope, at, claim_key=""):
    r = make_record(scope, "semantic", "fact", content, ("ev-1",), 0.8, claim_key=claim_key)
    return replace(r, recorded_at=at)


def rows(records):
    return [{"id": r.id, "tenant_id": r.scope.tenant_id, "user_id": r.scope.user_id,
             "kind": r.kind, "content": r.content, "recorded_at": r.recorded_at}
            for r in records]


def test():
    marcus = Scope("acme", "marcus")
    root = Path(tempfile.mkdtemp())

    a = rec("Staging runs in eu-west", marcus, JAN, "staging:region")
    b = rec("Deploys are frozen on Fridays", marcus, JUN)
    c = rec("Marcus prefers Python", marcus, DEC, "marcus:language")
    index = MemoryIndex(root / "index.db")

    # the sparse view: keyword search, ranked, with the id column kept out of matching
    assert index.rebuild(marcus, rows([a, b, c])) == 3
    assert index.keyword(marcus, "where does staging run") == [a.id]
    assert index.keyword(marcus, a.id) == []                    # ids are not searchable text
    assert index.keyword(marcus, "") == []                      # no words, no guessing

    # the temporal view: a window, ordered by time, not by relevance
    assert index.between(marcus, JAN, JUN) == [a.id]
    assert index.between(marcus, JAN, "2026-01-01T00:00:00+00:00") == [c.id, b.id, a.id]
    assert index.recent(marcus, k=2) == [c.id, b.id]

    # rebuild is the recovery path: wipe it, rebuild it, lose nothing
    index.rebuild(marcus, [])
    assert index.count(marcus) == 0 and index.keyword(marcus, "staging") == []
    index.rebuild(marcus, rows([a, b, c]))
    assert index.keyword(marcus, "where does staging run") == [a.id]

    # the profile view: one entry per claim key, newest wins, unkeyed records skipped
    assert profile([a, b, c]) == {"staging:region": "Staging runs in eu-west",
                                  "marcus:language": "Marcus prefers Python"}
    newer = rec("Staging runs in us-east", marcus, DEC, "staging:region")
    assert profile([a, newer])["staging:region"] == "Staging runs in us-east"
    assert profile([replace(a, status="superseded", superseded_at=DEC)]) == {}

    # engine end to end: views derive from records, and a wipe costs nothing
    eng = Engine(Ledger(root / "events.db"), MemoryIndex(root / "eng.db"))
    e1 = eng.observe(marcus, "user_message", "we moved staging to eu-west")
    e2 = eng.observe(marcus, "user_message", "deploys are frozen on Fridays now")
    eng.propose(marcus, Candidate("Staging now runs in eu-west", "semantic", (e1,),
                                  claim_key="staging:region"))
    eng.propose(marcus, Candidate("Deploys are frozen on Fridays", "semantic", (e2,)))

    assert eng.reindex(marcus) == 2
    assert eng.index.keyword(marcus, "staging region") != []
    assert list(eng.profile(marcus)) == ["staging:region"]

    eng.index.rebuild(marcus, [])                                # simulate a corrupted index
    assert eng.index.keyword(marcus, "staging") == []
    assert eng.reindex(marcus) == 2                      # rebuilt from records
    assert eng.index.keyword(marcus, "staging region") != []
    assert len(eng.ledger.read(marcus, event_type="user_message")) == 2   # ledger untouched

    # another tenant's records never enter this scope's view
    assert eng.reindex(Scope("globex")) == 0

    # one scope's rebuild leaves every other scope's rows in place
    zed = Scope("globex", "zed")
    ez = eng.observe(zed, "user_message", "globex deploys run on Sunday evenings")
    eng.propose(zed, Candidate("Globex deploys run on Sunday evenings", "semantic", (ez,)))
    assert eng.reindex(zed) == 1
    assert eng.index.keyword(marcus, "staging region") != []      # marcus survived it
    assert eng.index.keyword(zed, "staging region") == []         # and stays separate
    assert eng.index.count(marcus) == 2 and eng.index.count(zed) == 1

    # the temporal view is scoped too, so "what changed" cannot cross a tenant
    window = ("2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00")
    assert len(eng.between(marcus, *window)) == 2
    assert len(eng.between(zed, *window)) == 1

    print("06 index-views: ok")


if __name__ == "__main__":
    test()
