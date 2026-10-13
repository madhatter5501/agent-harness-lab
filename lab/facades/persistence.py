import json
import time

from ..common import database


class RunStore:
    """Mutable execution checkpoints. Replace with PostgreSQL or a workflow engine."""

    def initialize(self):
        with database("harness") as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT, body TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, run_id TEXT, body TEXT)"
            )
            for row in db.execute("SELECT body FROM runs").fetchall():
                run = json.loads(row[0])
                if run["status"] == "running":
                    run["status"] = "interrupted"
                    run["answer"] = (
                        "Harness restarted. Inspect API state before retrying a write."
                    )
                    db.execute(
                        "UPDATE runs SET body=? WHERE id=?",
                        (json.dumps(run), run["id"]),
                    )

    def save(self, run):
        with database("harness") as db:
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                (run["id"], run["owner"], json.dumps(run)),
            )

    def get(self, run_id, owner):
        with database("harness") as db:
            row = db.execute(
                "SELECT body FROM runs WHERE id=? AND owner=?", (run_id, owner)
            ).fetchone()
        if not row:
            raise KeyError("Run not found")
        return json.loads(row[0])

    def recent(self, owner):
        with database("harness") as db:
            rows = db.execute(
                "SELECT body FROM runs WHERE owner=? ORDER BY rowid DESC LIMIT 30",
                (owner,),
            ).fetchall()
        return [
            {k: r[k] for k in ("id", "prompt", "status", "mode")}
            for r in (json.loads(row[0]) for row in rows)
        ]


class AuditLog:
    """Append-only application events, not a tamper-proof compliance archive."""

    def __init__(self, store):
        self.store = store

    def record(self, run, kind, title, data=None):
        event = {
            "seq": len(run["events"]) + 1,
            "kind": kind,
            "title": title,
            "time": time.time(),
            "data": json.loads(json.dumps(data or {})),
        }
        run["events"].append(event)
        with database("harness") as db:
            db.execute(
                "INSERT INTO events(run_id,body) VALUES (?,?)",
                (run["id"], json.dumps(event)),
            )
        self.store.save(run)

    def read(self, run_id):
        with database("harness") as db:
            rows = db.execute(
                "SELECT body FROM events WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
