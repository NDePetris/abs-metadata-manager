"""Synthetic-only report benchmark; never opens user settings or existing databases."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from test_persistent_state import snapshot
from test_quality_policy import clean

from abs_manager.store import Store
from abs_manager.verification_ui import quality_queue


def benchmark(legacy_queue=False, ui=False):
    with tempfile.TemporaryDirectory(prefix="abs-s3-profile-") as directory:
        store = Store(Path(directory) / "synthetic.sqlite")
        started = time.perf_counter()
        raw = [clean(f"audio-{i}") for i in range(100)] + [clean(f"ebook-{i}", False) for i in range(650)]
        if ui:
            for book in raw:
                book["media"]["metadata"]["genres"] = ["Science Fiction"]
        aid = snapshot(store, raw)
        setup = time.perf_counter() - started
        counts = {}
        original = store.rows

        def counted(query, args=()):
            counts[query] = counts.get(query, 0) + 1
            return original(query, args)

        store.rows = counted
        started = time.perf_counter()
        report = store.verification_report(aid)
        elapsed = time.perf_counter() - started
        started = time.perf_counter()
        if legacy_queue:
            # S2 selector sorts all titles and rescans the library for each duplicate label.
            report.sort(key=lambda r: str(r["fields"]["title"]["value"]))
            for r in report:
                sum(x["fields"]["title"]["value"] == r["fields"]["title"]["value"] for x in report)
        else:
            quality_queue(report)
        queue_seconds = time.perf_counter() - started
        rendering = {}
        if ui:
            from unittest.mock import patch

            from streamlit.testing.v1 import AppTest
            script = '''
import streamlit as st
from abs_manager.store import Store
from abs_manager.verification_ui import verification_view
st.set_page_config(layout="wide")
store = Store(st.session_state["fixture_db"])
verification_view(store, lambda s: s.audit(st.session_state["fixture_audit"]))
'''
            with patch.dict(os.environ, {"ABS_MM_SETTINGS": str(Path(directory) / "isolated-settings")}):
                app = AppTest.from_string(script, default_timeout=120)
                app.session_state["fixture_db"] = store.path
                app.session_state["fixture_audit"] = aid
                started = time.perf_counter()
                app.run()
                rendering["initial_render_seconds"] = round(time.perf_counter() - started, 4)
                started = time.perf_counter()
                app.run()
                rendering["rerender_seconds"] = round(time.perf_counter() - started, 4)
                assert not app.exception
        for handler in store.log.handlers[:]:
            handler.close()
            store.log.removeHandler(handler)
        return {"books": len(report), "setup_seconds": round(setup, 4),
                "report_seconds": round(elapsed, 4), "queue_seconds": round(queue_seconds, 4), **rendering,
                "select_queries": sum(counts.values()),
                "queries": sorted([(n, q) for q, n in counts.items()], reverse=True)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-queue", action="store_true")
    parser.add_argument("--ui", action="store_true", help="Also measure synthetic categorized-library Streamlit rendering")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.legacy_queue, args.ui), indent=2))
