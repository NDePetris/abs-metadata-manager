"""Synthetic ABS BookFinder variants; no production payload/root-cause claim."""
import copy
import json

import pytest
from conftest import FakeAdapter
from test_streamlined import app, page, widget

from abs_manager.diagnostics import CandidateError
from abs_manager.sources import AbsSource
from abs_manager.verification import candidate, reference_values
from abs_manager.verification_workflow import run_verification, start_verification


def source_for_rows(api, rows):
    api.metadata_search = lambda *args: copy.deepcopy(rows)
    return AbsSource(api, "audible")


@pytest.mark.parametrize("updates", [
    {}, {"title": None, "subtitle": None, "series": None},
    {"authors": None, "narrators": None, "isbn": None, "asin": None, "duration": None, "url": None},
    {"author": "Smith, John", "narrator": "Kate Reading, Michael Kramer"},
    {"authors": [{"name": "Author", "role": "author"}], "narrator": {"name": "Reader", "role": "narrator"}},
    {"series": "Cycle", "seriesNumber": "2.5"}, {"series": {"name": "Cycle", "sequence": 2.5}},
    {"series": [{"name": "Cycle", "sequence": None}], "duration": 12.5, "link": "https://example.invalid/book"},
    # ABS server/providers/Audible.js cleanResult (blob cf1a15f): series alias,
    # null standalone series, display-string contributors and minute duration.
    {"series": [{"series": "Cycle", "sequence": "2.5"}], "author": "Author", "narrator": "Reader", "duration": 12},
])
def test_abs_variants_preserve_original(updates):
    raw = {"title": "Title", **updates}
    result = source_for_rows(FakeAdapter(), [raw]).search({"title": "Title"})
    assert result.get("state", "ok") == "ok"
    c = result["candidates"][0]
    assert c["raw"] == raw and result["raw"] == [raw]
    assert c["duration"] == (raw["duration"] * 60 if raw.get("duration") is not None else None)
    if raw.get("series", "absent") is None:
        assert reference_values({"candidate": c})["series"] is None


@pytest.mark.parametrize("field,value,actual", [
    ("title", {}, "object"), ("subtitle", [], "array"), ("authors", [None], "null"),
    ("narrator", 42, "number"), ("series", ["unsupported"], "string"),
    ("series", {"name": 4}, "number"), ("series", {"name": "Cycle", "sequence": {}}, "object"),
    ("duration", "12 hrs", "string"), ("duration", True, "boolean"),
    ("url", {}, "object"), ("link", [], "array"), ("isbn", {}, "object"),
    ("asin", [], "array"), ("language", [], "array"), ("id", {}, "object"),
])
def test_bad_field_structural_diagnostic(field, value, actual):
    raw = {"title": "private title", "description": "synthetic-secret", field: value}
    result = source_for_rows(FakeAdapter(), [raw]).search({"title": "private query"})
    assert result["state"] == "unavailable" and result["raw"] == [raw]
    d = result["candidate_diagnostics"][0]
    assert d["provider"] == "abs:audible" and d["candidate_index"] == 0
    assert d["category"] == "candidate_normalization" and d["stage"] == "normalization"
    assert d["actual_type"] == actual and d["field"].startswith(field) and d["expected_type"]
    assert all(s not in json.dumps(d) for s in ("private title", "synthetic-secret", "private query", "unsupported"))


def test_partial_pipeline_persists_matches_and_rejections(setup):
    store, api, aid = setup
    good = {"title": "Heaven's River", "author": "Dennis E. Taylor", "narrator": "Ray Porter",
            "asin": "B012345678", "duration": 1000 / 60, "series": None}
    source = source_for_rows(api, [None, good, {"title": {"private": "synthetic-secret"}}])
    run_verification(store, start_verification(store, aid, source, ["current-id"]), source)
    report = store.verification_report(aid)[0]
    result = report["lookups"][0]
    assert result["state"] == "partial" and len(report["matches"]) == 1
    assert [d["candidate_index"] for d in result["candidate_diagnostics"]] == [0, 2]
    assert result["candidate_diagnostics"][0]["category"] == "unsupported_candidate_shape"
    cid = report["matches"][0]["candidate_id"]
    store.decide_candidate(aid, "current-id", cid, "rejected", "Human rejected")
    store.confirm_targets(aid, "current-id", {"title": "Heaven's River"})
    api.metadata_search = lambda *a: [{**good, "url": "https://example.invalid/another-query"}, False]
    run_verification(store, start_verification(store, aid, source, ["current-id"], refresh=True), source)
    updated = store.verification_report(aid)[0]
    assert updated["matches"][0]["candidate_id"] == cid and updated["matches"][0]["rejected"]
    assert store.field_decisions(aid, "current-id")["title"]["valid"]
    assert not api.writes and not store.history()


def test_identity_and_serialization_stages_are_distinct(monkeypatch):
    api = FakeAdapter()
    source = source_for_rows(api, [{"title": "Title"}])
    def fail(*args):
        raise ValueError("synthetic-secret")
    monkeypatch.setattr("abs_manager.verification.digest", fail)
    result = source.search({"title": "Title"})
    assert result["diagnostic"]["category"] == "candidate_identity"
    assert "synthetic-secret" not in json.dumps(result["diagnostic"])
    monkeypatch.undo()
    source.normalize = lambda *args: {"private": object()}
    assert source.search({"title": "Title"})["diagnostic"]["category"] == "candidate_persistence"


def test_partial_ui_search_is_explicit_and_preserves_draft(setup, monkeypatch):
    store, api, _aid = setup
    calls = []
    def search(*args):
        calls.append(args)
        return [{"title": "Heaven's River", "asin": "B012345678"}, {"series": 42}]
    api.metadata_search = search
    at = app(store, api, monkeypatch)
    at.session_state["provider_discovery"] = {"server": api.server, "status": "available", "providers": [{"value": "audible"}]}
    page(at, "Match & Verify")
    widget(at, "text_input", "Desired subtitle").set_value("Unsaved draft").run()
    widget(at, "button", "Search").click().run()
    assert not at.exception and any("Partial results" in w.value for w in at.warning)
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved draft"
    assert len(widget(at, "selectbox", "Reference candidate").options) == 1
    at.run()
    assert len(calls) == 1 and not api.writes


def test_existing_identity_formula_unchanged():
    # Published ASIN identities must not depend on optional fields or query URLs.
    a = candidate({"asin": "B012345678", "title": "Title"}, "abs:audible")
    b = candidate({"asin": "B012345678", "title": None, "series": None}, "abs:audible", "other")
    assert a["id"] == b["id"]
    with pytest.raises(CandidateError):
        candidate({"authors": [{"unexpected": "synthetic-secret"}]}, "abs:audible")


def test_abs_series_alias_retains_fallback_identity_and_all_series():
    raw = {"title": "Title", "series": [{"series": "Cycle", "sequence": "2.5"},
                                         {"series": "Universe", "sequence": None}]}
    c = candidate(raw, "abs:audible")
    # Earlier captures silently omitted these provider series entries from the
    # fallback digest. Keep that identity while restoring the actual evidence.
    legacy = candidate({"title": "Title", "series": []}, "abs:audible")
    assert c["id"] == legacy["id"]
    assert c["values"]["series"] == [{"name": "Cycle", "sequence": "2.5"}, {"name": "Universe", "sequence": None}]
    assert c["raw"] == raw
