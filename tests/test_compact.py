"""Synthetic regressions for compact review, identity and credential recovery."""

import pytest
from conftest import FakeAdapter, make_book
from test_hardening import ebook_store
from test_streamlined import app, page, widget

from abs_manager.models import review_fields
from abs_manager.store import Store
from abs_manager.verification import can_copy_reference, candidate, reference_values
from abs_manager.verification_ui import draft_scope
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import preview, run_audit


class Reference:
    id, scope, targeted_only = "fixture", "public", False

    def __init__(self, raw=None, work_only=False):
        self.raw = raw or {"id": "recording", "title": "Title", "author": ["Dennis E. Taylor"],
                           "narrator": ["Ray Porter"], "duration": 1000}
        self.work_only = work_only
        self.calls = 0

    def query(self, book):
        return {"title": book.values["title"]}

    def search(self, query):
        self.calls += 1
        return {"candidates": [candidate(self.raw, self.id, work_only=self.work_only)]}


def add_reference(store, aid, source, iid="current-id"):
    run_verification(store, start_verification(store, aid, source, [iid], refresh=True), source)


def two_books(tmp_path):
    api = FakeAdapter([make_book(title="Title", series=None), make_book(title="Z book", item_id="second", series=None)])
    store = Store(tmp_path / "ui.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    return store, api, aid


def copy_button(at, field):
    return next(b for b in at.button if b.key and b.key.endswith(":copy:" + field))


def test_reference_copy_switch_navigation_preserves_drafts(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    source = Reference()
    add_reference(store, aid, source)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "selectbox", "Quality").set_value("All books").run()
    widget(at, "selectbox", "Book").set_value("current-id").run()
    assert not any(c.value for c in at.checkbox if c.label in [f.capitalize() for f in review_fields(True)])
    widget(at, "text_input", "Desired subtitle").set_value("Unsaved subtitle").run()
    copy_button(at, "title").click().run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved subtitle"
    assert widget(at, "checkbox", "Title").value
    assert not store.corrections(aid) and not store.field_decisions(aid, "current-id")
    source.id = "another-provider"
    add_reference(store, aid, source)
    at.run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved subtitle"
    widget(at, "button", "Next").click().run()
    assert widget(at, "text_input", "Desired subtitle").value == ""
    widget(at, "text_input", "Desired title").set_value("Second draft").run()
    widget(at, "button", "Previous").click().run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved subtitle"
    page(at, "Settings")
    page(at, "Match & Verify")
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved subtitle"
    assert source.calls == 2 and not api.writes and not preview(store, aid)["books"]


def test_confirmation_is_selected_atomic_and_next_only_on_success(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "selectbox", "Quality").set_value("All books").run()
    assert widget(at, "button", "Confirm").disabled
    widget(at, "text_input", "Desired title").set_value("").run()
    widget(at, "text_input", "Desired subtitle").set_value("Keep after failure").run()
    widget(at, "button", "Confirm & Next").click().run()
    assert at.error and widget(at, "selectbox", "Book").value == "current-id"
    assert widget(at, "text_input", "Desired title").value == ""
    assert widget(at, "text_input", "Desired subtitle").value == "Keep after failure"
    assert widget(at, "checkbox", "Title").value and widget(at, "checkbox", "Subtitle").value
    assert not store.corrections(aid) and not store.field_decisions(aid, "current-id")
    widget(at, "text_input", "Desired title").set_value("Title").run()
    widget(at, "checkbox", "Subtitle").uncheck().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert widget(at, "selectbox", "Book").value == "second"
    decisions = store.field_decisions(aid, "current-id")
    assert set(decisions) == {"title"} and decisions["title"]["decision"] == "confirmed"
    widget(at, "button", "Previous").click().run()
    assert widget(at, "text_input", "Desired subtitle").value == "Keep after failure"
    widget(at, "checkbox", "Subtitle").check().run()
    widget(at, "button", "Confirm").click().run()
    row = store.corrections(aid)[0]
    assert row["proposed"] == "Keep after failure" and row["accepted"]
    widget(at, "checkbox", "Subtitle").check().run()
    widget(at, "button", "Confirm").click().run()
    assert len([r for r in store.corrections(aid) if r["status"] == "pending"]) == 1
    assert not preview(store, aid)["books"] and not api.writes


def test_snapshot_drafts_expire_and_pending_target_is_restored(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    store.confirm_targets(aid, "current-id", {"subtitle": "Accepted pending"})
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert widget(at, "text_input", "Desired subtitle").value == "Accepted pending"
    widget(at, "text_input", "Desired subtitle").set_value("Unsaved only").run()
    run_audit(store, api, api.libraries()[0])
    at.run()
    assert widget(at, "text_input", "Desired subtitle").value == "Accepted pending"
    assert not widget(at, "checkbox", "Subtitle").value and not api.writes


def test_global_library_isolation(tmp_path, monkeypatch):
    store, api, _aid = two_books(tmp_path)
    raw = make_book(title="Title", series=None)
    raw["libraryId"] = "library-b"
    other = FakeAdapter([raw])
    other.libraries = lambda: [{"id": "library-b", "name": "Ebooks", "mediaType": "book"}]
    run_audit(store, other, other.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "text_input", "Desired title").set_value("Library A draft").run()
    widget(at, "selectbox", "Library").set_value("library-b").run()
    assert widget(at, "text_input", "Desired title").value == "Title"
    assert widget(at, "button", "Confirm").disabled
    page(at, "Changes")
    assert not [c for c in at.checkbox if c.label.startswith("Select ")]
    page(at, "Settings")
    assert at.session_state["library"]["id"] == "library-b"
    page(at, "Match & Verify")
    widget(at, "selectbox", "Library").set_value("library-a").run()
    assert widget(at, "text_input", "Desired title").value == "Library A draft"
    assert not api.writes


def test_scope_includes_server_library_snapshot_item():
    audit = {"server": "one", "library": "one", "id": 1}
    item = {"item_id": "one"}
    original = draft_scope(audit, item)
    assert all(draft_scope({**audit, k: v}, item) != original for k, v in
               (("server", "two"), ("library", "two"), ("id", 2)))
    assert draft_scope(audit, {"item_id": "two"}) != original


def test_missing_and_work_only_reference_cannot_clear_or_verify_recording(setup):
    store, _api, aid = setup
    source = Reference({"id": "work", "title": "Heaven's River: Bobiverse, Book 4", "author": ["Dennis E. Taylor"]}, True)
    add_reference(store, aid, source)
    item = store.verification_report(aid)[0]
    match = item["matches"][0]
    assert "narrators" not in reference_values(match) and "series" not in reference_values(match)
    assert not can_copy_reference(item, match, "narrators")
    assert can_copy_reference(item, match, "title")
    store.decide_candidate(aid, "current-id", match["candidate_id"], "selected", "Book-level only")
    item = store.verification_report(aid)[0]
    assert item["fields"]["narrators"]["status"] != "Verified"
    assert not can_copy_reference(item, match, "narrators")
    explicit = candidate({"title": "Title", "subtitle": "", "series": []}, "fixture")
    m = {"candidate": explicit}
    assert reference_values(m)["subtitle"] == "" and reference_values(m)["series"] == []


def test_ebook_applicability_shared_ui_and_confirmation(tmp_path, monkeypatch):
    store, api, aid = ebook_store(tmp_path)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert not any(c.label == "Narrators" for c in at.checkbox)
    widget(at, "button", "Select all").click().run()
    widget(at, "button", "Confirm").click().run()
    assert set(store.field_decisions(aid, "current-id")) == set(review_fields(False))
    with pytest.raises(ValueError, match="applicable"):
        store.confirm_targets(aid, "current-id", {"narrators": ["Nobody"]})
    assert not api.writes




def test_legacy_and_nullable_references_never_infer_empty_values():
    legacy = candidate({"title": "Title", "authors": ["Author"]}, "googlebooks", work_only=True)
    legacy.pop("supplied")
    legacy["raw"] = {"volumeInfo": {"title": "Title", "authors": ["Author"], "subtitle": ""}}
    # Older captures have normalized defaults, but the provider omitted these fields.
    values = reference_values({"candidate": legacy})
    assert values == {"title": "Title", "authors": ["Author"]}
    assert "identifiers" not in values and "narrators" not in values and "series" not in values
    nullable = candidate({"title": "Title", "authors": None, "narrators": None, "isbn": None}, "fixture")
    match = {"candidate": nullable, "classification": "correct_recording", "level": "recording",
             "candidate_id": nullable["id"]}
    item = {"type": "ebook", "selected_candidate": None}
    assert reference_values(match)["authors"] is None
    assert not can_copy_reference(item, match, "authors")
    assert not can_copy_reference(item, match, "identifiers")
    assert nullable["values"]["authors"] == []  # Display logic leaves captured normalized evidence alone.
    omitted = candidate({"title": "Title", "author": []}, "openlibrary", work_only=True)
    omitted["raw"] = {"title": "Title", "key": "/works/synthetic"}
    assert "authors" not in reference_values({"candidate": omitted})


def test_reference_identifier_copy_merges_an_unsaved_other_identifier(tmp_path, monkeypatch):
    store, api, aid = ebook_store(tmp_path)
    source = Reference({"id": "book", "title": "Title", "author": ["Dennis E. Taylor"],
                        "isbn": "9780306406157"}, True)
    add_reference(store, aid, source)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "text_input", "Desired asin").set_value("UNSAVEDASIN")
    copy_button(at, "identifiers").click().run()
    assert widget(at, "text_input", "Desired isbn").value == "9780306406157"
    assert widget(at, "text_input", "Desired asin").value == "UNSAVEDASIN"
    assert widget(at, "checkbox", "Identifiers").value
    assert not store.corrections(aid) and not store.field_decisions(aid, "current-id") and not api.writes


def test_multiple_structured_edits_in_one_rerun_are_all_retained(tmp_path, monkeypatch):
    api = FakeAdapter([make_book(title="Title", authors=["First, Author", "Second Author"], seq="2.5")])
    store = Store(tmp_path / "structured.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "text_input", "Desired authors 1").set_value("Edited, First")
    widget(at, "text_input", "Desired authors 2").set_value("Edited Second")
    widget(at, "text_input", "Series name").set_value("Edited series")
    widget(at, "text_input", "Series position").set_value("12.5")
    at.run()
    assert widget(at, "text_input", "Desired authors 1").value == "Edited, First"
    assert widget(at, "text_input", "Desired authors 2").value == "Edited Second"
    assert widget(at, "text_input", "Series name").value == "Edited series"
    assert widget(at, "text_input", "Series position").value == "12.5"
    widget(at, "button", "Confirm").click().run()
    targets = {r["field"]: r["proposed"] for r in store.corrections(aid)}
    assert targets == {"authors": ["Edited, First", "Edited Second"],
                       "series": [{"name": "Edited series", "sequence": "12.5"}]}
    assert not preview(store, aid)["books"] and not api.writes
