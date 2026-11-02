from test_streamlined import app, page, widget


def test_lookup_executes_immediately_and_reruns_do_not_fetch(setup, monkeypatch):
    store, api, _aid = setup
    calls = []
    api.metadata_search = lambda provider, query: calls.append((provider, query)) or []
    at = app(store, api, monkeypatch)
    at.session_state["providers"] = [{"value": "audible"}]
    at.session_state["provider_discovery"] = {"server": api.server, "status": "available",
                                               "providers": at.session_state["providers"]}
    page(at, "Match & Verify")
    widget(at, "button", "Search").click().run()
    assert not at.exception and len(calls) == 1
    at.run()
    assert len(calls) == 1 and not api.writes


def test_reject_then_search_another_source(setup, monkeypatch):
    store, api, aid = setup
    calls = []
    def search(provider, query):
        calls.append(provider)
        return [{"id": "recording", "title": query["title"], "author": "Dennis E. Taylor", "narrator": "Other"}]
    api.metadata_search = search
    at = app(store, api, monkeypatch)
    at.session_state["providers"] = [{"value": "audible"}, {"value": "audnexus"}]
    at.session_state["provider_discovery"] = {"server": api.server, "status": "available",
                                               "providers": at.session_state["providers"]}
    page(at, "Match & Verify")
    widget(at, "button", "Search").click().run()
    widget(at, "button", "Not my edition").click().run()
    assert not at.exception
    widget(at, "selectbox", "Source").set_value("abs:audnexus")
    widget(at, "button", "Search another source").click().run()
    assert calls == ["audible", "audnexus"]
    assert any(d["decision"] == "rejected" for d in store.candidate_decisions(aid, "current-id").values())
    assert widget(at, "selectbox", "Book").value == "current-id" and not api.writes
