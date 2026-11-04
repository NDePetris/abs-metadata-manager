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
from .diagnostics import CandidateError, data_type
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


def contributor_comparison(left, right):
    """Compare a display credit only against complete, separately known names.

    Never infer people by splitting on commas. Require one unique complete
    partition, preserving punctuation within the known names and all input data.
    """
    a, b = names(left), names(right)
    if not a or not b:
        return "Missing contributor information"
    if comparable("narrators", a) == comparable("narrators", b):
        return "Equivalent people"
    if len(a) > 1 and len(b) == 1:
        a, b = b, a
    if len(a) != 1 or len(b) < 2 or len(b) > 16:
        return "Different contributors"
    tokens = lambda text: re.findall(r"\w+", canon(text))
    known = [tokens(name) for name in b]
    text = tokens(a[0])
    if len({tuple(k) for k in known}) != len(known) or any(len(k) < 2 for k in known):
        return "Ambiguous combined string"

    def partitions(offset, remaining):
        if not remaining:
            return int(offset == len(text))
        count = 0
        for i in remaining:
            end = offset + len(known[i])
            if text[offset:end] == known[i]:
                count += partitions(end, remaining - {i})
                if count > 1:
                    return count
        return count

    count = partitions(0, set(range(len(known))))
    return "Equivalent people · formatting differs" if count == 1 else "Ambiguous combined string"


def fields_equal(field, left, right):
    if field == "narrators":
        return contributor_comparison(left, right).startswith("Equivalent people")
    return comparable(field, left) == comparable(field, right)


def candidate(raw: dict, source: str, url: str = "", *, work_only=False) -> dict:
    """Normalize ABS/provider fields without discarding the provider's original record."""
    if not isinstance(raw, dict):
        raise CandidateError(expected="object", actual=data_type(raw), stage="shape")
    for key in ("title", "subtitle", "language", "isbn", "asin", "url", "link", "publisher", "cover"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise CandidateError(key, "string or null", data_type(raw[key]))
    for key in ("authors", "author", "narrators", "narrator", "translators", "translator"):
        value = raw.get(key)
        if value is None:
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if not isinstance(entry, str) and not (isinstance(entry, dict) and isinstance(entry.get("name"), str)):
                raise CandidateError(key, "string or named object", data_type(entry))
    duration = raw.get("duration")
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        raise CandidateError("duration", "finite nonnegative number or null", data_type(duration))
    if raw.get("abridged") is not None and type(raw["abridged"]) is not bool:
        raise CandidateError("abridged", "boolean or null", data_type(raw["abridged"]))
    for key in ("publishedDate", "publishedYear", "id", "uuid", "recordingId", "productId"):
        if raw.get(key) is not None and type(raw[key]) not in (str, int):
            raise CandidateError(key, "string, integer or null", data_type(raw[key]))
    series = raw.get("series") or []
    if raw.get("series") is not None and not isinstance(raw["series"], (str, dict, list)):
        raise CandidateError("series", "string, object, array or null", data_type(raw["series"]))
    if isinstance(series, str):
        if raw.get("seriesNumber") not in (None, "") and sequence(raw["seriesNumber"]) is None:
            raise CandidateError("seriesNumber", "nonnegative decimal or null", data_type(raw["seriesNumber"]))
        series = [{"name": series, "sequence": sequence(raw.get("seriesNumber"))}] if series else []
    if isinstance(series, dict):
        series = [series]
    for entry in series:
        if not isinstance(entry, dict):
            raise CandidateError("series[]", "named object", data_type(entry))
        key = "name" if "name" in entry else "series"
        if not isinstance(entry.get(key), str):
            raise CandidateError("series[]." + key, "string", data_type(entry.get(key)))
        if entry.get("sequence") not in (None, "") and sequence(entry["sequence"]) is None:
            raise CandidateError("series[].sequence", "nonnegative decimal or null", data_type(entry["sequence"]))
    # ABS Audible.cleanResult uses `series`, while saved ABS metadata uses `name`.
    legacy_series = [{"name": s["name"], "sequence": sequence(s.get("sequence"))}
                     for s in series if isinstance(s.get("name"), str)]
    series = [{"name": s.get("name", s.get("series")), "sequence": sequence(s.get("sequence"))}
              for s in series]
    def credits(plural, singular):
        # Preserve the previous explicit-null precedence and identity projection.
        value = raw.get(plural, raw.get(singular))
        return names([value] if isinstance(value, dict) else value)

    v = {"title": raw.get("title"), "authors": credits("authors", "author"),
         "narrators": credits("narrators", "narrator"),
         "translators": credits("translators", "translator"),
         "series": series, "series_position": [s["sequence"] for s in series],
         "identifiers": {k: raw[k] for k in ("isbn", "asin") if raw.get(k)}}
    if "subtitle" in raw:
        v["subtitle"] = raw["subtitle"]
    result = {"source": source, "url": raw.get("url") or url, "values": v,
              "supplied": [f for f, keys in {"title": ("title",), "subtitle": ("subtitle",),
                           "authors": ("authors", "author"), "narrators": ("narrators", "narrator"),
                           "series": ("series",), "identifiers": ("isbn", "asin")}.items()
                           if any(k in raw for k in keys)],
              "language": raw.get("language"), "duration": raw.get("duration"),
              "abridged": raw.get("abridged"), "publisher": raw.get("publisher"),
              "published": raw.get("publishedDate", raw.get("publishedYear")),
              "cover": raw.get("cover"), "work_only": work_only, "raw": raw}
    # A provider's search URL and raw response can change between queries. Keep
    # rejection identities stable for the same provider recording.
    identifier = str(v["identifiers"].get("asin", "")).strip().upper()
    provider_id = next((str(raw[k]) for k in ("id", "uuid", "recordingId", "productId")
                        if isinstance(raw.get(k), (str, int)) and str(raw[k]).strip()), None)
    identity_values = {**v, "series": legacy_series, "series_position": [s["sequence"] for s in legacy_series]}
    recording_key = ({"source": source, "title": canon(v["title"]),
                      "authors": comparable("authors", v["authors"]), "narrators": comparable("narrators", v["narrators"]),
                      "duration": result["duration"], "language": result["language"], "abridged": result["abridged"]}
                     if not work_only and v["title"] and v["authors"] and v["narrators"] and result["duration"] else None)
    identity_data = {"source": source, "asin": identifier} if re.fullmatch(r"[A-Z0-9]{10}", identifier) else {"source": source, "provider_id": provider_id} if provider_id else recording_key if recording_key else {
        "source": source, "values": identity_values, "language": result["language"],
        "duration": result["duration"], "abridged": result["abridged"],
        "publisher": result["publisher"], "published": result["published"], "work_only": work_only}
    try:
        result["id"] = digest(identity_data)
    except (TypeError, ValueError, KeyError):
        raise CandidateError(stage="identity") from None
    return result


def reference_values(match):
    """Omission is not an empty value, including previously captured candidates."""
    item = match["candidate"]
    raw = item.get("raw", {})
    raw = raw.get("volumeInfo", raw)
    aliases = {"title": ("title",), "subtitle": ("subtitle",),
               "authors": ("authors", "author", "author_name"), "narrators": ("narrators", "narrator"),
               "series": ("series",), "identifiers": ("isbn", "asin")}
    supplied = item.get("supplied")
    if supplied is None:
        supplied = [f for f, keys in aliases.items() if any(k in raw for k in keys)
                    or f == "identifiers" and item["values"].get(f)]
    result = {f: v for f, v in item["values"].items() if f in supplied}
    for field in list(result):
        keys = [k for k in aliases.get(field, (field,)) if k in raw]
        if field != "identifiers" and not keys:
            result.pop(field)
            continue
        original = raw[keys[0]] if keys else None
        if field != "identifiers" and original is None:
            result[field] = None
        if field in ("authors", "narrators") and (original is None or not isinstance(original, (str, list, dict))
                                                   or original and not result[field]):
            result[field] = None
    return result


def can_copy_reference(item, match, field):
    supplied = reference_values(match)
    if field not in supplied or supplied[field] is None or match["classification"] == "wrong_work":
        return False
    if field == "identifiers" and not supplied[field]:
        return False  # No usable identifier; never infer a request to clear a group of identifiers.
    if field in ("title", "authors") or item["type"] == "ebook":
        return True
    return not match["candidate"].get("work_only") and (
        match["level"] == "recording" or item["selected_candidate"] == match["candidate_id"])


def match(book: Book, item: dict) -> dict:
    saved, other = values(book), item["values"]
    signals, contradictions = [], []
    for field in ("title", "authors", "series", "series_position", "narrators"):
        if saved.get(field) and other.get(field):
            equal = fields_equal(field, saved[field], other[field])
            if field == "title":
                series_name = saved["series"][0]["name"] if len(saved["series"]) == 1 else ""
                equal = embedded_title_key(saved[field], series_name) == embedded_title_key(other[field], series_name)
            (signals if equal else contradictions).append(field)
    exact_ids = []
    for key in ("asin", "isbn"):
        a, b = saved["identifiers"].get(key), other.get("identifiers", {}).get(key)
        if a and b:
            a, b = ((isbn_key(a), isbn_key(b)) if key == "isbn" else
                    (str(a).strip().upper(), str(b).strip().upper()))
            if key == "asin" and not (re.fullmatch(r"[A-Z0-9]{10}", a) and
                                      re.fullmatch(r"[A-Z0-9]{10}", b)):
                continue
            if not a or not b:
                continue
            equal = a == b
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
    wrong_work = not work or bool({"title", "authors"} & set(contradictions))
    recording_conflicts = {"asin", "narrators", "duration", "abridgment", "language"} & set(contradictions)
    recording = (book.is_audio and not item.get("work_only") and not wrong_work and not recording_conflicts
                 and ("asin" in exact_ids or {"narrators", "duration"} <= set(signals)))
    classification = ("wrong_work" if wrong_work else "correct_recording" if recording else
                      "different_recording" if book.is_audio and not item.get("work_only") and recording_conflicts
                      else "recording_uncertain")
    return {"candidate_id": item["id"], "signals": signals, "contradictions": contradictions,
            "classification": classification,
            "level": "recording" if recording else "work" if not wrong_work else "unmatched"}


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
           reviews: dict | None = None, calibre: dict | None = None, rejected=()) -> dict:
    saved = values(book)
    _, findings = analyze(book)
    folder = folder_evidence(book.raw.get("relPath") or book.raw["path"])
    if not book.is_audio:
        folder["title"] = re.sub(r"\s*\(\d+\)$", "", folder["title"])
        folder["limitation"] = "Calibre names may be truncated/sanitized; folders cannot prove a conflict."
    usable = [lookup.get("previous_success", lookup) if lookup.get("state") == "unavailable" else lookup
              for lookup in evidence]
    candidates = list({c["id"]: c for lookup in usable if lookup.get("state") in ("ok", "partial")
                       for c in lookup.get("candidates", [])}.values())
    rejected = set(rejected)
    matches = [{**match(book, c), "candidate": c, "rejected": c["id"] in rejected} for c in candidates]
    plausible = [m for m in matches if m["level"] != "unmatched" and not m["rejected"]
                 and m["classification"] != "different_recording"]
    chosen = next((m for m in matches if m["candidate_id"] == selected and not m["rejected"]
                   and m["classification"] != "wrong_work"), None)
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
                equal = fields_equal(field, value, other)
                recording_field = field in ("subtitle", "narrators", "translators", "series_position", "identifiers") and book.is_audio
                human_recording = chosen and not external["candidate"].get("work_only")
                if not recording_field or external["level"] == "recording" or human_recording:
                    status = ("Consistent" if chosen else "Verified") if equal else "Conflict"
                    reason = "External evidence agrees." if equal else "External candidate differs; discrepancy requires review."
                    if chosen:
                        reason += " Human-selected edition; selection is not independent recording verification."
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
            "match_classification": external["classification"] if external else
            ("no_match" if not any(not m["rejected"] for m in matches) else "different_recording" if any(
                m["classification"] == "different_recording" and not m["rejected"] for m in matches) else "wrong_work"),
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
