"""Allowlisted preferences and user-scoped encrypted credentials."""
import json
import os
from pathlib import Path

from .config import server_url
from .credentials import protect
from .models import digest


def location():
    return Path(os.environ.get("ABS_MM_SETTINGS", str(Path(os.environ.get("LOCALAPPDATA", ".")) /
                                                    "ABS Metadata Manager")))


def load():
    path = location() / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(values):
    allowed = {"server", "library", "executable", "calibre_executable", "calibre_library", "source"}
    if set(values) - allowed:
        raise ValueError("Unknown preference.")
    root = location()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / "settings.tmp"
    temporary.write_text(json.dumps(values), encoding="utf-8")
    temporary.replace(root / "settings.json")


def credential(server, value=None, forget=False):
    path = location() / (digest(server_url(server)) + ".dpapi")
    if forget:
        path.unlink(missing_ok=True)
        return ""
    if value is not None:
        encrypted = protect(value.encode("utf-8"))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(path)
        return ""
    return protect(path.read_bytes(), decrypt=True).decode("utf-8") if path.exists() else ""
