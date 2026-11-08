"""Compact native review. Draft state is separate from disposable widget state."""
import copy
import os
from collections import Counter

import streamlit as st

from . import preferences
from .adapter import AdapterError, PublicMetadataTransport
from .diagnostics import provider_failure
from .locking import BusyError, operation_lock
from .models import digest, review_fields
from .sources import RECORDING_PROVIDERS, AbsSource, PublicSource
from .verification import can_copy_reference, contributor_comparison, reference_values
from .verification_workflow import (
    MAX_BATCH,
    evidence_coverage,
    retry_unavailable,
    run_coverage,
    run_verification,
    start_verification,
    unassessed_batch,
)

ESSENTIAL = ("title", "authors", "narrators", "series", "series_position")


def quality_queue(report, disposition="Exceptions", search="", field=None, reason=None):
    """One row per book, essential defects first; no persistence or transport."""
    def include(r):
        exceptions = {f: d for f, d in r["fields"].items() if d["disposition"] != "Pass"}
        return ((disposition == "All books" or disposition == "Exceptions" and exceptions
                 or r["disposition"] == disposition)
                and (not field or field in exceptions)
                and (not reason or any(d["reason_code"] == reason for d in exceptions.values()))
                and search.casefold() in (display(r["fields"]["title"]["value"]) + " " +
                                         display(r["fields"]["authors"]["value"])).casefold())

    def priority(r):
        return (0 if disposition == "All books" else ("Needs attention", "Needs evidence", "Pass").index(r["disposition"]),
                0 if disposition == "All books" else -sum(r["fields"][f]["disposition"] == "Needs attention" for f in ESSENTIAL),
                str(r["fields"]["title"]["value"]).casefold(), r["item_id"])

    return sorted((r for r in report if include(r)), key=priority)


def quality_explanation(details):
    basis = {"independently_verified": "Independently verified; manual confirmation unnecessary",
             "calibre_authority": "Calibre authority; manual confirmation unnecessary",
             "human_confirmed": "Human confirmed", "correction_outstanding": "Correction staged but not observed"}
    return basis.get(details["reason_code"], details["reason_code"].replace("_", " ").capitalize())


def library_review_controls(store, audit, report):
    from .library_review import category_inventory

    aid = audit["id"]
    key = "library-review:" + digest([store.path, audit["server"], audit["library"], aid])
    labels = {r["item_id"]: display(r["fields"]["title"]["value"]) + " · " +
              (r["identity"].get("relPath") or r["identity"]["path"]) for r in report}
    label_counts = Counter(labels.values())
    labels = {iid: label + (" · " + iid if label_counts[label] > 1 else "") for iid, label in labels.items()}
    with st.expander("Cover gallery · explicit visual review"):
        st.caption("Saved ABS artwork may depict any appropriate edition of the correct work. "
                   "Ebook source artwork is maintained in Calibre; this reviews the ABS displayed image.")
        st.caption("Correct artwork in native ABS (open the book → Edit → Cover), or Calibre for ebooks. "
                   "Refresh Import and retrieve again after editing. Same-path image changes require a new retrieval.")
        choices = sorted(report, key=lambda r: (r["fields"]["cover"]["disposition"] == "Pass",
                                                bool(r["fields"]["cover"]["value"]), labels[r["item_id"]]))
        selected = st.multiselect("Covers to retrieve (maximum 12)", [r["item_id"] for r in choices],
                                  format_func=labels.get, max_selections=12, key=key + ":covers")
        adapter = st.session_state.get("adapter")
        connected = adapter is not None and adapter.server == audit["server"]
        if st.button("Retrieve selected covers", disabled=not selected or not connected):
            gallery = {}
            try:
                for iid in selected:
                    data, observation = store.observe_cover(aid, iid, adapter)
                    gallery[iid] = {"data": data, **observation}
            except (ValueError, BusyError) as exc:
                st.error(str(exc))
            st.session_state[key + ":gallery"] = gallery
            st.rerun()
        gallery = st.session_state.get(key + ":gallery", {})
        if gallery:
            with st.form(key + ":inspection"):
                inspected = []
                columns = st.columns(3)
                for index, (iid, observation) in enumerate(gallery.items()):
                    with columns[index % 3]:
                        st.write(labels[iid])
                        if observation["data"]:
                            st.image(observation["data"], width=180)
                            if st.checkbox("I inspected this artwork; appropriate for this work",
                                           key=key + ":inspect:" + iid + ":" + observation["content_digest"]):
                                inspected.append(iid)
                        else:
                            st.warning("Saved cover missing, unavailable or changed. Refresh Import / correct in native app.")
                if st.form_submit_button("Confirm inspected covers"):
                    try:
                        for iid in inspected:
                            store.confirm_cover(aid, iid, gallery[iid]["content_digest"])
                    except (ValueError, BusyError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["review_message"] = f"{len(inspected)} inspected covers confirmed locally."
                        st.session_state.pop(key + ":gallery", None)
                        st.rerun()
    with st.expander("Genre and tag hygiene · library scope"):
        inventory = category_inventory(report, store.category_preferences(aid))
        st.caption(f"Scope: {audit['name']} · {len(report)} books. ABS genres and ABS tags are distinct; "
                   "empty categories are allowed. Preferences are not independent bibliographic verification.")
        if not inventory:
            st.write("No saved genres or tags; no categorization cleanup required.")
        else:
            st.dataframe([{k: v for k, v in r.items() if k != "books"} for r in inventory], hide_index=True)
            options = list(range(len(inventory)))
            chosen = st.selectbox("Category", options,
                                  format_func=lambda i: inventory[i]["field"] + " · " + inventory[i]["value"], key=key + ":category")
            row = inventory[chosen]
            st.write(f"{row['count']} impacted books · {row['decision']}")
            st.dataframe([{"Book": labels[iid]} for iid in row["books"]], hide_index=True)
            with st.form(key + ":category-decision:" + digest([row["field"], row["value"]])):
                options = ["Acceptable", "Unwanted", "Unreviewed"]
                decision = st.selectbox("Library preference", options, index=options.index(row["decision"]))
                st.caption("Applies to this exact saved value across this server/library only. "
                           "Correct unwanted values, duplicates or capitalization in native ABS; use Calibre for ebook source metadata. "
                           "No rename, deletion or remote write is performed.")
                if st.form_submit_button("Save category preference"):
                    try:
                        store.decide_category(aid, row["field"], row["value"], decision)
                    except (ValueError, BusyError) as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()


def acquisition_controls(store, audit, report, selected_id):
    """Only button branches call providers; rendering reads existing checkpoints."""
    adapter = st.session_state.get("adapter")
    discovery = st.session_state.get("provider_discovery", {})
    providers = discovery.get("providers", []) if adapter and discovery.get("server") == adapter.server else []
    choices = ["abs:" + p["value"] for p in providers] + ["openlibrary", "googlebooks"]
    source_id = st.selectbox("Batch evidence source", choices, format_func=source_label)
    st.json(evidence_coverage(report, source_id), expanded=False)
    st.caption("Counts describe captured evidence, not quality passes. Ebooks use existing Calibre comparisons. "
               "Requests run sequentially with transport pacing; nothing resumes automatically.")
    limit = int(st.number_input("Books per invocation", min_value=1, max_value=MAX_BATCH, value=10))
    ids = [r["item_id"] for r in report if r["type"] == "audiobook"]
    selected = st.multiselect("Selected audiobooks", ids, default=[selected_id] if selected_id in ids else [],
                             format_func=lambda iid: next(str(r["fields"]["title"]["value"]) for r in report if r["item_id"] == iid))
    refresh = st.checkbox("Refresh selected evidence (ignore successful cache)")
    runs = [r for r in store.verification_runs(audit["id"]) if r["source"] == source_id]
    pending = [r for r in runs if r["state"] != "complete"]
    run = st.selectbox("Saved evidence run", [r["id"] for r in runs],
                       format_func=lambda rid: next(f"Run {rid}: {r['state']} · {r['done']}/{r['total']} attempted"
                                                   for r in runs if r["id"] == rid)) if runs else None
    if run:
        st.write(run_coverage(store, run))
    missing_key = source_id == "googlebooks" and not os.getenv("GOOGLE_BOOKS_API_KEY", "")
    with st.container(horizontal=True):
        start = st.button("Verify selected batch", disabled=not selected or missing_key)
        unassessed = st.button("Verify unassessed batch", disabled=source_id == "openlibrary" or missing_key)
        resume = st.button("Resume evidence run", disabled=run not in [r["id"] for r in pending] or missing_key)
        retry = st.button("Retry unavailable evidence", disabled=run is None or missing_key)
        cancel = st.button("Cancel evidence run", disabled=run not in [r["id"] for r in pending])
    if cancel:
        store.cancel_verification(run)
        st.rerun()
    if start or unassessed or resume or retry:
        try:
            source = source_for(source_id, adapter)
            if start or unassessed:
                batch = unassessed_batch(report, source_id, limit) if unassessed else selected[:limit]
                if not batch:
                    st.info("No unattempted audiobooks for this source. Use selected refresh or explicit retry.")
                    return
                run = start_verification(store, audit["id"], source, batch, refresh=refresh if start else False)
            elif retry:
                retry_unavailable(store, run)
            with st.spinner("Gathering bounded read-only evidence…"):
                run_verification(store, run, source, limit=limit)
            st.rerun()
        except (ValueError, AdapterError, BusyError) as exc:
            st.error(str(exc))


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
            st.info("Refresh a library in Library first.")
            return
        aid = audit["id"]
        report = store.verification_report(aid)
        if not report:
            st.info("This library is empty.")
            return
        report = quality_queue(report, "All books")
        all_ids = [r["item_id"] for r in report]
        scope_key = "book:" + digest([audit["server"], audit["library"], aid])
        accepted = sum(r["review_status"] == "Accepted" for r in report)
        counts = Counter(r["disposition"] for r in report)
        st.write(" · ".join(f"{d}: {counts[d]}" for d in ("Pass", "Needs attention", "Needs evidence")))
        st.caption(f"Historical manual acceptance: {accepted} / {len(report)} books · Current = imported ABS snapshot")
        with st.expander("Queue filters"):
            search = st.text_input("Search title or author", key=scope_key + ":search")
            field_filter = st.selectbox("Affected field", ["Any"] + list(report[0]["fields"]), key=scope_key + ":field")
            reasons = sorted({d["reason_code"] for r in report for d in r["fields"].values() if d["disposition"] != "Pass"})
            reason_filter = st.selectbox("Reason", ["Any"] + reasons, key=scope_key + ":reason")
        library_review_controls(store, audit, report)
        from .correction_plan_ui import plan_controls
        plan_controls(store, audit, report)
        previous, selector, following, filter_col = st.columns([1.25, 5, 1, 2], vertical_alignment="bottom", gap="small")
        review_filter = filter_col.selectbox("Quality", ["Exceptions", "Needs attention", "Needs evidence", "Pass", "All books"],
                                            key=scope_key + ":filter", label_visibility="collapsed")
        queue = quality_queue(report, review_filter, search, None if field_filter == "Any" else field_filter,
                              None if reason_filter == "Any" else reason_filter)
        ids = [r["item_id"] for r in queue]
        if message := st.session_state.pop("review_message", None):
            st.success(message)
        if not ids:
            st.info("No quality exceptions remain." if counts["Pass"] == len(report) else "No books match these filters.")

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
        title_counts = Counter(str(r["fields"]["title"]["value"]) for r in report)
        for r in report:
            label = str(r["fields"]["title"]["value"]) + " · " + display(r["fields"]["authors"]["value"])
            if title_counts[str(r["fields"]["title"]["value"])] > 1:
                label += " · " + r["type"] + " · " + (r["identity"].get("relPath") or r["identity"]["path"])
            labels[r["item_id"]] = r["disposition"] + " · " + label
        widget_key = scope_key + ":widget"
        st.session_state[widget_key] = st.session_state[scope_key]

        def choose():
            st.session_state[scope_key] = st.session_state[widget_key]

        iid = selector.selectbox("Book", ids, key=widget_key, format_func=labels.get, on_change=choose,
                                 label_visibility="collapsed")
        following.button("Next", disabled=index == len(ids) - 1, on_click=navigate, args=(index + 1,))
        item = next(r for r in report if r["item_id"] == iid)
        essential = [item["fields"][f] for f in ESSENTIAL if item["fields"][f]["quality_applicable"]
                     and (f != "series" or item["fields"][f]["value"])]
        st.caption(f"Essential bibliographic fields: {sum(d['disposition'] == 'Pass' for d in essential)} / "
                   f"{len(essential)} pass · Artwork: {item['fields']['cover']['disposition']} · "
                   f"Book: {item['disposition']}")
        with st.expander("Quality reasons and provenance"):
            for field, details in item["fields"].items():
                st.write(f"{field.capitalize()} · {details['disposition']} · {quality_explanation(details)}")
                st.caption(details["reason"])
                for provenance in details["provenance"]:
                    st.caption(display(provenance))
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
                with current, st.container(gap=None):
                    st.write(display(assessment["value"]))
                    st.caption(assessment["disposition"] + " · " + quality_explanation(assessment))
                    sources = list(dict.fromkeys(p["source"] for p in assessment["provenance"]))
                    if sources:
                        st.caption("Source: " + ", ".join(sources))
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
                    if review_filter == "Exceptions":
                        remaining = {r["item_id"] for r in store.verification_report(aid) if r["disposition"] != "Pass"}
                        following_ids = ids[index + 1:] + ids[:index + 1]
                        target = next((i for i in following_ids if i in remaining), None)
                        if target is None:
                            st.session_state["review_message"] = "No quality exceptions remain. No remote write was sent."
                        else:
                            st.session_state[scope_key] = target
                    elif index < len(ids) - 1:
                        navigate(index + 1)
                st.rerun()
        if reopen or later:
            store.decide_fields(aid, iid, selected, "reopened" if reopen else "later", note or "Human review decision")
            st.rerun()
        with st.expander("Advanced / Evidence"):
            with st.expander("Read-only batch evidence"):
                acquisition_controls(store, audit, report, iid)
            st.caption("Human acceptance is separate from independent verification. Snapshot: " + str(audit["finished"]))
            st.write("Desired metadata: " + ("Accepted target" if corrections else item["review_status"]))
            observed = [r for r in store.corrections(aid) if r["item_id"] == iid and r["abs_observed"]]
            verified = any(r["library"] == audit["library"] and r["item_id"] == iid and r["state"] == "verified"
                           and r["action"] == "apply" for r in store.history(audit["server"]))
            st.write("ABS metadata: " + ("Verified by readback (see History)" if verified else
                                        "Observed on import" if observed else "Snapshot only; accepted corrections not checked"))
            st.write("Embedded file metadata: Not checked · Folder structure: Not checked")
            st.caption("Use Cover gallery above to retrieve, inspect and confirm saved artwork.")
            if state.get("manual") and st.button("Show reference again"):
                state["manual"] = False
                st.rerun()
            st.json(item)
