from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]


def cli_config() -> dict:
    path = Path.home() / ".abs-cli" / "config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise ValueError("Repair ~/.abs-cli/config.json before connecting.") from exc


def server_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        raise ValueError("Use an http(s) server URL without embedded credentials.")
    if parts.query or parts.fragment:
        raise ValueError("Server URL must not contain query parameters or fragments.")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def detect_cli(configured: str = "") -> str:
    if configured.strip():
        path = Path(configured.strip()).expanduser()
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(configured.strip())
        return found or configured.strip()
    candidates = [shutil.which("abs-cli"), ROOT / ".tools" / "abs-cli.exe",
                  Path(os.getenv("LOCALAPPDATA", "")) / "abs-cli" / "abs-cli.exe"]
    return next((str(p) for p in candidates if p and Path(p).is_file()), "abs-cli")


@dataclass(frozen=True)
class Settings:
    server: str
    token: str = field(repr=False)
    executable: str = "abs-cli"
    interval: float = 1.5
    timeout: float = 90

    def __post_init__(self):
        object.__setattr__(self, "server", server_url(self.server))
        if not self.token.strip():
            raise ValueError("Set ABS_TOKEN, enter an API key, or configure abs-cli first.")
        if self.interval < 0.1 or self.timeout < 1:
            raise ValueError("Invalid request pacing or timeout.")


def defaults() -> dict:
    saved = cli_config()
    return {"server": os.getenv("ABS_SERVER") or os.getenv("ABS_URL") or saved.get("server") or "",
            "token": os.getenv("ABS_TOKEN") or saved.get("accessToken") or "",
            "executable": detect_cli(os.getenv("ABS_CLI_PATH", "")),
            "library": os.getenv("ABS_LIBRARY") or saved.get("defaultLibrary") or "",
            "interval": float(os.getenv("ABS_MM_INTERVAL", "1.5")),
            "timeout": float(os.getenv("ABS_MM_TIMEOUT", "90"))}
