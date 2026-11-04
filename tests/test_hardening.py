"""Synthetic Calibre identity, credential recovery, and source-state regressions."""
import copy
import importlib
import json

import pytest
from conftest import FakeAdapter, make_book
from test_streamlined import page, widget

from abs_manager import preferences
from abs_manager.calibre import import_catalog
from abs_manager.store import Store
from abs_manager.workflow import preview, run_audit


def ebook_store(tmp_path, path="Author/Title (12)"):
    raw = make_book(title="Title", path=path)
    raw["media"].update(duration=0, audioFiles=[])
    api = FakeAdapter([raw])
    store = Store(tmp_path / "ebook.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    return store, api, aid



def record(**kwargs):
    return {"id": 12, "uuid": "fixture-uuid", "title": "Title", "authors": ["Dennis E. Taylor"], **kwargs}



@pytest.mark.parametrize("target", [{"authors": ["Correct author"]}, {"title": "Correct title"},
                                    {"title": "Correct title", "authors": ["Correct author"]}])
def test_first_calibre_validation_after_accepted_edits_and_reimport(tmp_path, target):
    store, api, aid = ebook_store(tmp_path)
    store.confirm_targets(aid, "current-id", target)
    rows = [record(**target)]
    assert import_catalog(store, aid, json.dumps(rows), "library-one") == 1
    assert all(r["calibre_verified"] for r in store.corrections(aid))
    api.items["current-id"]["media"]["metadata"].update({k: [{"name": n} for n in v] if k == "authors" else v
                                                      for k, v in target.items()})
    second = run_audit(store, api, api.libraries()[0])
    assert import_catalog(store, second, json.dumps(rows), "library-one") == 1
    assert all(r["abs_observed"] for r in store.corrections(second))
    assert not api.writes



def test_calibre_binding_retained_after_subsequent_title_author_edits(tmp_path):
    store, _api, aid = ebook_store(tmp_path)
    assert import_catalog(store, aid, json.dumps([record()]), "library-one") == 1
    target = {"title": "Revised", "authors": ["Revised author"]}
    store.confirm_targets(aid, "current-id", target)
    assert import_catalog(store, aid, json.dumps([record(**target)]), "library-one") == 1
    assert all(r["calibre_verified"] for r in store.corrections(aid))



@pytest.mark.parametrize("change", [{"uuid": "other"}, {"id": 13}, {"authors": ["Contradictory author"]}])
def test_calibre_bound_identity_contradictions_fail_closed(tmp_path, change):
    store, _api, aid = ebook_store(tmp_path)
    import_catalog(store, aid, json.dumps([record()]), "library-one")
    store.confirm_targets(aid, "current-id", {"title": "Revised"})
    assert import_catalog(store, aid, json.dumps([record(title="Revised", **change)]), "library-one") == 0
    assert not store.corrections(aid)[0]["calibre_verified"]
    assert import_catalog(store, aid, json.dumps([record(title="Revised")]), "library-two") == 0



def test_ambiguous_calibre_requires_explicit_association_and_context(tmp_path):
    store, _api, aid = ebook_store(tmp_path, path="Author/Title")
    target = {"title": "Corrected", "authors": ["Corrected author"]}
    store.confirm_targets(aid, "current-id", target)
    catalog = json.dumps([record(**target), record(id=13, uuid="other", **target)])
    assert import_catalog(store, aid, catalog, "library-one") == 0
    assert not any(r["calibre_verified"] for r in store.corrections(aid))
    assert import_catalog(store, aid, catalog, "library-one", {"current-id": {"id": 12, "uuid": "fixture-uuid"}}) == 1
    assert all(r["calibre_verified"] for r in store.corrections(aid))
    with pytest.raises(ValueError):
        import_catalog(store, aid, catalog, associations={"current-id": {"id": 12, "uuid": "fixture-uuid"}})



def test_unscoped_numeric_calibre_id_never_verifies(tmp_path):
    store, _api, aid = ebook_store(tmp_path)
    store.confirm_targets(aid, "current-id", {"title": "Corrected"})
    assert import_catalog(store, aid, json.dumps([record(title="Corrected")])) == 0
    assert not store.corrections(aid)[0]["calibre_verified"]
    assert store.verification_report(aid)[0]["calibre_limitation"]



@pytest.mark.parametrize("failure", [ValueError("synthetic-secret"), PermissionError("synthetic-secret"),
                                     UnicodeError("synthetic-secret")])
def test_corrupt_credentials_are_redacted_and_recoverable(tmp_path, monkeypatch, failure):
    module = importlib.reload(preferences)
    monkeypatch.setenv("ABS_MM_SETTINGS", str(tmp_path / "settings"))
    monkeypatch.setattr(module, "protect", lambda value, decrypt=False: value[::-1])
    module.credential("http://test.invalid", "synthetic-secret")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(module, "protect", fail)
    with pytest.raises(module.CredentialError) as caught:
        module.credential("http://test.invalid")
    assert "synthetic-secret" not in str(caught.value)
    module.credential("http://test.invalid", forget=True)
    assert module.credential("http://test.invalid") == ""
    monkeypatch.setattr(module, "protect", lambda value, decrypt=False: value[::-1])
    module.credential("http://test.invalid", "replacement-synthetic")
    assert module.credential("http://test.invalid") == "replacement-synthetic"



def test_startup_bad_credential_recovers_in_settings(tmp_path, monkeypatch):
    module = importlib.reload(preferences)
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "startup.sqlite"))
    module.save({"server": "http://test.invalid"})
    def fail(*args, **kwargs):
        raise module.CredentialError(module.CREDENTIAL_RECOVERY)
    monkeypatch.setattr(module, "credential", fail)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(__import__("test_streamlined").APP), default_timeout=120).run()
    assert not at.exception and at.warning and "adapter" not in at.session_state
    page(at, "Settings")
    assert widget(at, "text_input", "API key")
    monkeypatch.setattr(module, "credential", lambda *a, **k: "")
    widget(at, "button", "Forget saved API key").click().run()
    assert not at.exception and "credential_error" not in at.session_state



def test_abs_write_does_not_verify_files(setup):
    from abs_manager.workflow import Workflow, confirmation
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": "Corrected"})
    fid = next(f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target")
    store.review([fid], "approved")
    plan = preview(store, aid, [fid])
    original = copy.deepcopy(api.items["current-id"]["media"]["audioFiles"])
    Workflow(store, api).apply(plan, confirmation(plan))
    assert api.items["current-id"]["media"]["audioFiles"] == original
    assert not any(r["field"] in ("embedded", "folder") for r in store.corrections(aid))



def test_inaccessible_credential_file_is_redacted(tmp_path, monkeypatch):
    from pathlib import Path
    module = importlib.reload(preferences)
    monkeypatch.setenv("ABS_MM_SETTINGS", str(tmp_path / "settings"))
    def denied(*args, **kwargs):
        raise PermissionError("synthetic-secret")
    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(module.CredentialError) as caught:
        module.credential("http://test.invalid")
    assert "synthetic-secret" not in str(caught.value)



def test_valid_saved_credential_reconnects_without_transport(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_streamlined import APP

    from abs_manager.adapter import CliAdapter
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "valid.sqlite"))
    preferences.save({"server": "http://test.invalid"})
    monkeypatch.setattr(preferences, "credential", lambda *a, **k: "synthetic-key")
    monkeypatch.setattr(CliAdapter, "_run", lambda *a, **k: pytest.fail("No unsolicited request"))
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception and at.session_state["adapter"].server == "http://test.invalid"
    at.run()
    assert not at.exception and "credential_error" not in at.session_state



def test_duplicate_calibre_uuid_is_ambiguous(tmp_path):
    store, _api, aid = ebook_store(tmp_path)
    store.confirm_targets(aid, "current-id", {"title": "Corrected"})
    assert import_catalog(store, aid, json.dumps([record(title="Corrected"), record(id=13)]), "library-one") == 0
    assert not store.corrections(aid)[0]["calibre_verified"]

