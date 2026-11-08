import copy

import pytest
from conftest import FakeAdapter, make_book

from abs_manager.bulk import Bulk, strict_json


def prepare(tmp_path, api=None):
    api = api or FakeAdapter()
    bulk = Bulk(api, tmp_path / "bulk", tmp_path / "legacy.sqlite3")
    export = bulk.export("library-a")
    b = export["books"][0]
    document = {
        "schema_version": 1,
        "snapshot_id": export["snapshot_id"],
        "scope": export["scope"],
        "items": [
            {
                "item_id": b["item_id"],
                "identity": b["identity"],
                "changes": {"title": {"before": b["current"]["title"], "after": "Correct title"}},
            }
        ],
    }
    return bulk, api, document


def test_one_book_complete_path_and_retry(tmp_path):
    bulk, api, doc = prepare(tmp_path)
    plan = bulk.preview(doc)
    api.check_journal = lambda: assert_intent(bulk)
    result = bulk.apply(plan, "APPLY " + plan["digest"][:12])
    assert result[0]["state"] == "verified"
    assert api.writes == [("current-id", {"title": "Correct title"})]
    bulk.apply(plan, "APPLY " + plan["digest"][:12])
    assert len(api.writes) == 1


def assert_intent(bulk):
    assert list(bulk.records().values())[-1]["state"] == "intent"


@pytest.mark.parametrize("failure", ["before", "after"])
def test_uncertain_restart_and_readonly_recovery(tmp_path, failure):
    bulk, api, doc = prepare(tmp_path)
    api.fail = failure
    plan = bulk.preview(doc)
    assert bulk.apply(plan, "APPLY " + plan["digest"][:12])[0]["state"] == "uncertain"
    restarted = Bulk(api, bulk.directory, bulk.legacy)
    with pytest.raises(ValueError, match="Uncertain"):
        restarted.apply(plan, "APPLY " + plan["digest"][:12])
    assert restarted.check_results()[0]["state"] == ("verified" if failure == "after" else "not_applied")
    assert len(api.writes) == 1


def test_confirmation_stale_and_protected(tmp_path):
    bulk, api, doc = prepare(tmp_path)
    plan = bulk.preview(doc)
    with pytest.raises(ValueError, match="Confirmation"):
        bulk.apply(plan, "yes")
    api.items["current-id"]["media"]["tags"] = ["changed"]
    assert bulk.apply(plan, "APPLY " + plan["digest"][:12])[0]["state"] == "conflict"
    assert not api.writes


def test_schema_and_ebook(tmp_path):
    raw = make_book()
    raw["media"].update(duration=0, audioFiles=[])
    bulk, api, doc = prepare(tmp_path, FakeAdapter([raw]))
    assert bulk.ebook_report(doc)["corrections"][0]["kind"] == "ebook"
    with pytest.raises(ValueError, match="audiobook"):
        bulk.preview(doc)
    with pytest.raises(ValueError, match="Duplicate"):
        strict_json('{"x":1,"x":2}')
    with pytest.raises(ValueError):
        strict_json('{"x":NaN}')
    bad = copy.deepcopy(doc)
    bad["items"][0]["changes"]["tags"] = {"before": [], "after": []}
    with pytest.raises(ValueError, match="Unsupported"):
        bulk.review(bad)
    assert not api.writes


def test_export_500_and_privacy(tmp_path):
    bulk, _, doc = prepare(tmp_path, FakeAdapter([make_book(item_id=f"book-{i}") for i in range(500)]))
    exported = bulk.export("library-a")
    assert exported["book_count"] == 500
    assert "path" not in str(exported)
    assert len(bulk.review(doc)) == 1


def test_untrusted_series_is_bounded_before_decimal_conversion(tmp_path):
    bulk, _, doc = prepare(tmp_path)
    doc["items"][0]["changes"] = {
        "series": {"before": [], "after": [{"name": "Series", "sequence": "1e99999999"}]}
    }
    with pytest.raises(ValueError, match="bounded"):
        bulk.review(doc)
