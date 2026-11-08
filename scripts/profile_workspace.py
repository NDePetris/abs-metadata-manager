"""Small, isolated page-load benchmark; no real credentials, database or transport."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from conftest import FakeAdapter, make_book
from streamlit.testing.v1 import AppTest
from test_review_import import save

from abs_manager.store import Store


def benchmark():
    with tempfile.TemporaryDirectory(prefix="abs-ui-", ignore_cleanup_errors=True) as directory:  # noqa: SIM117
        with patch.dict(os.environ, {"ABS_MM_DB": str(Path(directory) / "ui.sqlite"),
                                    "ABS_MM_SETTINGS": str(Path(directory) / "settings")}):
            store = Store(os.environ["ABS_MM_DB"])
            raw = [make_book(title=f"Example {i:02}", item_id=f"book-{i}") for i in range(12)]
            aid = save(store, raw)
            for book in store.books(aid):
                store.confirm_targets(aid, book.id, {"description": "Édition 世界\n\n" * 500}, "Synthetic")
            # History payloads are intentionally long; overview must not deserialize all of them.
            for i in range(24):
                store.record(FakeAdapter.server, "library-a", f"book-{i % 12}", "apply", "verified",
                             {"description": "Historical text " * 2000})
            api = FakeAdapter(raw)
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
            at.session_state["adapter"] = api
            at.session_state["library"] = api.libraries()[0]
            result = {"books": 12, "operations": 24}

            def render(label):
                start = time.perf_counter()
                at.run()
                assert not at.exception, at.exception
                assert not at.error, [e.value for e in at.error]
                result[label] = {"ms": round((time.perf_counter() - start) * 1000, 1),
                                 "bytes": sum(e.proto.ByteSize() for e in at if getattr(e, "proto", None)),
                                 "editors": len(at.text_area)}

            render("cold_start")
            nav = next(w for w in at.radio if w.label == "Workspace")
            modern = "Review" in nav.options
            nav.set_value("Review" if modern else "Changes")
            render("review" if modern else "changes")
            render("rerun")
            next(w for w in at.radio if w.label == "Workspace").set_value("Activity" if modern else "History")
            render("activity")
            render("activity_rerun")
            assert not api.reads and not api.writes
            return result


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
