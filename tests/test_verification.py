import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from conftest import make_book

from abs_manager.adapter import AdapterError, CliAdapter, PublicMetadataTransport
from abs_manager.audit import analyze
from abs_manager.calibre import import_catalog
from abs_manager.config import Settings
from abs_manager.models import Book
from abs_manager.sources import AbsSource, PublicSource
from abs_manager.store import Store
from abs_manager.verification import QUALITY_FIELDS, assess, candidate, export_csv, isbn_key, match
from abs_manager.verification_workflow import (
    propose_discrepancy,
    retry_unavailable,
    run_verification,
    start_verification,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "metadata_sources.json").read_text())


def book(**kw):
    return Book(make_book(title="Example", series="Example Series", seq="2.5",
                          authors=["Author One"], narrators=["Narrator One"],
                          path="Author One/Example Series/2.5 - Example {Narrator One}", **kw))


def evidence(*candidates):
    return [{"source": "test", "state": "ok", "captured": "2026-10-28", "query": {"title": "Example"},
             "candidates": list(candidates)}]


def external(**kw):
    return candidate({"title": "Example", "author": "Author One", "narrator": "Narrator One",
                      "duration": 1000, **kw}, "test", "https://example.invalid/book")


class FakeSource:
    id = "test"
    scope = "test.invalid"
    targeted_only = False

    def __init__(self):
        self.calls = []
        self.fail = False
        self.empty = False

    def query(self, b):
        return {"title": b.values["title"]}

    def search(self, query):
        self.calls.append(query)
        if self.fail:
            raise AdapterError("test outage")
        return {"raw": [], "candidates": [] if self.empty else [external()]}


def test_every_book_report_even_without_findings_and_offline(setup):
    store, api, aid = setup
    clean = book(item_id="clean")
    clean.raw["media"]["audioFiles"][0]["metaTags"]["tagTitle"] = "Example"
    clean.raw["media"]["chapters"][0]["title"] = "Prologue"
    assert not analyze(clean)[1]
    store.add_book(aid, clean)
    report = Store(store.path).verification_report(aid)
    assert {b["item_id"] for b in report} == {"current-id", "clean"}
    assert all(set(b["fields"]) == set(QUALITY_FIELDS) for b in report)
    assert all(f["provenance"] for b in report for f in b["fields"].values())
    assert all(f["status"] != "Verified" for b in report for f in b["fields"].values())
    assert "clean" in export_csv(report)
    assert not api.writes


def test_ebook_calibre_folders_and_requirements():
    raw = copy.deepcopy(book().raw)
    raw.update(path="Author/Example with punctuation (653)", relPath="Author/Example trunc (653)")
    raw["media"].update(audioFiles=[], duration=0, chapters=[])
    raw["media"]["metadata"].update(narrators=[], series=[])
    b = Book(raw)
    findings = analyze(b)[1]
    assert not any(f.field in ("narrators", "title", "series") for f in findings)
    report = assess(b)
    assert all(report["fields"][f]["status"] == "Not applicable" for f in ("narrators", "chapters", "embedded", "series_position"))
    assert report["folder"]["title"] == "Example trunc"
    assert report["calibre_limitation"]
    assert report["fields"]["subtitle"]["status"] == "Unverified"


@pytest.mark.parametrize("changes,status", [({}, "Consistent"), ({"start": -1}, "Conflict"),
    ({"end": 1001}, "Conflict"), ({"start": 1000}, "Conflict"), ({"end": "bad"}, "Conflict"),
    ({"end": float("inf")}, "Conflict"), ({"end": None}, "Unverified")])
def test_chapter_timing(changes, status):
    raw = copy.deepcopy(book().raw)
    raw["media"]["chapters"][0].update(changes)
    result = assess(Book(raw))["fields"]["chapters"]
    assert result["status"] == status
    if not changes:
        assert "Generic" in result["reason"]


def test_chapter_overlap_and_absent():
    raw = copy.deepcopy(book().raw)
    raw["media"]["chapters"].append({"title": "Later", "start": 900, "end": 1000})
    assert assess(Book(raw))["fields"]["chapters"]["status"] == "Conflict"
    raw["media"]["chapters"] = []
    assert assess(Book(raw))["fields"]["chapters"]["status"] == "Missing"
    raw["media"]["numChapters"] = 10
    assert assess(Book(raw))["fields"]["chapters"]["status"] == "Unverified"


def test_cover_presence_never_means_correctness():
    b = book()
    assert assess(b, evidence(external(cover="https://example.invalid/c.jpg")))["fields"]["cover"]["status"] == "Unverified"
    raw = copy.deepcopy(b.raw)
    raw["media"]["coverPath"] = None
    assert assess(Book(raw))["fields"]["cover"]["status"] == "Missing"


def test_embedded_staleness_composer_and_no_mutation():
    raw = copy.deepcopy(book().raw)
    raw["media"]["audioFiles"][0]["metaTags"].update(tagComposer="Some composer", tagTitle="Old title")
    before = copy.deepcopy(raw)
    r = assess(Book(raw))
    assert r["fields"]["embedded"]["status"] == "Conflict"
    assert "reimport" in r["fields"]["embedded"]["reason"]
    assert r["fields"]["narrators"]["value"] == ["Narrator One"]
    assert raw == before


@pytest.mark.parametrize("identifier,value", [("asin", "B012345678"), ("isbn", "9780306406157")])
def test_exact_identifiers_with_recording_corroboration(identifier, value):
    raw = copy.deepcopy(book().raw)
    raw["media"]["metadata"][identifier] = value
    c = external(**{identifier: value})
    m = match(Book(raw), c)
    assert m["level"] == "recording"
    assert identifier in m["signals"]
    r = assess(Book(raw), evidence(c))
    assert r["fields"]["narrators"]["status"] == "Verified"
    assert r["fields"]["narrators"]["provenance"][-1]["candidate_id"] == c["id"]
    assert isbn_key("0-306-40615-2") == isbn_key("9780306406157")


def test_work_match_cannot_verify_narrator_or_recording_cover():
    c = external(duration=None)
    assert match(book(), c)["level"] == "work"
    r = assess(book(), evidence(c))
    assert r["fields"]["title"]["status"] == "Verified"
    assert r["fields"]["narrators"]["status"] == "Unverified"
    assert r["fields"]["cover"]["status"] == "Unverified"
    c = candidate({"title": "Example", "narrator": "Narrator One"}, "test")
    assert match(book(), c)["level"] == "unmatched"


def test_exact_asin_with_conflicting_credit_needs_review():
    raw = copy.deepcopy(book().raw)
    raw["media"]["metadata"]["asin"] = "B012345678"
    c = external(asin="B012345678", narrator="Correct recording narrator")
    r = assess(Book(raw), evidence(c))
    assert r["matches"][0]["classification"] == "different_recording"
    assert r["fields"]["narrators"]["status"] == "Unverified"
    assert isbn_key("9780306406158") == ""
    raw["media"]["metadata"]["isbn"] = "9780306406158"
    c = external(isbn="9780306406158")
    assert assess(Book(raw), evidence(c))["fields"]["identifiers"]["status"] == "Conflict"


def test_editions_ambiguous_and_human_selection_distinct():
    first, second = external(), external(narrator="Other recording narrator", duration=2000)
    r = assess(book(), evidence(first, second))
    assert not r["ambiguous"]
    assert r["fields"]["narrators"]["status"] == "Verified"
    assert r["matches"][1]["level"] == "work"
    assert "narrators" in r["matches"][1]["contradictions"]
    r = assess(book(), evidence(first, second), selected=first["id"])
    assert r["fields"]["narrators"]["status"] == "Consistent"


def test_multi_credits_decimals_and_optional_fields():
    raw = copy.deepcopy(book().raw)
    raw["media"]["metadata"].update(authors=[{"name": "Author One"}, {"name": "Author Two"}],
                                      narrators=["Narrator One", "Narrator Two"], translator="Translator One")
    c = external(authors=["Author Two", "Author One"], narrators=["Narrator Two", "Narrator One"],
                 series="Example Series", seriesNumber="02.50", translator="Translator One")
    r = assess(Book(raw), evidence(c))
    for f in ("authors", "narrators", "series_position", "translators"):
        assert r["fields"][f]["status"] == "Verified"
    assert r["fields"]["series_position"]["value"] == ["2.5"]


@pytest.mark.parametrize("subtitle,status", [("A real subtitle", "Unverified"), ("Example", "Conflict"),
                                            ("Example Series, Book 2.5", "Conflict")])
def test_subtitle_distinctions(subtitle, status):
    raw = copy.deepcopy(book().raw)
    raw["media"]["metadata"]["subtitle"] = subtitle
    assert assess(Book(raw))["fields"]["subtitle"]["status"] == status


def test_overlapping_actionable_rules_become_observations():
    raw = make_book(title="The Eye of the World", series="The Wheel of Time", seq="1",
                    authors=["Sarah J Maas", "Other"])
    findings = analyze(Book(raw))[1]
    fields = [f.field for f in findings if f.actionable]
    assert len(fields) == len(set(fields))
    raw = make_book(title="Harry Potter and the Goblet of Fire", series="Harry Potter", seq="4",
                    authors=["Stephen Fry"], narrators=["J.K. Rowling"])
    fields = [f.field for f in analyze(Book(raw))[1] if f.actionable]
    assert len(fields) == len(set(fields))


def test_cache_resume_cancel_offline_and_failure_retry(setup):
    store, api, aid = setup
    store.add_book(aid, book(item_id="second"))
    source = FakeSource()
    rid = start_verification(store, aid, source, ["current-id", "second"])
    run_verification(store, rid, source, limit=1)
    assert len(source.calls) == 1
    assert store.verification_runs(aid)[0]["state"] == "paused"
    store.cancel_verification(rid)
    assert store.verification_runs(aid)[0]["state"] == "cancelled"
    run_verification(Store(store.path), rid, source)
    assert len(source.calls) == 2
    assert store.verification_runs(aid)[0]["state"] == "complete"
    assert len(store.verification_report(aid)) == 2
    rid = start_verification(store, aid, source, ["current-id", "second"])
    run_verification(store, rid, source)
    assert len(source.calls) == 2
    source.fail = True
    rid = start_verification(store, aid, source, ["current-id", "second"], refresh=True)
    run_verification(store, rid, source)
    assert store.verification_runs(aid)[0]["done"] == 1
    assert store.verification_evidence(aid, "current-id")[0]["state"] == "unavailable"
    assert store.verification_evidence(aid, "current-id")[0]["previous_success"]["state"] == "ok"
    assert any(b["matches"] for b in Store(store.path).verification_report(aid))
    source.fail = False
    retry_unavailable(store, rid)
    run_verification(store, rid, source)
    assert store.verification_runs(aid)[0]["state"] == "complete"
    assert not api.writes


def test_empty_results_cached_and_cancellation_at_book_boundary(setup):
    store, _, aid = setup
    source = FakeSource()
    source.empty = True
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source, cancelled=lambda: True)
    assert not source.calls
    run_verification(store, rid, source)
    assert store.verification_evidence(aid, "current-id")[0]["state"] == "no_match"
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source)
    assert len(source.calls) == 1


def test_cache_identical_queries_once_per_run(setup):
    store, _, aid = setup
    raw = copy.deepcopy(store.book(aid, "current-id").raw)
    raw["id"] = "same-query"
    store.add_book(aid, Book(raw))
    source = FakeSource()
    rid = start_verification(store, aid, source, ["current-id", "same-query"])
    run_verification(store, rid, source)
    assert len(source.calls) == 1
    assert store.verification_runs(aid)[0]["done"] == 2


def test_scopes_and_source_binding(setup):
    store, api, aid = setup
    source = FakeSource()
    source.targeted_only = True
    with pytest.raises(ValueError):
        start_verification(store, aid, source, ["missing", "current-id"])
    source.id, source.scope = "abs:audible", "https://other.invalid"
    with pytest.raises(ValueError, match="original server"):
        start_verification(store, aid, source, ["current-id"])
    source.id, source.scope = "test", api.server
    rid = start_verification(store, aid, source, ["current-id"])
    source.scope = "other"
    with pytest.raises(ValueError, match="original provider"):
        run_verification(store, rid, source)


def test_public_and_abs_schemas_and_units():
    transport = SimpleNamespace(public_search=lambda source, query: FIXTURES[source])
    for key in ("openlibrary", "googlebooks"):
        source = PublicSource(key, transport)
        result = source.search(source.query(book()))
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["work_only"]
        assert match(book(), result["candidates"][0])["level"] == "work"
    api = SimpleNamespace(server="http://test.invalid", metadata_search=lambda p, q: FIXTURES["abs"])
    source = AbsSource(api, "audible")
    result = source.search(source.query(book()))
    assert result["candidates"][0]["duration"] == 1200
    assert result["candidates"][0]["raw"]["duration"] == 20
    custom = AbsSource(api, "custom-audible-catalog")
    assert custom.search(custom.query(book()))["candidates"][0]["work_only"]


def test_cli_providers_search_argv_and_schema(monkeypatch):
    adapter = CliAdapter(Settings("http://test.invalid", "synthetic", "fake"))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args == ["metadata", "providers"]:
            return {"providers": {"books": [{"value": "custom", "text": "Custom"}, {"value": "audible"}]}}
        return FIXTURES["abs"]

    monkeypatch.setattr(adapter, "_run", run)
    assert adapter.metadata_providers()[0]["value"] == "audible"
    assert adapter.metadata_search("audible", {"title": "B012345678", "author": "Author"}) == FIXTURES["abs"]
    assert calls[-1] == ["metadata", "search", "--provider", "audible", "--title", "B012345678", "--author", "Author"]
    monkeypatch.setattr(adapter, "_run", lambda *args: {"invalid": []})
    with pytest.raises(AdapterError):
        adapter.metadata_search("audible", {"title": "title"})


@pytest.mark.parametrize("failure", ["timeout", "rate", "bad", "auth"])
def test_public_transport_failures_no_secret_exposure(monkeypatch, failure):
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise requests.Timeout("key=secret")
        response = requests.Response()
        response.status_code = 429 if failure == "rate" else 401 if failure == "auth" else 200
        response._content = b"not-json"
        response.url = "https://test.invalid?key=secret"
        return response

    monkeypatch.setattr("abs_manager.adapter.requests.get", get)
    monkeypatch.setattr("abs_manager.adapter.time.sleep", lambda *a: None)
    with pytest.raises(AdapterError) as exc:
        PublicMetadataTransport(google_key="secret").public_search("googlebooks", {"q": "Example"})
    assert "secret" not in str(exc.value)
    assert len(calls) == (3 if failure in ("timeout", "rate") else 1)
    assert all(not c["allow_redirects"] for c in calls)
    assert all("Authorization" not in c["headers"] for c in calls)


def test_human_review_keeps_status_and_provenance(setup):
    store, api, aid = setup
    before = store.verification_report(aid)[0]["fields"]["cover"]["status"]
    store.annotate_verification(aid, "current-id", "cover", "Compared edition jacket, https://example.invalid")
    field = Store(store.path).verification_report(aid)[0]["fields"]["cover"]
    assert field["status"] == before
    assert field["human_review"]["date"]
    assert field["human_review"]["kind"] == "human review"
    assert not api.writes


def test_calibre_authoritative_readonly_import(setup):
    store, api, aid = setup
    raw = copy.deepcopy(book(item_id="ebook").raw)
    raw.update(relPath="Author/Example trunc (653)", path="Author/Example trunc (653)")
    raw["media"].update(audioFiles=[], duration=0)
    store.add_book(aid, Book(raw))
    text = json.dumps([{"id": 653, "title": "Authoritative title", "authors": ["Author One", "Author Two"],
                        "series": "Example Series", "series_index": 2.5, "identifiers": {"isbn": "9780306406157"}}])
    assert import_catalog(store, aid, text) == 1
    report = next(b for b in store.verification_report(aid) if b["item_id"] == "ebook")
    assert report["calibre_limitation"] is None
    assert report["fields"]["title"]["status"] == "Conflict"
    assert report["fields"]["title"]["provenance"][-1]["catalog_id"] == 653
    with pytest.raises(ValueError):
        store.propose(aid, "ebook", "title", "Authoritative title", "catalog")
    assert not api.writes


def test_discrepancy_proposal_is_pending_and_guards_survive(setup):
    store, api, aid = setup
    source = FakeSource()
    raw = store.book(aid, "current-id").raw
    current = Book(raw)
    # An exact recording identification supports adding a missing subtitle.
    c = candidate({"title": current.values["title"], "authors": current.values["authors"],
                   "narrators": current.values["narrators"], "duration": 1000, "subtitle": "Edition subtitle"}, "test")
    source.search = lambda query: {"raw": [], "candidates": [c]}
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source)
    fid = propose_discrepancy(store, aid, "current-id", "subtitle", c["id"])
    f = next(f for f in store.findings(aid) if f["id"] == fid)
    assert f["status"] == "pending"
    assert f["target"] == "Edition subtitle"
    from abs_manager.workflow import preview
    assert not preview(store, aid)["books"]
    with pytest.raises(ValueError):
        propose_discrepancy(store, aid, "current-id", "cover", c["id"])
    assert not api.writes


def test_csv_formula_injection():
    raw = copy.deepcopy(book().raw)
    raw["media"]["metadata"]["title"] = "=HYPERLINK()"
    assert "'=HYPERLINK()" in export_csv([assess(Book(raw))])


def test_fractional_series_discrepancy_can_be_pending(setup):
    store, api, aid = setup
    source = FakeSource()
    b = store.book(aid, "current-id")
    c = candidate({"title": b.values["title"], "authors": b.values["authors"],
                   "narrators": b.values["narrators"], "duration": 1000,
                   "series": [{"name": "Bobiverse", "sequence": "4.5"}]}, "test")
    source.search = lambda query: {"raw": [], "candidates": [c]}
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source)
    # Remove the pre-existing title proposal only; series currently has no proposal.
    fid = propose_discrepancy(store, aid, "current-id", "series", c["id"])
    assert next(f for f in store.findings(aid) if f["id"] == fid)["target"][0]["sequence"] == "4.5"
    assert not api.writes


def test_review_outcome_does_not_become_external_verification(setup):
    store, _, aid = setup
    store.annotate_verification(aid, "current-id", "cover", "Wrong edition jacket; compared source reference",
                                outcome="Confirmed error")
    f = store.verification_report(aid)[0]["fields"]["cover"]
    assert f["status"] == "Unverified"
    assert f["human_review"]["outcome"] == "Confirmed error"


@pytest.mark.parametrize("data", [{"title": {}}, {"title": "Example", "url": []}, {"title": "Example", "subtitle": 42}])
def test_malformed_candidate_fails_closed(data):
    with pytest.raises(ValueError):
        candidate(data, "test")


def test_ebook_numeric_title_folder_is_not_a_series_position():
    raw = make_book(title="1984", path="George Orwell/1984 (653)", series="Classics", seq="1", narrators=[])
    raw["media"].update(audioFiles=[], duration=0)
    assert not any(f.field in ("title", "narrators", "series") for f in analyze(Book(raw))[1])


def test_duplicate_appended_subtitle_and_explicit_missing_series():
    raw = make_book(title="Example: Real subtitle", series=None)
    raw["media"]["metadata"]["subtitle"] = "Real subtitle"
    assert assess(Book(raw))["fields"]["subtitle"]["status"] == "Conflict"
    raw["media"]["metadata"].update(title="Example: Explicit Series, Book 2.5", subtitle="")
    assert assess(Book(raw))["fields"]["series"]["status"] == "Missing"
