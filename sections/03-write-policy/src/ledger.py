"""Event ledger (production memory track, section 2: Capture).

The write side of the whole track: every raw observation lands here once and
is never edited. Extraction, consolidation, and every index downstream are
views computed from these rows, so any of them can be dropped and rebuilt.

Section 9's log_run() is the mini version: (session_id, role, content) rows.
This ledger adds what production needs on top:
  scope        : tenant / user / agent columns, so one tenant's reads can
                 never return another tenant's rows.
  event types  : tool results, corrections, approvals, outcomes, not just chat.
  two clocks   : occurred_at (true in the world, may be unknown) vs
                 recorded_at (when the system learned it, always set here).
  immutability : enforced by SQLite triggers, not by convention. UPDATE and
                 DELETE abort at the database layer.

Corrections are new events, never edits, so the full history stays visible
to consolidation (section 6).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from contract import Scope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    user_id     TEXT,
    agent_id    TEXT,
    event_type  TEXT NOT NULL,
    content     TEXT NOT NULL,
    occurred_at TEXT,
    recorded_at TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_scope ON events (tenant_id, user_id, recorded_at);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""


@dataclass(frozen=True)
class Event:
    """One immutable observation. occurred_at is world time and may be unknown;
    recorded_at is system time and is always set by the ledger, never the caller."""
    id: str
    scope: Scope
    event_type: str
    content: str
    occurred_at: str | None
    recorded_at: str
    metadata: dict


@dataclass
class Ledger:
    """The capture handle. append() is the only write path; there is no update
    or delete method, and the triggers abort both at the SQL layer anyway."""
    path: Path

    def append(self, scope, event_type, content, occurred_at=None, metadata=None) -> str:
        """One INSERT, no read-modify-write. Returns the new event id, the key
        later sections use as provenance (source_event_ids on derived memories)."""
        event_id = uuid.uuid4().hex
        con = self._db()
        con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, scope.tenant_id, scope.user_id, scope.agent_id,
                     event_type, content, occurred_at, _now(), json.dumps(metadata or {})))
        con.commit()
        con.close()
        return event_id

    def read(self, scope, event_type=None, since=None) -> list[Event]:
        """Rows for one scope, oldest first. The tenant filter is mandatory;
        user / agent / type narrow it when set. `since` compares recorded_at."""
        sql, args = "SELECT * FROM events WHERE tenant_id = ?", [scope.tenant_id]
        for column, value in (("user_id", scope.user_id), ("agent_id", scope.agent_id),
                              ("event_type", event_type)):
            if value is not None:
                sql += f" AND {column} = ?"
                args.append(value)
        if since is not None:
            sql += " AND recorded_at >= ?"
            args.append(since)
        con = self._db()
        rows = con.execute(sql + " ORDER BY recorded_at, rowid", args).fetchall()
        con.close()
        return [_event(r) for r in rows]

    def _db(self):
        con = sqlite3.connect(self.path)
        con.executescript(_SCHEMA)                    # idempotent: CREATE ... IF NOT EXISTS
        return con


def _event(row) -> Event:
    id_, tenant, user, agent, event_type, content, occurred, recorded, meta = row
    return Event(id_, Scope(tenant, user, agent), event_type, content,
                 occurred, recorded, json.loads(meta))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
