"""Targeted S4 stabilization; all catalogs, journals and adapters are synthetic."""
import copy
import json

import pytest
from conftest import FakeAdapter
from test_quality_policy import clean, reference
from test_s4_corrections import acquire, fixture
from test_streamlined import app, page

from abs_manager.calibre import import_catalog
from abs_manager.correction_plan import library_plan, stage_selected
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, confirmation, preview, run_audit


def test_journal_blocks_writes_without_weakening_evidence_or_local_staging(tmp_path, monkeypatch):
    store, api, aid = fixture(tmp_path)
    before = library_plan(store, aid)
    oid = store.record(api.server, "library-a", "audio-0", "apply", "uncertain", {})
    journal = store.operation(oid)
    plan = library_plan(store, aid)
    assert plan["writes_blocked"] and not before["writes_blocked"]
    assert plan["proposals"] == before["proposals"]
    assert plan["counts"] == before["counts"]
    assert all(p["grouped"] and p["strength"] == "Recording-backed" for p in plan["proposals"])
    reads = list(api.reads)
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    at.run()
    assert any("Writes are blocked" in w.value and "History" in w.value for w in at.warning)
    assert api.reads == reads and not api.writes
    assert stage_selected(store, plan, [p["id"] for p in plan["proposals"]]) == 3
    ids = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target"]
    store.review(ids, "approved")
    batch = preview(store, aid, ids)
    with pytest.raises(SafetyError, match="Reconcile"):
        Workflow(store, api).apply(batch, confirmation(batch))
    assert not api.writes and store.operation(oid) == journal


@pytest.mark.parametrize("kind", ["weak", "ambiguous", "contradictory"])
def test_clear_journal_cannot_upgrade_inadequate_evidence(tmp_path, kind):
    store, api, aid = fixture(tmp_path)
    if kind == "weak":
        candidate = reference()
        candidate["work_only"] = True
        candidates = [candidate]
    elif kind == "ambiguous":
        candidates = [reference(), reference(narrators=["Another narrator"])]
    else:
        candidates = [reference(duration=3000)]
    acquire(store, aid, candidates)
    plan = library_plan(store, aid)
    assert not plan["writes_blocked"]
    assert not any(p["grouped"] for p in plan["proposals"])
    assert not api.writes


def verified_ebooks(tmp_path):
    books = []
    catalogs = {}
    for label in ("A", "B"):
        raw = clean("ebook-" + label, False)
        raw["path"] = raw["relPath"] = f"Author/Book {label} (1)"
        raw["media"]["metadata"]["title"] = "Book " + label
        books.append(raw)
        catalogs[label] = [{"id": 1, "uuid": "uuid-" + label, "title": "Corrected " + label,
                            "authors": ["Author One"]}]
    api = FakeAdapter(books)
    store = Store(tmp_path / "calibre-scopes.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    for label in ("A", "B"):
        store.confirm_targets(aid, "ebook-" + label, {"title": "Corrected " + label})
        assert import_catalog(store, aid, json.dumps(catalogs[label]), "source-" + label) == 1
    assert all(c["status"] == "verified_in_calibre" and not c["abs_observed"] for c in store.corrections(aid))
    return store, api, aid, catalogs


@pytest.mark.parametrize("kind", ["matching_A", "matching_B", "unscoped", "missing_uuid",
                                   "duplicate_uuid", "missing_work", "incomplete_other_record", "missing_field"])
def test_irrelevant_or_non_authoritative_catalog_preserves_verified_corrections(tmp_path, kind):
    store, api, aid, catalogs = verified_ebooks(tmp_path)
    before = store.corrections(aid)
    reads = list(api.reads)
    scope, catalog = "source-A", copy.deepcopy(catalogs["A"])
    if kind == "matching_B":
        scope, catalog = "source-B", catalogs["B"]
    elif kind == "unscoped":
        scope, catalog = None, []
    elif kind == "missing_uuid":
        catalog[0].pop("uuid")
    elif kind == "duplicate_uuid":
        catalog.append({**catalog[0], "id": 2})
    elif kind == "missing_work":
        catalog[0].pop("authors")
    elif kind == "incomplete_other_record":
        catalog = [{"id": 2, "title": "Unrelated"}]
    elif kind == "missing_field":
        # A field absent from a matched catalog cannot contradict its historic target.
        store.confirm_targets(aid, "ebook-A", {"series": [{"name": "Cycle", "sequence": "2.5"}]})
        with_series = [{**catalog[0], "series": "Cycle", "series_index": "2.5"}]
        import_catalog(store, aid, json.dumps(with_series), scope)
        before = store.corrections(aid)
    import_catalog(store, aid, json.dumps(catalog), scope)
    assert store.corrections(aid) == before
    assert api.reads == reads and not api.writes
    if kind in ("matching_A", "matching_B", "unscoped"):
        matched = json.loads(store.rows("SELECT data FROM calibre_catalogs WHERE audit_id=?", (aid,))[0]["data"])
        assert set(matched) == {"ebook-A", "ebook-B"}


@pytest.mark.parametrize("change", ["uuid", "removed", "title", "authors"])
def test_authoritative_changes_reopen_only_the_corresponding_binding(tmp_path, change):
    store, api, aid, catalogs = verified_ebooks(tmp_path)
    before_b = next(c for c in store.corrections(aid) if c["item_id"] == "ebook-B")
    catalog = copy.deepcopy(catalogs["A"])
    if change == "removed":
        catalog = []
    elif change == "uuid":
        catalog[0]["uuid"] = "replacement-uuid"
    else:
        catalog[0][change] = "Different title" if change == "title" else ["Different author"]
    import_catalog(store, aid, json.dumps(catalog), "source-A")
    after = {c["item_id"]: c for c in store.corrections(aid)}
    assert after["ebook-A"]["status"] == "needs_review"
    assert after["ebook-A"]["calibre_verified"] and not after["ebook-A"]["abs_observed"]
    assert after["ebook-B"] == before_b
    binding = next(r for r in store.rows("SELECT * FROM calibre_bindings") if r["item_id"] == "ebook-A")
    assert (binding["calibre_library"], binding["calibre_id"], binding["calibre_uuid"]) == ("source-A", 1, "uuid-A")
    assert not api.writes
