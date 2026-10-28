from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .models import Book, canon, person_key, sequence

WOT = {name.casefold(): str(i) for i, name in enumerate([
    "The Eye of the World", "The Great Hunt", "The Dragon Reborn", "The Shadow Rising",
    "The Fires of Heaven", "Lord of Chaos", "A Crown of Swords", "The Path of Daggers",
    "Winter's Heart", "Crossroads of Twilight", "Knife of Dreams", "The Gathering Storm",
    "Towers of Midnight", "A Memory of Light"], 1)}
HP = {str(i): "Harry Potter and " + name for i, name in enumerate([
    "the Philosopher's Stone", "the Chamber of Secrets", "the Prisoner of Azkaban",
    "the Goblet of Fire", "the Order of the Phoenix", "the Half-Blood Prince", "the Deathly Hallows"], 1)}
SERIES_ALIASES = {"Dresden Files": "The Dresden Files", "Chidren of Time": "Children of Time"}


def embedded_title_key(value, series_name):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s*\(Unabridged\)\s*$", "", value, flags=re.IGNORECASE)
    if series_name:
        value = re.sub(r":\s*" + re.escape(series_name) + r",\s*Book\s+\d+(?:\.\d+)?\s*$", "", value,
                       flags=re.IGNORECASE)
    return canon(value)


@dataclass
class Finding:
    field: str
    kind: str
    current: object
    proposed: object
    confidence: str
    reason: str
    evidence: dict
    actionable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def folder_evidence(path: str) -> dict:
    parts = path.replace("\\", "/").rstrip("/").split("/")
    if parts and re.search(r"\.(m4b|mp3|epub)$", parts[-1], re.IGNORECASE):
        parts.pop()
    leaf = parts[-1] if parts else ""
    narrator = re.search(r"\s*\{([^}]+)}\s*$", leaf)
    clean = leaf[:narrator.start()].strip() if narrator else leaf
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:-\s*|\s+)(.+)$", clean)
    return {"title": match[2].strip() if match else clean,
            "sequence": sequence(match[1]) if match else None,
            "series": parts[-2] if len(parts) >= 3 else None,
            "author": parts[-3] if len(parts) >= 3 else None,
            "narrator_credit": narrator[1] if narrator else None}


def embedded_evidence(book: Book) -> dict:
    # Only tags already exposed by ABS; no ffprobe calls or audio-file access.
    files = book.raw["media"].get("audioFiles", [])
    tags = [f.get("metaTags", {}) for f in files if isinstance(f, dict)]
    result = {}
    for field, keys in {"title": ("tagTitle", "title"), "album": ("tagAlbum", "album"),
                        "artist": ("tagArtist", "artist"), "composer": ("tagComposer", "composer")}.items():
        values = {t[k] for t in tags for k in keys if isinstance(t.get(k), str) and t[k].strip()}
        if len(values) == 1:
            result[field] = next(iter(values))
        elif values:
            result[field] = sorted(values)
    # Imported private snapshots supply explicitly labelled prior evidence.
    result.update(book.raw.get("_imported_embedded", {}))
    return result


def analyze(book: Book) -> tuple[dict, list[Finding]]:
    current = book.values
    title = current["title"] or ""
    series = current["series"]
    folder = folder_evidence(book.raw.get("relPath") or book.raw["path"])
    embedded = embedded_evidence(book)
    evidence = {"folder": folder, "embedded": embedded}
    normalized = {**current, "series": [{**s, "sequence": sequence(s["sequence"])} for s in series]}
    findings: list[Finding] = []

    def note(field, kind, reason, proposed=None, confidence="info", actionable=False):
        if actionable and proposed == current[field]:
            return
        findings.append(Finding(field, kind, current.get(field), proposed, confidence, reason, evidence, actionable))

    for field in ("title", "authors", "narrators"):
        if not current[field]:
            note(field, "missing", "Missing metadata. Verify the recording and contributor roles before editing.")
    if not current["subtitle"]:
        # Empty subtitles are normal; no finding without contradictory evidence.
        normalized["subtitle"] = ""

    sname = series[0]["name"] if len(series) == 1 else ""
    seq = sequence(series[0]["sequence"]) if len(series) == 1 else None
    clean = re.sub(r"\s*\(Unabridged\)\s*$", "", title, flags=re.IGNORECASE)
    title_reasons = []
    if clean != title:
        clean = clean.strip()
        title_reasons.append("Remove the terminal Unabridged edition label.")

    # Match a series suffix precisely; do not strip legitimate title: subtitle punctuation.
    suffix = re.search(r":\s*([^:]+?),\s*Book\s+(\d+(?:\.\d+)?)\s*$", clean, re.IGNORECASE)
    if suffix and (not sname or canon(suffix[1]) == canon(sname)):
        clean = clean[:suffix.start()].strip()
        title_reasons.append("Move the explicit series and book-number suffix out of the title.")
        if not series:
            note("series", "missing", "Explicit series and position in title; confirm this edition.",
                 [{"name": suffix[1], "sequence": sequence(suffix[2])}], "medium", True)

    if sname:
        prefixed = re.sub(r"^" + re.escape(sname) + r"\s*(?:,?\s*Book\s*)?(\d+(?:\.\d+)?)\s*[-:]\s*",
                          "", clean, flags=re.IGNORECASE)
        if prefixed != clean:
            clean = prefixed
            title_reasons.append("Remove the explicit series-number prefix.")
        numbered = re.match(r"^(\d+(?:\.\d+)?)\s*(?:-\s*|\s+)(.+)$", clean)
        if numbered:
            candidate = numbered[2].strip()
            corroborated = (canon(candidate) == canon(folder["title"]) and
                            canon(candidate) in {embedded_title_key(embedded.get("title"), sname),
                                                 embedded_title_key(embedded.get("album"), sname)})
            if sequence(numbered[1]) == seq or corroborated:
                clean = candidate
                title_reasons.append("Number prefix matches series position or folder plus embedded title.")
        if sname == "Harry Potter" and seq in HP and re.match(r"^HP\W*\d", title, re.IGNORECASE):
            clean = HP[seq]
            title_reasons.append("Replace the HP ripping prefix using the existing Harry Potter position.")
        if sname == "The Wheel of Time" and canon(clean) in WOT:
            if seq is None:
                note("series", "missing", "Missing position; known mainline Wheel of Time title.",
                     [{"name": sname, "sequence": WOT[canon(clean)]}], "medium", True)
            elif seq != WOT[canon(clean)]:
                note("series", "conflict", "Existing position differs from known mainline order; investigate edition.")
            if current["authors"] == ["The Wheel of Time"] and int(WOT[canon(clean)]) <= 11:
                note("authors", "conflict", "Series name imported as author of a Robert Jordan mainline volume.",
                     ["Robert Jordan"], "medium", True)

    if clean and clean != title:
        note("title", "corroborated", " ".join(title_reasons), clean, "high", True)
    elif title and (title != title.strip() or "  " in title):
        note("title", "formatting", "Whitespace variation is harmless; retained for review.")
    normalized["title"] = clean

    normalized_series = [{"name": SERIES_ALIASES.get(s["name"], s["name"]),
                          "sequence": s["sequence"]} for s in series]
    if normalized_series != series:
        note("series", "formatting", "Known canonical series spelling; all positions are retained.",
             normalized_series, "high", True)
    elif len(series) == 1 and seq is None and folder["sequence"]:
        folder_series = SERIES_ALIASES.get(folder["series"], folder["series"])
        title_agrees = canon(clean) == canon(folder["title"])
        tag_agrees = canon(clean) in {embedded_title_key(embedded.get("title"), sname),
                                     embedded_title_key(embedded.get("album"), sname)}
        if folder_series == sname and title_agrees and tag_agrees:
            note("series", "missing", "Position missing; folder and embedded title corroborate this book.",
                 [{"name": sname, "sequence": folder["sequence"]}], "medium", True)
        elif not any(f.field == "series" for f in findings):
            note("series", "missing", "Series position missing. Folder alone does not authorize a replacement.")
    if seq and folder["sequence"] and seq != folder["sequence"]:
        note("series", "conflict", "Folder position differs. Existing series data is retained; investigate.")
    if sname and folder["series"] and canon(sname) != canon(SERIES_ALIASES.get(folder["series"], folder["series"])):
        note("series", "conflict", "Folder series differs from observed metadata; no overwrite proposed.")

    authors = ["Sarah J. Maas" if person_key(a) == "sarahjmaas" else a for a in current["authors"]]
    normalized["authors"] = authors
    normalized["series"] = [{**s, "sequence": sequence(s["sequence"])} for s in normalized_series]
    if authors != current["authors"]:
        note("authors", "formatting", "Canonical Sarah J. Maas initials; other authors are retained.", authors, "high", True)
    hp_record = sname == "Harry Potter" or title.startswith("Harry Potter and ")
    if (hp_record and len(current["authors"]) == len(current["narrators"]) == 1
            and person_key(current["authors"][0]) == "stephenfry"
            and person_key(current["narrators"][0]) == "jkrowling"):
        note("authors", "conflict", "Harry Potter author/narrator roles appear reversed in the Stephen Fry edition.",
             ["J.K. Rowling"], "high", True)
        note("narrators", "conflict", "Retain the Stephen Fry recording credit in its narrator role.",
             ["Stephen Fry"], "high", True)
    if folder["narrator_credit"] and current["narrators"] and canon(folder["narrator_credit"]) != canon(
            ", ".join(current["narrators"])):
        note("narrators", "conflict", "Folder credit differs. Edition-specific live narrator credits are retained.")
    if folder["title"] and clean and canon(folder["title"]) != canon(clean):
        note("title", "suspicious", "Folder title differs; it may represent another edition or a legitimate subtitle.")
    embedded_title = embedded.get("title")
    if isinstance(embedded_title, str) and canon(embedded_title) not in (canon(title), canon(clean)):
        note("title", "conflict", "Embedded title differs. Embedded tags may be stale; inspect before editing.")

    media = book.raw["media"]
    if not media.get("coverPath"):
        note("cover", "missing", "No library cover. Cover changes are outside this MVP.")
    if book.is_audio:
        chapters = media.get("chapters", [])
        count = len(chapters) or media.get("numChapters", 0)
        if not count:
            note("chapters", "missing", "No chapters exposed by ABS. Never invent timings.")
        elif chapters and any(re.fullmatch(r"(?:chapter|track|part)\s*\d+", c.get("title", ""), re.IGNORECASE) for c in chapters):
            note("chapters", "informational", "Generic chapter labels can be legitimate. No changes proposed.")
    return normalized, findings
