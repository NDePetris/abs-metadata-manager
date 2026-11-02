from types import SimpleNamespace

import pytest

from abs_manager import preferences
from abs_manager.calibre import validate_local


def test_preferences_allowlist_and_encrypted_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_SETTINGS", str(tmp_path / "settings"))
    # Bypass only the autouse fake credential function to exercise persistence, never Windows APIs.
    import importlib
    module = importlib.reload(preferences)
    monkeypatch.setattr(module, "protect", lambda data, decrypt=False: data[::-1])
    module.save({"server": "http://test.invalid", "library": "synthetic"})
    with pytest.raises(ValueError):
        module.save({"token": "synthetic-secret"})
    module.credential("http://test.invalid", "synthetic-secret")
    assert module.credential("http://test.invalid") == "synthetic-secret"
    assert "synthetic-secret" not in (tmp_path / "settings" / "settings.json").read_text()
    assert not any(b"synthetic-secret" in p.read_bytes() for p in (tmp_path / "settings").iterdir())
    module.credential("http://test.invalid", forget=True)
    assert not module.credential("http://test.invalid")


def test_calibre_runner_fixed_readonly_arguments(setup, tmp_path, monkeypatch):
    store, _api, aid = setup
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="[]")
    monkeypatch.setattr("subprocess.run", run)
    validate_local(store, aid, "fixture-calibredb", str(tmp_path))
    args, kwargs = calls[0]
    assert args[:2] == ["fixture-calibredb", "list"]
    assert "--for-machine" in args and kwargs["shell"] is False
    assert kwargs["timeout"] == 60


def test_preview_selection_rejects_changed_approval(setup):
    from abs_manager.workflow import SafetyError, Workflow, confirmation, preview
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": "Desired", "narrators": ["Desired narrator"]})
    ids = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target"]
    store.review(ids, "approved")
    plan = preview(store, aid, ids[:1])
    assert len(plan["books"][0]["changes"]) == 1
    store.review(ids[:1], "pending")
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes


def test_pending_target_survives_refresh_without_approval(setup):
    from abs_manager.workflow import preview, run_audit
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": "Desired"})
    new = run_audit(store, api, api.libraries()[0])
    pending = [f for f in store.findings(new) if f["kind"] == "confirmed_target"]
    assert pending and pending[0]["target"] == "Desired" and pending[0]["status"] == "pending"
    assert not preview(store, new)["books"] and not api.writes
