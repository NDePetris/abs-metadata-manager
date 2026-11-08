import time

from fastapi.testclient import TestClient
from test_bulk import prepare

from abs_manager.bulk_app import create_app


def test_http_confirmation_and_session(tmp_path):
    service, api, doc = prepare(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("__SESSION_TOKEN__", encoding="utf-8")
    app = create_app(service, assets)
    with TestClient(app) as client:
        assert client.get("/api/job").status_code == 403
        client.headers["x-session-token"] = app.state.session_token
        assert client.get("/api/job", headers={"origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/api/review?library_id=other", json=doc).status_code == 400
        assert len(client.post("/api/review?library_id=library-a", json=doc).json()) == 1
        plan = client.post("/api/preview", json=doc).json()
        assert (
            client.post("/api/apply", json={"digest": plan["digest"], "confirmation": "yes"}).status_code
            == 400
        )
        assert not api.writes
        assert (
            client.post(
                "/api/apply", json={"digest": plan["digest"], "confirmation": plan["phrase"]}
            ).status_code
            == 200
        )
        for _ in range(100):
            job = client.get("/api/job").json()
            if not job["running"]:
                break
            time.sleep(0.01)
        assert job["result"][0]["state"] == "verified"
        assert (
            client.post(
                "/api/apply", json={"digest": plan["digest"], "confirmation": plan["phrase"]}
            ).status_code
            == 400
        )
        assert len(api.writes) == 1


def test_more_guard_cases(tmp_path):
    service, api, doc = prepare(tmp_path)
    doc["items"][0]["changes"]["title"]["before"] = "wrong"
    assert service.review(doc)[0]["conflict"]
    assert service.review(doc)[0]["current"] != "wrong"
    doc["items"][0]["changes"]["title"]["before"] = api.items["current-id"]["media"]["metadata"]["title"]
    plan = service.preview(doc)
    api.mutate_after = lambda raw: raw["media"].update(tags=["unexpected"])
    assert service.apply(plan, "APPLY " + plan["digest"][:12])[0]["state"] == "uncertain"
    assert service.check_results()[0]["state"] == "uncertain"


def test_legacy_guard_and_damaged_journal(tmp_path):
    import sqlite3

    import pytest

    service, api, _doc = prepare(tmp_path)
    with sqlite3.connect(service.legacy) as db:
        db.execute("CREATE TABLE journal (state TEXT)")
        db.execute("INSERT INTO journal VALUES ('uncertain')")
    with pytest.raises(ValueError, match="Legacy"):
        service.ready()
    service.journal.write_text("{incomplete", encoding="utf-8")
    with pytest.raises(ValueError):
        service.ready()
    assert not api.writes


def test_running_legacy_app_also_blocks_uncertain_bulk(setup):
    from pathlib import Path

    import pytest

    from abs_manager.bulk import Bulk
    from abs_manager.workflow import SafetyError, Workflow

    store, api, _ = setup
    journal = Bulk(None, Path(store.path).parent / "bulk", store.path)
    journal.record("synthetic-operation", "uncertain", {})
    with pytest.raises(SafetyError, match="uncertain bulk"):
        Workflow(store, api)._ready(api.server, "library-a")
    assert not api.writes
