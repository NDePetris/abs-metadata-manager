"""Journal access and bounded presentation; no timing thresholds or live services."""
import json

import pytest
from conftest import FakeAdapter, make_book
from streamlit.testing.v1 import AppTest
from test_review_import import encode, example_document, proposal, save
from test_streamlined import app, page, widget

from abs_manager import review_import_ui
from abs_manager.models import description_preview
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, batch_results, confirmation, preview, run_audit


def test_selected_and_unresolved_records_ignore_unrelated_payloads(tmp_path, monkeypatch):
    store = Store(tmp_path / "journal.sqlite")
    stale = store.record("server", "library", "old", "apply", "verified", {})
    data = {"before": {"description": "Roshar’s\n\n世界" * 1000}, "complete": [1, 2]}
    selected = store.record("server", "library", "selected", "apply", "uncertain", data)
    store.transition(selected, "uncertain", observed={"full": data})
    with store.connect() as db:
        db.execute("UPDATE journal SET data='not JSON', observed='not JSON' WHERE id=?", (stale,))
    with monkeypatch.context() as local:
        local.setattr(store, "history", lambda *a, **k: pytest.fail("Must query selected operation directly"))
        row = store.operation(selected)
        assert row["data"] == data and row["observed"] == {"full": data}
        with pytest.raises(StopIteration):
            store.operation(selected + 1)
    assert store.unresolved("server") == [row]
    assert store.has_unresolved("server") and store.has_unresolved()
    assert not store.has_unresolved("other")


def test_history_summaries_filters_and_full_history_compatibility(tmp_path):
    store = Store(tmp_path / "journal.sqlite")
    states = ("prepared", "applied", "uncertain", "verified", "reverted", "failed", "skipped", "not_applied")
    for state in states:
        store.record("server", "library", state, "apply", state, {"long": "世界" * 1000})
    other = store.record("other", "elsewhere", "other", "rollback", "verified", {})
    summaries = store.history_summaries("server", library="library")
    assert [r["state"] for r in summaries] == list(reversed(states))
    assert all("data" not in r and "observed" not in r for r in summaries)
    assert store.history_summaries(states=[]) == []
    assert [r["id"] for r in store.history_summaries("other")] == [other]
    assert len(store.history_summaries(states=["verified"])) == 2
    assert [r["state"] for r in store.unresolved("server")] == ["uncertain", "applied", "prepared"]
    assert len(store.history("server")) == len(states)
    assert store.history("") == store.history()


def test_large_descriptions_browsing_apply_reconcile_and_rollback(tmp_path, monkeypatch):
    original = "Original Roshar’s — 世界\n\n" * 100
    target = "Approved Édition — 世界\n\n" * 100
    raw = make_book(title="Title", series=None)
    raw["media"]["metadata"]["description"] = original
    api = FakeAdapter([raw])
    store = Store(tmp_path / "descriptions.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    store.confirm_targets(aid, "current-id", {"description": target}, "Synthetic evidence")
    reads = list(api.reads)
    at = app(store, api, monkeypatch)
    page(at, "Changes")
    table = at.dataframe[-1].value
    assert "truncated" in table.iloc[0]["Suggested"] and len(table.iloc[0]["Suggested"]) < 270
    assert not any(original in t.value or target in t.value for t in at.text)
    widget(at, "selectbox", "Inspect full description correction").select("current-id").run()
    assert any(t.value == original for t in at.text_area)
    assert any(t.value == target for t in at.text_area)
    assert not at.exception and api.reads == reads and not api.writes
    fid = next(f["id"] for f in store.findings(aid) if f["field"] == "description")
    store.review([fid], "approved")
    plan = preview(store, aid, [fid])
    assert plan["books"][0]["payload"]["metadata"]["description"] == target
    api.fail = "after"
    flow = Workflow(store, api)
    with pytest.raises(SafetyError, match="uncertain"):
        flow.apply(plan, confirmation(plan))
    oid = store.history_summaries()[0]["id"]
    assert flow.reconcile(oid) == "verified"
    api.fail = None
    assert store.saved_batches(api.server, "library-a") == [{"id": oid, "digest": plan["digest"]}]
    assert store.saved_batches(api.server, "elsewhere") == []
    assert batch_results(store, plan)[0]["status"] == "Verified applied"
    reads = list(api.reads)
    page(at, "History")
    assert not any(t.value == original or t.value == target for t in at.text_area)
    widget(at, "checkbox", "Show full operation descriptions").check().run()
    assert any(t.value == original for t in at.text_area)
    assert sum(t.value == target for t in at.text_area) == 2  # intended and observed
    widget(at, "checkbox", "Load raw operation JSON").check().run()
    assert not at.exception and api.reads == reads and len(api.writes) == 1
    back = flow.rollback_preview(oid)
    assert back["payload"]["metadata"]["description"] == original
    rid = flow.rollback(back, confirmation(back, "ROLLBACK"))
    assert store.operation(rid)["state"] == "verified"
    assert api.writes == [("current-id", {"description": target}), ("current-id", {"description": original})]
    assert description_preview(target).startswith(target[:200])
    assert json.loads(store.rows("SELECT data FROM journal WHERE id=?", (oid,))[0]["data"])["after"]["description"] == target


def test_preview_boundaries_preserve_unicode_and_short_text():
    for value in (None, "", "Roshar’s\n\n世界", "é" * 200):
        assert description_preview(value) == value
    value = "世界’s\n\n" * 1000
    assert description_preview(value) == value[:200] + "… (truncated; inspect full text)"


def test_recommendation_full_text_and_parse_reuse_recheck_saved_state(tmp_path, monkeypatch):
    store = Store(tmp_path / "recommendations.sqlite")
    aid = save(store, [make_book(title="Title")])
    target = "Roshar’s Édition — 世界\n\n" * 200
    doc = example_document(store, aid)
    doc["recommendations"] = [proposal(store.books(aid)[0], "description", target, "editorial_review")]
    parsed = []
    original = review_import_ui.parse_review

    def counted(data):
        parsed.append(data)
        return original(data)

    monkeypatch.setattr(review_import_ui, "parse_review", counted)
    at = AppTest.from_string(f"""
from abs_manager.store import Store
from abs_manager.review_import_ui import import_corrections
store = Store({store.path!r})
import_corrections(store, store.audit({aid}))
""", default_timeout=60).run()
    at.file_uploader[0].upload("synthetic.json", encode(doc), "application/json").run()
    assert len(parsed) == 1 and not at.exception
    assert not any(t.value == target for t in at.text)
    table = at.dataframe[0].value
    assert "truncated" in table.iloc[0]["Suggested"] and len(table.iloc[0]["Suggested"]) < 300
    widget(at, "selectbox", "Recommendation details").select_index(0).run()
    assert any(t.value == target for t in at.text_area) and len(parsed) == 1
    store.confirm_targets(aid, "current-id", {"description": target}, "Reviewed separately")
    at.run()
    assert at.dataframe[0].value.iloc[0]["Status"] == "Staged"
    assert widget(at, "button", "Stage selected changes locally (0)").disabled
    assert len(parsed) == 1 and not store.history()
    doc["recommendations"][0]["complete_desired_value"] = target + "Changed upload."
    at.file_uploader[0].upload("synthetic.json", encode(doc), "application/json").run()
    assert len(parsed) == 2 and not at.exception
    at.file_uploader[0].upload("invalid.json", b"{}", "application/json").run()
    assert at.error and len(parsed) == 3 and not store.history()
