from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .audit import analyze
from .locking import operation_lock
from .models import Book, compact, validate_target


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS audits (
                    id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL,
                    name TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL,
                    finished TEXT, error TEXT, read_only INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS books (
                    audit_id INTEGER NOT NULL REFERENCES audits(id), item_id TEXT NOT NULL,
                    raw TEXT NOT NULL, normalized TEXT NOT NULL,
                    PRIMARY KEY(audit_id,item_id));
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL REFERENCES audits(id),
                    item_id TEXT NOT NULL, field TEXT NOT NULL, kind TEXT NOT NULL,
                    current TEXT NOT NULL, proposed TEXT NOT NULL, confidence TEXT NOT NULL,
                    reason TEXT NOT NULL, evidence TEXT NOT NULL, actionable INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', target TEXT NOT NULL, reviewed TEXT);
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL,
                    item_id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL, data TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '', observed TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, query, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(query, args)]

    def start_audit(self, server, library, name, read_only=False) -> int:
        with self.connect() as db:
            # Any new attempt invalidates prior approvals, including interrupted audits.
            db.execute("UPDATE findings SET status='expired', reviewed=? WHERE status='approved' AND audit_id IN "
                       "(SELECT id FROM audits WHERE server=? AND library=?)", (now(), server, library))
            db.execute("UPDATE audits SET status='interrupted', finished=? WHERE server=? AND library=? "
                       "AND status='running'", (now(), server, library))
            cursor = db.execute("INSERT INTO audits(server,library,name,status,started,read_only) "
                                "VALUES(?,?,?,'running',?,?)", (server, library, name, now(), read_only))
            return cursor.lastrowid

    def add_book(self, audit_id: int, book: Book):
        normalized, findings = analyze(book)
        with self.connect() as db:
            db.execute("INSERT INTO books VALUES(?,?,?,?)", (audit_id, book.id, compact(book.raw), compact(normalized)))
            for finding in findings:
                f = finding.to_dict()
                db.execute("INSERT INTO findings(audit_id,item_id,field,kind,current,proposed,confidence,reason,"
                           "evidence,actionable,target) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (audit_id, book.id, f["field"], f["kind"], compact(f["current"]), compact(f["proposed"]),
                            f["confidence"], f["reason"], compact(f["evidence"]), f["actionable"], compact(f["proposed"])))

    def finish_audit(self, audit_id, status="complete", error=None):
        with self.connect() as db:
            db.execute("UPDATE audits SET status=?, finished=?, error=? WHERE id=?", (status, now(), error, audit_id))

    def audit(self, audit_id):
        return self.rows("SELECT * FROM audits WHERE id=?", (audit_id,))[0]

    def latest(self, server, library):
        rows = self.rows("SELECT * FROM audits WHERE server=? AND library=? ORDER BY id DESC LIMIT 1", (server, library))
        return rows[0] if rows else None

    def audits(self):
        return self.rows("SELECT a.*, (SELECT COUNT(*) FROM books b WHERE b.audit_id=a.id) AS books "
                         "FROM audits a ORDER BY id DESC")

    def books(self, audit_id):
        return [Book(json.loads(r["raw"])) for r in self.rows("SELECT raw FROM books WHERE audit_id=?", (audit_id,))]

    def book(self, audit_id, item_id):
        rows = self.rows("SELECT raw FROM books WHERE audit_id=? AND item_id=?", (audit_id, item_id))
        if not rows:
            raise ValueError("Snapshot book not found.")
        return Book(json.loads(rows[0]["raw"]))

    def findings(self, audit_id):
        rows = self.rows("SELECT * FROM findings WHERE audit_id=? ORDER BY item_id, id", (audit_id,))
        for r in rows:
            for key in ("current", "proposed", "target", "evidence"):
                r[key] = json.loads(r[key])
        return rows

    def review(self, ids: list[int], status: str, edited=None):
        with operation_lock(self.path):
            self._review(ids, status, edited)

    def propose(self, audit_id: int, item_id: str, field: str, target, reason: str) -> int:
        """Manual proposals remain pending until a separate deliberate approval."""
        with operation_lock(self.path):
            audit = self.audit(audit_id)
            if audit["status"] != "complete" or audit["read_only"] or self.latest(
                    audit["server"], audit["library"])["id"] != audit_id:
                raise ValueError("A complete current live audiobook audit is required.")
            book = self.book(audit_id, item_id)
            if not book.is_audio:
                raise ValueError("Calibre-managed ebooks are read-only.")
            target = validate_target(field, target)
            if target == book.values[field] or not reason.strip():
                raise ValueError("Enter a different target and evidence/reason.")
            with self.connect() as db:
                if db.execute("SELECT 1 FROM findings WHERE audit_id=? AND item_id=? AND field=? "
                              "AND actionable=1 AND status NOT IN ('rejected','expired','applied')",
                              (audit_id, item_id, field)).fetchone():
                    raise ValueError("Edit or reject the existing proposal for this field first.")
                cursor = db.execute("INSERT INTO findings(audit_id,item_id,field,kind,current,proposed,confidence,reason,"
                                    "evidence,actionable,target) VALUES(?,?,?,'manual',?,?,'review',?,?,1,?)",
                                    (audit_id, item_id, field, compact(book.values[field]), compact(target), reason,
                                     compact({"source": "User-entered proposal; verify edition."}), compact(target)))
                return cursor.lastrowid

    def _review(self, ids: list[int], status: str, edited=None):
        if status not in ("pending", "approved", "rejected") or not ids:
            raise ValueError("Select findings and a valid review decision.")
        if edited is not None and len(ids) != 1:
            raise ValueError("Edit one field at a time.")
        with self.connect() as db:
            for finding_id in ids:
                row = db.execute("SELECT f.*, a.server, a.library, a.status AS audit_status, a.read_only "
                                 "FROM findings f JOIN audits a ON a.id=f.audit_id WHERE f.id=?", (finding_id,)).fetchone()
                if row is None or not row["actionable"] or row["status"] in ("applied", "expired"):
                    raise ValueError("Only active correction proposals can be reviewed.")
                latest = db.execute("SELECT MAX(id) FROM audits WHERE server=? AND library=?",
                                    (row["server"], row["library"])).fetchone()[0]
                if row["audit_status"] != "complete" or row["audit_id"] != latest or row["read_only"]:
                    raise ValueError("Run a complete live audiobook audit before approving changes.")
                target = validate_target(row["field"], edited if edited is not None else json.loads(row["target"]))
                if target == json.loads(row["current"]):
                    raise ValueError("The proposed target equals the current value.")
                db.execute("UPDATE findings SET status=?, target=?, reviewed=? WHERE id=?",
                           (status, compact(target), now(), finding_id))

    def record(self, server, library, item_id, action, state, data, message="") -> int:
        with self.connect() as db:
            cursor = db.execute("INSERT INTO journal(server,library,item_id,action,state,created,updated,data,message) "
                                "VALUES(?,?,?,?,?,?,?,?,?)", (server, library, item_id, action, state, now(), now(),
                                                             compact(data), message))
            return cursor.lastrowid

    def transition(self, operation_id, state, message="", observed=None, consume=(), parent=None):
        with self.connect() as db:
            db.execute("UPDATE journal SET state=?, updated=?, message=?, observed=? WHERE id=?",
                       (state, now(), message, compact(observed) if observed is not None else None, operation_id))
            for finding_id in consume:
                db.execute("UPDATE findings SET status='applied', reviewed=? WHERE id=?", (now(), finding_id))
            if parent is not None:
                db.execute("UPDATE journal SET state='reverted', updated=?, message=? WHERE id=?",
                           (now(), f"Guarded rollback verified by operation {operation_id}.", parent))

    def history(self, server=None):
        rows = self.rows("SELECT * FROM journal" + (" WHERE server=?" if server else "") + " ORDER BY id DESC",
                         (server,) if server else ())
        for row in rows:
            row["data"] = json.loads(row["data"])
            row["observed"] = json.loads(row["observed"]) if row["observed"] else None
        return rows

    def operation(self, operation_id):
        return next(row for row in self.history() if row["id"] == operation_id)

    def unresolved(self, server):
        return [r for r in self.history(server) if r["state"] in ("prepared", "applied", "uncertain")]
