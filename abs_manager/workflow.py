from __future__ import annotations

import json
from collections.abc import Callable

from .adapter import Adapter, AdapterError, CliAdapter, InventoryChanged, inventory, membership
from .diagnostics import event
from .locking import operation_lock
from .models import Book, digest, payload
from .store import Store


class SafetyError(RuntimeError):
    pass


def library_identity(library: dict) -> dict:
    return {"id": library["id"], "name": library["name"], "mediaType": library.get("mediaType"),
            "folders": sorted(library.get("folders", []), key=lambda folder: json.dumps(folder, sort_keys=True))}


def resume_unavailable(store: Store, audit: dict, server: str, library: dict) -> str | None:
    """Local eligibility only. Remote membership is rechecked inside the explicit Resume action."""
    if audit["server"] != server or audit["library"] != library["id"]:
        return "Connect to the original server and select the original library."
    if audit["read_only"] or audit["status"] not in ("running", "interrupted"):
        return "Only an interrupted live audit can be resumed."
    if store.latest(server, library["id"])["id"] != audit["id"]:
        return "A newer audit exists. Resume its saved progress or start a fresh audit."
    if audit["resume_blocked"]:
        return audit["resume_blocked"]
    if audit["library_identity"] is None or (audit["membership"] is None and store.saved_progress(audit["id"])):
        return ("This older audit has no membership checkpoint, so unchanged membership cannot be verified. "
                "Start a fresh audit once; its interruptions will be resumable. Saved history is retained.")
    if json.loads(audit["library_identity"]) != library_identity(library):
        return "Library identity or folders changed. Start a fresh audit."
    return None


def run_audit(store: Store, adapter: Adapter, library: dict, progress: Callable = lambda *args: None,
              *, resume_id: int | None = None, diagnostic: Callable = lambda record: None) -> int:
    with operation_lock(store.path):
        if library.get("mediaType") != "book":
            raise SafetyError("Select a book library.")
        if resume_id is not None:
            audit = store.audit(resume_id)
            if reason := resume_unavailable(store, audit, adapter.server, library):
                raise SafetyError(reason)
            audit_id = resume_id
        else:
            audit_id = store.start_audit(adapter.server, library["id"], library["name"],
                                         library_identity=library_identity(library))
        previous_diagnostic, previous_log = None, None
        if isinstance(adapter, CliAdapter):
            previous_diagnostic, previous_log = adapter.diagnostic, adapter.log

            def report(record):
                store.request_diagnostic(audit_id, record)
                diagnostic(record)

            adapter.diagnostic, adapter.log = report, store.log

        def saved_progress(done, total, text):
            saved_count = store.saved_progress(audit_id)
            event(store.log, audit_id=audit_id, saved=saved_count, expected=total)
            progress(saved_count, total, text)

        try:
            live = next((lib for lib in adapter.libraries() if lib["id"] == library["id"]), None)
            if live is None or library_identity(live) != library_identity(library):
                reason = "Library identity or folders changed. Start a fresh audit."
                store.block_resume(audit_id, reason)
                raise SafetyError(reason)
            progress(store.saved_progress(audit_id), 0, "Re-enumerating current library membership…")
            members = membership(adapter, library["id"])
            saved = {}
            if resume_id is not None:
                if audit["membership"] is not None and members != json.loads(audit["membership"]):
                    reason = "Library membership, item identity, or paths changed. Start a fresh audit."
                    store.block_resume(audit_id, reason)
                    raise SafetyError(reason)
                saved = {book.id: book for book in store.books(audit_id)}
                if any(item_id not in members or book.identity != members[item_id]
                       for item_id, book in saved.items()):
                    reason = "Saved item identity/path differs from current membership. Start a fresh audit."
                    store.block_resume(audit_id, reason)
                    raise SafetyError(reason)
                store.resume_audit(audit_id)
                if audit["membership"] is None:
                    store.checkpoint(audit_id, members, library_identity(live))
            else:
                store.checkpoint(audit_id, members, library_identity(live))
            progress(len(saved), len(members), "Membership verified; continuing saved progress…")
            for book in inventory(adapter, library["id"], saved_progress, members=members, saved=saved):
                store.add_book(audit_id, book)
            # Detect membership/path changes while expanded requests were in flight as well.
            if membership(adapter, library["id"]) != members:
                reason = "Library membership, item identity, or paths changed during audit. Start a fresh audit."
                store.block_resume(audit_id, reason)
                raise SafetyError(reason)
            final_library = next((lib for lib in adapter.libraries() if lib["id"] == library["id"]), None)
            if final_library is None or library_identity(final_library) != library_identity(live):
                reason = "Library identity or folders changed during audit. Start a fresh audit."
                store.block_resume(audit_id, reason)
                raise SafetyError(reason)
            store.finish_audit(audit_id)
        except BaseException as exc:
            # Adapter/Safety messages contain controlled diagnostics, never arbitrary response bodies.
            error = str(exc) if isinstance(exc, (AdapterError, SafetyError)) else (
                "Audit interrupted or failed validation. Saved progress retained; incomplete audits cannot be approved or applied.")
            if isinstance(exc, InventoryChanged):
                store.block_resume(audit_id, error)
            store.finish_audit(audit_id, "interrupted", error)
            raise
        finally:
            if isinstance(adapter, CliAdapter):
                adapter.diagnostic, adapter.log = previous_diagnostic, previous_log
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
                    self.store.record(plan["server"], plan["library"], identity["id"], "apply",
                                      "skipped" if isinstance(exc, SafetyError) else "failed",
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
