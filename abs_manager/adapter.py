from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import requests

from .config import ROOT, Settings, cli_config
from .diagnostics import event, identifier, local_log, stderr_summary
from .models import Book, payload


class AdapterError(RuntimeError):
    pass


class InventoryChanged(AdapterError):
    """A verified identity/membership mismatch requires a new snapshot."""


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
        self.diagnostic = lambda record: None
        self.log = local_log(ROOT / "data" / "audit.log")

    def _pace(self):
        time.sleep(max(0, self.settings.interval - (time.monotonic() - self._last)))
        self._last = time.monotonic()

    def _run(self, args: list[str], body: str | None = None, parse: bool = True, validate=None):
        # Positive allowlist: mutations (including rollback's update) always get ONE attempt.
        readonly = body is None and tuple(args[:2]) in {
            ("libraries", "list"), ("items", "list"), ("items", "get"),
            ("metadata", "providers"), ("metadata", "search")}
        mutation = args[:2] == ["items", "update"] and "--stdin" in args
        operation = " ".join(args[:2]) if args[0] != "--version" else "version"
        target = {}
        for flag, key in (("--id", "item_id"), ("--page", "page")):
            if flag in args:
                target[key] = identifier(args[args.index(flag) + 1], self.settings.token)
        env = dict(os.environ, ABS_SERVER=self.server, ABS_TOKEN=self.settings.token, ABS_DEBUG="0")
        for attempt in range(1, (3 if readonly else 1) + 1):
            self._pace()
            record = {"operation": operation, **target, "attempt": attempt, "retry_count": attempt - 1,
                      "duration": 0.0, "exit_code": None, "category": "pending", "state": "requesting"}
            self.diagnostic(record.copy())
            started = time.monotonic()
            category, detail, code = "ok", "", None
            try:
                result = subprocess.run(
                    [self.settings.executable, *args], input=body, capture_output=True, check=False,
                    text=True, encoding="utf-8", errors="replace", env=env,
                    timeout=self.settings.timeout, shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                code = result.returncode
                if code:
                    detail = stderr_summary(result.stderr, self.settings.token)
                    statuses = re.findall(r"\b[1-5]\d\d\b", detail)
                    if any(s in statuses for s in ("401", "403")) or re.search(
                            r"unauthorized|forbidden", detail, re.IGNORECASE):
                        category = "authorization"
                    elif any(s in statuses for s in ("429", "502", "503", "504")):
                        category = "transient_http"
                    elif statuses:
                        category = "permanent_http"
                    elif re.search(r"ECONNRESET|connection reset", detail, re.IGNORECASE):
                        category = "connection_reset"
                    elif re.search(r"ETIMEDOUT|timed out|timeout", detail, re.IGNORECASE):
                        category = "timeout"
                    else:
                        category = "cli_error"
                else:
                    try:
                        value = json.loads(result.stdout) if parse else result.stdout.strip()
                    except ValueError:
                        category = "malformed_response"
                    if category == "ok" and validate:
                        try:
                            validate(value)
                        except (ValueError, TypeError, KeyError, AttributeError):
                            category = "invalid_schema"
            except subprocess.TimeoutExpired as exc:
                category, detail = "timeout", stderr_summary(exc.stderr, self.settings.token)
            except ConnectionResetError:
                category = "connection_reset"
            except OSError:
                category = "executable_error"
            retry = readonly and attempt < 3 and category in {"timeout", "connection_reset", "transient_http"}
            record.update(duration=round(time.monotonic() - started, 3), exit_code=code, category=category,
                          stderr=detail, state="retrying" if retry else "ok" if category == "ok" else "failed")
            event(self.log, **record)
            self.diagnostic(record.copy())
            if category == "ok":
                if "WARN" in result.stderr and "abs-cli reported a compatibility warning; see Settings." not in self.warnings:
                    self.warnings.append("abs-cli reported a compatibility warning; see Settings.")
                return value
            if retry:
                time.sleep(2 ** (attempt - 1))
                continue
            context = " ".join(f"{key}={val}" for key, val in target.items())
            message = (f"{operation} {context} failed: {category}; duration={record['duration']:.3f}s; "
                       f"retries={attempt - 1}; exit_code={code}. {detail}")
            if mutation:
                message += " A submitted update may have reached the server; reconcile History."
            elif readonly:
                message += " Read-only request; saved audit progress is retained."
            raise AdapterError(message) from None

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
        response = self._run(["libraries", "list"], validate=lambda r: self._schema(r, "libraries"))
        if not isinstance(response, dict) or not isinstance(response.get("libraries"), list):
            raise AdapterError("Unexpected library response schema.")
        return response["libraries"]

    def page(self, library: str, page: int, limit: int) -> dict:
        response = self._run(["items", "list", "--library", library, "--limit", str(limit), "--page", str(page),
                              "--sort", "addedAt"], validate=lambda r: self._schema(r, "results"))
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise AdapterError("Unexpected inventory response schema.")
        return response

    def item(self, item_id: str) -> dict:
        response = self._run(["items", "get", "--id", item_id, "--expanded"], validate=Book)
        Book(response)  # Reject minified schemas before auditing or updating.
        return response

    def metadata_providers(self) -> list[dict]:
        response = self._run(["metadata", "providers"])
        try:
            providers = response["providers"]["books"]
            if not isinstance(providers, list) or not all(
                    isinstance(p, dict) and isinstance(p.get("value"), str) and p["value"] for p in providers):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AdapterError("Unexpected metadata provider schema.") from None
        return sorted(providers, key=lambda p: (not any(
            x in (p["value"] + p.get("text", "")).lower() for x in ("audible", "audnexus")), p["value"]))

    def metadata_search(self, provider: str, query: dict) -> list[dict]:
        if not provider or not query.get("title"):
            raise ValueError("Select a discovered provider and a nonempty title/ASIN.")
        args = ["metadata", "search", "--provider", provider, "--title", query["title"]]
        if query.get("author"):
            args += ["--author", query["author"]]
        response = self._run(args)
        if not isinstance(response, list):
            raise AdapterError("Unexpected metadata search schema (expected candidate array).")
        if not all(isinstance(row, dict) for row in response):
            raise AdapterError("Invalid metadata search candidate.")
        return response

    @staticmethod
    def _schema(response, key):
        if not isinstance(response, dict) or not isinstance(response.get(key), list):
            raise TypeError("Unexpected response schema.")

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


def item_identity(row: dict) -> dict:
    if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
            or not row.get("libraryId") or not (row.get("path") or row.get("relPath"))
            or row.get("mediaType") != "book"):
        raise AdapterError("Inventory lacks a verifiable book identity/path; run a fresh audit.")
    return {key: row.get(key) for key in ("id", "libraryId", "path", "relPath", "ino", "mediaType")}


class PublicMetadataTransport:
    """GET-only documented bibliographic APIs. No ABS credentials enter this transport."""

    def __init__(self, google_key="", contact="", timeout=20):
        self.google_key, self.timeout = google_key, timeout
        self.agent = "abs-metadata-manager/0.2 (personal metadata review" + ("; " + contact if contact else "") + ")"
        self._last = 0.0

    def public_search(self, source, query):
        if source not in ("openlibrary", "googlebooks"):
            raise ValueError("Unsupported source.")
        if source == "googlebooks" and not self.google_key:
            raise AdapterError("Google Books requires a configured API key; ABS and Open Library need none.")
        url = {"openlibrary": "https://openlibrary.org/search.json",
               "googlebooks": "https://www.googleapis.com/books/v1/volumes"}[source]
        params = dict(query)
        if source == "openlibrary":
            params.update(limit=10, fields="key,title,author_name")
        else:
            params.update(maxResults=10, key=self.google_key)
        for attempt in range(3):
            time.sleep(max(0, 1.5 - (time.monotonic() - self._last)))
            self._last = time.monotonic()
            try:
                response = requests.get(url, params=params, headers={"User-Agent": self.agent},
                                        timeout=self.timeout, allow_redirects=False)
                if response.status_code in (429, 502, 503, 504):
                    # Long Retry-After values stop this action instead of ignoring the server's request.
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after and (not retry_after.isdigit() or int(retry_after) > 30):
                        raise AdapterError("Provider rate limited/unavailable; retry later explicitly.")
                    if attempt < 2:
                        time.sleep(max(2 ** attempt, int(retry_after or 0)))
                        continue
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise TypeError
                return result
            except (requests.Timeout, requests.ConnectionError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise AdapterError("Public provider unavailable: timeout/connection failure after three read attempts.") from None
            except (requests.RequestException, ValueError, TypeError):
                # requests exceptions can include the URL/API key. Never propagate them.
                raise AdapterError("Public provider unavailable: HTTP failure, rate limit, or invalid response.") from None


def membership(adapter: Adapter, library: str, progress: Callable = lambda *args: None, limit=50) -> dict:
    """Enumerate the entire membership before any expanded requests or reuse."""
    seen = {}
    expected = None
    for page_number in range(10000):
        page = adapter.page(library, page_number, limit)
        total = page.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise AdapterError("Inventory must include a valid total.")
        if expected is None:
            expected = total
        if total != expected or page.get("page", page_number) != page_number:
            raise InventoryChanged(f"items list page={page_number}: library changed during pagination; run a fresh audit.")
        rows = page["results"]
        if not rows and len(seen) != total:
            raise AdapterError("Inventory stopped before the reported total.")
        for row in rows:
            identity = item_identity(row)
            item_id = identity["id"]
            if not item_id or item_id in seen or row.get("libraryId") != library:
                raise AdapterError("Duplicate, missing, or wrong-library inventory identity.")
            seen[item_id] = identity
        progress(len(seen), total, f"Checking membership · page {page_number}")
        if len(seen) > total:
            raise AdapterError("Inventory exceeds reported total.")
        if len(seen) == total:
            return seen
    raise AdapterError("Pagination safety limit reached.")


def inventory(adapter: Adapter, library: str, progress: Callable[[int, int, str], None], limit=50,
              *, members=None, saved=None):
    members = membership(adapter, library, limit=limit) if members is None else members
    saved = saved or {}
    for item_id, book in saved.items():
        if item_id not in members or book.identity != members[item_id]:
            raise InventoryChanged("Saved item identity/path differs from current membership; start a fresh audit.")
    for index, (item_id, identity) in enumerate(members.items(), 1):
        book = saved.get(item_id)
        if book is None:
            book = Book(adapter.item(item_id))
            if book.identity != identity:
                raise InventoryChanged("items get: expanded item identity/path differs from inventory; start a fresh audit.")
            yield book
        progress(index, len(members), f"Item {item_id} · saved")
