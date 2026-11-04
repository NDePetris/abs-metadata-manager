"""Durable human decisions and manual remediation; never authorizes a write."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .locking import operation_lock
from .models import FIELDS, compact, digest, result_matches, review_fields, target_observed, validate_target
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
            CREATE TABLE IF NOT EXISTS calibre_bindings (
              server TEXT NOT NULL, library TEXT NOT NULL, item_id TEXT NOT NULL,
              identity_digest TEXT NOT NULL, calibre_library TEXT NOT NULL,
              calibre_id INTEGER NOT NULL, calibre_uuid TEXT NOT NULL,
              PRIMARY KEY(server,library,item_id));
            INSERT INTO calibre_catalog_history(audit_id,imported,data)
              SELECT c.audit_id, strftime('%Y-%m-%dT%H:%M:%fZ','now'), c.data
              FROM calibre_catalogs c WHERE NOT EXISTS
                (SELECT 1 FROM calibre_catalog_history h WHERE h.audit_id=c.audit_id);
            COMMIT;
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(corrections)")}
            for name in ("accepted", "calibre_library", "calibre_uuid"):
                if name not in columns:
                    db.execute(f"ALTER TABLE corrections ADD COLUMN {name} TEXT")

    def confirm_targets(self, audit_id, item_id, targets, evidence="Human confirmed displayed values"):
        """Atomically accept values and stage differences; never approve a write."""
        with operation_lock(self.path), self.connect() as db:
            audit, book, ident = self._scope(audit_id, item_id)
            if (audit["read_only"] or audit["status"] != "complete"
                    or self.latest(audit["server"], audit["library"])["id"] != audit_id):
                raise ValueError("Complete the latest live import first.")
            if not targets or any(f not in review_fields(book.is_audio) for f in targets):
                raise ValueError("Select applicable metadata fields.")
            for field, target in targets.items():
                if field == "identifiers":
                    if book.is_audio or not isinstance(target, dict) or set(target) - {"isbn", "asin"} or any(not isinstance(v, str) for v in target.values()):
                        raise ValueError("Identifiers are manual Calibre corrections only.")
                elif target != values(book)[field]:
                    validate_target(field, target)
            for field, target in targets.items():
                current = values(book)[field]
                db.execute("UPDATE corrections SET status='discarded',updated=? WHERE server=? AND library=? "
                           "AND item_id=? AND field=? AND status IN ('pending','edited_in_calibre','verified_in_calibre','needs_review')",
                           (timestamp(), audit["server"], audit["library"], item_id, field))
                db.execute("UPDATE findings SET status='rejected' WHERE audit_id=? AND item_id=? AND field=? "
                           "AND status IN ('pending','approved') AND actionable=1", (audit_id, item_id, field))
                db.execute("INSERT INTO field_decision_history(server,library,item_id,identity_digest,field,value,"
                           "value_digest,decision,evidence,source,created,audit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (audit["server"], audit["library"], item_id, ident, field, compact(current), digest(current),
                            "confirmed" if current == target else "correction_needed", evidence, "human", timestamp(), audit_id))
                if target != current:
                    db.execute("INSERT INTO corrections(server,library,item_id,identity_digest,title,field,current,"
                               "proposed,evidence,status,created,updated,audit_id,accepted) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                               (audit["server"], audit["library"], item_id, identity(book, field), book.values["title"],
                                field, compact(current), compact(target), evidence, timestamp(), timestamp(), audit_id, timestamp()))
                    if book.is_audio:
                        db.execute("INSERT INTO findings(audit_id,item_id,field,kind,current,proposed,confidence,reason,"
                                   "evidence,actionable,target) VALUES(?,?,?,'confirmed_target',?,?,'human',?,'{}',1,?)",
                                   (audit_id, item_id, field, compact(current), compact(target), evidence, compact(target)))

    def discard_correction(self, correction_id):
        """Archive a local target without removing evidence or touching its write journal."""
        with operation_lock(self.path), self.connect() as db:
            row = db.execute("SELECT * FROM corrections WHERE id=?", (correction_id,)).fetchone()
            if row is None:
                raise ValueError("Correction not found.")
            db.execute("UPDATE corrections SET status='discarded',updated=? WHERE id=?", (timestamp(), correction_id))
            db.execute("UPDATE findings SET status='rejected' WHERE item_id=? AND field=? AND kind='confirmed_target' AND status IN ('pending','approved') AND audit_id IN (SELECT id FROM audits WHERE server=? AND library=?)",
                       (row["item_id"], row["field"], row["server"], row["library"]))

    def expected_transition(self, audit_id, item_id, original):
        audit = self.audit(audit_id)
        current = self.book(audit_id, item_id)
        if original.identity != current.identity:
            return False
        for row in self.history(audit["server"]):
            data = row["data"]
            if (row["state"] == "verified" and row["action"] == "apply" and row["library"] == audit["library"]
                    and row["item_id"] == item_id and data.get("before") == original.values
                    and result_matches(data.get("after", {}), current.values, data.get("changes", {}))
                    and data.get("protected") == current.protected()
                    and data.get("identity") == current.identity and original.protected() == data.get("protected")):
                return True
        import copy
        expected = copy.deepcopy(original.raw)
        corrections = self.corrections(audit_id)
        trusted = [r for r in corrections if r["item_id"] == item_id and r["accepted"] and r["calibre_verified"]
                   and r["calibre_library"] and r["calibre_uuid"] and r["status"] not in ("identity_ambiguous", "discarded", "needs_review")]
        for row in trusted:
            if row["field"] == "identifiers" and values(current)["identifiers"] == row["proposed"]:
                expected["media"]["metadata"].pop("isbn", None)
                expected["media"]["metadata"].pop("asin", None)
                expected["media"]["metadata"].update(row["proposed"])
        from .models import Book
        return bool(trusted) and identity(Book(expected)) == identity(current) and Book(expected).protected() == current.protected()

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
            if decision == "confirmed" and not book.is_audio and "narrators" in fields:
                raise ValueError("Select applicable metadata fields.")
            with self.connect() as db:
                for field in dict.fromkeys(fields):
                    if decision == "reopened":
                        db.execute("UPDATE corrections SET status='needs_review',updated=? WHERE server=? AND library=? AND item_id=? AND field=? AND status='pending'",
                                   (timestamp(), audit["server"], audit["library"], item_id, field))
                        db.execute("UPDATE findings SET status='rejected' WHERE audit_id=? AND item_id=? AND field=? AND status IN ('pending','approved') AND kind='confirmed_target'",
                                   (audit_id, item_id, field))
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
            accepted_clear = (row["field"] == "subtitle" and row["value"] == ""
                              and vals["subtitle"] is None and any(
                                  c["item_id"] == item_id and c["field"] == "subtitle"
                                  and c["accepted"] and c["proposed"] == ""
                                  and c["identity_digest"] == identity(book, "subtitle")
                                  and c["status"] == "observed_in_abs" for c in self.corrections(audit_id)))
            row["valid"] = (row["identity_digest"] == ident or self.expected_transition(
                audit_id, item_id, self.book(row["audit_id"], item_id))) and (
                    row["value_digest"] == digest(vals[row["field"]]) or accepted_clear)
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
                if d["decision"] == "confirmed" and (not d["valid"] or absent or d["identity_digest"] != identity(book)):
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
                            "confirmed" if d["valid"] and not absent else "reopened",
                            "Expected correction verified." if d["valid"] and not absent else "Saved value or edition identity changed during import.",
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
            "SELECT * FROM corrections WHERE server=? AND library=? AND status NOT IN ('identity_ambiguous','discarded','needs_review') AND NOT (abs_observed IS NOT NULL AND (status='observed_in_abs' OR calibre_verified IS NOT NULL)) AND audit_id<?",
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
            if not book or (identity(book, row["field"]) != row["identity_digest"] and not self.expected_transition(audit_id, row["item_id"], self.book(row["audit_id"], row["item_id"]))):
                db.execute(
                    "UPDATE corrections SET status='identity_ambiguous', updated=? WHERE id=?",
                    (timestamp(), row["id"]),
                )
            elif row["accepted"] and values(book).get(row["field"]) == json.loads(row["current"]) and book.is_audio and row["field"] in FIELDS:
                db.execute("UPDATE findings SET status='rejected' WHERE audit_id=? AND item_id=? AND field=? AND actionable=1 AND status='pending'",
                           (audit_id, book.id, row["field"]))
                db.execute("INSERT INTO findings(audit_id,item_id,field,kind,current,proposed,confidence,reason,evidence,actionable,target) VALUES(?,?,?,'confirmed_target',?,?,'human',?,'{}',1,?)",
                           (audit_id, book.id, row["field"], row["current"], row["proposed"], row["evidence"], row["proposed"]))
            elif row["abs_observed"] is None and (target_observed(
                row["field"], json.loads(row["proposed"]), values(book).get(row["field"]))
                if row["accepted"] else values(book).get(row["field"]) == json.loads(row["proposed"])):
                db.execute(
                    "UPDATE corrections SET abs_observed=?,updated=?, status=CASE WHEN ? THEN 'observed_in_abs' ELSE status END WHERE id=?",
                    (timestamp(), timestamp(), book.is_audio, row["id"]),
                )
                if row["accepted"]:
                    field = row["field"]
                    value = values(book)[field]
                    db.execute("INSERT INTO field_decision_history(server,library,item_id,identity_digest,field,value,value_digest,decision,evidence,source,created,audit_id) VALUES(?,?,?,?,?,?,?,'confirmed',?,'reconciliation',?,?)",
                               (audit["server"], audit["library"], book.id, identity(book), field, compact(value), digest(value),
                                "Previously accepted target observed in inventory.", timestamp(), audit_id))

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
            applicable = review_fields(book.is_audio)
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
