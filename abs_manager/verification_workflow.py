"""User-initiated, sequential read-only verification with durable checkpoints.

The only correction integration delegates to Store.propose; never approves or writes.
"""
from __future__ import annotations

import json

from .adapter import AdapterError
from .locking import operation_lock
from .models import FIELDS, compact, digest
from .store import now
from .verification import comparable


def start_verification(store, audit_id, source, item_ids, *, refresh=False, query_overrides=None):
    with operation_lock(store.path):
        audit = store.audit(audit_id)
        if audit["status"] != "complete":
            raise ValueError("Select a completed audit snapshot.")
        if source.id.startswith("abs:") and source.scope != audit["server"]:
            raise ValueError("Connect to the snapshot's original server for ABS provider searches.")
        books = {b.id: b for b in store.books(audit_id)}
        if not item_ids or len(set(item_ids)) != len(item_ids) or not set(item_ids) <= books.keys():
            raise ValueError("Select books from this snapshot.")
        if source.targeted_only and len(item_ids) != 1:
            raise ValueError("Open Library supports targeted human-initiated single-book lookups only.")
        overrides = query_overrides or {}
        if not isinstance(overrides, dict) or not set(overrides) <= set(item_ids):
            raise ValueError("Search refinements must refer to selected books.")
        with store.connect() as db:
            cursor = db.execute("INSERT INTO verification_runs(audit_id,source,scope,state,created,updated) "
                                "VALUES(?,?,?,'paused',?,?)", (audit_id, source.id, source.scope, now(), now()))
            run_id = cursor.lastrowid
            for item_id in item_ids:
                query = source.query(books[item_id])
                if item_id in overrides:
                    refinement = overrides[item_id]
                    allowed = ({"title", "author"} if source.id.startswith("abs:") else
                               {"q", "title", "author"} if source.id == "openlibrary" else
                               {"q"} if source.id == "googlebooks" else set(query))
                    if (not isinstance(refinement, dict) or not refinement or not set(refinement) <= allowed
                            or any(not isinstance(v, str) or not v.strip() for v in refinement.values())):
                        raise ValueError("Unsupported or empty provider search refinement.")
                    if source.id in ("openlibrary", "googlebooks") or "title" in refinement:
                        # A replacement title/ASIN must not retain a stale author filter.
                        query = dict(refinement)
                    else:
                        query = {**query, **refinement}
                key = digest({"source": source.id, "scope": source.scope, "query": query, "schema": 1})
                cached = None if refresh else db.execute(
                    "SELECT id FROM verification_lookups WHERE cache_key=? "
                    "AND json_extract(data,'$.state') IN ('ok','no_match') ORDER BY id DESC LIMIT 1", (key,)).fetchone()
                db.execute("INSERT INTO verification_tasks VALUES(?,?,?,?,?)",
                           (run_id, item_id, compact(query), key, cached[0] if cached else None))
        return run_id


def run_verification(store, run_id, source, *, progress=lambda *args: None, cancelled=lambda: False, limit=110):
    with operation_lock(store.path):
        run = store.rows("SELECT * FROM verification_runs WHERE id=?", (run_id,))[0]
        if (run["source"], run["scope"]) != (source.id, source.scope):
            raise ValueError("Resume with the original provider/server.")
        if run["state"] == "complete":
            return
        with store.connect() as db:
            db.execute("UPDATE verification_runs SET state='running',updated=? WHERE id=?", (now(), run_id))
        tasks = store.rows("SELECT * FROM verification_tasks WHERE run_id=? ORDER BY item_id", (run_id,))
        done = sum(t["lookup_id"] is not None for t in tasks)
        processed = 0
        try:
            for task in tasks:
                state = store.rows("SELECT state FROM verification_runs WHERE id=?", (run_id,))[0]["state"]
                if cancelled() or state == "cancelled":
                    store.cancel_verification(run_id)
                    return
                if task["lookup_id"] is not None:
                    continue
                if processed >= limit:
                    break
                cached = store.rows("SELECT id FROM verification_lookups WHERE cache_key=? AND captured>=? "
                                    "AND json_extract(data,'$.state') IN ('ok','no_match') ORDER BY id DESC LIMIT 1",
                                    (task["cache_key"], run["created"]))
                if cached:
                    with store.connect() as db:
                        db.execute("UPDATE verification_tasks SET lookup_id=? WHERE run_id=? AND item_id=?",
                                   (cached[0]["id"], run_id, task["item_id"]))
                    done += 1
                    progress(done, len(tasks), "cached")
                    continue
                query = json.loads(task["query"])
                data = {"source": source.id, "scope": source.scope, "query": query, "captured": now(),
                        "state": "ok", "candidates": []}
                try:
                    data.update(source.search(query))
                    if not data["candidates"]:
                        data["state"] = "no_match"
                except (AdapterError, ValueError, KeyError, TypeError):
                    data.update(state="unavailable", error="Provider unavailable or response failed schema validation.")
                with store.connect() as db:
                    cursor = db.execute("INSERT INTO verification_lookups(cache_key,source,scope,captured,data) "
                                        "VALUES(?,?,?,?,?)", (task["cache_key"], source.id, source.scope,
                                                              data["captured"], compact(data)))
                    db.execute("UPDATE verification_tasks SET lookup_id=? WHERE run_id=? AND item_id=?",
                               (cursor.lastrowid, run_id, task["item_id"]))
                done += 1
                processed += 1
                progress(done, len(tasks), data["state"])
                if data["state"] == "unavailable":
                    # Do not hammer an unavailable provider for the rest of a library.
                    break
        finally:
            with store.connect() as db:
                pending = db.execute("SELECT COUNT(*) FROM verification_tasks WHERE run_id=? AND lookup_id IS NULL",
                                     (run_id,)).fetchone()[0]
                db.execute("UPDATE verification_runs SET state=?,updated=? WHERE id=? AND state!='cancelled'",
                           ("paused" if pending else "complete", now(), run_id))


def retry_unavailable(store, run_id):
    """Explicit retry only; successful/empty completed queries remain attached."""
    with operation_lock(store.path), store.connect() as db:
        db.execute("UPDATE verification_tasks SET lookup_id=NULL WHERE run_id=? AND lookup_id IN "
                   "(SELECT id FROM verification_lookups WHERE json_extract(data,'$.state')='unavailable')", (run_id,))
        db.execute("UPDATE verification_runs SET state='paused',updated=? WHERE id=?", (now(), run_id))


def propose_discrepancy(store, audit_id, item_id, field, candidate_id):
    if field not in FIELDS:
        raise ValueError("Read-only field: remediate externally; translator/cover/chapter writes are unsupported.")
    report = next(b for b in store.verification_report(audit_id) if b["item_id"] == item_id)
    matched = next((m for m in report["matches"] if m["candidate_id"] == candidate_id), None)
    human_recording = (matched and report["selected_candidate"] == candidate_id and not matched.get("rejected")
                       and matched["classification"] != "wrong_work" and not matched["candidate"].get("work_only"))
    if (not matched or matched.get("rejected") or
            not human_recording and (matched["level"] != "recording" or report["ambiguous"])):
        raise ValueError("A corroborated recording and explicit selection of ambiguous editions are required.")
    item = matched["candidate"]
    if field not in item["values"]:
        raise ValueError("Candidate has no evidence for this field.")
    target = item["values"][field]
    before = store.book(audit_id, item_id).values[field]
    equal = comparable(field, target) == comparable(field, before)
    if field == "series":
        equal = equal and comparable("series_position", [s["sequence"] for s in target]) == comparable(
            "series_position", [s["sequence"] for s in before])
    if equal:
        raise ValueError("Candidate agrees; no discrepancy to propose.")
    reason = compact({"source": item["source"], "url": item["url"], "candidate_id": candidate_id,
                      "signals": matched["signals"], "contradictions": matched["contradictions"],
                      "human_selection": report["selected_candidate"], "evidence": item["raw"],
                      "lookups": [{k: lookup[k] for k in ("source", "scope", "query", "captured")}
                                  for attempt in report["lookups"]
                                  for lookup in [attempt.get("previous_success", attempt)] if any(
                                      c["id"] == candidate_id for c in lookup.get("candidates", []))]})
    return store.propose(audit_id, item_id, field, target, reason)
