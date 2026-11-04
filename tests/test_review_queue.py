"""Quality queue membership remains separate from historical manual acceptance."""
from conftest import FakeAdapter, make_book
from test_compact import two_books
from test_streamlined import app, page, widget

from abs_manager.models import review_fields
from abs_manager.workflow import run_audit


def accept(store, aid, iid, **targets):
    book = store.book(aid, iid)
    store.confirm_targets(aid, iid, {**{f: book.values[f] for f in review_fields(book.is_audio)}, **targets})


def test_staged_correction_stays_in_quality_queue_after_manual_acceptance(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    accept(store, aid, "current-id", subtitle="Pending correction")
    assert store.verification_report(aid)[0]["review_status"] == "Accepted"
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert widget(at, "selectbox", "Quality").value == "Exceptions"
    assert len(widget(at, "selectbox", "Book").options) == 2
    widget(at, "selectbox", "Book").set_value("current-id").run()
    assert widget(at, "text_input", "Desired subtitle").value == "Pending correction"
    assert any("Historical manual acceptance: 1 / 2" in c.value for c in at.caption)
    assert any("Correction staged but not observed" in c.value for c in at.caption)
    assert not api.writes


def test_accepting_editable_fields_does_not_claim_full_book_completion(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    accept(store, aid, "second")
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "selectbox", "Book").set_value("current-id").run()
    widget(at, "text_input", "Desired subtitle").set_value("Draft correction").run()
    widget(at, "button", "Select all").click().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert not at.exception
    assert widget(at, "selectbox", "Book").value == "second"
    assert not any("Review complete" in s.value for s in at.success)
    assert any("Historical manual acceptance: 2 / 2" in c.value for c in at.caption)
    assert all(r["disposition"] != "Pass" for r in store.verification_report(aid))
    widget(at, "selectbox", "Book").set_value("current-id").run()
    assert widget(at, "text_input", "Desired subtitle").value == "Draft correction"
    assert not api.writes


def test_empty_pass_filter_and_partial_confirmation_remains_in_queue(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "selectbox", "Quality").set_value("Pass").run()
    assert any("No books match" in i.value for i in at.info)
    widget(at, "button", "Show all books").click().run()
    assert widget(at, "selectbox", "Quality").value == "All books"
    widget(at, "checkbox", "Title").check().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert widget(at, "selectbox", "Book").value == "second"
    widget(at, "button", "Previous").click().run()
    assert widget(at, "selectbox", "Book").value == "current-id"
    assert all(r["review_status"] != "Accepted" for r in store.verification_report(aid))
    assert not api.writes


def test_generic_or_discarded_correction_does_not_complete_review(tmp_path):
    store, _api, aid = two_books(tmp_path)
    accept(store, aid, "current-id", subtitle="Target")
    assert store.verification_report(aid)[0]["review_status"] == "Accepted"
    store.discard_correction(store.corrections(aid)[0]["id"])
    assert store.verification_report(aid)[0]["review_status"] != "Accepted"
    accept(store, aid, "second")
    store.decide_fields(aid, "second", ["subtitle"], "correction_needed", "No confirmed target")
    assert store.verification_report(aid)[1]["review_status"] != "Accepted"


def test_review_acceptance_is_scoped_to_library(tmp_path):
    store, api, aid = two_books(tmp_path)
    accept(store, aid, "current-id")
    other_raw = make_book(title="Title", series=None)
    other_raw["libraryId"] = "other"
    other = FakeAdapter([other_raw])
    other.libraries = lambda: [{"id": "other", "name": "Other", "mediaType": "book"}]
    other_aid = run_audit(store, other, other.libraries()[0])
    assert store.verification_report(other_aid)[0]["review_status"] != "Accepted"
    assert not store.field_decisions(other_aid, "current-id")
    assert store.verification_report(aid)[0]["review_status"] == "Accepted"
    assert not api.writes and not other.writes
