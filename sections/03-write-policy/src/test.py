"""Write policy offline checks: rules, classifier hook, validator, audit log.

    python sections/03-write-policy/src/test.py
"""
import tempfile
from pathlib import Path

from contract import Scope
from engine import Engine
from ledger import Ledger
from policy import (Candidate, Decision, DEFER, IGNORE, REQUIRE_APPROVAL, STORE,
                    decide, gate, validate)


def test():
    existing = ["User prefers tabs over spaces for indentation",
                "Deploys wait for Monday, never Friday"]
    decisions = []
    log = lambda c, d: decisions.append((c, d))
    ok = lambda content, **kw: Candidate(content, "semantic", source_event_ids=("ev-1",), **kw)

    # rules fire first, cheapest checks in front
    assert gate(Candidate("CI provider is Buildkite", "semantic"), existing, log=log).action == IGNORE
    assert gate(ok("def parse_config(path): loads the yaml"), existing, log=log).action == IGNORE
    assert gate(ok("User prefers tabs over spaces for indentation"), existing, log=log).action == IGNORE
    deferred = gate(ok("User prefers tabs in Python and Go projects"), existing, log=log)
    assert deferred.action == DEFER and "consolidation" in deferred.reason
    assert gate(ok("likes stuff"), existing, log=log).action == IGNORE
    assert gate(ok("Home address is 12 Elm Street", sensitive=True), existing, log=log).action == REQUIRE_APPROVAL

    # nothing objects: store, and the decision says why
    stored = gate(ok("Staging database rotates credentials every Tuesday"), existing, log=log)
    assert stored.action == STORE and stored.reason and 0 < stored.confidence <= 1

    # every candidate leaves an audit row, rejections included
    assert len(decisions) == 7
    assert all(d.reason for _c, d in decisions)

    # the classifier only sees what rules cannot settle
    calls = []
    classifier = lambda c: (calls.append(c) or (IGNORE, "smalltalk, not durable", 0.7))
    assert decide(ok("had a nice chat about the weather today"), existing, classifier).action == IGNORE
    assert decide(ok("User prefers tabs over spaces for indentation"), existing, classifier).action == IGNORE
    assert len(calls) == 1                        # the duplicate never reached the model

    # a lying classifier is caught by the validator, not by hope
    for bad in (("update", "not an action", 0.5), (STORE, "", 0.5), (STORE, "ok", 1.2)):
        try:
            validate(Decision(*bad), ok("anything specific enough to store"))
            raise AssertionError(f"validator should have rejected {bad}")
        except ValueError:
            pass
    try:
        validate(Decision(STORE, "ok", 0.5), Candidate("no evidence at all", "semantic"))
        raise AssertionError("store without source events must fail")
    except ValueError:
        pass

    # the engine wires the gate to the ledger: every decision becomes an event
    eng = Engine(Ledger(Path(tempfile.mkdtemp()) / "engine.db"))
    scope = Scope("acme", "marcus")
    first = eng.propose(scope, ok("Staging database rotates credentials every Tuesday"))
    again = eng.propose(scope, ok("Staging database rotates credentials every Tuesday"))
    assert first.action == STORE and again.action == IGNORE      # stored once, then known
    logged = eng.ledger.read(scope, event_type="memory_decision")
    assert [e.content.split(":")[0] for e in logged] == ["store", "ignore"]
    assert all(e.metadata["reason"] for e in logged)

    print("02 write-policy: ok")


if __name__ == "__main__":
    test()
