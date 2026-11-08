"""Synthetic description/history benchmark. Never opens existing settings or databases."""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from conftest import FakeAdapter, make_book
from streamlit.testing.v1 import AppTest
from test_review_import import encode, example_document, proposal, save

from abs_manager.models import compact
from abs_manager.store import Store
from abs_manager.workflow import operation_comparison, preview


def measure(call, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = call()
        samples.append(time.perf_counter() - start)
    return {"median_ms": round(median(samples) * 1000, 2)}, result


def render(app):
    timing, _ = measure(app.run, 3)
    assert not app.exception, app.exception
    timing["element_proto_bytes"] = sum(e.proto.ByteSize() for e in app if getattr(e, "proto", None) is not None)
    return timing


def benchmark():
    with tempfile.TemporaryDirectory(prefix="abs-description-profile-", ignore_cleanup_errors=True) as directory:  # noqa: SIM117
        # Empty settings, explicit fake adapter, no inherited ABS configuration.
        with patch.dict(os.environ, {"ABS_MM_DB": str(Path(directory) / "synthetic.sqlite"),
                                    "ABS_MM_SETTINGS": str(Path(directory) / "settings")}):
            store = Store(os.environ["ABS_MM_DB"])
            description = ("Roshar’s Édition — 世界. " + "A complete sentence. " * 30 + "\n\n") * 10
            raw = [make_book(title=f"Book {i:03}", item_id=f"book-{i}") for i in range(110)]
            for item in raw:
                item["media"]["metadata"]["description"] = description
            aid = save(store, raw)
            document = example_document(store, aid)
            books = store.books(aid)
            for book, suggested in zip(books[:55], books[55:]):
                document["recommendations"].append(proposal(suggested, "description", description + "Approved ending.",
                                                            "editorial_review"))
                store.confirm_targets(aid, book.id, {"description": description + "Approved ending."}, "Synthetic")
            ids = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target"]
            store.review(ids, "approved")
            plan = preview(store, aid, ids[:10])
            with store.connect() as db:
                for i in range(220):
                    group = plan["books"][i % len(plan["books"])]
                    observed = copy.deepcopy(raw[int(group["identity"]["id"].split("-")[1])])
                    observed["media"]["metadata"].update(group["changes"])
                    data = {**group, "after": {**group["before"], **group["changes"]},
                            "pre_update_raw": raw[i % 10], "batch_plan": plan,
                            "preview_digest": plan["digest"], "audit_id": aid}
                    db.execute("INSERT INTO journal(server,library,item_id,action,state,created,updated,data,observed) "
                               "VALUES(?,?,?,'apply',?,'synthetic','synthetic',?,?)",
                               (FakeAdapter.server, "library-a", group["identity"]["id"],
                                ("verified", "uncertain", "prepared", "applied")[i % 4],
                                compact(data), compact(observed)))
            result = {"books": 110, "operations": 220, "description_characters": len(description)}
            result["history_summaries"], summaries = measure(
                lambda: store.history_summaries() if hasattr(store, "history_summaries") else store.history())
            result["history_summaries"]["returned_json_bytes"] = len(compact(summaries).encode())
            result["selected_operation"], operation = measure(lambda: store.operation(220))
            result["description_comparison"], comparison = measure(
                lambda: operation_comparison(operation["data"], operation["observed"]))
            result["description_comparison"]["json_bytes"] = len(compact(comparison).encode())
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
            app.session_state["adapter"] = FakeAdapter(raw)
            app.session_state["library"] = {"id": "library-a", "name": "Synthetic", "mediaType": "book"}
            app.run()
            next(w for w in app.radio if w.label == "Workspace").set_value("Review")
            result["changes_overview"] = render(app)
            next(w for w in app.radio if w.label == "Workspace").set_value("Activity")
            result["history_view"] = render(app)
            upload = AppTest.from_string(f"""
from abs_manager.store import Store
from abs_manager.review_import_ui import import_corrections
store = Store({store.path!r})
import_corrections(store, store.audit({aid}))
""", default_timeout=120).run()
            upload.file_uploader[0].upload("synthetic.json", encode(document), "application/json").run()
            result["recommendations_rerun"] = render(upload)
            next(w for w in upload.selectbox if w.label == "Field").select("description")
            result["recommendations_filter"] = render(upload)
            editor_key = upload.dataframe[0].proto.id.split("-", 2)[2]
            upload.session_state[editor_key] = {"edited_rows": {0: {"Select": True}},
                                                 "added_rows": [], "deleted_rows": []}
            result["recommendations_selection"] = render(upload)
            # Include the full selected recommendation only through the explicit details control.
            next(w for w in upload.selectbox if w.label == "Recommendation details").select_index(0)
            result["recommendation_details"] = render(upload)
            for handler in store.log.handlers[:]:
                handler.close()
                store.log.removeHandler(handler)
            return result


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
