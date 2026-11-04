"""S2 quality is deterministic analysis of synthetic snapshots, never acceptance."""
import copy
import time

import pytest
from conftest import make_book
from test_persistent_state import snapshot
from test_verification import FakeSource, evidence

from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.verification import assess, candidate, match, quality_policy
from abs_manager.verification_workflow import (
    evidence_coverage,
    retry_unavailable,
    run_coverage,
    run_verification,
    start_verification,
    unassessed_batch,
)


def clean(item_id="clean", audio=True):
    raw = make_book(title="Example", item_id=item_id, authors=["Author One"], narrators=["Narrator One"],
                    series=None, path="Author One/Example {Narrator One}")
    raw["media"]["metadata"].update(asin="B012345678", genres=[])
    raw["media"].update(tags=[], chapters=[])
    raw["media"]["audioFiles"][0]["metaTags"] = {"tagTitle": "Example"}
    if not audio:
        raw["media"].update(audioFiles=[], duration=0)
    return raw


def reference(**changes):
    return candidate({"title": "Example", "authors": ["Author One"], "narrators": ["Narrator One"],
                      "asin": "B012345678", "duration": 1000, **changes}, "catalog")


def test_strong_fields_optional_absence_cover_and_purity():
    r = assess(Book(clean()), evidence(reference()))
    before = copy.deepcopy(r)
    q = quality_policy(r)
    assert q == quality_policy(r) and r == before
    for field in ("title", "authors", "narrators", "identifiers"):
        assert q["fields"][field]["reason_code"] == "independently_verified"
    for field in ("subtitle", "translators", "series", "genres", "tags"):
        assert q["fields"][field]["reason_code"] == "optional_absent"
    assert q["fields"]["cover"]["disposition"] == "Needs evidence"
    assert not q["fields"]["embedded"]["quality_applicable"]
    # Full correctness still needs explicit visual cover evidence (S3).
    decision = {"cover": {"valid": True, "decision": "confirmed", "value": r["fields"]["cover"]["value"]}}
    assert quality_policy(r, decision)["disposition"] == "Pass"


@pytest.mark.parametrize("field,value", [("title", ""), ("authors", []), ("narrators", [])])
def test_required_missing(field, value):
    raw = clean()
    raw["media"]["metadata"][field] = value
    assert quality_policy(assess(Book(raw)))["fields"][field]["disposition"] == "Needs attention"


def test_unnumbered_series_can_be_explicitly_confirmed():
    raw = clean()
    raw["media"]["metadata"]["series"] = [{"name": "Unnumbered collection", "sequence": None}]
    r = assess(Book(raw))
    assert quality_policy(r)["fields"]["series_position"]["disposition"] == "Needs attention"
    d = {"series_position": {"valid": True, "decision": "confirmed", "value": [None]}}
    assert quality_policy(r, d)["fields"]["series_position"]["reason_code"] == "human_confirmed"


@pytest.mark.parametrize("decision", ["unable", "later", "reopened", "correction_needed"])
def test_unresolved_decisions_cannot_pass_with_strong_evidence(decision):
    r = assess(Book(clean()), evidence(reference()))
    d = {"title": {"valid": True, "decision": decision, "value": "Example"}}
    assert quality_policy(r, d)["fields"]["title"]["disposition"] != "Pass"


def test_stale_confirmation_and_outstanding_correction():
    r = assess(Book(clean()), evidence(reference()))
    d = {"title": {"valid": False, "decision": "confirmed", "value": "Old title"}}
    assert quality_policy(assess(Book(clean())), d)["fields"]["title"]["disposition"] == "Needs evidence"
    c = [{"item_id": "clean", "field": "title", "status": "pending"}]
    assert quality_policy(r, {}, c)["fields"]["title"]["reason_code"] == "correction_outstanding"
    c[0]["status"] = "observed_in_abs"
    assert quality_policy(r, {}, c)["fields"]["title"]["disposition"] == "Pass"


def test_new_conflict_reopens_quality_but_not_write_approval(tmp_path):
    store = Store(tmp_path / "db")
    aid = snapshot(store, [clean()])
    store.decide_fields(aid, "clean", ["subtitle"], "confirmed", "checked")
    source = FakeSource()
    source.search = lambda q: {"candidates": [reference(subtitle="New subtitle")]}
    run_verification(store, start_verification(store, aid, source, ["clean"]), source)
    f = store.verification_report(aid)[0]["fields"]["subtitle"]
    assert f["disposition"] == "Needs attention" and f["decision"]["valid"]
    assert not store.corrections(aid)


def test_reimport_compatibility_and_material_change(tmp_path):
    store = Store(tmp_path / "db")
    raw = clean()
    aid = snapshot(store, [raw])
    store.decide_fields(aid, "clean", ["title"], "confirmed", "checked")
    aid = snapshot(store, [raw])
    assert store.verification_report(aid)[0]["fields"]["title"]["reason_code"] == "human_confirmed"
    raw["media"]["metadata"]["title"] = "Changed"
    aid = snapshot(store, [raw])
    assert store.verification_report(aid)[0]["fields"]["title"]["disposition"] != "Pass"


@pytest.mark.parametrize("changes", [{"asin": None}, {"asin": "unreliable"}, {"asin": "B999999999"},
                                    {"narrators": ["Other Narrator"]}, {"language": "fr"}, {"abridged": True},
                                    {"isbn": "9780306406157"}])
def test_lookalike_recordings_and_identifier_conflicts(changes):
    raw = clean()
    raw["media"]["metadata"].update(abridged=False, isbn="9780140328721")
    b = Book(raw)
    c = reference(**changes)
    assert match(b, c)["level"] != "recording"
    assert quality_policy(assess(b, evidence(c)))["fields"]["narrators"]["disposition"] != "Pass"


def test_multiple_recordings_preserve_work_agreement_and_source_conflicts():
    raw = clean()
    raw["media"]["metadata"].pop("asin")
    b = Book(raw)
    first, second = reference(asin=None), reference(asin=None, duration=1010)
    r = quality_policy(assess(b, evidence(first, second)))
    assert r["fields"]["title"]["disposition"] == "Pass"
    assert r["fields"]["narrators"]["disposition"] == "Needs evidence"
    first = reference(subtitle="One")
    second = candidate({**first["raw"], "subtitle": "Two"}, "other provider")
    r = quality_policy(assess(Book(clean()), evidence(first, second)))
    assert r["fields"]["subtitle"]["reason_code"] == "conflicting_references"


def test_failed_refresh_preserves_success_and_coverage_resume(tmp_path):
    store = Store(tmp_path / "db")
    aid = snapshot(store, [clean("a"), clean("b")])
    source = FakeSource()
    source.search = lambda q: {"candidates": [reference()]}
    rid = start_verification(store, aid, source, ["a", "b"])
    run_verification(store, rid, source, limit=1)
    # Identical compatible queries reuse the first lookup even across tasks.
    run_verification(Store(store.path), rid, source, limit=1)
    assert store.verification_runs(aid)[0]["state"] == "complete"
    assert run_coverage(store, rid)["cache_reused"] == 1
    assert evidence_coverage(store.verification_report(aid))["attempted"] == 2
    source.fail = True
    def unavailable(q):
        from abs_manager.adapter import AdapterError
        raise AdapterError("synthetic outage")
    source.search = unavailable
    rid = start_verification(store, aid, source, ["a", "b"], refresh=True)
    run_verification(store, rid, source)
    report = store.verification_report(aid)
    coverage = evidence_coverage(report)
    assert coverage["unavailable"] == 1 and coverage["matched"] == 2
    assert report[0]["fields"]["title"]["disposition"] == "Pass"
    retry_unavailable(store, rid)
    assert store.verification_runs(aid)[0]["done"] == 0
    assert evidence_coverage(store.verification_report(aid))["matched"] == 2


def test_synthetic_library_size_coverage(tmp_path):
    store = Store(tmp_path / "db")
    raw = [clean(f"audio-{i}") for i in range(100)] + [clean(f"ebook-{i}", False) for i in range(650)]
    aid = snapshot(store, raw)
    started = time.monotonic()
    report = store.verification_report(aid)
    elapsed = time.monotonic() - started
    assert len(report) == 750
    assert all(r["disposition"] in ("Pass", "Needs evidence", "Needs attention") for r in report)
    coverage = evidence_coverage(report)
    assert coverage["unattempted"] == 100 and coverage["remaining"] == 750
    assert len(unassessed_batch(report, "catalog")) == 25
    print(f"Synthetic 100 audiobook / 650 ebook report: {elapsed:.2f}s")


def test_same_provider_identity_cannot_hide_contradictions_by_result_order():
    first, second = reference(subtitle="One"), reference(subtitle="Two")
    assert first["id"] == second["id"]
    for entries in ((first, second), (second, first)):
        r = quality_policy(assess(Book(clean()), evidence(*entries)))
        assert len(r["matches"]) == 2
        assert r["fields"]["subtitle"]["reason_code"] == "conflicting_references"


def test_multiple_isbn_recordings_do_not_establish_recording_identity():
    raw = clean()
    raw["media"]["metadata"].pop("asin")
    raw["media"]["metadata"]["isbn"] = "9780306406157"
    first = reference(asin="B111111111", isbn="9780306406157")
    second = reference(asin="B222222222", isbn="9780306406157", duration=1010)
    q = quality_policy(assess(Book(raw), evidence(first, second)))
    assert q["fields"]["title"]["disposition"] == "Pass"
    assert q["fields"]["narrators"]["disposition"] == "Needs evidence"


def test_fresh_exact_identity_conflict_reopens_human_confirmation(tmp_path):
    store = Store(tmp_path / "db")
    aid = snapshot(store, [clean()])
    store.decide_fields(aid, "clean", ["narrators"], "confirmed", "checked")
    source = FakeSource()
    source.search = lambda q: {"candidates": [reference(narrators=["Different recording"])]}
    run_verification(store, start_verification(store, aid, source, ["clean"]), source)
    q = store.verification_report(aid)[0]
    assert q["fields"]["narrators"]["reason_code"] == "identity_conflict"


def test_partial_results_and_unattempted_are_distinct(tmp_path):
    store = Store(tmp_path / "db")
    aid = snapshot(store, [clean("a"), clean("b")])
    source = FakeSource()
    source.search = lambda q: {"state": "partial", "candidates": [reference()]}
    run_verification(store, start_verification(store, aid, source, ["a"]), source)
    report = store.verification_report(aid)
    c = evidence_coverage(report)
    assert c["partial"] == 1 and c["unattempted"] == 1 and c["remaining"] == 2
    assert next(r for r in report if r["item_id"] == "a")["fields"]["title"]["reason_code"] == "partial_evidence"


def test_keyboard_interruption_checkpoints_and_explicit_resume(tmp_path):
    store = Store(tmp_path / "db")
    other = clean("b")
    other["media"]["metadata"]["subtitle"] = "Distinct cache query"
    aid = snapshot(store, [clean("a"), other])
    source = FakeSource()
    source.search = lambda q: {"candidates": [reference()]}
    rid = start_verification(store, aid, source, ["a", "b"])
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_verification(store, rid, source, progress=interrupt)
    assert store.verification_runs(aid)[0]["done"] == 1
    assert store.verification_runs(aid)[0]["state"] == "paused"
    run_verification(Store(store.path), rid, source)
    assert store.verification_runs(aid)[0]["done"] == 2


def test_cache_invalidation_on_identifier_change(tmp_path):
    store = Store(tmp_path / "db")
    raw = clean()
    aid = snapshot(store, [raw])
    source = FakeSource()
    run_verification(store, start_verification(store, aid, source, ["clean"]), source)
    raw["media"]["metadata"]["asin"] = "B999999999"
    aid = snapshot(store, [raw])
    run_verification(store, start_verification(store, aid, source, ["clean"]), source)
    assert len(source.calls) == 2


def test_omitted_identifier_is_incomplete_not_conflicting():
    raw = clean()
    raw["media"]["metadata"]["isbn"] = "9780306406157"
    q = quality_policy(assess(Book(raw), evidence(reference())))
    assert q["fields"]["identifiers"]["disposition"] == "Needs evidence"
    assert q["fields"]["narrators"]["disposition"] == "Pass"
