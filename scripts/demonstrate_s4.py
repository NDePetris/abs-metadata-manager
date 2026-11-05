"""Reproducible synthetic library proposal/batch exercise. Never reads user settings."""
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from conftest import FakeAdapter
from test_quality_policy import clean, reference
from test_verification import FakeSource

from abs_manager.calibre import import_catalog
from abs_manager.correction_plan import completion_report, library_plan, stage_selected
from abs_manager.store import Store
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import SafetyError, Workflow, batch_results, confirmation, preview, run_audit


def demonstrate():
    with tempfile.TemporaryDirectory(prefix="abs-s4-demo-") as directory:
        books = [clean(f"audio-{i:03}") for i in range(100)]
        for i, raw in enumerate(books):
            if i < 20:
                raw["media"]["metadata"]["narrators"] = []
            if 20 <= i < 30:
                raw["media"]["metadata"].pop("asin")
            if 30 <= i < 40:
                raw["media"]["metadata"]["title"] = "Example (Unabridged)"
            if 40 <= i < 45:
                raw["media"]["metadata"]["authors"] = [{"name": "Sarah J Maas"}]
        for i in range(650):
            raw = clean(f"ebook-{i:03}", False)
            raw["path"] = raw["relPath"] = f"Author One/Example ({i + 1})"
            books.append(raw)
        api = FakeAdapter(books)
        store = Store(Path(directory) / "synthetic.sqlite")
        aid = run_audit(store, api, api.libraries()[0])
        source = FakeSource()
        source.query = lambda b: {"title": b.values["title"], "asin": b.metadata.get("asin")}
        source.search = lambda q: {"candidates": [reference()] if q["asin"] else [
            reference(asin="B111111111"), reference(asin="B222222222")]}
        run_verification(store, start_verification(store, aid, source, [b["id"] for b in books[:100]]), source)
        import_catalog(store, aid, json.dumps([{"id": i + 1, "uuid": f"uuid-{i}", "title": "Example",
                                               "authors": ["Author One"], "series": None,
                                               "identifiers": {"asin": "B012345678"}} for i in range(600)]),
                       str(Path(directory) / "synthetic-calibre"))
        started = time.perf_counter()
        report = store.verification_report(aid)
        report_seconds = time.perf_counter() - started
        started = time.perf_counter()
        plan = library_plan(store, aid, report)
        plan_seconds = time.perf_counter() - started
        completion = completion_report(store, aid, report, plan)
        selected = [p["id"] for p in plan["proposals"] if p["grouped"]]
        stage_selected(store, plan, selected)
        findings = [f for f in store.findings(aid) if f["kind"] == "confirmed_target"]
        results = []
        for offset in range(0, len(findings), 10):
            ids = [f["id"] for f in findings[offset:offset + 10]]
            store.review(ids, "approved")
            batch = preview(store, aid, ids)
            Workflow(store, api).apply(batch, confirmation(batch))
            results.extend(batch_results(store, batch))
        # An actual fake timeout-after-acceptance leaves durable uncertainty and stops the next item.
        for iid in ("audio-090", "audio-091"):
            store.confirm_targets(aid, iid, {"subtitle": "Synthetic correction"})
        ids = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target" and f["status"] == "pending"]
        store.review(ids, "approved")
        interrupted = preview(store, aid, ids)
        api.fail = "after"
        try:
            Workflow(store, api).apply(interrupted, confirmation(interrupted))
        except SafetyError:
            pass
        blocked = False
        try:
            Workflow(store, api)._ready(api.server, "library-a")
        except SafetyError:
            blocked = True
        result = {"books": len(report), "plan_categories": plan["counts"],
                "automatic_field_passes": completion["pass_bases"], "proposed_fields": len(plan["proposals"]),
                "group_eligible_fields": len(selected), "individual_review": len(plan["proposals"]) - len(selected),
                "manual_books": sum(bool(r["manual_fields"]) for r in plan["inventory"]),
                "group_staging_submits": 1, "previous_per_book_staging_submits": len({p["item_id"] for p in plan["proposals"] if p["grouped"]}),
                "write_authorizations": (len(findings) + 9) // 10,
                "simulated_verified_items": sum(r["status"] == "Verified applied" for r in results),
                "simulated_per_item_writes": len(api.writes), "unresolved_journal_blocks": blocked,
                "interrupted_results": [r["status"] for r in batch_results(store, interrupted)],
                "report_seconds": round(report_seconds, 4), "plan_seconds": round(plan_seconds, 4),
                "production_validated": False}
        logging.shutdown()
        return result


if __name__ == "__main__":
    print(json.dumps(demonstrate(), indent=2))
