"""Owner review controls for an uploaded recommendations document."""
from collections import Counter
from hashlib import sha256

import streamlit as st

from .adapter import AdapterError
from .locking import BusyError
from .models import compact
from .review_import import (
    ALREADY_APPLIED,
    ALREADY_STAGED,
    EXISTING,
    HELD,
    NATIVE_FIELDS,
    READY,
    check_live,
    parse_review,
    review_rows,
    stage_review,
)
from .workflow import MAX_APPLY_BATCH


def _label(row):
    return f"{row.author} · {row.title} · {row.proposal.field} · {row.proposal.item_id}"


def _table(rows, live):
    return [{"Author": r.author, "Book": r.title, "Item ID": r.proposal.item_id, "Field": r.proposal.field,
             "ABS field": NATIVE_FIELDS.get(r.proposal.field, r.proposal.field),
             "Expected": compact(r.proposal.expected), "Saved current": compact(r.current),
             "Desired": compact(r.proposal.desired),
             "Confidence": r.proposal.confidence, "Review status": r.proposal.status,
             "Capability": r.capability, "Disposition": r.disposition, "Live check": live.get(r.proposal.id, "")}
            for r in rows]


def import_corrections(store, audit):
    with st.expander("Import AI corrections"):
        upload = st.file_uploader("Reviewed recommendations JSON", type=["json"])
        if upload is None:
            return
        data = upload.getvalue()
        key = f"review_import:{audit['id']}:{sha256(data).hexdigest()}"
        try:
            document = parse_review(data)
            rows = review_rows(store, audit["id"], document)
        except ValueError as exc:
            st.error(str(exc))
            return
        if notice := st.session_state.pop(key + ":notice", None):
            st.success(notice)
        source_id = document.source["snapshot_id"]
        st.caption(f"Source snapshot #{source_id} · Selected saved snapshot #{audit['id']}. "
                   "Staging creates local targets. ABS preview, approval and confirmation happen in Changes.")
        if source_id != audit["id"]:
            st.info("A different saved snapshot is selected. Staging requires matching book identity and "
                    "metadata in both snapshots.")
        if store.latest(audit["server"], audit["library"])["id"] != audit["id"] or audit["read_only"]:
            st.info("Complete the latest live import to enable local staging.")

        new = [r for r in rows if r.proposal.status != EXISTING]
        prior = [r for r in rows if r.proposal.status == EXISTING]
        st.caption(f"{len(new)} new recommendations · {len(prior)} prior corrections in the upload")
        summary = []
        for group, values in (("Field", (r.proposal.field for r in new)),
                              ("Review status", (r.proposal.status for r in new)),
                              ("Capability", (r.capability for r in new))):
            summary.extend({"Group": group, "Value": value, "Count": count}
                           for value, count in sorted(Counter(values).items()))
        st.dataframe(summary, hide_index=True, width="stretch")

        live = st.session_state.get(key + ":live", {})
        cols = st.columns(2)
        author = cols[0].selectbox("Author", sorted({r.author for r in new}), index=None, key=key + ":author")
        book = cols[1].selectbox("Book", sorted({r.title for r in new}), index=None, key=key + ":book")
        field = cols[0].selectbox("Field", sorted({r.proposal.field for r in new}), index=None, key=key + ":field")
        confidence = cols[1].selectbox("Confidence", sorted({r.proposal.confidence for r in new}),
                                      index=None, key=key + ":confidence")
        state = st.selectbox("Disposition", sorted({r.disposition for r in new}), index=None, key=key + ":state")
        visible = sorted((r for r in new if (author is None or author == r.author)
                          and (book is None or book == r.title)
                          and (field is None or field == r.proposal.field)
                          and (confidence is None or confidence == r.proposal.confidence)
                          and (state is None or state == r.disposition)), key=_label)
        normal = {READY, HELD, ALREADY_APPLIED, ALREADY_STAGED, "No change",
                  "Native edit outstanding", "Editorial review outstanding"}
        conflicts = [r for r in visible if r.disposition not in normal]
        st.dataframe(_table([r for r in visible if r.disposition in normal], live), hide_index=True, width="stretch")
        if conflicts:
            st.write(f"Conflicts and unavailable proposals ({len(conflicts)})")
            st.dataframe(_table(conflicts, live), hide_index=True, width="stretch")
        details = st.selectbox("Recommendation details", range(len(visible)), index=None,
                               format_func=lambda i: _label(visible[i]), key=key + ":details:" + compact(
                                   [author, book, field, confidence, state]))
        if details is not None:
            p = visible[details].proposal
            st.text(p.rationale)
            st.json({"expected": p.expected, "saved_current": visible[details].current,
                     "desired": p.desired, "evidence": p.evidence,
                     "declared_write_path": p.write_path}, expanded=False)

        if prior:
            st.write("Previously staged in the source export")
            st.dataframe(_table(prior, live), hide_index=True, width="stretch")

        native = [r for r in new if r.capability != "Guarded ABS"
                  and r.disposition not in (ALREADY_APPLIED, ALREADY_STAGED, "No change")]
        if native:
            checklist = [{"item_id": r.proposal.item_id, "author": r.author, "book": r.title,
                          "authority": r.capability, "field": NATIVE_FIELDS.get(r.proposal.field, r.proposal.field),
                          "expected_original_value": r.proposal.expected, "saved_current_value": r.current,
                          "complete_desired_value": r.proposal.desired,
                          "review_status": r.proposal.status, "disposition": r.disposition,
                          "rationale": r.proposal.rationale, "confidence": r.proposal.confidence,
                          "evidence": r.proposal.evidence} for r in sorted(native, key=_label)]
            st.download_button(f"Download native/manual review checklist ({len(native)})",
                               compact({"source_snapshot": source_id, "genre_policy": document.genre_policy,
                                        "outstanding": checklist}), file_name="native-metadata-checklist.json",
                               mime="application/json", on_click="ignore", key=key + ":download")

        held = {r.proposal.id: r for r in new if r.disposition == HELD}
        confirmed = st.multiselect("Confirm recording for these narrator changes", list(held),
                                   format_func=lambda pid: _label(held[pid]), key=key + ":held",
                                   help="Select a book only after confirming its recording supports the exact desired narrator credits.") if held else []
        eligible = {r.proposal.id: r for r in new
                    if r.disposition == READY or r.disposition == HELD and r.proposal.id in confirmed}
        selection_key = key + ":selected"
        shown = [r.proposal.id for r in visible if r.proposal.id in eligible]
        chosen = [pid for pid in st.session_state.get(selection_key, []) if pid in eligible]
        cols = st.columns(2)
        if cols[0].button("Select eligible rows in view", disabled=not shown, key=key + ":all"):
            chosen = list(dict.fromkeys(chosen + shown))
        if cols[1].button("Clear rows in view", disabled=not shown, key=key + ":none"):
            chosen = [pid for pid in chosen if pid not in shown]
        if st.session_state.get(selection_key) != chosen:
            st.session_state[selection_key] = chosen
        selected = st.multiselect("Fields selected for staging", list(eligible),
                                  format_func=lambda pid: _label(eligible[pid]), key=selection_key)
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
                st.session_state[key + ":notice"] = f"Staged {count} fields locally. Review them in Changes."
                st.rerun()
        except (ValueError, BusyError) as exc:
            st.error(str(exc))
        except AdapterError:
            st.error("The ABS read failed. Check the connection and try again.")
