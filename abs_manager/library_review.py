"""Read-only cover observations and library category hygiene; no remediation writers."""
from collections import Counter, defaultdict

from .models import digest
from .persistent_state import identity


def cover_identity(book):
    media = book.raw["media"]
    return digest([identity(book), media.get("coverPath"), media.get("coverLastUpdate")])


def category_inventory(report, decisions=()):
    preferences = {(d["field"], d["value"]): d["decision"] for d in decisions}
    result = []
    for field in ("genres", "tags"):
        members, duplicates, variants = defaultdict(list), defaultdict(list), defaultdict(set)
        for book in report:
            values = category_values(book["fields"][field]["value"])
            for value, count in Counter(values).items():
                members[value].append(book["item_id"])
                if count > 1:
                    duplicates[value].append(book["item_id"])
                variants[value.strip().casefold()].add(value)
        for value in sorted(members, key=str.casefold):
            result.append({"field": field, "value": value, "books": members[value],
                           "count": len(members[value]), "duplicates": duplicates[value],
                           "variants": sorted(variants[value.strip().casefold()] - {value}),
                           "decision": preferences.get((field, value), "Unreviewed")})
    return result


def category_values(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError("Unsupported saved ABS genre/tag shape; expected an array of strings. Refresh and inspect the native item.")
    return value


def category_assessments(report, inventory):
    """Supply hygiene evidence to the existing quality policy, never bibliographic proof."""
    indexed = {(r["field"], r["value"]): r for r in inventory}
    for book in report:
        for field in ("genres", "tags"):
            details = book["fields"][field]
            rows = [indexed[field, value] for value in category_values(details["value"])]
            if not rows:
                continue
            problems = [r for r in rows if not r["value"].strip() or r["decision"] == "Unwanted"
                        or book["item_id"] in r["duplicates"] or r["variants"]]
            details["hygiene"] = "category_problem" if problems else (
                "category_accepted" if all(r["decision"] == "Acceptable" for r in rows) else "category_unreviewed")
            details["reason"] = ("Unwanted, repeated or differently capitalized category; correct in native ABS/Calibre."
                                 if problems else "Library category preference; separate from bibliographic verification.")
            details["provenance"].append({"source": "Library category review", "values": rows})
