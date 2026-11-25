"""Bounded JSON and private local artifacts."""

import json
import os
import re
import tempfile
from pathlib import Path

from .models import compact

INPUT_LIMIT = 25_000_000
LOCAL_LIMIT = 250_000_000


def data_directory():
    """Use one directory per OS user, independent of shell location or checkout."""
    return (Path.home() / ".absmm").resolve()


def strict_json(text):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("Nonstandard JSON number.")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
        # Reject nonfinite overflows and unpaired surrogates, including in keys.
        compact(value).encode("utf-8")
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid JSON nesting or Unicode.") from exc
    return value


def read_json(path, limit=INPUT_LIMIT):
    with Path(path).open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("JSON file exceeds the size limit.")
    return strict_json(content.decode("utf-8-sig"))


def contains_token(value, token):
    if not token:
        return False
    pending = [value]
    while pending:
        entry = pending.pop()
        if isinstance(entry, dict):
            pending.extend(entry)
            pending.extend(entry.values())
        elif isinstance(entry, list):
            pending.extend(entry)
        elif isinstance(entry, str) and token in entry:
            return True
    return False


def identifier(value, length):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise ValueError("Invalid local artifact identifier.")
    return value


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"Expected fields {', '.join(required)}.")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8", newline="\n"
    ) as stream:
        temp = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
