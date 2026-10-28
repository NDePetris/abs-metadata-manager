from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import requests

from .config import Settings, cli_config
from .models import Book, payload


class AdapterError(RuntimeError):
    pass


class Adapter(Protocol):
    server: str

    def libraries(self) -> list[dict]: ...
    def page(self, library: str, page: int, limit: int) -> dict: ...
    def item(self, item_id: str) -> dict: ...
    def update(self, item_id: str, changes: dict) -> None: ...
    def check_write_capability(self) -> None: ...


class CliAdapter:
    """Small CLI-only metadata transport. Credentials go through the child environment."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.server = settings.server
        self.warnings: list[str] = []
        self.version = ""
        self._last = 0.0

    def _pace(self):
        time.sleep(max(0, self.settings.interval - (time.monotonic() - self._last)))
        self._last = time.monotonic()

    def _run(self, args: list[str], body: str | None = None, parse: bool = True):
        self._pace()
        env = dict(os.environ, ABS_SERVER=self.server, ABS_TOKEN=self.settings.token, ABS_DEBUG="0")
        try:
            result = subprocess.run(
                [self.settings.executable, *args], input=body, capture_output=True, check=False,
                text=True, encoding="utf-8", errors="replace", env=env,
                timeout=self.settings.timeout, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError("CLI timed out. A submitted update may have reached the server; reconcile History.") from exc
        except OSError as exc:
            raise AdapterError("Cannot start abs-cli. Check the executable path in Settings.") from exc
        # Avoid exposing untrusted server response bodies or tokens in UI/errors.
        if result.returncode:
            raise AdapterError(f"abs-cli exited with code {result.returncode}. Check connectivity, API key and permissions.")
        if "WARN" in result.stderr and "abs-cli reported a compatibility warning; see Settings." not in self.warnings:
            self.warnings.append("abs-cli reported a compatibility warning; see Settings.")
        if not parse:
            return result.stdout.strip()
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise AdapterError("abs-cli returned invalid JSON. Update compatibility must be checked.") from exc

    def probe(self) -> dict:
        self.version = self._run(["--version"], parse=False)
        for command, required in [(["items", "get"], "--expanded"),
                                  (["items", "update"], "--stdin"),
                                  (["items", "list"], "--page")]:
            if required not in self._run([*command, "--help-full"], parse=False):
                raise AdapterError("This abs-cli lacks required commands; install version 1.2.0.")
        libraries = self.libraries()
        # abs-cli has no status command. This diagnostic GET never reads or writes metadata.
        server_version = "unavailable"
        self._pace()
        try:
            response = requests.get(self.server + "/status", timeout=self.settings.timeout, allow_redirects=False)
            response.raise_for_status()
            server_version = response.json().get("serverVersion", "unavailable")
        except (requests.RequestException, ValueError):
            self.warnings.append("Server version unavailable; library connection succeeded.")
        if server_version not in ("2.37.0", "2.37.1"):
            self.warnings.append("abs-cli 1.2.0 declares ABS 2.37.0–2.37.1 tested; validate your server with one book.")
        if self.version != "1.2.0":
            self.warnings.append("Only abs-cli 1.2.0 is validated by this app. Reads allowed; writes disabled.")
        try:
            self.check_write_capability()
            writable = True
        except AdapterError as exc:
            self.warnings.append(str(exc))
            writable = False
        return {"cli_version": self.version, "server_version": server_version,
                "libraries": libraries, "warnings": list(dict.fromkeys(self.warnings)), "writable": writable}

    def check_write_capability(self):
        if not self.version:
            self.version = self._run(["--version"], parse=False)
        if self.version != "1.2.0":
            raise AdapterError("Writes require the validated abs-cli 1.2.0 executable.")
        # 1.2.0 retries a 401 PATCH without its body when a refresh token exists.
        # No refresh token means the CLI exits before retrying. Fail closed.
        try:
            has_refresh = bool(cli_config().get("refreshToken"))
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        if has_refresh:
            raise AdapterError("Writes disabled: ~/.abs-cli/config.json contains a refresh token. "
                               "Use an API key and remove the refreshToken property after backing up that config; "
                               "abs-cli 1.2.0 can retry PATCH after refreshing. Read-only auditing is available.")

    def libraries(self) -> list[dict]:
        response = self._run(["libraries", "list"])
        if not isinstance(response, dict) or not isinstance(response.get("libraries"), list):
            raise AdapterError("Unexpected library response schema.")
        return response["libraries"]

    def page(self, library: str, page: int, limit: int) -> dict:
        response = self._run(["items", "list", "--library", library, "--limit", str(limit), "--page", str(page),
                              "--sort", "addedAt"])
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise AdapterError("Unexpected inventory response schema.")
        return response

    def item(self, item_id: str) -> dict:
        response = self._run(["items", "get", "--id", item_id, "--expanded"])
        Book(response)  # Reject minified schemas before auditing or updating.
        return response

    def update(self, item_id: str, changes: dict) -> None:
        self.check_write_capability()
        self._run(["items", "update", "--id", item_id, "--stdin"],
                  json.dumps(payload(changes, restoring=True), ensure_ascii=False))

    def cover(self, item_id: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="abs-cover-") as temp:
            path = Path(temp) / "cover.jpg"
            self._run(["items", "cover", "get", "--id", item_id, "--output", str(path)], parse=False)
            if path.stat().st_size > 10_000_000:
                raise AdapterError("Cover exceeds the 10 MB preview limit.")
            return path.read_bytes()


def inventory(adapter: Adapter, library: str, progress: Callable[[int, int, str], None], limit=50):
    """Never declare an incomplete or shifting pagination run complete."""
    seen: set[str] = set()
    expected = None
    for page_number in range(10000):
        page = adapter.page(library, page_number, limit)
        total = page.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise AdapterError("Inventory must include a valid total.")
        if expected is None:
            expected = total
        if total != expected or page.get("page", page_number) != page_number:
            raise AdapterError("Library changed during pagination; run a fresh audit.")
        rows = page["results"]
        if not rows and len(seen) != total:
            raise AdapterError("Inventory stopped before the reported total.")
        for row in rows:
            item_id = row.get("id")
            if not item_id or item_id in seen or row.get("libraryId") != library:
                raise AdapterError("Duplicate, missing, or wrong-library inventory identity.")
            seen.add(item_id)
            raw = adapter.item(item_id)
            book = Book(raw)
            if book.id != item_id or book.identity["libraryId"] != library:
                raise AdapterError("Expanded item identity differs from inventory.")
            yield book
            progress(len(seen), total, book.values["title"] or item_id)
        if len(seen) > total:
            raise AdapterError("Inventory exceeds reported total.")
        if len(seen) == total:
            return
    raise AdapterError("Pagination safety limit reached.")
