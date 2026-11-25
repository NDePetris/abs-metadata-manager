"""Append-only local write history. Callers hold the workspace lock."""

import os

from .files import identifier, keys, strict_json
from .models import Book, compact, payload

PENDING = {"intent", "uncertain"}
INITIAL = {"intent", "conflict", "already_applied"}
RESOLUTIONS = {"verified", "not_applied", "uncertain", "abandoned"}


def records(path):
    latest = {}
    if not path.exists():
        return latest
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("Incomplete journal event.")
                event = strict_json(line)
                keys(event, ("op_id", "run_id", "state", "message"), ("data",))
                op_id = identifier(event["op_id"], 32)
                identifier(event["run_id"], 32)
                if not isinstance(event["message"], str):
                    raise TypeError("Invalid journal message.")
                previous = latest.get(op_id)
                if previous is None:
                    if event["state"] not in INITIAL:
                        raise ValueError("Invalid initial journal state.")
                    data = event["data"]
                    keys(data, ("server", "library_id", "item_id", "raw", "changes"))
                    book = Book(data["raw"])
                    if (
                        not isinstance(data["server"], str) or not data["server"]
                        or book.id != data["item_id"] or book.raw["libraryId"] != data["library_id"]
                    ):
                        raise ValueError("Invalid journal identity.")
                    if event["state"] == "intent":
                        payload(data["changes"])
                else:
                    if (
                        "data" in event or previous["state"] not in PENDING
                        or event["state"] not in RESOLUTIONS or event["run_id"] != previous["run_id"]
                    ):
                        raise ValueError("Invalid journal state transition.")
                    event["data"] = previous["data"]
                latest[op_id] = event
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
        raise ValueError("Invalid or incomplete write journal. Investigate before continuing.") from exc
    return latest


def record(path, op_id, run_id, state, message="", data=None):
    event = {"op_id": op_id, "run_id": run_id, "state": state, "message": message}
    if data is not None:
        event["data"] = data
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(compact(event) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
