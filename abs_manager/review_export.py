"""Build a metadata-only review bundle from an existing local snapshot."""

import json
import re
from collections import defaultdict

from .locking import operation_lock
from .models import FIELDS, WRITE_FIELDS, compact, digest
from .persistent_state import identity
from .store import now

MEDIA_FIELDS = ("tags", "duration", "numTracks", "numAudioFiles", "numChapters", "numEbooks", "ebookFormat")
CHAPTER_FIELDS = ("id", "title", "start", "end", "position", "index")
REVIEW_METADATA_FIELDS = {
    "genres": "genres", "publishedYear": "publication_year", "publishedDate": "publication_date",
    "publisher": "publisher", "language": "language", "isbn": "isbn", "asin": "asin",
    "identifiers": "identifiers", "edition": "edition", "abridged": "abridged",
}
AUDIO_FIELDS = ("index", "trackNumFromMeta", "discNumFromMeta", "format", "duration", "bitRate",
                "language", "codec", "timeBase", "channels", "channelLayout", "sampleRate", "bitDepth")
EMBEDDED_FIELDS = (
    "title", "subtitle", "album", "artist", "albumArtist", "author", "composer", "narrator",
    "translator", "publisher", "year", "date", "description", "comment", "synopsis", "genre",
    "series", "seriesPart", "seriesPosition", "movement", "movementName", "language", "isbn",
    "asin", "audibleAsin", "track", "disc", "copyright", "edition", "abridged",
)
EMBEDDED_KEYS = {field.lower() for field in EMBEDDED_FIELDS}
PRIVATE_KEY = re.compile(
    r"authorization|authentication|credentials?|password|passwd|apikey|cookie|"
    r"(?:access|refresh|auth|bearer|session|id|api)token|(?:client|api|auth|shared)secret|"
    r"^(?:token|tokens|secret|secrets|progress)$|"
    r"playback|mediaprogress|readingprogress|bookmark|listening|sessionid|headers|userdata|userid|username|useremail|"
    r"^(?:user|users|email|login|auth|owner|account|raw|file|files|filedata|contentbytes|binary|"
    r"base64|journal|recovery|history|directory|folder|filename|inode|ino)$|"
    r"(?:file|folder|directory|cover|image|absolute|relative|local|rel|full)path|^path$|"
    r"filecontent|audiocontent|ebookcontent|coverbytes|imagebytes|privatekey|lastlistened|lastplayed|"
    r"^(?:currenttime|isfinished|finishedat|lastupdate|session|sessions|deviceid|coverurl|imageurl)$"
)
PRIVATE_TEXT = re.compile(
    r"(?i)(?<![\w:/])(?:[a-z]:[\\/]|\\\\|file:/{1,3}|/(?!/)[^/\s<>]+/)|"
    r"https?://[^/\s@]+@|[?&](?:api[_-]?key|key|token|access[_-]?token|password|signature|x-(?:amz|goog)-[\w-]+)=|"
    r"\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{8,}|^\s*(?:data:|/(?!/))"
)


def _sanitize(value):
    if isinstance(value, dict):
        return {key: _sanitize(child) for key, child in value.items()
                if not PRIVATE_KEY.search(re.sub(r"[^a-z0-9]", "", key.lower()))
                and not PRIVATE_TEXT.search(key)}
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    if isinstance(value, str) and PRIVATE_TEXT.search(value):
        return "[redacted]"
    return value


def _pick(record, fields):
    return {key: record[key] for key in fields if key in record}


def _chapters(chapters):
    if isinstance(chapters, list):
        return [_pick(chapter, CHAPTER_FIELDS) for chapter in chapters if isinstance(chapter, dict)]
    return None


def _credits(value):
    """Normalize names into credit objects while retaining supplied roles and empty values."""
    if isinstance(value, (str, dict)) and value:
        value = [value]
    if isinstance(value, list):
        return [{"name": credit} if isinstance(credit, str) else credit for credit in value]
    return value


def _review_values(book, current):
    metadata = current["metadata"]
    managed = (field for field in FIELDS if field in metadata and (book.is_audio or field != "narrators"))
    review = _sanitize(_pick(book.values, managed))
    review.update({target: metadata[key] for key, target in REVIEW_METADATA_FIELDS.items() if key in metadata})
    for source, target in (("translator", "translators"), ("translators", "translators"),
                           ("contributors", "contributors")):
        if source in metadata:
            review[target] = _credits(metadata[source])
    if "description" in metadata:
        description = metadata["description"]
        review["description"] = {"$ref": "#/current/metadata/description"} if description else description
    if "tags" in current:
        review["tags"] = current["tags"]
    if book.is_audio and "chapters" in current:
        chapters = current["chapters"]
        review["chapters"] = None if chapters is None else [
            _pick(chapter, ("title", "position", "index", "start", "end")) for chapter in chapters
        ]
    if "cover" in current:
        review["cover_present"] = current["cover"]["present"]
    return review


def _current_and_file_evidence(book):
    media = book.raw["media"]
    current = {"metadata": book.metadata, **_pick(media, MEDIA_FIELDS)}
    if "coverPath" in media:
        current["cover"] = {"present": bool(media["coverPath"])}
    if "chapters" in media:
        current["chapters"] = _chapters(media["chapters"])
    ebook = media.get("ebookFile")
    if isinstance(ebook, dict):
        current["ebook_format"] = _pick(ebook, ("format", "ebookFormat", "ext"))
        if isinstance(ebook.get("metadata"), dict):
            current["ebook_format"].update(_pick(ebook["metadata"], ("ext",)))

    files = []
    for audio in media.get("audioFiles") or []:
        if not isinstance(audio, dict):
            continue
        details = _pick(audio, AUDIO_FIELDS)
        if "chapters" in audio:
            details["chapters"] = _chapters(audio["chapters"])
        tags = audio.get("metaTags")
        if isinstance(tags, dict):
            details["embedded_tags"] = {
                key: value for key, value in tags.items()
                if key.lower().removeprefix("tag") in EMBEDDED_KEYS
            }
        files.append(details)
    source_files = {"audio_files": files} if "audioFiles" in media else {}
    if "_imported_embedded" in book.raw and isinstance(book.raw["_imported_embedded"], dict):
        source_files["prior_export_tags"] = _pick(book.raw["_imported_embedded"], EMBEDDED_FIELDS)
    return current, source_files


def _reference_evidence(report, cover_present):
    references, findings = {}, {}
    for field, details in report["fields"].items():
        if field == "cover":
            if cover_present is False:
                findings[field] = {"status": "Missing"}
            continue
        assessment = _pick(details, ("status", "disposition", "reason_code", "quality_applicable", "hygiene"))
        if details["reason_code"] not in ("optional_absent", "not_applicable", "inspection_deferred",
                                          "evidence_insufficient"):
            findings[field] = assessment
        for entry in details["provenance"]:
            if "candidate_id" not in entry and entry["source"] != "Calibre catalog":
                continue
            reference = _pick(entry, ("source", "value", "match", "imported", "catalog_id", "catalog_digest"))
            if "lookups" in entry:
                reference["captures"] = [_pick(lookup, ("captured", "state")) for lookup in entry["lookups"]]
            references.setdefault(field, []).append(reference)
    return {"references": references, "quality": findings}


def export_review_bundle(store, audit_id, *, exported_at=None):
    """Return UTF-8 JSON bytes. Only read saved data under the existing operation lock."""
    with operation_lock(store.path):
        return _export(store, audit_id, exported_at)


def _export(store, audit_id, exported_at):
    try:
        audit = store.audit(audit_id)
    except IndexError:
        raise ValueError("Select an existing completed snapshot.") from None
    if audit["status"] != "complete" or not audit["finished"]:
        raise ValueError("Select an existing completed snapshot.")
    books = sorted(store.books(audit_id), key=lambda book: book.id)
    members = json.loads(audit["membership"]) if audit["membership"] is not None else None
    if (any(book.raw["libraryId"] != audit["library"] for book in books)
            or members is not None and set(members) != {book.id for book in books}
            or audit["expected"] is not None and audit["expected"] != len(books)):
        raise ValueError("Snapshot inventory does not match the saved library membership.")

    reports = {row["item_id"]: row for row in store.verification_report(audit_id)}
    staged = defaultdict(list)
    for row in store.findings(audit_id):
        if row["actionable"] and (row["status"] == "approved" or
                row["status"] == "pending" and row["kind"] in ("manual", "external", "confirmed_target")):
            staged[row["item_id"]].append({"field": row["field"], "proposed": row["target"]})
    corrections = defaultdict(list)
    for row in store.corrections(audit_id):
        if row["status"] not in ("discarded", "observed_in_abs") and row["audit_id"] <= audit_id:
            corrections[row["item_id"]].append(row)
    bindings = {row["item_id"]: row for row in store.rows(
        "SELECT * FROM calibre_bindings WHERE server=? AND library=?", (audit["server"], audit["library"]))}
    preferences = sorted(
        (_pick(row, ("field", "value", "decision")) for row in store.category_preferences(audit_id)),
        key=lambda row: (row["field"], row["value"]),
    )

    records = []
    for book in books:
        fingerprint = identity(book)
        current, files = _current_and_file_evidence(book)
        current = _sanitize(current)
        review = _review_values(book, current)
        evidence = _reference_evidence(reports[book.id], review.get("cover_present"))
        evidence["source_files"] = files
        binding = bindings.get(book.id)
        if not book.is_audio and binding and binding["identity_digest"] == fingerprint:
            evidence["calibre_binding"] = {
                **_pick(binding, ("calibre_id", "calibre_uuid")),
                "library_fingerprint": digest(binding["calibre_library"]),
            }
        proposals = staged[book.id] + [
            _pick(row, ("field", "proposed")) for row in corrections[book.id]
            if row["field"] != "cover" and identity(book, row["field"]) == row["identity_digest"]
        ]
        observation = _pick(reports[book.id]["abs_observation"], ("source_audit", "fresh_abs", "captured"))
        if audit["read_only"]:
            observation = {"source_audit": audit_id, "fresh_abs": False}
        unique_proposals = {compact(proposal): proposal for proposal in proposals}
        records.append({
            "item_id": book.id, "identity_digest": fingerprint,
            "media_type": "audiobook" if book.is_audio else "ebook",
            "managed_values_digest": digest(book.values),
            "observation": observation,
            "current": current,
            "review_values": review,
            "evidence": _sanitize(evidence),
            "existing_corrections": _sanitize([unique_proposals[key] for key in sorted(unique_proposals)]),
        })

    bundle = {
        "schema_version": 1,
        "source": {
            "application": "abs-metadata-manager", "snapshot_id": audit_id, "library_id": audit["library"],
            "library_name": _sanitize(audit["name"]), "snapshot_started_at": audit["started"],
            "snapshot_completed_at": audit["finished"], "offline_import": bool(audit["read_only"]),
            "library_scope_fingerprint": digest([audit["server"], audit["library"]]),
            "book_count": len(books),
        },
        "authority": {
            "audiobook": {"current": "Audiobookshelf", "guarded_write_fields": [] if audit["read_only"] else list(WRITE_FIELDS),
                          "other_fields": "native_or_manual"},
            "ebook": {"current": "Audiobookshelf", "corrections": "Calibre", "guarded_write_fields": [],
                      "other_fields": "manual_in_calibre"},
        },
        "category_preferences": _sanitize(preferences),
        "books": records,
    }
    bundle["content_digest"] = digest(bundle)
    bundle["exported_at"] = now() if exported_at is None else exported_at
    return (compact(bundle) + "\n").encode("utf-8")
