from __future__ import annotations

from collections.abc import Callable

from .adapter import Adapter, inventory
from .locking import operation_lock
from .models import Book, digest, payload
from .store import Store


class SafetyError(RuntimeError):
    pass


def run_audit(store: Store, adapter: Adapter, library: dict, progress: Callable = lambda *args: None) -> int:
    with operation_lock(store.path):
        if library.get("mediaType") != "book":
            raise SafetyError("Select a book library.")
        live_libraries = adapter.libraries()
        if not any(lib["id"] == library["id"] for lib in live_libraries):
            raise SafetyError("Selected library no longer exists.")
        audit_id = store.start_audit(adapter.server, library["id"], library["name"])
        try:
            for book in inventory(adapter, library["id"], progress):
                store.add_book(audit_id, book)
            store.finish_audit(audit_id)
        except BaseException:
            store.finish_audit(audit_id, "interrupted", "Audit interrupted or failed. Start a fresh audit; writes disabled.")
            raise
        return audit_id


def preview(store: Store, audit_id: int) -> dict:
    audit = store.audit(audit_id)
    latest = store.latest(audit["server"], audit["library"])
    if audit["status"] != "complete" or latest["id"] != audit_id or audit["read_only"]:
        raise SafetyError("Only the latest complete live audiobook audit can produce an apply preview.")
    groups = {}
    for finding in store.findings(audit_id):
        if finding["status"] != "approved" or not finding["actionable"]:
            continue
        book = store.book(audit_id, finding["item_id"])
        if not book.is_audio:
            raise SafetyError("Ebooks are read-only. Calibre remains authoritative.")
        group = groups.setdefault(book.id, {"identity": book.identity, "before": book.values,
                                            "changes": {}, "finding_ids": []})
        field = finding["field"]
        if field in group["changes"]:
            raise SafetyError("Multiple approved proposals for the same field. Reset one decision.")
        if finding["current"] != book.values[field]:
            raise SafetyError("Proposal source does not match its snapshot.")
        group["changes"][field] = finding["target"]
        group["finding_ids"].append(finding["id"])
    for group in groups.values():
        group["payload"] = payload(group["changes"])
    result = {"server": audit["server"], "library": audit["library"], "audit_id": audit_id,
              "books": list(groups.values())}
    result["digest"] = digest(result)
    return result


def confirmation(plan: dict, action="APPLY") -> str:
    return f"{action} {plan['digest'][:12]}"


def guard(book: Book, identity: dict, values: dict):
    if book.identity != identity:
        raise SafetyError("Live identity, library, or path changed. Run a fresh audit; no ID migration is allowed.")
    if book.values != values:
        raise SafetyError("Live metadata differs from the expected source. Run a fresh audit.")
    if book.raw.get("isMissing") or book.raw.get("isInvalid") or not book.is_audio:
        raise SafetyError("Live item is missing, invalid, or not an audiobook.")


def verify(book: Book, data: dict, values: dict):
    guard(book, data["identity"], values)
    if book.protected() != data["protected"]:
        raise SafetyError("Unrelated live metadata/media changed. Reconcile and inspect the book.")


class Workflow:
    def __init__(self, store: Store, adapter: Adapter):
        self.store, self.adapter = store, adapter

    def _ready(self, server: str, library: str):
        if self.adapter.server != server:
            raise SafetyError("Connected server differs from this audit/history entry.")
        if self.store.unresolved(server):
            raise SafetyError("Reconcile prepared, applied, or uncertain History entries before any further writes.")
        if not any(lib["id"] == library and lib.get("mediaType") == "book" for lib in self.adapter.libraries()):
            raise SafetyError("The selected book library no longer exists.")
        self.adapter.check_write_capability()

    def apply(self, plan: dict, phrase: str, progress: Callable = lambda *args: None) -> list[int]:
        if phrase != confirmation(plan):
            raise SafetyError("Enter the exact confirmation phrase shown in the preview.")
        with operation_lock(self.store.path):
            fresh = preview(self.store, plan["audit_id"])
            if fresh != plan:
                raise SafetyError("Approvals or preview changed. Review and confirm the new preview.")
            self._ready(plan["server"], plan["library"])
            operations = []
            for index, group in enumerate(plan["books"], 1):
                identity = group["identity"]
                try:
                    live = Book(self.adapter.item(identity["id"]))
                    guard(live, identity, group["before"])
                except Exception as exc:
                    self.store.record(plan["server"], plan["library"], identity["id"], "apply", "skipped",
                                      group, "Live validation failed. No update submitted; run a fresh audit.")
                    raise SafetyError("Live validation failed. Batch stopped without updating this book.") from exc
                after = {**live.values, **group["changes"]}
                data = {**group, "after": after, "protected": live.protected(), "pre_update_raw": live.raw,
                        "audit_id": plan["audit_id"], "preview_digest": plan["digest"]}
                operation_id = self._execute(plan["server"], plan["library"], "apply", data)
                operations.append(operation_id)
                progress(index, len(plan["books"]), live.values["title"])
            return operations

    def _execute(self, server: str, library: str, action: str, data: dict) -> int:
        item_id = data["identity"]["id"]
        # Durable FULL-synchronous commit happens before a subprocess can send PATCH.
        operation_id = self.store.record(server, library, item_id, action, "prepared", data)
        try:
            self.adapter.update(item_id, data["changes"])
            self.store.transition(operation_id, "applied", "Update returned; verification pending.")
            observed = Book(self.adapter.item(item_id))
            verify(observed, data, data["after"])
            self.store.transition(operation_id, "verified", "Requested fields and unrelated metadata verified.",
                                  observed.raw, consume=data.get("finding_ids", ()), parent=data.get("parent"))
        except BaseException as exc:
            self.store.transition(operation_id, "uncertain", "Write or verification interrupted. Do not retry; reconcile.")
            if not isinstance(exc, Exception):
                raise
            raise SafetyError(f"Operation {operation_id} is uncertain. Batch stopped; reconcile in History.") from exc
        return operation_id

    def reconcile(self, operation_id: int) -> str:
        """Read-only remotely. Never automatically resubmit a write."""
        with operation_lock(self.store.path):
            row = self.store.operation(operation_id)
            if row["server"] != self.adapter.server:
                raise SafetyError("Connect to the operation's original server.")
            if row["state"] not in ("prepared", "applied", "uncertain"):
                raise SafetyError("Only unresolved entries require reconciliation.")
            data = row["data"]
            observed = Book(self.adapter.item(row["item_id"]))
            try:
                verify(observed, data, data["after"])
            except SafetyError:
                try:
                    verify(observed, data, data["before"])
                except SafetyError:
                    self.store.transition(operation_id, "uncertain", "Mixed or unrelated state. Inspect manually; writes blocked.",
                                          observed.raw)
                    return "uncertain"
                self.store.transition(operation_id, "not_applied", "Live values still match the before state; no retry submitted.",
                                      observed.raw)
                # A retry requires another explicit approval AND a fresh preview/confirmation.
                with self.store.connect() as db:
                    for fid in data.get("finding_ids", ()):
                        db.execute("UPDATE findings SET status='pending' WHERE id=? AND status='approved'", (fid,))
                return "not_applied"
            self.store.transition(operation_id, "verified", "Read-only reconciliation confirms the intended result.",
                                  observed.raw, consume=data.get("finding_ids", ()), parent=data.get("parent"))
            return "verified"

    def rollback_preview(self, operation_id: int) -> dict:
        row = self.store.operation(operation_id)
        if row["state"] != "verified" or row["action"] != "apply":
            raise SafetyError("Select a verified apply operation that has not been reverted.")
        data = row["data"]
        changes = {field: data["before"][field] for field in data["changes"]}
        result = {"server": row["server"], "library": row["library"], "parent": operation_id,
                  "identity": data["identity"], "before": data["after"], "after": data["before"],
                  "changes": changes, "payload": payload(changes, restoring=True), "protected": data["protected"]}
        result["digest"] = digest(result)
        return result

    def rollback(self, plan: dict, phrase: str) -> int:
        if phrase != confirmation(plan, "ROLLBACK"):
            raise SafetyError("Enter the exact rollback confirmation phrase.")
        with operation_lock(self.store.path):
            if plan != self.rollback_preview(plan["parent"]):
                raise SafetyError("Rollback preview changed. Refresh it before confirming.")
            self._ready(plan["server"], plan["library"])
            observed = Book(self.adapter.item(plan["identity"]["id"]))
            try:
                verify(observed, plan, plan["before"])
            except SafetyError as exc:
                self.store.record(plan["server"], plan["library"], observed.id, "rollback", "skipped", plan,
                                  "Rollback refused: live applied values or unrelated metadata changed.")
                raise SafetyError("Rollback refused: live state changed since the verified update.") from exc
            return self._execute(plan["server"], plan["library"], "rollback", {**plan, "pre_update_raw": observed.raw})


def import_previous(store: Store, audit: dict, label="Previous export") -> int:
    """Offline v2.1 export import is always read-only; never grants a production identity."""
    if audit.get("status") != "complete" or not isinstance(audit.get("books"), list):
        raise ValueError("Expected a complete v2.1 audit with a books array.")
    with operation_lock(store.path):
        aid = store.start_audit("offline://previous-export", "offline", label, read_only=True)
        try:
            for book in audit["books"]:
                authors = [{"name": a if isinstance(a, str) else a["name"]} for a in book.get("authors", [])]
                raw = {"id": book["item_id"], "libraryId": "offline", "mediaType": "book", "relPath": book["rel_path"],
                       "_imported_embedded": book.get("embedded", {}),
                       "media": {"metadata": {"title": book.get("title"), "subtitle": book.get("subtitle"),
                                               "authors": authors, "narrators": book.get("narrators", []),
                                               "series": book.get("series", [])},
                                 "coverPath": "present-in-export" if book.get("cover_present") else None,
                                 "numAudioFiles": 1, "numChapters": book.get("chapter_count", 0),
                                 "chapters": [{"title": name} for name in book.get("chapter_sample", [])]}}
                store.add_book(aid, Book(raw))
            store.finish_audit(aid)
        except BaseException:
            store.finish_audit(aid, "interrupted", "Import failed. Read-only partial snapshot.")
            raise
        return aid
