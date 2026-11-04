from test_streamlined import app, page, widget

from abs_manager.workflow import run_audit


def test_accept_multiple_fields_reimport_and_reopen(setup, monkeypatch):
    store, api, _aid = setup
    at = app(store, api, monkeypatch)
    page(at, "Match & Verify")
    widget(at, "checkbox", "Title").check().run()
    widget(at, "checkbox", "Authors").check().run()
    widget(at, "button", "Confirm").click().run()
    assert not at.exception
    new = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(new, "current-id")["title"]["valid"]
    api.items["current-id"]["media"]["metadata"]["title"] = "Unexpected edit"
    latest = run_audit(store, api, api.libraries()[0])
    assert store.field_decisions(latest, "current-id")["title"]["decision"] == "reopened"
    assert store.field_decisions(latest, "current-id")["authors"]["valid"]
    assert not api.writes
