"""S4 uses only synthetic snapshots, cached providers and fake ABS transport."""
import copy
import json

import pytest
from conftest import FakeAdapter
from test_quality_policy import clean, reference
from test_streamlined import app, page, widget
from test_verification import FakeSource

from abs_manager.correction_plan import completion_report, library_plan, stage_selected
from abs_manager.store import Store
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import run_audit


def fixture(tmp_path, count=3):
    books = [clean(f"audio-{i}") for i in range(count)]
    for b in books:
        b["media"]["metadata"]["narrators"] = []
    api = FakeAdapter(books)
    store = Store(tmp_path / "s4.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    acquire(store, aid)
    return store, api, aid


def acquire(store, aid, candidates=None):
    source = FakeSource()
    source.search = lambda q: {"candidates": candidates or [reference()]}
    run_verification(store, start_verification(store, aid, source,
                     [b.id for b in store.books(aid) if b.is_audio], refresh=True), source)


def test_grouped_targets_atomic_deduplicated_and_never_write_approved(tmp_path):
    store, api, aid = fixture(tmp_path)
    plan = library_plan(store, aid)
    assert len(plan["proposals"]) == 3
    chosen = [p["id"] for p in plan["proposals"]][:2]
    assert stage_selected(store, plan, chosen) == 2
    assert len(store.corrections(aid)) == 2
    assert not any(f["status"] == "approved" for f in store.findings(aid))
    assert not api.writes
    assert len(library_plan(store, aid)["proposals"]) == 1
    with pytest.raises(ValueError, match="changed"):
        stage_selected(store, plan, chosen)
    assert len(store.corrections(aid)) == 2


def test_stale_evidence_rejection_and_atomic_invalid_selection(tmp_path):
    store, api, aid = fixture(tmp_path)
    plan = library_plan(store, aid)
    with pytest.raises(ValueError):
        stage_selected(store, plan, [plan["proposals"][0]["id"], "invalid"])
    assert not store.corrections(aid)
    acquire(store, aid, [reference(narrators=["Other"] )])
    with pytest.raises(ValueError, match="changed"):
        stage_selected(store, plan, [plan["proposals"][0]["id"]])
    assert not api.writes


def test_no_narrators_from_work_ambiguous_or_contradictory_recordings(tmp_path):
    store, api, aid = fixture(tmp_path)
    work = reference()
    work["work_only"] = True
    acquire(store, aid, [work])
    assert not library_plan(store, aid)["proposals"]
    acquire(store, aid, [reference(), reference(narrators=["Other"])])
    assert not library_plan(store, aid)["proposals"]
    assert not api.writes


def test_edgedancer_no_embedded_title_proposal(tmp_path):
    b = clean()
    b["media"]["metadata"].update(title="Edgedancer", series=[{"name": "Stormlight Archive", "sequence": "2.5"}])
    b["path"] = b["relPath"] = "Author/Stormlight Archive/2.5 - Edgedancer"
    b["media"]["audioFiles"][0]["metaTags"]["tagTitle"] = "Stormlight Archive 02.5 - Edgedancer"
    api = FakeAdapter([b])
    store = Store(tmp_path / "edge.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    assert not any(p["field"] == "title" for p in library_plan(store, aid)["proposals"])


def test_whole_inventory_750_books(tmp_path):
    books = [clean(f"audio-{i}") for i in range(100)] + [clean(f"ebook-{i}", False) for i in range(650)]
    api = FakeAdapter(books)
    store = Store(tmp_path / "whole.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    plan = library_plan(store, aid)
    assert sum(plan["counts"].values()) == len(plan["inventory"]) == 750
    assert len({r["item_id"] for r in plan["inventory"]}) == 750
    assert completion_report(store, aid, plan=plan)["books"] == 750
    assert not api.writes


def test_native_plan_ui_rerender_never_fetches_or_writes(tmp_path, monkeypatch):
    store, api, aid = fixture(tmp_path)
    before = copy.deepcopy(api.reads)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    at.run()
    assert not at.exception and api.reads == before and not api.writes
    plan = library_plan(store, aid)
    widget(at, "multiselect", "Supported audiobook targets").set_value([plan["proposals"][0]["id"]]).run()
    widget(at, "button", "Accept selected local targets").click().run()
    assert not at.exception and len(store.corrections(aid)) == 1
    assert not api.writes and api.reads == before
    assert json.loads(json.dumps(completion_report(store, aid)))["proposals"] == 2
