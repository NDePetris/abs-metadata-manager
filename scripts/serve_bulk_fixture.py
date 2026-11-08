"""Synthetic loopback browser harness. Never connects to an ABS server."""

import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FakeAdapter, make_book

from abs_manager.bulk import Bulk
from abs_manager.bulk_app import create_app

books = [make_book(title=f"Synthetic Book {i:03}", item_id=f"book-{i}") for i in range(500)]
books[-1]["media"].update(duration=0, audioFiles=[])
with tempfile.TemporaryDirectory(prefix=".test-tmp-browser-", dir=ROOT) as directory:
    bulk = Bulk(FakeAdapter(books), Path(directory) / "bulk", Path(directory) / "absent.sqlite3")
    uvicorn.run(create_app(bulk), host="127.0.0.1", port=8517, log_level="warning", access_log=False)
