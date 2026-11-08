# Design

The bulk rebuild uses one desktop screen with native browser controls, a paginated
differences table, inline value editing, one explicit confirmation, and progress/results.
No design system or additional workspaces. The prior experience below remains
reference for the legacy fallback; it does not govern the new bulk architecture.

Practical native Streamlit desktop workspace. Keep the configured light theme;
no custom CSS, animation, branding assets, generated comps or mobile-specific UI.
Desktop acceptance sizes are 1024×800 and 1280×800.

## Current implementation

The collapsed sidebar contains the single global Library selector, Import,
Match & Verify, Changes, History and Settings, plus connection status. Normal
pages use the latest completed inventory; historical snapshots are advanced tools.

Import shows **Export metadata for ChatGPT** beside the completed snapshot name,
date and book count. An explicit click prepares a local JSON download, including
when a newer import is incomplete.

Match & Verify starts with quality counts and a searchable exception selector,
ordered by disposition and essential defects, with quality/field/reason filters.
Historical manual acceptance is labeled separately. All books includes passing books
in stable title order. Essential bibliographic progress and artwork state are separate.
Previous/Next, compact candidate
selection and a Field / Current in ABS / Reference / Desired grid. Keep the three
value columns readable together. Current is an imported snapshot, not a live read.
Long text wraps; contributor order and multiple decimal series rows survive editing.
Fields start unselected. Editing/copying selects a field but does not confirm it.
Confirm or Confirm & Next accepts selected targets locally; changed targets enter
Changes without write approval. Advanced / Evidence holds provenance, raw evidence,
covers and source synchronization. Drafts are scoped to the exact browser session,
server/library/import/item; compatible durable human decisions can survive imports.

Changes groups corrections by book. Field approval, exact preview, phrase and
acknowledgement precede guarded execution. Calibre checks are separate and read-only.
History emphasizes activity, with journals/rollback advanced and unresolved recovery
prominent. Rendering/navigation perform no remote mutation.

Changes also includes a collapsed **Import AI corrections** panel with a grouped
recommendation table, prior corrections, and a native-edit checklist. Explicit
selection stages eligible fields locally. A bounded live comparison runs only
on request. See [review import](docs/REVIEW_IMPORT.md).

S2 adds Read-only batch evidence inside Advanced / Evidence: selected or
unattempted audiobooks, 1–25 books per invocation, explicit refresh, saved-run
resume/cancel and unavailable retry. Coverage describes acquisition outcomes,
separately from matches and quality. Ebooks retain existing Calibre comparisons.
S3 consumes the engine contract in [docs/S2_ENGINE.md](docs/S2_ENGINE.md).
An explicit gallery retrieves up to 12 saved covers and confirms only checked,
inspected images, bound to content hashes. Library genre/tag hygiene has exact-value
preferences and searchable affected-book lists. Both are collapsed native expanders
beside the queue; corrections stay in native ABS/Calibre. No retrieval occurs on render.
See [S3 delivery and limitations](docs/S3_REVIEW.md).

S4 adds collapsed Library correction proposals and Library completion / owner sign-off
panels beside the existing queue. Native tables show before/after and evidence strength;
grouped selection saves local targets only. Changes starts with a grouped staged-field
selector/table; individual editors remain in a collapsed panel. Exact preview approval
and guarded apply remain separate. Bounded external observation and Calibre validation
retain distinct authority/sync states. See [S4 workflow](docs/S4_WORKFLOW.md).

## Quality presentation (implemented in S3)

The exception queue becomes the primary workflow, ordered/filterable by quality
disposition and essential-field priority rather than requiring every field to be
accepted manually:

- **Pass:** strong explained evidence or explicit human confirmation supports the
  existing applicable value. Show which basis applies; neither grants write approval.
- **Needs attention:** required metadata is missing or conflicting; show the evidence
  and affected fields. Optional absence alone is not an error.
- **Needs evidence:** unavailable, ambiguous or inadequate evidence; make the gap
  explicit. Unknown, review later and explicit exceptions cannot silently pass.

Retain the compact Current / Reference / Desired editor for resolving exceptions.
Show explanations, source/capture provenance and work versus recording scope near
the decision. Work evidence cannot validate narrator credits. Keep assessment,
human confirmation, staged correction and observed authoritative changes distinct.

Integrate read-only cover gallery/review and genre/tag hygiene discovery into the
same exception flow. Presence of a cover is not correctness; do not introduce
cover/tag mutation controls. Calibre owns ebook corrections; later ABS observation
is separate. Uninspected files remain Not checked, not automatic outstanding work.

DESIGN.md is authoritative. The minimal `.impeccable/design.json` points here for
design tooling; it is not another token system or screenshot evidence registry.
