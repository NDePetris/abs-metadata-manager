import copy
import io
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from conftest import TEST_TOKEN, FakeAdapter, make_book, simulator

from absmm import cli
from absmm.adapter import AdapterError, HttpAdapter, membership
from absmm.bulk import Bulk
from absmm.files import data_directory, read_json, strict_json
from absmm.journal import records
from absmm.locking import BusyError, operation_lock
from absmm.models import compact, digest
from absmm.review import load_approval, review


def put(path, value):
    path.write_text(compact(value), encoding="utf-8")
    return path


def prepare(tmp_path, api=None):
    api = api or FakeAdapter()
    bulk = Bulk(api, tmp_path)
    export, _ = bulk.export("library-a")
    raw = read_json(export)
    document = {"schema_version": 2, "snapshot_id": raw["snapshot_id"], "items": []}
    return bulk, api, raw, document


def propose(doc, book, field, target, **overrides):
    item = next((item for item in doc["items"] if item["item_id"] == book["item_id"]), None)
    if item is None:
        item = {"item_id": book["item_id"], "identity": book["identity"], "changes": {}}
        doc["items"].append(item)
    change = {
        "before": book["current"][field], "after": target, "reason": "Check reference",
        "evidence": "Publisher catalog, audiobook record", "confidence": "high", "source": "ai_review",
    }
    change.update(overrides)
    item["changes"][field] = change


def generate(directory, doc):
    report, _ = review(directory, put(directory / "corrections.json", doc))
    return report, read_json(report.with_suffix(".json"))


def approve(directory, manifest, rows=None):
    if rows is None:
        rows = [{"change_id": row["change_id"]} for row in manifest["rows"]]
    return put(directory / "approvals.json", {"manifest_digest": digest(manifest), "approvals": rows})


def apply(bulk, approvals, limit=None, library=None, confirms=None, backup=None):
    values = load_approval(bulk.directory, approvals, limit)
    return bulk.apply(*values, library, confirms or (lambda *_: True), backup or (lambda: True))


def test_complete_cli_and_http_workflow(tmp_path, monkeypatch, capsys):
    api = FakeAdapter()
    with simulator(api) as url:
        monkeypatch.setenv("ABS_URL", url)
        monkeypatch.setenv("ABS_TOKEN", TEST_TOKEN)
        monkeypatch.delenv("ABS_LIBRARY", raising=False)
        monkeypatch.setattr(cli, "data_directory", lambda: tmp_path)
        assert cli.main(["export", "--list-libraries"]) == 0
        assert "library-a  Audiobooks" in capsys.readouterr().out
        assert cli.main(["export", "--library", "library-a"]) == 0
        exported = read_json(next(tmp_path.glob("export-*.json")))
        doc = {"schema_version": 2, "snapshot_id": exported["snapshot_id"], "items": []}
        book = exported["books"][0]
        propose(doc, book, "title", "Original proposal")
        propose(doc, book, "tags", ["Fantasy"])
        propose(doc, book, "series", book["current"]["series"] + [{"name": "Cosmere", "sequence": None}])
        assert cli.main(["review", str(put(tmp_path / "corrections.json", doc))]) == 0
        manifest = read_json(next(tmp_path.glob("review-*.json")))
        selected = [{"change_id": row["change_id"]} for row in manifest["rows"]]
        selected[-1]["edit"] = "Roshar’s divided peoples"
        approvals = approve(tmp_path, manifest, selected)
        prompts = []
        monkeypatch.setattr(cli, "ask", lambda phrase: prompts.append(phrase) or True)
        journal = tmp_path / "writes.jsonl"
        api.check_journal = lambda: list(records(journal).values())[-1]["state"] == "intent" or pytest.fail(
            "Intent was not persisted before PATCH"
        )
        assert cli.main(["apply", str(approvals), "--limit", "1"]) == 0
        assert prompts[0].startswith("APPLY ") and prompts[1] == "BACKUP CONFIRMED"
        assert "Roshar’s divided peoples" in capsys.readouterr().out
        patches = [call for call in api.http_calls if call[0] == "PATCH"]
        assert patches == [("PATCH", "/api/items/current-id/media", {
            "metadata": {
                "title": "Roshar’s divided peoples",
                "series": [{"name": "Bobiverse", "sequence": "4"}, {"name": "Cosmere", "sequence": None}],
            },
            "tags": ["Fantasy"],
        })]
        assert cli.main(["apply", str(approvals)]) == 0
        assert cli.main(["check"]) == 0
        assert len(api.writes) == 1


@pytest.mark.parametrize(("failure", "state"), [("before", "not_applied"), ("after", "verified")])
def test_timeout_recovery_and_latest_state(tmp_path, failure, state):
    api = FakeAdapter()
    with simulator(api) as url:
        bulk = Bulk(HttpAdapter(url, TEST_TOKEN), tmp_path)
        path, _ = bulk.export("library-a")
        exported = read_json(path)
        doc = {"schema_version": 2, "snapshot_id": exported["snapshot_id"], "items": []}
        propose(doc, exported["books"][0], "title", "Correct title")
        _, manifest = generate(tmp_path, doc)
        approvals = approve(tmp_path, manifest)
        api.fail = failure
        assert apply(bulk, approvals)[0][1] == "uncertain"
        with pytest.raises(ValueError, match="Unresolved"):
            apply(Bulk(HttpAdapter(url, TEST_TOKEN), tmp_path), approvals)
        assert bulk.check()[0][1] == state
        assert len(api.writes) == 1
        api.fail = None
        assert apply(bulk, approvals)[0][1] == ("already_applied" if state == "verified" else "verified")


def test_transport_disables_redirects_proxies_retries_and_redacts(monkeypatch):
    api = FakeAdapter()
    api.fail = "redirect"
    monkeypatch.setenv("HTTP_PROXY", "http://not-a-local-proxy.invalid:7777")
    with simulator(api) as url:
        adapter = HttpAdapter(url, TEST_TOKEN)
        assert adapter.session.trust_env is False
        with pytest.raises(AdapterError, match="HTTP 307"):
            adapter.update("current-id", {"tags": []})
        assert api.http_calls == [("PATCH", "/api/items/current-id/media", {"tags": []})]
        with pytest.raises(AdapterError, match="HTTP 401") as error:
            HttpAdapter(url, "wrong-key").libraries()
        assert TEST_TOKEN not in str(error.value)


def test_inventory_export_privacy_and_second_membership(tmp_path):
    api = FakeAdapter([make_book(item_id=f"book-{i}") for i in range(55)])
    bulk, _, exported, _ = prepare(tmp_path, api)
    assert len(exported["books"]) == len(api.reads) == 55
    assert not any(key in compact(exported) for key in (api.server, "/books/", "inode-1", "oldLibraryItemId"))
    assert "/books/" in next(tmp_path.glob("snapshot-*.json")).read_text()
    original = api.item

    def item_that_changes_membership(item_id):
        if item_id == "book-54":
            api.items["new-book"] = make_book(item_id="new-book")
        return original(item_id)

    api.item = item_that_changes_membership
    with pytest.raises(ValueError, match="Library changed"):
        bulk.export("library-a")
    api.items["book-5"]["libraryId"] = "wrong-library"
    with pytest.raises(AdapterError, match="wrong-library"):
        membership(api, "library-a")


@pytest.mark.parametrize("change", ["protected", "path", "stale", "ebook"])
def test_live_conflicts_skip_one_book_and_continue(tmp_path, change):
    api = FakeAdapter([make_book(item_id="a"), make_book(item_id="b")])
    bulk, _, exported, doc = prepare(tmp_path, api)
    for book in exported["books"]:
        propose(doc, book, "title", "Correct title")
    _, manifest = generate(tmp_path, doc)
    if change == "protected":
        api.items["a"]["media"]["tags"] = ["unknown"]
    elif change == "path":
        api.items["a"]["relPath"] = "renamed"
    elif change == "stale":
        api.items["a"]["media"]["metadata"]["title"] = "Changed externally"
    else:
        api.items["a"]["media"]["audioFiles"] = []
    assert [result[1] for result in apply(bulk, approve(tmp_path, manifest))] == ["conflict", "verified"]
    assert [item for item, _ in api.writes] == ["b"]


def test_flags_and_unsupported_corrections(tmp_path):
    mixed = make_book(item_id="mixed", authors=["First, Jr.", "Second"])
    mixed["media"]["ebookFile"] = {"ino": "ebook-1"}
    mixed["media"]["metadata"]["publishedYear"] = "circa 2015"
    ebook = make_book(item_id="ebook")
    ebook["media"].update(audioFiles=[], ebookFile={"ino": "ebook-2"})
    bulk, _, exported, doc = prepare(tmp_path, FakeAdapter([mixed, ebook]))
    first, second = exported["books"]
    assert [book["kind"] for book in (first, second)] == ["mixed", "ebook"]
    propose(doc, first, "authors", ["Second", "First, Jr."])
    propose(doc, first, "publishedYear", "2015")
    propose(doc, first, "title", "Mixed title")
    propose(doc, second, "title", "Ebook title")
    _, manifest = generate(tmp_path, doc)
    order = next(row for row in manifest["rows"] if row["field"] == "authors")
    year = next(row for row in manifest["rows"] if row["field"] == "publishedYear")
    title = next(row for row in manifest["rows"] if row["field"] == "title" and row["item_id"] == "mixed")
    assert order["blocked"] and "Author order" in " ".join(order["flags"])
    assert year["blocked"] and "cannot be safely restored" in " ".join(year["flags"])
    assert not title["selected"] and not title["blocked"]
    with pytest.raises(ValueError, match="unsupported"):
        apply(bulk, approve(tmp_path, manifest))
    assert apply(bulk, approve(tmp_path, manifest, [{"change_id": title["change_id"]}]))[0][1] == "verified"


def test_author_membership_verification_reports_reordered_names(tmp_path):
    bulk, api, exported, doc = prepare(tmp_path)
    propose(doc, exported["books"][0], "authors", ["First Writer", "Second Writer"])
    _, manifest = generate(tmp_path, doc)
    assert not manifest["rows"][0]["selected"]
    api.mutate_after = lambda item: item["media"]["metadata"]["authors"].reverse()
    result = apply(bulk, approve(tmp_path, manifest))[0]
    assert result[1] == "verified"
    assert "Author membership verified. Author order differs" in result[2]


def test_series_removal_and_manual_edit_validation(tmp_path):
    bulk, _, exported, doc = prepare(tmp_path)
    book = exported["books"][0]
    propose(doc, book, "series", [])
    with pytest.raises(ValueError, match="explicit reason"):
        generate(tmp_path, doc)
    propose(doc, book, "series", [], reason="Remove the incorrect Bobiverse membership")
    _, manifest = generate(tmp_path, doc)
    assert "Removes a series membership" in manifest["rows"][0]["flags"]
    assert manifest["rows"][0]["selected"] is False
    propose(doc, book, "series", book["current"]["series"] + [{"name": "Cosmere", "sequence": None}])
    _, manifest = generate(tmp_path, doc)
    change_id = manifest["rows"][0]["change_id"]
    for edit, error in [(None, "raw text"), ("[", "Expecting"), ("[]", "new series removal")]:
        with pytest.raises((ValueError, TypeError), match=error):
            apply(bulk, approve(tmp_path, manifest, [{"change_id": change_id, "edit": edit}]))


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', r'{"x":"\ud800"}'])
def test_strict_json(text):
    with pytest.raises(ValueError):
        strict_json(text)


def test_snapshot_and_manifest_validation(tmp_path):
    bulk, api, exported, doc = prepare(tmp_path)
    propose(doc, exported["books"][0], "title", "Correct title")
    _, manifest = generate(tmp_path, doc)
    change_id = manifest["rows"][0]["change_id"]
    for ids in [[change_id, change_id], ["a" * 64]]:
        with pytest.raises(ValueError, match="Duplicate or unknown"):
            apply(bulk, approve(tmp_path, manifest, [{"change_id": key} for key in ids]))
    approvals = approve(tmp_path, manifest)
    with pytest.raises(ValueError, match="another ABS server or library"):
        apply(bulk, approvals, library="other-library")
    api.server = "http://other.invalid"
    with pytest.raises(ValueError, match="another ABS server or library"):
        apply(bulk, approvals)
    changed = copy.deepcopy(manifest)
    changed["rows"][0]["after"] = "Tampered"
    put(tmp_path / f"review-{digest(manifest)}.json", changed)
    with pytest.raises(ValueError, match="Manifest digest"):
        apply(bulk, approvals)
    generate(tmp_path, doc)
    path = next(tmp_path.glob("snapshot-*.json"))
    snapshot = read_json(path)
    snapshot["books"]["current-id"]["media"]["coverPath"] = "changed"
    put(path, snapshot)
    with pytest.raises(ValueError, match="Manifest differs"):
        apply(bulk, approvals)
    doc["items"][0]["changes"]["title"]["before"] = "Wrong"
    with pytest.raises(ValueError, match="Before value"):
        generate(tmp_path, doc)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.embedded = []
        self.in_embedded = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(attrs)
            self.in_embedded = ("id", "review-data") in attrs

    def handle_data(self, data):
        if self.in_embedded:
            self.embedded.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_embedded = False


def test_hostile_metadata_remains_inert_and_complete(tmp_path):
    attack = '</ScRiPt><script src="https://bad.invalid/x"></script><img onerror="alert(1)">'
    _, _, exported, doc = prepare(tmp_path, FakeAdapter([make_book(title=attack)]))
    full = attack + "\n\n" + "Full publisher description " * 500
    propose(doc, exported["books"][0], "description", full, reason=attack)
    report, manifest = generate(tmp_path, doc)
    document = report.read_text(encoding="utf-8")
    scripts = Scripts()
    scripts.feed(document)
    assert len(scripts.scripts) == 2
    assert attack not in document
    assert "innerHTML" not in document and "fetch(" not in document
    embedded = json.loads("".join(scripts.embedded))
    assert embedded["manifest_digest"] == digest(manifest)
    assert embedded["rows"][0]["title"] == attack
    assert embedded["rows"][0]["after"] == full
    assert not embedded["rows"][0]["selected"]


@pytest.mark.parametrize(
    ("observed", "state"), [
        ("<p>Roshar’s divided peoples.</p><p>News &amp; views.</p>", "verified"),
        ("<p>RosharΓÇÖs divided peoples.</p><p>News &amp; views.</p>", "uncertain"),
        ("<p>Roshar’s divided peoples.</p><p>Material change.</p>", "uncertain"),
    ],
)
def test_description_write_readback_and_abandonment(tmp_path, observed, state):
    bulk, api, exported, doc = prepare(tmp_path)
    propose(doc, exported["books"][0], "description", "Roshar’s divided peoples.\n\nNews & views.")
    _, manifest = generate(tmp_path, doc)
    api.mutate_after = lambda item: item["media"]["metadata"].update(description=observed)
    assert apply(bulk, approve(tmp_path, manifest))[0][1] == state
    if state == "uncertain":
        op_id = next(iter(records(bulk.journal)))
        assert bulk.check()[0][1] == "uncertain"
        with pytest.raises(ValueError, match="Explicit abandonment"):
            bulk.abandon(op_id, "Inspected manually", lambda *_: False)
        bulk.abandon(op_id, "Inspected manually", lambda *_: True)
        assert records(bulk.journal)[op_id]["state"] == "abandoned"
        assert "Inspected manually" in bulk.journal.read_text(encoding="utf-8")
        assert len(api.writes) == 1


def test_cwd_does_not_change_journal_and_lock_is_cross_process(tmp_path, monkeypatch):
    before = data_directory()
    monkeypatch.chdir(tmp_path)
    assert data_directory() == before
    lock_path = tmp_path / "workspace"
    script = (
        "import sys\nfrom absmm.locking import operation_lock, BusyError\n"
        "try:\n with operation_lock(sys.argv[1]): pass\nexcept BusyError:\n sys.exit(17)\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    with operation_lock(lock_path):
        result = subprocess.run([sys.executable, "-c", script, str(lock_path)], env=environment, check=False)
    assert result.returncode == 17
    with operation_lock(lock_path), pytest.raises(BusyError), operation_lock(lock_path):
        pass


def test_wrong_confirmation_noninteractive_and_corrupt_journal(tmp_path, monkeypatch):
    bulk, api, exported, doc = prepare(tmp_path)
    propose(doc, exported["books"][0], "title", "Correct title")
    _, manifest = generate(tmp_path, doc)
    approvals = approve(tmp_path, manifest)
    with pytest.raises(ValueError, match="Confirmation"):
        apply(bulk, approvals, confirms=lambda *_: False)
    assert apply(bulk, approvals, backup=lambda: False)[0][1] == "not_attempted"
    monkeypatch.setattr(sys, "stdin", io.StringIO("APPLY something\n"))
    with pytest.raises(ValueError, match="interactive terminal"):
        cli.ask("APPLY something")
    bulk.journal.write_text('{"op_id":', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid or incomplete"):
        apply(bulk, approvals)
    assert not api.writes

def test_typed_edits_and_limit_count_distinct_books(tmp_path):
    api = FakeAdapter([make_book(item_id="b"), make_book(item_id="a")])
    api.items["a"]["media"]["metadata"]["asin"] = "B012345678"
    bulk, _, exported, doc = prepare(tmp_path, api)
    for book in exported["books"]:
        propose(doc, book, "title", "New title")
        propose(doc, book, "tags", ["Proposed"])
    propose(doc, exported["books"][1], "asin", "B987654321")
    _, manifest = generate(tmp_path, doc)
    edits = {"asin": "null", "tags": '["Fantasy", "High Fantasy"]'}
    rows = []
    for row in reversed(manifest["rows"]):
        selected = {"change_id": row["change_id"]}
        if row["field"] in edits:
            selected["edit"] = edits[row["field"]]
        rows.append(selected)
    approvals = approve(tmp_path, manifest, rows)
    captured = []

    def confirm(plan, phrase):
        assert phrase == "APPLY " + digest(plan)
        captured.append(plan)
        return True

    results = apply(bulk, approvals, limit=1, confirms=confirm)
    assert [entry[:2] for entry in results] == [("a", "verified"), ("b", "not_attempted")]
    assert api.writes == [("a", {"title": "New title", "tags": ["Fantasy", "High Fantasy"], "asin": None})]
    assert captured[0]["items"]["a"]["asin"]["after"] is None


def test_duplicate_items_invalid_fields_and_missing_factual_evidence(tmp_path):
    _, _, exported, doc = prepare(tmp_path)
    book = exported["books"][0]
    propose(doc, book, "authors", ["Someone Else"], evidence="")
    with pytest.raises(ValueError, match="require evidence"):
        generate(tmp_path, doc)
    propose(doc, book, "authors", ["Someone Else"])
    doc["items"].append(copy.deepcopy(doc["items"][0]))
    with pytest.raises(ValueError, match="Duplicate or unknown item"):
        generate(tmp_path, doc)
    doc["items"].pop()
    doc["items"][0]["changes"]["coverPath"] = doc["items"][0]["changes"]["authors"]
    with pytest.raises(ValueError, match="read-only"):
        generate(tmp_path, doc)
