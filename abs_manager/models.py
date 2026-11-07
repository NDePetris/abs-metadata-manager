from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.entities import html5
from typing import Any

FIELDS = ("title", "subtitle", "authors", "narrators", "series")
IMPORTED_FIELDS = ("description", "genres", "publishedYear")
WRITE_FIELDS = FIELDS + IMPORTED_FIELDS


def description_paragraphs(value: str) -> tuple[str, ...] | None:
    """Comparison only: accept bare p wrappers, never discard other HTML/formatting.

    Plain-text blank lines delimit paragraphs. Text within each paragraph stays
    exact (including whitespace, case and punctuation); decode entities once,
    after recognizing markup, so encoded literal tags remain literal text.
    Unsupported or malformed markup falls back to exact equality in same_value.
    """
    value = value.replace("\r\n", "\n")
    if re.search(r"<[!/?A-Za-z]", value):
        paragraph = r"<p>([^<>]*)</p>"
        if not re.fullmatch(rf"\s*{paragraph}(?:\s*{paragraph})*\s*", value):
            return None
        parts = re.findall(paragraph, value)
    else:
        parts = value.split("\n\n")

    def decode(match):
        entity = match[0]
        if not entity.startswith("&#") and entity[1:] not in html5:
            return entity
        if entity.startswith("&#") and len(entity) > 12:
            return entity
        decoded = unescape(entity)
        # HTML's error recovery may discard invalid numeric references. Such
        # lossy conversion is not evidence that the complete text was saved.
        if not decoded or any(ord(char) < 32 or char == "\ufffd" for char in decoded):
            return entity
        return decoded

    return tuple(re.sub(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);", decode, part)
                 for part in parts)


def same_value(field, left, right):
    if field == "description" and isinstance(left, str) and isinstance(right, str) and left != right:
        paragraphs = description_paragraphs(left)
        return paragraphs is not None and paragraphs == description_paragraphs(right)
    if (field == "authors" and isinstance(left, list) and isinstance(right, list)
            and all(isinstance(name, str) for name in left + right)):
        # ABS updates author membership. Keep names and multiplicity intact.
        return sorted(left) == sorted(right)
    return left == right


def target_observed(field, desired, observed):
    return same_value(field, desired, observed) or (field == "subtitle" and desired == "" and observed is None)


def result_matches(expected, observed, changes):
    return expected.keys() == observed.keys() and all(
        target_observed(field, value, observed[field]) if field in changes and changes[field] == value
        else same_value(field, value, observed[field]) for field, value in expected.items())


def review_fields(is_audio: bool) -> tuple[str, ...]:
    """The same editable/confirmable fields in the UI and local decision store."""
    return FIELDS if is_audio else ("title", "subtitle", "authors", "series", "identifiers")


def correction_fields(is_audio: bool) -> tuple[str, ...]:
    return WRITE_FIELDS if is_audio else review_fields(False)


def description_preview(value, limit=200):
    """Presentation only. Full values remain in snapshots, targets and write previews."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "… (truncated; inspect full text)"
    return value


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(compact(value).encode()).hexdigest()


def sequence(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            return None
        return format(number.normalize(), "f")
    except InvalidOperation:
        return None


def canon(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("’", "'")).strip().casefold()


def person_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def validate_target(field: str, value: Any) -> Any:
    if field not in WRITE_FIELDS:
        raise ValueError("This field is read-only.")
    if field in ("title", "subtitle"):
        if not isinstance(value, str) or (field == "title" and not value.strip()):
            raise ValueError("Title must be nonempty text; subtitle must be text.")
    elif field in ("authors", "narrators"):
        if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
            raise ValueError("Contributors must be a JSON list of nonempty names.")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate contributors are not allowed.")
    elif field == "series":
        if not isinstance(value, list):
            raise ValueError("Series must be a JSON list.")
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"name", "sequence"}:
                raise ValueError("Each series needs exactly name and sequence.")
            if not isinstance(entry["name"], str) or not entry["name"].strip():
                raise ValueError("Series name must be nonempty text.")
            number = entry["sequence"]
            if number is not None and (not isinstance(number, str) or sequence(number) is None):
                raise ValueError("Sequence must be a nonnegative decimal string or null.")
    elif field == "description" and (not isinstance(value, str) or not value):
        raise ValueError("Description must be nonempty text. ABS stores empty descriptions as null.")
    elif field == "genres":
        if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
            raise ValueError("Genres must be a JSON list of nonempty names.")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate genres are not allowed.")
    elif field == "publishedYear":
        if type(value) not in (str, int) or not re.fullmatch(r"[0-9]{4}", str(value)):
            raise ValueError("Publication year must contain exactly four digits.")
        return str(value)
    return copy.deepcopy(value)


@dataclass(frozen=True)
class Book:
    raw: dict

    def __post_init__(self):
        raw = self.raw
        if not raw.get("id") or not raw.get("libraryId") or not (raw.get("relPath") or raw.get("path")):
            raise ValueError("Item lacks a current ID, library, or path; refusing to infer identity.")
        if raw.get("mediaType") != "book":
            raise ValueError("Only book items are supported.")
        m = raw.get("media", {}).get("metadata", {})
        if not all(isinstance(m.get(key), list) for key in ("authors", "narrators", "series")):
            raise ValueError("Expanded contributor/series arrays are missing. Upgrade abs-cli; no unsafe fallback.")
        if not all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in m["authors"]):
            raise ValueError("Invalid expanded authors.")
        if not all(isinstance(x, str) for x in m["narrators"]):
            raise ValueError("Invalid expanded narrators.")
        if not all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in m["series"]):
            raise ValueError("Invalid expanded series.")

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def identity(self) -> dict:
        return {key: self.raw.get(key) for key in ("id", "libraryId", "path", "relPath", "ino", "mediaType")}

    @property
    def metadata(self) -> dict:
        return self.raw["media"]["metadata"]

    @property
    def values(self) -> dict:
        m = self.metadata
        return {
            "title": m.get("title"), "subtitle": m.get("subtitle"),
            "authors": [x["name"] for x in m["authors"]],
            "narrators": list(m["narrators"]),
            "series": [{"name": x["name"], "sequence": str(x["sequence"]) if x.get("sequence") is not None
                        else None} for x in m["series"]],
        }

    @property
    def is_audio(self) -> bool:
        media = self.raw["media"]
        return bool(media.get("audioFiles") or media.get("tracks") or media.get("numAudioFiles", 0)
                    or media.get("duration", 0))

    def write_values(self, fields) -> dict:
        """Keep the original snapshot digest and include new fields only when targeted."""
        return {**self.values, **{key: self.metadata.get(key) for key in IMPORTED_FIELDS if key in fields}}

    def protected(self, fields=()) -> dict:
        """Stable unrelated metadata/media. Computed display names may follow an approved edit."""
        media = copy.deepcopy(self.raw["media"])
        metadata = media.pop("metadata")
        for key in (*FIELDS, "titleIgnorePrefix", "authorName", "authorNameLF", "narratorName", "seriesName"):
            metadata.pop(key, None)
        for key in IMPORTED_FIELDS:
            if key in fields:
                metadata.pop(key, None)
        if "description" in fields:
            metadata.pop("descriptionPlain", None)
        media.pop("id", None)
        media.pop("libraryItemId", None)
        return {"metadata": metadata, "media": media}


def payload(changes: dict, restoring=False) -> dict:
    if not changes:
        raise ValueError("Empty update.")
    result = {}
    for field, value in changes.items():
        if restoring and field in ("title", "subtitle", "description", "publishedYear") and value is None:
            result[field] = None
        elif (restoring and field == "publishedYear" and isinstance(value, str)
              and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
            # ABS stores nonempty publishedYear strings verbatim.
            result[field] = value
        else:
            result[field] = validate_target(field, value)
    if "authors" in result:
        result["authors"] = [{"name": name} for name in result["authors"]]
    return {"metadata": result}
