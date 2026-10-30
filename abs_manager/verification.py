"""Pure quality assessment and conservative, deterministic evidence matching.

No transport, approval, or mutation capabilities belong in this module.
"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Protocol

from .audit import analyze, embedded_evidence, embedded_title_key, folder_evidence
from .models import Book, canon, compact, digest, sequence

QUALITY_FIELDS = ("title", "subtitle", "authors", "narrators", "translators", "series",
                  "series_position", "identifiers", "cover", "chapters", "embedded")
STATUSES = ("Verified", "Consistent", "Conflict", "Missing", "Unverified", "Not applicable")


class EvidenceAdjudicator(Protocol):
    """Future explanation-only extension; no implementation or automatic AI invocation."""

    def explain(self, values: dict, evidence: list[dict]) -> dict: ...


def names(value) -> list[str]:
    if isinstance(value, str):
        # Commas may be part of contributor names. Never split them heuristically.
        return [value] if value.strip() else []
    if isinstance(value, list):
        result = [x if isinstance(x, str) else x["name"] for x in value
                  if isinstance(x, str) or isinstance(x, dict) and isinstance(x.get("name"), str)]
        return [name for name in result if name.strip()]
    return []


def isbn_key(value) -> str:
    text = re.sub(r"[^0-9X]", "", str(value).upper())
    if len(text) == 10 and text[:9].isdigit() and (text[-1].isdigit() or text[-1] == "X"):
        digits = [int(c) if c.isdigit() else 10 for c in text]
        if sum((10 - i) * n for i, n in enumerate(digits)) % 11:
            return ""
        base = "978" + text[:9]
        return base + str((-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base))) % 10)
    if len(text) == 13 and text.isdigit() and not sum(
            int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(text)) % 10:
        return text
    return ""


def values(book: Book) -> dict:
    m, media = book.metadata, book.raw["media"]
    return {**book.values, "translators": names(m.get("translators", m.get("translator"))),
            "series_position": [sequence(s["sequence"]) for s in book.values["series"]],
            "identifiers": {k: m[k] for k in ("isbn", "asin") if m.get(k)},
            "cover": media.get("coverPath"), "chapters": media.get("chapters", []),
            "embedded": embedded_evidence(book)}


def comparable(field, value):
    if field in ("authors", "narrators", "translators"):
        return sorted(re.sub(r"[^\w]", "", canon(n), flags=re.UNICODE) for n in names(value))
    if field == "series":
        return sorted(canon(s["name"]) for s in value or [])
    if field == "series_position":
        return [sequence(s) for s in value or []]
    if field == "identifiers":
        return {k: isbn_key(v) if k == "isbn" else str(v).upper() for k, v in (value or {}).items()}
    return canon(value) if isinstance(value, str) else value


def candidate(raw: dict, source: str, url: str = "", *, work_only=False) -> dict:
    """Normalize ABS/provider fields without discarding the provider's original record."""
    for key in ("title", "subtitle", "language", "isbn", "asin", "url"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise ValueError("Invalid candidate text field.")
    series = raw.get("series", [])
    if isinstance(series, str):
        series = [{"name": series, "sequence": sequence(raw.get("seriesNumber"))}] if series else []
    if isinstance(series, dict):
        series = [series]
    series = [{"name": s["name"], "sequence": sequence(s.get("sequence"))}
              for s in series if isinstance(s, dict) and isinstance(s.get("name"), str)]
    v = {"title": raw.get("title"), "authors": names(raw.get("authors", raw.get("author"))),
         "narrators": names(raw.get("narrators", raw.get("narrator"))),
         "translators": names(raw.get("translators", raw.get("translator"))),
         "series": series, "series_position": [s["sequence"] for s in series],
         "identifiers": {k: raw[k] for k in ("isbn", "asin") if raw.get(k)}}
    if "subtitle" in raw:
        v["subtitle"] = raw["subtitle"]
    result = {"source": source, "url": raw.get("url") or url, "values": v,
              "language": raw.get("language"), "duration": raw.get("duration"),
              "abridged": raw.get("abridged"), "publisher": raw.get("publisher"),
              "published": raw.get("publishedDate", raw.get("publishedYear")),
              "cover": raw.get("cover"), "work_only": work_only, "raw": raw}
    result["id"] = digest(result)
    return result


def match(book: Book, item: dict) -> dict:
    saved, other = values(book), item["values"]
    signals, contradictions = [], []
    for field in ("title", "authors", "series", "series_position", "narrators"):
        if saved.get(field) and other.get(field):
            equal = comparable(field, saved[field]) == comparable(field, other[field])
            if field == "title":
                series_name = saved["series"][0]["name"] if len(saved["series"]) == 1 else ""
                equal = embedded_title_key(saved[field], series_name) == embedded_title_key(other[field], series_name)
            (signals if equal else contradictions).append(field)
    exact_ids = []
    for key in ("asin", "isbn"):
        a, b = saved["identifiers"].get(key), other.get("identifiers", {}).get(key)
        if a and b:
            equal = (bool(isbn_key(a)) and isbn_key(a) == isbn_key(b) if key == "isbn" else
                     bool(re.fullmatch(r"[A-Z0-9]{10}", str(a).upper())) and str(a).upper() == str(b).upper())
            (signals if equal else contradictions).append(key)
            if equal:
                exact_ids.append(key)
    language_aliases = {"eng": "en", "english": "en", "fre": "fr", "fra": "fr",
                        "french": "fr", "deu": "de", "ger": "de", "german": "de"}
    a, b = book.metadata.get("language"), item.get("language")
    if a and b:
        a, b = canon(a), canon(b)
        equal = language_aliases.get(a, a) == language_aliases.get(b, b)
        (signals if equal else contradictions).append("language")
    a, b = book.raw["media"].get("duration"), item.get("duration")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0 and b > 0:
        (signals if abs(a - b) <= max(60, a * .02) else contradictions).append("duration")
    a, b = book.metadata.get("abridged"), item.get("abridged")
    if a is not None and b is not None:
        (signals if a == b else contradictions).append("abridgment")
    for field, a in (("publisher", book.metadata.get("publisher")),
                     ("published", book.metadata.get("publishedYear"))):
        b = item.get(field)
        if a and b:
            (signals if canon(str(a)) == canon(str(b)) else contradictions).append(field)
    # ISBNs from work-level aggregations never identify a particular recording.
    work = ("title" in signals and "authors" in signals) or bool(exact_ids)
    identity_conflicts = {"title", "authors", "asin", "isbn", "language", "abridgment"} & set(contradictions)
    recording = (book.is_audio and not item.get("work_only") and work and not identity_conflicts
                 and (bool(exact_ids) or {"narrators", "duration"} <= set(signals))
                 and not {"duration"} & set(contradictions)
                 and (bool(exact_ids) or "narrators" not in contradictions))
    return {"candidate_id": item["id"], "signals": signals, "contradictions": contradictions,
            "level": "recording" if recording else "work" if work and not identity_conflicts else "unmatched"}


def chapter_assessment(book: Book) -> tuple[str, str]:
    media = book.raw["media"]
    chapters = media.get("chapters", [])
    if not isinstance(chapters, list) or not all(isinstance(c, dict) for c in chapters):
        return "Unverified", "Chapter schema unavailable or invalid; inspect the captured JSON."
    if not chapters:
        return ("Unverified", "Chapter count exists but timings were not captured.") if media.get("numChapters") else (
            "Missing", "No chapters exposed by the snapshot; never invent timings.")
    previous_end = 0
    duration = media.get("duration")
    duration = duration if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 else None
    incomplete = False
    for chapter in chapters:
        start, end = chapter.get("start"), chapter.get("end")
        if start is None or end is None:
            incomplete = True
            continue
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                   for x in (start, end)):
            return "Conflict", "Chapter timestamps must be finite numbers."
        if start < 0 or end <= start or start < previous_end or duration and end > duration + .05:
            return "Conflict", "Negative, overlapping, unordered, or out-of-bound chapter timestamps."
        previous_end = end
    generic = any(re.fullmatch(r"(?:chapter|track|part)\s*\d+", c.get("title") or "", re.IGNORECASE)
                  for c in chapters if isinstance(c.get("title", ""), (str, type(None))))
    return ("Unverified" if incomplete else "Consistent",
            "Generic labels are present, not absent chapters. " +
            ("Some timings unavailable." if incomplete else "Captured timestamps are valid; chapter content is unverified.")
            if generic else "Captured timestamps assessed; names/content need edition evidence.")


def assess(book: Book, evidence: list[dict] = (), *, selected: str | None = None,
           reviews: dict | None = None, calibre: dict | None = None) -> dict:
    saved = values(book)
    _, findings = analyze(book)
    folder = folder_evidence(book.raw.get("relPath") or book.raw["path"])
    if not book.is_audio:
        folder["title"] = re.sub(r"\s*\(\d+\)$", "", folder["title"])
        folder["limitation"] = "Calibre names may be truncated/sanitized; folders cannot prove a conflict."
    usable = [lookup.get("previous_success", lookup) if lookup.get("state") == "unavailable" else lookup
              for lookup in evidence]
    candidates = [c for lookup in usable if lookup.get("state") == "ok" for c in lookup.get("candidates", [])]
    matches = [{**match(book, c), "candidate": c} for c in candidates]
    plausible = [m for m in matches if m["level"] != "unmatched"]
    chosen = next((m for m in plausible if m["candidate_id"] == selected), None)
    # Human edition selection is recorded separately and never suppresses ambiguity automatically.
    automatic = plausible[0] if len(plausible) == 1 else None
    external = chosen or automatic
    fields = {}
    for field in QUALITY_FIELDS:
        value = saved[field]
        status, reason = "Unverified", "Saved value captured; no independent corroboration."
        provenance = [{"source": "Audiobookshelf snapshot", "item_id": book.id, "value": value}]
        if field in ("narrators", "chapters", "embedded") and not book.is_audio:
            status, reason = "Not applicable", "Ordinary ebook; Calibre remains authoritative."
        elif field == "chapters":
            status, reason = chapter_assessment(book)
        elif field == "cover":
            status, reason = ("Unverified", "Cover present; image correctness requires visual edition review.") if value else (
                "Missing", "No saved cover path. External cover presence does not verify the saved image.")
        elif field == "embedded":
            status, reason = ("Unverified", "Captured tags are evidence, not authority; composer is not assumed narrator.") if value else (
                "Unverified", "Embedded tags unavailable in snapshot.")
            checks = []
            for tag in ("title", "album", "artist"):
                if isinstance(value.get(tag), str):
                    reference = saved["title"] if tag != "artist" else ", ".join(saved["authors"])
                    checks.append(canon(value[tag]) == canon(reference or ""))
            if checks:
                status = "Consistent" if all(checks) else "Conflict"
                reason += " Stale tags can reappear after reimport without making saved metadata wrong."
        elif field == "series_position" and not saved["series"]:
            status, reason = "Not applicable", "No series assigned; standalone books need no position."
        elif not value and field in ("title", "authors", "narrators"):
            status, reason = "Missing", "Required credit/value is absent; identify the correct edition."
        elif field == "series_position" and any(v is None for v in value):
            status, reason = "Missing", "Assigned series lacks a valid position; omnibus/unnumbered editions need review."
        elif field == "identifiers" and value.get("isbn") and not isbn_key(value["isbn"]):
            status, reason = "Conflict", "Saved ISBN has an invalid length/checksum; confirm the identifier externally."
        elif field == "title" and book.is_audio and value and canon(folder["title"]) == canon(value):
            status, reason = "Consistent", "Saved title agrees with folder evidence; no independent external verification."
            provenance.append({"source": "Folder", "value": folder["title"]})
        relevant = [f.to_dict() for f in findings if f.field == field]
        # Formatting suggestions and weak folder mismatches are not confirmed factual errors.
        if field not in ("chapters", "cover", "embedded") and any(f["kind"] == "conflict" for f in relevant):
            status, reason = "Conflict", "Local evidence differs; inspect provenance before deciding which value is correct."
        if field == "series" and not value and any(f["kind"] == "missing" for f in relevant):
            status, reason = "Missing", "Explicit series evidence is present in the title; confirm the correct assignment."
        if field == "subtitle" and value and (canon(value) == canon(saved["title"] or "") or re.search(
                r"\bBook\s+\d+(?:\.\d+)?\s*$", value, re.IGNORECASE) or
                any(canon(saved["title"] or "").endswith(sep + canon(value)) for sep in (": ", " - "))):
            status, reason = "Conflict", "Possible duplicated title or promotional series subtitle; inspect edition."
        if external and field in external["candidate"]["values"] and status != "Not applicable":
            other = external["candidate"]["values"][field]
            # Missing optional source fields do not assert absence.
            valid = field != "identifiers" or not other.get("isbn") or bool(isbn_key(other["isbn"]))
            if valid and (other or field == "subtitle" and other == ""):
                provenance.append({"source": external["candidate"]["source"], "url": external["candidate"]["url"],
                                   "candidate_id": external["candidate_id"], "value": other,
                                   "match": external["level"], "human_selected": chosen is not None,
                                   "lookups": [{k: lookup[k] for k in ("query", "captured") if k in lookup}
                                               for lookup in usable if any(c["id"] == external["candidate_id"]
                                                                           for c in lookup.get("candidates", []))]})
                equal = comparable(field, value) == comparable(field, other)
                recording_field = field in ("narrators", "series_position") and book.is_audio
                if not recording_field or external["level"] == "recording":
                    status = ("Consistent" if chosen and len(plausible) > 1 else "Verified") if equal else "Conflict"
                    reason = "External evidence agrees." if equal else "External candidate differs; discrepancy requires review."
                    if chosen and len(plausible) > 1:
                        reason += " Human-selected edition; automatic ambiguity remains."
        if len(plausible) > 1 and not chosen and status not in ("Not applicable", "Missing"):
            status, reason = "Unverified", "Ambiguous: multiple plausible editions/candidates; inspect and select an edition."
        if calibre and not book.is_audio and field in calibre["values"]:
            other = calibre["values"][field]
            provenance.append({"source": "Calibre catalog", "catalog_id": calibre["id"], "value": other,
                               "imported": calibre["imported"], "catalog_digest": calibre["catalog_digest"]})
            status = "Consistent" if comparable(field, value) == comparable(field, other) else "Conflict"
            reason = "Compared with authoritative read-only Calibre catalog; catalog is not external verification."
        fields[field] = {"value": value, "status": status, "reason": reason, "provenance": provenance,
                         "local_findings": relevant, "human_review": (reviews or {}).get(field)}
    return {"item_id": book.id, "identity": book.identity, "type": "audiobook" if book.is_audio else "ebook",
            "fields": fields, "folder": folder, "lookups": list(evidence), "matches": matches,
            "ambiguous": len(plausible) > 1, "selected_candidate": selected,
            "edition_review": (reviews or {}).get("edition"),
            "calibre_limitation": None if book.is_audio or calibre else
            "Calibre catalog unavailable/unmatched: limited to Audiobookshelf and filesystem evidence."}


def export_csv(report: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["item_id", "type", "title", "field", "status", "value",
                                                "reason", "provenance", "local_findings", "human_review",
                                                "identity", "folder", "lookups", "matches", "ambiguous",
                                                "selected_candidate", "edition_review", "calibre_limitation"])
    writer.writeheader()
    for book in report:
        for field, assessment in book["fields"].items():
            row = {"item_id": book["item_id"], "type": book["type"], "title": book["fields"]["title"]["value"],
                   "field": field, **{k: assessment[k] for k in ("status", "value", "reason", "provenance",
                                                                "local_findings", "human_review")},
                   **{k: book[k] for k in ("identity", "folder", "lookups", "matches", "ambiguous",
                                          "selected_candidate", "edition_review", "calibre_limitation")}}
            row = {k: v if isinstance(v, str) else compact(v) for k, v in row.items()}
            writer.writerow({k: "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in row.items()})
    return buffer.getvalue()
