from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from html import unescape
from html.entities import html5
from typing import Any

FIELDS = ("title", "subtitle", "authors", "narrators", "series")
WRITE_FIELDS = FIELDS + ("description", "genres", "tags", "publishedYear", "asin")


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
    if field == "authors" and isinstance(left, list) and isinstance(right, list):
        return sorted(left) == sorted(right)
    if field == "series" and isinstance(left, list) and isinstance(right, list):
        # ABS preserves memberships and positions but need not return the submitted order.
        return sorted(map(compact, left)) == sorted(map(compact, right))
    return left == right


def state_matches(expected, observed, membership_only=False):
    return all(
        expected[field] == observed[field] if field == "authors" and not membership_only
        else same_value(field, expected[field], observed[field])
        for field in WRITE_FIELDS
    )


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(compact(value).encode()).hexdigest()


def clean_text(value):
    return isinstance(value, str) and not any(
        (ord(char) < 32 and char not in "\r\n\t") or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    )


def validate_target(field, value):
    if field not in WRITE_FIELDS:
        raise ValueError("This field is read-only.")
    if field in ("title", "subtitle", "description"):
        if field == "subtitle" and (value is None or value == ""):
            return None
        if not clean_text(value) or not value.strip():
            raise ValueError(f"{field} must contain nonempty text.")
    elif field in ("authors", "narrators", "genres", "tags"):
        if not isinstance(value, list) or not all(clean_text(x) and x.strip() == x and x for x in value):
            raise ValueError(f"{field} must be a JSON list of nonempty names without surrounding whitespace.")
        names = [name.casefold() if field == "authors" else name for name in value]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate names in {field}.")
    elif field == "series":
        if not isinstance(value, list):
            raise ValueError("Series must be a JSON list.")
        names = set()
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"name", "sequence"}:
                raise ValueError("Each series needs exactly name and sequence.")
            name, number = entry["name"], entry["sequence"]
            if not clean_text(name) or not name or name.strip() != name or name.casefold() in names:
                raise ValueError("Series names must be nonempty and unique, without surrounding whitespace.")
            names.add(name.casefold())
            if number is not None and (
                not isinstance(number, str) or len(number) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", number)
            ):
                raise ValueError("Series sequence must be a bounded nonnegative decimal string or null.")
    elif field == "publishedYear":
        if type(value) not in (str, int) or not re.fullmatch(r"[0-9]{4}", str(value)):
            raise ValueError("Publication year must contain exactly four digits.")
        return str(value)
    elif field == "asin" and value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{10}", value)
    ):
        raise ValueError("ASIN must be ten uppercase ASCII letters or digits, or null.")
    return copy.deepcopy(value)


def restorable_year(value):
    return value is None or bool(re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?", value))


@dataclass(frozen=True)
class Book:
    raw: dict

    def __post_init__(self):
        raw = self.raw
        if not raw.get("id") or not raw.get("libraryId") or not (raw.get("relPath") or raw.get("path")):
            raise ValueError("Item lacks a current ID, library, or path; refusing to infer identity.")
        if raw.get("mediaType") != "book":
            raise ValueError("Only book items are supported.")
        media = raw.get("media")
        if not isinstance(media, dict) or not isinstance(media.get("metadata"), dict):
            raise TypeError("Expanded ABS item has no metadata object.")
        m = media["metadata"]
        if not all(isinstance(m.get(key), list) for key in ("authors", "narrators", "series")):
            raise ValueError("Expanded contributor/series arrays are missing. The ABS item response is incompatible.")
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
            "title": m.get("title"), "subtitle": m.get("subtitle") or None,
            "authors": [x["name"] for x in m["authors"]],
            "narrators": list(m["narrators"]),
            "series": [{"name": x["name"], "sequence": str(x["sequence"]) if x.get("sequence") not in ("", None)
                        else None} for x in m["series"]],
            "description": m.get("description"), "genres": m.get("genres") or [],
            "tags": self.raw["media"].get("tags") or [], "asin": m.get("asin"),
            "publishedYear": str(m["publishedYear"]) if m.get("publishedYear") is not None else None,
        }

    @property
    def is_audio(self):
        media = self.raw["media"]
        return any(isinstance(media.get(key), list) and bool(media[key]) for key in ("audioFiles", "tracks"))

    @property
    def is_ebook(self):
        return bool(self.raw["media"].get("ebookFile") or self.raw["media"].get("ebookFormat"))

    @property
    def writable(self):
        return self.is_audio and not self.raw.get("isMissing") and not self.raw.get("isInvalid")

    def protected(self, fields=()) -> dict:
        """Stable unrelated metadata/media. Computed display names may follow an approved edit."""
        media = copy.deepcopy(self.raw["media"])
        metadata = media.pop("metadata")
        generated = ("titleIgnorePrefix", "authorName", "authorNameLF", "narratorName", "seriesName")
        for key in (*WRITE_FIELDS, *generated):
            metadata.pop(key, None)
        if "description" in fields:
            metadata.pop("descriptionPlain", None)
        for key in ("tags", "id", "libraryItemId"):
            media.pop(key, None)
        item = {key: self.raw.get(key) for key in ("libraryFolderId", "mediaId", "isFile", "libraryFiles")}
        return {"metadata": metadata, "media": media, "item": item}


def payload(changes):
    if not changes:
        raise ValueError("Empty update.")
    metadata = {field: validate_target(field, value) for field, value in changes.items() if field != "tags"}
    if "authors" in metadata:
        metadata["authors"] = [{"name": name} for name in metadata["authors"]]
    result = {"metadata": metadata} if metadata else {}
    if "tags" in changes:
        result["tags"] = validate_target("tags", changes["tags"])
    return result
