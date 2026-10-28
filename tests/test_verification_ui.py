from pathlib import Path

from streamlit.testing.v1 import AppTest

from abs_manager.adapter import CliAdapter
from abs_manager.models import Book
from abs_manager.sources import AbsSource
from abs_manager.verification import candidate
from abs_manager.verification_workflow import start_verification

APP = Path(__file__).resolve().parents[1] / "app.py"


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


def test_verification_empty_and_sample_render_reruns_no_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_MM_DB", str(tmp_path / "ui.sqlite3"))
    monkeypatch.setattr(CliAdapter, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Rendering cannot call transport")))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    widget(at, "radio", "Workspace").set_value("Verification").run()
    assert not at.exception
    assert any("Complete an audit" in i.value for i in at.info)
    widget(at, "radio", "Workspace").set_value("Library").run()
    widget(at, "button", "Load read-only sample library").click().run()
    widget(at, "radio", "Workspace").set_value("Verification").run()
    assert not at.exception
    assert len(at.dataframe[2].value) == 6  # matrix includes all sample books
    widget(at, "text_input", "Search quality report").set_value("no-such-title").run()
    assert any("No books match" in i.value for i in at.info)
    assert not at.exception
    widget(at, "text_input", "Search quality report").set_value("").run()
    widget(at, "text_area", "Human review evidence / source reference").set_value("Compared edition page")
    widget(at, "button", "Record manual review").click().run()
    assert not at.exception
    at.run()
    assert not at.exception


def test_lookup_explicit_submit_resume_and_reruns(setup, monkeypatch):
    store, api, aid = setup
    monkeypatch.setenv("ABS_MM_DB", store.path)
    requests = []
    api.metadata_providers = lambda: [{"value": "audible", "text": "Audible"}]

    def search(provider, query):
        requests.append((provider, query))
        b = store.book(aid, "current-id")
        return [{"title": b.values["title"], "author": b.values["authors"],
                 "narrator": b.values["narrators"], "duration": 1000 / 60}]

    api.metadata_search = search
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["adapter"] = api
    at.run()
    widget(at, "radio", "Workspace").set_value("Verification").run()
    widget(at, "button", "Discover server metadata providers").click().run()
    assert not at.exception
    widget(at, "multiselect", "Books for lookup").set_value(["current-id"])
    widget(at, "button", "Prepare online verification run").click().run()
    assert not requests
    widget(at, "button", "Start / resume read-only lookups").click().run()
    assert not at.exception
    assert len(requests) == 1
    assert any("recording" in w.value for w in at.markdown)
    widget(at, "text_input", "Edition selection evidence").set_value("Same recording runtime and credits")
    widget(at, "button", "Record human edition selection").click().run()
    for _ in range(2):
        at.run()
        assert not at.exception
    assert len(requests) == 1
    assert not api.writes
    assert store.verification_annotations(aid, "current-id")["edition"]["date"]


def test_cancel_run_ui_and_export_render_do_not_fetch(setup, monkeypatch):
    store, api, aid = setup
    monkeypatch.setenv("ABS_MM_DB", store.path)
    source = AbsSource(api, "audible")
    rid = start_verification(store, aid, source, ["current-id"])
    api.metadata_search = lambda *args: (_ for _ in ()).throw(AssertionError("No request expected"))
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["adapter"] = api
    at.run()
    widget(at, "radio", "Workspace").set_value("Verification").run()
    widget(at, "button", "Cancel pending lookups").click().run()
    assert not at.exception
    assert store.verification_runs(aid)[0]["id"] == rid
    assert store.verification_runs(aid)[0]["state"] == "cancelled"
    assert not api.writes


def test_verification_ebook_never_enables_corrections(setup, monkeypatch):
    store, api, aid = setup
    raw = api.items["current-id"]
    raw["id"] = "ebook"
    raw["media"].update(audioFiles=[], duration=0)
    store.add_book(aid, Book(raw))
    from test_verification import FakeSource

    from abs_manager.verification_workflow import run_verification
    source = FakeSource()
    c = candidate({"title": raw["media"]["metadata"]["title"], "author": "Dennis E. Taylor"}, "test")
    source.search = lambda query: {"raw": [], "candidates": [c]}
    rid = start_verification(store, aid, source, ["ebook"])
    run_verification(store, rid, source)
    monkeypatch.setenv("ABS_MM_DB", store.path)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    widget(at, "radio", "Workspace").set_value("Verification").run()
    widget(at, "selectbox", "Inspect verification book").set_value("ebook").run()
    assert not at.exception
    assert widget(at, "button", "Create pending correction in Review").disabled
