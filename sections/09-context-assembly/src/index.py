"""Index views (production memory track, section 7: many views over one ledger).

One event can feed several query-time shapes. None of them is the memory;
all of them are views computed from records, which are themselves views over
the ledger (section 2):

    raw event log    SQLite, append only, never rebuilt
      -> records     typed, resolved, consolidated (sections 4 to 6)
        -> sparse    FTS5 / bm25            exact names and dates
        -> temporal  recorded_at ranges     what changed in a window
        -> profile   one line per claim     current state, no search

The rule this section exists to enforce: a corrupted index is not an incident,
it is a rebuild. Nothing here is the only copy of anything, so `rebuild()`
is both the schema migration path and the recovery path.

The sparse view is stored because search needs an inverted index. The profile
view is computed on read: it is small, and materializing it would add a second
thing to invalidate. Section 8 fuses rankings from several
of these channels; this section only builds them.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from policy import words
from records import instant

TOP_K = 5

# The index carries scope columns for the same reason the ledger does. One
# shared table without them means a rebuild for one scope deletes another
# scope's rows, and every read returns whichever scope indexed last: a leak
# that looks like relevance rather than like a bug.
_SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS memory_index "
           "USING fts5(memory_id, tenant_id, user_id, kind, content, recorded_at)")

_SCOPE = "tenant_id = ? AND (? IS NULL OR user_id = ?)"


def _args(scope) -> list:
    return [scope.tenant_id, scope.user_id, scope.user_id]


@dataclass
class MemoryIndex:
    """The sparse and temporal views, in one FTS5 table. Stored, disposable,
    and rebuilt from records rather than repaired. Every read takes a scope."""
    path: Path

    def rebuild(self, scope, records) -> int:
        """Drop and rebuild one scope's rows. Corruption recovery and schema
        changes are the same operation here, because the index is a view and
        not a store. Other scopes' rows are left alone."""
        con = self._db()
        con.execute(f"DELETE FROM memory_index WHERE {_SCOPE}", _args(scope))
        con.executemany("INSERT INTO memory_index VALUES (?, ?, ?, ?, ?, ?)",
                        [(r["id"], r["tenant_id"], r["user_id"], r["kind"],
                          r["content"], r["recorded_at"]) for r in records])
        con.commit()
        con.close()
        return len(records)

    def keyword(self, scope, query, k=TOP_K) -> list[str]:
        """bm25-ranked ids. The column filter keeps ids and dates out of matching."""
        terms = words(query)
        if not terms:
            return []
        con = self._db()
        rows = con.execute(
            f"SELECT memory_id FROM memory_index WHERE memory_index MATCH ? AND {_SCOPE} "
            "ORDER BY rank LIMIT ?",
            [f"content : ({' OR '.join(sorted(terms))})"] + _args(scope) + [k]).fetchall()
        con.close()
        return [r[0] for r in rows]

    def recent(self, scope, k=TOP_K, kind=None) -> list[str]:
        """Newest first, optionally one kind (recent episodes are their own channel)."""
        sql = f"SELECT memory_id FROM memory_index WHERE {_SCOPE}"
        args = _args(scope)
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        con = self._db()
        rows = con.execute(sql + " ORDER BY recorded_at DESC LIMIT ?", args + [k]).fetchall()
        con.close()
        return [r[0] for r in rows]

    def between(self, scope, start, end, k=TOP_K) -> list[str]:
        """The temporal view: what the system learned inside a window. Answers
        'what changed last week' without ranking anything by relevance."""
        con = self._db()
        rows = con.execute(
            f"SELECT memory_id FROM memory_index WHERE {_SCOPE} "
            "AND recorded_at >= ? AND recorded_at < ? ORDER BY recorded_at DESC LIMIT ?",
            _args(scope) + [start, end, k]).fetchall()
        con.close()
        return [r[0] for r in rows]

    def count(self, scope) -> int:
        con = self._db()
        n = con.execute(f"SELECT count(*) FROM memory_index WHERE {_SCOPE}",
                        _args(scope)).fetchone()[0]
        con.close()
        return n

    def _db(self):
        con = sqlite3.connect(self.path)
        con.execute(_SCHEMA)
        return con


def profile(records) -> dict:
    """The profile view: current state, one entry per claim key, newest wins.

    No search involved. This is what a prompt wants when it needs "who is this
    user" rather than "what is relevant to this question", and it is why a
    claim key (section 5) is worth carrying: without it there is no key to
    project onto."""
    out = {}
    for r in sorted([r for r in records if r.status == "active" and r.claim_key],
                    key=lambda r: instant(r.recorded_at)):
        out[r.claim_key] = r.content
    return out

