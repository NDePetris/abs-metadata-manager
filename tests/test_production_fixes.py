import copy
import json
import subprocess
from types import SimpleNamespace

import pytest
from conftest import FakeAdapter, make_book
from test_streamlined import app, page, widget

from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.config import Settings
from abs_manager.models import Book
from abs_manager.sources import AbsSource
from abs_manager.store import Store
from abs_manager.verification import candidate, contributor_comparison, match
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import SafetyError, Workflow, confirmation, operation_comparison, preview, run_audit


def clearing(tmp_path):
    raw = make_book(title="Title", series=None)
    raw["media"]["metadata"]["subtitle"] = "The Wheel of Time Book 10"
    api = FakeAdapter([raw])
    store = Store(tmp_path / "clear.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    store.confirm_targets(aid, raw["id"], {"subtitle": "", "title": "Title"})
    fid = next(f["id"] for f in store.findings(aid) if f["field"] == "subtitle")
    store.review([fid], "approved")
    return store, api, aid, preview(store, aid, [fid])


@pytest.mark.parametrize("observed", [None, ""])
def test_clear_verified_import_acceptance_and_guarded_rollback(tmp_path, observed):
    store, api, _aid, plan = clearing(tmp_path)
    api.mutate_after = lambda raw: raw["media"]["metadata"].update(subtitle=observed)
    wf = Workflow(store, api)
    oid = wf.apply(plan, confirmation(plan))[0]
    assert store.operation(oid)["state"] == "verified"
    new = run_audit(store, api, api.libraries()[0])
    correction = store.corrections(new)[0]
    assert correction["abs_observed"] and correction["accepted"]
    assert store.field_decisions(new, "current-id")["subtitle"]["valid"]
    assert store.field_decisions(new, "current-id")["title"]["valid"]
    back = wf.rollback_preview(oid)
    assert back["before"]["subtitle"] == observed
    api.items["current-id"]["media"]["metadata"]["subtitle"] = "external"
    with pytest.raises(SafetyError):
        wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert len(api.writes) == 1
    api.items["current-id"]["media"]["metadata"]["subtitle"] = observed
    api.mutate_after = None
    rollback_id = wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert store.operation(rollback_id)["state"] == "verified"
    assert api.items["current-id"]["media"]["metadata"]["subtitle"] == "The Wheel of Time Book 10"


@pytest.mark.parametrize("mode,expected", [("null", "verified"), ("empty", "verified"),
    ("before", "not_applied"), ("unexpected", "uncertain"), ("title", "uncertain"),
    ("path", "uncertain"), ("id", "uncertain"), ("library", "uncertain"), ("protected", "uncertain")])
def test_readonly_clear_reconciliation(tmp_path, mode, expected):
    store, api, _aid, plan = clearing(tmp_path)
    before = Book(api.item("current-id"))
    data = {**plan["books"][0], "after": {**before.values, "subtitle": ""}, "protected": before.protected()}
    oid = store.record(api.server, "library-a", "current-id", "apply", "uncertain", data)
    raw = api.items["current-id"]
    raw["media"]["metadata"]["subtitle"] = {"before": before.values["subtitle"], "unexpected": "Other",
                                               "empty": ""}.get(mode)
    if mode == "title":
        raw["media"]["metadata"]["title"] = "Other"
    if mode == "path":
        raw["path"] = "/changed"
    if mode == "library":
        raw["libraryId"] = "other"
    if mode == "id":
        raw["id"] = "other"
    if mode == "protected":
        raw["media"]["metadata"]["description"] = "changed"
    assert Workflow(store, api).reconcile(oid) == expected
    assert not api.writes
    rows = operation_comparison(data, store.operation(oid)["observed"])
    assert rows[0]["Field"] == "Subtitle"
    if mode == "null":
        assert rows[0]["Observed"] == "Cleared (ABS returned null)"
        assert rows[0]["Comparison"] == "Semantically equivalent"
    if mode in ("title", "path", "protected", "library", "id"):
        assert any(r["Comparison"] == "Material difference" for r in rows)


def test_clear_stale_source_remains_strict(tmp_path):
    store, api, _aid, plan = clearing(tmp_path)
    api.items["current-id"]["media"]["metadata"]["subtitle"] = None
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes


@pytest.mark.parametrize("left,right,expected", [
    (["Kate Reading", "Michael Kramer"], ["Michael Kramer", "Kate Reading"], "Equivalent people"),
    (["Kate Reading", "Michael Kramer"], "Michael Kramer, Kate Reading", "Equivalent people"),
    (["Kate Reading", "Michael Kramer"], " Kate  Reading ; Michael Kramer ", "Equivalent people"),
    (["Smith, John", "Jane Doe Jr."], "Jane Doe Jr.; Smith, John", "Equivalent people"),
    (["Smith, John"], ["Smith", "John"], "Ambiguous combined string"),
    (["John Smith"], ["Jane Doe"], "Different contributors"),
    (["A B", "C"], "A, B, D", "Ambiguous combined string"),
    (["A", "A"], "A, A", "Ambiguous combined string"),
    ([], ["Jane Doe"], "Missing contributor information"),
])
def test_contributor_comparison(left, right, expected):
    original = copy.deepcopy((left, right))
    assert contributor_comparison(left, right).startswith(expected)
    assert (left, right) == original


def test_combined_credit_does_not_establish_recording():
    book = Book(make_book(title="Title", narrators=["Kate Reading", "Michael Kramer"]))
    raw = {"title": "Title", "author": book.values["authors"], "narrator": "Kate Reading, Michael Kramer"}
    item = candidate(raw, "abs:audible")
    result = match(book, item)
    assert "narrators" in result["signals"] and result["level"] != "recording"
    assert item["values"]["narrators"] == [raw["narrator"]]


def test_comparison_does_not_normalize_unapproved_subtitle_or_other_fields():
    from abs_manager.models import result_matches
    assert not result_matches({"subtitle": ""}, {"subtitle": None}, {})
    assert not result_matches({"title": ""}, {"title": None}, {"title": ""})
    assert not result_matches({"subtitle": None}, {"subtitle": ""}, {"subtitle": None})


@pytest.mark.parametrize("stderr,category", [("HTTP 401 Authorization: Bearer secret", "authorization"),
    ("HTTP 503 https://secret.invalid/?key=secret", "transient_http"), ("ETIMEDOUT secret", "timeout"),
    ("ENOTFOUND secret", "connection_reset")])
def test_cli_failure_persisted_safely(setup, monkeypatch, stderr, category):
    store, api, aid = setup
    adapter = CliAdapter(Settings(api.server, "secret", "fake-cli", .1, 3))
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=2, stderr=stderr, stdout="")
    monkeypatch.setattr(subprocess, "run", run)
    source = AbsSource(adapter, "audible")
    rid = start_verification(store, aid, source, ["current-id"])
    run_verification(store, rid, source)
    result = store.verification_evidence(aid, "current-id")[0]
    assert result["state"] == "unavailable" and result["diagnostic"]["category"] == category
    assert "secret" not in json.dumps(result)
    assert len(calls) == 1 and not store.history() and not store.corrections(aid)


@pytest.mark.parametrize("response,state", [([], "no_match"), ([{"title": "Title"}], "ok"),
                                           ({"matches": []}, "unavailable"), ([None], "unavailable")])
def test_search_schemas(setup, monkeypatch, response, state):
    store, api, aid = setup
    adapter = CliAdapter(Settings(api.server, "secret", "fake-cli", .1, 3))
    monkeypatch.setattr(adapter, "_run", lambda args: response)
    source = AbsSource(adapter, "audible")
    run_verification(store, start_verification(store, aid, source, ["current-id"]), source)
    assert store.verification_evidence(aid, "current-id")[0]["state"] == state


def test_ui_discovery_failure_search_switch_and_drafts(tmp_path, monkeypatch):
    store, api, _aid, plan = clearing(tmp_path)
    calls = []
    def discover():
        calls.append("discover")
        if len(calls) == 1:
            raise AdapterError("secret", {"category": "authorization", "stderr": "HTTP 401"})
        return [{"value": "audible", "text": "Audible"}, {"value": "openlibrary", "text": "Open Library"}]
    def search(provider, query):
        calls.append("search")
        raise AdapterError("secret", {"category": "timeout", "stderr": "ETIMEDOUT"})
    api.metadata_providers, api.metadata_search = discover, search
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert any("not discovered" in c.value for c in at.caption)
    widget(at, "text_input", "Desired subtitle").set_value("Unsaved draft").run()
    widget(at, "button", "Discover / Refresh ABS Providers").click().run()
    assert any("authorization error" in c.value for c in at.warning)
    widget(at, "button", "Discover / Refresh ABS Providers").click().run()
    widget(at, "selectbox", "Source").set_value("abs:audible").run()
    widget(at, "button", "Search").click().run()
    assert any("timeout" in c.value for c in at.warning)
    widget(at, "selectbox", "Source").set_value("openlibrary").run()
    assert widget(at, "text_input", "Desired subtitle").value == "Unsaved draft"
    assert len(calls) == 3 and not at.exception
    assert preview(store, _aid, plan["selected"]) == plan and not api.writes


def test_ui_readonly_reconcile(tmp_path, monkeypatch):
    store, api, _aid, plan = clearing(tmp_path)
    before = Book(api.item("current-id"))
    data = {**plan["books"][0], "after": {**before.values, "subtitle": ""}, "protected": before.protected()}
    oid = store.record(api.server, "library-a", "current-id", "apply", "uncertain", data)
    api.items["current-id"]["media"]["metadata"]["subtitle"] = None
    at = app(store, api, monkeypatch)
    page(at, "History")
    assert any("Not observed" in str(d.value) for d in at.dataframe)
    widget(at, "button", "Reconcile using live metadata (read-only)").click().run()
    assert store.operation(oid)["state"] == "verified" and not api.writes
    assert not at.exception


def test_ui_equivalent_narrators_and_acceptance(tmp_path, monkeypatch):
    raw = make_book(title="Title", narrators=["Kate Reading", "Michael Kramer"])
    api = FakeAdapter([raw])
    store = Store(tmp_path / "names.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    api.metadata_search = lambda provider, query: [{"title": "Title", "author": ["Dennis E. Taylor"],
        "narrator": "Michael Kramer, Kate Reading", "duration": 1000 / 60}]
    source = AbsSource(api, "audible")
    run_verification(store, start_verification(store, aid, source, ["current-id"]), source)
    store.confirm_targets(aid, "current-id", {"narrators": raw["media"]["metadata"]["narrators"]})
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    assert any("Equivalent people" in c.value for c in at.caption)
    assert store.field_decisions(aid, "current-id")["narrators"]["valid"]
    assert not at.exception and not api.writes


def test_accepted_empty_survives_later_null_import(tmp_path):
    store, api, _aid, plan = clearing(tmp_path)
    Workflow(store, api).apply(plan, confirmation(plan))
    first = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(first, "current-id")["subtitle"]["valid"]
    api.items["current-id"]["media"]["metadata"]["subtitle"] = None
    second = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(second, "current-id")["subtitle"]["valid"]
