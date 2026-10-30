"""Import a user-exported calibredb list --for-machine JSON catalog, never open its database."""
from __future__ import annotations

import json
import re

from .locking import operation_lock
from .models import canon, compact, digest, sequence
from .store import now
from .verification import isbn_key, names


def import_catalog(store, audit_id, text):
    catalog = json.loads(text)
    if not isinstance(catalog, list) or not all(isinstance(r, dict) and isinstance(r.get("id"), int)
                                              and isinstance(r.get("title"), str) for r in catalog):
        raise ValueError("Expected calibredb list --for-machine JSON records with numeric id and title.")
    if len({r["id"] for r in catalog}) != len(catalog):
        raise ValueError("Duplicate Calibre IDs; use a single library export.")
    if any(not isinstance(row.get("identifiers", {}), dict) or
           not isinstance(row.get("authors", []), (str, list)) or
           row.get("series") is not None and not isinstance(row["series"], str) for row in catalog):
        raise ValueError("Invalid Calibre contributor, series, or identifier schema.")
    result = {}
    captured, fingerprint = now(), digest(catalog)
    with operation_lock(store.path):
        if store.audit(audit_id)["status"] != "complete":
            raise ValueError("Select a completed snapshot.")
        for book in store.books(audit_id):
            if book.is_audio:
                continue
            folder = (book.raw.get("relPath") or book.raw["path"]).replace("\\", "/").rstrip("/")
            folder_id = re.search(r"\((\d+)\)(?:/[^/]+\.epub)?$", folder, re.IGNORECASE)
            isbn = book.metadata.get("isbn")
            matches = []
            for row in catalog:
                authors = row.get("authors", [])
                authors = authors.split(" & ") if isinstance(authors, str) else names(authors)
                id_match = folder_id and str(row["id"]) == folder_id[1]
                isbn_match = isbn_key(isbn) and row.get("identifiers", {}).get("isbn") and isbn_key(isbn) == isbn_key(
                    row["identifiers"]["isbn"])
                work_match = canon(row["title"]) == canon(book.values["title"] or "") and {
                    canon(a) for a in authors} == {canon(a) for a in book.values["authors"]}
                if id_match or isbn_match or work_match:
                    matches.append((row, authors))
            if len(matches) != 1:
                continue
            row, authors = matches[0]
            v = {"title": row["title"]}
            if "authors" in row:
                v["authors"] = authors
            if "series" in row:
                v["series"] = [{"name": row["series"], "sequence": sequence(row.get("series_index"))}] if row["series"] else []
                v["series_position"] = [s["sequence"] for s in v["series"]]
            if "identifiers" in row:
                v["identifiers"] = {k: v for k, v in row["identifiers"].items() if k in ("isbn", "asin")}
            result[book.id] = {"id": row["id"], "values": v, "raw": row, "imported": captured,
                               "catalog_digest": fingerprint}
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO calibre_catalogs VALUES(?,?)", (audit_id, compact(result)))
    return len(result)
