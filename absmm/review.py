"""Canonical corrections, offline presentation, and approval validation."""

import re
from importlib.resources import files

from .files import LOCAL_LIMIT, contains_token, identifier, keys, read_json, strict_json, write_text
from .models import Book, clean_text, compact, digest, restorable_year, same_value, validate_target

EDITORIAL = "user cataloging convention"


def snapshot_at(directory, snapshot_id):
    identifier(snapshot_id, 32)
    snapshot = read_json(directory / f"snapshot-{snapshot_id}.json", LOCAL_LIMIT)
    keys(snapshot, ("snapshot_id", "server", "library_id", "books"))
    if snapshot["snapshot_id"] != snapshot_id or not isinstance(snapshot["books"], dict):
        raise ValueError("Invalid private snapshot.")
    return snapshot


def removed_series(before, after):
    names = {entry["name"].casefold() for entry in after}
    return {entry["name"] for entry in before if entry["name"].casefold() not in names}


def flags_for(book, field, before, after):
    flags = []
    blocked = False
    if not book.writable:
        flags.append("Requires a valid audiobook")
        blocked = True
    if book.is_audio and book.is_ebook:
        flags.append("Shared with attached ebook")
    if field in ("authors", "narrators"):
        flags.append("Contributor change")
    if field == "authors" and before != after and same_value(field, before, after):
        flags.append("Author order requires manual correction in ABS")
        blocked = True
    if field in ("authors", "series"):
        old, new = (before, after) if field == "authors" else (
            [entry["name"] for entry in before], [entry["name"] for entry in after]
        )
        by_name = {name.casefold(): name for name in old}
        if any(name.casefold() in by_name and name != by_name[name.casefold()] for name in new):
            flags.append("Name capitalization requires manual correction in ABS")
            blocked = True
    if field == "series" and removed_series(before, after):
        flags.append("Removes a series membership")
    if field == "description":
        flags.append(
            "Replaces existing description" if before else "Confirm source and recording before selecting"
        )
    if field == "publishedYear" and not restorable_year(before):
        flags.append("Original publication year cannot be safely restored")
        blocked = True
    return flags, blocked


def validate_reason(change, editorial_allowed=False):
    reason = change["reason"]
    evidence = change.get("evidence")
    source = change["source"]
    if not clean_text(reason) or not reason.strip() or not clean_text(source) or not source.strip():
        raise ValueError("Every correction requires a reason and source.")
    if evidence is not None and not clean_text(evidence):
        raise ValueError("Evidence must be text.")
    if not (evidence and evidence.strip()) and not editorial_allowed:
        raise ValueError("Factual corrections require evidence.")
    if change["confidence"] not in ("high", "medium", "low"):
        raise ValueError("Confidence must be high, medium, or low.")
    return reason, evidence or "", source


def validate_change(field, change, current):
    keys(change, ("before", "after", "reason", "confidence", "source"), ("evidence",))
    target = validate_target(field, change["after"])
    before = current[field]
    if compact(change["before"]) != compact(before):
        raise ValueError("Before value does not match the snapshot.")
    # An identified cataloging rule can justify a style change.
    editorial = field in ("title", "subtitle", "series", "genres", "tags") and (
        clean_text(change["reason"]) and change["reason"].casefold().startswith(EDITORIAL)
    )
    reason, evidence, source = validate_reason(change, editorial)
    if field == "series" and any(name.casefold() not in reason.casefold() for name in removed_series(before, target)):
        raise ValueError("Give an explicit reason naming each removed series membership.")
    if before == target or (field in ("description", "series") and same_value(field, before, target)):
        return None
    if (evidence or reason).casefold().startswith(EDITORIAL) and field in (
        "authors", "narrators", "asin", "description", "publishedYear"
    ):
        raise ValueError(f"{field} requires factual evidence.")
    return before, target, reason, evidence, source


def validation_context(snapshot, iid):
    if not isinstance(iid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", iid):
        return "item ID unavailable or invalid"
    try:
        title = snapshot["books"][iid]["media"]["metadata"]["title"]
    except (KeyError, TypeError):
        title = None
    context = f"item ID {iid}"
    if isinstance(title, str) and title:
        context += f", book {compact(title[:120] + ('…' if len(title) > 120 else ''))}"
    return context


def make_manifest(snapshot, document):
    keys(document, ("schema_version", "snapshot_id", "items"))
    if type(document["schema_version"]) is not int or document["schema_version"] != 2:
        raise ValueError("Expected correction schema version 2.")
    if document["snapshot_id"] != snapshot["snapshot_id"]:
        raise ValueError("Correction snapshot mismatch.")
    if not isinstance(document["items"], list):
        raise TypeError("items must be an array.")
    scope = digest([snapshot["server"], snapshot["library_id"]])
    rows, errors = [], []
    seen = set()
    for index, item in enumerate(document["items"]):
        iid = item.get("item_id") if isinstance(item, dict) else None
        context = validation_context(snapshot, iid)
        location = f"$.items[{index}]"
        try:
            keys(item, ("item_id", "identity", "changes"))
            if not isinstance(iid, str) or iid in seen or iid not in snapshot["books"]:
                raise ValueError("Duplicate or unknown item ID.")
            seen.add(iid)
            book = Book(snapshot["books"][iid])
            identity = digest(book.identity)
            if book.id != iid or book.raw["libraryId"] != snapshot["library_id"] or item["identity"] != identity:
                raise ValueError("Item identity or library mismatch.")
            if not isinstance(item["changes"], dict) or not item["changes"]:
                raise ValueError("changes must be a nonempty object.")
        except (ValueError, TypeError) as exc:
            errors.append(f"{location} ({context}, item-level): {exc}")
            continue

        current = book.values
        for field, change in sorted(item["changes"].items()):
            try:
                validated = validate_change(field, change, current)
            except (ValueError, TypeError) as exc:
                label = field if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", field) else None
                field_location = f"{location}.changes" + (f".{label}" if label else "")
                errors.append(f"{field_location} ({context}, field {label or 'invalid name'}): {exc}")
                continue
            if validated is None:
                continue
            before, target, reason, evidence, source = validated
            flags, blocked = flags_for(book, field, before, target)
            change_id = digest([scope, snapshot["snapshot_id"], iid, identity, field, before, target])
            rows.append({
                "change_id": change_id, "item_id": iid, "identity": identity,
                "title": current["title"], "authors": current["authors"], "field": field,
                "before": before, "after": target, "reason": reason, "evidence": evidence,
                "confidence": change["confidence"], "source": source, "flags": flags,
                "blocked": blocked, "selected": change["confidence"] == "high" and not flags,
            })
    if errors:
        raise ValueError(f"Correction validation failed ({len(errors)} errors).\n" + "\n".join(errors))
    return {
        "snapshot_id": snapshot["snapshot_id"], "snapshot_digest": digest(snapshot), "scope": scope,
        "rows": sorted(rows, key=lambda row: (row["item_id"], row["field"])),
    }


def render_report(manifest, manifest_digest):
    template = files("absmm").joinpath("review.html").read_text(encoding="utf-8")
    embedded = compact({"manifest_digest": manifest_digest, "rows": manifest["rows"]}).replace("<", r"\u003c")
    return template.replace("<!-- REVIEW_DATA -->", embedded, 1)


def review(directory, corrections_path, token=""):
    document = read_json(corrections_path)
    if contains_token(document, token):
        raise ValueError("Corrections contain the configured credential.")
    keys(document, ("schema_version", "snapshot_id", "items"))
    snapshot = snapshot_at(directory, document["snapshot_id"])
    if contains_token(snapshot, token):
        raise ValueError("The private snapshot contains the configured credential. Export again.")
    manifest = make_manifest(snapshot, document)
    fingerprint = digest(manifest)
    base = directory / f"review-{fingerprint}"
    write_text(base.with_suffix(".json"), compact(manifest))
    write_text(base.with_suffix(".html"), render_report(manifest, fingerprint))
    return base.with_suffix(".html"), len(manifest["rows"])


def corrections_from_rows(manifest):
    keys(manifest, ("snapshot_id", "snapshot_digest", "scope", "rows"))
    if not isinstance(manifest["rows"], list):
        raise TypeError("Invalid manifest rows.")
    items = {}
    for row in manifest["rows"]:
        keys(row, (
            "change_id", "item_id", "identity", "title", "authors", "field", "before", "after",
            "reason", "evidence", "confidence", "source", "flags", "blocked", "selected",
        ))
        item = items.setdefault(row["item_id"], {
            "item_id": row["item_id"], "identity": row["identity"], "changes": {},
        })
        if row["field"] in item["changes"]:
            raise ValueError("Duplicate manifest field.")
        item["changes"][row["field"]] = {
            key: row[key] for key in ("before", "after", "reason", "evidence", "confidence", "source")
        }
    return {"schema_version": 2, "snapshot_id": manifest["snapshot_id"], "items": list(items.values())}


def edit_value(field, text):
    if not isinstance(text, str):
        raise TypeError("An edit must contain raw text.")
    if field in ("authors", "narrators", "series", "tags", "genres", "subtitle", "asin"):
        text = strict_json(text)
    return validate_target(field, text)


def load_approval(directory, approval_path, limit, token=""):
    approval = read_json(approval_path)
    if contains_token(approval, token):
        raise ValueError("Approved content contains the configured credential.")
    keys(approval, ("manifest_digest", "approvals"))
    fingerprint = identifier(approval["manifest_digest"], 64)
    manifest = read_json(directory / f"review-{fingerprint}.json", LOCAL_LIMIT)
    if contains_token(manifest, token):
        raise ValueError("The review manifest contains the configured credential.")
    if digest(manifest) != fingerprint:
        raise ValueError("Manifest digest does not match the approval.")
    snapshot = snapshot_at(directory, manifest.get("snapshot_id") if isinstance(manifest, dict) else None)
    if contains_token(snapshot, token):
        raise ValueError("The private snapshot contains the configured credential. Export again.")
    validated = make_manifest(snapshot, corrections_from_rows(manifest))
    if compact(validated) != compact(manifest):
        raise ValueError("Manifest differs from the validated corrections.")
    if not isinstance(approval["approvals"], list):
        raise TypeError("approvals must be an array.")
    rows = {row["change_id"]: row for row in validated["rows"]}
    selected = {}
    seen = set()
    for entry in approval["approvals"]:
        keys(entry, ("change_id",), ("edit",))
        change_id = identifier(entry["change_id"], 64)
        if change_id in seen or change_id not in rows:
            raise ValueError("Duplicate or unknown change ID.")
        seen.add(change_id)
        row = rows[change_id]
        if row["blocked"]:
            raise ValueError(f"Selected change is unsupported for {row['field']}.")
        field, before = row["field"], row["before"]
        target = edit_value(field, entry["edit"]) if "edit" in entry else row["after"]
        if contains_token(target, token):
            raise ValueError("Approved content contains the configured credential.")
        if field == "series" and removed_series(before, target) - removed_series(before, row["after"]):
            raise ValueError("A new series removal needs a revised proposal and a removal reason.")
        book = Book(snapshot["books"][row["item_id"]])
        flags, blocked = flags_for(book, field, before, target)
        if blocked:
            raise ValueError(f"Selected edit is unsupported for {field}.")
        if compact(before) == compact(target) or (
            field in ("description", "series") and same_value(field, before, target)
        ):
            continue
        selected.setdefault(book.id, {})[field] = {"before": before, "after": target, "flags": flags}
    books = sorted(selected)
    return snapshot, fingerprint, {iid: selected[iid] for iid in books[:limit]}, books[limit:] if limit else []
