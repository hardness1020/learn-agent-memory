"""Event ledger offline checks: append-only, scope isolation, rebuildable views.

    python tracks/production-memory/01-event-ledger/src/test.py
"""
import sqlite3
import tempfile
from pathlib import Path

from engine import Engine
from ledger import Ledger, Scope


def test():
    led = Ledger(Path(tempfile.mkdtemp()) / "events.db")
    marcus = Scope(tenant_id="acme", user_id="marcus")
    dana = Scope(tenant_id="globex", user_id="dana")

    # capture is more than chat: messages, tool results, corrections, outcomes
    led.append(marcus, "user_message", "use Python for this repo")
    led.append(marcus, "tool_result", "pytest: 3 passed", metadata={"tool": "Bash"})
    led.append(marcus, "user_message", "actually use TypeScript for this project")
    led.append(marcus, "user_correction", "Python is still my primary language")
    led.append(dana, "user_message", "dana prefers Go")

    # scope isolation: one tenant's read never returns another tenant's rows
    events = led.read(marcus)
    assert len(events) == 4
    assert all(e.scope.tenant_id == "acme" for e in events)
    assert not any("Go" in e.content for e in events)
    assert [e.event_type for e in led.read(dana)] == ["user_message"]
    assert events[1].metadata == {"tool": "Bash"}          # metadata round-trips

    # two clocks: recorded_at is stamped by the ledger, occurred_at only if known
    assert all(e.recorded_at for e in events)
    assert events[0].occurred_at is None
    led.append(marcus, "task_outcome", "deploy failed on migration lock",
               occurred_at="2026-07-20T09:00:00+00:00")
    assert led.read(marcus, event_type="task_outcome")[0].occurred_at.startswith("2026-07-20")

    # corrections are new events, not edits: the full history stays readable, in order
    prefs = [e.content for e in led.read(marcus, event_type="user_message")]
    assert prefs == ["use Python for this repo", "actually use TypeScript for this project"]

    # append-only is enforced by the database, not by convention
    con = sqlite3.connect(led.path)
    for sql in ("UPDATE events SET content = 'x'", "DELETE FROM events"):
        try:
            con.execute(sql)
            raise AssertionError(f"{sql} should have aborted")
        except sqlite3.DatabaseError as e:
            assert "append-only" in str(e)
    con.close()

    # any derived view recomputes from the rows: latest-wins language preference
    def language_view(scope):
        latest = {}
        for e in led.read(scope):
            if e.event_type in ("user_message", "user_correction"):
                for lang in ("Python", "TypeScript", "Go"):
                    if lang in e.content:
                        latest["language"] = lang
        return latest

    assert language_view(marcus) == {"language": "Python"}   # the correction wins
    assert language_view(dana) == {"language": "Go"}         # per scope, from the same table

    # the integration point starts here: observe() is a ledger append
    eng = Engine(Ledger(Path(tempfile.mkdtemp()) / "engine.db"))
    eid = eng.observe(marcus, "user_message", "hello from the engine")
    assert [e.id for e in eng.ledger.read(marcus)] == [eid]

    print("01 event-ledger: ok")


if __name__ == "__main__":
    test()
