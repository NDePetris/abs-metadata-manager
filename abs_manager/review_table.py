"""Small native table controls. Selection is local UI state, never approval."""
import streamlit as st

from .models import digest


def filter_rows(rows, search="", field=None, author=None, status=None, confidence=None, sort="Book"):
    query = search.casefold().strip()
    rows = [r for r in rows if (not query or query in (r["Book"] + " " + r["Author"]).casefold())
            and (not field or r["Field"] == field) and (not author or r["Author"] == author)
            and (not status or r["Status"] == status) and (not confidence or r["Evidence"] == confidence)]
    return sorted(rows, key=lambda r: (str(r.get(sort, "")).casefold(), r["Book"].casefold(), r["id"]))


def update_selection(selected, visible, checked, eligible):
    """Only visible eligible checkboxes change; hidden choices survive."""
    return ((set(selected) - set(visible)) | (set(checked) & set(visible))) & set(eligible)


def correction_table(rows, eligible, bulk_eligible, key):
    cols = st.columns([2, 1, 1])
    search = cols[0].text_input("Search corrections by title or author", key=key + ":search")
    field = cols[1].selectbox("Field", sorted({r["Field"] for r in rows}), index=None, key=key + ":field")
    sort = cols[2].selectbox("Sort by", ["Book", "Author", "Field", "Priority"], key=key + ":sort")
    with st.expander("More filters"):
        cols = st.columns(3)
        author = cols[0].selectbox("Author", sorted({r["Author"] for r in rows}), index=None, key=key + ":author")
        status = cols[1].selectbox("Workflow status", sorted({r["Status"] for r in rows}), index=None, key=key + ":status")
        confidence = cols[2].selectbox("Confidence / review requirement", sorted({r["Evidence"] for r in rows}),
                                       index=None, key=key + ":confidence")
    visible = filter_rows(rows, search, field, author, status, confidence, sort)
    ids = [r["id"] for r in visible]
    selected_key = key + ":selected_ids"
    selected = set(st.session_state.get(selected_key, [])) & set(eligible)
    cols = st.columns(2)
    if cols[0].button("Select eligible rows in view", key=key + ":all", disabled=not set(ids) & set(bulk_eligible)):
        selected |= set(ids) & set(bulk_eligible)
    if cols[1].button("Clear rows in view", key=key + ":none", disabled=not selected & set(ids)):
        selected -= set(ids)
    if visible:
        # A new widget identity when its input changes prevents replaying stale row-index edits.
        table_key = key + ":table:" + digest([ids, sorted(selected), [(r["Status"], r["Suggested"]) for r in visible]])
        table = [{"Select": r["id"] in selected, **{k: r[k] for k in
                  ("Book", "Author", "Field", "Current", "Suggested", "Evidence", "Status")}} for r in visible]
        edited = st.data_editor(table, hide_index=True, width="stretch", height=min(390, 38 + 35 * len(table)),
                                disabled=[k for k in table[0] if k != "Select"], key=table_key,
                                column_config={"Select": st.column_config.CheckboxColumn(width=50,
                                                   help="Only eligible rows can be retained. This is not field approval."),
                                               "Book": st.column_config.TextColumn(width=150),
                                               "Author": st.column_config.TextColumn(width=100),
                                               "Field": st.column_config.TextColumn(width=85),
                                               "Current": st.column_config.TextColumn(width=100),
                                               "Suggested": st.column_config.TextColumn(width=150),
                                               "Evidence": st.column_config.TextColumn(width=90),
                                               "Status": st.column_config.TextColumn(width=100)})
        selected = update_selection(selected, ids, [pid for pid, row in zip(ids, edited) if row["Select"]], eligible)
    else:
        st.info("No corrections match these filters.")
    st.session_state[selected_key] = sorted(selected)
    st.caption(f"{len(visible)} visible rows · {len(selected)} selected in total · "
               f"{len(selected - set(ids))} selected outside this view. Selection is not approval.")
    return visible, sorted(selected)
