"""Library views over existing assessments and local correction decisions.

No acquisition or write authority: all values come from a completed snapshot.
"""
from collections import Counter, defaultdict

from .models import FIELDS, digest, validate_target
from .verification import fields_equal, reference_values

CATEGORIES = ("Already acceptable", "Proposed corrections", "Needs more evidence", "Manual/native remediation")


def library_plan(store, audit_id, report=None):
    audit = store.audit(audit_id)
    if audit["status"] != "complete":
        raise ValueError("Select a completed snapshot.")
    report = store.verification_report(audit_id) if report is None else report
    report = sorted(report, key=lambda r: r["item_id"])
    staged = {(r["item_id"], r["field"]) for r in store.corrections(audit_id)
              if r["status"] not in ("discarded", "observed_in_abs")}
    staged.update((f["item_id"], f["field"]) for f in store.findings(audit_id)
                  if f["kind"] in ("manual", "external", "confirmed_target")
                  and f["status"] in ("pending", "approved", "applied"))
    blocked = bool(store.unresolved(audit["server"]))
    proposals, inventory = [], []
    for item in report:
        start = len(proposals)
        for field in FIELDS:
            details = item["fields"][field]
            if (item["item_id"], field) in staged or details["disposition"] == "Pass":
                continue
            options = []
            if item["type"] == "ebook":
                options = [(p["value"], {**p, "scope": "Calibre authority", "signals": [], "contradictions": []})
                           for p in details["provenance"] if p["source"] == "Calibre catalog"]
            for m in item["matches"]:
                if item["type"] == "ebook":
                    continue  # Public audiobook/work candidates cannot override Calibre authority.
                if m["rejected"] or m["classification"] in ("wrong_work", "different_recording"):
                    continue
                supplied = reference_values(m)
                if field not in supplied or supplied[field] is None:
                    continue
                # Non-work fields require positive recording identity, even for suggestions.
                if field not in ("title", "authors") and m["level"] != "recording":
                    continue
                options.append((supplied[field], {"source": m["candidate"]["source"],
                                "url": m["candidate"]["url"], "candidate_id": m["candidate_id"],
                                "scope": m["level"], "signals": m["signals"],
                                "contradictions": m["contradictions"]}))
            credible = bool(options) and all(fields_equal(field, options[0][0], v) for v, _ in options)
            if credible and not fields_equal(field, details["value"], options[0][0]):
                strong = all(p["scope"] == "recording" and not p["contradictions"] for _, p in options)
                # Conflicting/ambiguous recording evidence cannot be upgraded by result order.
                strong = strong and not details.get("evidence_issue") and (
                    len(options) == 1 or all("asin" in p["signals"] for _, p in options))
                strong = strong and not blocked and all(l.get("state") == "ok" for l in item["lookups"])
                strong = strong and not any(not m["rejected"] and m["contradictions"]
                                            and {"asin", "isbn"} & set(m["signals"]) for m in item["matches"])
                if field == "series":
                    # A field replacement must not silently drop another series relationship.
                    strong = strong and {s["name"] for s in details["value"]} <= {
                        s["name"] for s in options[0][0]}
                choices = [(options[0][0], "Recording-backed" if strong else "Individual review",
                            [p for _, p in options], details["reason"], strong)]
            elif options:
                choices = []  # Disagreement or agreement with saved value: no arbitrary replacement.
            else:
                choices = [(f["proposed"], "Individual review", [f["evidence"]], f["reason"], False)
                           for f in details["local_findings"] if f["actionable"]
                           and field != "narrators" and f["kind"] not in ("manual", "confirmed_target")]
            for target, strength, evidence, reason, grouped in choices[:1]:
                if field in ("authors", "narrators") and not target:
                    continue  # Empty provider credits cannot justify clearing required contributors.
                try:
                    validate_target(field, target)
                except ValueError:
                    continue
                if target == details["value"]:
                    continue
                row = {"item_id": item["item_id"], "identity": item["identity"], "type": item["type"],
                       "title": item["fields"]["title"]["value"], "field": field,
                       "current": details["value"], "target": target, "reason": reason,
                       "strength": strength, "evidence": evidence, "grouped": grouped and item["type"] == "audiobook",
                       "action": "Review audiobook target" if item["type"] == "audiobook" else "Correct in Calibre",
                       "status": "Proposed", "risk": "Exact field only; inspect edition and contributor roles"}
                row["id"] = digest([audit_id, audit["server"], audit["library"], row])
                proposals.append(row)
        manual = [f for f, d in item["fields"].items() if d["quality_applicable"]
                  and d["disposition"] != "Pass" and (f in ("cover", "genres", "tags") or item["type"] == "ebook")]
        category = ("Already acceptable" if item["disposition"] == "Pass" else
                    "Proposed corrections" if len(proposals) > start or any(iid == item["item_id"] for iid, _ in staged) else
                    "Needs more evidence" if item["type"] == "audiobook" and any(
                        d["quality_applicable"] and d["disposition"] != "Pass" for f, d in item["fields"].items()
                        if f not in ("cover", "genres", "tags")) else
                    "Manual/native remediation" if manual else "Needs more evidence")
        inventory.append({"item_id": item["item_id"], "title": item["fields"]["title"]["value"],
                          "type": item["type"], "category": category, "manual_fields": manual,
                          "quality": item["disposition"], "review": item["review_status"]})
    result = {"audit_id": audit_id, "server": audit["server"], "library": audit["library"],
              "evidence_digest": digest(report), "writes_blocked": blocked,
              "inventory": inventory, "proposals": proposals,
              "counts": {c: sum(r["category"] == c for r in inventory) for c in CATEGORIES}}
    result["digest"] = digest(result)
    return result


def stage_selected(store, plan, selected):
    """Validate the entire cached plan before atomic existing per-field staging."""
    from .locking import operation_lock
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Select distinct proposals.")
    with operation_lock(store.path), store.connect() as db:
        audit = store.audit(plan["audit_id"])
        if store.latest(audit["server"], audit["library"])["id"] != audit["id"] or library_plan(store, audit["id"]) != plan:
            raise ValueError("Snapshot, decisions or evidence changed. Regenerate the plan.")
        indexed = {p["id"]: p for p in plan["proposals"]}
        targets, evidence = defaultdict(dict), defaultdict(list)
        for pid in selected:
            p = indexed.get(pid)
            if not p or not p["grouped"]:
                raise ValueError("Only recording-backed audiobook proposals support grouped staging; inspect other exceptions individually.")
            targets[p["item_id"]][p["field"]] = p["target"]
            evidence[p["item_id"]].append(p)
        from .models import compact
        for iid, fields in targets.items():
            store._confirm_targets(db, audit["id"], iid, fields, compact(evidence[iid]))
    return sum(len(t) for t in targets.values())


def completion_report(store, audit_id, report=None, plan=None):
    report = store.verification_report(audit_id) if report is None else report
    plan = library_plan(store, audit_id, report) if plan is None else plan
    fields = [d for r in report for d in r["fields"].values() if d["quality_applicable"]]
    corrections = store.corrections(audit_id)
    from .verification_workflow import evidence_coverage
    observed = [c for c in corrections if c["abs_observed"] and c["status"] not in ("needs_review", "identity_ambiguous", "discarded")]
    journals = [r for r in store.history(plan["server"]) if r["library"] == plan["library"]]
    return {"audit_id": audit_id, "books": len(report), "applicable_fields": len(fields),
            "quality": dict(Counter(d["disposition"] for d in fields)),
            "pass_bases": dict(Counter(d["reason_code"] for d in fields if d["disposition"] == "Pass")),
            "review": dict(Counter(r["review_status"] for r in report)),
            "proposals": len(plan["proposals"]), "corrections": dict(Counter(c["status"] for c in corrections)),
            "abs_observed": len(observed),
            "verified_write_operations": sum(r["action"] == "apply" and r["state"] == "verified" for r in journals),
            "calibre_verified_waiting_abs": sum(bool(c["calibre_verified"]) and not c["abs_observed"]
                                                and c["status"] == "verified_in_calibre" for c in corrections),
            "artwork_unresolved": sum(r["fields"]["cover"]["disposition"] != "Pass" for r in report),
            "category_problems": sum(d.get("reason_code") == "category_problem" for d in fields),
            "unresolved_journals": [r["id"] for r in store.unresolved(plan["server"])],
            "coverage": evidence_coverage(report), "production_signoff": "Owner validation required (#16)",
            "limitations": "Cached evidence is not a fresh authority check. Artwork requires visual inspection; work evidence cannot establish narrators. Calibre and ABS observations are separate."}
