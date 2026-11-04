from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_streamlined import app, page, widget

from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.store import Store

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_empty_startup_and_all_reruns_are_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "empty.sqlite"))
    monkeypatch.setattr(CliAdapter, "_run", lambda *a, **k: pytest.fail("No CLI on render"))
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    for view in ("Import", "Match & Verify", "Changes", "History", "Settings"):
        page(at, view)
    assert not at.exception


def test_settings_success_failure_and_disconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "connection.sqlite"))
    calls = []
    def probe(adapter):
        calls.append(adapter.server)
        if "failed" in adapter.server:
            raise AdapterError("Synthetic failure")
        return {"server_version": "2.37.1", "cli_version": "1.2.0", "warnings": [],
                "libraries": [{"id": "audio", "name": "Audio", "mediaType": "book"}]}
    monkeypatch.setattr(CliAdapter, "probe", probe)
    monkeypatch.setattr(CliAdapter, "metadata_providers", lambda _: [])
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    page(at, "Settings")
    widget(at, "text_input", "Server URL").set_value("http://test.invalid")
    widget(at, "text_input", "API key").set_value("synthetic-key")
    widget(at, "button", "Connect & check compatibility").click().run()
    assert not at.exception and "adapter" in at.session_state
    at.run()
    assert len(calls) == 1
    widget(at, "text_input", "Server URL").set_value("http://failed.invalid")
    widget(at, "button", "Connect & check compatibility").click().run()
    assert not at.exception and "adapter" not in at.session_state
    assert at.error
    widget(at, "text_input", "Server URL").set_value("http://test.invalid")
    widget(at, "button", "Connect & check compatibility").click().run()
    widget(at, "button", "Disconnect").click().run()
    assert "adapter" not in at.session_state


def test_suggestions_do_not_appear_as_actionable_changes(setup, monkeypatch):
    store, api, _aid = setup
    at = app(store, api, monkeypatch)
    page(at, "Changes")
    assert not [c for c in at.checkbox if c.label.startswith("Select ")]
    assert not api.writes


def test_interrupted_inventory_keeps_resume_action(setup, monkeypatch):
    store, api, _aid = setup
    interrupted = store.start_audit(api.server, "library-a", "Audio")
    store.finish_audit(interrupted, "interrupted", "Synthetic interruption")
    at = app(store, api, monkeypatch)
    assert any(b.label == "Resume Import" for b in at.button)
    page(at, "Match & Verify")
    assert not api.writes


def test_history_retains_legacy_snapshots(tmp_path, monkeypatch):
    from abs_manager import preferences
    from abs_manager.sample import sample_audit
    from abs_manager.workflow import import_previous
    path = tmp_path / "history.sqlite"
    store = Store(path)
    import_previous(store, sample_audit())
    preferences.save({"server": "offline://previous-export", "library": "offline"})
    monkeypatch.setenv("ABS_MM_DB", str(path))
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    page(at, "History")
    assert widget(at, "selectbox", "Audit snapshot")
