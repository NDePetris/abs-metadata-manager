"""Native library proposal review; acquisition and mutations require explicit submits."""
import json
from collections import Counter

import streamlit as st

from .correction_plan import completion_report, library_plan, stage_selected
from .verification_ui import display


def plan_controls(store, audit, report):
    plan = library_plan(store, audit["id"], report)
    with st.expander("Library correction proposals"):
        st.write(" · ".join(f"{k}: {v}" for k, v in plan["counts"].items()))
        st.caption("Every book is counted once. Categories describe the next action; books may also have artwork or evidence gaps.")
        cols = st.columns(3)
        media = cols[0].selectbox("Proposal media", ["Any", "audiobook", "ebook"])
        field = cols[1].selectbox("Proposal field", ["Any"] + sorted({p["field"] for p in plan["proposals"]}))
        strength = cols[2].selectbox("Evidence strength", ["Any", "Recording-backed", "Individual review"])
        rows = [p for p in plan["proposals"] if (media == "Any" or p["type"] == media)
                and (field == "Any" or p["field"] == field) and (strength == "Any" or p["strength"] == strength)]
        duplicate_titles = Counter(p["title"] for p in plan["proposals"])

        def label(p):
            return p["title"] + " · " + p["field"] + (" · " + p["item_id"] if duplicate_titles[p["title"]] > 1 else "")
        st.dataframe([{"Book": p["title"], "Field": p["field"], "Current": display(p["current"]),
                       "Proposed": display(p["target"]), "Evidence": p["strength"], "Action": p["action"]}
                      for p in rows], hide_index=True, width="stretch")
        if rows:
            pid = st.selectbox("Inspect proposal evidence", [p["id"] for p in rows],
                               format_func=lambda v: next(label(p) for p in rows if p["id"] == v))
            inspected = next(p for p in rows if p["id"] == pid)
            st.caption(inspected["reason"] + " · " + inspected["risk"])
            with st.expander("Evidence details and original identity"):
                st.json(inspected)
        eligible = {p["id"]: p for p in rows if p["grouped"]}
        chosen = st.multiselect("Supported audiobook targets", list(eligible),
                                format_func=lambda v: label(eligible[v]),
                                key="proposals:" + plan["digest"])
        if chosen:
            st.dataframe([{"Book": eligible[i]["title"], "Field": eligible[i]["field"],
                           "Before": display(eligible[i]["current"]), "After": display(eligible[i]["target"])}
                          for i in chosen], hide_index=True, width="stretch")
        if st.button("Accept selected local targets", disabled=not chosen):
            stage_selected(store, plan, chosen)
            st.success("Targets staged in Changes. Separate field approval and exact write confirmation are required.")
            st.rerun()
        st.caption("Individual-review suggestions use the existing Current / Reference / Desired editor below. Ebooks, covers and categories require native tools. Evidence acquisition remains explicit under Advanced / Evidence.")
        st.download_button("Export correction plan", json.dumps(plan, ensure_ascii=False, indent=2),
                           "correction-plan.json", "application/json")
    with st.expander("Library completion and owner sign-off"):
        result = completion_report(store, audit["id"], report, plan)
        st.dataframe([{"Measure": "Books assessed", "Count": result["books"]},
                      {"Measure": "Applicable fields", "Count": result["applicable_fields"]},
                      *[{"Measure": k, "Count": v} for k, v in result["quality"].items()],
                      *[{"Measure": k.replace("_", " "), "Count": v} for k, v in result["pass_bases"].items()],
                      {"Measure": "Outstanding proposals", "Count": result["proposals"]},
                      {"Measure": "Artwork unresolved", "Count": result["artwork_unresolved"]},
                      {"Measure": "Category problems", "Count": result["category_problems"]},
                      {"Measure": "Unresolved write journals", "Count": len(result["unresolved_journals"])}],
                     hide_index=True, width="stretch")
        with st.expander("Execution, synchronization and coverage details"):
            st.json(result)
        st.dataframe(plan["inventory"], hide_index=True, width="stretch")
        st.caption("Review acceptance, current-value quality, write execution and observed synchronization are separate. Synthetic validation cannot complete production sign-off.")
        st.markdown("Before owner sign-off:\n\n- Back up the workspace and reconcile unresolved History entries.\n- Acquire remaining evidence and inspect unresolved editions/artwork.\n- Review and stage supported targets, then separately preview and authorize writes.\n- Correct ebooks in Calibre and native-only metadata in ABS.\n- Revalidate Calibre, observe fresh ABS values, and export this report.\n- Record remaining exceptions and owner validation in #16.")
        st.download_button("Export completion report", json.dumps(result, ensure_ascii=False, indent=2),
                           "library-completion.json", "application/json")
