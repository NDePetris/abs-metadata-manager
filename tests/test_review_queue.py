"""Queue membership derives exclusively from valid persisted human decisions."""
from conftest import FakeAdapter, make_book
from test_compact import two_books
from test_streamlined import app, page, widget

from abs_manager.models import review_fields
from abs_manager.store import Store
from abs_manager.workflow import run_audit


def accept(store, aid, iid, **targets):
    book = store.book(aid, iid)
    store.confirm_targets(aid, iid, {**{f: book.values[f] for f in review_fields(book.is_audio)}, **targets})


def test_queue_status_persistence_import_reopen_and_later(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    accept(store, aid, "current-id", subtitle="Pending correction")
    store.confirm_targets(aid, "second", {"title": "Z book"})
    store.decide_fields(aid, "second", ["subtitle"], "later", "Later")
    assert store.verification_report(aid)[0]["review_status"] == "Accepted"
    # A fresh connection and new valid import preserve human acceptance.
    store = Store(store.path)
    aid = run_audit(store, api, api.libraries()[0])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert widget(at, "selectbox", "Review status").value == "Needs review"
    assert widget(at, "selectbox", "Book").value == "second"
    assert widget(at, "button", "Next").disabled
    assert any("1 / 2 accepted · 1 remaining" in c.value for c in at.caption)
    widget(at, "text_input", "Desired subtitle").set_value("Unconfirmed").run()
    widget(at, "selectbox", "Review status").set_value("Accepted").run()
    assert widget(at, "selectbox", "Book").value == "current-id"
    assert widget(at, "text_input", "Desired subtitle").value == "Pending correction"
    widget(at, "selectbox", "Review status").set_value("All books").run()
    widget(at, "button", "Next").click().run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unconfirmed"
    widget(at, "selectbox", "Review status").set_value("Needs review").run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unconfirmed"
    store.decide_fields(aid, "current-id", ["title"], "reopened", "Recheck")
    at.run()
    widget(at, "button", "Previous").click().run()
    assert widget(at, "selectbox", "Book").value == "current-id"
    assert store.field_decisions(aid, "second")["subtitle"]["decision"] == "later"
    assert not api.writes


def test_confirm_next_skips_accepted_and_finishes_without_jump(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    accept(store, aid, "second")
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "text_input", "Desired subtitle").set_value("Draft correction").run()
    widget(at, "button", "Select all").click().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert not at.exception
    assert not [s for s in at.selectbox if s.label == "Book"]
    assert any("Review complete" in s.value for s in at.success)
    assert any("2 / 2 accepted · 0 remaining" in c.value for c in at.caption)
    widget(at, "button", "Show all books").click().run()
    assert widget(at, "selectbox", "Review status").value == "All books"
    assert widget(at, "selectbox", "Book").value == "current-id"
    assert widget(at, "text_input", "Desired subtitle").value == "Draft correction"
    assert store.corrections(aid)[0]["status"] == "pending" and not api.writes


def test_empty_accepted_and_partial_confirmation_remains_in_queue(tmp_path, monkeypatch):
    store, api, aid = two_books(tmp_path)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "selectbox", "Review status").set_value("Accepted").run()
    assert any("No accepted books" in i.value for i in at.info)
    widget(at, "button", "Show all books").click().run()
    widget(at, "selectbox", "Review status").set_value("Needs review").run()
    widget(at, "checkbox", "Title").check().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert widget(at, "selectbox", "Book").value == "second"
    widget(at, "button", "Previous").click().run()
    assert widget(at, "selectbox", "Book").value == "current-id"
    assert all(r["review_status"] != "Accepted" for r in store.verification_report(aid))
    assert not api.writes


def test_confirm_next_skips_middle_accepted_book(tmp_path, monkeypatch):
    api = FakeAdapter([make_book(title=title, item_id=iid, series=None)
                       for title, iid in (("A", "first"), ("B", "middle"), ("C", "last"))])
    store = Store(tmp_path / "queue.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    accept(store, aid, "middle")
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    before = store.field_decisions(aid, "middle")
    widget(at, "button", "Select all").click().run()
    widget(at, "button", "Confirm & Next").click().run()
    assert widget(at, "selectbox", "Book").value == "last"
    assert store.field_decisions(aid, "middle") == before and not api.writes


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
