# Product

A personal Windows desktop workspace for ensuring every audiobook and ebook in
Audiobookshelf has accurate, clean metadata. Python 3.12+, native Streamlit,
SQLite and abs-cli. ABS supplies current audiobook state; Calibre is authoritative
for ebooks, which remain read-only here.

## Priorities and quality

Essential: title, authors, audiobook narrators, series/numbering where applicable,
and appropriate cover artwork. Secondary: subtitles, chapter information and useful
bibliographic details. Hygiene: remove unwanted/inconsistent genres and tags
without adding unnecessary metadata. Optional absence is not automatically an error.

Phase 1 targets exception-first assessment of the existing library. Strong,
explainable evidence may establish acceptable existing values automatically;
human acceptance of every field is not required for quality-review completion.
Missing, conflicting or ambiguous values require review. The existing real-library
baseline must not be recreated or overwritten as development setup.

Keep these states distinct:

| State | Meaning |
| --- | --- |
| Verified quality | Adequate evidence supports this exact value at the appropriate work/recording scope. |
| Human-confirmed quality | A person accepts the value with reasoning/provenance; this is not independent verification. |
| Missing or conflicting metadata | Required information is absent or evidence disagrees; review is needed. |
| Insufficient evidence | Quality is unknown or ambiguous; no automatic pass. |
| Staged correction | A locally accepted desired target differs from the observed value; it is not write approval. |
| Observed ABS change | Guarded readback/reconciliation establishes the intended result and protected state; command submission alone does not. |

Explicit exceptions, unable-to-verify and review-later decisions stay visible.
Assessment cannot stage, approve or execute corrections. Every supported ABS
mutation still requires explicit field approval and exact guarded execution.
Calibre verification and later ABS sync observation are separate.

## Roadmap and scope

- Phase 0 delivered Import, Match & Verify, Changes, History and Settings,
  persisted evidence/decisions, compact comparison and guarded recovery. Live
  validation is incomplete: production operation 25 remains uncertain in the last
  recorded evidence. Local simulation does not establish end-to-end production
  compatibility; see [production incident](docs/PRODUCTION_FIXES.md).
- Phase 1 S1 establishes CI, isolation, recovery guidance and this harness. It
  changes no matching, assessment semantics, schema or UI behavior.
- S2 supplies deterministic quality dispositions and bounded explicit evidence
  acquisition over existing snapshots; see [engine contract](docs/S2_ENGINE.md).
- S3 supplies the quality exception queue, explicit saved-cover gallery/review and
  library genre/tag preferences; see [S3 delivery](docs/S3_REVIEW.md).
  Cover/tag writers are not authorized by this roadmap. S4 remains deferred.
- Source portability (embedded tags, files, folders and sidecars), chapter
  remediation, new providers, AI adjudication and background work remain deferred.

## Definition of done

For S1: repeatable synthetic tests/lint/dependency/launcher checks, explicit skip
reporting, reproducible backup/restore and safe unresolved-write reconciliation,
and aligned guidance without feature implementation.

For Phase 1: each applicable essential field has explained verified or
human-confirmed quality, or a visible unresolved exception; no hidden unknowns
count as passes. Review completion is distinct from library correctness and from
correction completion. Secondary/hygiene findings remain actionable without
creating artificial requirements. Corrections count as completed only when the
authoritative source is observed and verified; any later ABS sync is tracked
separately. Production end-to-end validation must be recorded before claiming it.
