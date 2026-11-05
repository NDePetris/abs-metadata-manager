"""Run a synthetic-only desktop review fixture: python -m streamlit run scripts/visual_fixture.py."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["ABS_MM_DB"] = str(ROOT / ".test-tmp-s3-visual" / "visual.sqlite")
os.environ["ABS_MM_SETTINGS"] = str(ROOT / ".test-tmp-s3-visual" / "settings")

import streamlit as st
from conftest import FakeAdapter, make_book

from abs_manager import preferences
from abs_manager.store import Store
from abs_manager.verification import candidate
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import run_audit

st.set_page_config(page_title="Audiobookshelf Metadata Manager", layout="wide", initial_sidebar_state="collapsed")

books = [make_book(title="Heaven's River", item_id="ordinary"),
         make_book(title="A very long title about the extraordinary journeys of several unlikely companions across distant worlds", item_id="long",
                   authors=["Author, First", "Second Author"], narrators=["Narrator One", "Narrator Two"]),
         make_book(title="No reference", item_id="manual", series=None),
         make_book(title="A book-level reference", item_id="work"),
         make_book(title="An ebook", item_id="ebook")]
books[1]["media"]["metadata"]["series"].append({"name": "Another series", "sequence": "2.5"})
books[-1]["media"].update(duration=0, audioFiles=[])


class FixtureSource:
    id, scope, targeted_only = "synthetic", "public", False

    def query(self, book):
        return {"title": book.values["title"]}

    def search(self, query):
        title = query.get("title", query.get("q", "Synthetic result"))
        return {"candidates": [candidate({"id": title, "title": title,
            "author": ["Dennis E. Taylor"], "narrator": ["Ray Porter"], "duration": 1000,
            "publishedYear": "2020", "series": [{"name": "Bobiverse", "sequence": "4"}]},
            self.id, work_only=title == "A book-level reference" or self.id in ("openlibrary", "googlebooks"))]}


def fixture_source(source_id, adapter):
    source = FixtureSource()
    source.id = source_id
    source.scope = adapter.server if source_id.startswith("abs:") else "public"
    return source


api = FakeAdapter(books)
def synthetic_cover(iid):
    import io

    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (160, 220), "steelblue" if iid == "ordinary" else "darkred").save(output, "PNG")
    return output.getvalue()


api.cover = synthetic_cover
api.metadata_search = lambda provider, query: [{"id": "synthetic-" + provider, "title": query["title"],
    "author": ["Dennis E. Taylor"], "narrator": ["Ray Porter"], "duration": 1000 / 60}]
store = Store(os.environ["ABS_MM_DB"])
if not store.audits():
    aid = run_audit(store, api, api.libraries()[0])
    src = FixtureSource()
    run_verification(store, start_verification(store, aid, src, ["ordinary", "long", "work"]), src)
    store.confirm_targets(aid, "long", {"subtitle": "A pending correction"})
    store.confirm_targets(aid, "manual", {"title": "No reference"})
if not preferences.load():
    preferences.save({"server": api.server, "library": "library-a"})
st.session_state["adapter"] = api
st.session_state["reconnect_checked"] = True
st.session_state["providers"] = [{"value": "audible"}, {"value": "audnexus"}]

st.session_state["provider_discovery"] = {"server": api.server, "status": "available",
                                        "providers": st.session_state["providers"]}
api.metadata_providers = lambda: st.session_state["providers"]

from abs_manager import verification_ui
from app import main

# All source choices, including the public-source controls, remain synthetic.
verification_ui.source_for = fixture_source

main()
