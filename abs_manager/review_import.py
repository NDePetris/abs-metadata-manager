"""Data-only import of reviewed metadata recommendations."""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .locking import operation_lock
from .models import FIELDS, Book, compact, digest, target_observed, validate_target
from .persistent_state import identity
from .workflow import MAX_APPLY_BATCH, SafetyError, guard, library_identity

DOCUMENT_TYPE = "audiobookshelf_metadata_review_recommendations"
DOCUMENT_VERSION = "1.0-review-proposal"
NATIVE_FIELDS = {"genres": "genres", "description": "description", "publication_year": "publishedYear"}
STATUSES = ("approved_recommendation", "editorial_review", "requires_recording_verification")
EXISTING = "already_staged_in_original_export_not_repeated"
READY = "Ready to stage"
HELD = "Recording confirmation required"
ALREADY_APPLIED = "Already observed in saved snapshot"
ALREADY_STAGED = "Already staged"
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Proposal:
    id: str
    item_id: str
    field: str
    expected: object
    desired: object
    identity_digest: str
    managed_digest: str
    status: str
    media_type: str | None = None
    rationale: str = ""
    confidence: str = ""
    evidence: tuple = ()
    write_path: str = ""

    @property
    def key(self):
        return self.item_id, self.field


@dataclass(frozen=True)
class ReviewDocument:
    source: dict
    genre_policy: dict
    proposals: tuple[Proposal, ...]


@dataclass(frozen=True)
class ReviewRow:
    proposal: Proposal
    author: str
    title: str
    current: object
    capability: str
    disposition: str


def _require(condition):
    if not condition:
        raise ValueError


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _names(value):
    return isinstance(value, list) and all(_text(name) for name in value)


def _hash(value):
    return isinstance(value, str) and bool(SHA256.fullmatch(value))


def _unique_object(pairs):
    result = dict(pairs)
    _require(len(result) == len(pairs))
    return result


def _genre_policy(policy):
    _require(isinstance(policy, dict))
    for flag in ("allow_multiple_genres", "must_include_broader_genre"):
        _require(type(policy[flag]) is bool)
    for key in ("controlled_genres", "exclude_media_format_as_genre"):
        _require(_names(policy[key]))
    parents = policy["genre_parent_rules"]
    controlled = set(policy["controlled_genres"])
    _require(isinstance(parents, dict) and set(parents) <= controlled)
    _require(all(_names(names) and set(names) <= controlled - {child} for child, names in parents.items()))


def _validate_values(field, expected, desired, policy):
    if field in FIELDS:
        if field == "series":
            # Bound decimal strings before the existing Decimal normalizer sees them.
            for entries in (expected, desired):
                _require(isinstance(entries, list))
                for entry in entries:
                    _require(isinstance(entry, dict))
                    number = entry.get("sequence")
                    _require(number is None or isinstance(number, str) and
                             bool(re.fullmatch(r"[0-9]{1,32}(?:\.[0-9]{1,32})?", number)))
            names = [entry.get("name") for entry in desired]
            _require(_names(names) and len(set(names)) == len(names))
        if field not in ("title", "subtitle") or expected is not None:
            if field == "title":
                _require(isinstance(expected, str))
            else:
                validate_target(field, expected)
        validate_target(field, desired)
        if field in ("authors", "narrators"):
            _require(bool(desired))
    elif field == "genres":
        _require(expected is None or _names(expected))
        _require(_names(desired) and bool(desired))
        genres = set(desired)
        _require(len(genres) == len(desired) and (policy["allow_multiple_genres"] or len(genres) == 1))
        _require(genres <= set(policy["controlled_genres"]) and
                 not genres.intersection(policy["exclude_media_format_as_genre"]))
        if policy["must_include_broader_genre"]:
            _require(all(set(policy["genre_parent_rules"].get(genre, ())) <= genres for genre in genres))
    elif field == "description":
        _require((expected is None or isinstance(expected, str)) and isinstance(desired, str))
    else:
        _require(expected is None or type(expected) in (str, int))
        _require(type(desired) in (str, int) and bool(re.fullmatch(r"[0-9]{4}", str(desired))))


def _proposal(row, policy, existing):
    _require(isinstance(row, dict))
    field, status = row["field"], row["status"]
    _require(field in (*FIELDS, *NATIVE_FIELDS))
    _require(_text(row["item_id"]) and _hash(row["identity_digest"]) and _hash(row["managed_values_digest"]))
    expected = row["expected_original_value"]
    desired = row["already_staged_desired_value" if existing else "complete_desired_value"]
    _validate_values(field, expected, desired, policy)
    args = (row["item_id"], field, expected, desired, row["identity_digest"], row["managed_values_digest"], status)
    if existing:
        _require(status == EXISTING)
        return Proposal(digest(["existing", row["item_id"], field]), *args)
    _require(status in STATUSES and row["media_type"] in ("audiobook", "ebook"))
    _require(status != STATUSES[1] or field == "description")
    _require(status != STATUSES[2] or field == "narrators")
    _require(all(_text(row[key]) for key in ("proposal_id", "rationale", "confidence", "write_path")))
    _require(isinstance(row["evidence"], list) and all(isinstance(e, dict) for e in row["evidence"]))
    return Proposal(row["proposal_id"], *args, row["media_type"], row["rationale"],
                    row["confidence"], tuple(row["evidence"]), row["write_path"])


def parse_review(data: bytes) -> ReviewDocument:
    """Parse only the pilot format. Error messages omit uploaded values."""
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError("Select a review JSON up to 5 MiB.")
    try:
        document = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        # Reject non-finite numbers and invalid Unicode even in evidence and unused advisory data.
        compact(document).encode("utf-8")
        _require(isinstance(document, dict))
    except (ValueError, RecursionError):
        raise ValueError("Invalid review JSON.") from None
    if (document.get("document_type"), document.get("document_version")) != (DOCUMENT_TYPE, DOCUMENT_VERSION):
        raise ValueError("Unsupported review document type or version.")
    try:
        source = document["source_export"]
        _require(document["import_ready"] is False and isinstance(document["application_safety"], dict))
        _require(source["application"] == "abs-metadata-manager" and type(source["schema_version"]) is int
                 and source["schema_version"] == 1)
        _require(_text(source["library_id"]) and _hash(source["library_scope_fingerprint"])
                 and _hash(source["content_digest"]))
        _require(type(source["snapshot_id"]) is int and 0 < source["snapshot_id"] < 2**63)
        _require(type(source["book_count"]) is int and source["book_count"] >= 0)
        _require(type(source["offline_import"]) is bool)
        _require(all(_text(source[key]) for key in ("snapshot_started_at", "snapshot_completed_at")))
        policy = document["genre_policy"]
        _genre_policy(policy)
        _require(all(isinstance(document[key], list) for key in ("recommendations", "existing_staged_corrections")))
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid review provenance, genre policy or recommendation lists.") from None
    proposals = []
    for key in ("recommendations", "existing_staged_corrections"):
        for number, row in enumerate(document[key], 1):
            try:
                proposals.append(_proposal(row, policy, key == "existing_staged_corrections"))
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Invalid field, status or value in {key} entry {number}.") from None
    return ReviewDocument(source, policy, tuple(proposals))


def _value(book, field):
    return book.values[field] if field in FIELDS else book.metadata.get(NATIVE_FIELDS[field])


def _staged(store, source_id, active_id):
    staged = defaultdict(list)
    for row in store.corrections(active_id):
        if row["status"] not in ("discarded", "observed_in_abs"):
            staged[row["item_id"], row["field"]].append(row["proposed"])
    for aid in {source_id, active_id}:
        for row in store.findings(aid):
            if row["actionable"] and (row["status"] in ("approved", "applied") or
                    row["status"] == "pending" and row["kind"] in ("confirmed_target", "manual", "external")):
                staged[row["item_id"], row["field"]].append(row["target"])
    return staged


def _disposition(p, source, active, staged, may_stage, duplicate):
    if duplicate:
        return "Duplicate or conflicting proposal"
    if source is None:
        return "Book missing from source snapshot"
    media_type = "audiobook" if source.is_audio else "ebook"
    if (identity(source) != p.identity_digest or p.media_type not in (None, media_type)):
        return "Source item identity mismatch"
    if digest(source.values) != p.managed_digest or _value(source, p.field) != p.expected:
        return "Source metadata differs from proposal"
    if active is None:
        return "Book missing from current snapshot"
    if identity(source, p.field) != identity(active, p.field) or active.is_audio != source.is_audio:
        return "Current item identity changed"
    if p.expected == p.desired:
        return "No change"
    if target_observed(p.field, p.desired, _value(active, p.field)):
        return ALREADY_APPLIED
    if staged:
        return ALREADY_STAGED if all(value == p.desired for value in staged) else "Conflicts with staged work"
    if p.status == EXISTING:
        return "Prior correction no longer staged"
    if _value(active, p.field) != p.expected or active.values != source.values:
        return "Current snapshot metadata changed"
    if p.field == "series" and not {s["name"] for s in p.expected} <= {s["name"] for s in p.desired}:
        return "Series membership would be removed"
    if p.field == "authors" and source.is_audio and any(a.get("role") or a.get("roles") for a in source.metadata["authors"]):
        return "Author roles require manual review"
    if p.status == STATUSES[1]:
        return "Editorial review outstanding"
    if p.field not in FIELDS or not source.is_audio:
        return "Native edit outstanding"
    if not may_stage or active.raw.get("isMissing") or active.raw.get("isInvalid"):
        return "Snapshot or item unavailable for staging"
    return HELD if p.status == STATUSES[2] else READY


def review_rows(store, active_id, document):
    """Check the saved source independently of the latest view. Bundle digest is provenance."""
    active = store.audit(active_id)
    provenance = document.source
    source_id = provenance["snapshot_id"]
    if (provenance["library_id"] != active["library"] or
            provenance["library_scope_fingerprint"] != digest([active["server"], active["library"]])):
        raise ValueError("Review document belongs to a different server or library.")
    try:
        source = store.audit(source_id)
    except IndexError:
        raise ValueError("The source snapshot is unavailable in this workspace.") from None
    if (source["status"] != "complete" or (source["server"], source["library"]) !=
            (active["server"], active["library"]) or source["started"] != provenance["snapshot_started_at"] or
            source["finished"] != provenance["snapshot_completed_at"] or
            bool(source["read_only"]) != provenance["offline_import"]):
        raise ValueError("Review provenance differs from the saved source snapshot.")
    saved = {b.id: b for b in store.books(source_id)}
    if len(saved) != provenance["book_count"] or any(b.raw["libraryId"] != source["library"] for b in saved.values()):
        raise ValueError("Review provenance differs from the saved inventory.")
    exact = source_id == active_id
    current = saved if exact else {b.id: b for b in store.books(active_id)}
    staged = _staged(store, source_id, active_id)
    latest = store.latest(active["server"], active["library"])
    may_stage = latest["id"] == active_id and active["status"] == "complete" and not active["read_only"]
    keys = Counter(p.key for p in document.proposals)
    ids = Counter(p.id for p in document.proposals)
    rows = []
    for p in document.proposals:
        book = saved.get(p.item_id)
        label = current.get(p.item_id, book)
        capability = ("Calibre / manual" if (book and not book.is_audio) or p.media_type == "ebook"
                      else "Guarded ABS" if p.field in FIELDS else "Native ABS")
        state = _disposition(p, book, current.get(p.item_id), staged.get(p.key), may_stage,
                             keys[p.key] > 1 or ids[p.id] > 1)
        rows.append(ReviewRow(p, ", ".join(label.values["authors"]) if label else "",
                              (label.values["title"] or "") if label else "Unavailable book",
                              _value(current[p.item_id], p.field) if p.item_id in current else None,
                              capability, state))
    return rows


def _selected_rows(store, active_id, document, selected, confirmed):
    rows = {r.proposal.id: r for r in review_rows(store, active_id, document)
            if r.disposition == READY or r.disposition == HELD and r.proposal.id in confirmed}
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= rows.keys():
        raise ValueError("Selection is no longer eligible. Review the current dispositions.")
    return [rows[pid] for pid in selected]


def stage_review(store, active_id, document, selected, confirmed=()):
    """Revalidate under the workspace lock and stage all selected fields in one transaction."""
    with operation_lock(store.path), store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = _selected_rows(store, active_id, document, selected, confirmed)
        for row in rows:
            p = row.proposal
            evidence = compact({"source": "Owner-selected AI recommendation", "proposal_id": p.id,
                                "bundle_digest": document.source["content_digest"], "status": p.status,
                                "recording_confirmed": row.disposition == HELD, "confidence": p.confidence,
                                "rationale": p.rationale, "evidence": p.evidence})
            store._confirm_targets(db, active_id, p.item_id, {p.field: p.desired}, evidence)
    return len(rows)


def check_live(store, adapter, active_id, document, selected, confirmed=()):
    """Explicit bounded GETs only. Does not update the snapshot or authorize writes."""
    with operation_lock(store.path):
        rows = _selected_rows(store, active_id, document, selected, confirmed)
        audit = store.audit(active_id)
        groups = defaultdict(list)
        for row in rows:
            groups[row.proposal.item_id].append(row.proposal)
        if len(groups) > MAX_APPLY_BATCH or adapter.server != audit["server"]:
            raise ValueError(f"Connect to the original server and select up to {MAX_APPLY_BATCH} books.")
        library = next((lib for lib in adapter.libraries() if lib["id"] == audit["library"]), None)
        if library is None or (audit["library_identity"] and library_identity(library) != json.loads(audit["library_identity"])):
            raise ValueError("Live library identity differs from the saved snapshot.")
        results = {}
        for iid, proposals in groups.items():
            saved = store.book(active_id, iid)
            try:
                live = Book(adapter.item(iid))
                guard(live, saved.identity, live.values)
            except (SafetyError, ValueError):
                results.update((p.id, "Live identity or item unavailable") for p in proposals)
                continue
            unchanged = live.values == saved.values and live.protected() == saved.protected()
            for p in proposals:
                results[p.id] = ("Matches saved snapshot" if unchanged else
                                 "Desired value already present in ABS" if target_observed(
                                     p.field, p.desired, live.values[p.field]) else "Live metadata mismatch")
        return results
