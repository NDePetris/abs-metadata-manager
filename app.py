from __future__ import annotations

import json
import os

import streamlit as st

from abs_manager import preferences
from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.changes_ui import changes_view
from abs_manager.config import ROOT, Settings, defaults
from abs_manager.locking import BusyError, operation_lock
from abs_manager.models import digest
from abs_manager.sample import sample_audit
from abs_manager.store import Store
from abs_manager.verification_ui import verification_view
from abs_manager.workflow import (
    SafetyError,
    Workflow,
    confirmation,
    import_previous,
    resume_unavailable,
    run_audit,
)

st.set_page_config(page_title="Audiobookshelf Metadata Manager", layout="wide", initial_sidebar_state="collapsed")


def display(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def flash(message):
    st.session_state["flash"] = message
    st.rerun()


def progress_bar():
    bar = st.progress(0, text="Starting sequential operation…")

    def update(done, total, title):
        bar.progress(min(done / max(total, 1), 1.0), text=f"{done} / {total or '?'} · {title}")

    return update


def audit_action(store, adapter, library, resume_id=None):
    status = st.empty()

    def diagnostic(record):
        target = record.get("item_id", f"page {record['page']}" if "page" in record else "libraries")
        status.info(f"{record['operation']} · {target} · attempt {record['attempt']} / 3 · "
                    f"{record['state']} · {record['category']} · {record['duration']:.3f}s · "
                    f"exit code {record['exit_code']}")

    try:
        aid = run_audit(store, adapter, library, progress_bar(), resume_id=resume_id, diagnostic=diagnostic)
    except (AdapterError, SafetyError, BusyError, ValueError, KeyError) as exc:
        latest = store.latest(adapter.server, library["id"])
        if latest:
            st.session_state["snapshot_choice"] = latest["id"]
        st.error(str(exc))
        # Render persisted progress and its diagnostic below, including after another rerun.
        return
    st.session_state["snapshot_choice"] = aid
    flash("Import complete. Valid field acceptance is retained; correction write approvals start fresh.")


def settings_view(store):
    st.title("Connection & settings")
    st.write("Connect to your Audiobookshelf server with an API key. Saved keys use Windows user-scoped encryption. Same-user code may retrieve them.")
    d = {**defaults(), **preferences.load()}
    # Widget keys belong to Streamlit; connection_result/error are application state.
    with st.form("settings_connection_form"):
        server = st.text_input("Server URL", value=d["server"], placeholder="http://your-server:13378")
        token = st.text_input("API key", value="", type="password", help="Enter a key, or leave blank to use this server’s saved Windows-encrypted key.")
        remember = st.checkbox("Remember API key with Windows encryption", value=True)
        with st.expander("Advanced CLI settings"):
            executable = st.text_input("abs-cli executable", value=d["executable"])
            c1, c2 = st.columns(2)
            interval = c1.number_input("Seconds between CLI calls", min_value=0.1, value=d["interval"], step=0.1)
            timeout = c2.number_input("CLI timeout (seconds)", min_value=5.0, value=d["timeout"], step=5.0)
        connect = st.form_submit_button("Connect & check compatibility", type="primary")
    if connect:
        # Invalidate the prior connection before attempting another server.
        for key in ("adapter", "connection_result", "connection_error", "library", "library_choice"):
            st.session_state.pop(key, None)
        try:
            with operation_lock(store.path), st.spinner("Checking CLI commands and reading libraries…"):
                adapter = CliAdapter(Settings(server, token or preferences.credential(server), executable, interval, timeout))
                result = adapter.probe()
                st.session_state["providers"] = adapter.metadata_providers()
            if remember and token:
                preferences.credential(server, token)
            preferences.save({**preferences.load(), "server": adapter.server, "executable": executable})
            st.session_state["server"] = adapter.server
            st.session_state["adapter"] = adapter
            st.session_state["connection_result"] = result
            st.session_state.pop("credential_error", None)
        except (AdapterError, BusyError, ValueError, KeyError) as exc:
            st.session_state["connection_error"] = str(exc)
        # Refresh the sidebar too, including when replacing a successful connection fails.
        st.rerun()
    if error := st.session_state.get("connection_error"):
        st.error(error)
    connection = st.session_state.get("connection_result")
    adapter = st.session_state.get("adapter")
    if connection and adapter:
        st.success("Connected. Use Library in the sidebar throughout the workspace.")
        st.write(f"**Server:** {adapter.server} · **ABS:** {connection['server_version']} · "
                 f"**abs-cli:** {connection['cli_version']}")
        for warning in connection["warnings"]:
            st.warning(warning)
        if st.button("Disconnect"):
            for key in ("adapter", "connection_result", "connection_error", "library"):
                st.session_state.pop(key, None)
            st.rerun()
    with st.form("calibre_settings"):
        prefs = preferences.load()
        calibre_executable = st.text_input("Calibre executable", value=prefs.get("calibre_executable", "calibredb"))
        calibre_library = st.text_input("Calibre library folder", value=prefs.get("calibre_library", ""))
        if st.form_submit_button("Save Calibre settings"):
            preferences.save({**prefs, "calibre_executable": calibre_executable, "calibre_library": calibre_library})
    if st.button("Forget saved API key", disabled=not d.get("server")):
        preferences.credential(d["server"], forget=True)
        st.session_state.pop("credential_error", None)
        st.success("Saved API key forgotten. Enter a new key to reconnect.")
    st.divider()
    st.subheader("Local data")
    st.code(store.path, language=None)
    st.caption("This database contains private paths, snapshots, review decisions, and rollback history. Back it up before moving PCs.")
    with st.expander("Open a previous v2.1 audit export (read-only)"):
        uploaded = st.file_uploader("Complete audit.json", type="json")
        if st.button("Import for offline review", disabled=uploaded is None):
            aid = import_previous(store, json.loads(uploaded.getvalue()), "Imported v2.1 export")
            st.session_state["snapshot_choice"] = aid
            flash("Imported a read-only snapshot. Run a fresh live audit before approving corrections.")


def remember_snapshot():
    st.session_state["snapshot_choice"] = st.session_state["snapshot_widget"]


def choose_snapshot(store):
    audits = store.audits()
    if not audits:
        return None
    # Prefer the latest snapshot for the connected library; retain historical/offline selection explicitly.
    adapter, library = st.session_state.get("adapter"), st.session_state.get("library")
    choices = [a["id"] for a in audits]
    if "snapshot_choice" not in st.session_state or st.session_state["snapshot_choice"] not in choices:
        latest = store.latest(adapter.server, library["id"]) if adapter and library else None
        st.session_state["snapshot_choice"] = latest["id"] if latest else choices[0]
    # Keep the selected snapshot when Streamlit cleans up widgets on Settings/History.
    st.session_state["snapshot_widget"] = st.session_state["snapshot_choice"]
    aid = st.selectbox("Audit snapshot", choices, key="snapshot_widget", on_change=remember_snapshot,
                       format_func=lambda i: next(f"#{i} · {a['name']} · {a['started'][:16]} UTC · "
                                                 f"{a['status']} · {a['books']} books"
                                                 for a in audits if a["id"] == i))
    audit = store.audit(aid)
    if audit["read_only"]:
        st.info("Offline snapshot · read-only. Write approval and Update Sources require a fresh live audiobook audit.")
    elif audit["status"] != "complete":
        st.warning("This audit is incomplete. Saved progress is retained. Resume after validation or start a fresh audit; approvals and writes are disabled.")
    st.caption(f"Source: {audit['server']} · Library ID: {audit['library']}")
    return audit


def library_view(store):
    st.title("Import")
    st.write("Import saved Audiobookshelf metadata into a local audit snapshot. External catalog searches are a separate step in Match & Verify.")
    adapter, library = st.session_state.get("adapter"), st.session_state.get("library")
    if adapter and library:
        st.write(f"**Connected:** {adapter.server} / {library['name']}")
        if st.button("Refresh Library", type="primary"):
            with st.spinner("Reading pages and expanded items, one call at a time…"):
                audit_action(store, adapter, library)
        st.caption("New imports expire write approvals and preserve valid field acceptance. Resume continues the same incomplete snapshot. "
                   "Use Streamlit’s Stop control to interrupt; incomplete audits cannot be approved or applied.")
    else:
        st.info("Connect in Settings, then use Library in the sidebar.")
    audit = active_inventory(store)
    attempt = store.latest(adapter.server, library["id"]) if adapter and library else None
    if attempt and attempt["status"] != "complete":
        audit = attempt
    if audit is None:
        st.subheader("Your first audit")
        st.write("Connect in Settings, choose Library in the sidebar, then Refresh Library.")
        if st.button("Load read-only sample library"):
            aid = import_previous(store, sample_audit(), "Synthetic sample library")
            st.session_state["snapshot_choice"] = aid
            preferences.save({"server": "offline://previous-export", "library": "offline"})
            flash("Loaded six synthetic examples. No server connection or metadata writes.")
        return
    books = store.books(audit["id"])
    if audit["status"] == "complete":
        st.caption(f"Last successful import: {audit['finished'][:16].replace('T', ' ')} UTC · {len(books)} books")
        summary = store.import_summary(audit["id"])
        st.subheader("Import comparison")
        st.write(f"{summary['new']} new · {summary['removed']} removed · {summary['changed']} changed · "
                 f"{summary['ambiguous']} identities need review · {summary['retained']} accepted fields retained · "
                 f"{summary['reopened']} fields awaiting reacceptance · {summary['unresolved']} books awaiting review")
        st.caption("Removed books keep their archived evidence. Changed IDs and paths never inherit acceptance silently.")
        with st.expander("New, changed, and removed inventory"):
            previous = store.rows("SELECT id FROM audits WHERE server=? AND library=? AND status='complete' "
                                  "AND id<? ORDER BY id DESC LIMIT 1",
                                  (audit["server"], audit["library"], audit["id"]))
            prior = {b.id: b for b in store.books(previous[0]["id"])} if previous else {}
            current = {b.id: b for b in books}
            changes = [{"Change": "New" if b.id not in prior else "Changed", "Book": b.values["title"],
                        "Item ID": b.id, "Path": b.raw.get("relPath") or b.raw.get("path")}
                       for b in books if b.id not in prior or digest(b.raw) != digest(prior[b.id].raw)]
            changes += [{"Change": "Removed · history retained", "Book": b.values["title"], "Item ID": b.id,
                         "Path": b.raw.get("relPath") or b.raw.get("path")} for b in prior.values()
                        if b.id not in current]
            if changes:
                st.dataframe(changes, hide_index=True, width="stretch")
            else:
                st.caption("No inventory changes since the prior completed import.")
    st.caption(f"Saved progress: {len(books)} / {audit['expected'] if audit['expected'] is not None else '?'} books")
    if audit["error"]:
        st.error(f"Latest audit error: {audit['error']}")
    if audit["status"] in ("running", "interrupted") and not audit["read_only"]:
        reason = resume_unavailable(store, audit, adapter.server, library) if adapter and library else (
            "Connect to the original server and choose the original Library in the sidebar.")
        if reason:
            st.info(f"Resume unavailable: {reason}")
        else:
            st.caption("Resume checks current membership and saved identities/paths before reusing books. A changed library requires a fresh audit.")
        if st.button("Resume Import", disabled=bool(reason)):
            audit_action(store, adapter, library, resume_id=audit["id"])
            st.rerun()
    slow = json.loads(audit["slow_requests"])
    with st.expander("Audit diagnostics · slowest item requests"):
        st.caption(f"Rotating local log: {store.log.handlers[0].baseFilename} (1 MB × 4 files). "
                   "Stderr is reduced to recognized HTTP/connection diagnostics; raw output is withheld.")
        if slow:
            st.dataframe(slow, hide_index=True, width="stretch")
        else:
            st.caption("No item timings recorded for this snapshot.")
    if books and sum(not b.is_audio for b in books) > len(books) / 2:
        st.warning("This selected library appears to contain mostly ebooks. Check Library in the sidebar. "
                   "Ebooks remain read-only; Calibre is authoritative.")
def show_plan(plan):
    st.write(f"**Server:** {plan['server']} · **Library ID:** {plan['library']}")
    for group in plan.get("books", [plan]):
        st.write(f"**{group['before'].get('title') or group['identity']['id']}**")
        st.caption(f"Item {group['identity']['id']} · {group['identity']['relPath'] or group['identity']['path']}")
        st.dataframe([{"Field": field, "Before": display(group["before"][field]), "After": display(value)}
                      for field, value in group["changes"].items()], hide_index=True, width="stretch")
        with st.expander("Exact CLI metadata payload"):
            st.json(group["payload"])


def history_view(store):
    st.title("History")
    st.write("Inspect durable intent and observed results. Reconciliation only reads the server; rollback requires confirmation.")
    with st.expander("Advanced / Troubleshooting", expanded=bool(store.unresolved(st.session_state.get("server", "")))):
        historical = choose_snapshot(store)
        if historical:
            st.dataframe(store.findings(historical["id"]), hide_index=True, width="stretch")
            archived = store.books(historical["id"])
            if archived:
                iid = st.selectbox("Historical book", [b.id for b in archived],
                                   format_func=lambda key: next(b.values["title"] or key for b in archived if b.id == key))
                st.json(store.book(historical["id"], iid).raw)
    audit = active_inventory(store)
    if audit:
        events = [{"When": a["started"], "Activity": "Import: " + a["status"], "Book": "Library", "Why": a["error"] or "Inventory refresh"}
                  for a in store.audits() if a["server"] == audit["server"] and a["library"] == audit["library"]]
        for table, event in (("field_decision_history", "Field decision"), ("candidate_decision_history", "Edition decision"), ("corrections", "Correction")):
            for row in store.rows(f"SELECT * FROM {table} WHERE server=? AND library=?", (audit["server"], audit["library"])):
                events.append({"When": row["created"], "Activity": event + ": " + row.get("decision", row.get("status", "")),
                               "Book": row["item_id"], "Why": row["evidence"]})
        for operation in store.history(audit["server"]):
            if operation["library"] == audit["library"]:
                events.append({"When": operation["updated"], "Activity": operation["action"] + ": " + operation["state"],
                               "Book": operation["item_id"], "Why": operation["message"]})
        for lookup in store.rows("SELECT l.captured,l.source,l.data,t.item_id FROM verification_lookups l JOIN verification_tasks t ON t.lookup_id=l.id JOIN verification_runs r ON r.id=t.run_id JOIN audits a ON a.id=r.audit_id WHERE a.server=? AND a.library=?",
                                 (audit["server"], audit["library"])):
            events.append({"When": lookup["captured"], "Activity": "Search: " + lookup["source"], "Book": lookup["item_id"],
                           "Why": json.loads(lookup["data"])["state"]})
        for catalog in store.rows("SELECT h.imported FROM calibre_catalog_history h JOIN audits a ON a.id=h.audit_id WHERE a.server=? AND a.library=?",
                                  (audit["server"], audit["library"])):
            events.append({"When": catalog["imported"], "Activity": "Calibre catalog compared", "Book": "Library", "Why": "Read-only validation"})
        st.dataframe(sorted(events, key=lambda e: e["When"], reverse=True), hide_index=True, width="stretch")
    with st.expander("Write journals and guarded recovery", expanded=any(r["state"] in ("prepared", "applied", "uncertain") for r in store.history())):
        rows = store.history()
        if not rows:
            st.info("No updates have been submitted. Verified, skipped, failed, and uncertain operations appear here.")
            return
        state_filter = st.multiselect("Operation states", sorted({r["state"] for r in rows}))
        visible = [r for r in rows if not state_filter or r["state"] in state_filter]
        st.dataframe([{k: r[k] for k in ("id", "created", "server", "library", "item_id", "action", "state", "message")}
                      for r in visible], hide_index=True, width="stretch")
        if not visible:
            return
        oid = st.selectbox("Inspect operation", [r["id"] for r in visible],
                           format_func=lambda oid: next(f"#{oid} · {r['action']} · {r['state']} · {r['item_id']}"
                                                       for r in visible if r["id"] == oid))
        row = store.operation(oid)
        data = row["data"]
        st.write(f"**{row['state']}** · {row['message']}")
        st.json({"identity": data.get("identity"), "before": data.get("before"),
                 "intended_after": data.get("after"), "payload": data.get("payload")})
        with st.expander("Persisted pre-update and observed result"):
            st.json({"pre_update": data.get("pre_update_raw"), "observed": row["observed"]})
        adapter = st.session_state.get("adapter")
        connected = adapter is not None and adapter.server == row["server"]
        if row["state"] in ("prepared", "applied", "uncertain"):
            st.warning("Do not retry this write. Compare live state with the persisted before/after values.")
            if st.button("Reconcile using live metadata (read-only)", disabled=not connected):
                result = Workflow(store, adapter).reconcile(oid)
                flash(f"Reconciliation result: {result}. No metadata write was sent.")
            st.caption("Mixed state remains blocked. Inspect the item in Audiobookshelf, restore a recorded state deliberately there, then reconcile again.")
        if row["state"] == "verified" and row["action"] == "apply":
            if not connected:
                st.info("Connect to the original server to preview and confirm a selective rollback.")
                return
            wf = Workflow(store, adapter)
            back = wf.rollback_preview(oid)
            with st.expander("Guarded rollback of this operation"):
                st.write("Rollback restores only these fields and requires the live values to still match the verified applied state.")
                show_plan(back)
                expected = confirmation(back, "ROLLBACK")
                st.code(expected, language=None)
                with st.form(f"rollback_{back['digest']}"):
                    phrase = st.text_input("Rollback confirmation")
                    checked = st.checkbox("Restore the exact previous values shown above.")
                    submit = st.form_submit_button("Restore confirmed fields", disabled=bool(store.unresolved(row["server"])))
                if submit:
                    if not checked:
                        st.error("Select the explicit rollback confirmation checkbox.")
                        return
                    wf.rollback(back, phrase)
                    flash("Rollback verified. Start a fresh audit before proposing further corrections.")


def active_inventory(store):
    library = st.session_state.get("library", {})
    server = st.session_state.get("server", getattr(st.session_state.get("adapter"), "server", ""))
    return next((a for a in store.audits() if a["server"] == server and a["library"] == library.get("id")
                 and a["status"] == "complete"), None)


def main():
    store = Store(os.getenv("ABS_MM_DB", str(ROOT / "data" / "metadata.sqlite3")))
    if "reconnect_checked" not in st.session_state:
        st.session_state["reconnect_checked"] = True
        saved = preferences.load()
        if saved.get("server", "").startswith(("http://", "https://")):
            try:
                token = preferences.credential(saved["server"])
                if token:
                    st.session_state["adapter"] = CliAdapter(Settings(saved["server"], token, saved.get("executable", "abs-cli")))
                else:
                    st.session_state["credential_error"] = preferences.CREDENTIAL_RECOVERY
            except preferences.CredentialError:
                st.session_state["credential_error"] = preferences.CREDENTIAL_RECOVERY
    with st.sidebar:
        st.header("Metadata Manager")
        saved = preferences.load()
        adapter = st.session_state.get("adapter")
        server = getattr(adapter, "server", saved.get("server", ""))
        st.session_state["server"] = server
        libraries = st.session_state.get("connection_result", {}).get("libraries", [])
        if not libraries:
            libraries = [{"id": a["library"], "name": a["name"], "mediaType": "book"}
                         for a in store.audits() if a["server"] == server]
        libraries = {lib["id"]: lib for lib in libraries if lib.get("mediaType") == "book"}
        if libraries:
            ids = list(libraries)
            library_key = "global_library:" + server
            if st.session_state.get(library_key) not in ids:
                preferred = saved.get("library", st.session_state.get("library", {}).get("id"))
                st.session_state[library_key] = preferred if preferred in ids else ids[0]
            chosen = st.selectbox("Library", ids, format_func=lambda key: libraries[key]["name"], key=library_key)
            st.session_state["library"] = libraries[chosen]
            if saved.get("library") != chosen:
                preferences.save({**saved, "library": chosen})
        else:
            st.session_state.pop("library", None)
        page = st.radio("Workspace", ["Import", "Match & Verify", "Changes", "History", "Settings"])
        adapter = st.session_state.get("adapter")
        st.caption(f"Connected · {adapter.server}" if adapter else "Local workspace · disconnected")
        st.divider()
        st.caption("Import → Match & Verify → Changes → History")
    if any(r["state"] in ("prepared", "applied", "uncertain") for r in store.history()):
        st.warning("Recovery required. Open History to reconcile before further writes.")
    if message := st.session_state.pop("flash", None):
        st.success(message)
    if error := st.session_state.get("credential_error"):
        st.warning(error)
    views = {"Import": library_view, "Match & Verify": lambda s: verification_view(s, active_inventory),
             "Changes": lambda s: changes_view(s, active_inventory(s), show_plan, progress_bar),
             "History": history_view, "Settings": settings_view}
    try:
        views[page](store)
    except (AdapterError, SafetyError, BusyError, ValueError, KeyError) as exc:
        st.error(str(exc))
        st.caption("Read-only transient requests use up to three attempts. Resume interrupted audits from Import; "
                   "start fresh if the library changed. Uncertain writes require reconciliation in Reconcile & History and are never retried automatically.")


if __name__ == "__main__":
    main()
