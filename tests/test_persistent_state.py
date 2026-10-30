import copy
import json
import shutil
import sqlite3

import pytest
from conftest import make_book

from abs_manager.calibre import import_catalog
from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.verification import candidate


def snapshot(store, raw):
    aid = store.start_audit("test", "library-a", "Test")
    for book in raw:
        store.add_book(aid, Book(book))
    store.finish_audit(aid)
    return aid


def test_persistent_decisions_and_reconciliation(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    book = make_book()
    first = snapshot(store, [book])
    store.decide_fields(first, book["id"], ["title", "subtitle"], "confirmed", "checked", "human")
    store.decide_candidate(first, book["id"], "bad", "rejected", "wrong narrator")
    changed = copy.deepcopy(book)
    changed["media"]["metadata"]["description"] = "new description"
    second = snapshot(Store(path), [changed])
    assert store.field_decisions(second, book["id"])["title"]["valid"]
    assert store.candidate_decisions(second, book["id"])["bad"]["valid"]
    assert store.import_summary(second)["retained"] == 2
    changed["media"]["metadata"]["title"] = "New title"
    third = snapshot(store, [changed, make_book(item_id="new")])
    assert store.field_decisions(third, book["id"])["title"]["decision"] == "reopened"
    assert store.field_decisions(third, book["id"])["subtitle"]["valid"]
    assert store.import_summary(third)["new"] == 1
    changed["path"] = "/moved"
    fourth = snapshot(store, [changed])
    assert store.candidate_decisions(fourth, book["id"])["bad"]["decision"] == "unsure"
    assert store.import_summary(fourth)["removed"] == 1
    assert len(store.field_decisions(fourth, book["id"], history=True)) == 4


def test_calibre_confirmation_and_abs_observation(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    book = make_book(path="Author/Title (12)")
    book["media"]["duration"] = 0
    book["media"]["audioFiles"] = []
    first = snapshot(store, [book])
    cid = store.queue_correction(first, book["id"], "title", "Correct title", "catalog evidence", 12)
    store.mark_calibre_edited(cid)

    def catalog(title):
        return json.dumps([{"id": 12, "title": title, "authors": ["Dennis E. Taylor"]}])

    import_catalog(store, first, catalog("Old title"))
    assert store.corrections(first)[0]["status"] == "edited_in_calibre"
    import_catalog(store, first, catalog("Correct title"))
    row = store.corrections(first)[0]
    assert row["calibre_verified"] and not row["abs_observed"]
    assert len(store.rows("SELECT * FROM calibre_catalog_history")) == 2
    updated = copy.deepcopy(book)
    updated["media"]["metadata"]["title"] = "Correct title"
    second = snapshot(store, [updated])
    assert store.corrections(second)[0]["abs_observed"]


def test_additive_migration_preserves_existing_audits(tmp_path):
    path = tmp_path / "legacy.sqlite"
    store = Store(path)
    aid = snapshot(store, [make_book()])
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE field_decision_history")
        db.execute("DROP TABLE candidate_decision_history")
        db.execute("DROP TABLE corrections")
    reopened = Store(path)
    assert reopened.audit(aid)["status"] == "complete"
    assert reopened.book(aid, "current-id").id == "current-id"
    Store(path)


def test_failed_reconciliation_rolls_back_completion(tmp_path):
    store = Store(tmp_path / "atomic.sqlite")
    book = make_book()
    first = snapshot(store, [book])
    store.decide_fields(first, book["id"], ["title"], "confirmed", "checked")
    changed = copy.deepcopy(book)
    changed["media"]["metadata"]["title"] = "Changed"
    second = store.start_audit("test", "library-a", "Test")
    store.add_book(second, Book(changed))
    with store.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_finish BEFORE UPDATE OF status ON audits WHEN NEW.status='complete' BEGIN SELECT RAISE(ABORT, 'simulated crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_audit(second)
    assert store.audit(second)["status"] == "running"
    assert len(store.field_decisions(second, book["id"], history=True)) == 1
    with store.connect() as db:
        db.execute("DROP TRIGGER fail_finish")
    store.finish_audit(second)
    assert store.field_decisions(second, book["id"])["title"]["decision"] == "reopened"


def test_edition_changes_rejections_and_removed_identity(tmp_path):
    store = Store(tmp_path / "lifecycle.sqlite")
    book = make_book()
    first = snapshot(store, [book])
    store.decide_fields(first, book["id"], ["title", "subtitle"], "confirmed", "reviewed")
    store.decide_candidate(first, book["id"], "wrong", "rejected", "wrong narrator")
    store.decide_candidate(first, book["id"], "right", "selected", "matches")
    changed = copy.deepcopy(book)
    changed["media"]["metadata"]["narrators"] = ["Another narrator"]
    second = snapshot(store, [changed])
    assert all(d["decision"] == "reopened" for d in store.field_decisions(second, book["id"]).values())
    assert store.candidate_decisions(second, book["id"])["wrong"]["decision"] == "rejected"
    assert store.candidate_decisions(second, book["id"])["right"]["decision"] == "unsure"
    store.decide_fields(second, book["id"], ["title"], "confirmed", "checked again")
    changed["media"]["duration"] = 2000
    third = snapshot(store, [changed])
    assert store.field_decisions(third, book["id"])["title"]["decision"] == "reopened"
    store.decide_fields(third, book["id"], ["title"], "confirmed", "checked again")
    removed = snapshot(store, [])
    assert store.import_summary(removed)["removed"] == 1
    restored = snapshot(store, [changed])
    assert store.field_decisions(restored, book["id"])["title"]["decision"] == "reopened"
    assert store.candidate_decisions(restored, book["id"])["wrong"]["decision"] == "unsure"
    again = snapshot(store, [changed])
    summary = store.import_summary(again)
    assert summary["new"] == summary["removed"] == summary["changed"] == 0
    assert store.field_decisions(again, book["id"])["title"]["decision"] == "reopened"
    with pytest.raises(ValueError):
        store.decide_fields(first, book["id"], ["title"], "confirmed")


def test_new_id_and_unrelated_external_conflict_do_not_transfer_or_revoke(tmp_path):
    store = Store(tmp_path / "identity.sqlite")
    book = make_book()
    first = snapshot(store, [book])
    store.decide_fields(first, book["id"], ["title"], "confirmed", "Human confirmation")
    store.decide_fields(first, book["id"], ["subtitle"], "confirmed", "Human confirmation")
    external = candidate(
        {
            "title": book["media"]["metadata"]["title"],
            "subtitle": "Different subtitle",
            "author": "Dennis E. Taylor",
            "narrator": "Ray Porter",
            "duration": 1000,
        },
        "provider",
    )
    evidence = {"source": "provider", "scope": "test", "state": "ok", "query": {}, "candidates": [external]}
    with store.connect() as db:
        db.execute(
            "INSERT INTO verification_runs VALUES(1,?,'provider','test','complete','date','date')", (first,)
        )
        db.execute(
            "INSERT INTO verification_lookups VALUES(1,'key','provider','test','date',?)",
            (json.dumps(evidence),),
        )
        db.execute("INSERT INTO verification_tasks VALUES(1,?,'{}','key',1)", (book["id"],))
    report = store.verification_report(first)[0]
    assert report["fields"]["subtitle"]["status"] == "Conflict"
    assert report["fields"]["subtitle"]["decision"]["decision"] == "confirmed"
    assert store.field_decisions(first, book["id"])["title"]["decision"] == "confirmed"
    replacement = copy.deepcopy(book)
    replacement["id"] = "replacement"
    second = snapshot(store, [replacement])
    assert store.field_decisions(second, "replacement") == {}
    summary = store.import_summary(second)
    assert (summary["new"], summary["removed"], summary["ambiguous"]) == (1, 1, 1)
    assert store.field_decisions(first, book["id"])["title"]["decision"] == "confirmed"


def test_correction_never_reconciles_after_absence(tmp_path):
    store = Store(tmp_path / "correction.sqlite")
    book = make_book()
    first = snapshot(store, [book])
    store.queue_correction(first, book["id"], "title", "Fixed title", "evidence")
    unchanged = snapshot(store, [book])
    assert store.corrections(unchanged)[0]["status"] == "pending"
    missing = snapshot(store, [])
    assert store.corrections(missing)[0]["status"] == "identity_ambiguous"
    changed = copy.deepcopy(book)
    changed["media"]["metadata"]["title"] = "Fixed title"
    restored = snapshot(store, [changed])
    assert store.corrections(restored)[0]["abs_observed"] is None
    assert store.corrections(restored)[0]["status"] == "identity_ambiguous"


def test_pending_calibre_correction_can_be_verified_without_claim(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    book = make_book(path="Author/Book (12)")
    book["media"]["duration"] = 0
    book["media"]["audioFiles"] = []
    first = snapshot(store, [book])
    store.queue_correction(first, book["id"], "title", "Fixed", "evidence", 12)
    export = lambda title: json.dumps([{"id": 12, "title": title, "authors": ["Dennis E. Taylor"]}])
    import_catalog(store, first, export("Wrong"))
    assert store.corrections(first)[0]["status"] == "pending"
    import_catalog(store, first, export("Fixed"))
    assert store.corrections(first)[0]["status"] == "verified_in_calibre"
    catalogs = store.rows("SELECT data FROM calibre_catalog_history ORDER BY id")
    assert [json.loads(c["data"])[0]["title"] for c in catalogs] == ["Wrong", "Fixed"]


def test_migration_from_copied_legacy_database_preserves_history(tmp_path):
    legacy = tmp_path / "legacy-source.sqlite"
    raw = make_book()
    with sqlite3.connect(legacy) as db:
        db.executescript("""
          CREATE TABLE audits(id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL,
             name TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, error TEXT,
             read_only INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE books(audit_id INTEGER NOT NULL, item_id TEXT NOT NULL, raw TEXT NOT NULL,
             normalized TEXT NOT NULL, PRIMARY KEY(audit_id,item_id));
          CREATE TABLE findings(id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL, item_id TEXT NOT NULL,
             field TEXT NOT NULL, kind TEXT NOT NULL, current TEXT NOT NULL, proposed TEXT NOT NULL,
             confidence TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, actionable INTEGER NOT NULL,
             status TEXT NOT NULL DEFAULT 'pending', target TEXT NOT NULL, reviewed TEXT);
          CREATE TABLE journal(id INTEGER PRIMARY KEY, server TEXT NOT NULL, library TEXT NOT NULL,
             item_id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL,
             updated TEXT NOT NULL, data TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', observed TEXT);
          CREATE TABLE verification_runs(id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL, source TEXT NOT NULL,
             scope TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
          CREATE TABLE verification_lookups(id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, source TEXT NOT NULL,
             scope TEXT NOT NULL, captured TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE verification_tasks(run_id INTEGER NOT NULL, item_id TEXT NOT NULL, query TEXT NOT NULL,
             cache_key TEXT NOT NULL, lookup_id INTEGER, PRIMARY KEY(run_id,item_id));
          CREATE TABLE verification_annotations(audit_id INTEGER NOT NULL, item_id TEXT NOT NULL,
             field TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(audit_id,item_id,field));
          INSERT INTO audits VALUES(1,'test','library-a','Legacy','complete','date','date',NULL,0);
          INSERT INTO findings VALUES(1,1,'current-id','title','manual','"old"','"new"','review','reason','{}',1,'rejected','"new"','date');
          INSERT INTO journal VALUES(1,'test','library-a','current-id','apply','verified','date','date','{}','preserved',NULL);
          INSERT INTO verification_runs VALUES(1,1,'provider','scope','complete','date','date');
          INSERT INTO verification_lookups VALUES(1,'key','provider','scope','date','{"source":"provider","scope":"scope","state":"no_match","query":{}}');
          INSERT INTO verification_tasks VALUES(1,'current-id','{}','key',1);
          INSERT INTO verification_annotations VALUES(1,'current-id','title','{"outcome":"Reviewed","evidence":"legacy"}');
        """)
        db.execute("INSERT INTO books VALUES(?,?,?,?)", (1, raw["id"], json.dumps(raw), "{}"))
    migrated = tmp_path / "migration-copy.sqlite"
    shutil.copyfile(legacy, migrated)
    store = Store(migrated)
    assert store.audit(1)["status"] == "complete"
    assert store.findings(1)[0]["status"] == "rejected"
    assert store.history()[0]["message"] == "preserved"
    assert store.verification_evidence(1, raw["id"])[0]["state"] == "no_match"
    assert store.verification_annotations(1, raw["id"])["title"]["outcome"] == "Reviewed"
    assert store.field_decisions(1, raw["id"]) == {}
    # An interrupted additive upgrade leaves any successfully created table in place.
    with sqlite3.connect(migrated) as db:
        db.execute("DROP TABLE candidate_decision_history")
        db.execute("DROP TABLE calibre_catalog_history")
        db.execute("INSERT INTO calibre_catalogs VALUES(1,?)", ('{"legacy":"comparison evidence"}',))
    reopened = Store(migrated)
    assert reopened.history()[0]["message"] == "preserved"
    assert reopened.candidate_decisions(1, raw["id"]) == {}
    Store(migrated)
    assert [json.loads(row["data"]) for row in reopened.rows("SELECT data FROM calibre_catalog_history")] == [
        {"legacy": "comparison evidence"}
    ]
    with sqlite3.connect(legacy) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='field_decision_history'").fetchone()
