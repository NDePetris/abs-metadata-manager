import json
import logging
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest
from conftest import FakeAdapter, make_book

from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.config import Settings, defaults
from abs_manager.diagnostics import local_log
from abs_manager.locking import BusyError, operation_lock
from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, preview, resume_unavailable, run_audit


@pytest.fixture(autouse=True)
def close_test_logs():
    # Windows cannot remove temporary log files while their handlers remain open.
    before = set(logging.Logger.manager.loggerDict)
    yield
    for name in set(logging.Logger.manager.loggerDict) - before:
        logger = logging.getLogger(name)
        if name.startswith("abs.audit."):
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


def interrupted(tmp_path):
    store = Store(tmp_path / "audit.sqlite3")
    api = FakeAdapter([make_book(item_id=str(i)) for i in range(3)])
    original = api.item

    def item(item_id):
        if item_id == "1":
            raise KeyboardInterrupt()
        return original(item_id)

    api.item = item
    with pytest.raises(KeyboardInterrupt):
        run_audit(store, api, api.libraries()[0])
    api.item = original
    return store, api, store.latest(api.server, "library-a")["id"]


@pytest.mark.parametrize("restart", [False, True])
def test_resume_retains_snapshot_and_skips_saved_expanded_requests(tmp_path, restart):
    store, api, aid = interrupted(tmp_path)
    findings = store.findings(aid)
    assert store.saved_progress(aid) == 1
    assert store.audit(aid)["expected"] == 3
    with pytest.raises(ValueError):
        store.review([findings[0]["id"]], "approved")
    with pytest.raises(SafetyError):
        preview(store, aid)
    if restart:
        store = Store(store.path)
        # Simulate an abrupt exit leaving status running, rather than a caught Stop.
        with store.connect() as db:
            db.execute("UPDATE audits SET status='running' WHERE id=?", (aid,))
        api = FakeAdapter(list(api.items.values()))
    api.reads.clear()
    assert run_audit(store, api, api.libraries()[0], resume_id=aid) == aid
    assert api.reads == ["1", "2"]
    assert len(store.audits()) == 1
    assert len(store.books(aid)) == 3
    assert store.findings(aid)[:len(findings)] == findings
    assert store.audit(aid)["status"] == "complete"
    assert not api.writes
    assert not store.history()
    assert not preview(store, aid)["books"]


@pytest.mark.parametrize("change", ["add", "remove", "replace", "path", "relPath", "ino", "library", "folders", "server"])
def test_resume_rejects_changed_identity_or_membership_before_expanded_reads(tmp_path, change):
    store, api, aid = interrupted(tmp_path)
    library = api.libraries()[0]
    if change == "add":
        api.items["extra"] = make_book(item_id="extra")
    elif change == "remove":
        del api.items["2"]
    elif change == "replace":
        del api.items["2"]
        api.items["replacement"] = make_book(item_id="replacement")
    elif change in ("path", "relPath", "ino"):
        api.items["0"][change] = "changed"
    elif change == "library":
        library["id"] = "other"
    elif change == "folders":
        api.libraries = lambda: [{**library, "folders": [{"id": "folder", "fullPath": "/moved"}]}]
    else:
        api.server = "http://other.invalid"
    api.reads.clear()
    with pytest.raises(SafetyError):
        run_audit(store, api, library, resume_id=aid)
    assert not api.reads
    assert not api.writes
    assert store.saved_progress(aid) == 1
    assert store.audit(aid)["status"] != "complete"


def test_legacy_partial_is_preserved_but_cannot_claim_unchanged_membership(tmp_path):
    store = Store(tmp_path / "old.sqlite3")
    api = FakeAdapter()
    aid = store.start_audit(api.server, "library-a", "Audiobooks")
    store.add_book(aid, Book(make_book()))
    store.finish_audit(aid, "interrupted")
    assert "no membership checkpoint" in resume_unavailable(store, store.audit(aid), api.server, api.libraries()[0])
    with pytest.raises(SafetyError, match="no membership checkpoint"):
        run_audit(store, api, api.libraries()[0], resume_id=aid)
    assert store.saved_progress(aid) == 1


def test_cannot_complete_partial_checkpoint_or_resume_superseded_audit(tmp_path):
    store, api, aid = interrupted(tmp_path)
    with pytest.raises(ValueError, match="every expected book"):
        store.finish_audit(aid)
    new = run_audit(store, api, api.libraries()[0])
    assert new != aid
    with pytest.raises(SafetyError, match="newer audit"):
        run_audit(store, api, api.libraries()[0], resume_id=aid)
    assert store.saved_progress(aid) == 1


def test_resume_cannot_bypass_workspace_lock(tmp_path):
    store, api, aid = interrupted(tmp_path)
    with operation_lock(store.path), pytest.raises(BusyError):
        run_audit(store, api, api.libraries()[0], resume_id=aid)
    assert not api.writes


@pytest.fixture
def cli(tmp_path, monkeypatch):
    adapter = CliAdapter(Settings("https://test.invalid", "private-api-key", "fake-cli", 0.1))
    adapter.log = local_log(tmp_path / "audit.log")
    sleeps = []
    monkeypatch.setattr(adapter, "_pace", lambda: None)
    monkeypatch.setattr("abs_manager.adapter.time.sleep", sleeps.append)
    monkeypatch.setattr("abs_manager.adapter.cli_config", dict)
    return adapter, sleeps


@pytest.mark.parametrize("failure", ["HTTP 429", "HTTP 502", "HTTP 503", "HTTP 504", "ECONNRESET", "timeout",
                                     "Request failed (502 Bad Gateway)", "Response status code: 503"])
def test_readonly_retries_succeed_with_backoff(cli, monkeypatch, failure):
    adapter, sleeps = cli
    calls, events = [], []
    adapter.diagnostic = events.append

    def run(args, **kw):
        calls.append((args, kw))
        if len(calls) < 3:
            if failure == "timeout":
                raise subprocess.TimeoutExpired("fake-cli", 90, stderr="private-api-key")
            return SimpleNamespace(stdout="", stderr=failure, returncode=2)
        return SimpleNamespace(stdout=json.dumps(make_book()), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    adapter.item("current-id")
    assert len(calls) == 3
    assert sleeps == [1, 2]
    assert all(kw["timeout"] == 90 for _, kw in calls)
    assert events[-1]["state"] == "ok"
    assert events[-1]["retry_count"] == 2
    assert all(args[1:3] == ["items", "get"] for args, _ in calls)


@pytest.mark.parametrize("stderr,stdout", [("HTTP 401", ""), ("HTTP 403", ""), ("HTTP 404", ""),
                                         ("validation failed", ""), ("", "not-json"), ("", "{}"),
                                         ("HTTP 401; HTTP 502", "")])
def test_permanent_or_malformed_errors_are_not_retried(cli, monkeypatch, stderr, stdout):
    adapter, sleeps = cli
    calls = []

    def run(*args, **kw):
        calls.append(args)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=2 if stderr else 0)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AdapterError):
        adapter.item("current-id")
    assert len(calls) == 1
    assert not sleeps


def test_exhausted_retries_keep_progress_through_multiple_resumes(cli, tmp_path, monkeypatch):
    adapter, sleeps = cli
    store = Store(tmp_path / "audit.sqlite3")
    books = [make_book(item_id=str(i)) for i in range(3)]
    library = FakeAdapter().libraries()[0]
    fail = True
    calls = []

    def run(args, **kw):
        calls.append(args)
        assert args[1:3] in (["items", "list"], ["items", "get"], ["libraries", "list"])
        if args[1:3] == ["libraries", "list"]:
            value = {"libraries": [library]}
        elif args[1:3] == ["items", "list"]:
            value = {"results": books, "total": 3, "page": 0}
        else:
            item_id = args[args.index("--id") + 1]
            if item_id == "1" and fail:
                raise subprocess.TimeoutExpired("fake-cli", 90)
            value = books[int(item_id)]
        return SimpleNamespace(stdout=json.dumps(value), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    for resume_id in (None, 1, 1):
        with pytest.raises(AdapterError, match="items get.*timeout.*retries=2"):
            run_audit(store, adapter, library, resume_id=resume_id)
        store = Store(store.path)
        assert store.saved_progress(1) == 1
        assert store.audit(1)["status"] == "interrupted"
        assert "submitted update" not in store.audit(1)["error"]
    fail = False
    run_audit(store, adapter, library, resume_id=1)
    assert store.saved_progress(1) == 3
    assert sum(args[1:3] == ["items", "get"] and args[4] == "0" for args in calls) == 1
    slow = json.loads(store.audit(1)["slow_requests"])
    assert slow and all("duration" in r and "exit_code" in r for r in slow)
    assert sleeps == [1, 2] * 3


def test_diagnostics_never_retain_raw_stderr_or_secrets(cli, monkeypatch, tmp_path):
    adapter, _ = cli
    raw = ('HTTP 502 Bad Gateway Authorization: Bearer private-api-key\n'
           'https://user:password@host/?token=query-secret x-api-key: another-secret\n'
           'accessToken="unknown-secret" CF-Access-Client-Secret: tunnel-secret')
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="", stderr=raw, returncode=2))
    with pytest.raises(AdapterError) as error:
        adapter.page("library-a", 7, 50)
    text = str(error.value) + (tmp_path / "audit.log").read_text(encoding="utf-8")
    for secret in ("private-api-key", "password", "query-secret", "another-secret", "unknown-secret", "tunnel-secret"):
        assert secret not in text
    assert "items list" in text and "page=7" in text
    assert "HTTP 502" in text
    assert "Bad Gateway" in text
    assert "submitted update" not in text
    handler = adapter.log.handlers[0]
    assert handler.maxBytes == 1_000_000 and handler.backupCount == 3


def test_mutation_with_transient_http_failure_is_never_retried(cli, monkeypatch):
    adapter, sleeps = cli
    adapter.version = "1.2.0"
    calls = []

    def run(*args, **kw):
        calls.append(args)
        return SimpleNamespace(stdout="", stderr="HTTP 503", returncode=2)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AdapterError, match="reconcile History"):
        adapter.update("current-id", {"title": "Explicitly confirmed elsewhere"})
    assert len(calls) == 1 and not sleeps


def test_changed_membership_while_expanding_cannot_complete(tmp_path):
    api = FakeAdapter()
    store = Store(tmp_path / "audit.sqlite3")
    original = api.item

    def item(item_id):
        raw = original(item_id)
        api.items["extra"] = make_book(item_id="extra")
        return raw

    api.item = item
    with pytest.raises(SafetyError, match="changed during audit"):
        run_audit(store, api, api.libraries()[0])
    audit = store.audit(1)
    assert audit["status"] == "interrupted" and audit["resume_blocked"]
    assert store.saved_progress(1) == 1
    assert not api.writes


def test_resume_after_failure_before_membership_checkpoint(tmp_path):
    store = Store(tmp_path / "audit.sqlite3")
    api = FakeAdapter()
    page = api.page
    api.page = lambda *a: (_ for _ in ()).throw(AdapterError("items list failed: timeout"))
    with pytest.raises(AdapterError):
        run_audit(store, api, api.libraries()[0])
    assert store.audit(1)["membership"] is None
    api.page = page
    assert run_audit(store, api, api.libraries()[0], resume_id=1) == 1
    assert store.audit(1)["status"] == "complete"


def test_timeout_setting_default_and_override(monkeypatch):
    monkeypatch.setattr("abs_manager.config.cli_config", dict)
    monkeypatch.delenv("ABS_MM_TIMEOUT", raising=False)
    assert defaults()["timeout"] == 90
    monkeypatch.setenv("ABS_MM_TIMEOUT", "120")
    assert defaults()["timeout"] == 120
    assert Settings("https://test.invalid", "fake-key", timeout=120).timeout == 120


@pytest.mark.parametrize("operation", ["libraries", "page"])
def test_listing_operations_retry_and_keep_page_context(cli, monkeypatch, operation):
    adapter, sleeps = cli
    calls = []

    def run(args, **kw):
        calls.append(args)
        if len(calls) == 1:
            return SimpleNamespace(stdout="", stderr="API error (502): Bad Gateway", returncode=2)
        value = {"libraries": []} if operation == "libraries" else {"results": [], "total": 0, "page": 7}
        return SimpleNamespace(stdout=json.dumps(value), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    adapter.libraries() if operation == "libraries" else adapter.page("library-a", 7, 50)
    assert len(calls) == 2 and sleeps == [1]


def test_old_database_schema_migrates_without_discarding_audit_history(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE audits (id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL,
                name TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL,
                finished TEXT, error TEXT, read_only INTEGER NOT NULL DEFAULT 0);
            INSERT INTO audits VALUES(1,'https://test.invalid','library-a','Audiobooks','interrupted',
                '2026-10-28',NULL,'Old interrupted audit',0);
        """)
    store = Store(path)
    audit = store.audit(1)
    assert audit["error"] == "Old interrupted audit"
    assert audit["membership"] is None and audit["expected"] is None
    assert json.loads(audit["slow_requests"]) == []


def test_expanded_identity_mismatch_blocks_resume_without_saving_wrong_target(tmp_path):
    store = Store(tmp_path / "audit.sqlite3")
    api = FakeAdapter()
    api.item = lambda item_id: make_book(item_id="new-id")
    with pytest.raises(AdapterError, match="identity/path differs"):
        run_audit(store, api, api.libraries()[0])
    assert store.audit(1)["resume_blocked"]
    assert store.saved_progress(1) == 0
    assert not api.writes
