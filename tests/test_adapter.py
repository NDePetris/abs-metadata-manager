import copy
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeAdapter, make_book

from abs_manager.adapter import AdapterError, CliAdapter, inventory
from abs_manager.config import Settings, cli_config, detect_cli, server_url
from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.workflow import Workflow, confirmation, preview, run_audit


@pytest.mark.parametrize("count", [0, 1, 50, 51, 110])
def test_all_pages(count):
    api = FakeAdapter([make_book(item_id=str(i)) for i in range(count)] if count else [make_book()])
    if count == 0:
        api.items = {}
    updates = []
    books = list(inventory(api, "library-a", lambda *args: updates.append(args)))
    assert len(books) == count
    assert len(updates) == count
    assert not api.writes


@pytest.mark.parametrize("mode", ["duplicates", "short", "changing_total", "wrong_library"])
def test_bad_pagination_is_never_complete(mode):
    api = FakeAdapter()
    api.items.update({str(n): make_book(item_id=str(n)) for n in range(5)})
    calls = []

    def page(library, n, limit):
        calls.append(n)
        if mode == "duplicates":
            rows, total = [make_book()], 3
        elif mode == "short":
            rows, total = ([make_book()] if n == 0 else []), 3
        elif mode == "changing_total":
            rows, total = [make_book(item_id=str(n))], 3 if n == 0 else 4
        else:
            rows, total = [{**make_book(), "libraryId": "elsewhere"}], 1
        return {"results": rows, "total": total, "page": n}
    api.page = page
    with pytest.raises(AdapterError):
        list(inventory(api, "library-a", lambda *args: None))


def test_partial_audit_blocks_approvals_and_expires_prior(setup):
    store, api, aid = setup
    store.review([next(f["id"] for f in store.findings(aid) if f["actionable"])], "approved")
    api.page = lambda *args: {"results": [], "total": 110}
    with pytest.raises(AdapterError):
        run_audit(store, api, api.libraries()[0])
    assert store.latest(api.server, "library-a")["status"] == "interrupted"
    assert not any(f["status"] == "approved" for f in store.findings(aid))


def test_cli_subprocess_argv_environment_stdin_and_redaction(monkeypatch):
    adapter = CliAdapter(Settings("http://localhost:13378", "very-secret", "test-cli", 0.1, 3))
    adapter.version = "1.2.0"
    monkeypatch.setattr("abs_manager.adapter.cli_config", dict)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout='{"updated":true}', stderr="", returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    adapter.update("uuid-current", {"authors": ["Author One", "Author Two"], "title": "Title’s punctuation"})
    args, kw = calls[0]
    assert args == ["test-cli", "items", "update", "--id", "uuid-current", "--stdin"]
    assert "very-secret" not in str(args)
    assert kw["env"]["ABS_TOKEN"] == "very-secret"
    assert kw["shell"] is False
    assert set(json.loads(kw["input"])["metadata"]) == {"authors", "title"}
    assert json.loads(kw["input"])["metadata"]["authors"] == [{"name": "Author One"}, {"name": "Author Two"}]
    assert "very-secret" not in repr(adapter.settings)


def test_cli_timeout_never_retried(monkeypatch):
    adapter = CliAdapter(Settings("http://localhost:13378", "secret", "test-cli", 0.1, 3))
    adapter.version = "1.2.0"
    monkeypatch.setattr("abs_manager.adapter.cli_config", dict)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired("test-cli", 3)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AdapterError, match="reconcile"):
        adapter.update("a", {"title": "new"})
    assert len(calls) == 1


def test_refresh_token_blocks_upstream_write_retry(monkeypatch):
    adapter = CliAdapter(Settings("http://localhost:13378", "secret", "test-cli"))
    adapter.version = "1.2.0"
    monkeypatch.setattr("abs_manager.adapter.cli_config", lambda: {"refreshToken": "stored-refresh-secret"})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("No CLI call allowed"))
    with pytest.raises(AdapterError, match="refresh token"):
        adapter.update("a", {"title": "new"})


def test_minified_metadata_refused():
    raw = make_book()
    del raw["media"]["metadata"]["authors"]
    with pytest.raises(ValueError, match="Expanded"):
        Book(raw)


@pytest.mark.parametrize("url", ["https://user:secret@host", "file:///books", "https://host?token=secret"])
def test_server_url_rejects_embedded_credentials(url):
    with pytest.raises(ValueError):
        server_url(url)


class LocalABS(BaseHTTPRequestHandler):
    """Local protocol simulator only; it never contacts an Audiobookshelf server."""

    def log_message(self, *args):
        pass

    def reply(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        if self.path == "/status":
            self.reply({"serverVersion": "2.37.1", "isInit": True})
        elif self.path == "/api/libraries":
            self.reply({"libraries": [{"id": "library-a", "name": "Audiobooks", "mediaType": "book"}]})
        elif self.path.startswith("/api/libraries/library-a/items"):
            self.reply({"results": [self.server.book], "total": 1, "limit": 50, "page": 0})
        elif self.path == "/api/items/current-id?expanded=1":
            self.reply(self.server.book)
        else:
            self.reply({"error": "Unexpected test endpoint"}, 404)

    def do_PATCH(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.requests.append(("PATCH", self.path, body))
        if self.path != "/api/items/current-id/media":
            self.reply({}, 404)
            return
        self.server.book["media"]["metadata"].update(copy.deepcopy(body["metadata"]))
        self.reply({"updated": True, "libraryItem": self.server.book})


def test_official_cli_metadata_search_get_only(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from abs_manager.sources import AbsSource
    from abs_manager.verification_workflow import run_verification, start_verification

    executable = detect_cli()
    if not Path(executable).is_file():
        pytest.skip("Install official abs-cli 1.2.0 for read-only search protocol test")

    class MetadataABS(LocalABS):
        def do_GET(self):
            self.server.requests.append(("GET", self.path))
            if self.path == "/api/search/providers":
                self.reply({"providers": {"books": [{"value": "audible", "text": "Audible"}]}})
            elif self.path.startswith("/api/search/books?"):
                query = parse_qs(urlsplit(self.path).query)
                assert query["provider"] == ["audible"]
                self.reply([{"title": "Example", "author": "Author", "narrator": "Narrator", "duration": 20,
                             "url": "https://example.invalid/recording"}])
            elif self.path == "/status":
                self.reply({"serverVersion": "2.37.1"})
            else:
                self.reply({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MetadataABS)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = CliAdapter(Settings(f"http://127.0.0.1:{server.server_port}", "synthetic-only", executable, .1, 10))
        assert adapter.metadata_providers()[0]["value"] == "audible"
        store = Store(tmp_path / "search.sqlite3")
        aid = store.start_audit(adapter.server, "library-a", "Test")
        store.add_book(aid, Book(make_book()))
        store.finish_audit(aid)
        source = AbsSource(adapter, "audible")
        rid = start_verification(store, aid, source, ["current-id"])
        run_verification(store, rid, source)
        captured = store.verification_evidence(aid, "current-id")[0]
        assert captured["state"] == "ok"
        assert captured["candidates"][0]["duration"] == 1200
        assert all(r[0] == "GET" for r in server.requests)
        assert any(r[1].startswith("/api/search/books?") for r in server.requests)
        assert not store.history()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_official_cli_against_local_http_simulator(tmp_path):
    executable = detect_cli()
    if not Path(executable).is_file():
        pytest.skip("Install official abs-cli 1.2.0 to run its local protocol smoke test")
    if cli_config().get("refreshToken"):
        pytest.skip("A personal refresh-token config must not be used by the transport write test")
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalABS)
    server.book = make_book()
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = CliAdapter(Settings(f"http://127.0.0.1:{server.server_port}", "local-test-token", executable, 0.1, 10))
        result = adapter.probe()
        assert result["cli_version"] == "1.2.0"
        assert result["server_version"] == "2.37.1"
        assert result["writable"]
        store = Store(tmp_path / "transport.sqlite3")
        aid = run_audit(store, adapter, result["libraries"][0])
        assert not any(r[0] == "PATCH" for r in server.requests)
        ids = [f["id"] for f in store.findings(aid) if f["actionable"]]
        store.review(ids, "approved")
        plan = preview(store, aid)
        wf = Workflow(store, adapter)
        oid = wf.apply(plan, confirmation(plan))[0]
        assert store.operation(oid)["state"] == "verified"
        back = wf.rollback_preview(oid)
        wf.rollback(back, confirmation(back, "ROLLBACK"))
        patches = [r for r in server.requests if r[0] == "PATCH"]
        assert len(patches) == 2
        assert patches[0][2] == {"metadata": {"title": "Heaven's River"}}
        assert patches[1][2]["metadata"]["title"].endswith("Book 4")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_official_cli_transient_reads_resume_without_patch(tmp_path, monkeypatch):
    executable = detect_cli()
    if not Path(executable).is_file():
        pytest.skip("Install official abs-cli 1.2.0 for the local retry/resume protocol test")
    if cli_config().get("refreshToken"):
        pytest.skip("Do not use personal refresh-token config in protocol tests")

    class InterruptedABS(LocalABS):
        def do_GET(self):
            self.server.requests.append(("GET", self.path))
            if self.path == "/api/libraries":
                self.reply({"libraries": [{"id": "library-a", "name": "Audiobooks", "mediaType": "book"}]})
            elif self.path.startswith("/api/libraries/library-a/items"):
                self.reply({"results": self.server.books, "total": 2, "limit": 50, "page": 0})
            elif self.path == "/api/items/current-id?expanded=1":
                self.reply(self.server.books[0])
            elif self.path == "/api/items/second?expanded=1":
                if self.server.failures:
                    self.server.failures -= 1
                    self.reply({"error": "Bad Gateway; never-log-this-response-secret"}, 502)
                else:
                    self.reply(self.server.books[1])
            else:
                self.reply({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), InterruptedABS)
    server.books = [make_book(), make_book(item_id="second")]
    server.requests, server.failures = [], 3
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = CliAdapter(Settings(f"http://127.0.0.1:{server.server_port}", "local-test-token", executable, 0.1, 10))
        monkeypatch.setattr(adapter, "_pace", lambda: None)
        monkeypatch.setattr("abs_manager.adapter.time.sleep", lambda delay: None)
        store = Store(tmp_path / "retry.sqlite3")
        library = adapter.libraries()[0]
        with pytest.raises(AdapterError, match="transient_http.*retries=2.*exit_code=2"):
            run_audit(store, adapter, library)
        assert store.saved_progress(1) == 1
        assert store.audit(1)["status"] == "interrupted"
        run_audit(Store(store.path), adapter, library, resume_id=1)
        assert store.audit(1)["status"] == "complete"
        assert store.saved_progress(1) == 2
        assert server.requests.count(("GET", "/api/items/current-id?expanded=1")) == 1
        assert server.requests.count(("GET", "/api/items/second?expanded=1")) == 4
        assert all(request[0] == "GET" for request in server.requests)
        diagnostics = store.audit(1)["slow_requests"] + (tmp_path / "audit.log").read_text(encoding="utf-8")
        assert "never-log-this-response-secret" not in diagnostics
        assert "local-test-token" not in diagnostics
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
