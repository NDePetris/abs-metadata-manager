from __future__ import annotations

import json
from collections.abc import Callable

from .adapter import Adapter, AdapterError, CliAdapter, InventoryChanged, inventory, membership
from .diagnostics import event
from .locking import operation_lock
from .models import Book, digest, payload, result_matches, target_observed
from .store import Store


class SafetyError(RuntimeError):
    pass


MAX_APPLY_BATCH = 10


def operation_comparison(data, observed):
    """Compact intent and actual differences; no inference about journal state."""
    rows = []
    try:
        book = Book(observed) if observed else None
    except (ValueError, KeyError, TypeError):
        book = None

    def show(field, value, actual=False):
        if field == "subtitle" and data.get("changes", {}).get(field) == "" and value in (None, ""):
            return "Cleared (ABS returned null)" if actual and value is None else "Cleared"
        return json.dumps(value, ensure_ascii=False)

    before, after = data.get("before", {}), data.get("after", {})
    for field in sorted(set(before) | set(after)):
        actual = book.values.get(field) if book else None
        equal = book is not None and (target_observed(field, after.get(field), actual)
                                     if field in data.get("changes", {}) else after.get(field) == actual)
        if field in data.get("changes", {}) or not equal:
            rows.append({"Field": field.capitalize(), "Before": show(field, before.get(field)),
                         "Intended": show(field, after.get(field)),
                         "Observed": show(field, actual, True) if book else "Not observed",
                         "Comparison": ("Semantically equivalent" if actual != after.get(field) else "Matches")
                         if equal else "Material difference" if book else "Not observed"})

    def differences(prefix, expected, actual):
        if isinstance(expected, dict) and isinstance(actual, dict):
            for key in sorted(expected.keys() | actual.keys()):
                differences(prefix + "." + key, expected.get(key, "[not present]"),
                            actual.get(key, "[not present]"))
        elif isinstance(expected, list) and isinstance(actual, list):
            for index in range(max(len(expected), len(actual))):
                differences(f"{prefix}[{index}]", expected[index] if index < len(expected) else "[not present]",
                            actual[index] if index < len(actual) else "[not present]")
        elif expected != actual:
            def short(value):
                text = json.dumps(value, ensure_ascii=False)
                return text if len(text) <= 240 else text[:240] + "… (see Advanced)"
            rows.append({"Field": prefix, "Before": short(expected), "Intended": short(expected),
                         "Observed": short(actual), "Comparison": "Material difference"})

    if book:
        differences("Identity", data.get("identity"), book.identity)
        differences("Protected", data.get("protected"), book.protected())
        if book.raw.get("isMissing") or book.raw.get("isInvalid") or not book.is_audio:
            rows.append({"Field": "Media validity", "Comparison": "Material difference"})
    return rows


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


def preview(store: Store, audit_id: int, selected=None) -> dict:
    audit = store.audit(audit_id)
    latest = store.latest(audit["server"], audit["library"])
    if audit["status"] != "complete" or latest["id"] != audit_id or audit["read_only"]:
        raise SafetyError("Only the latest complete live audiobook audit can produce an apply preview.")
    groups = {}
    if selected is not None and (not selected or len(selected) != len(set(selected))):
        raise SafetyError("Duplicate or empty selected fields are not allowed.")
    selected = sorted(selected) if selected is not None else None
    for finding in store.findings(audit_id):
        if selected is not None and finding["id"] not in selected:
            continue
        if finding["status"] != "approved" or not finding["actionable"]:
            continue
        book = store.book(audit_id, finding["item_id"])
        if not book.is_audio:
            raise SafetyError("Ebooks are read-only. Calibre remains authoritative.")
        group = groups.setdefault(book.id, {"identity": book.identity, "before": book.values,
                                            "protected": book.protected(), "changes": {}, "finding_ids": [],
                                            "approvals": {}})
        field = finding["field"]
        if field in group["changes"]:
            raise SafetyError("Multiple approved proposals for the same field. Reset one decision.")
        if finding["current"] != book.values[field]:
            raise SafetyError("Proposal source does not match its snapshot.")
        group["changes"][field] = finding["target"]
        group["finding_ids"].append(finding["id"])
        group["approvals"][str(finding["id"])] = finding["reviewed"]
    for group in groups.values():
        group["payload"] = payload(group["changes"])
    if groups:
        evidence = {r["item_id"]: r for r in store.verification_report(audit_id)}
        for iid, group in groups.items():
            item = evidence[iid]
            effective = [l.get("previous_success", l) for l in item["lookups"]]
            group["evidence_digest"] = digest({
                "evidence": [l for l in effective if l["state"] != "unavailable"],
                "candidates": item["candidate_decisions"], "fields": item["human_decisions"]})
    if len(groups) > MAX_APPLY_BATCH:
        raise SafetyError(f"Select at most {MAX_APPLY_BATCH} books per apply invocation.")
    result = {"server": audit["server"], "library": audit["library"], "audit_id": audit_id,
              "books": list(groups.values())}
    if selected is not None:
        if set(selected) != {fid for group in groups.values() for fid in group["finding_ids"]}:
            raise SafetyError("Selected approvals changed.")
        result["selected"] = selected
    result["digest"] = digest(result)
    return result


def confirmation(plan: dict, action="APPLY") -> str:
    return f"{action} {plan['digest'][:12]}"


def observe_selected(store, adapter, audit_id, selected):
    """Bounded read-only refresh; untouched books retain their old capture provenance.

    A new snapshot expires write approvals and reuses import reconciliation. All
    requested reads and identities must validate before replacing the current view.
    """
    from .store import now
    with operation_lock(store.path):
        audit = store.audit(audit_id)
        if (not selected or len(selected) > 25 or len(selected) != len(set(selected))
                or audit["status"] != "complete" or audit["read_only"]
                or store.latest(audit["server"], audit["library"])["id"] != audit_id):
            raise SafetyError("Select 1–25 distinct books from the latest complete live snapshot.")
        if adapter.server != audit["server"] or store.unresolved(audit["server"]):
            raise SafetyError("Connect to the original server and reconcile unresolved History first.")
        library = next((lib for lib in adapter.libraries() if lib["id"] == audit["library"]), None)
        if library is None or (audit["library_identity"] and library_identity(library) != json.loads(audit["library_identity"])):
            raise SafetyError("Library identity changed. Run a full import.")
        books = {b.id: b for b in store.books(audit_id)}
        observed = {}
        for iid in selected:
            if iid not in books:
                raise SafetyError("Selected book is not in this snapshot.")
            live = Book(adapter.item(iid))
            if live.identity != books[iid].identity or live.raw.get("isMissing") or live.raw.get("isInvalid"):
                raise SafetyError("Book moved, changed identity or became unavailable. Run a full import.")
            observed[iid] = live
        fresh_id = store.start_audit(audit["server"], audit["library"], audit["name"],
                                     library_identity=library_identity(library))
        try:
            for iid, original in books.items():
                store.add_book(fresh_id, observed.get(iid, original))
            # Catalog is historic authority evidence, not a new Calibre observation.
            with store.connect() as db:
                from .persistent_state import identity
                catalog_rows = store.rows("SELECT data FROM calibre_catalogs WHERE audit_id=?", (audit_id,))
                if catalog_rows:
                    catalog = json.loads(catalog_rows[0]["data"])
                    compatible = {iid: value for iid, value in catalog.items() if iid in books
                                  and identity(books[iid]) == identity(observed.get(iid, books[iid]))}
                    db.execute("INSERT INTO calibre_catalogs VALUES(?,?)", (fresh_id, json.dumps(compatible)))
                for iid in books:
                    db.execute("INSERT INTO verification_annotations VALUES(?,?,?,?)",
                               (fresh_id, iid, "observation", json.dumps({"captured": now() if iid in observed else None,
                                "source_audit": audit_id, "fresh_abs": iid in observed})))
            store.finish_audit(fresh_id)
        except BaseException:
            store.finish_audit(fresh_id, "interrupted", "Read-only refresh interrupted; no remote update submitted.")
            raise
        return fresh_id


def guard(book: Book, identity: dict, values: dict):
    if book.identity != identity:
        raise SafetyError("Live identity, library, or path changed. Run a fresh audit; no ID migration is allowed.")
    if book.values != values:
        raise SafetyError("Live metadata differs from the expected source. Run a fresh audit.")
    if book.raw.get("isMissing") or book.raw.get("isInvalid") or not book.is_audio:
        raise SafetyError("Live item is missing, invalid, or not an audiobook.")


def batch_results(store, plan):
    """Reconstruct progress from the existing per-item journals after restart."""
    history = [r for r in store.history(plan["server"]) if r["action"] == "apply"
               and r["data"].get("preview_digest") == plan["digest"]]
    rows = []
    for group in plan["books"]:
        iid = group["identity"]["id"]
        row = next((r for r in history if r["item_id"] == iid), None)
        status = ({"verified": "Verified applied", "not_applied": "Failed without application",
                   "failed": "Failed without application", "skipped": "Blocked",
                   "prepared": "Uncertain", "applied": "Uncertain", "uncertain": "Uncertain",
                   "reverted": "Reverted"}.get(row["state"], "Blocked") if row else "Not attempted")
        rows.append({"item_id": iid, "title": group["before"]["title"], "status": status,
                     "operation_id": row["id"] if row else None})
    return rows


def verify(book: Book, data: dict, values: dict, *, intended=False):
    expected = values
    if intended and result_matches(values, book.values, data["changes"]):
        expected = book.values
    guard(book, data["identity"], expected)
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
            fresh = preview(self.store, plan["audit_id"], plan.get("selected"))
            if fresh != plan:
                raise SafetyError("Approvals or preview changed. Review and confirm the new preview.")
            try:
                self._ready(plan["server"], plan["library"])
            except BaseException:
                self._expire_batch(plan)
                raise
            operations = []
            for index, group in enumerate(plan["books"], 1):
                identity = group["identity"]
                try:
                    self._ready(plan["server"], plan["library"])
                    current = preview(self.store, plan["audit_id"], group["finding_ids"])
                    if current["books"] != [group]:
                        raise SafetyError("Selected targets or approvals changed during execution.")
                    live = Book(self.adapter.item(identity["id"]))
                    guard(live, identity, group["before"])
                    if live.protected() != group["protected"]:
                        raise SafetyError("Protected metadata changed since preview.")
                except Exception as exc:
                    self.store.record(plan["server"], plan["library"], identity["id"], "apply",
                                      "skipped" if isinstance(exc, SafetyError) else "failed",
                                      {**group, "batch_plan": plan, "preview_digest": plan["digest"]},
                                      "Live validation failed. No update submitted; run a fresh audit.")
                    self._expire_batch(plan)
                    raise SafetyError("Live validation failed. Batch stopped without updating this book.") from exc
                after = {**live.values, **group["changes"]}
                data = {**group, "after": after, "protected": live.protected(), "pre_update_raw": live.raw,
                        "audit_id": plan["audit_id"], "preview_digest": plan["digest"], "batch_plan": plan}
                try:
                    operation_id = self._execute(plan["server"], plan["library"], "apply", data)
                except BaseException:
                    self._expire_batch(plan)
                    raise
                operations.append(operation_id)
                try:
                    progress(index, len(plan["books"]), live.values["title"])
                except BaseException:
                    self._expire_batch(plan)
                    raise
            return operations

    def _expire_batch(self, plan):
        with self.store.connect() as db:
            for group in plan["books"]:
                for fid in group["finding_ids"]:
                    db.execute("UPDATE findings SET status='pending' WHERE id=? AND status='approved'", (fid,))

    def _execute(self, server: str, library: str, action: str, data: dict) -> int:
        item_id = data["identity"]["id"]
        # Durable FULL-synchronous commit happens before a subprocess can send PATCH.
        operation_id = self.store.record(server, library, item_id, action, "prepared", data)
        observed = None
        try:
            self.adapter.update(item_id, data["changes"])
            self.store.transition(operation_id, "applied", "Update returned; verification pending.")
            observed = Book(self.adapter.item(item_id))
            verify(observed, data, data["after"], intended=True)
            self.store.transition(operation_id, "verified", "Requested fields and unrelated metadata verified.",
                                  observed.raw, consume=data.get("finding_ids", ()), parent=data.get("parent"))
        except BaseException as exc:
            self.store.transition(operation_id, "uncertain", "Write or verification interrupted. Do not retry; reconcile.",
                                  observed.raw if observed else None)
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
                verify(observed, data, data["after"], intended=True)
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
                  "identity": data["identity"],
                  "before": Book(row["observed"]).values if row["observed"] else data["after"],
                  "after": data["before"],
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
