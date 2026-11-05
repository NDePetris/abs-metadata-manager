"""Import a user-exported calibredb list --for-machine JSON catalog, never open its database."""
from __future__ import annotations

import json
import re

from .locking import operation_lock
from .models import canon, compact, digest, sequence
from .store import now
from .verification import isbn_key, names


def import_catalog(store, audit_id, text, library=None, associations=None):
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
    # Unscoped exports are archived only; numeric IDs cannot associate a book without library context.
    associations = associations or {}
    result, bindings = {}, []
    audit = store.audit(audit_id)
    from .persistent_state import identity
    if associations and not library:
        raise ValueError("An explicit association requires a Calibre library context.")
    captured, fingerprint = now(), digest(catalog)
    with operation_lock(store.path):
        if store.audit(audit_id)["status"] != "complete":
            raise ValueError("Select a completed snapshot.")
        existing = {r["item_id"]: r for r in store.rows(
            "SELECT * FROM calibre_bindings WHERE server=? AND library=?", (audit["server"], audit["library"]))}
        all_corrections = store.corrections(audit_id)
        for book in store.books(audit_id):
            if book.is_audio or not library:
                continue
            folder = (book.raw.get("relPath") or book.raw["path"]).replace("\\", "/").rstrip("/")
            folder_id = re.search(r"\((\d+)\)(?:/[^/]+\.epub)?$", folder, re.IGNORECASE)
            isbn = isbn_key(book.metadata.get("isbn"))
            corrections = [r for r in all_corrections if r["item_id"] == book.id
                           and r["identity_digest"] == identity(book, r["field"])
                           and r["status"] not in ("discarded", "identity_ambiguous", "needs_review")]
            bound = existing.get(book.id)
            # Recover bindings made by versions preceding the binding table.
            legacy = {(r["calibre_library"], r["calibre_id"], r["calibre_uuid"]) for r in corrections
                      if r["calibre_library"] and r["calibre_uuid"]}
            if not bound and len(legacy) == 1:
                lib, cid, uuid = next(iter(legacy))
                bound = {"calibre_library": lib, "calibre_id": cid, "calibre_uuid": uuid,
                         "identity_digest": identity(book)}
            explicit = associations.get(book.id)
            titles = {canon(book.values["title"] or "")} | {canon(r["proposed"]) for r in corrections
                       if r["field"] == "title" and r["accepted"]}
            author_sets = [{canon(a) for a in book.values["authors"]}] + [
                {canon(a) for a in r["proposed"]} for r in corrections if r["field"] == "authors" and r["accepted"]]
            matches = []
            for row in catalog:
                authors = row.get("authors", [])
                authors = authors.split(" & ") if isinstance(authors, str) else names(authors)
                compatible = canon(row["title"]) in titles and {canon(a) for a in authors} in author_sets
                identifiers = [book.metadata] + [r["proposed"] for r in corrections
                               if r["field"] == "identifiers" and r["accepted"]]
                if any(book.metadata.get(k) and row.get("identifiers", {}).get(k)
                       and not any(str(v.get(k, "")).casefold() == str(row["identifiers"][k]).casefold()
                                   for v in identifiers) for k in ("isbn", "asin")):
                    continue
                id_match = bool(folder_id and str(row["id"]) == folder_id[1])
                isbn_match = bool(isbn and isbn == isbn_key(row.get("identifiers", {}).get("isbn")))
                if explicit:
                    if row["id"] != explicit.get("id") or not row.get("uuid") or row["uuid"] != explicit.get("uuid"):
                        continue
                    if bound and (bound["calibre_library"] != library or bound["calibre_uuid"] != row["uuid"]
                                  or bound["calibre_id"] != row["id"] or bound["identity_digest"] != identity(book)):
                        raise ValueError("Association contradicts an existing Calibre binding; resolve identity first.")
                    if not compatible:
                        continue
                elif bound:
                    if (bound["calibre_library"] != library or bound["identity_digest"] != identity(book)
                            or bound["calibre_id"] != row["id"] or bound["calibre_uuid"] != row.get("uuid")
                            or not compatible):
                        continue
                elif not compatible or not (id_match or isbn_match):
                    continue
                if library and (not isinstance(row.get("uuid"), str) or not row["uuid"].strip()
                                or sum(r.get("uuid") == row["uuid"] for r in catalog) != 1):
                    continue
                matches.append((row, authors))
            if len(matches) != 1:
                continue
            row, authors = matches[0]
            if library:
                bindings.append((audit["server"], audit["library"], book.id, identity(book), library, row["id"], row["uuid"]))
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
        # Two ABS items cannot silently acquire the same Calibre identity.
        keys = [(b[4], b[5], b[6]) for b in bindings]
        ambiguous = {k for k in keys if keys.count(k) > 1}
        for binding in bindings:
            if (binding[4], binding[5], binding[6]) in ambiguous:
                result.pop(binding[2], None)
        with store.connect() as db:
            db.executemany("INSERT OR REPLACE INTO calibre_bindings VALUES(?,?,?,?,?,?,?)",
                           [b for b in bindings if b[2] in result])
            previous = db.execute("SELECT data FROM calibre_catalogs WHERE audit_id=?", (audit_id,)).fetchone()
            retained = {iid: entry for iid, entry in json.loads(previous[0] if previous else "{}").items()
                        if not library or existing.get(iid, {}).get("calibre_library") != library}
            db.execute("INSERT OR REPLACE INTO calibre_catalogs VALUES(?,?)", (audit_id, compact({**retained, **result})))
            db.execute("INSERT INTO calibre_catalog_history(audit_id,imported,data) VALUES(?,?,?)", (audit_id, captured, compact(catalog)))
            audit = store.audit(audit_id)
            books = {book.id: book for book in store.books(audit_id)}
            records_by_id = {r["id"]: r for r in catalog}
            unique_uuids = {r.get("uuid") for r in catalog if isinstance(r.get("uuid"), str) and r["uuid"].strip()}
            # Historical timestamps remain evidence, but a later incompatible catalog reopens review.
            for prior in db.execute("SELECT * FROM corrections WHERE server=? AND library=? AND calibre_verified IS NOT NULL AND status NOT IN ('discarded','needs_review','identity_ambiguous')",
                                    (audit["server"], audit["library"])).fetchall():
                book = books.get(prior["item_id"])
                bound = existing.get(prior["item_id"])
                if (not library or library != prior["calibre_library"] or not book
                        or identity(book, prior["field"]) != prior["identity_digest"]
                        or not bound or bound["identity_digest"] != identity(book)
                        or bound["calibre_library"] != library or bound["calibre_id"] != prior["calibre_id"]
                        or bound["calibre_uuid"] != prior["calibre_uuid"]):
                    continue  # No authoritative observation of this correction's existing binding.
                record = records_by_id.get(prior["calibre_id"])
                if record is None:
                    if len(unique_uuids) != len(catalog):
                        continue  # An identity-incomplete export cannot establish removal.
                elif (not isinstance(record.get("uuid"), str) or not record["uuid"].strip()
                      or sum(r.get("uuid") == record["uuid"] for r in catalog) != 1):
                    continue
                elif record["uuid"] == prior["calibre_uuid"] and not {"title", "authors"} <= record.keys():
                    continue  # Missing work fields are not a material change.
                fresh = result.get(prior["item_id"])
                if fresh and prior["field"] not in fresh["values"]:
                    continue  # An omitted optional field supplies no contrary observation.
                if not (fresh and library == prior["calibre_library"] and fresh["id"] == prior["calibre_id"]
                        and fresh["raw"].get("uuid") == prior["calibre_uuid"]
                        and fresh["values"].get(prior["field"]) == json.loads(prior["proposed"])):
                    db.execute("UPDATE corrections SET status='needs_review',updated=? WHERE id=?", (captured, prior["id"]))
            rows = db.execute("SELECT * FROM corrections WHERE server=? AND library=? AND status IN ('pending','edited_in_calibre')", (audit["server"], audit["library"])).fetchall()
            for correction in rows:
                match = result.get(correction["item_id"])
                book = books.get(correction["item_id"])
                if (library and match and book and identity(book, correction["field"]) == correction["identity_digest"]
                        and (correction["calibre_id"] is None or correction["calibre_id"] == match["id"])
                        and (not correction["calibre_library"] or correction["calibre_library"] == library)
                        and (not correction["calibre_uuid"] or correction["calibre_uuid"] == match["raw"].get("uuid"))
                        and correction["field"] in match["values"]
                        and match["values"][correction["field"]] == json.loads(correction["proposed"])):
                    if library:
                        db.execute("UPDATE corrections SET calibre_library=?,calibre_uuid=? WHERE id=?",
                                   (library, match["raw"].get("uuid"), correction["id"]))
                    db.execute("UPDATE corrections SET status='verified_in_calibre', calibre_id=?, calibre_verified=?, updated=? WHERE id=?",
                               (match["id"], captured, captured, correction["id"]))
    return len(result)


def reconciliation_states(store, audit_id, report=None):
    """Describe authority and sync independently; a catalog mismatch is not proof Calibre is wrong."""
    report = store.verification_report(audit_id) if report is None else report
    corrections = store.corrections(audit_id)
    result = []
    for item in report:
        if item["type"] != "ebook":
            continue
        rows = [c for c in corrections if c["item_id"] == item["item_id"] and c["status"] != "discarded"]
        linked = not item["calibre_limitation"]
        differences = [f for f, d in item["fields"].items() if any(
            p["source"] == "Calibre catalog" for p in d["provenance"]) and d["status"] == "Conflict"]
        state = ("Matching unavailable or ambiguous" if not linked else
                 "External edit reported; revalidation required" if any(c["status"] == "edited_in_calibre" for c in rows) else
                 "Calibre correction needs review" if any(c["status"] in ("pending", "needs_review", "identity_ambiguous") for c in rows) else
                 "Calibre verified; ABS synchronization pending" if differences and any(c["status"] == "verified_in_calibre" for c in rows) else
                 "Calibre differs; review authority or ABS synchronization" if differences else "Calibre and ABS agree")
        result.append({"item_id": item["item_id"], "title": item["fields"]["title"]["value"],
                       "state": state, "different_fields": differences,
                       "authority": "Calibre", "abs_observation": item.get("abs_observation"),
                       "limitation": "Catalog comparison only; covers/custom columns unavailable. A discrepancy requires source review before assuming Calibre is correct."})
    return result


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
