"""Native desktop verification workspace. Network actions only in submit branches."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import streamlit as st

from .adapter import PublicMetadataTransport
from .calibre import import_catalog
from .locking import operation_lock
from .models import FIELDS
from .sources import AbsSource, PublicSource
from .verification import QUALITY_FIELDS, STATUSES, export_csv
from .verification_workflow import (
    propose_discrepancy,
    retry_unavailable,
    run_verification,
    start_verification,
)


def source_for(source_id, adapter):
    if source_id.startswith("abs:"):
        if adapter is None:
            raise ValueError("Connect to the original server in Settings.")
        return AbsSource(adapter, source_id[4:])
    return PublicSource(source_id, PublicMetadataTransport(os.getenv("GOOGLE_BOOKS_API_KEY", ""),
                                                         os.getenv("METADATA_CONTACT", "")))


def verification_view(store, choose_snapshot):
    st.title("Metadata quality & verification")
    st.write("Every book in a completed snapshot is assessed. Verification is read-only; human reviews and "
             "edition selections are recorded separately from external corroboration.")
    audit = choose_snapshot(store)
    if audit is None:
        st.info("Complete an audit or load the read-only sample from Library.")
        return
    if audit["status"] != "complete":
        st.info("Finish or resume this audit from Library before verification.")
        return
    aid = audit["id"]
    adapter = st.session_state.get("adapter")
    connected = adapter is not None and adapter.server == audit["server"]
    report = store.verification_report(aid)
    if not report:
        st.info("This completed snapshot contains no books.")
        return
    by_id = {r["item_id"]: r for r in report}
    title = lambda key: by_id[key]["fields"]["title"]["value"] or key
    st.write(f"**{len(report)} books** · {sum(r['type'] == 'audiobook' for r in report)} audiobooks · "
             f"{sum(r['type'] == 'ebook' for r in report)} ebooks")
    st.dataframe([{"Field": field, **{status: sum(b["fields"][field]["status"] == status for b in report)
                                             for status in STATUSES}} for field in QUALITY_FIELDS],
                 hide_index=True, width="stretch")
    with st.expander("Coverage by evidence source"):
        st.dataframe([
            {"Evidence": "Audiobookshelf saved metadata", "Books with captured evidence": len(report),
             "Limit": "Saved values do not by themselves verify accuracy."},
            {"Evidence": "Embedded audiobook tags", "Books with captured evidence": sum(
                bool(b["fields"]["embedded"]["value"]) for b in report),
             "Limit": "Only tags exposed by ABS. Composer is not assumed narrator."},
            {"Evidence": "External bibliographic / recording", "Books with captured evidence": sum(
                any(l["state"] == "ok" for l in b["lookups"]) for b in report),
             "Limit": "Work matches cannot corroborate recording narrators or saved cover images."},
            {"Evidence": "Cover presence", "Books with captured evidence": sum(
                bool(b["fields"]["cover"]["value"]) for b in report), "Limit": "Correctness requires visual edition review."},
            {"Evidence": "Audio chapter data", "Books with captured evidence": sum(
                bool(b["fields"]["chapters"]["value"]) and b["type"] == "audiobook" for b in report),
             "Limit": "Timing validation is local; content/name correctness needs recording evidence."},
            {"Evidence": "Calibre catalog / ebook", "Books with captured evidence": sum(
                b["type"] == "ebook" and b["calibre_limitation"] is None for b in report),
             "Limit": "Unmatched ebooks have only ABS/filesystem evidence."}], hide_index=True, width="stretch")
    with st.expander("Independent online lookups · explicit actions only"):
        st.caption("Queries send title/author or ASIN/ISBN to the selected source. ABS providers are discovered "
                   "from your connected server. Open Library is single-book only. Google Books needs your API key. "
                   "No AI service is used. Results are cached indefinitely until you explicitly refresh.")
        with st.form("discover_verification_providers"):
            discover = st.form_submit_button("Discover server metadata providers", disabled=not connected)
        if discover:
            with operation_lock(store.path):
                st.session_state["verification_providers"] = {"server": adapter.server,
                                                              "rows": adapter.metadata_providers()}
        discovery = st.session_state.get("verification_providers", {})
        providers = discovery.get("rows", []) if connected and discovery.get("server") == adapter.server else []
        choices = ["abs:" + p["value"] for p in providers] + ["openlibrary", "googlebooks"]
        with st.form(f"start_verification_{aid}"):
            source_id = st.selectbox("Lookup source", choices)
            scope = st.radio("Lookup scope", ["One book", "Selected books", "All audiobooks"])
            selected = st.multiselect("Books for lookup", list(by_id), format_func=title)
            refresh = st.checkbox("Refresh cached evidence with new requests", value=False)
            start = st.form_submit_button("Prepare online verification run")
        if start:
            ids = [b["item_id"] for b in report if b["type"] == "audiobook"] if scope == "All audiobooks" else selected
            if scope == "One book" and len(ids) != 1:
                raise ValueError("Select exactly one book.")
            start_verification(store, aid, source_for(source_id, adapter), ids, refresh=refresh)
            st.success("Run prepared locally. Start or resume it below to make read-only requests.")
        runs = store.verification_runs(aid)
        if runs:
            st.dataframe([{k: r[k] for k in ("id", "source", "state", "done", "total", "updated")}
                          for r in runs], hide_index=True, width="stretch")
            rid = st.selectbox("Verification run", [r["id"] for r in runs])
            run = next(r for r in runs if r["id"] == rid)
            st.progress(run["done"] / max(run["total"], 1), text=f"{run['done']} / {run['total']} captured")
            st.caption("Each response is saved immediately. Cancel stops pending requests at the next book boundary. "
                       "Streamlit Stop can interrupt this action; resume retains all completed records. "
                       "Use a small batch for frequent pause/cancel opportunities.")
            with st.form(f"resume_verification_{rid}"):
                batch = st.number_input("Maximum books this action", min_value=1, max_value=1000, value=10)
                retry = st.checkbox("Retry unavailable records explicitly", value=False)
                go = st.form_submit_button("Start / resume read-only lookups")
                cancel = st.form_submit_button("Cancel pending lookups")
            if cancel:
                store.cancel_verification(rid)
                st.rerun()
            if go:
                source = source_for(run["source"], adapter)
                if retry:
                    retry_unavailable(store, rid)
                bar = st.progress(0)
                run_verification(store, rid, source, limit=int(batch),
                                 progress=lambda done, total, state: bar.progress(
                                     done / max(total, 1), text=f"{done} / {total} · {state}"))
                st.rerun()
    with st.expander("Read-only Calibre catalog input"):
        st.caption('Export using: calibredb list --for-machine --fields title,authors,series,series_index,identifiers '
                   '> calibre-catalog.json. Upload that JSON from one Calibre library. Matching uses folder ID, '
                   'ISBN, or exact title plus authors; ambiguous matches remain unmatched. No Calibre database is opened.')
        with st.form(f"calibre_import_{aid}"):
            upload = st.file_uploader("Calibre JSON catalog", type=["json"])
            submit_catalog = st.form_submit_button("Compare Calibre catalog")
        if submit_catalog:
            if upload is None:
                raise ValueError("Choose an exported JSON catalog.")
            count = import_catalog(store, aid, upload.getvalue().decode("utf-8-sig"))
            st.success(f"Matched {count} ebooks. Reload Verification to see the comparison.")
    col1, col2, col3 = st.columns(3)
    search = col1.text_input("Search quality report").casefold()
    statuses = col2.multiselect("Quality statuses", STATUSES)
    ambiguous = col3.checkbox("Ambiguous editions only")
    visible = [b for b in report if search in str(title(b["item_id"])).casefold()
               and (not statuses or any(f["status"] in statuses for f in b["fields"].values()))
               and (not ambiguous or b["ambiguous"])]
    st.dataframe([{"Book": title(b["item_id"]), "Type": b["type"], "Ambiguous": b["ambiguous"],
                   **{k: f["status"] for k, f in b["fields"].items()}} for b in visible],
                 hide_index=True, width="stretch")
    st.download_button("Export complete quality report (JSON)", json.dumps(
        {"schema": 1, "snapshot": audit, "books": report}, ensure_ascii=False, indent=2),
        f"verification-{aid}.json", "application/json")
    st.download_button("Export complete quality report (CSV)", export_csv(report),
                       f"verification-{aid}.csv", "text/csv")
    if not visible:
        st.info("No books match these filters. Exports still include every book.")
        return
    item_id = st.selectbox("Inspect verification book", [b["item_id"] for b in visible], format_func=title)
    item = by_id[item_id]
    if item["calibre_limitation"]:
        st.info(item["calibre_limitation"])
    st.dataframe([{"Field": key, "Saved value": json.dumps(f["value"], ensure_ascii=False),
                   "Status": f["status"], "Assessment": f["reason"],
                   "Human reviewed": f["human_review"]["date"] if f["human_review"] else ""}
                  for key, f in item["fields"].items()], hide_index=True, width="stretch")
    left, right = st.columns(2)
    with left:
        st.write("**Saved Audiobookshelf values**")
        st.json({k: f["value"] for k, f in item["fields"].items() if k != "embedded"})
    with right:
        st.write("**Captured embedded tags and folder evidence**")
        st.json({"embedded": item["fields"]["embedded"]["value"], "folder": item["folder"]})
    with st.expander("All field provenance and lookup outcomes"):
        st.json(item)
    if connected and item["fields"]["cover"]["value"]:
        with st.form(f"verification_cover_{aid}_{item_id}"):
            cover = st.form_submit_button("Preview saved cover (read-only)")
        if cover:
            with operation_lock(store.path):
                st.image(adapter.cover(item_id), width=220)
    matches = {m["candidate_id"]: m for m in item["matches"]}
    if matches:
        cid = st.selectbox("Inspect external candidate / edition", list(matches),
                           format_func=lambda key: f"{matches[key]['candidate']['source']} · "
                           f"{matches[key]['candidate']['values'].get('title')} · "
                           f"{', '.join(matches[key]['candidate']['values'].get('narrators', []))} · {key[:8]}")
        m = matches[cid]
        st.write(f"Match level: **{m['level']}** · signals: {', '.join(m['signals']) or 'none'} · "
                 f"contradictions: {', '.join(m['contradictions']) or 'none'}")
        st.dataframe([{"Field": f, "Audiobookshelf": json.dumps(item["fields"][f]["value"], ensure_ascii=False),
                       "External candidate": json.dumps(v, ensure_ascii=False)}
                      for f, v in m["candidate"]["values"].items() if f in item["fields"]],
                     hide_index=True, width="stretch")
        url = m["candidate"]["url"]
        if urlsplit(url).scheme in ("https", "http") and urlsplit(url).netloc:
            st.link_button("Open source reference", url)
        with st.form(f"select_edition_{aid}_{item_id}"):
            reason = st.text_input("Edition selection evidence")
            select = st.form_submit_button("Record human edition selection", disabled=m["level"] == "unmatched")
        if select:
            store.annotate_verification(aid, item_id, "edition", reason, candidate_id=cid)
            st.rerun()
        with st.form(f"verification_proposal_{aid}_{item_id}"):
            field = st.selectbox("Supported discrepancy field", FIELDS)
            propose = st.form_submit_button("Create pending correction in Review",
                                            disabled=item["type"] != "audiobook" or bool(audit["read_only"]))
        if propose:
            propose_discrepancy(store, aid, item_id, field, cid)
            st.success("Pending proposal created. Approve it in Review, then confirm the exact preview in Apply.")
    with st.form(f"human_review_{aid}_{item_id}"):
        field = st.selectbox("Manually reviewed field", QUALITY_FIELDS)
        evidence = st.text_area("Human review evidence / source reference")
        reviewed = st.form_submit_button("Record manual review")
    if reviewed:
        store.annotate_verification(aid, item_id, field, evidence)
        st.rerun()
    st.caption("Translator, cover, chapter, identifier, and embedded-tag corrections remain read-only. "
               "Use Audiobookshelf or your source catalog deliberately for external remediation. "
               "Formatting preferences are advisory; conflicting evidence alone does not establish which source is wrong.")
