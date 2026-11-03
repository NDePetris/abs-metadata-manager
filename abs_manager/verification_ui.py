"""Compact native review. Draft state is separate from disposable widget state."""
import copy
import os

import streamlit as st

from . import preferences
from .adapter import AdapterError, PublicMetadataTransport
from .diagnostics import provider_failure
from .locking import BusyError, operation_lock
from .models import digest, review_fields
from .sources import RECORDING_PROVIDERS, AbsSource, PublicSource
from .verification import can_copy_reference, contributor_comparison, reference_values
from .verification_workflow import run_verification, start_verification


def source_for(source_id, adapter):
    if source_id.startswith("abs:"):
        if adapter is None:
            raise ValueError("Connect to the original server first.")
        return AbsSource(adapter, source_id[4:])
    return PublicSource(source_id, PublicMetadataTransport(os.getenv("GOOGLE_BOOKS_API_KEY", "")))


def discover_providers(store, adapter):
    """Called exclusively by explicit connection/refresh actions."""
    previous = st.session_state.get("provider_discovery", {})
    state = {"server": adapter.server, "status": "available",
             "providers": previous.get("providers", []) if previous.get("server") == adapter.server else []}
    try:
        with operation_lock(store.path):
            state["providers"] = adapter.metadata_providers()
    except (AdapterError, ValueError, KeyError, TypeError) as exc:
        state.update(status="failed", diagnostic=provider_failure(exc, "ABS", "metadata providers"))
    st.session_state["provider_discovery"] = state
    st.session_state["providers"] = state["providers"]
    return state


def source_label(source):
    if source.startswith("abs:"):
        provider = source[4:]
        found = next((p for p in st.session_state.get("providers", []) if p["value"] == provider), {})
        name = found.get("text") or provider.title()
        return f"ABS · {name} · " + ("recording" if provider in RECORDING_PROVIDERS else "bibliographic")
    return {"openlibrary": "Open Library · direct · bibliographic",
            "googlebooks": "Google Books · direct · bibliographic"}.get(source, source)


def display(value):
    if value is None:
        return "Not set"
    if value == "" or value == [] or value == {}:
        return "Empty"
    if isinstance(value, list):
        return "; ".join(str(v) if not isinstance(v, dict) else
                         v.get("name", "") + (" · " + str(v["sequence"]) if v.get("sequence") is not None else "")
                         for v in value)
    if isinstance(value, dict):
        return " · ".join(f"{k.upper()}: {v}" for k, v in value.items())
    return str(value)


def draft_scope(audit, item):
    return "review:" + digest([audit["server"], audit["library"], audit["id"], item["item_id"]])


def set_draft(scope, field, value):
    state = st.session_state[scope]
    state["targets"][field] = copy.deepcopy(value(state["targets"][field]) if callable(value) else value)
    state["selected"][field] = True


def text_control(label, value, key, changed=None, *, long=False):
    # Widget keys can be removed by Streamlit on navigation. Rehydrate from durable session drafts.
    if changed or key not in st.session_state:
        st.session_state[key] = value
    return (st.text_area if long else st.text_input)(label, key=key, label_visibility="collapsed",
                                                   placeholder=label, on_change=changed,
                                                   **({"height": 100} if long else {}))


def editor(field, value, key, changed=None):
    """Small typed editors, shared with Changes; never serialize names with delimiters."""
    if changed is None and field in ("authors", "narrators", "series"):
        rows = value if field == "series" else [{"name": name} for name in value]
        edited = st.data_editor(rows or [{"name": "", **({"sequence": None} if field == "series" else {})}],
                                num_rows="dynamic", key=key,
                                column_config={"name": st.column_config.TextColumn("Name"),
                                               "sequence": st.column_config.TextColumn("Position")})
        return ([{"name": r["name"], "sequence": r.get("sequence") or None} for r in edited if r.get("name")]
                if field == "series" else [r["name"] for r in edited if r.get("name")])
    if field in ("title", "subtitle"):
        def update():
            changed(st.session_state[key])
        text = text_control("Desired " + field, value or "", key, update if changed else None,
                            long=len(value or "") > 95 or "\n" in (value or ""))
        return value if text == (value or "") else text
    if field == "identifiers":
        result = dict(value)
        for name in ("isbn", "asin"):
            def update(name=name):
                text = st.session_state[key + name]
                changed(lambda current: {**current, name: text})
            result[name] = text_control("Desired " + name, value.get(name, ""), key + name,
                                        update if changed else None)
        return value if all(result[k] == value.get(k, "") for k in result) else result
    if field in ("authors", "narrators"):
        rows = list(value)
        if len(rows) <= 1:
            def update():
                text = st.session_state[key]
                changed([text] if text else [])
            text = text_control("Desired " + field, rows[0] if rows else "", key, update if changed else None)
            result = [text] if text else []
            return result
        with st.popover(f"{len(rows)} names · Edit", width="stretch"):
            result = []
            for i, name in enumerate(rows):
                def update(i=i):
                    text = st.session_state[key + str(i)]
                    changed(lambda current: [text if j == i else name for j, name in enumerate(current)])
                result.append(text_control(f"Desired {field} {i + 1}", name, key + str(i), update if changed else None))
                if changed:
                    a, b = st.columns(2)
                    if a.button("Move up", key=key + f":up:{i}", disabled=i == 0):
                        rows[i - 1], rows[i] = name, rows[i - 1]
                        changed(rows)
                        st.rerun()
                    if b.button("Remove", key=key + f":remove:{i}"):
                        changed(rows[:i] + rows[i + 1:])
                        st.rerun()
            if changed and st.button("Add " + field, key=key + ":add"):
                changed(rows + [""])
                st.rerun()
        return result
    rows = copy.deepcopy(value)

    def series_rows():
        result = []
        for i, row in enumerate(rows or [{"name": "", "sequence": None}]):
            name_col, pos_col = st.columns([3, 1], gap="small")
            entry = {}
            for prop, col in (("name", name_col), ("sequence", pos_col)):
                subkey = key + f":{i}:{prop}"
                def update(i=i, prop=prop, subkey=subkey):
                    text = st.session_state[subkey] or (None if prop == "sequence" else "")
                    changed(lambda current: [{**r, prop: text} if j == i else r for j, r in
                                            enumerate(current or [{"name": "", "sequence": None}])])
                with col:
                    text = text_control("Series " + ("position" if prop == "sequence" else "name"),
                                        row.get(prop) or "", subkey, update if changed else None)
                entry[prop] = text or (None if prop == "sequence" else "")
            result.append(entry)
            if changed and len(rows) > 1 and st.button("Remove series", key=key + f":remove:{i}"):
                changed(rows[:i] + rows[i + 1:])
                st.rerun()
        if changed and st.button("Add series", key=key + ":add"):
            changed(rows + [{"name": "", "sequence": None}])
            st.rerun()
        return result

    if len(rows) > 1:
        with st.popover(f"{len(rows)} series · Edit", width="stretch"):
            result = series_rows()
    else:
        # Compact ordinary name/decimal position, additional rows disclosed on demand.
        name_col, pos_col = st.columns([3, 1], gap="small")
        row = (rows or [{"name": "", "sequence": None}])[0]
        result = [dict(row)]
        for prop, col in (("name", name_col), ("sequence", pos_col)):
            subkey = key + ":" + prop
            def update(prop=prop, subkey=subkey):
                text = st.session_state[subkey] or (None if prop == "sequence" else "")

                def apply(current):
                    updated = {**(current[0] if current else {"name": "", "sequence": None}), prop: text}
                    return [updated] if updated["name"] or updated["sequence"] is not None else []

                changed(apply)
            with col:
                text = text_control("Series " + ("position" if prop == "sequence" else "name"), row.get(prop) or "",
                                    subkey, update if changed else None)
            result[0][prop] = text or (None if prop == "sequence" else "")
    return result if any(r["name"] or r["sequence"] is not None for r in result) else []


def reference_area(store, audit, item, scope):
    aid, iid = audit["id"], item["item_id"]
    matches = [m for m in item["matches"] if not m.get("rejected")]
    match = None
    if matches:
        ids = [m["candidate_id"] for m in matches]
        state = st.session_state[scope]
        chosen = state.get("candidate", item["selected_candidate"])
        if chosen not in ids:
            chosen = ids[0]
        st.session_state[scope + ":candidate"] = chosen

        def choose_candidate():
            state["candidate"] = st.session_state[scope + ":candidate"]

        edition, summary_col = st.columns([3, 5], gap="small", vertical_alignment="center")
        cid = edition.selectbox("Reference candidate", ids, key=scope + ":candidate", on_change=choose_candidate,
                           label_visibility="collapsed", format_func=lambda c: next(
                               m["candidate"]["source"] + " — " + display(m["candidate"]["values"].get("title"))
                               for m in matches if m["candidate_id"] == c))
        match = next(m for m in matches if m["candidate_id"] == cid)
        c = match["candidate"]
        classification = {"correct_recording": "Recording evidence matches", "recording_uncertain":
                          "Title and author match; audiobook recording unconfirmed", "different_recording":
                          "Different or uncertain recording", "wrong_work": "Wrong book or contradictory work"}[match["classification"]]
        summary = classification + (" · Reference accepted by you" if item["selected_candidate"] == cid else " · Candidate only")
        details = [f"{label}: {display(c[k])}" for k, label in (("published", "Year"), ("duration", "Duration (s)")) if c.get(k) is not None]
        if c["values"].get("identifiers"):
            details.append(display(c["values"]["identifiers"]))
        summary_col.caption(summary + (" · " + " · ".join(details) if details else ""))
        if match["contradictions"]:
            st.warning("Contradictions: " + ", ".join(match["contradictions"]))
    else:
        st.caption("No reference selected. Search a source or continue manually.")
    # One compact action strip; browsing a default candidate never accepts it.
    with st.container(horizontal=True, gap="small"):
        if match:
            accept = "Accept book-level reference" if match["candidate"].get("work_only") else "This is my edition"
            for label, decision in ((accept, "selected"), ("Not my edition", "rejected"), ("Wrong book", "wrong_book")):
                if st.button(label, key=scope + label, disabled=decision == "selected" and match["classification"] == "wrong_work"):
                    store.decide_candidate(aid, iid, match["candidate_id"], decision, label)
                    st.session_state[scope]["manual"] = False
                    st.rerun()
        with st.popover("Search / Refine"):
            adapter = st.session_state.get("adapter")
            discovery = st.session_state.get("provider_discovery", {})
            if not adapter or discovery.get("server") != adapter.server:
                discovery = {}
            if st.button("Discover / Refresh ABS Providers", key=scope + ":discover", disabled=adapter is None):
                discover_providers(store, adapter)
                st.rerun()
            st.caption("ABS providers: " + discovery.get("status", "not discovered"))
            if discovery.get("diagnostic"):
                st.warning(discovery["diagnostic"]["summary"])
                if discovery.get("providers"):
                    st.caption("Showing previously discovered providers; refresh failed.")
                with st.expander("Safe discovery diagnostic"):
                    st.json(discovery["diagnostic"])
            providers = discovery.get("providers", [])
            choices = ["abs:" + p["value"] for p in providers] + ["openlibrary", "googlebooks"]
            preferred = preferences.load().get("source")
            source = st.selectbox("Source", choices, index=choices.index(preferred) if preferred in choices else 0,
                                  key=scope + ":source", format_func=source_label)
            missing_key = source == "googlebooks" and not os.getenv("GOOGLE_BOOKS_API_KEY", "")
            if missing_key:
                st.caption("Google Books requires an independent GOOGLE_BOOKS_API_KEY; not configured.")
            title = st.text_input("Refine title / ASIN or public query", key=scope + ":query")
            with st.container(horizontal=True):
                search = st.button("Search", key=scope + ":search", disabled=missing_key)
                another = st.button("Search another source", key=scope + ":another", disabled=missing_key)
            if search or another:
                st.session_state[scope]["manual"] = False
                preferences.save({**preferences.load(), "source": source})
                src = source_for(source, st.session_state.get("adapter"))
                overrides = {iid: {"title" if source.startswith("abs:") else "q": title}} if title.strip() else None
                rid = start_verification(store, aid, src, [iid], refresh=True, query_overrides=overrides)
                with st.spinner("Searching…"):
                    run_verification(store, rid, src, limit=1)
                latest = next(l for l in store.verification_evidence(aid, iid) if l["source"] == src.id)
                st.session_state[scope]["search_result"] = latest
                decisions = store.candidate_decisions(aid, iid)
                new_candidates = [c for lookup in store.verification_evidence(aid, iid)
                                  if lookup["source"] == src.id and lookup["state"] in ("ok", "partial")
                                  for c in lookup.get("candidates", []) if not (
                                      decisions.get(c["id"], {}).get("valid") and
                                      decisions[c["id"]]["decision"] in ("rejected", "wrong_book"))]
                if new_candidates:
                    st.session_state[scope]["candidate"] = new_candidates[0]["id"]
                st.rerun()
        if st.button("Continue manually", key=scope + ":manual"):
            st.session_state[scope]["manual"] = True
            st.rerun()
    result = st.session_state[scope].get("search_result") or next(iter(item.get("lookups", [])), None)
    if result:
        name = source_label(result["source"])
        if result["state"] == "unavailable":
            st.warning(name + " search failed: " + result["error"])
            with st.expander("Safe search diagnostic"):
                st.json(result.get("candidate_diagnostics") or result.get("diagnostic", {"summary": result["error"]}))
        else:
            if result["state"] == "partial":
                st.warning(f"Partial results: {len(result['candidates'])} usable candidates; "
                           f"{len(result['candidate_diagnostics'])} could not be processed. Review diagnostics before choosing.")
                with st.expander("Safe search diagnostic"):
                    st.json(result["candidate_diagnostics"])
            st.caption(name + " · " + (f"Found {len(result['candidates'])} candidates" if result["candidates"]
                                       else "No matching candidates"))
    return None if st.session_state[scope].get("manual") else match


def verification_view(store, inventory):
    with st.container(gap="xsmall"):
        audit = inventory(store)
        if not audit:
            st.info("Refresh a library in Import first.")
            return
        aid = audit["id"]
        report = store.verification_report(aid)
        if not report:
            st.info("This library is empty.")
            return
        report.sort(key=lambda r: (str(r["fields"]["title"]["value"]), r["item_id"]))
        all_ids = [r["item_id"] for r in report]
        scope_key = "book:" + digest([audit["server"], audit["library"], aid])
        accepted = sum(r["review_status"] == "Accepted" for r in report)
        previous, selector, following, filter_col = st.columns([1.25, 5, 1, 2], vertical_alignment="bottom", gap="small")
        review_filter = filter_col.selectbox("Review status", ["Needs review", "Accepted", "All books"],
                                            key=scope_key + ":filter", label_visibility="collapsed")
        queue = [r for r in report if review_filter == "All books" or
                 (r["review_status"] == "Accepted") == (review_filter == "Accepted")]
        ids = [r["item_id"] for r in queue]
        if message := st.session_state.pop("review_message", None):
            st.success(message)
        st.caption(f"{accepted} / {len(report)} accepted · {len(report) - accepted} remaining · Current = imported ABS snapshot")
        if not ids:
            st.info("All books accepted. Review complete." if review_filter == "Needs review" else "No accepted books yet.")

            def show_all():
                st.session_state[scope_key + ":filter"] = "All books"

            st.button("Show all books", on_click=show_all)
            return
        if st.session_state.get(scope_key) not in ids:
            old_index = all_ids.index(st.session_state[scope_key]) if st.session_state.get(scope_key) in all_ids else 0
            st.session_state[scope_key] = next((i for i in ids if all_ids.index(i) >= old_index), ids[0])
        index = ids.index(st.session_state[scope_key])

        def navigate(i):
            st.session_state[scope_key] = ids[i]

        previous.button("Previous", disabled=index == 0, on_click=navigate, args=(index - 1,))
        # Secondary labels distinguish duplicate titles without exposing internal IDs.
        labels = {}
        for r in report:
            label = str(r["fields"]["title"]["value"]) + " · " + display(r["fields"]["authors"]["value"])
            if sum(x["fields"]["title"]["value"] == r["fields"]["title"]["value"] for x in report) > 1:
                label += " · " + r["type"] + " · " + (r["identity"].get("relPath") or r["identity"]["path"])
            labels[r["item_id"]] = label
        widget_key = scope_key + ":widget"
        st.session_state[widget_key] = st.session_state[scope_key]

        def choose():
            st.session_state[scope_key] = st.session_state[widget_key]

        iid = selector.selectbox("Book", ids, key=widget_key, format_func=labels.get, on_change=choose,
                                 label_visibility="collapsed")
        following.button("Next", disabled=index == len(ids) - 1, on_click=navigate, args=(index + 1,))
        item = next(r for r in report if r["item_id"] == iid)
        scope = draft_scope(audit, item)
        corrections = {r["field"]: r for r in reversed(store.corrections(aid))
                       if r["item_id"] == iid and r["accepted"] and r["status"] in ("pending", "edited_in_calibre", "verified_in_calibre")}
        fields = review_fields(item["type"] == "audiobook")
        if scope not in st.session_state:
            st.session_state[scope] = {"targets": {f: copy.deepcopy(corrections.get(f, {}).get("proposed", item["fields"][f]["value"]))
                                                   for f in fields}, "selected": {f: False for f in fields}}
        state = st.session_state[scope]
        match = reference_area(store, audit, item, scope)
        reference = reference_values(match) if match else {}
        with st.container(key="comparison", gap="xsmall"):
            columns = st.columns([1.25, 2, 2, 2.7], gap="small")
            for col, label in zip(columns, ("Field", "Current in ABS", "Reference", "Desired"), strict=True):
                col.markdown("**" + label + "**")
            for field in fields:
                assessment = item["fields"][field]
                label, current, ref, desired = st.columns([1.25, 2, 2, 2.7], gap="small")
                skey = scope + ":select:" + field
                st.session_state[skey] = state["selected"][field]

                def select(field=field, skey=skey):
                    state["selected"][field] = st.session_state[skey]

                label.checkbox(field.capitalize(), key=skey, on_change=select)
                decision = item["human_decisions"].get(field, {})
                human = ("Accepted" if decision.get("decision") == "confirmed" else "Correction" if field in corrections else "Review") if decision.get("valid") else "Review"
                with current, st.container(gap=None):
                    st.write(display(assessment["value"]))
                    st.caption(assessment["status"] + " · " + human)
                with ref, st.container(gap=None):
                    st.write(display(reference[field]) if field in reference else "Not supplied")
                    if field == "narrators" and field in reference:
                        st.caption(contributor_comparison(assessment["value"], reference[field]))
                    if match and field in reference:
                        copied = (lambda current, value=reference[field]: {**current, **value}
                                  if isinstance(value, dict) else value) if field == "identifiers" else reference[field]
                        st.button("Use reference", key=scope + ":copy:" + field,
                                  help="Copy " + field + " to its draft; does not confirm or approve a write.",
                                  disabled=not can_copy_reference(item, match, field),
                                  on_click=set_draft, args=(scope, field, copied), type="tertiary")
                with desired, st.container(gap=None):
                    editor(field, state["targets"][field], scope + ":edit:" + field,
                           lambda value, field=field: set_draft(scope, field, value))
                    with st.container(horizontal=True, gap="small"):
                        st.button("Restore current", key=scope + ":restore:" + field,
                                  help="Restore imported " + field + " to the draft.", on_click=set_draft,
                                  args=(scope, field, assessment["value"]), type="tertiary")
                        if field in ("authors", "narrators", "series"):
                            with st.popover("Add", type="tertiary", help="Add an individual name or series"):
                                if st.button("Add " + field, key=scope + ":add:" + field):
                                    set_draft(scope, field, state["targets"][field] +
                                              ([{"name": "", "sequence": None}] if field == "series" else [""]))
                                    st.rerun()
        selected = [f for f in fields if state["selected"][f]]

        def select_all():
            state["selected"] = dict.fromkeys(fields, True)

        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.button("Select all", on_click=select_all)
            st.write(f"{len(selected)} selected")
            confirm_next = st.button("Confirm & Next", type="primary", disabled=not selected)
            confirm = st.button("Confirm", disabled=not selected)
            with st.popover("More review actions"):
                note = st.text_input("Note (optional)", key=scope + ":note")
                reopen = st.button("Reopen Selected", disabled=not selected)
                later = st.button("Review Later", disabled=not selected)
        if confirm or confirm_next:
            try:
                store.confirm_targets(aid, iid, {f: state["targets"][f] for f in selected}, note or "Human confirmed displayed values")
            except (ValueError, BusyError) as exc:
                st.error(str(exc))
            else:
                state["selected"] = dict.fromkeys(fields, False)
                st.session_state["review_message"] = "Confirmed locally. Corrections are in Changes; no remote write was sent."
                if confirm_next:
                    if review_filter == "Needs review":
                        remaining = {r["item_id"] for r in store.verification_report(aid) if r["review_status"] != "Accepted"}
                        following_ids = ids[index + 1:] + ids[:index + 1]
                        target = next((i for i in following_ids if i in remaining), None)
                        if target is None:
                            st.session_state["review_message"] = "All books accepted. Review complete. No remote write was sent."
                        else:
                            st.session_state[scope_key] = target
                    elif index < len(ids) - 1:
                        navigate(index + 1)
                st.rerun()
        if reopen or later:
            store.decide_fields(aid, iid, selected, "reopened" if reopen else "later", note or "Human review decision")
            st.rerun()
        with st.expander("Advanced / Evidence"):
            st.caption("Human acceptance is separate from independent verification. Snapshot: " + str(audit["finished"]))
            st.write("Desired metadata: " + ("Accepted target" if corrections else item["review_status"]))
            observed = [r for r in store.corrections(aid) if r["item_id"] == iid and r["abs_observed"]]
            verified = any(r["library"] == audit["library"] and r["item_id"] == iid and r["state"] == "verified"
                           and r["action"] == "apply" for r in store.history(audit["server"]))
            st.write("ABS metadata: " + ("Verified by readback (see History)" if verified else
                                        "Observed on import" if observed else "Snapshot only; accepted corrections not checked"))
            st.write("Embedded file metadata: Not checked · Folder structure: Not checked")
            adapter = st.session_state.get("adapter")
            if adapter and adapter.server == audit["server"] and st.button("Preview cover", key=scope + ":cover"):
                with operation_lock(store.path):
                    st.image(adapter.cover(iid), width=220)
            if state.get("manual") and st.button("Show reference again"):
                state["manual"] = False
                st.rerun()
            st.json(item)
