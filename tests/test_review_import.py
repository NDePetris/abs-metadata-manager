import copy
import json
from collections import Counter

import pytest
from conftest import FakeAdapter, make_book

from abs_manager.models import Book, digest
from abs_manager.persistent_state import identity
from abs_manager.review_import import (
    ALREADY_APPLIED,
    ALREADY_STAGED,
    DOCUMENT_TYPE,
    DOCUMENT_VERSION,
    EXISTING,
    HELD,
    READY,
    check_live,
    parse_review,
    review_rows,
    stage_review,
)
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, confirmation, preview


def save(store, raw):
    aid = store.start_audit(FakeAdapter.server, "library-a", "Synthetic")
    for item in raw:
        store.add_book(aid, Book(item))
    store.finish_audit(aid)
    return aid


def example_document(store, aid):
    audit = store.audit(aid)
    return {
        "document_type": DOCUMENT_TYPE, "document_version": DOCUMENT_VERSION, "import_ready": False,
        "source_export": {"application": "abs-metadata-manager", "schema_version": 1, "snapshot_id": aid,
                          "library_id": audit["library"], "content_digest": digest("synthetic bundle"),
                          "library_scope_fingerprint": digest([audit["server"], audit["library"]]),
                          "snapshot_started_at": audit["started"], "snapshot_completed_at": audit["finished"],
                          "book_count": len(store.books(aid)), "offline_import": False},
        "genre_policy": {"allow_multiple_genres": True, "must_include_broader_genre": True,
                         "genre_parent_rules": {"Fantasy Romance": ["Fantasy", "Romance"]},
                         "exclude_media_format_as_genre": ["Audiobook"],
                         "controlled_genres": ["Fantasy", "Romance", "Fantasy Romance", "Audiobook"]},
        "application_safety": {},
        "recommendations": [],
        "existing_staged_corrections": [],
    }


def proposal(book, field, desired, status="approved_recommendation"):
    native = {"publication_year": "publishedYear", "genres": "genres", "description": "description"}
    return {
        "proposal_id": f"{book.id}-{field}", "item_id": book.id, "media_type": "audiobook",
        "identity_digest": identity(book), "managed_values_digest": digest(book.values), "field": field,
        "expected_original_value": book.values.get(field, book.metadata.get(native.get(field))),
        "complete_desired_value": desired, "status": status, "confidence": "high",
        "write_path": "audiobook_guarded_writer", "rationale": "Synthetic owner review", "evidence": [{}],
    }


def encode(document):
    return json.dumps(document).encode()


@pytest.fixture
def pilot(tmp_path):
    store = Store(tmp_path / "review.sqlite3")
    raw = [make_book(title=f"Example {i}", item_id=f"book-{i}", series="Example series", seq="2.50")
           for i in range(110)]
    aid = save(store, raw)
    doc = example_document(store, aid)
    books = store.books(aid)
    changes = [
        ("genres", 108, ["Fantasy", "Romance", "Fantasy Romance"]),
        ("description", 55, "Complete description"),
        ("title", 11, "Edited title"),
        ("subtitle", 9, "Edited subtitle"),
        ("narrators", 7, ["New narrator"]),
        ("series", 7, [{"name": "Example series", "sequence": "2.50"}, {"name": "Other", "sequence": "0.5"}]),
        ("publication_year", 6, "2026"),
        ("authors", 3, ["New author", "Second author"]),
    ]
    for field, count, desired in changes:
        for i, book in enumerate(books[:count]):
            status = ("editorial_review" if field == "description" else
                      "requires_recording_verification" if field == "narrators" and i < 3 else
                      "approved_recommendation")
            doc["recommendations"].append(proposal(book, field, desired, status))
    for book in books[-6:]:
        p = proposal(book, "title", "Prior title")
        store.confirm_targets(aid, book.id, {"title": "Prior title"})
        doc["existing_staged_corrections"].append({
            **p, "status": EXISTING, "already_staged_desired_value": p["complete_desired_value"],
        })
    return store, aid, doc


def test_representative_pilot(pilot):
    store, aid, raw = pilot
    document = parse_review(encode(raw))
    rows = review_rows(store, aid, document)
    assert Counter(r.proposal.status for r in rows) == {
        "approved_recommendation": 148, "editorial_review": 55, "requires_recording_verification": 3, EXISTING: 6,
    }
    assert Counter((r.capability, r.disposition) for r in rows) == {
        ("Guarded ABS", READY): 34, ("Guarded ABS", HELD): 3, ("Guarded ABS", ALREADY_STAGED): 6,
        ("Native ABS", "Native edit outstanding"): 114, ("Native ABS", "Editorial review outstanding"): 55,
    }
    assert next(r for r in rows if r.proposal.field == "series").proposal.desired == [
        {"name": "Example series", "sequence": "2.50"}, {"name": "Other", "sequence": "0.5"},
    ]
    assert all(r.disposition != READY for r in rows if r.proposal.field == "genres")


@pytest.fixture
def single(setup):
    store, adapter, aid = setup
    doc = example_document(store, aid)
    doc["recommendations"] = [proposal(store.books(aid)[0], "title", "Changed title")]
    return store, adapter, aid, doc


@pytest.mark.parametrize("field,value", [
    ("title", " "), ("title", []), ("path", "/bad"), ("__proto__", {}), ("authors", [{"name": "A"}]),
    ("authors", []), ("narrators", ["A", "A"]), ("series", [{"name": "A", "sequence": "1e999999"}]),
    ("series", [{"name": "A", "sequence": "2.5", "id": "x"}]), ("genres", ["Fantasy Romance"]),
    ("genres", ["Audiobook"]), ("genres", ["Fantasy", "Fantasy"]), ("description", {"$ref": "/private"}),
    ("publication_year", True),
])
def test_invalid_targets(single, field, value):
    store, _, aid, doc = single
    doc["recommendations"] = [proposal(store.books(aid)[0], field, value)]
    with pytest.raises(ValueError, match="entry 1"):
        parse_review(encode(doc))


def test_invalid_document(single):
    _, _, _, doc = single
    for payload in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":"\\ud800"}', b"\xff", b"[1]", b"x" * (5 * 1024**2 + 1)):
        with pytest.raises(ValueError):
            parse_review(payload)
    for patch in ({"document_version": 2}, {"document_type": "other"}, {"import_ready": True},
                  {"recommendations": {}}, {"existing_staged_corrections": None}, {"application_safety": []}):
        with pytest.raises(ValueError):
            parse_review(encode({**doc, **patch}))


@pytest.mark.parametrize("patch,message", [
    ({"item_id": "missing"}, "Book missing from source snapshot"),
    ({"identity_digest": digest("wrong")}, "Source item identity mismatch"),
    ({"managed_values_digest": digest("wrong")}, "Source metadata differs from proposal"),
    ({"expected_original_value": "Stale"}, "Source metadata differs from proposal"),
    ({"media_type": "ebook"}, "Source item identity mismatch"),
])
def test_record_binding(single, patch, message):
    store, _, aid, doc = single
    doc["recommendations"][0].update(patch)
    assert review_rows(store, aid, parse_review(encode(doc)))[0].disposition == message


def test_provenance_binding(single):
    store, _, aid, doc = single
    for patch in ({"library_id": "other"}, {"library_scope_fingerprint": digest("wrong")},
                  {"snapshot_id": aid + 1}, {"snapshot_completed_at": "wrong"}, {"book_count": 99}):
        invalid = {**doc, "source_export": {**doc["source_export"], **patch}}
        with pytest.raises(ValueError):
            review_rows(store, aid, parse_review(encode(invalid)))


@pytest.mark.parametrize("target", ["Changed title", "Contradictory title"])
def test_duplicate_keys_block_both(single, target):
    store, _, aid, doc = single
    doc["recommendations"].append({**doc["recommendations"][0], "proposal_id": "different",
                                   "complete_desired_value": target})
    assert {r.disposition for r in review_rows(store, aid, parse_review(encode(doc)))} == {
        "Duplicate or conflicting proposal",
    }


def test_conflicts_do_not_overwrite_approval_and_holds_require_confirmation(single):
    store, api, aid, doc = single
    book = store.books(aid)[0]
    store.confirm_targets(aid, book.id, {"title": "Existing target"})
    fid = next(f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target")
    store.review([fid], "approved")
    hold = proposal(book, "narrators", ["New voice"], "requires_recording_verification")
    native = proposal(book, "description", "Long description", "editorial_review")
    doc["recommendations"] += [hold, native]
    document = parse_review(encode(doc))
    before = preview(store, aid)
    assert [r.disposition for r in review_rows(store, aid, document)] == [
        "Conflicts with staged work", HELD, "Editorial review outstanding",
    ]
    for p in doc["recommendations"]:
        with pytest.raises(ValueError):
            stage_review(store, aid, document, [p["proposal_id"]])
    assert preview(store, aid) == before
    assert stage_review(store, aid, document, [hold["proposal_id"]], [hold["proposal_id"]]) == 1
    assert preview(store, aid)["books"][0]["finding_ids"] == [fid]
    assert not api.writes


def test_staging_rolls_back_and_unrelated_journal_stays_blocking(single, monkeypatch):
    store, api, aid, doc = single
    doc["recommendations"].append(proposal(store.books(aid)[0], "subtitle", "New subtitle"))
    document = parse_review(encode(doc))
    ids = [p.id for p in document.proposals]
    operation = store.record(api.server, "library-a", "unrelated", "apply", "uncertain", {})
    findings = store.findings(aid)
    original = store._confirm_targets

    def fail_second(db, aid, iid, targets, evidence):
        if "subtitle" in targets:
            raise ValueError("Synthetic second-target failure")
        original(db, aid, iid, targets, evidence)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_confirm_targets", fail_second)
        with pytest.raises(ValueError, match="second-target"):
            stage_review(store, aid, document, ids)
    assert store.corrections(aid) == []
    assert store.findings(aid) == findings
    assert stage_review(store, aid, document, ids) == 2
    assert not api.writes
    assert preview(store, aid)["books"] == []
    staged = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target"]
    store.review(staged, "approved")
    plan = preview(store, aid, staged)
    with pytest.raises(SafetyError, match="uncertain History"):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes
    assert store.operation(operation)["state"] == "uncertain"


def test_live_check_apply_and_rollback(single):
    store, api, aid, doc = single
    document = parse_review(encode(doc))
    pid = document.proposals[0].id
    book = api.items["current-id"]
    original = copy.deepcopy(book)
    assert check_live(store, api, aid, document, [pid]) == {pid: "Matches saved snapshot"}
    book["media"]["metadata"]["title"] = "External edit"
    assert check_live(store, api, aid, document, [pid]) == {pid: "Live metadata mismatch"}
    book["media"]["metadata"]["title"] = "Changed title"
    assert check_live(store, api, aid, document, [pid]) == {pid: "Desired value already present in ABS"}
    assert not api.writes and not store.history()
    api.items["current-id"] = original
    assert stage_review(store, aid, document, [pid]) == 1
    assert preview(store, aid)["books"] == []
    fid = next(f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target")
    store.review([fid], "approved")
    plan = preview(store, aid, [fid])
    wf = Workflow(store, api)
    with pytest.raises(SafetyError):
        wf.apply(plan, "incorrect")
    operation, = wf.apply(plan, confirmation(plan))
    assert api.writes == [("current-id", {"title": "Changed title"})]
    back = wf.rollback_preview(operation)
    wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert Book(api.item("current-id")).values["title"] == store.books(aid)[0].values["title"]


def test_new_snapshot_compares_exact_item_and_current_values(single):
    store, adapter, aid, doc = single
    document = parse_review(encode(doc))
    raw = adapter.item("current-id")
    current = save(store, [raw])
    assert review_rows(store, current, document)[0].disposition == READY
    assert review_rows(store, aid, document)[0].disposition == "Snapshot or item unavailable for staging"
    raw["media"]["metadata"]["title"] = "External edit"
    current = save(store, [raw])
    assert review_rows(store, current, document)[0].disposition == "Current snapshot metadata changed"
    raw["media"]["metadata"]["title"] = "Changed title"
    current = save(store, [raw])
    assert review_rows(store, current, document)[0].disposition == ALREADY_APPLIED
    raw["relPath"] = "Moved"
    current = save(store, [raw])
    assert review_rows(store, current, document)[0].disposition == "Current item identity changed"


def test_series_replacement_cannot_drop_membership(single):
    store, _, aid, doc = single
    doc["recommendations"] = [proposal(store.books(aid)[0], "series", [{"name": "Other", "sequence": "0.25"}])]
    assert review_rows(store, aid, parse_review(encode(doc)))[0].disposition == "Series membership would be removed"


def test_upload_filter_select_and_stage_in_streamlit(single, tmp_path):
    from streamlit.testing.v1 import AppTest

    store, _, aid, doc = single
    book = store.books(aid)[0]
    doc["recommendations"] += [
        proposal(book, "genres", ["Fantasy", "Romance", "Fantasy Romance"]),
        proposal(book, "narrators", ["New voice"], "requires_recording_verification"),
    ]
    script = tmp_path / "upload_app.py"
    script.write_text(f"""
from abs_manager.review_import_ui import import_corrections
from abs_manager.store import Store
store = Store({str(store.path)!r})
import_corrections(store, store.audit({aid}))
""", encoding="utf-8")
    at = AppTest.from_file(script, default_timeout=20).run()

    def widget(elements, label):
        return next(element for element in elements if element.label == label)

    at.file_uploader[0].upload("synthetic.json", encode(doc), "application/json").run()
    assert not at.exception
    assert store.corrections(aid) == []
    assert len(widget(at.multiselect, "Fields selected for staging").options) == 1
    widget(at.selectbox, "Field").select("genres").run()
    assert widget(at.button, "Select eligible rows in view").disabled
    widget(at.selectbox, "Field").select("title").run()
    widget(at.button, "Select eligible rows in view").click().run()
    assert not at.exception
    assert widget(at.multiselect, "Fields selected for staging").value == [doc["recommendations"][0]["proposal_id"]]
    widget(at.button, "Clear rows in view").click().run()
    assert not widget(at.multiselect, "Fields selected for staging").value
    widget(at.button, "Select eligible rows in view").click().run()
    widget(at.button, "Stage selected changes locally (1)").click().run()
    assert not at.exception
    assert [(r["field"], r["status"]) for r in store.corrections(aid)] == [("title", "pending")]
    assert preview(store, aid)["books"] == []
