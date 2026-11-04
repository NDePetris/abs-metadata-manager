import importlib.util
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from abs_manager.adapter import AdapterError, CliAdapter, probe_metadata_search
from abs_manager.config import Settings
from abs_manager.diagnostics import provider_failure, response_shape


@pytest.mark.parametrize("body,kind", [
    ("<!DOCTYPE html><html>private metadata and key=synthetic-secret</html>", "html_like"),
    ("<html><body>interstitial</body></html>", "html_like"),
    ("", "empty"), ("Unauthorized", "other_non_json"),
    ('[{"title":"synthetic-secret"}', "json_like_invalid"),
    ('\ufeff[]', "utf8_bom"), ('INFO synthetic-secret\n[]', "other_non_json"),
])
def test_zero_exit_non_json_remains_rejected_with_shape_only(monkeypatch, body, kind):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=body, stderr="", returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    adapter = CliAdapter(Settings("http://test.invalid", "synthetic-secret", "fake-cli", .1, 3))
    with pytest.raises(AdapterError) as caught:
        adapter._run(["metadata", "search", "--provider", "audible", "--title", "Crossroads of Twilight",
                      "--author", "Robert Jordan"])
    diagnostic = provider_failure(caught.value, "abs:audible", "metadata search")
    assert diagnostic["category"] == "malformed_response"
    assert diagnostic["exit_code"] == 0 and diagnostic["response_shape"]["kind"] == kind
    assert "synthetic-secret" not in json.dumps(diagnostic) + str(caught.value)
    assert "HTTP status/content type unknown" in diagnostic["reached_abs"]
    assert calls == [["fake-cli", "metadata", "search", "--provider", "audible",
                      "--title", "Crossroads of Twilight", "--author", "Robert Jordan"]]


@pytest.mark.parametrize("body,kind", [(b"\x1f\x8bcompressed", "gzip_bytes"),
                                      (b"\xff\xfeprivate", "invalid_utf8"),
                                      (b'[{"private":"value"}]', "valid_json")])
def test_raw_byte_shape_does_not_expose_contents(body, kind):
    shape = response_shape(body)
    assert shape["kind"] == kind
    assert "private" not in json.dumps(shape) and "value" not in json.dumps(shape)


def test_large_unicode_json_has_no_cli_adapter_formatting_workaround(monkeypatch):
    rows = [{"title": "Résumé – Ω 日本語 " * 20000, "narrator": "Smith, John"}]
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps(rows, ensure_ascii=False), stderr="", returncode=0))
    adapter = CliAdapter(Settings("http://test.invalid", "synthetic", "fake-cli", .1, 3))
    assert adapter._run(["metadata", "search", "--provider", "audible", "--title", "Title"]) == rows


@pytest.mark.parametrize("status,content_type,body,kind", [
    (200, "text/html", b"<html>synthetic-secret</html>", "html_like"),
    (200, "application/json", b'[{"title":"synthetic-secret"}]', "valid_json"),
    (200, "application/json", b"", "empty"),
    (401, "text/plain", b"Unauthorized", "other_non_json"),
    (302, "text/html", b"", "empty"),
    (429, "application/json", b'{"error":"synthetic-secret"}', "valid_json"),
    (503, "text/plain", b"synthetic-secret", "other_non_json"),
    (200, "application/json", b'{"error":"synthetic-secret"}', "valid_json"),
    (200, "application/json", b"[null]", "valid_json"),
])
def test_explicit_probe_is_one_get_no_redirect_or_credential_output(monkeypatch, status, content_type, body, kind):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header("Location", "https://must-not-follow.invalid/?key=synthetic-secret")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(f"http://127.0.0.1:{server.server_port}", "synthetic-secret", timeout=3)
        result = probe_metadata_search(settings, "audible", {"title": "Crossroads of Twilight", "author": "Robert Jordan"})
        assert result["http_status"] == status and result["response_shape"]["kind"] == kind
        assert len(calls) == 1 and calls[0][0] == "GET"
        parts = urlsplit(calls[0][1])
        assert parts.path == "/api/search/books"
        assert parse_qs(parts.query) == {"provider": ["audible"], "title": ["Crossroads of Twilight"], "author": ["Robert Jordan"]}
        assert calls[0][2] == "Bearer synthetic-secret"
        assert "synthetic-secret" not in json.dumps(result) and "Location" not in json.dumps(result)
        # The production read-only alternative uses that exact endpoint and key,
        # rejects non-JSON/schema errors, and never launches/refreshes via CLI.
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("No CLI search or fallback"))
        adapter = CliAdapter(settings)
        messages = []
        adapter.log = SimpleNamespace(info=messages.append)
        if status == 200 and body in (b'[{"title":"synthetic-secret"}]', b'[null]'):
            assert len(adapter.metadata_search("audible", {"title": "Crossroads of Twilight", "author": "Robert Jordan"})) == 1
        else:
            expected = ("authorization" if status == 401 else "transient_http" if status in (429, 503)
                        else "permanent_http" if status == 302 else "invalid_schema" if kind == "valid_json"
                        else "malformed_response")
            with pytest.raises(AdapterError) as caught:
                adapter.metadata_search("audible", {"title": "Crossroads of Twilight", "author": "Robert Jordan"})
            diagnostic = provider_failure(caught.value, "abs:audible", "metadata search")
            assert diagnostic["category"] == expected and diagnostic["transport"] == "direct_get"
            assert diagnostic["http_status"] == str(status) and diagnostic["exit_code"] is None
            assert "synthetic-secret" not in json.dumps(diagnostic)
        assert len(calls) == 2 and calls[0] == calls[1]
        assert "synthetic-secret" not in "".join(messages)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_cli_diagnostic_refuses_existing_config_without_reading_it(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_metadata_search.py"
    spec = importlib.util.spec_from_file_location("diagnose_search", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / ".abs-cli" / "config.json"
    config.parent.mkdir()
    config.write_text("synthetic-private-config")
    monkeypatch.setattr(module, "cli_profile", lambda: tmp_path)
    monkeypatch.setattr("sys.argv", [str(path), "--with-cli"])
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("Must refuse before any prompts or requests"))
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("Must not read saved credentials"))
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 2
    assert "synthetic-private-config" not in capsys.readouterr().err


@pytest.mark.parametrize("exception,category", [(requests.Timeout, "timeout"),
                                                (requests.ConnectionError, "connection_reset")])
def test_direct_search_does_not_retry_or_expose_request_exception(monkeypatch, exception, category):
    calls = []
    def get(*args, **kwargs):
        calls.append((args, kwargs))
        raise exception("https://secret.invalid/?key=synthetic-secret")
    monkeypatch.setattr(requests.Session, "get", get)
    adapter = CliAdapter(Settings("http://test.invalid", "synthetic-secret", "fake-cli", .1, 3))
    with pytest.raises(AdapterError) as caught:
        adapter.metadata_search("audible", {"title": "Title"})
    assert len(calls) == 1 and caught.value.diagnostic["category"] == category
    assert "synthetic-secret" not in str(caught.value) + json.dumps(caught.value.diagnostic)
