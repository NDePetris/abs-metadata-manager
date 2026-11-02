"""Native controls for persistent human review and source-specific remediation."""

from __future__ import annotations

import json

import streamlit as st

from .calibre import import_catalog
from .models import FIELDS
from .verification import QUALITY_FIELDS

DECISIONS = {
    "confirmed": "Saved metadata confirmed correct",
    "correction_needed": "Saved metadata needs correction",
    "unable": "Unable to verify",
    "later": "Review later",
    "reopened": "Reopen / revoke previous acceptance",
}
MATCHES = {
    "correct_recording": "Correct work and recording",
    "recording_uncertain": "Correct work, recording uncertain",
    "different_recording": "Different recording of the same work",
    "wrong_work": "Wrong work",
    "no_match": "No match found",
}


def display(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def outstanding_fields(item, decisions):
    return [
        field
        for field, assessment in item["fields"].items()
        if assessment["status"] != "Not applicable"
        and not (decisions.get(field, {}).get("valid") and decisions[field]["decision"] == "confirmed")
    ]


def review_status(item, decisions):
    active = [d for d in decisions.values() if d["valid"]]
    if any(d["decision"] == "correction_needed" for d in active):
        return "Correction needed"
    if not outstanding_fields(item, decisions):
        return "Accepted"
    if any(not d["valid"] and d["decision"] == "confirmed" for d in decisions.values()):
        return "Needs review"
    if any(d["decision"] == "unable" for d in active):
        return "Unable to verify"
    if item.get("match_classification") in ("different_recording", "recording_uncertain", "wrong_work"):
        return "Wrong/uncertain edition"
    if all(a["status"] in ("Verified", "Not applicable") for a in item["fields"].values()):
        return "Resolved"
    return "Needs review"


def field_review(store, audit, item):
    aid, iid = audit["id"], item["item_id"]
    decisions = store.field_decisions(aid, iid)
    st.dataframe(
        [
            {
                "Field": field,
                "Saved value": display(assessment["value"]),
                "Independent assessment": assessment["status"],
                "Your decision": (
                    DECISIONS[decisions[field]["decision"]]
                    if decisions[field]["valid"]
                    else "Reopened: value or edition identity changed"
                )
                if field in decisions
                else "Needs review",
                "Evidence": decisions.get(field, {}).get("evidence", ""),
            }
            for field, assessment in item["fields"].items()
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Select the individually displayed fields you reviewed. Acceptance is remembered across imports; "
        "external disagreements remain inspectable. These decisions never approve metadata writes."
    )
    with st.form(f"durable_review_{aid}_{iid}"):
        fields = st.multiselect("Fields reviewed", QUALITY_FIELDS)
        decision = st.selectbox("Saved metadata decision", list(DECISIONS), format_func=DECISIONS.get)
        evidence = st.text_area("Decision evidence")
        source = st.text_input("Source attribution / reference")
        submit = st.form_submit_button("Save persistent field decisions")
    if submit:
        if not evidence.strip():
            raise ValueError("Describe the evidence for your decision.")
        store.decide_fields(aid, iid, fields, decision, evidence, source)
        st.rerun()
    with st.expander("Field decision history and previous accepted values"):
        history = store.field_decisions(aid, iid, history=True)
        if history:
            st.dataframe(
                [
                    {
                        k: display(row.get(k))
                        for k in (
                            "field",
                            "value",
                            "decision",
                            "evidence",
                            "source",
                            "created",
                            "audit_id",
                            "valid",
                        )
                    }
                    for row in history
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption(
                "No persistent field decisions yet. Earlier generic review annotations remain separate."
            )


def resolution_view(store, audit):
    if audit["status"] != "complete":
        st.info("Finish or resume this import before reviewing saved metadata.")
        return
    report = store.verification_report(audit["id"])
    states = {b["item_id"]: b["human_decisions"] for b in report}
    statuses = {b["item_id"]: review_status(b, states[b["item_id"]]) for b in report}
    left, right = st.columns([2, 1])
    search = left.text_input("Search review queue").casefold()
    selected_status = right.selectbox(
        "Review queue",
        [
            "Outstanding",
            "All",
            "Needs review",
            "Wrong/uncertain edition",
            "Correction needed",
            "Accepted",
            "Unable to verify",
            "Resolved",
        ],
    )
    visible = [
        b
        for b in report
        if search in display(b["fields"]["title"]["value"]).casefold()
        and (
            selected_status == "All"
            or selected_status == statuses[b["item_id"]]
            or selected_status == "Outstanding"
            and statuses[b["item_id"]] not in ("Accepted", "Resolved")
        )
    ]
    st.caption(
        f"{len(visible)} of {len(report)} books shown. Accepted fields are excluded from outstanding review."
    )
    if not visible:
        st.info(
            "No books need attention in this view. Import again after source edits, or choose All to reopen acceptance."
        )
        return
    st.dataframe(
        [
            {
                "Book": b["fields"]["title"]["value"],
                "Status": statuses[b["item_id"]],
                "Fields awaiting acceptance": ", ".join(outstanding_fields(b, states[b["item_id"]])),
                "Edition": MATCHES.get(b.get("match_classification"), "No match found"),
            }
            for b in visible
        ],
        hide_index=True,
        width="stretch",
    )
    iid = st.selectbox(
        "Resolve book",
        [b["item_id"] for b in visible],
        format_func=lambda key: next(
            b["fields"]["title"]["value"] or key for b in visible if b["item_id"] == key
        ),
    )
    item = next(b for b in visible if b["item_id"] == iid)
    st.subheader(item["fields"]["title"]["value"] or iid)
    st.write(f"Edition: **{MATCHES.get(item.get('match_classification'), 'No match found')}**")
    if item.get("manual_edition"):
        st.info(f"Manual edition association: {item['manual_edition']['evidence']}")
    field_review(store, audit, item)
    with st.expander("External candidates, contradictions, and historical source evidence"):
        for match in item["matches"]:
            st.write(
                f"**{MATCHES.get(match.get('classification'), match['level'])}** · "
                f"{match['candidate']['source']} · {'Rejected' if match.get('rejected') else 'Candidate'}"
            )
            st.json(
                {
                    "values": match["candidate"]["values"],
                    "signals": match["signals"],
                    "contradictions": match["contradictions"],
                    "source": match["candidate"]["url"],
                }
            )
        st.json(
            {
                "folder": item["folder"],
                "lookups": item["lookups"],
                "legacy_reviews": {
                    f: a["human_review"] for f, a in item["fields"].items() if a["human_review"]
                },
            }
        )
    manual_correction_form(store, audit, item)


def manual_correction_form(store, audit, item):
    aid, iid = audit["id"], item["item_id"]
    audio = item["type"] == "audiobook"
    with st.expander("Prepare a source correction"):
        st.caption(
            "Audiobook proposals still require separate field approval and exact confirmation. "
            "Edit ebooks in Calibre; cover, chapter, tag, and other unsupported changes are manual."
        )
        with st.form(f"source_correction_{aid}_{iid}"):
            field = st.selectbox("Correction field", QUALITY_FIELDS)
            proposed = st.text_area(
                "Proposed source value",
                help="Plain text for title/subtitle/cover; JSON for lists and identifiers.",
            )
            evidence = st.text_area("Correction evidence / manual action")
            calibre_id = st.text_input("Calibre ID (optional)") if not audio else ""
            submit = st.form_submit_button("Prepare source correction")
        if submit:
            if not evidence.strip():
                raise ValueError("Provide supporting evidence and the required manual action.")
            target = proposed if field in ("title", "subtitle", "cover") else json.loads(proposed)
            if audio and field in FIELDS:
                store.propose(aid, iid, field, target, evidence)
            else:
                store.queue_correction(
                    aid, iid, field, target, evidence, int(calibre_id) if calibre_id.strip() else None
                )
            st.session_state["flash"] = (
                "Correction prepared. Open Update Sources for the source-specific next step."
            )
            st.rerun()


def correction_queue(store, audit):
    rows = store.corrections(audit["id"])
    st.write(
        "Edit ebooks in Calibre, export its catalog, and compare it here. Marking an edit is a reminder; "
        "only a later matching catalog confirms it. A later ABS import separately confirms synchronization."
    )
    if rows:
        st.dataframe(
            [
                {
                    "ID": r["id"],
                    "Book": r["title"],
                    "Calibre ID": r["calibre_id"],
                    "Field": r["field"],
                    "Current": display(r["current"]),
                    "Proposed": display(r["proposed"]),
                    "Evidence": r["evidence"],
                    "Correction status": r["status"],
                    "Verified in Calibre": r["calibre_verified"],
                    "Observed in ABS": r["abs_observed"],
                }
                for r in rows
            ],
            hide_index=True,
            width="stretch",
        )
        eligible = [
            r
            for r in rows
            if not r["calibre_verified"]
            and r["status"] != "identity_ambiguous"
            and not store.book(r["audit_id"], r["item_id"]).is_audio
        ]
        if eligible:
            with st.form(f"calibre_edited_{audit['id']}"):
                selected = st.selectbox(
                    "Correction edited in Calibre",
                    [r["id"] for r in eligible],
                    format_func=lambda key: next(
                        f"#{key} · {r['title']} · {r['field']}" for r in eligible if r["id"] == key
                    ),
                )
                submit = st.form_submit_button("Mark edited in Calibre")
            if submit:
                store.mark_calibre_edited(selected)
                st.rerun()
    else:
        st.info(
            "No manual corrections queued. In Resolve, inspect a book and choose Prepare a source correction."
        )
    with st.form(f"reconcile_catalog_{audit['id']}"):
        upload = st.file_uploader("Updated Calibre JSON catalog", type=["json"])
        submit = st.form_submit_button(
            "Import catalog and check corrections", disabled=audit["status"] != "complete"
        )
    if submit:
        if upload is None:
            raise ValueError("Choose an exported Calibre JSON catalog.")
        count = import_catalog(store, audit["id"], upload.getvalue().decode("utf-8-sig"))
        st.session_state["flash"] = (
            f"Compared {count} ebooks. Only corrections present in this catalog are confirmed."
        )
        st.rerun()
    st.caption(
        "Export: calibredb list --for-machine --fields title,authors,series,series_index,identifiers. "
        "This app never opens or modifies Calibre's database or ebook files."
    )


def persistent_history(store):
    with st.expander("Persistent verification and remediation history"):
        for label, table in (
            ("Field decisions", "field_decision_history"),
            ("Edition selections and rejections", "candidate_decision_history"),
            ("Source corrections", "corrections"),
        ):
            rows = store.rows(f"SELECT * FROM {table} ORDER BY id DESC")
            st.write(f"**{label}**")
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.caption("No records yet. History is retained when books leave the library.")
