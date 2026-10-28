from pathlib import Path

from conftest import FakeAdapter
from streamlit.testing.v1 import AppTest

from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.store import Store
from abs_manager.workflow import run_audit

APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


def navigate(at, view):
    widget(at, "radio", "Workspace").set_value(view).run()
    assert not at.exception


def connection_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "connection.sqlite3"))
    monkeypatch.setenv("ABS_SERVER", "http://test.invalid")
    monkeypatch.setenv("ABS_TOKEN", "test-only-key")
    monkeypatch.setattr(CliAdapter, "_run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("Connection UI tests must not invoke the CLI")))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    navigate(at, "Settings")
    return at


def test_settings_success_survives_reruns_navigation_and_disconnect(tmp_path, monkeypatch):
    probes = []
    libraries = [{"id": key, "name": key, "mediaType": "book"} for key in ("audio-a", "audio-b")]

    def probe(adapter):
        probes.append(adapter.server)
        return {"cli_version": "1.2.0", "server_version": "2.37.1", "libraries": libraries,
                "warnings": ["Test compatibility warning"], "writable": True}

    monkeypatch.setattr(CliAdapter, "probe", probe)
    at = connection_app(tmp_path, monkeypatch)
    widget(at, "button", "Connect & check compatibility").click().run()
    assert not at.exception
    assert at.session_state["connection_result"]["server_version"] == "2.37.1"
    assert any("Connected." in s.value for s in at.success)
    assert any("2.37.1" in m.value and "1.2.0" in m.value for m in at.markdown)
    assert any("Test compatibility warning" in w.value for w in at.warning)
    widget(at, "selectbox", "Book library").set_value("audio-b").run()
    for page in ("Library", "Review", "Apply", "History", "Settings"):
        navigate(at, page)
        at.run()
        assert not at.exception
        assert at.session_state["library"]["id"] == "audio-b"
        assert any("Connected · http://test.invalid" in c.value for c in at.caption)
    assert widget(at, "selectbox", "Book library").value == "audio-b"
    assert len(probes) == 1
    widget(at, "button", "Disconnect").click().run()
    assert not at.exception
    assert "adapter" not in at.session_state
    assert "connection_result" not in at.session_state
    assert "library" not in at.session_state
    assert any("disconnected" in c.value for c in at.caption)


def test_settings_failed_reconnection_clears_stale_status_and_can_recover(tmp_path, monkeypatch):
    probes = []

    def probe(adapter):
        probes.append(adapter.server)
        if len(probes) == 2:
            raise AdapterError("Simulated connection failure")
        return {"cli_version": "1.2.0", "server_version": "2.37.1", "warnings": [],
                "libraries": [{"id": "audio", "name": "Audio", "mediaType": "book"}], "writable": True}

    monkeypatch.setattr(CliAdapter, "probe", probe)
    at = connection_app(tmp_path, monkeypatch)
    widget(at, "button", "Connect & check compatibility").click().run()
    widget(at, "text_input", "Server URL").set_value("http://failed.invalid")
    widget(at, "button", "Connect & check compatibility").click().run()
    assert not at.exception
    for key in ("adapter", "connection_result", "library"):
        assert key not in at.session_state
    assert any("Simulated connection failure" in e.value for e in at.error)
    assert not at.success
    assert any("disconnected" in c.value for c in at.caption)
    for page in ("Library", "Apply", "History", "Settings"):
        navigate(at, page)
        at.run()
        assert not at.exception
    assert any("Simulated connection failure" in e.value for e in at.error)
    assert len(probes) == 2  # Neither reruns nor navigation retry a connection.
    widget(at, "text_input", "Server URL").set_value("http://test.invalid")
    widget(at, "button", "Connect & check compatibility").click().run()
    assert not at.exception
    assert not at.error
    assert any("Connected." in s.value for s in at.success)
    assert len(probes) == 3


def test_snapshot_selection_survives_widget_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "snapshots.sqlite3"))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    widget(at, "button", "Load read-only sample library").click().run()
    store = Store(tmp_path / "snapshots.sqlite3")
    from abs_manager.sample import sample_audit
    from abs_manager.workflow import import_previous
    import_previous(store, sample_audit(), "Second sample")
    at.run()
    widget(at, "selectbox", "Audit snapshot").set_value(2).run()
    widget(at, "selectbox", "Audit snapshot").set_value(1).run()
    for page in ("Settings", "History", "Library", "Review", "Apply"):
        navigate(at, page)
    assert widget(at, "selectbox", "Audit snapshot").value == 1
    assert at.session_state["snapshot_choice"] == 1


def test_empty_startup_and_all_reruns_are_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "empty.sqlite3"))
    monkeypatch.setattr("abs_manager.adapter.CliAdapter._run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("UI rendering must never call the CLI")))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    for view in ("Review", "Apply", "History", "Settings", "Library"):
        navigate(at, view)
    widget(at, "button", "Load read-only sample library").click().run()
    assert not at.exception
    assert len(Store(tmp_path / "empty.sqlite3").books(1)) == 6
    for view in ("Review", "Apply", "History", "Library"):
        navigate(at, view)


def test_ui_approve_preview_confirm_verify_and_guarded_rollback(tmp_path, monkeypatch):
    path = tmp_path / "ui.sqlite3"
    monkeypatch.setenv("ABS_MM_DB", str(path))
    api = FakeAdapter()
    store = Store(path)
    aid = run_audit(store, api, api.libraries()[0])
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["adapter"] = api
    at.session_state["library"] = api.libraries()[0]
    at.session_state["snapshot_choice"] = aid
    at.run()
    navigate(at, "Apply")
    assert not api.writes
    navigate(at, "Review")
    fid = next(f["id"] for f in store.findings(aid) if f["actionable"])
    widget(at, "multiselect", "Explicitly select proposals for a bulk decision").set_value([fid]).run()
    widget(at, "button", "Approve selected fields").click().run()
    assert not at.exception
    assert store.findings(aid)[0]["status"] == "approved"
    navigate(at, "Apply")
    phrase = next(c.value for c in at.code if c.value.startswith("APPLY "))
    # Even with a valid phrase, omitting the deliberate checkbox cannot write.
    widget(at, "text_input", "Apply confirmation").set_value(phrase)
    widget(at, "button", "Apply confirmed changes").click().run()
    assert not api.writes
    widget(at, "checkbox", "I approve the exact fields shown and want to write this batch to Audiobookshelf.").check()
    widget(at, "button", "Apply confirmed changes").click().run()
    assert not at.exception
    assert len(api.writes) == 1
    assert store.history()[0]["state"] == "verified"
    for view in ("Library", "Review", "Apply", "History"):
        navigate(at, view)
        at.run()
        assert len(api.writes) == 1
    rollback_phrase = next(c.value for c in at.code if c.value.startswith("ROLLBACK "))
    widget(at, "text_input", "Rollback confirmation").set_value(rollback_phrase)
    widget(at, "checkbox", "Restore the exact previous values shown above.").check()
    widget(at, "button", "Restore confirmed fields").click().run()
    assert not at.exception
    assert len(api.writes) == 2
    assert any(row["state"] == "reverted" for row in store.history())
    at.run()
    assert len(api.writes) == 2


def test_editing_an_approved_field_defaults_back_to_pending(setup, monkeypatch):
    store, api, aid = setup
    monkeypatch.setenv("ABS_MM_DB", store.path)
    fid = next(f["id"] for f in store.findings(aid) if f["actionable"])
    store.review([fid], "approved")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    navigate(at, "Review")
    assert widget(at, "selectbox", "Decision").value == "pending"
    widget(at, "text_area", "Edit proposed value").set_value("A separately reviewed title")
    widget(at, "button", "Save field decision").click().run()
    assert not at.exception
    saved = next(f for f in store.findings(aid) if f["id"] == fid)
    assert saved["target"] == "A separately reviewed title"
    assert saved["status"] == "pending"
    assert not api.writes
