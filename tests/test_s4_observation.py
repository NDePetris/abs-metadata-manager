import copy
import json

import pytest
from test_s4_corrections import fixture

from abs_manager.calibre import import_catalog, reconciliation_states
from abs_manager.correction_plan import library_plan, stage_selected
from abs_manager.workflow import SafetyError, observe_selected, run_audit


def test_calibre_authority_abs_lag_and_changed_uuid(tmp_path):
    from conftest import FakeAdapter
    from test_quality_policy import clean

    from abs_manager.store import Store
    raw = clean("ebook", False)
    raw["path"] = raw["relPath"] = "Author/Example (1)"
    api = FakeAdapter([raw])
    store = Store(tmp_path / "ebook.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    store.confirm_targets(aid, "ebook", {"title": "Corrected"})
    catalog = [{"id": 1, "uuid": "uuid-1", "title": "Corrected", "authors": ["Author One"]}]
    store.mark_calibre_edited(store.corrections(aid)[0]["id"])
    assert "revalidation" in reconciliation_states(store, aid)[0]["state"] or "unavailable" in reconciliation_states(store, aid)[0]["state"]
    import_catalog(store, aid, json.dumps(catalog), "synthetic-library")
    assert store.corrections(aid)[0]["calibre_verified"]
    assert not store.corrections(aid)[0]["abs_observed"]
    assert "synchronization pending" in reconciliation_states(store, aid)[0]["state"]
    api.items["ebook"]["media"]["metadata"]["title"] = "Corrected"
    fresh = observe_selected(store, api, aid, ["ebook"])
    assert store.corrections(fresh)[0]["abs_observed"]
    assert reconciliation_states(store, fresh)[0]["state"] == "Calibre and ABS agree"
    catalog[0]["uuid"] = "replaced"
    assert import_catalog(store, fresh, json.dumps(catalog), "synthetic-library") == 0
    assert store.corrections(fresh)[0]["status"] == "needs_review"
    assert not api.writes


def test_unresolved_journal_cannot_close_via_import(tmp_path):
    store, api, aid = fixture(tmp_path, 1)
    store.confirm_targets(aid, "audio-0", {"narrators": ["Native"]})
    oid = store.record(api.server, "library-a", "audio-0", "apply", "uncertain", {})
    api.items["audio-0"]["media"]["metadata"]["narrators"] = ["Native"]
    new = run_audit(store, api, api.libraries()[0])
    assert not store.corrections(new)[0]["abs_observed"]
    assert store.operation(oid)["state"] == "uncertain"

def test_external_partial_observation_and_later_drift(tmp_path):
    store, api, aid = fixture(tmp_path)
    store.decide_fields(aid, "audio-0", ["cover"], "confirmed", "Inspected synthetic artwork")
    stage_selected(store, library_plan(store, aid), [p["id"] for p in library_plan(store, aid)["proposals"]])
    api.items["audio-0"]["media"]["metadata"]["narrators"] = ["Narrator One"]
    api.items["audio-0"]["media"]["tags"] = ["native edit"]
    api.items["audio-0"]["media"]["metadata"]["genres"] = ["Native genre"]
    api.items["audio-0"]["media"]["coverPath"] = "/new-cover.jpg"
    fresh = observe_selected(store, api, aid, ["audio-0", "audio-1"])
    corrections = {c["item_id"]: c for c in store.corrections(fresh)}
    assert corrections["audio-0"]["status"] == "observed_in_abs"
    assert not corrections["audio-1"]["abs_observed"]
    reports = {r["item_id"]: r for r in store.verification_report(fresh)}
    assert reports["audio-0"]["fields"]["tags"]["value"] == ["native edit"]
    assert reports["audio-0"]["fields"]["genres"]["value"] == ["Native genre"]
    assert reports["audio-0"]["fields"]["cover"]["disposition"] != "Pass"
    assert not reports["audio-2"]["abs_observation"]["fresh_abs"]
    api.items["audio-0"]["media"]["metadata"]["narrators"] = ["Unexpected"]
    fresh = observe_selected(store, api, fresh, ["audio-0"])
    assert next(c for c in store.corrections(fresh) if c["item_id"] == "audio-0")["status"] == "needs_review"
    assert not api.writes


def test_external_refresh_fail_closed_identity_server_recovery(tmp_path):
    store, api, aid = fixture(tmp_path)
    original = copy.deepcopy(api.items["audio-0"])
    api.items["audio-0"]["path"] = "/moved"
    with pytest.raises(SafetyError):
        observe_selected(store, api, aid, ["audio-0"])
    assert store.latest(api.server, "library-a")["id"] == aid
    api.items["audio-0"] = original
    oid = store.record(api.server, "library-a", "audio-0", "apply", "uncertain", {})
    with pytest.raises(SafetyError, match="reconcile"):
        observe_selected(store, api, aid, ["audio-0"])
    assert store.operation(oid)["state"] == "uncertain" and not api.writes


def test_bounded_refresh_does_not_inherit_calibre_after_edition_identity_change(tmp_path):
    from conftest import FakeAdapter
    from test_quality_policy import clean

    from abs_manager.store import Store
    raw = clean("ebook", False)
    raw["path"] = raw["relPath"] = "Author/Example (1)"
    api = FakeAdapter([raw])
    store = Store(tmp_path / "identity.sqlite")
    aid = run_audit(store, api, api.libraries()[0])
    assert import_catalog(store, aid, json.dumps([{"id": 1, "uuid": "uuid-1", "title": "Example",
                                                  "authors": ["Author One"]}]), "synthetic-library") == 1
    api.items["ebook"]["media"]["metadata"]["asin"] = "B222222222"
    fresh = observe_selected(store, api, aid, ["ebook"])
    assert store.verification_report(fresh)[0]["calibre_limitation"]
    assert not api.writes

