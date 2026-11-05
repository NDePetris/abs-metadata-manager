"""S3 synthetic quality, cover, hygiene and report batching regressions."""
import copy
import io

import pytest
from conftest import FakeAdapter
from PIL import Image
from test_persistent_state import snapshot
from test_quality_policy import clean, reference
from test_streamlined import app, page, widget
from test_verification import FakeSource

from abs_manager.adapter import AdapterError
from abs_manager.library_review import category_inventory
from abs_manager.store import Store
from abs_manager.verification_ui import quality_queue
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import run_audit


def image_bytes(color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (20, 30), color).save(output, "PNG")
    return output.getvalue()


def corroborate(store, aid, ids, partial=False):
    source = FakeSource()
    source.search = lambda q: {"state": "partial" if partial else "ok", "candidates": [reference()]}
    run_verification(store, start_verification(store, aid, source, ids), source)


def fixture(tmp_path, raws=None):
    api = FakeAdapter(raws or [clean()])
    api.cover = lambda iid: image_bytes()
    store = Store(tmp_path / "synthetic.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    api.reads.clear()
    return store, api, aid


def test_visible_automatic_pass_partial_progress_and_human_distinction(tmp_path, monkeypatch):
    store, api, aid = fixture(tmp_path)
    corroborate(store, aid, ["clean"])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    captions = " ".join(c.value for c in at.caption)
    assert "Pass · Independently verified; manual confirmation unnecessary" in captions
    assert "Source: Audiobookshelf snapshot, catalog" in captions
    assert "Essential bibliographic fields: 3 / 3 pass" in captions
    assert "Artwork: Needs evidence" in captions
    assert not store.field_decisions(aid, "clean")
    store.decide_fields(aid, "clean", ["title"], "confirmed", "Human judgment")
    at.run()
    assert any("Pass · Human confirmed" in c.value for c in at.caption)
    assert not api.reads and not api.writes


def test_fully_corroborated_book_leaves_queue_after_only_visual_inspection(tmp_path, monkeypatch):
    store, api, aid = fixture(tmp_path)
    corroborate(store, aid, ["clean"])
    _, observation = store.observe_cover(aid, "clean", api)
    store.confirm_cover(aid, "clean", observation["content_digest"])
    row = store.verification_report(aid)[0]
    assert row["disposition"] == "Pass" and row["review_status"] != "Accepted"
    assert set(store.field_decisions(aid, "clean")) == {"cover"}
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert any("No quality exceptions remain" in i.value for i in at.info)
    widget(at, "button", "Show all books").click().run()
    assert widget(at, "selectbox", "Book").value == "clean"
    store.confirm_targets(aid, "clean", {"subtitle": "Pending"})
    at.run()
    assert store.verification_report(aid)[0]["disposition"] == "Needs attention"
    assert any("Correction staged but not observed" in c.value for c in at.caption)
    assert not api.writes


@pytest.mark.parametrize("result", [None, b"not an image", b"", b"x" * 10_000_001])
def test_unavailable_invalid_and_oversized_images_do_not_pass(tmp_path, result):
    store, api, aid = fixture(tmp_path)
    api.cover = lambda iid: result
    if result is None:
        def fail(iid):
            raise AdapterError("Synthetic failure containing no credentials")
        api.cover = fail
    data, observation = store.observe_cover(aid, "clean", api)
    assert data is None and observation["state"] == "unavailable"
    assert store.verification_report(aid)[0]["fields"]["cover"]["reason_code"] == "cover_unavailable"
    with pytest.raises(ValueError, match="Retrieve and inspect"):
        store.confirm_cover(aid, "clean", "invented")


def test_missing_cover_cannot_be_confirmed_and_never_downloads(tmp_path):
    raw = clean()
    raw["media"]["coverPath"] = None
    store, api, aid = fixture(tmp_path, [raw])
    api.cover = lambda iid: pytest.fail("Missing saved path must not download")
    assert store.observe_cover(aid, "clean", api)[0] is None
    store.decide_fields(aid, "clean", ["cover"], "confirmed", "Legacy confirmation")
    assert store.verification_report(aid)[0]["fields"]["cover"]["reason_code"] == "required_missing"


def test_cover_decision_persists_and_new_content_failure_path_or_identity_reopens(tmp_path):
    store, api, aid = fixture(tmp_path)
    _, observed = store.observe_cover(aid, "clean", api)
    store.confirm_cover(aid, "clean", observed["content_digest"])
    new = run_audit(Store(store.path), api, api.libraries()[0])
    assert store.verification_report(new)[0]["fields"]["cover"]["reason_code"] == "human_confirmed"
    api.cover = lambda iid: image_bytes("red")
    _, changed = store.observe_cover(new, "clean", api)
    assert changed["content_digest"] != observed["content_digest"]
    assert store.verification_report(new)[0]["fields"]["cover"]["disposition"] == "Needs evidence"
    with pytest.raises(ValueError):
        store.confirm_cover(new, "clean", observed["content_digest"])
    store.confirm_cover(new, "clean", changed["content_digest"])
    api.items["clean"]["media"]["coverPath"] = "/new/cover.png"
    data, observation = store.observe_cover(new, "clean", api)
    assert data is None and observation["state"] == "unavailable"
    assert store.verification_report(new)[0]["fields"]["cover"]["disposition"] == "Needs attention"
    newer = run_audit(store, api, api.libraries()[0])
    assert store.verification_report(newer)[0]["fields"]["cover"]["disposition"] != "Pass"


def test_cover_gallery_is_bounded_explicit_and_rerenders_do_not_refetch(tmp_path, monkeypatch):
    store, api, aid = fixture(tmp_path, [clean("one"), clean("two")])
    calls = []
    api.cover = lambda iid: calls.append(iid) or image_bytes()
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    at.run()
    assert not calls and not api.reads
    widget(at, "multiselect", "Covers to retrieve (maximum 12)").set_value(["one", "two"]).run()
    widget(at, "button", "Retrieve selected covers").click().run()
    assert calls == ["one", "two"]
    at.run()
    assert calls == ["one", "two"] and not store.field_decisions(aid, "one")
    inspect = [c for c in at.checkbox if c.label.startswith("I inspected")]
    inspect[0].check()
    widget(at, "button", "Confirm inspected covers").click().run()
    assert not at.exception
    report = {r["item_id"]: r for r in store.verification_report(aid)}
    assert report["one"]["fields"]["cover"]["reason_code"] == "human_confirmed"
    assert report["two"]["fields"]["cover"]["disposition"] != "Pass"
    assert calls == ["one", "two"] and not api.writes


def test_hygiene_distinct_values_duplicates_variants_and_exact_library_scope(tmp_path):
    raws = [clean("a"), clean("b", False), clean("c")]
    raws[0]["media"]["metadata"]["genres"] = ["Science Fiction", "Science Fiction"]
    raws[1]["media"]["metadata"]["genres"] = ["science fiction"]
    for raw in raws:
        raw["media"]["tags"] = ["keep", "unwanted"]
    store, api, aid = fixture(tmp_path, raws)
    store.decide_category(aid, "tags", "keep", "Acceptable")
    store.decide_category(aid, "tags", "unwanted", "Unwanted")
    report = store.verification_report(aid)
    inventory = category_inventory(report, store.category_preferences(aid))
    genre = next(r for r in inventory if r["value"] == "Science Fiction")
    assert genre["duplicates"] == ["a"] and genre["variants"] == ["science fiction"]
    unwanted = next(r for r in inventory if r["value"] == "unwanted")
    assert unwanted["books"] == ["a", "b", "c"] and unwanted["count"] == 3
    assert all(r["fields"]["tags"]["reason_code"] == "category_problem" for r in report)
    # Removing an unwanted value does not constitute a bibliographic verification decision.
    for raw in api.items.values():
        raw["media"]["tags"] = ["keep"]
    new = run_audit(store, api, api.libraries()[0])
    assert all(r["fields"]["tags"]["reason_code"] == "category_accepted" for r in store.verification_report(new))
    other = copy.deepcopy(raws[0])
    other["libraryId"] = "other"
    other_aid = store.start_audit(api.server, "other", "Other")
    from abs_manager.models import Book
    store.add_book(other_aid, Book(other))
    store.finish_audit(other_aid)
    assert not store.category_preferences(other_aid)
    assert not api.writes


def test_optional_empty_categories_and_prefs_never_override_bibliography(tmp_path):
    store, _, aid = fixture(tmp_path)
    report = store.verification_report(aid)
    assert not category_inventory(report)
    assert all(report[0]["fields"][f]["reason_code"] == "optional_absent" for f in ("genres", "tags"))
    with pytest.raises(ValueError):
        store.decide_category(aid, "title", "Example", "Acceptable")


def test_queue_groups_books_prioritizes_essential_filters_and_no_side_effects(tmp_path):
    raw = clean("missing")
    raw["media"]["metadata"]["title"] = ""
    store, api, aid = fixture(tmp_path, [clean("correct"), raw, clean("partial")])
    corroborate(store, aid, ["correct"])
    corroborate(store, aid, ["partial"], partial=True)
    report = store.verification_report(aid)
    assert quality_queue(report)[0]["item_id"] == "missing"
    assert len({r["item_id"] for r in quality_queue(report)}) == 3
    assert [r["item_id"] for r in quality_queue(report, field="title", reason="required_missing")] == ["missing"]
    assert not quality_queue(report, search="not a title")
    assert len(quality_queue(report, "All books", "Author One")) == 3
    assert not api.writes and not store.field_decisions(aid, "correct")


def test_batched_report_matches_unbatched_decision_and_evidence_apis(tmp_path):
    store, _, aid = fixture(tmp_path)
    corroborate(store, aid, ["clean"])
    store.decide_fields(aid, "clean", ["title"], "confirmed", "Checked")
    first = store.verification_report(aid)[0]
    assert first["human_decisions"] == store.field_decisions(aid, "clean")
    assert first["lookups"] == store.verification_evidence(aid, "clean")
    assert first["candidate_decisions"] == store.candidate_decisions(aid, "clean")
    assert first == store.verification_report(aid)[0]  # No persisted input mutation/cache staleness.


def test_report_query_count_does_not_scale_with_book_count(tmp_path):
    store = Store(tmp_path / "db")
    aid = snapshot(store, [clean(str(i)) for i in range(50)])
    original, queries = store.rows, []
    def counted(query, args=()):
        queries.append(query)
        return original(query, args)
    store.rows = counted
    assert len(store.verification_report(aid)) == 50
    assert len(queries) <= 15


def test_category_preference_does_not_silence_explicit_individual_review_later(tmp_path):
    raw = clean()
    raw["media"]["tags"] = ["keep"]
    store, _, aid = fixture(tmp_path, [raw])
    store.decide_fields(aid, "clean", ["tags"], "later", "Investigate this book")
    store.decide_category(aid, "tags", "keep", "Acceptable")
    assert store.verification_report(aid)[0]["fields"]["tags"]["reason_code"] == "human_unresolved"


@pytest.mark.parametrize("value", ["not an array", [{"name": "unexpected"}], [4]])
def test_unsupported_abs_category_shapes_are_not_silently_inventoried(tmp_path, value):
    raw = clean()
    raw["media"]["tags"] = value
    store, _, aid = fixture(tmp_path, [raw])
    with pytest.raises(ValueError, match="expected an array of strings"):
        store.verification_report(aid)


def test_new_gallery_content_requires_new_inspection_checkbox(tmp_path, monkeypatch):
    store, api, _ = fixture(tmp_path)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "multiselect", "Covers to retrieve (maximum 12)").set_value(["clean"]).run()
    widget(at, "button", "Retrieve selected covers").click().run()
    inspection = next(c for c in at.checkbox if c.label.startswith("I inspected"))
    inspection.check().run()
    api.cover = lambda iid: image_bytes("red")
    widget(at, "button", "Retrieve selected covers").click().run()
    assert not next(c for c in at.checkbox if c.label.startswith("I inspected")).value


def test_category_preference_form_persists_without_network_on_rerender(tmp_path, monkeypatch):
    raw = clean()
    raw["media"]["metadata"]["genres"] = ["Science Fiction"]
    store, api, aid = fixture(tmp_path, [raw])
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert widget(at, "selectbox", "Library preference").value == "Unreviewed"
    widget(at, "selectbox", "Library preference").set_value("Acceptable")
    widget(at, "button", "Save category preference").click().run()
    assert store.verification_report(aid)[0]["fields"]["genres"]["reason_code"] == "category_accepted"
    at.run()
    assert widget(at, "selectbox", "Library preference").value == "Acceptable"
    assert not api.reads and not api.writes
