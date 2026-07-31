"""Consolidation offline checks: propose, validate, commit, and what it refuses.

    python sections/06-consolidation/src/test.py
"""
import tempfile
from dataclasses import replace
from pathlib import Path

from consolidate import (ABSTRACTION, COMPRESSION, Proposal, Rejected, apply,
                         check, consolidate, propose_compression)
from contract import Scope
from engine import Engine
from ledger import Ledger
from policy import Candidate
from records import TypedStore, make_record
from resolve import ABSTRACT, SUPERSEDE, valid_at

RECORDED = "2025-01-01T00:00:00+00:00"
AT = "2026-01-01T00:00:00+00:00"       # a pass runs after the records it merges


def rec(content, scope, events=("ev-1",), confidence=0.8, kind="semantic", claim_key=""):
    """Fixed clock, so a consolidation at AT is never earlier than what it closes."""
    r = make_record(scope, kind, "fact", content, events, confidence, claim_key=claim_key)
    return replace(r, recorded_at=RECORDED, valid_from=RECORDED)


def test():
    marcus, other = Scope("acme", "marcus"), Scope("globex", "marcus")

    # the validator refuses every shape that would lose evidence
    a = rec("Marcus prefers Python for scripting", marcus, ("ev-1",))
    b = rec("Marcus prefers Python for small scripts", marcus, ("ev-2",))
    store = TypedStore([a, b])
    for bad, why in (
            (Proposal("guessing", "x", (a.id, b.id)), "unknown level"),
            (Proposal(COMPRESSION, "   ", (a.id, b.id)), "empty content"),
            (Proposal(COMPRESSION, "x", (a.id,)), "single source"),
            (Proposal(COMPRESSION, "x", (a.id, a.id)), "same source twice"),
            (Proposal(COMPRESSION, "x", (a.id, "ghost")), "source does not exist")):
        try:
            check(bad, store)
            raise AssertionError(f"validator accepted {why}")
        except Rejected:
            pass

    # never across scopes, however similar the wording
    cross = TypedStore([a, rec("Marcus prefers Python for scripting", other)])
    try:
        check(Proposal(COMPRESSION, "x", tuple(r.id for r in cross.records)), cross)
        raise AssertionError("validator merged two tenants")
    except Rejected:
        pass

    # never a record that is already closed
    closed = TypedStore([replace(a, status="superseded", superseded_at=AT), b])
    try:
        check(Proposal(COMPRESSION, "x", (a.id, b.id)), closed)
        raise AssertionError("validator reused a closed record")
    except Rejected:
        pass

    # apply: the derived record inherits provenance and the weakest confidence
    store = TypedStore([a, replace(b, confidence=0.4)])
    ops = apply(Proposal(COMPRESSION, "Marcus prefers Python", (a.id, b.id)), store, AT)
    derived = [r for r in store.records if r.status == "active"]
    assert len(derived) == 1
    assert derived[0].source_event_ids == ("ev-1", "ev-2")   # both, chaining to the ledger
    assert derived[0].confidence == 0.4                      # no confidence invented
    assert derived[0].epistemic_type == "inference"          # derived, never a fact
    assert "consolidated" in derived[0].tags
    assert [o.op for o in ops] == [ABSTRACT, SUPERSEDE, SUPERSEDE]
    assert all(r.status == "superseded" for r in store.records if r.id != derived[0].id)

    # the proposer groups what repeats and leaves singletons alone
    store = TypedStore([a, b, rec("Deploys run on Thursdays", marcus, ("ev-3",))])
    proposals = propose_compression(store.records)
    assert len(proposals) == 1
    assert set(proposals[0].source_ids) == {a.id, b.id}

    # a full pass reports what it refused, it does not swallow it
    store = TypedStore([a, b])
    report = consolidate(store, marcus, AT,
                         proposer=lambda rs: [Proposal(ABSTRACTION, "x", (a.id,))])
    assert report == {"proposed": 1, "applied": 0,
                      "rejected": [("x", "an abstraction of fewer than two records is a rewrite")],
                      "operations": []}

    # engine end to end: raw evidence survives a consolidation pass untouched
    root = Path(tempfile.mkdtemp())
    eng = Engine(Ledger(root / "events.db"))
    e1 = eng.observe(marcus, "user_message", "I mostly reach for Python when scripting")
    e2 = eng.observe(marcus, "user_message", "for small scripts I use Python")
    eng.propose(marcus, Candidate("Marcus prefers Python for scripting", "semantic", (e1,)))
    eng.propose(marcus, Candidate("Marcus prefers Python for small scripts", "semantic", (e2,)))
    before = len(eng.ledger.read(marcus, event_type="user_message"))

    report = eng.consolidate(marcus)
    assert report["applied"] == 1
    active = [r for r in eng.store.records if r.status == "active"]
    assert len(active) == 1 and active[0].source_event_ids == tuple(sorted((e1, e2)))
    assert len(eng.store.records) == 3                       # merged, not deleted
    assert len(eng.ledger.read(marcus, event_type="user_message")) == before
    assert [e.content.split(":")[0] for e in
            eng.ledger.read(marcus, event_type="memory_operation")][-3:] == \
        ["abstract", "supersede", "supersede"]

    # a second pass has nothing left to merge
    assert eng.consolidate(marcus)["proposed"] == 0

    # a bad kind is refused by the validator, not by make_record mid-mutation
    good = rec("Deploys are frozen on Fridays", marcus, ("ev-9",))
    also = rec("Deploys are frozen every Friday", marcus, ("ev-10",))
    store = TypedStore([good, also])
    report = consolidate(store, marcus, AT, proposer=lambda rs: [
        Proposal(COMPRESSION, "Deploys are frozen on Fridays", (good.id, also.id)),
        Proposal(COMPRESSION, "Anything", (good.id, also.id), kind="fact")])
    assert report["applied"] == 1
    assert report["rejected"] == [("Anything", "unknown kind: fact")]
    assert len(store.records) == 3                       # one derived, two closed

    # the derived record keeps the claim key, so the profile view survives a merge
    k1 = rec("Marcus prefers Python for scripting", marcus, ("ev-11",), claim_key="marcus:lang")
    k2 = rec("Marcus prefers Python for small scripts", marcus, ("ev-12",), claim_key="marcus:lang")
    store = TypedStore([k1, k2])
    apply(Proposal(COMPRESSION, "Marcus prefers Python", (k1.id, k2.id)), store, AT)
    merged = [r for r in store.records if r.status == "active"][0]
    assert merged.claim_key == "marcus:lang"
    assert merged.valid_from == AT                       # not true before the pass
    assert not valid_at([merged], "2020-01-01T00:00:00+00:00")

    # different keys are different claims: refused, so neither key is lost
    j1 = rec("Marcus prefers Python for scripting", marcus, ("ev-13",), claim_key="marcus:lang")
    j2 = rec("Marcus prefers Python for small scripts", marcus, ("ev-14",), claim_key="marcus:tool")
    store = TypedStore([j1, j2])
    try:
        apply(Proposal(COMPRESSION, "Marcus prefers Python", (j1.id, j2.id)), store, AT)
        raise AssertionError("merged two different claims")
    except Rejected:
        pass
    assert propose_compression(store.records) == []       # never proposed either
    assert all(r.status == "active" for r in store.records)

    # a tenant-wide pass merges each user's own records instead of starving both
    alice = Scope("acme", "alice")
    store = TypedStore([rec("Deploys are frozen on Fridays", alice, ("ev-15",)),
                        rec("Deploys are frozen on Friday afternoons", marcus, ("ev-16",)),
                        rec("Deploys are frozen every Friday", marcus, ("ev-17",))])
    report = consolidate(store, Scope("acme"), AT)
    assert report["rejected"] == []                      # no cross-scope group proposed
    assert report["applied"] == 1                        # marcus's pair merged
    assert sorted(r.status for r in store.records) == \
        ["active", "active", "superseded", "superseded"]

    # what the gate defers, consolidation can actually merge: one measure, one threshold
    root = Path(tempfile.mkdtemp())
    eng = Engine(Ledger(root / "defer.db"))
    e1 = eng.observe(marcus, "user_message", "deploys always wait on the migration lock")
    eng.propose(marcus, Candidate(
        "Staging deploys wait for the migration lock before starting", "semantic", (e1,)))
    d = eng.propose(marcus, Candidate(
        "Deploys wait for the migration lock in every environment", "semantic", (e1,)))
    assert d.action == "defer"                            # 0.75: near-duplicate band
    assert [r for r in eng.store.records if "deferred" in r.tags]
    assert eng.consolidate(marcus)["applied"] == 1        # the deferred pair merges
    assert len([r for r in eng.store.records if r.status == "active"]) == 1

    print("05 consolidation: ok")


if __name__ == "__main__":
    test()
