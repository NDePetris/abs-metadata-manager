import copy
import ipaddress
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from absmm.files import strict_json
from absmm.models import compact


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
        if "tags" in applied:
            self.items[item_id]["media"]["tags"] = applied.pop("tags")
        metadata.update(applied)
        if self.mutate_after:
            self.mutate_after(self.items[item_id])
        if self.fail == "after":
            raise TimeoutError("Timed out after server accepted")
        if self.fail == "interrupt":
            raise KeyboardInterrupt()


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """Fail before DNS or connection if a test attempts external traffic."""
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


TEST_TOKEN = "synthetic-abs-credential"


@contextmanager
def simulator(api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data=None, **headers):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if data is not None:
                self.wfile.write(compact(data).encode("utf-8"))

        def authorized(self):
            if self.headers.get("Authorization") == f"Bearer {TEST_TOKEN}":
                return True
            self.respond(401, {"message": TEST_TOKEN})
            return False

        def do_GET(self):
            if not self.authorized():
                return
            uri = urlsplit(self.path)
            query = parse_qs(uri.query)
            api.http_calls.append(("GET", uri.path, query))
            if uri.path == "/api/libraries":
                self.respond(200, {"libraries": api.libraries()})
            elif uri.path == "/api/libraries/library-a/items":
                self.respond(200, api.page("library-a", int(query["page"][0]), int(query["limit"][0])))
            elif uri.path.startswith("/api/items/") and query == {"expanded": ["1"]}:
                item_id = uri.path.rsplit("/", 1)[1]
                if item_id not in api.items:
                    self.respond(404)
                else:
                    self.respond(200, api.item(item_id))
            else:
                self.respond(404)

        def do_PATCH(self):
            if not self.authorized():
                return
            body = strict_json(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            api.http_calls.append(("PATCH", self.path, body))
            if api.fail == "redirect":
                self.respond(307, Location=self.path)
                return
            if not self.path.startswith("/api/items/") or not self.path.endswith("/media"):
                self.respond(404)
                return
            changes = body.get("metadata", {}).copy()
            if "authors" in changes:
                changes["authors"] = [person["name"] for person in changes["authors"]]
            if "tags" in body:
                changes["tags"] = body["tags"]
            try:
                api.update(self.path.split("/")[-2], changes)
            except TimeoutError:
                self.close_connection = True
            else:
                self.respond(200, {"updated": True})

    api.http_calls = []
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()
