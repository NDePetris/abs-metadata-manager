from __future__ import annotations

import csv
import io
import json
import os

import streamlit as st
from dotenv import load_dotenv

from abs_manager.adapter import AdapterError, CliAdapter
from abs_manager.audit import analyze
from abs_manager.config import ROOT, Settings, defaults
from abs_manager.locking import BusyError, operation_lock
from abs_manager.models import FIELDS, compact
from abs_manager.sample import sample_audit
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, confirmation, import_previous, preview, run_audit

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Audiobookshelf Metadata Manager", layout="wide", initial_sidebar_state="auto")


def display(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def flash(message):
    st.session_state["flash"] = message
    st.rerun()


def parse_editor(field, text):
    return text if field in ("title", "subtitle") else json.loads(text)


def progress_bar():
    bar = st.progress(0, text="Starting sequential operation…")

    def update(done, total, title):
        bar.progress(done / max(total, 1), text=f"{done} / {total} · {title}")

    return update


def settings_view(store):
    st.title("Connection & settings")
    st.write("Connect to your Audiobookshelf server with an API key. Credentials stay in this session.")
    d = defaults()
    # Widget keys belong to Streamlit; connection_result/error are application state.
    with st.form("settings_connection_form"):
        server = st.text_input("Server URL", value=d["server"], placeholder="http://your-server:13378")
        token = st.text_input("API key", value=d["token"], type="password", help="Uses ABS_TOKEN or abs-cli config when available.")
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
                adapter = CliAdapter(Settings(server, token, executable, interval, timeout))
                result = adapter.probe()
            st.session_state["adapter"] = adapter
            st.session_state["connection_result"] = result
        except (AdapterError, BusyError, ValueError, KeyError) as exc:
            st.session_state["connection_error"] = str(exc)
        # Refresh the sidebar too, including when replacing a successful connection fails.
        st.rerun()
    if error := st.session_state.get("connection_error"):
        st.error(error)
    connection = st.session_state.get("connection_result")
    adapter = st.session_state.get("adapter")
    if connection and adapter:
        st.success("Connected. Select an audiobook library below.")
        st.write(f"**Server:** {adapter.server} · **ABS:** {connection['server_version']} · "
                 f"**abs-cli:** {connection['cli_version']}")
        for warning in connection["warnings"]:
            st.warning(warning)
        libraries = [lib for lib in connection["libraries"] if lib.get("mediaType") == "book"]
        if libraries:
            ids = [lib["id"] for lib in libraries]
            saved_library = st.session_state.get("library", {}).get("id", d["library"])
            chosen = st.selectbox("Book library", ids, index=ids.index(saved_library) if saved_library in ids else 0,
                                  format_func=lambda key: next(lib["name"] for lib in libraries if lib["id"] == key),
                                  key="library_choice")
            st.session_state["library"] = next(lib for lib in libraries if lib["id"] == chosen)
            st.caption("ABS uses mediaType=book for audiobooks and ebooks. Audio items are writable; ebooks stay read-only.")
        if st.button("Disconnect"):
            for key in ("adapter", "connection_result", "connection_error", "library"):
                st.session_state.pop(key, None)
            st.rerun()
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
        st.info("Offline snapshot · read-only. Approval and Apply require a fresh live audiobook audit.")
    elif audit["status"] != "complete":
        st.warning("This audit is incomplete. Partial observations are retained; run another whole-library audit to review changes.")
    st.caption(f"Source: {audit['server']} · Library ID: {audit['library']}")
    return audit


def library_view(store):
    st.title("Library")
    st.write("Audit the whole library, then review each proposed field correction.")
    adapter, library = st.session_state.get("adapter"), st.session_state.get("library")
    if adapter and library:
        st.write(f"**Connected:** {adapter.server} / {library['name']}")
        if st.button("Audit whole library", type="primary"):
            with st.spinner("Reading pages and expanded items, one call at a time…"):
                aid = run_audit(store, adapter, library, progress_bar())
            st.session_state["snapshot_choice"] = aid
            flash("Audit complete. Every proposal starts pending; open Review to make decisions.")
        st.caption("New audits expire prior approvals. Use Streamlit’s Stop control to interrupt; incomplete audits cannot be applied.")
    else:
        st.info("Open Settings to connect and select your audiobook library.")
    audit = choose_snapshot(store)
    if audit is None:
        st.subheader("Your first audit")
        st.write("Connect in Settings, select the audiobook library, and choose Audit whole library.")
        if st.button("Load read-only sample library"):
            aid = import_previous(store, sample_audit(), "Synthetic sample library")
            st.session_state["snapshot_choice"] = aid
            flash("Loaded six synthetic examples. No server connection or metadata writes.")
        return
    books = store.books(audit["id"])
    findings = store.findings(audit["id"])
    st.write(f"**{len(books)} books** · {sum(b.is_audio for b in books)} with audio · "
             f"{sum(f['actionable'] for f in findings)} correction proposals · "
             f"{sum(not f['actionable'] for f in findings)} observations")
    query = st.text_input("Search titles, authors, narrators, or paths").casefold()
    filtered = [b for b in books if query in compact(b.values).casefold() or query in
                (b.raw.get("relPath") or b.raw.get("path", "")).casefold()]
    if not filtered:
        st.info("No books match this search.")
        return
    st.dataframe([{"Title": b.values["title"], "Authors": ", ".join(b.values["authors"]),
                   "Narrators": ", ".join(b.values["narrators"]), "Series": display(b.values["series"]),
                   "Type": "Audiobook" if b.is_audio else "Ebook · read-only",
                   "Cover": "Present" if b.raw["media"].get("coverPath") else "Missing"} for b in filtered],
                 hide_index=True, width="stretch")
    selected = st.selectbox("Inspect book", [b.id for b in filtered],
                            format_func=lambda key: next(b.values["title"] or key for b in filtered if b.id == key))
    book = next(b for b in filtered if b.id == selected)
    left, right = st.columns([2, 1])
    with left:
        st.subheader(book.values["title"] or "Untitled book")
        st.write(book.raw.get("relPath") or book.raw["path"])
        st.json(book.values)
        with st.expander("Normalized values and evidence"):
            normalized, fs = analyze(book)
            st.write("Normalized values are analytical evidence; they are never applied automatically.")
            st.json({"normalized": normalized, "evidence": fs[0].evidence if fs else {},
                     "chapter_names": [c.get("title") for c in book.raw["media"].get("chapters", [])]})
        with st.expander("Observed item JSON"):
            st.json(book.raw)
    with right:
        if book.raw["media"].get("coverPath"):
            if adapter and adapter.server == audit["server"] and st.button("Load cover preview"):
                with operation_lock(store.path):
                    image_bytes = adapter.cover(book.id)
                st.image(image_bytes, width=220)
            else:
                st.caption("Cover present in snapshot. Load its preview when connected to the source server.")
        else:
            st.caption("No cover found.")


def review_view(store):
    st.title("Review")
    st.write("Confidence is advisory. Only your explicit approval adds a field to Apply.")
    audit = choose_snapshot(store)
    if audit is None:
        st.info("Run an audit from Library to start reviewing.")
        return
    aid = audit["id"]
    findings = store.findings(aid)
    books = {b.id: b for b in store.books(aid)}
    can_review = audit["status"] == "complete" and not audit["read_only"] and store.latest(
        audit["server"], audit["library"])["id"] == aid
    cols = st.columns(3)
    fields = cols[0].multiselect("Fields", sorted({f["field"] for f in findings}))
    confidence = cols[1].multiselect("Confidence", sorted({f["confidence"] for f in findings}))
    statuses = cols[2].multiselect("Review status", sorted({f["status"] for f in findings}))
    corrections_only = st.checkbox("Show correction proposals only", value=True)
    filtered = [f for f in findings if (not fields or f["field"] in fields)
                and (not confidence or f["confidence"] in confidence)
                and (not statuses or f["status"] in statuses) and (f["actionable"] or not corrections_only)]
    st.caption(f"{len(filtered)} findings shown. Observations about covers, chapters, and uncertain differences remain read-only.")
    records = [{"ID": f["id"], "Book": books[f["item_id"]].values["title"], "Field": f["field"],
                "Current": display(f["current"]), "Proposed": display(f["target"]), "Kind": f["kind"],
                "Confidence": f["confidence"], "Status": f["status"], "Reason": f["reason"]} for f in filtered]
    if records:
        st.dataframe(records, hide_index=True, width="stretch")
        by_id = {f["id"]: f for f in filtered}
        choices = [f["id"] for f in filtered if f["actionable"] and f["status"] not in ("applied", "expired")]
        selected = st.multiselect("Explicitly select proposals for a bulk decision", choices,
                                 format_func=lambda fid: f"#{fid} · {books[by_id[fid]['item_id']].values['title']} · {by_id[fid]['field']}")
        c1, c2 = st.columns(2)
        if c1.button("Approve selected fields", disabled=not selected or not can_review):
            store.review(selected, "approved")
            flash(f"Approved {len(selected)} explicitly selected fields. Open Apply to preview.")
        if c2.button("Reject selected fields", disabled=not selected or not can_review):
            store.review(selected, "rejected")
            flash(f"Rejected {len(selected)} selected fields.")
        fid = st.selectbox("Inspect one finding", list(by_id),
                           format_func=lambda fid: f"#{fid} · {books[by_id[fid]['item_id']].values['title']} · {by_id[fid]['field']}")
        f = by_id[fid]
        st.subheader(f"{f['field'].capitalize()} · {f['kind']}")
        a, b = st.columns(2)
        a.write("**Current observed value**")
        a.code(display(f["current"]), language="json" if not isinstance(f["current"], str) else None, wrap_lines=True)
        b.write("**Proposed value**")
        b.code(display(f["target"]), language="json" if not isinstance(f["target"], str) else None, wrap_lines=True)
        st.write(f["reason"])
        with st.expander("Evidence", expanded=False):
            st.json(f["evidence"])
        if f["actionable"]:
            disabled = not can_review or f["status"] in ("applied", "expired")
            st.caption(f"Saved decision: {f['status']}. Choose approval explicitly for any edited value.")
            with st.form(f"review_{fid}_{f['reviewed']}"):
                text = st.text_area("Edit proposed value", value=display(f["target"]),
                                    help="Text for title/subtitle. JSON arrays for authors, narrators, or series.")
                decision = st.selectbox("Decision", ["pending", "approved", "rejected"], index=0)
                if st.form_submit_button("Save field decision", disabled=disabled):
                    store.review([fid], decision, parse_editor(f["field"], text))
                    flash("Field decision saved. Editing the target requires the decision chosen here.")
    else:
        st.info("No findings match these filters.")
    with st.expander("Create a manual field proposal"):
        st.caption("Use this for missing credits, subtitles, or corrections that need your own edition evidence. New proposals start pending.")
        if books:
            with st.form("manual_proposal"):
                item_id = st.selectbox("Book", list(books), format_func=lambda key: books[key].values["title"] or key)
                field = st.selectbox("Metadata field", FIELDS)
                target_text = st.text_area("New value", help='Example contributors: ["Author One", "Author Two"]. Series: [{"name":"Series","sequence":"2.5"}]')
                reason = st.text_input("Evidence / reason")
                if st.form_submit_button("Create pending proposal", disabled=not can_review):
                    store.propose(aid, item_id, field, parse_editor(field, target_text), reason)
                    flash("Created a pending proposal. Review and approve it explicitly before applying.")
    if findings:
        st.download_button("Export findings & decisions (JSON)", json.dumps(findings, ensure_ascii=False, indent=2),
                           f"audit-{aid}-findings.json", "application/json")
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=["id", "item_id", "field", "current", "target", "confidence", "status", "reason"])
        writer.writeheader()
        for f in findings:
            values = {k: display(f[k]) for k in writer.fieldnames}
            # Prevent exported spreadsheet cells from evaluating formula-like book/user text.
            writer.writerow({k: "'" + v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in values.items()})
        st.download_button("Export findings & decisions (CSV)", buffer.getvalue(), f"audit-{aid}-findings.csv", "text/csv")


def show_plan(plan):
    st.write(f"**Server:** {plan['server']} · **Library ID:** {plan['library']}")
    for group in plan.get("books", [plan]):
        st.write(f"**{group['before'].get('title') or group['identity']['id']}**")
        st.caption(f"Item {group['identity']['id']} · {group['identity']['relPath'] or group['identity']['path']}")
        st.dataframe([{"Field": field, "Before": display(group["before"][field]), "After": display(value)}
                      for field, value in group["changes"].items()], hide_index=True, width="stretch")
        with st.expander("Exact CLI metadata payload"):
            st.json(group["payload"])


def apply_view(store):
    st.title("Apply")
    st.write("Preview approved changes, then confirm this exact batch. Live values are revalidated before each write.")
    audit = choose_snapshot(store)
    if audit is None:
        st.info("Run an audit and approve fields in Review first.")
        return
    if audit["read_only"] or audit["status"] != "complete":
        return
    plan = preview(store, audit["id"])
    if not plan["books"]:
        st.info("No approved fields are pending. Open Review to approve specific corrections.")
        return
    count = sum(len(b["changes"]) for b in plan["books"])
    st.subheader(f"Dry run · {count} fields across {len(plan['books'])} books")
    show_plan(plan)
    st.caption("This preview performs no remote writes. One metadata update per book; stop at the first uncertain result.")
    adapter = st.session_state.get("adapter")
    selected_library = st.session_state.get("library", {})
    connected = (adapter is not None and adapter.server == plan["server"]
                 and selected_library.get("id") == plan["library"])
    blocked = store.unresolved(plan["server"])
    if blocked:
        st.warning("Unresolved History entries block writes. Reconcile them first.")
    if not connected:
        st.info("Connect to this snapshot’s source server and select its library in Settings before applying.")
    expected = confirmation(plan)
    st.write("To confirm the exact values above, enter:")
    st.code(expected, language=None)
    with st.form(f"apply_{plan['digest']}"):
        phrase = st.text_input("Apply confirmation", key=f"phrase_{plan['digest']}")
        acknowledged = st.checkbox("I approve the exact fields shown and want to write this batch to Audiobookshelf.")
        submit = st.form_submit_button("Apply confirmed changes", type="primary", disabled=not connected or bool(blocked))
    if submit:
        if not acknowledged:
            st.error("Select the explicit write confirmation checkbox.")
            return
        ids = Workflow(store, adapter).apply(plan, phrase, progress_bar())
        flash(f"Verified {len(ids)} book updates. Results and guarded rollback are available in History.")


def history_view(store):
    st.title("History")
    st.write("Inspect durable intent and observed results. Reconciliation only reads the server; rollback requires confirmation.")
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


def main():
    store = Store(os.getenv("ABS_MM_DB", str(ROOT / "data" / "metadata.sqlite3")))
    with st.sidebar:
        st.header("Metadata Manager")
        page = st.radio("Workspace", ["Library", "Review", "Apply", "History", "Settings"])
        adapter = st.session_state.get("adapter")
        st.caption(f"Connected · {adapter.server}" if adapter else "Local workspace · disconnected")
        st.divider()
        st.caption("Audit → Review → Preview → Confirm → Verify")
    if message := st.session_state.pop("flash", None):
        st.success(message)
    views = {"Library": library_view, "Review": review_view, "Apply": apply_view,
             "History": history_view, "Settings": settings_view}
    try:
        views[page](store)
    except (AdapterError, SafetyError, BusyError, ValueError, KeyError) as exc:
        st.error(str(exc))
        st.caption("No automatic retry. Check Settings, run a fresh audit for stale metadata, or reconcile History for uncertain updates.")


if __name__ == "__main__":
    main()
