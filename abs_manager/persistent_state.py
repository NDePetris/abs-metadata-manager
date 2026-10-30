"""Durable human decisions and manual remediation; never authorizes a write."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .locking import operation_lock
from .models import compact, digest
from .verification import QUALITY_FIELDS, values


def timestamp():
    return datetime.now(UTC).isoformat()


def identity(book, exclude=None):
    # Path and inode changes are deliberately conservative; no old-ID resolution.
    m, media = book.metadata, book.raw["media"]
    return digest(
        {
            "identity": book.identity,
            "asin": None if exclude == "identifiers" else m.get("asin"),
            "isbn": None if exclude == "identifiers" else m.get("isbn"),
            "narrators": None if exclude == "narrators" else book.values["narrators"],
            "duration": media.get("duration"),
            "language": m.get("language"),
            "abridged": m.get("abridged"),
        }
    )


class PersistentState:
    def _init_persistent_state(self):
        with self.connect() as db:
            db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS field_decision_history (
              id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL, item_id TEXT NOT NULL,
              identity_digest TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, value_digest TEXT NOT NULL,
              decision TEXT NOT NULL, evidence TEXT NOT NULL, source TEXT NOT NULL, created TEXT NOT NULL,
              audit_id INTEGER NOT NULL REFERENCES audits(id));
            CREATE INDEX IF NOT EXISTS field_decision_lookup ON field_decision_history(server,library,item_id,field,id);
            CREATE TABLE IF NOT EXISTS candidate_decision_history (
              id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL, item_id TEXT NOT NULL,
              identity_digest TEXT NOT NULL, candidate_id TEXT NOT NULL, decision TEXT NOT NULL,
              evidence TEXT NOT NULL, created TEXT NOT NULL, audit_id INTEGER NOT NULL REFERENCES audits(id));
            CREATE INDEX IF NOT EXISTS candidate_decision_lookup ON candidate_decision_history(server,library,item_id,candidate_id,id);
            CREATE TABLE IF NOT EXISTS corrections (
              id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL, item_id TEXT NOT NULL,
              identity_digest TEXT NOT NULL, title TEXT NOT NULL, calibre_id INTEGER, field TEXT NOT NULL,
              current TEXT NOT NULL, proposed TEXT NOT NULL, evidence TEXT NOT NULL, status TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL, audit_id INTEGER NOT NULL REFERENCES audits(id),
              calibre_verified TEXT, abs_observed TEXT);
            CREATE TABLE IF NOT EXISTS calibre_catalog_history (
              id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL REFERENCES audits(id),
              imported TEXT NOT NULL, data TEXT NOT NULL);
            INSERT INTO calibre_catalog_history(audit_id,imported,data)
              SELECT c.audit_id, strftime('%Y-%m-%dT%H:%M:%fZ','now'), c.data
              FROM calibre_catalogs c WHERE NOT EXISTS
                (SELECT 1 FROM calibre_catalog_history h WHERE h.audit_id=c.audit_id);
            COMMIT;
            """)

    def _scope(self, audit_id, item_id):
        audit = self.audit(audit_id)
        book = self.book(audit_id, item_id)
        return audit, book, identity(book)

    def decide_fields(self, audit_id, item_id, fields, decision, evidence="", source=""):
        if (
            decision not in ("confirmed", "correction_needed", "unable", "later", "reopened")
            or not fields
            or any(f not in QUALITY_FIELDS for f in fields)
        ):
            raise ValueError("Choose valid fields and a decision.")
        with operation_lock(self.path):
            audit, book, ident = self._scope(audit_id, item_id)
            if (
                audit["status"] != "complete"
                or self.latest(audit["server"], audit["library"])["id"] != audit_id
            ):
                raise ValueError("The latest complete audit is required.")
            vals = values(book)
            with self.connect() as db:
                for field in dict.fromkeys(fields):
                    value = vals[field]
                    db.execute(
                        "INSERT INTO field_decision_history(server,library,item_id,identity_digest,field,value,value_digest,decision,evidence,source,created,audit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            audit["server"],
                            audit["library"],
                            item_id,
                            ident,
                            field,
                            compact(value),
                            digest(value),
                            decision,
                            evidence,
                            source,
                            timestamp(),
                            audit_id,
                        ),
                    )

    def field_decisions(self, audit_id, item_id, history=False):
        audit, book, ident = self._scope(audit_id, item_id)
        rows = self.rows(
            "SELECT * FROM field_decision_history WHERE server=? AND library=? AND item_id=? AND audit_id<=? ORDER BY id DESC",
            (audit["server"], audit["library"], item_id, audit_id),
        )
        vals = values(book)
        for row in rows:
            row["value"] = json.loads(row["value"])
            row["valid"] = row["identity_digest"] == ident and row["value_digest"] == digest(
                vals[row["field"]]
            )
        if history:
            return rows
        result = {}
        for row in rows:
            result.setdefault(row["field"], row)
        return result

    def decide_candidate(self, audit_id, item_id, candidate_id, decision, evidence=""):
        if not candidate_id or decision not in ("selected", "rejected", "wrong_book", "unsure", "absent"):
            raise ValueError("Choose a candidate and a valid decision.")
        with operation_lock(self.path):
            audit, _, ident = self._scope(audit_id, item_id)
            if (
                audit["status"] != "complete"
                or self.latest(audit["server"], audit["library"])["id"] != audit_id
            ):
                raise ValueError("The latest complete audit is required.")
            with self.connect() as db:
                db.execute(
                    "INSERT INTO candidate_decision_history(server,library,item_id,identity_digest,candidate_id,decision,evidence,created,audit_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        audit["server"],
                        audit["library"],
                        item_id,
                        ident,
                        candidate_id,
                        decision,
                        evidence,
                        timestamp(),
                        audit_id,
                    ),
                )

    def candidate_decisions(self, audit_id, item_id, history=False):
        audit, _, ident = self._scope(audit_id, item_id)
        rows = self.rows(
            "SELECT * FROM candidate_decision_history WHERE server=? AND library=? AND item_id=? AND audit_id<=? ORDER BY id DESC",
            (audit["server"], audit["library"], item_id, audit_id),
        )
        for row in rows:
            row["valid"] = row["identity_digest"] == ident
            if not row["valid"] and row["decision"] in ("rejected", "wrong_book"):
                original = self.book(row["audit_id"], item_id)
                row["valid"] = original.identity == self.book(audit_id, item_id).identity
        if history:
            return rows
        result = {}
        for row in rows:
            result.setdefault(row["candidate_id"], row)
        return result

    def queue_correction(self, audit_id, item_id, field, proposed, evidence="", calibre_id=None):
        if field not in QUALITY_FIELDS or not evidence.strip():
            raise ValueError("Choose a field and provide supporting evidence.")
        with operation_lock(self.path):
            audit, book, _ident = self._scope(audit_id, item_id)
            if (
                audit["status"] != "complete"
                or self.latest(audit["server"], audit["library"])["id"] != audit_id
            ):
                raise ValueError("The latest complete audit is required.")
            current = values(book)[field]
            if current == proposed:
                raise ValueError("The proposed value is already present.")
            with self.connect() as db:
                cursor = db.execute(
                    "INSERT INTO corrections(server,library,item_id,identity_digest,title,calibre_id,field,current,proposed,evidence,status,created,updated,audit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?,?)",
                    (
                        audit["server"],
                        audit["library"],
                        item_id,
                        identity(book, field),
                        book.values["title"],
                        calibre_id,
                        field,
                        compact(current),
                        compact(proposed),
                        evidence,
                        "pending",
                        timestamp(),
                        timestamp(),
                        audit_id,
                    ),
                )
                return cursor.lastrowid

    def corrections(self, audit_id):
        audit = self.audit(audit_id)
        rows = self.rows(
            "SELECT * FROM corrections WHERE server=? AND library=? ORDER BY id DESC",
            (audit["server"], audit["library"]),
        )
        for row in rows:
            row["current"] = json.loads(row["current"])
            row["proposed"] = json.loads(row["proposed"])
        return rows

    def mark_calibre_edited(self, correction_id):
        with operation_lock(self.path), self.connect() as db:
            row = db.execute("SELECT * FROM corrections WHERE id=?", (correction_id,)).fetchone()
            if not row or row["calibre_verified"] or row["status"] == "identity_ambiguous":
                raise ValueError("Select an outstanding correction.")
            book = self.book(row["audit_id"], row["item_id"])
            if book.is_audio:
                raise ValueError("Calibre edits apply only to ebooks.")
            db.execute(
                "UPDATE corrections SET status='edited_in_calibre',updated=? WHERE id=?",
                (timestamp(), correction_id),
            )

    def _reconcile_import(self, audit_id, db):
        audit = self.audit(audit_id)
        books = {b.id: b for b in self.books(audit_id)}
        prior = self.rows(
            "SELECT id FROM audits WHERE server=? AND library=? AND status='complete' AND id<? ORDER BY id DESC LIMIT 1",
            (audit["server"], audit["library"], audit_id),
        )
        prior_ids = {b.id for b in self.books(prior[0]["id"])} if prior else set()
        # Read all evidence before writing, then commit the reconciliation and completion together.
        field_rows = []
        candidate_rows = []
        for item_id, book in books.items():
            absent = bool(prior and item_id not in prior_ids)
            for d in self.field_decisions(audit_id, item_id).values():
                if d["decision"] == "confirmed" and (not d["valid"] or absent):
                    value = values(book)[d["field"]]
                    field_rows.append(
                        (
                            audit["server"],
                            audit["library"],
                            item_id,
                            identity(book),
                            d["field"],
                            compact(value),
                            digest(value),
                            "reopened",
                            "Saved value or edition identity changed during import.",
                            "reconciliation",
                            timestamp(),
                            audit_id,
                        )
                    )
            for d in self.candidate_decisions(audit_id, item_id).values():
                if d["decision"] != "unsure" and (not d["valid"] or absent):
                    candidate_rows.append(
                        (
                            audit["server"],
                            audit["library"],
                            item_id,
                            identity(book),
                            d["candidate_id"],
                            "unsure",
                            "Edition identity changed during import.",
                            timestamp(),
                            audit_id,
                        )
                    )
        corrections = db.execute(
            "SELECT * FROM corrections WHERE server=? AND library=? AND status!='identity_ambiguous' AND NOT (abs_observed IS NOT NULL AND (status='observed_in_abs' OR calibre_verified IS NOT NULL)) AND audit_id<?",
            (audit["server"], audit["library"], audit_id),
        ).fetchall()
        db.executemany(
            "INSERT INTO field_decision_history(server,library,item_id,identity_digest,field,value,value_digest,decision,evidence,source,created,audit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            field_rows,
        )
        db.executemany(
            "INSERT INTO candidate_decision_history(server,library,item_id,identity_digest,candidate_id,decision,evidence,created,audit_id) VALUES(?,?,?,?,?,?,?,?,?)",
            candidate_rows,
        )
        for row in corrections:
            book = books.get(row["item_id"])
            if not book or identity(book, row["field"]) != row["identity_digest"]:
                db.execute(
                    "UPDATE corrections SET status='identity_ambiguous', updated=? WHERE id=?",
                    (timestamp(), row["id"]),
                )
            elif row["abs_observed"] is None and values(book).get(row["field"]) == json.loads(
                row["proposed"]
            ):
                db.execute(
                    "UPDATE corrections SET abs_observed=?,updated=?, status=CASE WHEN ? THEN 'observed_in_abs' ELSE status END WHERE id=?",
                    (timestamp(), timestamp(), book.is_audio, row["id"]),
                )

    def import_summary(self, audit_id):
        audit = self.audit(audit_id)
        previous = self.rows(
            "SELECT id FROM audits WHERE server=? AND library=? AND status='complete' AND id<? ORDER BY id DESC LIMIT 1",
            (audit["server"], audit["library"], audit_id),
        )
        current = {b.id: b for b in self.books(audit_id)}
        prior = {b.id: b for b in self.books(previous[0]["id"])} if previous else {}
        common = current.keys() & prior.keys()
        changed = [i for i in common if digest(current[i].raw) != digest(prior[i].raw)]
        ambiguous = [i for i in common if identity(current[i]) != identity(prior[i])]
        old_paths = {
            b.identity.get("relPath") or b.identity.get("path") for i, b in prior.items() if i not in current
        }
        ambiguous += [
            i
            for i, b in current.items()
            if i not in prior and (b.identity.get("relPath") or b.identity.get("path")) in old_paths
        ]
        per_item = {item: self.field_decisions(audit_id, item) for item in current}
        decisions = [d for fields in per_item.values() for d in fields.values()]
        unresolved = 0
        for item_id, book in current.items():
            applicable = set(QUALITY_FIELDS)
            if not book.is_audio:
                applicable -= {"narrators", "chapters", "embedded"}
            if not book.values["series"]:
                applicable.discard("series_position")
            if any(
                not (
                    per_item[item_id].get(field, {}).get("valid")
                    and per_item[item_id][field]["decision"] == "confirmed"
                )
                for field in applicable
            ):
                unresolved += 1
        return {
            "new": len(current.keys() - prior.keys()),
            "removed": len(prior.keys() - current.keys()),
            "changed": len(changed),
            "ambiguous": len(ambiguous),
            "retained": sum(d["valid"] and d["decision"] == "confirmed" for d in decisions),
            "reopened": sum(d["decision"] == "reopened" for d in decisions),
            "unresolved": unresolved,
        }
