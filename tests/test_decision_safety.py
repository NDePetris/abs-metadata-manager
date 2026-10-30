from conftest import FakeAdapter, make_book

from abs_manager.store import Store
from abs_manager.workflow import Workflow, confirmation, preview, run_audit


def test_field_decision_never_approves_a_write_and_new_audit_expires_approval(tmp_path):
    api = FakeAdapter([make_book()])
    store = Store(tmp_path / "decisions.sqlite3")
    audit = run_audit(store, api, api.libraries()[0])
    finding = next(f for f in store.findings(audit) if f["actionable"])
    store.decide_fields(
        audit, finding["item_id"], [finding["field"]], "confirmed", evidence="Checked edition"
    )
    assert not preview(store, audit)["books"]
    Workflow(store, api).apply(preview(store, audit), confirmation(preview(store, audit)))
    assert not api.writes

    store.review([finding["id"]], "approved")
    assert preview(store, audit)["books"]
    fresh = run_audit(store, api, api.libraries()[0])
    assert not preview(store, fresh)["books"]
    assert not api.writes
