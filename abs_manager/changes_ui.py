"""Accumulated confirmed corrections, grouped by book."""

import streamlit as st

from .calibre import import_catalog, reconciliation_states, validate_local
from .models import review_fields
from .verification_ui import display, editor
from .workflow import MAX_APPLY_BATCH, Workflow, batch_results, confirmation, observe_selected, preview


def changes_view(store, audit, show_plan, progress):
    st.title("Changes")
    if not audit:
        st.info("Refresh a library first.")
        return
    if st.checkbox("All Libraries overview"):
        overview = []
        seen = set()
        for snapshot in store.audits():
            scope = (snapshot["server"], snapshot["library"])
            if scope in seen:
                continue
            seen.add(scope)
            overview.extend({"Library": snapshot["name"], "Book": r["title"], "Field": r["field"],
                             "Desired": display(r["proposed"]), "Status": r["status"]}
                            for r in store.corrections(snapshot["id"]) if r["status"] not in ("discarded", "observed_in_abs"))
        st.dataframe(overview, hide_index=True, width="stretch")
        st.caption("Select a library in the sidebar to approve and process its changes.")
    aid = audit["id"]
    with st.expander("Observe external changes (read-only)"):
        inventory = {b.id: b for b in store.books(aid)}
        observed = st.multiselect("Books to refresh (up to 25)", list(inventory),
                                  format_func=lambda iid: inventory[iid].values["title"])
        st.caption("Only selected books are reread. Others retain their older values. Write approvals expire. After native artwork changes, retrieve and inspect covers again in Match & Verify.")
        adapter = st.session_state.get("adapter")
        if st.button("Observe selected ABS values", disabled=not observed or adapter is None):
            st.session_state["snapshot_choice"] = observe_selected(store, adapter, aid, observed)
            st.rerun()
        st.dataframe(reconciliation_states(store, aid), hide_index=True, width="stretch")
        st.caption("Correct ebook metadata in Calibre, then Validate Calibre Changes. Use native ABS scan/refresh as appropriate for your library settings; observe ABS separately. A Calibre edit does not prove ABS synchronization.")
    rows = [r for r in store.corrections(aid) if r["status"] not in ("discarded", "observed_in_abs")]
    findings = [f for f in store.findings(aid) if f["actionable"] and f["status"] in ("pending", "approved")
                and f["kind"] in ("manual", "external", "confirmed_target")]
    # Legacy approvals remain visible, including approved algorithmic proposals.
    findings += [f for f in store.findings(aid) if f["actionable"] and f["status"] == "approved"
                 and f not in findings]
    books = {b.id: b for b in store.books(aid)}
    st.caption(f"Apply at most {MAX_APPLY_BATCH} audiobooks per invocation. Execution stops on the first unsafe or uncertain result.")
    eligible = [f for f in findings if f["item_id"] in books and books[f["item_id"]].is_audio]
    grouped = st.multiselect("Select staged fields together", [f["id"] for f in eligible],
                             format_func=lambda fid: next(books[f["item_id"]].values["title"] + " · " + f["field"] + " · " + f["item_id"]
                                                          for f in eligible if f["id"] == fid))
    rows = [r for r in rows if r["item_id"] not in books or r["field"] in review_fields(books[r["item_id"]].is_audio)]
    findings = [f for f in findings if f["item_id"] in books and f["field"] in review_fields(books[f["item_id"]].is_audio)]
    selected = list(grouped)
    st.dataframe([{"Book": books[f["item_id"]].values["title"], "Field": f["field"],
                   "Current": display(f["current"]), "Target": display(f["target"]), "Status": f["status"]}
                  for f in eligible], hide_index=True, width="stretch")
    with st.expander("Individual staged corrections"):
        for iid in dict.fromkeys([r["item_id"] for r in rows] + [f["item_id"] for f in findings]):
            st.subheader(books[iid].values["title"] if iid in books else "Removed book · " + iid)
            whole_book = st.checkbox("Select book for ABS preview", key="whole:" + str(aid) + iid)
            for row in [r for r in rows if r["item_id"] == iid]:
                st.write(f"**{row['field']}** · {row['status']} · " + ("ABS" if iid in books and books[iid].is_audio else "Calibre / manual"))
                st.text("Current: " + display(row["current"]) + "\nDesired: " + display(row["proposed"]))
                st.caption(row["evidence"])
                st.caption(f"Calibre verified: {row['calibre_verified'] or 'Not checked'} · ABS observed: {row['abs_observed'] or 'Not checked'}")
                if iid in books and row["field"] in review_fields(books[iid].is_audio) and row["status"] in ("pending", "edited_in_calibre", "verified_in_calibre"):
                    with st.form(f"correction:{row['id']}"):
                        target = editor(row["field"], row["proposed"], f"queue:{row['id']}")
                        edit = st.form_submit_button("Confirm edited correction")
                        discard = st.form_submit_button("Discard correction")
                    if edit or discard:
                        if discard:
                            store.discard_correction(row["id"])
                        else:
                            store.confirm_targets(aid, iid, {row["field"]: target}, row["evidence"])
                        st.rerun()
                elif st.button("Archive correction", key="archive:" + str(row["id"])):
                    store.discard_correction(row["id"])
                    st.rerun()
            for finding in [f for f in findings if f["item_id"] == iid]:
                checked = st.checkbox(f"Select {finding['field']}: {display(finding['target'])}", key=f"select:{finding['id']}")
                if (checked or whole_book) and finding["id"] not in selected:
                    selected.append(finding["id"])
    if not rows and not findings:
        st.info("No confirmed corrections pending. Verify or edit fields in Match & Verify.")
    st.caption(f"Selected: {len(selected)} fields across "
               f"{len({f['item_id'] for f in eligible if f['id'] in selected})} audiobooks · "
               f"Excluded from this selection: {len(eligible) - len(selected)} supported fields. "
               "Ebook/manual corrections remain in Individual staged corrections.")
    if st.button("Approve selected fields for ABS preview", disabled=not selected):
        store.review(selected, "approved")
    approved = [fid for fid in selected if any(f["id"] == fid and f["status"] == "approved" for f in store.findings(aid))]
    if approved:
        plan = preview(store, aid, approved)
        show_plan(plan)
        st.code(confirmation(plan))
        adapter = st.session_state.get("adapter")
        with st.form("apply:" + plan["digest"]):
            phrase = st.text_input("Apply confirmation")
            checked = st.checkbox("I approve the exact fields shown and want to write this batch to Audiobookshelf.")
            submit = st.form_submit_button("Apply confirmed changes", disabled=adapter is None or bool(store.unresolved(audit["server"])))
        if submit:
            if not checked:
                raise ValueError("Select the explicit confirmation checkbox.")
            st.session_state["last_batch"] = plan
            try:
                Workflow(store, adapter).apply(plan, phrase, progress())
            finally:
                st.dataframe(batch_results(store, plan), hide_index=True, width="stretch")
            st.success("Verified in ABS. Refresh Library to observe the updated inventory.")
    batches = {}
    for operation in store.history(audit["server"]):
        saved = operation["data"].get("batch_plan")
        if saved and saved["library"] == audit["library"]:
            batches.setdefault(saved["digest"], saved)
    if batches:
        with st.expander("Saved batch results"):
            bid = st.selectbox("Batch fingerprint", list(batches))
            st.dataframe(batch_results(store, batches[bid]), hide_index=True, width="stretch")
    if st.button("Validate Calibre Changes"):
        from .preferences import load
        settings = load()
        count = validate_local(store, aid, settings.get("calibre_executable", "calibredb"), settings.get("calibre_library", ""))
        st.success(f"{count} ebooks matched in Calibre. Unmatched records stay unverified; use Advanced catalog input to associate ambiguous records. ABS synchronization is checked separately on refresh.")
    with st.expander("Advanced catalog input"):
        st.caption("Use the exact Calibre library folder as context. Ambiguous records require an explicit association; numeric IDs alone cannot verify corrections.")
        library = st.text_input("Catalog library context")
        upload = st.file_uploader("Calibre catalog JSON", type="json")
        if st.button("Compare catalog", disabled=upload is None or not library):
            count = import_catalog(store, aid, upload.getvalue().decode("utf-8-sig"), library or None)
            st.success(f"{count} ebooks matched. Correction verification requires a library context and UUID.")
        ebooks = [b for b in books.values() if not b.is_audio]
        if ebooks:
            iid = st.selectbox("Associate ebook", [b.id for b in ebooks], format_func=lambda i: books[i].values["title"])
            cid = st.number_input("Calibre record ID", min_value=0, step=1)
            uuid = st.text_input("Calibre record UUID")
            if st.button("Associate and compare catalog", disabled=upload is None or not library or not uuid):
                import_catalog(store, aid, upload.getvalue().decode("utf-8-sig"), library,
                               {iid: {"id": cid, "uuid": uuid}})
                st.success("Read-only association compared. No Calibre or ABS write was sent.")
