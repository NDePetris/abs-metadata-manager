
import pytest
from conftest import FakeAdapter, make_book

from abs_manager.locking import BusyError, operation_lock
from abs_manager.models import Book
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, confirmation, preview, run_audit


def approve(store, aid):
    ids = [f["id"] for f in store.findings(aid) if f["actionable"]]
    store.review(ids, "approved")
    return preview(store, aid)


def test_audit_and_preview_do_not_write(setup):
    store, api, aid = setup
    aid = run_audit(store, api, api.libraries()[0])
    assert preview(store, aid)["books"] == []
    approve(store, aid)
    assert not api.writes


def test_no_approval_or_confirmation_no_writes(setup):
    store, api, aid = setup
    wf = Workflow(store, api)
    plan = preview(store, aid)
    wf.apply(plan, confirmation(plan))
    assert not api.writes
    plan = approve(store, aid)
    with pytest.raises(SafetyError):
        wf.apply(plan, "APPLY")
    assert not api.writes


def test_persistent_decisions_and_edits(setup):
    store, _api, aid = setup
    fid = next(f["id"] for f in store.findings(aid) if f["actionable"])
    store.review([fid], "approved", edited="Custom Title: Real Subtitle")
    reopened = Store(store.path)
    assert preview(reopened, aid)["books"][0]["changes"] == {"title": "Custom Title: Real Subtitle"}
    reopened.review([fid], "rejected")
    assert not preview(store, aid)["books"]


def test_new_audit_invalidates_even_identical_approvals(setup):
    store, api, aid = setup
    old = approve(store, aid)
    new = run_audit(store, api, api.libraries()[0])
    assert not preview(store, new)["books"]
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(old, confirmation(old))
    assert not api.writes


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(id="new-id"),
    lambda b: b.update(libraryId="other-library"),
    lambda b: b.update(relPath="renamed/path"),
    lambda b: b.update(path="/changed/path"),
    lambda b: b["media"]["metadata"].update(title="External edit"),
    lambda b: b["media"]["metadata"].update(narrators=["External narrator"]),
    lambda b: b.update(isMissing=True),
])
def test_live_state_revalidation_and_stale_rejection(setup, mutation):
    store, api, aid = setup
    plan = approve(store, aid)
    mutation(api.items["current-id"])
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes
    assert store.history()[0]["state"] == "skipped"


def test_old_id_never_resolved_to_new_id(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    raw = api.items.pop("current-id")
    raw["id"] = "new-uuid"
    raw["oldLibraryItemId"] = "current-id"
    api.items[raw["id"]] = raw
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes


def test_preview_bound_to_exact_approvals_and_values(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    fid = plan["books"][0]["finding_ids"][0]
    store.review([fid], "approved", edited="Changed after preview")
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes


def test_verified_apply_persists_intent_before_send_and_preserves_unrelated(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    before = Book(api.item("current-id")).protected()
    api.check_journal = lambda: assert_prepared(Store(store.path))
    ids = Workflow(store, api).apply(plan, confirmation(plan))
    assert len(api.writes) == 1
    assert api.writes[0] == ("current-id", {"title": "Heaven's River"})
    assert store.operation(ids[0])["state"] == "verified"
    assert Book(api.item("current-id")).protected() == before
    assert not preview(store, aid)["books"]


def assert_prepared(store):
    assert store.history()[0]["state"] == "prepared"
    assert store.history()[0]["data"]["pre_update_raw"]["id"] == "current-id"


@pytest.mark.parametrize("mode,result", [("after", "verified"), ("before", "not_applied")])
def test_timeouts_reconcile_without_resubmission(setup, mode, result):
    store, api, aid = setup
    plan = approve(store, aid)
    api.fail = mode
    wf = Workflow(store, api)
    with pytest.raises(SafetyError, match="uncertain"):
        wf.apply(plan, confirmation(plan))
    row = store.history()[0]
    assert row["state"] == "uncertain"
    with pytest.raises(SafetyError):
        wf.apply(plan, confirmation(plan))
    assert len(api.writes) == 1
    assert wf.reconcile(row["id"]) == result
    assert len(api.writes) == 1
    assert not preview(store, aid)["books"]


def test_interrupted_write_durable_recovery(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    api.fail = "interrupt"
    with pytest.raises(KeyboardInterrupt):
        Workflow(store, api).apply(plan, confirmation(plan))
    reopened = Store(store.path)
    assert reopened.unresolved(api.server)
    assert Workflow(reopened, api).reconcile(reopened.history()[0]["id"]) == "verified"


def test_crash_after_prepared_reconciles_before_state(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    live = Book(api.item("current-id"))
    group = plan["books"][0]
    oid = store.record(api.server, "library-a", live.id, "apply", "prepared",
                       {**group, "after": {**live.values, **group["changes"]}, "protected": live.protected()})
    assert Workflow(Store(store.path), api).reconcile(oid) == "not_applied"
    assert not api.writes


def test_mixed_result_and_unrelated_changes_remain_uncertain(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    api.mutate_after = lambda b: b["media"].update(tags=["unexpected"])
    wf = Workflow(store, api)
    with pytest.raises(SafetyError):
        wf.apply(plan, confirmation(plan))
    assert wf.reconcile(store.history()[0]["id"]) == "uncertain"
    assert len(api.writes) == 1


def test_guarded_rollback(setup):
    store, api, aid = setup
    wf = Workflow(store, api)
    plan = approve(store, aid)
    oid = wf.apply(plan, confirmation(plan))[0]
    back = wf.rollback_preview(oid)
    with pytest.raises(SafetyError):
        wf.rollback(back, "ROLLBACK")
    bid = wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert api.items["current-id"]["media"]["metadata"]["title"] == plan["books"][0]["before"]["title"]
    assert store.operation(oid)["state"] == "reverted"
    assert store.operation(bid)["state"] == "verified"
    assert not preview(store, aid)["books"]


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_rollback_refuses_external_edits(setup, field):
    store, api, aid = setup
    wf = Workflow(store, api)
    plan = approve(store, aid)
    oid = wf.apply(plan, confirmation(plan))[0]
    back = wf.rollback_preview(oid)
    api.items["current-id"]["media"]["metadata"][field] = "Changed outside app"
    with pytest.raises(SafetyError):
        wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert len(api.writes) == 1


def test_uncertain_rollback_reconciles_parent(setup):
    store, api, aid = setup
    wf = Workflow(store, api)
    plan = approve(store, aid)
    oid = wf.apply(plan, confirmation(plan))[0]
    back = wf.rollback_preview(oid)
    api.fail = "after"
    with pytest.raises(SafetyError):
        wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert wf.reconcile(store.history()[0]["id"]) == "verified"
    assert store.operation(oid)["state"] == "reverted"


def test_combines_only_approved_fields_and_preserves_contributors(setup):
    store, api, aid = setup
    manual = store.propose(aid, "current-id", "authors", ["Author One", "Author Two"], "Checked credits")
    store.review([manual], "approved")
    plan = preview(store, aid)
    Workflow(store, api).apply(plan, confirmation(plan))
    assert set(api.writes[0][1]) == {"authors"}
    assert Book(api.item("current-id")).values["authors"] == ["Author One", "Author Two"]
    assert Book(api.item("current-id")).values["title"].endswith("Book 4")


def test_manual_subtitle_with_null_rollback(setup):
    store, api, aid = setup
    fid = store.propose(aid, "current-id", "subtitle", "A real subtitle", "Checked publisher credits")
    assert not preview(store, aid)["books"]
    store.review([fid], "approved")
    plan = preview(store, aid)
    wf = Workflow(store, api)
    oid = wf.apply(plan, confirmation(plan))[0]
    back = wf.rollback_preview(oid)
    wf.rollback(back, confirmation(back, "ROLLBACK"))
    assert Book(api.item("current-id")).values["subtitle"] is None


def test_batch_stops_after_first_uncertain(tmp_path):
    api = FakeAdapter([make_book(item_id="a"), make_book(item_id="b")])
    store = Store(tmp_path / "batch.sqlite3")
    aid = run_audit(store, api, api.libraries()[0])
    plan = approve(store, aid)
    api.fail = "after"
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert len(api.writes) == 1


def test_process_lock_rejects_second_operation(setup):
    store, api, aid = setup
    with operation_lock(store.path):
        with pytest.raises(BusyError):
            run_audit(store, api, api.libraries()[0])
        with pytest.raises(BusyError):
            store.review([store.findings(aid)[0]["id"]], "approved")


def test_ebooks_are_readonly(setup):
    store, api, aid = setup
    raw = make_book(item_id="epub")
    raw["media"].update(audioFiles=[], duration=0, ebookFile={"metadata": {"ext": ".epub"}})
    store.add_book(aid, Book(raw))
    findings = [f for f in store.findings(aid) if f["item_id"] == "epub"]
    assert findings and not any(f["actionable"] for f in findings)
    with pytest.raises(ValueError):
        store.review([findings[0]["id"]], "approved")
    with pytest.raises(ValueError):
        store.propose(aid, "epub", "title", "New title", "Never edit ebooks")
    assert not preview(store, aid)["books"]
    assert not api.writes


def test_wrong_server_blocks_apply(setup):
    store, api, aid = setup
    plan = approve(store, aid)
    api.server = "http://other.invalid"
    with pytest.raises(SafetyError):
        Workflow(store, api).apply(plan, confirmation(plan))
    assert not api.writes
