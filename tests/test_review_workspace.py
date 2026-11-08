"""Bounded table and integrated UI coverage using two synthetic books."""
from hashlib import sha256

import pytest
from conftest import make_book
from streamlit.testing.v1 import AppTest
from test_review_import import encode, example_document, proposal, save
from test_streamlined import app, page, widget

from abs_manager.models import Book
from abs_manager.review_table import filter_rows, update_selection
from abs_manager.store import Store


def test_filters_sort_and_hidden_selection():
    rows = [{"id": i, "Book": title, "Author": author, "Field": field, "Status": "Needs review",
             "Evidence": "high", "Priority": i} for i, title, author, field in
            ((1, "Zebra", "Alice", "title"), (2, "Alpha", "Zoë", "description"))]
    assert [r["id"] for r in filter_rows(rows)] == [2, 1]
    assert [r["id"] for r in filter_rows(rows, sort="Author")] == [1, 2]
    assert [r["id"] for r in filter_rows(rows, search="ZOË", field="description", confidence="high")] == [2]
    assert not filter_rows(rows, status="Staged")
    assert update_selection({1}, [2, 3], [2, 3], {1, 2}) == {1, 2}
    assert update_selection({1, 2}, [2], [], {1, 2}) == {1}


def uploaded(tmp_path):
    store = Store(tmp_path / "upload.sqlite")
    raw = [make_book(title="Alpha", item_id="a"), make_book(title="Zebra", item_id="b")]
    aid = save(store, raw)
    doc = example_document(store, aid)
    a, b = [Book(r) for r in raw]
    doc["recommendations"] = [proposal(a, "title", "New Alpha"), proposal(b, "title", "New Zebra"),
                              proposal(a, "description", "Édition 世界\n\n" * 60, "editorial_review"),
                              proposal(a, "narrators", ["New voice"], "requires_recording_verification")]
    at = AppTest.from_string(f"""
from abs_manager.store import Store
from abs_manager.review_import_ui import import_corrections
store = Store({store.path!r})
import_corrections(store, store.audit({aid}))
""", default_timeout=20).run()
    data = encode(doc)
    at.file_uploader[0].upload("synthetic.json", data, "application/json").run()
    assert not at.exception
    return store, aid, doc, at, f"review_import:{aid}:{sha256(data).hexdigest()}"


def test_filtered_bulk_detail_edit_stage_and_reimport(tmp_path):
    store, aid, doc, at, key = uploaded(tmp_path)
    widget(at, "text_input", "Search corrections by title or author").set_value("Alpha").run()
    widget(at, "button", "Select eligible rows in view").click().run()
    assert at.session_state[key + ":selected_ids"] == ["a-title"]  # excludes description and held narrator
    widget(at, "text_input", "Search corrections by title or author").set_value("Zebra").run()
    widget(at, "button", "Select eligible rows in view").click().run()
    assert at.session_state[key + ":selected_ids"] == ["a-title", "b-title"]
    widget(at, "selectbox", "Sort by").select("Author").run()
    widget(at, "selectbox", "Recommendation details").select("a-description").run()
    assert widget(at, "text_area", "Proposed description").value == doc["recommendations"][2]["complete_desired_value"]
    exact = "Edited — 世界’s\n\nFull paragraph.\n"
    widget(at, "text_area", "Proposed description").set_value(exact)
    widget(at, "button", "Save proposed description").click().run()
    assert at.session_state[key + ":descriptions"]["a-description"] == exact
    assert at.session_state[key + ":selected_ids"] == ["a-title", "b-title"]
    assert not store.corrections(aid)
    widget(at, "button", "Stage selected changes locally (2)").click().run()
    assert not at.exception and not store.history()
    assert len(store.corrections(aid)) == 2
    assert all(f["status"] == "pending" for f in store.findings(aid) if f["kind"] == "confirmed_target")
    at.file_uploader[0].upload("synthetic.json", encode(doc), "application/json").run()
    assert at.session_state[key + ":selected_ids"] == []
    assert len(store.corrections(aid)) == 2


def test_checkbox_individual_description_selection(tmp_path):
    store, aid, doc, at, key = uploaded(tmp_path)
    widget(at, "selectbox", "Field").select("description").run()
    assert widget(at, "button", "Select eligible rows in view").disabled
    # Streamlit's editor widget state uses visible row indices; the durable selection uses proposal IDs.
    editor_key = at.dataframe[0].proto.id.split("-", 2)[2]
    at.session_state[editor_key] = {"edited_rows": {0: {"Select": True}}, "added_rows": [], "deleted_rows": []}
    at.run()
    assert at.session_state[key + ":selected_ids"] == ["a-description"]
    widget(at, "button", "Stage selected changes locally (1)").click().run()
    assert store.corrections(aid)[0]["proposed"] == doc["recommendations"][2]["complete_desired_value"]


def test_navigation_recovery_and_lazy_staged_approval(setup, monkeypatch):
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"description": "Complete 世界\n\n" * 60}, "Synthetic")
    store.record(api.server, "library-a", "other", "apply", "uncertain", {})
    api.item = lambda *_: pytest.fail("Rendering cannot fetch live metadata")
    with monkeypatch.context() as local:
        local.setattr(Store, "verification_report", lambda *_: pytest.fail("Assessment must load on request"))
        at = app(store, api, monkeypatch)
    assert not at.exception and not at.text_area
    assert any("Recovery required" in w.value for w in at.warning)
    widget(at, "button", "Select eligible rows in view").click().run()
    widget(at, "button", "Approve selected fields for ABS preview").click().run()
    assert not at.exception
    assert widget(at, "button", "Apply confirmed changes").disabled
    assert any("metadata" in j.value for j in at.json)
    for destination in ("Library", "Activity", "Settings", "Review"):
        page(at, destination)
    assert not api.writes


def test_manual_only_corrections_are_readonly(tmp_path, monkeypatch):
    from conftest import FakeAdapter
    raw = make_book(title="Ebook")
    raw["media"].update(audioFiles=[], duration=0)
    api = FakeAdapter([raw])
    store = Store(tmp_path / "manual.sqlite")
    aid = save(store, [raw])
    store.confirm_targets(aid, "current-id", {"title": "Calibre title"}, "Synthetic")
    at = app(store, api, monkeypatch)
    assert not at.exception
    assert widget(at, "button", "Approve selected fields for ABS preview").disabled
    assert not api.writes


def test_resolved_corrections_do_not_reopen_apply_panel(setup, monkeypatch):
    store, api, aid = setup
    store.confirm_targets(aid, "current-id", {"title": "Changed"}, "Synthetic")
    store.discard_correction(store.corrections(aid)[0]["id"])
    at = app(store, api, monkeypatch)
    assert not at.exception
    assert not widget(at, "checkbox", "Stage and apply").value
    assert not api.writes
