"""Reproducible no-secret 100 audio / 650 ebook review-effort demonstration."""
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from conftest import FakeAdapter
from test_quality_policy import clean, reference
from test_s3_review import image_bytes
from test_verification import FakeSource

from abs_manager.calibre import import_catalog
from abs_manager.models import review_fields
from abs_manager.store import Store
from abs_manager.verification_workflow import run_verification, start_verification
from abs_manager.workflow import run_audit


def demonstrate():
    with tempfile.TemporaryDirectory(prefix="abs-s3-demo-") as directory:
        books = [clean(f"audio-{i}") for i in range(100)]
        for i, raw in enumerate(books):
            if 60 <= i < 70:
                raw["media"]["metadata"].pop("asin")  # Work match, ambiguous recording.
            if 70 <= i < 80:
                raw["media"]["metadata"]["title"] = ""  # Essential defect.
            if i < 20:
                raw["media"]["coverPath"] = None
        for i in range(650):
            raw = clean(f"ebook-{i}", False)
            raw["path"] = raw["relPath"] = f"Author One/Example ({i + 1})"
            if i < 30:
                raw["media"]["coverPath"] = None
            books.append(raw)
        for i, raw in enumerate(books):
            raw["media"]["metadata"]["genres"] = ["Science Fiction"]
            raw["media"]["tags"] = ["keep"] if i >= 20 else (
                ["Unwanted"] if i < 10 else ["Review" if i % 2 else "review"])
        api = FakeAdapter(books)
        api.cover = lambda iid: image_bytes()
        store = Store(Path(directory) / "synthetic.sqlite")
        aid = run_audit(store, api, api.libraries()[0])
        source = FakeSource()
        source.query = lambda book: {"title": book.values["title"], "asin": book.metadata.get("asin")}
        source.search = lambda q: {"candidates": [reference()] if q.get("asin") else [
            reference(asin="B111111111"), reference(asin="B222222222")]}
        full = [f"audio-{i}" for i in range(90)]
        run_verification(store, start_verification(store, aid, source, full), source, limit=100)
        source.search = lambda q: {"state": "partial", "candidates": [reference()]}
        run_verification(store, start_verification(store, aid, source, [f"audio-{i}" for i in range(90, 100)], refresh=True), source)
        # An imported synthetic Calibre catalog, not fabricated independent work evidence.
        import_catalog(store, aid, json.dumps([
            {"id": i + 1, "uuid": f"synthetic-{i}", "title": "Example", "authors": ["Author One"],
             "series": None, "identifiers": {"asin": "B012345678"}} for i in range(600)]),
                       library=str(Path(directory) / "synthetic-calibre"))
        for i in range(80, 90):
            store.confirm_targets(aid, f"audio-{i}", {"subtitle": "Pending correction"})
        before = store.verification_report(aid)
        store.decide_category(aid, "genres", "Science Fiction", "Acceptable")
        store.decide_category(aid, "tags", "keep", "Acceptable")
        store.decide_category(aid, "tags", "Unwanted", "Unwanted")
        after_categories = store.verification_report(aid)
        # Twelve explicitly retrieved and individually inspected covers, representing one gallery page.
        for i in range(20, 32):
            iid = f"audio-{i}"
            _, observation = store.observe_cover(aid, iid, api)
            store.confirm_cover(aid, iid, observation["content_digest"])
        after = store.verification_report(aid)
        fields = [d for r in after for d in r["fields"].values() if d["quality_applicable"]]
        editable = [(f, r["fields"][f]) for r in after for f in review_fields(r["type"] == "audiobook")]
        automatic = {"independently_verified", "calibre_authority"}
        result = {
            "books": len(after), "dispositions": dict(Counter(r["disposition"] for r in after)),
            "automatic_evidence_pass_fields": sum(d["reason_code"] in automatic for d in fields),
            "automatic_evidence_pass_editable_fields": sum(d["reason_code"] in automatic for _, d in editable),
            "previous_manual_editable_field_confirmations": len(editable),
            "remaining_individual_bibliographic_field_checks": sum(d["disposition"] != "Pass" for _, d in editable),
            "books_with_bibliographic_exceptions": sum(any(r["fields"][f]["disposition"] != "Pass" for f in review_fields(r["type"] == "audiobook")) for r in after),
            "category_preferences_recorded": 3,
            "category_fields_resolved_by_library_preferences": sum(
                a["fields"][f]["disposition"] != "Pass" and b["fields"][f]["disposition"] == "Pass"
                for a, b in zip(before, after_categories) for f in ("genres", "tags")),
            "covers_inspected_on_one_gallery_page": 12,
            "remaining_cover_checks": sum(r["fields"]["cover"]["disposition"] != "Pass" for r in after),
            "pending_corrections": len(store.corrections(aid)), "remote_writes": len(api.writes),
            "ambiguous_audiobook_editions": sum(r["type"] == "audiobook" and r["ambiguous"] for r in after),
            "calibre_matched_ebooks": sum(r["type"] == "ebook" and any(
                p["source"] == "Calibre catalog" for p in r["fields"]["title"]["provenance"]) for r in after),
        }
        for handler in store.log.handlers[:]:
            handler.close()
            store.log.removeHandler(handler)
        return result


if __name__ == "__main__":
    print(json.dumps(demonstrate(), indent=2))
