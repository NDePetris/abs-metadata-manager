"""Embedded source discrepancies cannot establish incorrect saved ABS titles."""
import copy

import pytest
from conftest import FakeAdapter, make_book
from test_persistent_state import snapshot
from test_streamlined import app, page
from test_verification import evidence

from abs_manager.audit import analyze
from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.verification import assess, candidate, quality_policy
from abs_manager.verification_ui import quality_queue
from abs_manager.workflow import preview


def source_prefixed_book(title="Edgedancer", series="Stormlight Archive", position="2.5"):
    raw = make_book(title=title, item_id="synthetic-source-title", authors=["Brandon Sanderson"],
                    narrators=[], series=series, seq=position,
                    path=f"Brandon Sanderson/{series}/{position} - {title}")
    raw["media"]["metadata"]["asin"] = "B012345678"
    raw["media"]["audioFiles"][0]["metaTags"] = {"tagTitle": f"{series} 0{position} - {title}"}
    return raw


@pytest.mark.parametrize("title,series,position", [("Edgedancer", "Stormlight Archive", "2.5"),
                                                 ("Side Story", "Another Series", "4.5")])
def test_embedded_series_prefix_is_deferred_diagnostic_not_abs_title_conflict(title, series, position):
    raw = source_prefixed_book(title, series, position)
    before = copy.deepcopy(raw)
    book = Book(raw)
    normalized, findings = analyze(book)
    diagnostic = next(f for f in findings if f.field == "embedded")
    assert diagnostic.kind == "informational" and not diagnostic.actionable
    assert diagnostic.evidence["embedded"]["title"] == raw["media"]["audioFiles"][0]["metaTags"]["tagTitle"]
    assert not any(f.field == "title" and (f.kind == "conflict" or f.actionable) for f in findings)
    assert normalized["title"] == title and raw == before
    result = quality_policy(assess(book))
    assert result["fields"]["title"]["status"] == "Consistent"
    assert result["fields"]["title"]["disposition"] == "Needs evidence"
    assert "folder" in result["fields"]["title"]["reason"]
    assert result["fields"]["authors"]["disposition"] == "Needs evidence"
    assert result["fields"]["narrators"]["reason_code"] == "required_missing"
    assert result["fields"]["series"]["disposition"] == "Needs evidence"
    assert result["fields"]["cover"]["disposition"] == "Needs evidence"
    embedded = result["fields"]["embedded"]
    assert embedded["status"] == "Conflict" and not embedded["quality_applicable"]
    assert embedded["reason_code"] == "inspection_deferred"
    assert "embedded audio title" in embedded["reason"] and "cleanup is deferred" in embedded["reason"]


def test_independent_evidence_can_verify_clean_title_despite_source_prefix():
    book = Book(source_prefixed_book())
    reference = candidate({"title": "Edgedancer", "authors": ["Brandon Sanderson"]},
                          "Synthetic work catalog", work_only=True)
    result = quality_policy(assess(book, evidence(reference)))
    assert result["fields"]["title"]["reason_code"] == "independently_verified"
    assert result["fields"]["title"]["provenance"][-1]["source"] == "Synthetic work catalog"
    assert result["fields"]["narrators"]["disposition"] == "Needs attention"
    assert not result["fields"]["embedded"]["quality_applicable"]


def test_credible_external_identity_still_flags_incorrect_abs_title():
    book = Book(source_prefixed_book(title="Incorrect saved title"))
    reference = candidate({"title": "Edgedancer", "authors": ["Brandon Sanderson"],
                           "asin": "B012345678"}, "Synthetic recording catalog")
    result = quality_policy(assess(book, evidence(reference)))
    assert result["fields"]["title"]["disposition"] == "Needs attention"
    assert result["fields"]["title"]["reason_code"] == "identity_conflict"
    assert "title" in result["matches"][0]["contradictions"]
    assert result["matches"][0]["candidate"]["source"] == "Synthetic recording catalog"


def test_source_diagnostic_never_stages_and_preserves_decisions_and_pending_correction(tmp_path):
    store = Store(tmp_path / "synthetic.sqlite")
    raw = source_prefixed_book()
    iid = raw["id"]
    aid = snapshot(store, [raw])
    report = store.verification_report(aid)
    assert not store.corrections(aid) and not preview(store, aid)["books"]
    assert report[0]["fields"]["title"]["disposition"] == "Needs evidence"
    assert not any(f["field"] == "title" and f["actionable"] for f in store.findings(aid))
    assert quality_queue(report, field="narrators")[0]["item_id"] == iid
    store.decide_fields(aid, iid, ["title"], "confirmed", "Inspected the correct saved title")
    store.confirm_targets(aid, iid, {"subtitle": "Pending subtitle"})
    decisions, corrections = store.field_decisions(aid, iid), store.corrections(aid)
    result = store.verification_report(aid)[0]
    assert result["fields"]["title"]["reason_code"] == "human_confirmed"
    assert result["fields"]["subtitle"]["reason_code"] == "correction_outstanding"
    assert store.field_decisions(aid, iid) == decisions and store.corrections(aid) == corrections
    aid = snapshot(store, [raw])
    result = store.verification_report(aid)[0]
    assert result["fields"]["title"]["reason_code"] == "human_confirmed"
    assert result["fields"]["subtitle"]["reason_code"] == "correction_outstanding"


def test_quality_ui_keeps_source_diagnostic_separate_from_title(tmp_path, monkeypatch):
    raw = source_prefixed_book()
    store = Store(tmp_path / "synthetic.sqlite")
    snapshot(store, [raw])
    adapter = FakeAdapter([raw])
    adapter.server = "test"
    at = app(store, adapter, monkeypatch)
    page(at, "Match & Verify")
    captions = " ".join(c.value for c in at.caption)
    assert "Saved title agrees with folder evidence; no independent external verification." in captions
    assert "Captured embedded audio title differs from saved ABS metadata. Source-file cleanup is deferred." in captions
    assert "Local evidence differs" not in captions
    assert not adapter.reads and not adapter.writes
