import copy
import ipaddress
import os
import socket
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

    def update(self, item_id, changes, *, restoring=False):
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


@pytest.fixture(autouse=True)
def isolated_user_settings(tmp_path, monkeypatch, cli_profile_path):
    # Never inherit a developer's connection, database, provider keys or proxies.
    for key in tuple(os.environ):
        if key.startswith(("ABS_", "GOOGLE_BOOKS_", "CALIBRE_")) or key.upper() in {
            "METADATA_CONTACT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"
        }:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("ABS_MM_SETTINGS", str(tmp_path / "preferences"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("NETRC", str(tmp_path / "absent-netrc"))
    test_home = tmp_path / "home"
    test_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: test_home))
    monkeypatch.setattr("abs_manager.config.cli_config", dict)
    monkeypatch.setattr("abs_manager.adapter.cli_config", dict)
    monkeypatch.setattr("abs_manager.preferences.credential", lambda *args, **kwargs: "")


@pytest.fixture(scope="session")
def cli_profile_path():
    # Capture the real executable's profile before Python's home is isolated.
    from scripts.diagnose_metadata_search import cli_profile
    return cli_profile()


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """Fail before DNS or connection if a Python test attempts external traffic.

    Official CLI subprocesses have separate clean-profile checks and receive
    only the local simulator URL; Python socket patches cannot constrain them.
    """
    resolve = socket.getaddrinfo
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(host):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host == "localhost":
            return
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError("Tests permit only literal loopback addresses or localhost")

    def getaddrinfo(host, *args, **kwargs):
        check(host)
        return resolve(host, *args, **kwargs)

    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check(address[0])
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check(address[0])
        return connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
