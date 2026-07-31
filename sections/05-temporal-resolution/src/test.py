"""Temporal resolution offline checks: conflict, supersede, retract, two clocks.

    python sections/05-temporal-resolution/src/test.py
"""
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from contract import Scope
from engine import Engine
from ledger import Ledger
from policy import Candidate
from records import MemoryRecord, TypedStore, make_record, validate
from resolve import (ADD, SUPERSEDE, as_of, conflicts, resolve, retract,
                     supersede, valid_at)

JAN = "2025-01-01T00:00:00+00:00"
JUN = "2025-06-01T00:00:00+00:00"
DEC = "2025-12-01T00:00:00+00:00"


def rec(content, scope, claim_key="", at=JAN, valid_from=None):
    """A record with a fixed clock, so the time-travel checks are deterministic."""
    r = make_record(scope, "semantic", "fact", content, ("ev-1",), 0.8, claim_key=claim_key)
    return replace(r, recorded_at=at, valid_from=valid_from or at)


def test():
    marcus, other = Scope("acme", "marcus"), Scope("globex", "marcus")

    # conflict: same claim key, different object, both active
    sd = rec("Lives in San Diego", marcus, "marcus:lives_in")
    sf = rec("Lives in San Francisco", marcus, "marcus:lives_in", at=JUN)
    assert conflicts(sf, [sd]) == [sd]
    assert conflicts(sf, [sf]) == []                        # never itself
    assert conflicts(rec("Writes Python", marcus), [sd]) == []   # no key, no conflict

    # supersede closes both clocks and keeps the record readable
    closed, op = supersede(sd, JUN, cause_id=sf.id)
    assert closed.status == "superseded" and closed.superseded_at == JUN
    assert closed.valid_to == JUN and closed.content == sd.content
    assert op.op == SUPERSEDE and op.cause_id == sf.id

    # retract is a different claim: it was never true, not "no longer true"
    wrong, op = retract(sd, JUN, "extraction error")
    assert wrong.status == "retracted" and wrong.valid_to is None
    assert op.reason == "extraction error"

    # resolve: the new claim opens, the old one closes, both operations returned
    store = TypedStore([sd])
    ops = resolve(store, sf, at=JUN)
    assert [o.op for o in ops] == [SUPERSEDE, ADD]
    assert [r.status for r in store.records] == ["superseded", "active"]

    # a claim never closes another tenant's identical claim
    store = TypedStore([rec("Lives in San Diego", other, "marcus:lives_in")])
    resolve(store, rec("Lives in San Francisco", marcus, "marcus:lives_in", at=JUN), at=JUN)
    assert [r.status for r in store.records] == ["active", "active"]

    # two clocks answer two different questions
    store = TypedStore([sd])
    resolve(store, sf, at=JUN)
    assert len(as_of(store.records, DEC)) == 1               # today: only San Francisco
    assert len(as_of(store.records, JAN)) == 1               # back then: only San Diego
    assert as_of(store.records, JAN)[0].content == "Lives in San Diego"
    assert len(valid_at(store.records, JAN)) == 1            # world time, same shape here

    # a record can be recorded today about last year: the clocks diverge
    backdated = rec("Lived in Austin", marcus, at=DEC, valid_from=JAN)
    assert valid_at([backdated], JUN) == [backdated]         # true in June
    assert as_of([backdated], JUN) == []                     # but unknown in June

    # validation rejects impossible timelines
    for bad in (dict(valid_from=DEC, valid_to=JAN),
                dict(recorded_at=DEC, superseded_at=JAN, status="superseded"),
                dict(superseded_at=DEC)):                    # active, yet superseded
        base = dict(id="x", scope=marcus, kind="semantic", epistemic_type="fact",
                    content="c", source_event_ids=("ev-1",), confidence=0.5,
                    recorded_at=JAN)
        try:
            validate(MemoryRecord(**{**base, **bad}))
            raise AssertionError(f"accepted an impossible timeline: {bad}")
        except ValueError:
            pass

    # engine end to end: a correction closes the old claim and lands in the ledger
    root = Path(tempfile.mkdtemp())
    eng = Engine(Ledger(root / "events.db"))
    eid = eng.observe(marcus, "user_message", "I moved to San Francisco last month")
    eng.propose(marcus, Candidate("Marcus lives in San Diego", "semantic", (eid,),
                                  claim_key="marcus:lives_in"))
    eng.propose(marcus, Candidate("Marcus lives in San Francisco", "semantic", (eid,),
                                  claim_key="marcus:lives_in"))
    statuses = sorted(r.status for r in eng.store.records)
    assert statuses == ["active", "superseded"]              # nothing was deleted
    ops = eng.ledger.read(marcus, event_type="memory_operation")
    assert [e.content.split(":")[0] for e in ops] == ["add", "supersede", "add"]
    now = datetime.now(timezone.utc).isoformat()
    assert len(eng.believed_at(marcus, now)) == 1            # one live claim today
    assert len(eng.believed_at(marcus, DEC)) == 0            # neither existed in 2025

    # a string event id is shredded into per-character ids, so it is refused
    try:
        make_record(marcus, "semantic", "fact", "Marcus lives here now", "abc123", 0.8)
        raise AssertionError("accepted one id string as a sequence of ids")
    except ValueError:
        pass

    # timestamps are compared as moments, not as text: this offset precedes JUN
    east = "2025-06-01T04:00:00+05:00"          # 23:00Z on May 31, before JUN
    later = rec("Recorded in June", marcus, at=JUN)
    assert as_of([later], east) == []                     # not yet known
    assert as_of([later], "2025-06-01T04:00:00-05:00") == [later]

    # supersede cannot strand a record outside every time-travel query
    late = rec("Recorded in December", marcus, "marcus:where", at=DEC)
    try:
        resolve(TypedStore([late]), rec("Superseding in June", marcus, "marcus:where"), at=JUN)
        raise AssertionError("superseded a record before it was recorded")
    except ValueError:
        pass

    # a tenant-level claim closes the user-level claim it corrects
    store = TypedStore([rec("Deploys are frozen on Fridays", marcus, "acme:freeze")])
    resolve(store, rec("Deploys are frozen on Mondays", Scope("acme"), "acme:freeze", at=JUN), at=JUN)
    assert [r.status for r in store.records] == ["superseded", "active"]

    # the audit queries see exactly what the live reads see, and no more
    root = Path(tempfile.mkdtemp())
    eng = Engine(Ledger(root / "two-users.db"))
    alice = Scope("acme", "alice")
    ea = eng.observe(alice, "user_message", "I deploy on Thursday mornings")
    eng.propose(marcus, Candidate("Marcus prefers Python for scripting", "semantic", (ea,)))
    eng.propose(alice, Candidate("Alice runs deploys on Thursday mornings", "semantic", (ea,)))
    now = datetime.now(timezone.utc).isoformat()
    assert len(eng._records(marcus)) == 1
    assert len(eng.believed_at(marcus, now)) == 1         # not alice's too
    assert len(eng.true_at(marcus, now)) == 1
    assert len(eng.believed_at(Scope("acme"), now)) == 2  # tenant-wide sees both

    # a keyed correction survives the duplicate rule even at 0.875 overlap
    eid = eng.observe(marcus, "user_message", "actually I switched to light mode")
    dark = Candidate("Marcus prefers dark mode in the editor and the terminal",
                     "semantic", (eid,), claim_key="marcus:theme")
    light = Candidate("Marcus prefers light mode in the editor and the terminal",
                      "semantic", (eid,), claim_key="marcus:theme")
    assert eng.propose(marcus, dark).action == "store"
    assert eng.propose(marcus, light).action == "store"   # a correction, not a duplicate
    assert eng.propose(marcus, light).action == "ignore"  # the same words are a duplicate
    themed = [r for r in eng.store.records if r.claim_key == "marcus:theme"]
    assert sorted(r.status for r in themed) == ["active", "superseded"]
    assert [r.content for r in themed if r.status == "active"] == [light.content]

    # sensitivity is checked before anything that can store, duplicate band included
    eng.propose(marcus, Candidate("Staging deploy token rotates every Tuesday",
                                  "semantic", (eid,)))
    secret = Candidate("Staging deploy token equals abc123xyz", "semantic", (eid,),
                       sensitive=True)
    assert eng.propose(marcus, secret).action == "require_approval"
    assert not [r for r in eng.store.records if "abc123xyz" in r.content]

    print("04 temporal-resolution: ok")


if __name__ == "__main__":
    test()
