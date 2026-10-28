"""Source schemas and query plans. All actual HTTP/CLI transport lives in adapter.py."""
from __future__ import annotations

from typing import Protocol
from urllib.parse import urlencode

from .models import Book, digest
from .verification import candidate


class Source(Protocol):
    id: str
    scope: str
    targeted_only: bool

    def query(self, book: Book) -> dict: ...
    def search(self, query: dict) -> dict: ...


class AbsSource:
    targeted_only = False

    def __init__(self, adapter, provider):
        self.adapter, self.provider = adapter, provider
        self.id = "abs:" + provider
        self.scope = adapter.server

    def query(self, book):
        recording = any(p in self.provider.lower() for p in ("audible", "audnexus"))
        return {"title": book.metadata.get("asin") if recording and book.metadata.get("asin") else
                book.values["title"], "author": ", ".join(book.values["authors"])}

    def search(self, query):
        response = self.adapter.metadata_search(self.provider, query)
        candidates = []
        for raw in response:
            # ABS BookFinder normalizes provider duration to minutes, saved media is seconds.
            normalized = dict(raw)
            if isinstance(raw.get("duration"), (int, float)):
                normalized["duration"] = raw["duration"] * 60
            url = raw.get("url") or raw.get("link") or self.scope + "/api/search/books?" + urlencode(
                {"provider": self.provider, **query})
            item = candidate(normalized, self.id, url,
                             work_only=not any(p in self.provider.lower() for p in ("audible", "audnexus")))
            item["raw"] = raw
            item["id"] = digest({k: v for k, v in item.items() if k != "id"})
            candidates.append(item)
        return {"raw": response, "candidates": candidates}


class PublicSource:
    scope = "public"

    def __init__(self, name, transport):
        if name not in ("openlibrary", "googlebooks"):
            raise ValueError("Unsupported public source.")
        self.id, self.transport = name, transport
        self.targeted_only = name == "openlibrary"

    def query(self, book):
        isbn = book.metadata.get("isbn")
        if self.id == "openlibrary":
            return {"q": "isbn:" + isbn} if isbn else {
                "title": book.values["title"], "author": " ".join(book.values["authors"])}
        return {"q": "isbn:" + isbn if isbn else
                "intitle:" + (book.values["title"] or "") + " inauthor:" + " ".join(book.values["authors"])}

    def search(self, query):
        response = self.transport.public_search(self.id, query)
        result = []
        if self.id == "openlibrary":
            if not isinstance(response.get("docs"), list):
                raise ValueError("Unexpected Open Library schema.")
            for row in response["docs"]:
                # Search returns works. Never borrow one ISBN/narrator from an aggregate of editions.
                raw = {"title": row.get("title"), "author": row.get("author_name", [])}
                item = candidate(raw, self.id, "https://openlibrary.org" + row["key"], work_only=True)
                item["raw"] = row
                result.append(item)
        else:
            if not isinstance(response.get("items", []), list) or "error" in response:
                raise ValueError("Unexpected Google Books schema.")
            for row in response.get("items", []):
                info = row["volumeInfo"]
                raw = {k: info[k] for k in ("title", "subtitle", "authors", "language", "publisher", "publishedDate")
                       if k in info}
                identifiers = info.get("industryIdentifiers", [])
                isbn = next((i["identifier"] for i in identifiers if i.get("type") == "ISBN_13"), None)
                if isbn:
                    raw["isbn"] = isbn
                item = candidate(raw, self.id, "https://books.google.com/books?id=" + row["id"], work_only=True)
                item["raw"] = row
                result.append(item)
        return {"raw": response, "candidates": result}
