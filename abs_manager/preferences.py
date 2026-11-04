"""Allowlisted preferences and user-scoped encrypted credentials."""
import json
import os
from pathlib import Path

from .config import server_url
from .credentials import protect
from .models import digest

CREDENTIAL_RECOVERY = ("The saved API key is unavailable. Open Settings to reconnect with a new key "
                       "or forget the saved credential. No key contents were displayed.")


class CredentialError(ValueError):
    pass


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
    try:
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
        try:
            encrypted = path.read_bytes()
        except FileNotFoundError:
            return ""
        result = protect(encrypted, decrypt=True).decode("utf-8")
        if not result.strip():
            raise ValueError("Empty credential")
        return result
    except (OSError, ValueError, UnicodeError):
        raise CredentialError(CREDENTIAL_RECOVERY) from None
