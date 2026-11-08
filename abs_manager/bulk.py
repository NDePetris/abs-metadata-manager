"""Small snapshot/proposal writer. No audit, staging or provider dependencies."""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

from .adapter import AdapterError, inventory, membership
from .locking import operation_lock
from .models import WRITE_FIELDS, Book, compact, digest, result_matches, validate_target


def strict_json(text):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def keys(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ValueError(f"Expected keys {', '.join(required)}")


def values(book):
    return book.write_values(WRITE_FIELDS)


class Bulk:
    def __init__(self, adapter, directory, legacy_database):
        self.adapter = adapter
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.legacy = Path(legacy_database)
        self.journal = self.directory / "writes.jsonl"

    def records(self):
        if not self.journal.exists():
            return {}
        latest = {}
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            row = strict_json(line)
            keys(row, ("operation", "state", "data", "message"))
            if row["state"] not in ("intent", "uncertain", "verified", "not_applied"):
                raise ValueError("Invalid journal state; writes blocked")
            latest[row["operation"]] = row
        return latest

    def record(self, operation, state, data, message=""):
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                compact({"operation": operation, "state": state, "data": data, "message": message}) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def ready(self):
        if any(r["state"] in ("intent", "uncertain") for r in self.records().values()):
            raise ValueError("Uncertain write: check ABS results before further writes")
        if self.legacy.exists():
            with sqlite3.connect(self.legacy.resolve().as_uri() + "?mode=ro", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                if db.execute(
                    "SELECT 1 FROM journal WHERE state IN ('prepared','applied','uncertain') LIMIT 1"
                ).fetchone():
                    raise ValueError(
                        "Legacy unresolved write: use existing recovery instructions before writing"
                    )

    def snapshot(self, snapshot_id):
        if (
            not isinstance(snapshot_id, str)
            or len(snapshot_id) != 32
            or any(c not in "0123456789abcdef" for c in snapshot_id)
        ):
            raise ValueError("Invalid snapshot ID")
        return strict_json((self.directory / f"{snapshot_id}.json").read_text(encoding="utf-8"))

    def export(self, library, progress=lambda *args: None):
        with operation_lock(str(self.legacy)):
            libraries = self.adapter.libraries()
            if not any(x["id"] == library and x.get("mediaType") == "book" for x in libraries):
                raise ValueError("Select a book library")
            members = membership(self.adapter, library, progress)
            books = list(inventory(self.adapter, library, progress, members=members))
            if membership(self.adapter, library, progress) != members:
                raise ValueError("Library changed during export; export again")
            sid = uuid.uuid4().hex
            snapshot = {
                "snapshot_id": sid,
                "server": self.adapter.server,
                "library_id": library,
                "books": {b.id: b.raw for b in books},
            }
            path = self.directory / f"{sid}.json"
            with path.open("x", encoding="utf-8") as stream:
                stream.write(compact(snapshot))
                stream.flush()
                os.fsync(stream.fileno())
            context_fields = ("publisher", "language", "isbn", "asin", "publishedDate", "edition", "abridged")
            exported = []
            for b in books:
                exported.append(
                    {
                        "item_id": b.id,
                        "identity": digest(b.identity),
                        "kind": "audiobook" if b.is_audio else "ebook",
                        "current": values(b),
                        "context": {k: b.metadata.get(k) for k in context_fields},
                        "cover_present": bool(b.raw["media"].get("coverPath")),
                        "chapters": [
                            {k: c.get(k) for k in ("title", "start", "end")}
                            for c in b.raw["media"].get("chapters", [])
                        ],
                    }
                )
            return {
                "schema_version": 1,
                "snapshot_id": sid,
                "scope": digest([self.adapter.server, library]),
                "library_id": library,
                "book_count": len(books),
                "books": exported,
            }

    def review(self, document):
        keys(document, ("schema_version", "snapshot_id", "scope", "items"))
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Unsupported schema version")
        snapshot = self.snapshot(document["snapshot_id"])
        if snapshot["server"] != self.adapter.server or document["scope"] != digest(
            [snapshot["server"], snapshot["library_id"]]
        ):
            raise ValueError("Wrong server/library scope")
        if not isinstance(document["items"], list):
            raise TypeError("items must be an array")
        rows, seen = [], set()
        for item in document["items"]:
            keys(item, ("item_id", "identity", "changes"), ("reason",))
            iid = item["item_id"]
            if not isinstance(iid, str) or iid in seen or iid not in snapshot["books"]:
                raise ValueError("Duplicate or unknown current item ID")
            seen.add(iid)
            book = Book(snapshot["books"][iid])
            if item["identity"] != digest(book.identity):
                raise ValueError("Identity mismatch")
            if not isinstance(item.get("reason", ""), str):
                raise TypeError("reason must be text")
            if not isinstance(item["changes"], dict) or not item["changes"]:
                raise ValueError("changes must be a nonempty object")
            current = values(book)
            for field, change in item["changes"].items():
                keys(change, ("before", "after"))
                if field not in WRITE_FIELDS or (not book.is_audio and field == "narrators"):
                    raise ValueError(f"Unsupported field: {field}")
                if field == "series" and isinstance(change["after"], list):
                    for entry in change["after"]:
                        number = entry.get("sequence") if isinstance(entry, dict) else None
                        if number is not None and (
                            not isinstance(number, str)
                            or len(number) > 64
                            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", number)
                        ):
                            raise ValueError("Series sequence must be a bounded decimal string")
                target = validate_target(field, change["after"])
                if target != current[field]:
                    rows.append(
                        {
                            "item_id": iid,
                            "field": field,
                            "before": change["before"],
                            "current": current[field],
                            "after": target,
                            "title": current["title"],
                            "kind": "audiobook" if book.is_audio else "ebook",
                            "conflict": change["before"] != current[field],
                            "reason": item.get("reason", ""),
                        }
                    )
        return rows

    def preview(self, document):
        rows = self.review(document)
        if any(r["conflict"] for r in rows):
            raise ValueError("Stale before values; exclude conflicts")
        if not rows or any(r["kind"] != "audiobook" for r in rows):
            raise ValueError("Select audiobook differences only")
        return {"document": copy.deepcopy(document), "rows": rows, "digest": digest(document)}

    @staticmethod
    def matches(book, data, after=False):
        before = Book(data["raw"])
        fields = data["changes"]
        expected = {**values(before), **(fields if after else {})}
        return (
            book.identity == before.identity
            and book.is_audio
            and not book.raw.get("isMissing")
            and not book.raw.get("isInvalid")
            and result_matches(expected, values(book), fields if after else {})
            and book.protected(fields) == before.protected(fields)
        )

    def apply(self, plan, confirmation, progress=lambda *args: None):
        if confirmation != "APPLY " + plan["digest"][:12] or self.preview(plan["document"]) != plan:
            raise ValueError("Confirmation or preview changed")
        snapshot = self.snapshot(plan["document"]["snapshot_id"])
        results = []
        with operation_lock(str(self.legacy)):
            self.ready()
            history = list(self.records().values())
            self.adapter.check_write_capability()
            if not any(x["id"] == snapshot["library_id"] for x in self.adapter.libraries()):
                raise ValueError("Library no longer exists")
            grouped = {}
            for row in plan["rows"]:
                grouped.setdefault(row["item_id"], {})[row["field"]] = row["after"]
            for index, (iid, changes) in enumerate(grouped.items(), 1):
                data = {
                    "server": self.adapter.server,
                    "library_id": snapshot["library_id"],
                    "item_id": iid,
                    "raw": snapshot["books"][iid],
                    "changes": changes,
                    "digest": plan["digest"],
                }
                op = uuid.uuid4().hex
                try:
                    live = Book(self.adapter.item(iid))
                    prior = [
                        r
                        for r in history
                        if r["state"] == "verified"
                        and r["data"]["server"] == data["server"]
                        and r["data"]["item_id"] == iid
                        and r["data"]["changes"] == changes
                    ]
                    if prior and self.matches(live, data, True):
                        results.append(
                            {
                                "item_id": iid,
                                "fields": list(changes),
                                "changes": changes,
                                "state": "verified",
                                "message": "Already applied; skipped",
                            }
                        )
                        continue
                    if not self.matches(live, data):
                        raise ValueError("Live identity/metadata changed; export again")
                except (AdapterError, ValueError, TypeError, KeyError, OSError) as exc:
                    results.append(
                        {
                            "item_id": iid,
                            "fields": list(changes),
                            "changes": changes,
                            "state": "conflict",
                            "message": str(exc),
                        }
                    )
                    progress(index, len(grouped), results.copy())
                    continue
                self.record(op, "intent", data)
                try:
                    self.adapter.update(iid, changes)
                    observed = Book(self.adapter.item(iid))
                    if not self.matches(observed, data, True):
                        raise ValueError("Readback mismatch; check ABS results")
                    self.record(op, "verified", data)
                    results.append(
                        {
                            "item_id": iid,
                            "fields": list(changes),
                            "changes": changes,
                            "state": "verified",
                            "message": "Readback verified",
                        }
                    )
                except BaseException as exc:
                    self.record(op, "uncertain", data, "Submission/readback interrupted; do not retry")
                    results.append(
                        {
                            "item_id": iid,
                            "fields": list(changes),
                            "changes": changes,
                            "state": "uncertain",
                            "message": "Check ABS results before retry",
                        }
                    )
                    progress(index, len(grouped), results.copy())
                    if not isinstance(exc, Exception):
                        raise
                    break
                progress(index, len(grouped), results.copy())
        return results

    def check_results(self):
        with operation_lock(str(self.legacy)):
            for op, row in self.records().items():
                if row["state"] not in ("intent", "uncertain"):
                    continue
                data = row["data"]
                if data["server"] != self.adapter.server:
                    raise ValueError("Connect to the original server")
                live = Book(self.adapter.item(data["item_id"]))
                state = (
                    "verified"
                    if self.matches(live, data, True)
                    else "not_applied"
                    if self.matches(live, data)
                    else "uncertain"
                )
                self.record(op, state, data, "Read-only ABS check")
            return [
                {
                    "item_id": r["data"]["item_id"],
                    "fields": list(r["data"]["changes"]),
                    "changes": r["data"]["changes"],
                    "state": r["state"],
                    "message": r["message"],
                }
                for r in self.records().values()
                if r["data"]["server"] == self.adapter.server
            ]

    def ebook_report(self, document):
        rows = self.review(document)
        return {
            "schema_version": 1,
            "purpose": "Manual Calibre review; ABS observations, not Calibre verification",
            "corrections": [r for r in rows if r["kind"] == "ebook"],
        }
