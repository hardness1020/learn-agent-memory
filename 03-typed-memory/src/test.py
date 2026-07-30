"""Typed memory offline checks: validation, kind routing, epistemic split.

    python tracks/production-memory/03-typed-memory/src/test.py
"""
import tempfile
from dataclasses import replace
from pathlib import Path

from contract import Scope
from engine import Engine
from ledger import Ledger
from policy import Candidate
from records import MemoryRecord, TypedStore, grounded, make_record


def test():
    marcus = Scope("acme", "marcus")
    rec = lambda kind, etype, content, conf=0.9: make_record(
        marcus, kind, etype, content, ("ev-1",), conf)

    # the constructor validates: no record exists without passing these checks
    r = rec("semantic", "fact", "Staging credentials rotate every Tuesday")
    assert r.id and r.recorded_at and r.status == "active"
    for bad in (lambda: rec("vibes", "fact", "x"),
                lambda: rec("semantic", "hunch", "x"),
                lambda: rec("semantic", "fact", "x", conf=1.2),
                lambda: make_record(marcus, "semantic", "fact", "x", (), 0.5)):
        try:
            bad()
            raise AssertionError("validation should have rejected")
        except ValueError:
            pass

    # three kinds answer three questions; the store routes by kind and scope
    store = TypedStore()
    store.add(rec("episodic", "evidence", "Deploy hit a migration lock, fixed by rollback"))
    store.add(rec("semantic", "preference", "Marcus mainly writes Python"))
    store.add(rec("procedural", "fact", "Check migration locks before schema migrations"))
    store.add(make_record(Scope("globex", "dana"), "semantic", "fact", "Dana ships Go", ("ev-9",), 0.8))

    assert [r.kind for r in store.current(marcus, "episodic")] == ["episodic"]
    assert len(store.current(marcus, "semantic")) == 1          # dana's tenant is invisible
    assert store.current(Scope("acme"), "semantic")             # tenant-wide read still works

    # the epistemic axis: what happened never blends with what the system thinks
    guess = store.add(rec("semantic", "inference", "Marcus probably dislikes Java", conf=0.4))
    beliefs = store.current(marcus, "semantic")
    assert len(beliefs) == 2
    assert guess not in grounded(beliefs)                       # inference filtered out
    assert all(r.epistemic_type in ("evidence", "fact") for r in grounded(store.records))

    # status gates visibility: superseded records leave current() but keep existing
    store.add(replace(rec("semantic", "fact", "Old office was in San Diego"), status="superseded"))
    assert all("San Diego" not in r.content for r in store.current(marcus, "semantic"))
    assert any("San Diego" in r.content for r in store.records)   # still there for audit

    # the engine now stores gate survivors as validated typed records
    eng = Engine(Ledger(Path(tempfile.mkdtemp()) / "engine.db"))
    d = eng.propose(marcus, Candidate("Staging rotates credentials every Tuesday",
                                      "semantic", ("ev-7",)), epistemic_type="fact")
    assert d.action == "store"
    stored = eng.store.current(marcus, "semantic")[0]
    assert stored.epistemic_type == "fact"
    assert stored.source_event_ids == ("ev-7",)                   # provenance survives the gate
    assert stored.confidence == d.confidence                      # the decision's confidence carries over
    assert eng.ledger.read(marcus, event_type="memory_decision")  # and the decision is an event

    print("03 typed-memory: ok")


if __name__ == "__main__":
    test()
