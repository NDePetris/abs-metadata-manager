"""Import a user-exported calibredb list --for-machine JSON catalog, never open its database."""
from __future__ import annotations

import json
import re

from .locking import operation_lock
from .models import canon, compact, digest, sequence
from .store import now
from .verification import isbn_key, names


def import_catalog(store, audit_id, text, library=None):
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
                if library:
                    corrections = [r for r in store.corrections(audit_id) if r["item_id"] == book.id]
                    bound = [r for r in corrections if r["calibre_library"] == library and r["calibre_uuid"]]
                    titles = {canon(book.values["title"] or "")} | {canon(r["proposed"]) for r in corrections
                              if r["field"] == "title" and r["accepted"]}
                    compatible = (canon(row["title"]) in titles and {canon(a) for a in authors} ==
                                  {canon(a) for a in book.values["authors"]})
                    if bound:
                        if not any(r["calibre_uuid"] == row.get("uuid") and r["calibre_id"] == row["id"] for r in bound):
                            continue
                    elif not compatible or not row.get("uuid"):
                        continue
                if id_match or isbn_match or work_match or library and bound:
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
            db.execute("INSERT INTO calibre_catalog_history(audit_id,imported,data) VALUES(?,?,?)", (audit_id, captured, compact(catalog)))
            audit = store.audit(audit_id)
            from .persistent_state import identity
            books = {book.id: book for book in store.books(audit_id)}
            rows = db.execute("SELECT * FROM corrections WHERE server=? AND library=? AND status IN ('pending','edited_in_calibre')", (audit["server"], audit["library"])).fetchall()
            for correction in rows:
                match = result.get(correction["item_id"])
                book = books.get(correction["item_id"])
                if (match and book and identity(book, correction["field"]) == correction["identity_digest"]
                        and (correction["calibre_id"] is None or correction["calibre_id"] == match["id"])
                        and correction["field"] in match["values"]
                        and match["values"][correction["field"]] == json.loads(correction["proposed"])):
                    if library:
                        db.execute("UPDATE corrections SET calibre_library=?,calibre_uuid=? WHERE id=?",
                                   (library, match["raw"].get("uuid"), correction["id"]))
                    db.execute("UPDATE corrections SET status='verified_in_calibre', calibre_id=?, calibre_verified=?, updated=? WHERE id=?",
                               (match["id"], captured, captured, correction["id"]))
    return len(result)


def validate_local(store, audit_id, executable, library):
    """Only invoke the fixed local catalog listing command."""
    import os
    import subprocess
    from pathlib import Path
    if not library or not Path(library).is_dir():
        raise ValueError("Configure a local Calibre library folder in Settings.")
    with operation_lock(store.path):
        try:
            result = subprocess.run([executable, "list", "--with-library", str(Path(library).resolve()),
                                     "--for-machine", "--fields", "title,authors,series,series_index,identifiers,uuid"],
                                    check=False, capture_output=True, text=True, encoding="utf-8", timeout=60, shell=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Calibre listing unavailable or timed out. No correction was verified.") from None
        if result.returncode:
            raise ValueError("Calibre could not list the library. Close Calibre and retry explicitly, or use a catalog export.")
    return import_catalog(store, audit_id, result.stdout, str(Path(library).resolve()))
