"""Owner review controls for an uploaded recommendations document."""
from dataclasses import replace
from hashlib import sha256

import streamlit as st

from .adapter import AdapterError
from .locking import BusyError
from .models import compact, description_preview, validate_target
from .review_import import (
    ABS_FIELDS,
    ALREADY_APPLIED,
    ALREADY_STAGED,
    EDITORIAL,
    EXISTING,
    HELD,
    READY,
    check_live,
    parse_review,
    review_rows,
    stage_review,
)
from .review_table import correction_table
from .workflow import MAX_APPLY_BATCH


def _label(row):
    return f"{row.author} · {row.title} · {row.proposal.field} · {row.proposal.item_id}"


def _table(rows, live):
    def value(row, text):
        return compact(description_preview(text) if row.proposal.field == "description" else text)

    return [{"Author": r.author, "Book": r.title, "Item ID": r.proposal.item_id, "Field": r.proposal.field,
             "ABS field": ABS_FIELDS.get(r.proposal.field, r.proposal.field),
             "Expected": value(r, r.proposal.expected), "Saved current": value(r, r.current),
             "Desired": value(r, r.proposal.desired),
             "Confidence": r.proposal.confidence, "Review status": r.proposal.status,
             "Capability": r.capability, "Disposition": r.disposition, "Live check": live.get(r.proposal.id, "")}
            for r in rows]


def import_corrections(store, audit):
    with st.container():
        with st.expander("Import AI suggestions"):
            upload = st.file_uploader("Reviewed recommendations JSON", type=["json"], max_upload_size=5)
        if upload is None:
            st.caption("Import recommendations to review proposed corrections. Existing staged work is available below.")
            return
        data = upload.getvalue()
        key = f"review_import:{audit['id']}:{sha256(data).hexdigest()}"
        try:
            # Only immutable upload parsing is reused. Re-evaluate saved state on every rerun,
            # and stage_review/check_live independently revalidate under the workspace lock.
            parsed = st.session_state.get("parsed_review_upload")
            if parsed is None or parsed[0] != key:
                parsed = (key, parse_review(data))
                st.session_state["parsed_review_upload"] = parsed
            overrides = st.session_state.get(key + ":descriptions", {})
            document = replace(parsed[1], proposals=tuple(replace(p, desired=overrides[p.id])
                               if p.id in overrides else p for p in parsed[1].proposals))
            rows = review_rows(store, audit["id"], document)
        except ValueError as exc:
            st.error(str(exc))
            return
        if notice := st.session_state.pop(key + ":notice", None):
            st.success(notice)
        source_id = document.source["snapshot_id"]
        with st.expander("Import provenance"):
            st.caption(f"Source snapshot #{source_id} · Selected saved snapshot #{audit['id']}. "
                       "Staging creates local targets. Stage and apply requires separate write approval.")
        if source_id != audit["id"]:
            st.info("A different saved snapshot is selected. Staging requires matching book identity and "
                    "metadata in both snapshots.")
        if store.latest(audit["server"], audit["library"])["id"] != audit["id"] or audit["read_only"]:
            st.info("Complete the latest live import to enable local staging.")

        new = [r for r in rows if r.proposal.status != EXISTING]
        prior = [r for r in rows if r.proposal.status == EXISTING]
        st.caption(f"{sum(r.disposition in (READY, EDITORIAL, HELD) for r in new)} recommendations need review · "
                   f"{len(new)} uploaded rows · {len(prior)} prior corrections. Upload confidence is advisory.")
        live = st.session_state.get(key + ":live", {})
        held = {r.proposal.id: r for r in new if r.disposition == HELD}
        confirmed = []
        if held:
            with st.expander("Recording confirmation"):
                confirmed = st.multiselect("Confirm recording for these narrator changes", list(held),
                                       format_func=lambda pid: _label(held[pid]), key=key + ":held",
                                       help="Confirm the exact recording and narrator credits before selection.")
        eligible = {r.proposal.id: r for r in new
                    if r.disposition in (READY, EDITORIAL) or r.disposition == HELD and r.proposal.id in confirmed}
        def status(row):
            return {READY: "Needs review", EDITORIAL: "Needs review", HELD: "Needs recording evidence",
                    ALREADY_STAGED: "Staged", ALREADY_APPLIED: "Observed in library"}.get(row.disposition, row.disposition)

        table = [{"id": r.proposal.id, "Book": r.title, "Author": r.author, "Field": r.proposal.field,
                  "Current": compact(description_preview(r.current) if r.proposal.field == "description" else r.current),
                  "Suggested": compact(description_preview(r.proposal.desired) if r.proposal.field == "description" else r.proposal.desired),
                  "Evidence": r.proposal.confidence + (" / individual editorial review" if r.disposition == EDITORIAL else ""),
                  "Status": status(r), "Priority": 0 if r.disposition == READY else 1} for r in new]
        _, selected = correction_table(table, eligible,
            [pid for pid, r in eligible.items() if r.disposition not in (EDITORIAL, HELD)], key)
        indexed = {r.proposal.id: r for r in new}
        details = st.selectbox("Recommendation details", list(indexed), index=None,
                               format_func=lambda pid: _label(indexed[pid]), key=key + ":details")
        if details is not None:
            row = indexed[details]
            p = row.proposal
            book = store.book(audit["id"], p.item_id)
            st.subheader(row.title)
            if book:
                st.caption("Authors: " + ", ".join(book.values["authors"]) + " · Narrators: " +
                           ", ".join(book.values.get("narrators", [])))
                st.caption("Series: " + compact(book.values.get("series", [])))
            st.caption(row.disposition + " · " + row.capability)
            st.write(p.rationale)
            st.dataframe(_table([r for r in new if r.proposal.item_id == p.item_id], live),
                         hide_index=True, width="stretch")
            if p.field == "description":
                cols = st.columns(2)
                cols[0].text_area("Saved current description", row.current or "", disabled=True,
                                  height=240, key=key + ":original:" + p.id)
                with cols[1]:
                    with st.form(key + ":edit:" + p.id):
                        desired = st.text_area("Proposed description", p.desired or "", height=240)
                        save = st.form_submit_button("Save proposed description", disabled=p.id not in eligible)
                    if save:
                        validate_target("description", desired)
                        st.session_state[key + ":descriptions"] = {**overrides, p.id: desired}
                        st.rerun()
                st.caption("Read the full description, then select its checkbox individually. Saving an edit is not staging or approval.")
            with st.expander("Evidence and source details"):
                st.json({"expected": p.expected, "saved_current": row.current,
                         "desired": p.desired, "evidence": p.evidence,
                         "declared_write_path": p.write_path, "live_check": live.get(p.id, "Not requested")})

        if prior:
            st.write("Previously staged in the source export")
            st.dataframe(_table(prior, live), hide_index=True, width="stretch")

        native = [r for r in new if r.capability != "Guarded ABS"
                  and r.disposition not in (ALREADY_APPLIED, ALREADY_STAGED, "No change")]
        if native:
            checklist = [{"item_id": r.proposal.item_id, "author": r.author, "book": r.title,
                          "authority": r.capability, "field": ABS_FIELDS.get(r.proposal.field, r.proposal.field),
                          "expected_original_value": r.proposal.expected, "saved_current_value": r.current,
                          "complete_desired_value": r.proposal.desired,
                          "review_status": r.proposal.status, "disposition": r.disposition,
                          "rationale": r.proposal.rationale, "confidence": r.proposal.confidence,
                          "evidence": r.proposal.evidence} for r in sorted(native, key=_label)]
            st.download_button(f"Download native/manual review checklist ({len(native)})",
                               compact({"source_snapshot": source_id, "genre_policy": document.genre_policy,
                                        "outstanding": checklist}), file_name="native-metadata-checklist.json",
                               mime="application/json", on_click="ignore", key=key + ":download")

        live_blocked = any(pid in live and live[pid] != "Matches saved snapshot" for pid in selected)
        if live_blocked:
            st.warning("Selected fields include an ABS mismatch or a value already present in ABS. "
                       "Review the live-check column and refresh the inventory or update the selection.")
        adapter = st.session_state.get("adapter")
        books = {eligible[pid].proposal.item_id for pid in selected}
        try:
            if st.button(f"Check selected books in ABS (up to {MAX_APPLY_BATCH}, read-only)",
                         disabled=adapter is None or not books or len(books) > MAX_APPLY_BATCH, key=key + ":check"):
                st.session_state[key + ":live"] = {**live, **check_live(
                    store, adapter, audit["id"], document, selected, confirmed)}
                st.rerun()
            if st.button(f"Stage selected changes locally ({len(selected)})", disabled=not selected or live_blocked,
                         key=key + ":stage"):
                count = stage_review(store, audit["id"], document, selected, confirmed)
                st.session_state[key + ":notice"] = f"Staged {count} fields locally. Open Stage and apply below for separate approval."
                st.rerun()
        except (ValueError, BusyError) as exc:
            st.error(str(exc))
        except AdapterError:
            st.error("The ABS read failed. Check the connection and try again.")
