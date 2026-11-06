import json
from pathlib import Path

import pytest
from conftest import FakeAdapter, make_book
from test_persistent_state import snapshot
from test_quality_policy import clean, reference
from test_streamlined import app, page, widget
from test_verification import FakeSource

from abs_manager.calibre import import_catalog
from abs_manager.models import FIELDS, Book, digest, payload
from abs_manager.persistent_state import identity
from abs_manager.review_export import export_review_bundle
from abs_manager.store import Store
from abs_manager.verification_workflow import run_verification, start_verification


@pytest.fixture
def review_books():
    audio = make_book(title="The Étoile", item_id="a", seq="2.50", authors=["Áda", "David"],
                      narrators=["Renée", "Liu"], path="/secret-files/audio")
    media = audio["media"]
    metadata = media["metadata"]
    metadata.update(
        description="<p>Étoile — full description.</p>", descriptionPlain="Étoile — full description.",
        publisher="Éditions", publishedYear="2024", publishedDate="2024-01-03", isbn="9780140328721",
        asin="B012345678", abridged=False, edition="Unabridged", translators=["Júlia"],
        contributors=[{"name": "Júlia", "role": "translator"}, {"name": "Aki", "role": "editor"}],
        identifiers={"oclc": "12345"}, custom={"null": None, "blank": "", "list": [], "labels": ["漢字"]},
    )
    metadata["authors"][0]["role"] = "author"
    metadata["series"].append({"name": "宇宙", "sequence": "0.5"})
    media["tags"] = ["  keep as-is  ", "漢字"]
    media["chapters"] = [{"id": 0, "title": "Début", "start": 0, "end": 12.25, "position": 1,
                          "path": "/secret-files/chapter"}]
    media["audioFiles"][0].update(index=1, duration=1000.25, codec="aac", embeddedCoverArt="mjpeg",
                                  metadata={"path": "/secret-files/audio", "filename": "secret-filename"},
                                  metaTags={"tagTitle": "Alternate title", "tagDescription": "Alternate description",
                                            "tagISBN": "9780140328721", "tagAPIKey": "secret-tag"})
    audio["userMediaProgress"] = {"userId": "secret-user", "currentTime": 12.25}
    audio["bookmarks"] = [{"title": "secret-bookmark"}]
    metadata["identifiers"]["accessToken"] = "secret-identifier"
    metadata["custom"]["authorization"] = "secret-header"
    metadata["custom"]["filePath"] = "secret-path"
    metadata["custom"]["nested"] = [{"accessToken": "secret-token", "editionNote": "Retain me"}]
    metadata["custom"]["source"] = "https://example.invalid/book?api_key=secret-url"
    metadata["custom"]["localRef"] = "C:/secret-files/ref"

    ebook = clean("z", False)
    del ebook["media"]["metadata"]["subtitle"]
    ebook["media"]["ebookFile"] = {"ebookFormat": "epub", "metadata": {"ext": ".epub", "data": "secret-bytes",
                                                                      "path": "/secret-files/Example (42).epub"}}
    ebook["relPath"] = "/secret-files/Example (42)/Example.epub"
    return audio, ebook


def test_complete_metadata_evidence_and_provenance(tmp_path, review_books):
    audio, ebook = review_books
    store = Store(tmp_path / "test.sqlite")
    aid = snapshot(store, [ebook, audio])
    store.confirm_targets(aid, "a", {"subtitle": "Proposed subtitle"})
    subtitle = next(f for f in store.findings(aid) if f["field"] == "subtitle" and f["kind"] == "confirmed_target")
    store.review([subtitle["id"]], "approved")
    store.decide_category(aid, "genres", "Science Fiction", "Unwanted")
    assert import_catalog(store, aid, json.dumps([{"id": 42, "title": "Example", "authors": ["Author One"],
                                                 "uuid": "calibre-book", "secret": "secret-catalog-body"}]),
                          library="/secret-files/calibre") == 1
    before = Path(store.path).read_bytes()
    data = export_review_bundle(store, aid, exported_at="2026-11-05T12:00:00+00:00")
    assert "漢字".encode() in data and b"secret-" not in data and b"li_old_123" not in data
    assert before == Path(store.path).read_bytes()
    result = json.loads(data)
    assert result["schema_version"] == 1
    assert result["source"]["snapshot_id"] == aid
    assert result["source"]["library_id"] == "library-a"
    assert result["source"]["library_scope_fingerprint"] == digest(["test", "library-a"])
    assert result["source"]["book_count"] == 2
    first, second = result["books"]
    assert [first["item_id"], second["item_id"]] == ["a", "z"]
    assert first["identity_digest"] == identity(Book(audio))
    assert first["managed_values_digest"] == digest(Book(audio).values)
    assert first["observation"]["fresh_abs"] is True
    current = first["current"]
    expected = {**audio["media"]["metadata"], "identifiers": {"oclc": "12345"}, "custom": {
        "null": None, "blank": "", "list": [], "labels": ["漢字"],
        "nested": [{"editionNote": "Retain me"}], "source": "[redacted]", "localRef": "[redacted]",
    }}
    assert current["metadata"] == expected
    assert current["tags"] == audio["media"]["tags"]
    assert current["duration"] == 1000
    assert current["cover"] == {"present": True}
    assert current["chapters"] == [{"id": 0, "title": "Début", "start": 0, "end": 12.25, "position": 1}]
    assert first["evidence"]["source_files"]["audio_files"] == [{
        "index": 1, "duration": 1000.25, "codec": "aac",
        "embedded_tags": {"tagTitle": "Alternate title", "tagDescription": "Alternate description",
                          "tagISBN": "9780140328721"},
    }]
    assert first["review_values"] == {
        **Book(audio).values,
        "description": {"$ref": "#/current/metadata/description"},
        "genres": ["Science Fiction"], "tags": ["  keep as-is  ", "漢字"],
        "translators": [{"name": "Júlia"}],
        "contributors": [{"name": "Júlia", "role": "translator"}, {"name": "Aki", "role": "editor"}],
        "publication_year": "2024", "publication_date": "2024-01-03", "publisher": "Éditions", "language": "en",
        "isbn": "9780140328721", "asin": "B012345678", "identifiers": {"oclc": "12345"},
        "edition": "Unabridged", "abridged": False, "cover_present": True,
        "chapters": [{"title": "Début", "start": 0, "end": 12.25, "position": 1}],
    }
    assert data.count(current["metadata"]["description"].encode()) == 1
    managed = {field: first["review_values"][field] for field in FIELDS if field != "subtitle"}
    assert payload({**managed, "subtitle": "New subtitle"})["metadata"] == {
        **managed, "subtitle": "New subtitle", "authors": [{"name": "Áda"}, {"name": "David"}],
    }
    assert "cover" not in first["evidence"]["quality"]
    assert first["evidence"]["quality"]["genres"]["reason_code"] == "category_problem"
    assert first["existing_corrections"] == [{"field": "subtitle", "proposed": "Proposed subtitle"}]
    assert "subtitle" not in second["current"]["metadata"]
    assert {"subtitle", "narrators", "chapters"}.isdisjoint(second["review_values"])
    assert second["current"]["ebook_format"] == {"ebookFormat": "epub", "ext": ".epub"}
    assert second["evidence"]["calibre_binding"]["calibre_id"] == 42
    assert second["evidence"]["references"]["title"][0]["source"] == "Calibre catalog"
    assert result["authority"]["audiobook"]["guarded_write_fields"] == list(FIELDS)
    assert result["authority"]["audiobook"]["other_fields"] == "native_or_manual"
    assert result["authority"]["ebook"]["corrections"] == "Calibre"
    assert result["authority"]["ebook"]["guarded_write_fields"] == []
    assert result["category_preferences"] == [{"field": "genres", "value": "Science Fiction", "decision": "Unwanted"}]
    assert export_review_bundle(store, aid, exported_at=result.pop("exported_at")) == data
    assert json.loads(export_review_bundle(store, aid))["content_digest"] == result["content_digest"]
    assert result.pop("content_digest") == digest(result)


def test_review_values_preserve_absence_null_and_empty(tmp_path):
    missing, null, empty = (clean(name) for name in ("absent", "null", "empty"))
    for field in ("subtitle", "description", "genres"):
        del missing["media"]["metadata"][field]
    for field in ("tags", "chapters", "coverPath"):
        del missing["media"][field]
    null["media"]["metadata"].update(subtitle=None, description=None, genres=None, translators=None)
    null["media"].update(tags=None, coverPath=None)
    empty["media"]["metadata"].update(subtitle="", description="", genres=[], translator="")
    empty["media"].update(tags=[], chapters=[], coverPath="")
    store = Store(tmp_path / "test.sqlite")
    aid = snapshot(store, [missing, null, empty])
    books = {b["item_id"]: b for b in json.loads(export_review_bundle(store, aid))["books"]}
    fields = {"subtitle", "description", "genres", "tags", "translators", "cover_present", "chapters"}
    assert fields.isdisjoint(books["absent"]["review_values"])
    for name, expected in (("null", [None, None, None, None, None]),
                           ("empty", ["", "", [], [], ""])):
        review = books[name]["review_values"]
        assert [review[field] for field in ("subtitle", "description", "genres", "tags", "translators")] == expected
        assert review["cover_present"] is False
        assert books[name]["evidence"]["quality"]["cover"] == {"status": "Missing"}
    assert books["empty"]["review_values"]["chapters"] == []


def test_all_books_with_and_without_evidence(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite")
    aid = snapshot(store, [clean(f"item-{i:03d}") for i in reversed(range(110))])
    item_id = "item-001"
    source = FakeSource()
    monkeypatch.setattr(source, "search", lambda _: {"raw": ["provider-private-payload"], "candidates": [reference()]})
    run_verification(store, start_verification(store, aid, source, [item_id]), source)
    data = export_review_bundle(store, aid)
    books = json.loads(data)["books"]
    assert [book["item_id"] for book in books] == [f"item-{i:03d}" for i in range(110)]
    assert books[0]["evidence"]["references"] == {}
    assert books[1]["evidence"]["references"]["title"][0]["value"] == "Example"
    assert b"provider-private-payload" not in data


def test_invalid_snapshots_rejected(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    running = store.start_audit("test", "library-a", "Running")
    for aid in (running, running + 1):
        with pytest.raises(ValueError, match="completed snapshot"):
            export_review_bundle(store, aid)
    invalid = store.start_audit("test", "different-library", "Invalid")
    store.add_book(invalid, Book(clean()))
    store.finish_audit(invalid)
    with pytest.raises(ValueError, match="membership"):
        export_review_bundle(store, invalid)


def test_offline_import_reports_stale_source_and_no_writer(tmp_path):
    from abs_manager.sample import sample_audit
    from abs_manager.workflow import import_previous

    store = Store(tmp_path / "test.sqlite")
    aid = import_previous(store, sample_audit())
    result = json.loads(export_review_bundle(store, aid))
    assert result["source"]["offline_import"]
    assert result["authority"]["audiobook"]["guarded_write_fields"] == []
    assert all(book["observation"] == {"source_audit": aid, "fresh_abs": False} for book in result["books"])


def test_import_export_uses_latest_completed_and_only_generates_on_click(setup, monkeypatch):
    store, api, aid = setup
    running = store.start_audit(api.server, "library-a", "Interrupted")
    store.finish_audit(running, "interrupted")
    reads = list(api.reads)
    monkeypatch.setattr(FakeAdapter, "page", lambda *a: pytest.fail("Export must use saved inventory"))
    monkeypatch.setattr(FakeAdapter, "cover", lambda *a: pytest.fail("Export must not retrieve artwork"), raising=False)
    at = app(store, api, monkeypatch)
    assert "review_bundle" not in at.session_state
    widget(at, "button", "Export metadata for ChatGPT").click().run()
    assert not at.exception and at.get("download_button")
    bundle = at.session_state["review_bundle"][1]
    assert json.loads(bundle)["source"]["snapshot_id"] == aid
    for label in ("Match & Verify", "Changes", "History", "Import"):
        page(at, label)
    assert at.session_state["review_bundle"][1] == bundle
    assert api.reads == reads and api.writes == []
