from pathlib import Path

from conftest import FakeAdapter
from streamlit.testing.v1 import AppTest

from abs_manager.store import Store
from abs_manager.workflow import run_audit

APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


def navigate(at, view):
    widget(at, "radio", "Workspace").set_value(view).run()
    assert not at.exception


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
