"""Exercise explicit local decisions across the actual Streamlit workflow."""

import copy
from pathlib import Path

from conftest import FakeAdapter, make_book
from streamlit.testing.v1 import AppTest

from abs_manager.calibre import import_catalog
from abs_manager.models import FIELDS
from abs_manager.store import Store
from abs_manager.verification import QUALITY_FIELDS, candidate
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import preview, run_audit

APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


def page(at, name):
    widget(at, "radio", "Workspace").set_value(name).run()
    assert not at.exception


def app(store, api, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", store.path)
    at = AppTest.from_file(str(APP), default_timeout=45)
    at.session_state["adapter"] = api
    at.session_state["library"] = api.libraries()[0]
    return at.run()


def test_accept_individual_fields_reimport_reopen_and_no_writes(tmp_path, monkeypatch):
    store = Store(tmp_path / "ui.sqlite")
    api = FakeAdapter([make_book(title="My book", series=None)])
    aid = run_audit(store, api, api.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Resolve")
    widget(at, "multiselect", "Fields reviewed").set_value(list(QUALITY_FIELDS))
    widget(at, "text_area", "Decision evidence").set_value("Compared the owned recording and its credits")
    widget(at, "button", "Save persistent field decisions").click().run()
    assert not at.exception
    assert any("No books need attention" in i.value for i in at.info)
    assert not preview(store, aid)["books"]
    next_aid = run_audit(store, api, api.libraries()[0])
    assert store.import_summary(next_aid)["unresolved"] == 0
    at.session_state["snapshot_choice"] = next_aid
    at.run()
    assert any("No books need attention" in i.value for i in at.info)
    widget(at, "selectbox", "Review queue").set_value("Accepted").run()
    assert widget(at, "selectbox", "Resolve book").value == "current-id"
    assert all(
        d["decision"] == "confirmed" and d["valid"]
        for d in store.field_decisions(next_aid, "current-id").values()
    )
    api.items["current-id"]["media"]["metadata"]["title"] = "A changed title"
    last_aid = run_audit(store, api, api.libraries()[0])
    assert store.import_summary(last_aid)["unresolved"] == 1
    at.session_state["snapshot_choice"] = last_aid
    widget(at, "selectbox", "Review queue").set_value("Outstanding").run()
    assert widget(at, "selectbox", "Resolve book").value == "current-id"
    decisions = store.field_decisions(last_aid, "current-id")
    assert decisions["title"]["decision"] == "reopened"
    assert decisions["authors"]["decision"] == "confirmed"
    assert not api.writes


class CatalogSource:
    id, scope, targeted_only = "fixture", "public", False

    def query(self, book):
        return {"title": book.values["title"]}

    def search(self, query):
        return {
            "raw": [],
            "candidates": [
                candidate(
                    {
                        "title": query["title"],
                        "authors": ["Dennis E. Taylor"],
                        "narrators": ["Other narrator"],
                        "asin": "B000000001",
                    },
                    self.id,
                )
            ],
        }


def test_reject_candidate_and_manual_missing_edition_survive_import(tmp_path, monkeypatch):
    store = Store(tmp_path / "ui.sqlite")
    api = FakeAdapter([make_book()])
    aid = run_audit(store, api, api.libraries()[0])
    source = CatalogSource()
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "text_input", "Edition selection evidence").set_value(
        "This recording has a different narrator"
    )
    widget(at, "button", "Not my edition").click().run()
    assert not at.exception
    assert any(d["decision"] == "rejected" for d in store.candidate_decisions(aid, "current-id").values())
    widget(at, "text_input", "Edition reference / identifier").set_value("My publisher's CD release")
    widget(at, "text_area", "Manual recording evidence").set_value(
        "Ray Porter; unabridged; printed recording credits"
    )
    widget(at, "button", "Save manual edition association").click().run()
    assert not at.exception
    next_aid = run_audit(store, api, api.libraries()[0])
    report = store.verification_report(next_aid)[0]
    assert report["matches"][0]["rejected"]
    assert {d["decision"] for d in store.candidate_decisions(next_aid, "current-id").values()} == {
        "rejected",
        "absent",
    }
    assert not preview(store, next_aid)["books"] and not api.writes


def test_ebook_source_queue_requires_catalog_then_abs_import(tmp_path, monkeypatch):
    raw = make_book(title="Old title", path="Author/Title (12)", series=None)
    raw["media"].update(duration=0, audioFiles=[])
    api = FakeAdapter([raw])
    store = Store(tmp_path / "ui.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Resolve")
    widget(at, "selectbox", "Correction field").set_value("title")
    widget(at, "text_area", "Proposed source value").set_value("Correct title")
    widget(at, "text_area", "Correction evidence / manual action").set_value("Read the publisher title page")
    widget(at, "text_input", "Calibre ID (optional)").set_value("12")
    widget(at, "button", "Prepare source correction").click().run()
    assert not at.exception
    page(at, "Update Sources")
    widget(at, "button", "Mark edited in Calibre").click().run()
    assert not at.exception
    row = store.corrections(aid)[0]
    assert row["status"] == "edited_in_calibre" and not row["calibre_verified"]
    import_catalog(store, aid, '[{"id":12,"title":"Correct title","authors":["Dennis E. Taylor"]}]')
    at.run()
    assert store.corrections(aid)[0]["calibre_verified"]
    assert not store.corrections(aid)[0]["abs_observed"]
    updated = copy.deepcopy(raw)
    updated["media"]["metadata"]["title"] = "Correct title"
    api.items["current-id"] = updated
    next_aid = run_audit(store, api, api.libraries()[0])
    assert store.corrections(next_aid)[0]["abs_observed"]
    assert not api.writes
    assert not any(f["status"] == "approved" for f in store.findings(next_aid) if f["field"] in FIELDS)
