"""Accumulated confirmed corrections, grouped by book."""
import streamlit as st

from .calibre import import_catalog, reconciliation_states, validate_local
from .models import description_preview, digest, review_fields
from .review_table import correction_table
from .verification_ui import display, editor
from .workflow import MAX_APPLY_BATCH, Workflow, batch_results, confirmation, observe_selected, preview


def overview_value(field, value):
    return display(description_preview(value) if field == "description" else value)


def changes_view(store, audit, show_plan, progress):
    st.subheader("Stage and apply")
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
                             "Desired": overview_value(r["field"], r["proposed"]), "Status": r["status"]}
                            for r in store.corrections(snapshot["id"]) if r["status"] not in ("discarded", "observed_in_abs"))
        st.dataframe(overview, hide_index=True, width="stretch")
        st.caption("Select a library in the sidebar to approve and process its changes.")
    aid = audit["id"]
    with st.expander("Observe external changes (read-only)"):
        inventory = {b.id: b for b in store.books(aid)}
        observed = st.multiselect("Books to refresh (up to 25)", list(inventory),
                                  format_func=lambda iid: inventory[iid].values["title"])
        st.caption("Only selected books are reread. Others retain their older values. Write approvals expire. After native artwork changes, retrieve and inspect covers again in Quality exceptions.")
        adapter = st.session_state.get("adapter")
        if st.button("Observe selected ABS values", disabled=not observed or adapter is None):
            st.session_state["snapshot_choice"] = observe_selected(store, adapter, aid, observed)
            st.rerun()
        if st.checkbox("Load synchronization assessment", key=f"sync_assessment:{aid}"):
            st.dataframe(reconciliation_states(store, aid), hide_index=True, width="stretch")
        st.caption("Correct ebook metadata in Calibre, then Validate Calibre Changes. Use native ABS scan/refresh as appropriate for your library settings; observe ABS separately. A Calibre edit does not prove ABS synchronization.")
    rows = [r for r in store.corrections(aid) if r["status"] not in ("discarded", "observed_in_abs")]
    all_findings = store.findings(aid)
    findings = [f for f in all_findings if f["actionable"] and f["status"] in ("pending", "approved")
                and f["kind"] in ("manual", "external", "confirmed_target")]
    # Legacy approvals remain visible, including approved algorithmic proposals.
    findings += [f for f in all_findings if f["actionable"] and f["status"] == "approved"
                 and f not in findings]
    books = {b.id: b for b in store.books(aid)}
    st.caption(f"Apply at most {MAX_APPLY_BATCH} audiobooks per invocation. Execution stops on the first unsafe or uncertain result.")
    eligible = [f for f in findings if f["item_id"] in books and books[f["item_id"]].is_audio]
    labels = {f["id"]: books[f["item_id"]].values["title"] + " · " + f["field"] + " · " + f["item_id"]
              for f in eligible}
    table = [{"id": f["id"], "Book": books[f["item_id"]].values["title"],
              "Author": ", ".join(books[f["item_id"]].values["authors"]), "Field": f["field"],
              "Current": overview_value(f["field"], f["current"]),
              "Suggested": overview_value(f["field"], f["target"]), "Evidence": "Human staged target",
              "Status": "Approved / ready for preview" if f["status"] == "approved" else "Ready for approval",
              "Priority": 0 if f["status"] == "pending" else 1} for f in eligible]
    _, selected = correction_table(table, labels, labels, f"staged:{store.path}:{aid}")
    descriptions = {r["item_id"] for r in rows + findings if r["field"] == "description"}
    inspected = st.selectbox("Inspect full description correction", sorted(descriptions), index=None,
                             format_func=lambda iid: books[iid].values["title"] if iid in books else iid,
                             key=f"description_correction:{aid}") if descriptions else None
    if inspected is not None:
        values = [r for r in rows + findings if r["item_id"] == inspected and r["field"] == "description"]
        for index, row in enumerate(values):
            detail_key = f"description_detail:{aid}:{inspected}:{index}:{digest(row)}"
            st.text_area("Original description", display(row["current"]), disabled=True, key=detail_key + ":current")
            st.text_area("Proposed description", display(row.get("target", row.get("proposed"))),
                         disabled=True, key=detail_key + ":proposed")
    with st.expander("Inspect or edit a staged book / Calibre corrections"):
        book_ids = list(dict.fromkeys([r["item_id"] for r in rows] + [f["item_id"] for f in findings]))
        detail_id = st.selectbox("Staged book details", book_ids, index=None,
                                 format_func=lambda iid: books[iid].values["title"] if iid in books else iid,
                                 key=f"staged_detail:{aid}")
        for iid in ([detail_id] if detail_id is not None else []):
            st.subheader(books[iid].values["title"] if iid in books else "Removed book · " + iid)
            whole_book = st.checkbox("Select book for ABS preview", key="whole:" + str(aid) + iid)
            for row in [r for r in rows if r["item_id"] == iid]:
                st.write(f"**{row['field']}** · {row['status']} · " + ("ABS" if iid in books and books[iid].is_audio else "Calibre / manual"))
                st.text("Current: " + overview_value(row["field"], row["current"]) + "\nDesired: " +
                        overview_value(row["field"], row["proposed"]))
                st.caption(row["evidence"])
                st.caption(f"Calibre verified: {row['calibre_verified'] or 'Not checked'} · ABS observed: {row['abs_observed'] or 'Not checked'}")
                if iid in books and row["field"] in review_fields(books[iid].is_audio) and row["status"] in ("pending", "edited_in_calibre", "verified_in_calibre"):
                    if row["field"] == "description" and inspected != iid:
                        st.caption("Select this book in Inspect full description correction to edit its complete text.")
                        continue
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
                checked = st.checkbox(f"Select {finding['field']}: {overview_value(finding['field'], finding['target'])}",
                                      key=f"select:{finding['id']}")
                if (checked or whole_book) and finding["id"] not in selected:
                    selected.append(finding["id"])
    if not rows and not findings:
        st.info("No staged corrections pending. Review suggestions or open Quality exceptions below.")
    st.caption(f"Selected: {len(selected)} fields across "
               f"{len({f['item_id'] for f in eligible if f['id'] in selected})} audiobooks · "
               f"Excluded from this selection: {len(eligible) - len(selected)} supported fields. "
               "Ebook/manual corrections remain in the book inspector.")
    if st.button("Approve selected fields for ABS preview", disabled=not selected):
        store.review(selected, "approved")
    approved_ids = {f["id"] for f in store.findings(aid) if f["status"] == "approved"} if selected else set()
    approved = [fid for fid in selected if fid in approved_ids]
    if approved:
        plan = preview(store, aid, approved)
        for row in plan.get("exclusions", []):
            st.warning(f"{row['title']} ({row['item_id']}) · {row['field']}. {row['reason']}")
    if approved and plan["books"]:
        show_plan(plan)
        st.code(confirmation(plan))
        adapter = st.session_state.get("adapter")
        with st.form("apply:" + plan["digest"]):
            phrase = st.text_input("Apply confirmation")
            checked = st.checkbox("I approve the exact fields shown and want to write this batch to Audiobookshelf.")
            submit = st.form_submit_button("Apply confirmed changes", disabled=adapter is None or store.has_unresolved(audit["server"]))
        if submit:
            if not checked:
                raise ValueError("Select the explicit confirmation checkbox.")
            st.session_state["last_batch"] = plan
            try:
                Workflow(store, adapter).apply(plan, phrase, progress())
            finally:
                st.dataframe(batch_results(store, plan), hide_index=True, width="stretch")
            st.success("Verified in ABS. Refresh Library to observe the updated inventory.")
    with st.expander("Saved batch results"):
        if st.checkbox("Load saved batch results"):
            batches = {r["digest"]: r["id"] for r in store.saved_batches(audit["server"], audit["library"])}
            if batches:
                bid = st.selectbox("Batch fingerprint", list(batches))
                saved = store.operation(batches[bid])["data"]["batch_plan"]
                st.dataframe(batch_results(store, saved), hide_index=True, width="stretch")
            else:
                st.caption("No saved batches for this library.")
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
