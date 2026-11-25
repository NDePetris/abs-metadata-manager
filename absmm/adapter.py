"""The ABS HTTP boundary and stable inventory enumeration."""

import re
from urllib.parse import urlsplit, urlunsplit

import requests

from .files import contains_token
from .models import Book, payload


class AdapterError(RuntimeError):
    pass


def server_url(value):
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in ("http", "https") or not parts.hostname
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment or "\\" in parts.netloc
    ):
        raise ValueError("Set ABS_URL to an HTTP(S) server URL without credentials, query, or fragment.")
    port = parts.port
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    standard_port = 443 if parts.scheme == "https" else 80
    authority = host if port in (None, standard_port) else f"{host}:{port}"
    return urlunsplit((parts.scheme, authority, parts.path.rstrip("/"), "", ""))


def route_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid ABS identifier.")
    return value


class HttpAdapter:
    def __init__(self, server, token):
        self.server = server_url(server)
        self.token = token.strip()
        if not self.token or not self.token.isascii() or any(c.isspace() for c in self.token):
            raise ValueError("Set ABS_TOKEN to an Audiobookshelf API key.")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self.session.headers.update({"Authorization": f"Bearer {self.token}", "Accept": "application/json"})

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(
                method, self.server + path, timeout=(5, 30), allow_redirects=False, **kwargs
            )
        except requests.RequestException as exc:
            raise AdapterError(f"ABS {method} failed ({type(exc).__name__}).") from None
        if not 200 <= response.status_code < 300:
            # Upstream can echo request details. Status is enough to distinguish auth,
            # missing items, rate limits, and redirects without exposing response data.
            raise AdapterError(f"ABS {method} returned HTTP {response.status_code}.")
        if method == "PATCH":
            return
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            raise AdapterError("ABS GET returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise AdapterError("ABS GET returned an unexpected response shape.")
        if self.token in response.text or contains_token(data, self.token):
            raise AdapterError("ABS GET echoed credentials. Response discarded.")
        return data

    def libraries(self):
        libraries = self._request("GET", "/api/libraries").get("libraries")
        if not isinstance(libraries, list) or not all(
            isinstance(row, dict) and isinstance(row.get("id"), str)
            and isinstance(row.get("name"), str) for row in libraries
        ):
            raise AdapterError("ABS returned an invalid library list.")
        return libraries

    def page(self, library, page, limit):
        return self._request(
            "GET", f"/api/libraries/{route_id(library)}/items",
            params={"page": page, "limit": limit, "sort": "addedAt"},
        )

    def item(self, item_id):
        return self._request("GET", f"/api/items/{route_id(item_id)}", params={"expanded": 1})

    def update(self, item_id, changes):
        self._request("PATCH", f"/api/items/{route_id(item_id)}/media", json=payload(changes))


def item_identity(row):
    if (
        not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
        or not row.get("libraryId") or not (row.get("path") or row.get("relPath"))
        or row.get("mediaType") != "book"
    ):
        raise AdapterError("ABS item lacks a verifiable book identity and path.")
    return {key: row.get(key) for key in ("id", "libraryId", "path", "relPath", "ino", "mediaType")}


def membership(adapter, library, limit=50):
    """Enumerate the entire library while checking pagination and membership."""
    seen = {}
    expected = None
    for page_number in range(10000):
        page = adapter.page(library, page_number, limit)
        total, rows = page.get("total"), page.get("results")
        if type(total) is not int or total < 0 or not isinstance(rows, list):
            raise AdapterError("Inventory requires a valid total and results array.")
        if expected is None:
            expected = total
        if total != expected or page.get("page", page_number) != page_number:
            raise AdapterError("Library changed during pagination. Export again.")
        if not rows and len(seen) != total:
            raise AdapterError("Inventory stopped before the reported total.")
        for row in rows:
            identity = item_identity(row)
            item_id = identity["id"]
            if item_id in seen or identity["libraryId"] != library:
                raise AdapterError("Duplicate or wrong-library inventory identity.")
            seen[item_id] = identity
        if len(seen) > total:
            raise AdapterError("Inventory exceeds the reported total.")
        if len(seen) == total:
            return seen
    raise AdapterError("Pagination safety limit reached.")


def inventory(adapter, members):
    for item_id, identity in members.items():
        raw = adapter.item(item_id)
        if item_identity(raw) != identity:
            raise AdapterError("Expanded item identity differs from inventory. Export again.")
        yield Book(raw)
