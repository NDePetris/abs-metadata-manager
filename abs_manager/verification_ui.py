"""Book-focused native review; all external requests require explicit submission."""
import os

import streamlit as st

from . import preferences
from .adapter import PublicMetadataTransport
from .locking import operation_lock
from .models import FIELDS
from .sources import AbsSource, PublicSource
from .verification_workflow import run_verification, start_verification


def source_for(source_id, adapter):
    if source_id.startswith("abs:"):
        if adapter is None:
            raise ValueError("Connect to the original server first.")
        return AbsSource(adapter, source_id[4:])
    return PublicSource(source_id, PublicMetadataTransport(os.getenv("GOOGLE_BOOKS_API_KEY", "")))


def display(value):
    if isinstance(value, list):
        return "\n".join(str(v) if not isinstance(v, dict) else
                         f"{v.get('name', '')} · {v.get('sequence', '')}" for v in value) or "—"
    return str(value) if value is not None else "—"


def editor(field, value, key):
    if field in ("title", "subtitle"):
        text = (st.text_area if field == "title" else st.text_input)("Desired " + field, value=value or "", key=key)
        return None if value is None and not text else text
    if field == "identifiers":
        return {k: st.text_input("Desired " + k, value=value.get(k, ""), key=key + k) for k in ("isbn", "asin")}
    if field == "series":
        rows = st.data_editor(value or [{"name": "", "sequence": None}], num_rows="dynamic", key=key,
                              column_config={"sequence": st.column_config.TextColumn("Position")})
        return [{"name": r["name"], "sequence": r.get("sequence") or None} for r in rows if r.get("name")]
    text = st.text_area("Desired " + field + " (one name per line)", value="\n".join(value), key=key)
    return [name.strip() for name in text.splitlines() if name.strip()]


def verification_view(store, inventory):
    st.title("Match & Verify")
    audit = inventory(store)
    if not audit:
        st.info("Refresh a library in Import first.")
        return
    aid = audit["id"]
    report = store.verification_report(aid)
    if not report:
        st.info("This library is empty.")
        return
    st.caption(f"{len(report)} books · {sum(r['review_status'] == 'Accepted' for r in report)} accepted")
    query = st.text_input("Search books").casefold()
    report.sort(key=lambda r: (r["review_status"] == "Accepted", str(r["fields"]["title"]["value"])))
    visible = [r for r in report if query in str(r["fields"]["title"]["value"]).casefold()]
    if not visible:
        st.info("No matching books.")
        return
    ids = [r["item_id"] for r in visible]
    scope = f"{audit['server']}:{audit['library']}"
    key = "book:" + scope
    if st.session_state.get(key) not in ids:
        st.session_state[key] = ids[0]
    previous, selector, following = st.columns([2, 5, 2])
    index = ids.index(st.session_state[key])
    if previous.button("Previous", disabled=index == 0):
        st.session_state[key] = ids[index - 1]
    if following.button("Next", disabled=index == len(ids) - 1):
        st.session_state[key] = ids[index + 1]
    iid = selector.selectbox("Book", ids, key=key, format_func=lambda i: next(
        f"{r['fields']['title']['value']} · {i[:12]}" for r in visible if r["item_id"] == i))
    item = next(r for r in report if r["item_id"] == iid)
    adapter = st.session_state.get("adapter")
    connected = adapter is not None and adapter.server == audit["server"]
    with st.expander("Search reference sources"), st.form("search:" + scope):
        providers = st.session_state.get("providers", [])
        choices = ["abs:" + p["value"] for p in providers] + ["openlibrary", "googlebooks"]
        preferred = preferences.load().get("source")
        source = st.selectbox("Source", choices, index=choices.index(preferred) if preferred in choices else 0)
        title = st.text_input("Refine title / ASIN or public query")
        search = st.form_submit_button("Search")
        another = st.form_submit_button("Search another source")
    if search or another:
        preferences.save({**preferences.load(), "source": source})
        src = source_for(source, adapter)
        overrides = {iid: {"title" if source.startswith("abs:") else "q": title}} if title.strip() else None
        rid = start_verification(store, aid, src, [iid], refresh=True, query_overrides=overrides)
        with st.spinner("Searching…"):
            run_verification(store, rid, src, limit=1)
        st.rerun()
    for lookup in item["lookups"]:
        st.caption(f"{lookup['source']} · {lookup['state']} · {lookup.get('captured', '')}")
    matches = [m for m in item["matches"] if not m.get("rejected")]
    reference = {}
    if matches:
        cid = st.selectbox("Reference edition", [m["candidate_id"] for m in matches], format_func=lambda c: next(
            f"{m['candidate']['source']} · {display(m['candidate']['values'].get('narrators', []))} · {c[:8]}"
            for m in matches if m["candidate_id"] == c))
        match = next(m for m in matches if m["candidate_id"] == cid)
        reference = match["candidate"]["values"]
        st.write({"Edition": match["classification"], "Evidence": match["signals"],
                  "Contradictions": match["contradictions"], "Duration": match["candidate"].get("duration"),
                  "Identifiers": reference.get("identifiers")})
        with st.form(f"edition:{aid}:{iid}"):
            reason = st.text_input("Edition evidence")
            actions = {"This is my edition": "selected", "Not my edition": "rejected", "Wrong book": "wrong_book"}
            for label, decision in actions.items():
                if st.form_submit_button(label):
                    store.decide_candidate(aid, iid, cid, decision, reason or label)
                    st.rerun()
    st.caption("Continue manually at any time. Reference values are evidence; confirmation saves your desired values locally.")
    corrections = {r["field"]: r for r in reversed(store.corrections(aid))
                   if r["item_id"] == iid and r["accepted"] and r["status"] in ("pending", "edited_in_calibre", "verified_in_calibre")}
    with st.form(f"fields:{aid}:{iid}"):
        targets, selected = {}, []
        single = None
        use_reference = None
        fields_shown = FIELDS if item["type"] == "audiobook" else (*FIELDS, "identifiers")
        for field in fields_shown:
            assessment = item["fields"][field]
            decision = item["human_decisions"].get(field, {})
            st.write("**" + field.capitalize() + "**")
            saved, desired = st.columns(2)
            saved.write("**Saved value**")
            saved.text(display(assessment["value"]))
            with desired:
                draft_key = f"draft:{aid}:{iid}:{field}"
                targets[field] = editor(field, st.session_state.get(draft_key, corrections.get(field, {}).get("proposed", assessment["value"])),
                                        f"edit:{aid}:{iid}:{field}:" + str(st.session_state.get(draft_key + ":version", 0)))
            st.caption(f"Independent: {assessment['status']} · Human: {decision.get('decision', 'Needs review')}"
                       + (" · Correction pending" if field in corrections else ""))
            if field in reference:
                st.caption("Reference: " + display(reference[field]))
                eligible = (match["classification"] != "wrong_work" and (field in ("title", "authors")
                            or item["type"] == "ebook" or match["level"] == "recording"
                            or item["selected_candidate"] == match["candidate_id"] and not match["candidate"].get("work_only")))
                if st.form_submit_button("Use reference " + field, disabled=not eligible):
                    use_reference = field
            with st.expander("Evidence for " + field):
                st.write(assessment["reason"])
                for suggestion in assessment.get("local_findings", []):
                    st.write({"Suggestion": suggestion["proposed"], "Reason": suggestion["reason"]})
            if st.checkbox("Select " + field):
                selected.append(field)
            if st.form_submit_button("Confirm " + field):
                single = field
        note = st.text_input("Evidence / note (optional)")
        confirm = st.form_submit_button("Confirm selected fields")
        all_fields = st.form_submit_button("Confirm all displayed fields")
        reopen = st.form_submit_button("Reopen selected fields")
        later = st.form_submit_button("Review selected fields later")
    if reopen or later:
        store.decide_fields(aid, iid, selected, "reopened" if reopen else "later", note or "Human review decision")
        st.rerun()
    if use_reference:
        draft_key = f"draft:{aid}:{iid}:{use_reference}"
        st.session_state[draft_key] = reference[use_reference]
        st.session_state[draft_key + ":version"] = st.session_state.get(draft_key + ":version", 0) + 1
        st.rerun()
    if single or confirm or all_fields:
        fields = [single] if single else list(fields_shown) if all_fields else selected
        store.confirm_targets(aid, iid, {f: targets[f] for f in fields}, note or "Human confirmed displayed values")
        st.success("Confirmed. Edited values are staged in Changes; no remote write was sent.")
    with st.expander("Cover, chapters, file and folder evidence"):
        st.write("File metadata: Not checked · Folder verification: Not checked")
        st.json({f: item["fields"][f] for f in ("cover", "chapters", "embedded")})
        st.json(item["folder"])
        if connected and st.button("Preview cover"):
            with operation_lock(store.path):
                st.image(adapter.cover(iid), width=220)
    with st.expander("Advanced evidence and rejected editions"):
        st.json(item)
