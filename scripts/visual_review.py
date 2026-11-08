"""Two-book synthetic desktop fixture. Never uses real settings or services."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ["ABS_MM_DB"] = str(ROOT / ".test-tmp-review-visual" / "visual.sqlite")
os.environ["ABS_MM_SETTINGS"] = str(ROOT / ".test-tmp-review-visual" / "settings")

import streamlit as st
from conftest import FakeAdapter, make_book
from test_review_import import encode, example_document, proposal, save

from abs_manager import preferences
from abs_manager.store import Store

raw = [make_book(title="Heaven’s River", item_id="alpha"),
       make_book(title="The Long Way to a Small, Angry Planet", item_id="beta", authors=["Becky Chambers"])]
api = FakeAdapter(raw)
store = Store(os.environ["ABS_MM_DB"])
if not store.audits():
    save(store, raw)
aid = store.audits()[0]["id"]
doc = example_document(store, aid)
for book in store.books(aid):
    doc["recommendations"].extend([
        proposal(book, "subtitle", "A novel"),
        proposal(book, "description", "An unexpected journey across distant worlds.\n\nA complete, individually reviewed description. 世界’s edition.", "editorial_review"),
        proposal(book, "genres", ["Fantasy", "Romance", "Fantasy Romance"])])
data = encode(doc)
preferences.save({"server": api.server, "library": "library-a"})
st.session_state["adapter"] = api
st.session_state["reconnect_checked"] = True
# Keep the actual uploader visible; use a synthetic default until a file is chosen.
uploader = st.file_uploader


def fixture_upload(label, *args, **kwargs):
    upload = uploader(label, *args, **kwargs)
    return (upload or SimpleNamespace(getvalue=lambda: data)) if label == "Reviewed recommendations JSON" else upload


st.file_uploader = fixture_upload
from app import main

try:
    main()
finally:
    st.file_uploader = uploader
