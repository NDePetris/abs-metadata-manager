import copy

from conftest import make_book

from abs_manager.models import Book
from abs_manager.verification import assess, candidate, match
from abs_manager.verification_workflow import run_verification, start_verification


def sample():
    return Book(make_book(title="Example", authors=["Author"], narrators=["Narrator"]))


def entry(**changes):
    return candidate(
        {"title": "Example", "authors": ["Author"], "narrators": ["Narrator"], "duration": 1000, **changes},
        "catalog",
    )


def test_recording_contradictions_and_wrong_work():
    b = sample()
    for changes in ({"narrators": ["Someone Else"]}, {"duration": 2500}, {"abridged": True}):
        raw = copy.deepcopy(b.raw)
        raw["media"]["metadata"]["abridged"] = False
        m = match(Book(raw), entry(**changes))
        assert m["classification"] == "different_recording"
    assert match(b, entry(title="Other Book"))["classification"] == "wrong_work"


def test_isbn_does_not_prove_recording_or_allow_recording_conflicts():
    b = sample()
    b.raw["media"]["metadata"].update(isbn="9780306406157", subtitle="Saved subtitle")
    c = entry(isbn="9780306406157", duration=None, narrators=[], subtitle="Different subtitle")
    assert match(b, c)["classification"] == "recording_uncertain"
    result = assess(b, [{"state": "ok", "candidates": [c]}])
    assert result["fields"]["subtitle"]["status"] != "Conflict"
    assert result["fields"]["identifiers"]["status"] != "Verified"


def test_rejection_excludes_candidate_but_preserves_evidence():
    b = sample()
    c = entry()
    report = assess(b, [{"state": "ok", "candidates": [c]}], rejected={c["id"]})
    assert report["matches"][0]["rejected"]
    assert report["fields"]["narrators"]["status"] != "Verified"
    assert entry()["id"] == c["id"]


def test_human_can_select_conflicting_recording_without_independent_verification():
    b = sample()
    c = entry(narrators=["Other Narrator"])
    evidence = [{"state": "ok", "candidates": [c]}]
    report = assess(b, evidence, selected=c["id"])
    assert report["match_classification"] == "different_recording"
    assert report["fields"]["narrators"]["status"] == "Conflict"
    assert report["fields"]["title"]["status"] == "Consistent"
    assert "not independent" in report["fields"]["title"]["reason"]


def test_invalid_asin_does_not_establish_or_disprove_recording():
    b = sample()
    b.raw["media"]["metadata"]["asin"] = "invalid"
    c = entry(asin="invalid", duration=None, narrators=[])
    result = match(b, c)
    assert result["classification"] == "recording_uncertain"
    assert "asin" not in result["signals"] + result["contradictions"]
    b.raw["media"]["metadata"]["asin"] = " b012345678 "
    c = entry(asin="B012345678", duration=None, narrators=[])
    assert match(b, c)["classification"] == "correct_recording"


def test_refined_query_uses_cache_and_resume(setup):
    store, _, audit_id = setup

    class Source:
        id, scope, targeted_only = "test", "test", False

        def __init__(self):
            self.calls = []

        def query(self, book):
            return {"title": book.values["title"]}

        def search(self, query):
            self.calls.append(query)
            return {"candidates": []}

    source = Source()
    for _ in range(2):
        run = start_verification(
            store, audit_id, source, ["current-id"], query_overrides={"current-id": {"title": "Alternate"}}
        )
        run_verification(store, run, source)
    assert source.calls == [{"title": "Alternate"}]


def test_refinement_replaces_old_provider_constraints(setup):
    store, _, audit_id = setup

    class Source:
        targeted_only = False

        def __init__(self, source_id, scope):
            self.id, self.scope = source_id, scope
            self.calls = []

        def query(self, book):
            return {"title": "Old title", "author": "Old author"}

        def search(self, query):
            self.calls.append(query)
            return {"candidates": []}

    abs_source = Source("abs:audible", store.audit(audit_id)["server"])
    run = start_verification(
        store, audit_id, abs_source, ["current-id"], query_overrides={"current-id": {"title": "B012345678"}}
    )
    run_verification(store, run, abs_source)
    assert abs_source.calls == [{"title": "B012345678"}]
    public = Source("openlibrary", "public")
    run = start_verification(
        store, audit_id, public, ["current-id"], query_overrides={"current-id": {"q": "isbn:9780306406157"}}
    )
    run_verification(store, run, public)
    assert public.calls == [{"q": "isbn:9780306406157"}]
