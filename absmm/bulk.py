"""Export and the sole write and readback path."""

from uuid import uuid4

from .adapter import AdapterError, inventory, membership
from .files import write_text
from .journal import PENDING, record, records
from .locking import operation_lock
from .models import Book, compact, digest, state_matches


def book_libraries(adapter):
    return [library for library in adapter.libraries() if library.get("mediaType") == "book"]


def matches(live, data, after=False):
    before = Book(data["raw"])
    fields = data["changes"]
    expected = before.values | (fields if after else {})
    return (
        live.identity == before.identity and live.writable
        and state_matches(expected, live.values, membership_only=after and "authors" in fields)
        and live.protected(fields) == before.protected(fields)
    )


def verified_message(live, targets):
    if "authors" in targets and live.values["authors"] != targets["authors"]:
        return "Author membership verified. Author order differs; review manually in ABS."
    return "Readback verified."


class Bulk:
    def __init__(self, adapter, directory):
        self.adapter = adapter
        self.directory = directory
        self.lock = directory / "workspace"
        self.journal = directory / "writes.jsonl"

    def export(self, library):
        with operation_lock(self.lock):
            known = book_libraries(self.adapter)
            if library is None and len(known) == 1:
                library = known[0]["id"]
            if not any(row["id"] == library for row in known):
                raise ValueError("Select a book library with --library or ABS_LIBRARY. Use --list-libraries for IDs.")
            members = membership(self.adapter, library)
            books = list(inventory(self.adapter, members))
            if membership(self.adapter, library) != members:
                raise ValueError("Library changed during export. Export again.")
            snapshot_id = uuid4().hex
            snapshot = {
                "snapshot_id": snapshot_id, "server": self.adapter.server, "library_id": library,
                "books": {book.id: book.raw for book in books},
            }
            exported = []
            for book in books:
                media = book.raw["media"]
                exported.append({
                    "item_id": book.id, "identity": digest(book.identity),
                    "kind": "mixed" if book.is_audio and book.is_ebook else (
                        "audiobook" if book.is_audio else "ebook" if book.is_ebook else "book"
                    ),
                    "has_audio": book.is_audio, "has_ebook": book.is_ebook,
                    "current": book.values,
                    "context": {key: book.metadata.get(key) for key in (
                        "publisher", "isbn", "language", "publishedDate", "abridged",
                    )},
                    "cover_present": bool(media.get("coverPath")),
                    "chapters": [
                        {key: chapter.get(key) for key in ("title", "start", "end")}
                        for chapter in media.get("chapters") or []
                    ],
                })
            document = {"schema_version": 2, "snapshot_id": snapshot_id, "books": exported}
            private = self.directory / f"snapshot-{snapshot_id}.json"
            public = self.directory / f"export-{snapshot_id}.json"
            write_text(private, compact(snapshot))
            write_text(public, compact(document))
            return public, len(books)

    def apply(self, snapshot, manifest, selected, limited, library, confirm, backup):
        if snapshot["server"] != self.adapter.server or (library and library != snapshot["library_id"]):
            raise ValueError("Approval is for another ABS server or library.")
        if not selected:
            return []
        with operation_lock(self.lock):
            history = records(self.journal)
            if any(event["state"] in PENDING for event in history.values()):
                raise ValueError("Unresolved write. Run absmm check before applying.")
            if not any(row["id"] == snapshot["library_id"] for row in book_libraries(self.adapter)):
                raise ValueError("The approval library is unavailable.")
            plan = {
                "server": self.adapter.server, "library_id": snapshot["library_id"],
                "snapshot_id": snapshot["snapshot_id"], "manifest_digest": manifest, "items": selected,
            }
            if not confirm(plan, f"APPLY {digest(plan)}"):
                raise ValueError("Confirmation did not match. No writes submitted.")
            require_backup = not any(
                event["state"] in {"intent", "uncertain", "verified", "not_applied", "abandoned"}
                and event["data"]["server"] == self.adapter.server
                for event in history.values()
            )
            return self._apply_items(snapshot, selected, limited, backup, require_backup)

    def _apply_items(self, snapshot, selected, limited, backup, require_backup):
        run_id = uuid4().hex
        results = []
        remaining = iter(selected.items())
        for item_id, edits in remaining:
            op_id = uuid4().hex
            changes = {field: edit["after"] for field, edit in edits.items()}
            data = {
                "server": self.adapter.server, "library_id": snapshot["library_id"], "item_id": item_id,
                "raw": snapshot["books"][item_id], "changes": changes,
            }
            try:
                live = Book(self.adapter.item(item_id))
                if matches(live, data, after=True):
                    state, message = "already_applied", verified_message(live, changes)
                elif matches(live, data):
                    state, message = "intent", ""
                else:
                    state, message = "conflict", "Live identity or protected metadata changed. Export again."
            except AdapterError as exc:
                state, message = "conflict", str(exc)
            except (ValueError, TypeError, KeyError, AttributeError):
                state, message = "conflict", "Could not confirm the live item and its identity."
            if state != "intent":
                record(self.journal, op_id, run_id, state, message, data)
                results.append((item_id, state, message))
                continue
            if require_backup:
                if not backup():
                    results.append((item_id, "not_attempted", "Backup acknowledgment missing."))
                    break
                require_backup = False
            data["raw"] = live.raw
            record(self.journal, op_id, run_id, "intent", data=data)
            try:
                self.adapter.update(item_id, changes)
                observed = Book(self.adapter.item(item_id))
                if not matches(observed, data, after=True):
                    raise ValueError("Readback mismatch.")
                state, message = "verified", verified_message(observed, changes)
            except AdapterError as exc:
                state, message = "uncertain", f"{exc} Run absmm check before resubmitting."
            except (ValueError, TypeError, KeyError, OSError, AttributeError, KeyboardInterrupt):
                state, message = "uncertain", "Submission or readback could not be verified. Run absmm check."
            record(self.journal, op_id, run_id, state, message)
            results.append((item_id, state, message))
            if state == "uncertain":
                break
        results.extend((item_id, "not_attempted", "Run stopped.") for item_id, _ in remaining)
        results.extend((item_id, "not_attempted", "Outside --limit.") for item_id in limited)
        return results

    def check(self):
        with operation_lock(self.lock):
            results = []
            for event in records(self.journal).values():
                if event["state"] not in PENDING:
                    continue
                op_id, data = event["op_id"], event["data"]
                state, message = "uncertain", "State remains unresolved."
                if data["server"] != self.adapter.server:
                    results.append((op_id, state, "Reconnect to the original ABS server."))
                    continue
                try:
                    live = Book(self.adapter.item(data["item_id"]))
                    if matches(live, data, after=True):
                        state, message = "verified", verified_message(live, data["changes"])
                    elif matches(live, data):
                        state, message = "not_applied", "Original state observed."
                except AdapterError as exc:
                    message = str(exc)
                except (ValueError, TypeError, KeyError, AttributeError):
                    message = "Could not confirm the live item and its identity."
                if state != "uncertain":
                    record(self.journal, op_id, event["run_id"], state, message)
                results.append((op_id, state, message))
            return results

    def abandon(self, op_id, reason, confirm):
        with operation_lock(self.lock):
            event = records(self.journal).get(op_id)
            if event is None or event["state"] not in PENDING:
                raise ValueError("Choose an unresolved operation.")
            if event["data"]["server"] != self.adapter.server:
                raise ValueError("Reconnect to the original ABS server.")
            if not reason.strip() or not confirm(event["data"], f"ABANDON {op_id}"):
                raise ValueError("Explicit abandonment confirmation and a reason are required.")
            record(self.journal, op_id, event["run_id"], "abandoned", reason)
