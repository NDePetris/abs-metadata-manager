"""Description representation compatibility; all transport and journals are synthetic."""
from html import escape

import pytest
from conftest import FakeAdapter, make_book

from abs_manager.models import result_matches, same_value, target_observed
from abs_manager.store import Store
from abs_manager.workflow import SafetyError, Workflow, confirmation, operation_comparison, preview, run_audit


@pytest.mark.parametrize("plain,html", [
    ("William de Worde begins publishing...", "<p>William de Worde begins publishing...</p>"),
    ("First paragraph.\n\nSecond paragraph!", "<p>First paragraph.</p>\n<p>Second paragraph!</p>"),
    ('Édition & "news" — today.', '<p>Édition &amp; &quot;news&quot; &#8212; today.</p>'),
    ("2 < 3 & 5 > 4", "<p>2 &lt; 3 &amp; 5 &gt; 4</p>"),
    ("First.\r\n\r\nSecond.", "<p>First.</p><p>Second.</p>"),
])
def test_description_equivalence(plain, html):
    assert same_value("description", plain, html)
    assert same_value("description", html, plain)
    assert target_observed("description", plain, html)
    assert result_matches({"description": plain}, {"description": html}, {"description": plain})
    assert not same_value("title", plain, html)


@pytest.mark.parametrize("changed", [
    "<p>Changed words.</p><p>Second paragraph!</p>",
    "<p>First paragraph!</p><p>Second paragraph!</p>",
    "<p>First paragraph.</p>",
    "<p>First paragraph. Second paragraph!</p>",
    "<p>Second paragraph!</p><p>First paragraph.</p>",
    "<p>First paragraph.</p><p></p><p>Second paragraph!</p>",
    "<p>First paragraph.\n\nSecond paragraph!</p>",
    "<p><strong>First paragraph.</strong></p><p>Second paragraph!</p>",
    '<p style="color:red">First paragraph.</p><p>Second paragraph!</p>',
    "<p>First paragraph.<br>Second paragraph!</p>",
    "<p>First paragraph.</p><script>Second paragraph!</script>",
    "<p>First paragraph.</p><!-- hidden --><p>Second paragraph!</p>",
    "<p>First paragraph.</p><p>Second paragraph!",  # malformed
    "<p> First paragraph.</p><p>Second paragraph!</p>",
    None,
])
def test_material_differences_remain_blocked(changed):
    plain = "First paragraph.\n\nSecond paragraph!"
    assert not same_value("description", plain, changed)
    assert not target_observed("description", plain, changed)


def test_entities_decode_once_and_formatting_is_not_discarded():
    assert not same_value("description", "News & views", "<p>News &amp;amp; views</p>")
    assert not same_value("description", "News views", "<p>News&nbsp;views</p>")
    assert not same_value("description", "<em>News</em>", "News")
    assert not same_value("description", "<em>News</em>", "<p>&lt;em&gt;News&lt;/em&gt;</p>")
    assert same_value("description", "<em>News</em>", "<em>News</em>")
    assert not same_value("description", None, "<p></p>")
    assert not same_value("description", "News", "<p>News&#1;</p>")
    assert not same_value("description", "News �", "<p>News&#0;</p>")
    assert not same_value("description", "News & views", "<p>News &amp views</p>")
    assert not same_value("description", "¬it;", "<p>&notit;</p>")
    assert not same_value("description", "Roshar’s divided peoples", "<p>RosharΓÇÖs divided peoples</p>")


def description_plan(tmp_path):
    api = FakeAdapter([make_book(title="Title", series=None)])
    store = Store(tmp_path / "description.sqlite3")
    aid = run_audit(store, api, api.libraries()[0])
    target = "William de Worde begins publishing...\n\nNews & views."
    store.confirm_targets(aid, "current-id", {"description": target})
    fid = next(f["id"] for f in store.findings(aid) if f["field"] == "description")
    store.review([fid], "approved")
    return store, api, aid, preview(store, aid, [fid]), target


def wrap(raw):
    metadata = raw["media"]["metadata"]
    metadata["description"] = "".join(f"<p>{escape(p)}</p>" for p in metadata["description"].split("\n\n"))


def test_apply_and_rollback_verify_wrapped_text_without_changing_targets(tmp_path):
    store, api, _aid, plan, target = description_plan(tmp_path)
    original = plan["books"][0]["before"]["description"]
    assert plan["books"][0]["payload"]["metadata"]["description"] == target
    api.mutate_after = wrap
    wrap(api.items["current-id"])  # equivalent source representation passes the live guard
    flow = Workflow(store, api)
    oid, = flow.apply(plan, confirmation(plan))
    assert store.operation(oid)["state"] == "verified"
    assert api.writes == [("current-id", {"description": target})]
    assert store.operation(oid)["data"]["after"]["description"] == target
    rows = operation_comparison(store.operation(oid)["data"], store.operation(oid)["observed"])
    assert next(r for r in rows if r["Field"] == "Description")["Comparison"] == "Semantically equivalent"
    refreshed = run_audit(store, api, api.libraries()[0])
    assert store.corrections(refreshed)[0]["abs_observed"]
    store.confirm_targets(refreshed, "current-id", {"description": target})
    assert not preview(store, refreshed)["books"]  # representation-only changes do not stage writes
    back = flow.rollback_preview(oid)
    assert back["payload"]["metadata"]["description"] == original
    rid = flow.rollback(back, confirmation(back, "ROLLBACK"))
    assert store.operation(rid)["state"] == "verified"
    assert api.writes[-1] == ("current-id", {"description": original})


@pytest.mark.parametrize("mode,expected", [
    ("equivalent", "verified"), ("content", "uncertain"), ("paragraph", "uncertain"),
    ("protected", "uncertain"), ("title", "uncertain"), ("path", "uncertain"),
    ("id", "uncertain"), ("library", "uncertain"), ("invalid", "uncertain"),
    ("before", "not_applied"),
])
def test_readonly_reconcile_description(tmp_path, mode, expected):
    store, api, _aid, plan, target = description_plan(tmp_path)
    group = plan["books"][0]
    data = {**group, "after": {**group["before"], **group["changes"]}}
    oid = store.record(api.server, "library-a", "current-id", "apply", "uncertain", data)
    raw = api.items["current-id"]
    metadata = raw["media"]["metadata"]
    metadata["description"] = target if mode != "before" else group["before"]["description"]
    wrap(raw)
    if mode == "content":
        metadata["description"] = metadata["description"].replace("publishing", "reading")
    if mode == "paragraph":
        metadata["description"] = metadata["description"].replace("</p><p>", " ")
    if mode == "protected":
        raw["media"]["tags"] = ["external change"]
    if mode == "title":
        metadata["title"] = "External title"
    if mode in ("id", "path", "library"):
        raw[{"id": "id", "path": "path", "library": "libraryId"}[mode]] = "external"
    if mode == "invalid":
        raw["isInvalid"] = True
    # Reopen the journal, as for an operation recorded by the previous version.
    reopened = Store(store.path)
    flow = Workflow(reopened, api)
    assert reopened.unresolved(api.server)  # nothing clears merely by opening/rendering
    assert flow.reconcile(oid) == expected
    assert api.reads[-1] == "current-id" and not api.writes
    assert reopened.operation(oid)["data"]["after"]["description"] == target
    if expected == "uncertain":
        assert reopened.unresolved(api.server)
    if mode == "equivalent":
        with pytest.raises(SafetyError, match="Only unresolved"):
            flow.reconcile(oid)


def test_changed_readback_remains_uncertain(tmp_path):
    store, api, _aid, plan, target = description_plan(tmp_path)
    api.mutate_after = lambda raw: raw["media"]["metadata"].update(description=f"<p>{target} Changed.</p>")
    with pytest.raises(SafetyError, match="uncertain"):
        Workflow(store, api).apply(plan, confirmation(plan))
    operation = store.history()[0]
    assert operation["state"] == "uncertain" and len(api.writes) == 1
    rows = operation_comparison(operation["data"], operation["observed"])
    assert next(r for r in rows if r["Field"] == "Description")["Comparison"] == "Material difference"


def test_uncertain_rollback_can_reconcile_without_another_write(tmp_path):
    store, api, _aid, plan, _target = description_plan(tmp_path)
    api.mutate_after = wrap
    flow = Workflow(store, api)
    oid, = flow.apply(plan, confirmation(plan))
    back = flow.rollback_preview(oid)
    api.fail = "after"
    with pytest.raises(SafetyError, match="uncertain"):
        flow.rollback(back, confirmation(back, "ROLLBACK"))
    rid = store.history()[0]["id"]
    assert len(api.writes) == 2 and store.operation(rid)["state"] == "uncertain"
    assert flow.reconcile(rid) == "verified"
    assert len(api.writes) == 2 and store.operation(oid)["state"] == "reverted"
