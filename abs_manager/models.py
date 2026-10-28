from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

FIELDS = ("title", "subtitle", "authors", "narrators", "series")


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
    if field not in FIELDS:
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

    def protected(self) -> dict:
        """Stable unrelated metadata/media. Computed display names may follow an approved edit."""
        media = copy.deepcopy(self.raw["media"])
        metadata = media.pop("metadata")
        for key in (*FIELDS, "titleIgnorePrefix", "authorName", "authorNameLF", "narratorName", "seriesName"):
            metadata.pop(key, None)
        media.pop("id", None)
        media.pop("libraryItemId", None)
        return {"metadata": metadata, "media": media}


def payload(changes: dict, restoring=False) -> dict:
    if not changes:
        raise ValueError("Empty update.")
    result = {field: (None if restoring and field in ("title", "subtitle") and value is None
                      else validate_target(field, value)) for field, value in changes.items()}
    if "authors" in result:
        result["authors"] = [{"name": name} for name in result["authors"]]
    return {"metadata": result}
