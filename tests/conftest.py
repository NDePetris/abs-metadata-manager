import copy
from pathlib import Path

import pytest

from abs_manager.models import Book
from abs_manager.store import Store


def make_book(title="Heaven's River: Bobiverse, Book 4", item_id="current-id", seq="4", series="Bobiverse",
              path=None, authors=None, narrators=None):
    return {"id": item_id, "oldLibraryItemId": "li_old_123", "libraryId": "library-a", "mediaType": "book",
            "path": path or "/books/Dennis E. Taylor/Bobiverse/04 - Heaven's River {Ray Porter}",
            "relPath": path or "Dennis E. Taylor/Bobiverse/04 - Heaven's River {Ray Porter}",
            "ino": "inode-1", "isMissing": False, "isInvalid": False,
            "media": {"id": "media-1", "metadata": {"title": title, "subtitle": None,
                       "authors": [{"id": "author-1", "name": n} for n in (authors or ["Dennis E. Taylor"])],
                       "narrators": narrators if narrators is not None else ["Ray Porter"],
                       "series": [{"id": "series-1", "name": series, "sequence": seq}] if series else [],
                       "description": "Preserve this description", "genres": ["Science Fiction"],
                       "language": "en", "explicit": False},
                      "coverPath": "/metadata/cover.jpg", "tags": ["keep"], "duration": 1000,
                      "audioFiles": [{"ino": "audio-1", "metaTags": {"tagTitle": "Heaven's River"}}],
                      "chapters": [{"title": "Chapter 1", "start": 0, "end": 1000}]}}


class FakeAdapter:
    server = "http://test.invalid"

    def __init__(self, books=None):
        books = books or [make_book()]
        self.items = {b["id"]: copy.deepcopy(b) for b in books}
        self.writes = []
        self.reads = []
        self.fail = None
        self.mutate_after = None
        self.check_journal = None

    def libraries(self):
        return [{"id": "library-a", "name": "Audiobooks", "mediaType": "book"}]

    def page(self, library, page, limit):
        items = list(self.items.values())
        return {"results": copy.deepcopy(items[page * limit:(page + 1) * limit]), "total": len(items), "page": page}

    def item(self, item_id):
        self.reads.append(item_id)
        return copy.deepcopy(self.items[item_id])

    def check_write_capability(self):
        pass

    def update(self, item_id, changes):
        if self.check_journal:
            self.check_journal()
        self.writes.append((item_id, copy.deepcopy(changes)))
        if self.fail == "before":
            raise TimeoutError("Timed out before server accepted")
        metadata = self.items[item_id]["media"]["metadata"]
        applied = copy.deepcopy(changes)
        if "authors" in applied:
            applied["authors"] = [{"name": n, "id": "new-id"} for n in applied["authors"]]
        metadata.update(applied)
        if self.mutate_after:
            self.mutate_after(self.items[item_id])
        if self.fail == "after":
            raise TimeoutError("Timed out after server accepted")
        if self.fail == "interrupt":
            raise KeyboardInterrupt()


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    api = FakeAdapter()
    aid = store.start_audit(api.server, "library-a", "Audiobooks")
    store.add_book(aid, Book(api.item("current-id")))
    store.finish_audit(aid)
    return store, api, aid


PRIVATE = Path(__file__).parent / "fixtures" / "private" / "audit_snapshot.json"
