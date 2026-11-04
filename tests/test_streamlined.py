import copy
import json
from pathlib import Path

import pytest
from conftest import FakeAdapter, make_book
from streamlit.testing.v1 import AppTest

from abs_manager.calibre import import_catalog
from abs_manager.models import FIELDS
from abs_manager.store import Store
from abs_manager.verification import candidate
from abs_manager.workflow import Workflow, confirmation, preview, run_audit

APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


def app(store, api, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", store.path)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["adapter"] = api
    at.session_state["library"] = api.libraries()[0]
    return at.run()


def page(at, label):
    widget(at, "radio", "Workspace").set_value(label).run()
    assert not at.exception


def test_confirm_stage_apply_narrator_and_reimport(tmp_path, monkeypatch):
    api = FakeAdapter([make_book(title="Title", series=None)])
    store = Store(tmp_path / "state.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "button", "Select all").click().run()
    widget(at, "button", "Confirm").click().run()
    assert not at.exception
    assert not preview(store, aid)["books"]
    widget(at, "button", "Show all books").click().run()
    widget(at, "text_input", "Desired narrators").set_value("Correct Narrator").run()
    widget(at, "button", "Confirm").click().run()
    assert not at.exception and not api.writes
    page(at, "Changes")
    assert store.corrections(aid)[0]["proposed"] == ["Correct Narrator"]
    finding = next(f for f in store.findings(aid) if f["kind"] == "confirmed_target" and f["field"] == "narrators")
    store.review([finding["id"]], "approved")
    plan = preview(store, aid, [finding["id"]])
    Workflow(store, api).apply(plan, confirmation(plan))
    new = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(new, "current-id")["title"]["valid"]
    assert store.corrections(new)[0]["abs_observed"]


def test_identifier_calibre_transition_preserves_title(tmp_path):
    raw = make_book(title="Title", path="Author/Title (12)")
    raw["media"].update(audioFiles=[], duration=0)
    api = FakeAdapter([raw])
    store = Store(tmp_path / "ebook.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    store.confirm_targets(aid, raw["id"], {"title": "Title"})
    target = {"isbn": "9780306406157"}
    store.confirm_targets(aid, raw["id"], {"identifiers": target})
    import_catalog(store, aid, json.dumps([{"id": 12, "uuid": "fixture-uuid", "title": "Title",
                    "authors": ["Dennis E. Taylor"], "identifiers": target}]), "fixture-library")
    api.items[raw["id"]]["media"]["metadata"].update(target)
    new = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(new, raw["id"])["title"]["valid"]
    assert not api.writes


def test_provider_refresh_retains_recording_identity():
    a = candidate({"id": "stable-recording", "title": "Title", "narrator": "Name"}, "fixture")
    b = candidate({"id": "stable-recording", "title": "Title revised", "narrator": "Name revised"}, "fixture")
    assert a["id"] == b["id"]
    assert candidate({"id": "different-recording", "title": "Title"}, "fixture")["id"] != a["id"]


def test_staging_is_atomic_and_never_approves(setup):
    store, api, aid = setup
    before = copy.deepcopy(store.book(aid, "current-id").raw)
    with pytest.raises(ValueError):
        store.confirm_targets(aid, "current-id", {"title": "Correct", "authors": "bad"})
    assert not store.corrections(aid)
    store.confirm_targets(aid, "current-id", {"title": "Correct"})
    assert store.book(aid, "current-id").raw == before
    assert not preview(store, aid)["books"] and not api.writes


@pytest.mark.parametrize("destination", ["Import", "Match & Verify", "Changes", "History", "Settings"])
def test_navigation_reruns_no_transport(setup, monkeypatch, destination):
    store, api, _aid = setup
    at = app(store, api, monkeypatch)
    api.item = lambda *_: pytest.fail("Reruns cannot fetch")
    page(at, destination)
    at.run()
    assert not at.exception and not api.writes


def test_manual_fields_and_expected_targets_remain_separate(setup):
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {f: store.book(aid, "current-id").values[f] for f in FIELDS})
    assert not store.corrections(aid)
    new = run_audit(store, api, api.libraries()[0])
    assert all(d["valid"] for d in store.field_decisions(new, "current-id").values())


def test_rejection_survives_changed_provider_metadata_and_import(setup):
    from abs_manager.verification_workflow import run_verification, start_verification
    store, api, aid = setup
    class Source:
        id, scope, targeted_only = "fixture", "public", False
        revision = 0
        def query(self, book):
            return {"title": book.values["title"]}
        def search(self, query):
            return {"candidates": [candidate({"id": "recording", "title": "Title" + str(self.revision),
                                               "publisher": "Publisher" + str(self.revision)}, self.id)]}
    source = Source()
    run_verification(store, start_verification(store, aid, source, ["current-id"]), source)
    cid = store.verification_report(aid)[0]["matches"][0]["candidate_id"]
    store.decide_candidate(aid, "current-id", cid, "rejected", "Not the owned edition")
    source.revision = 1
    new = run_audit(store, api, api.libraries()[0])
    run_verification(store, start_verification(store, new, source, ["current-id"], refresh=True), source)
    assert store.verification_report(new)[0]["matches"][0]["rejected"]


def test_changes_ui_requires_exact_confirmation_and_checkbox(setup, monkeypatch):
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": "Confirmed target"})
    at = app(store, api, monkeypatch)
    page(at, "Changes")
    widget(at, "checkbox", "Select title: Confirmed target").check().run()
    widget(at, "button", "Approve selected fields for ABS preview").click().run()
    assert not at.exception and not api.writes
    phrase = next(c.value for c in at.code if c.value.startswith("APPLY "))
    widget(at, "text_input", "Apply confirmation").set_value(phrase)
    widget(at, "button", "Apply confirmed changes").click().run()
    assert not api.writes
    widget(at, "checkbox", "I approve the exact fields shown and want to write this batch to Audiobookshelf.").check()
    widget(at, "button", "Apply confirmed changes").click().run()
    assert not at.exception and len(api.writes) == 1
    at.run()
    assert len(api.writes) == 1 and store.history()[0]["state"] == "verified"
    page(at, "History")
    rollback_phrase = next(c.value for c in at.code if c.value.startswith("ROLLBACK "))
    widget(at, "text_input", "Rollback confirmation").set_value(rollback_phrase)
    widget(at, "checkbox", "Restore the exact previous values shown above.").check()
    widget(at, "button", "Restore confirmed fields").click().run()
    assert not at.exception and len(api.writes) == 2
    assert any(r["state"] == "reverted" for r in store.history())


def test_expected_correction_does_not_hide_unexplained_edition_change(setup):
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": store.book(aid, "current-id").values["title"], "narrators": ["Correct narrator"]})
    fid = next(f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target")
    store.review([fid], "approved")
    plan = preview(store, aid, [fid])
    api.items["current-id"]["media"]["metadata"]["language"] = "fr"
    Workflow(store, api).apply(plan, confirmation(plan))
    new = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(new, "current-id")["title"]["decision"] == "reopened"
