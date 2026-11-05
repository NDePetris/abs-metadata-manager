"""Synthetic-only browser fixture for the actual native S4 controls."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ["ABS_MM_DB"] = str(ROOT / ".test-tmp-s4-visual" / "visual.sqlite")
os.environ["ABS_MM_SETTINGS"] = str(ROOT / ".test-tmp-s4-visual" / "settings")

import streamlit as st
from conftest import FakeAdapter
from test_quality_policy import clean, reference
from test_verification import FakeSource

from abs_manager import preferences
from abs_manager.store import Store
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import run_audit

books = [clean(f"audio-{i}") for i in range(3)]
for i, b in enumerate(books):
    b["media"]["metadata"].update(narrators=[])
books[2]["media"]["metadata"]["title"] = "A very long title about journeys across distant worlds"
api = FakeAdapter(books)
store = Store(os.environ["ABS_MM_DB"])
if not store.audits():
    aid = run_audit(store, api, api.libraries()[0])
    source = FakeSource()
    source.search = lambda q: {"candidates": [reference()]}
    run_verification(store, start_verification(store, aid, source, [b["id"] for b in books]), source)
if not preferences.load():
    preferences.save({"server": api.server, "library": "library-a"})
st.session_state["adapter"] = api
st.session_state["reconnect_checked"] = True

from app import main

main()
