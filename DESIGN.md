# Design

Practical native Streamlit desktop workspace. Keep the configured light theme;
no custom CSS, animation, branding assets, generated comps or mobile-specific UI.
Desktop acceptance sizes are 1024×800 and 1280×800.

## Current implementation

The collapsed sidebar contains Review (default), Library, Activity and Settings,
with a single global Library selector and connection status. Review shows the latest
completed import date/count and a deliberate Refresh Library action. It does not
fetch metadata merely by opening or filtering the workspace.

Review starts with a native correction table after **Import AI suggestions**. The
uploader and provenance are collapsed. One row represents one proposed field;
Current and Suggested description values are abbreviated, while the exact text is
retained for the detail editor and payload. Search, field/author/status/confidence
filters and book/author/field/priority sorting operate on the same table. Durable
session selection uses proposal IDs, independent of visible row indices. Bulk
selection affects only visible eligible rows; hidden selections remain and are
counted explicitly. Editorial descriptions require individual selection; held
narrators require explicit recording confirmation and are excluded from bulk.
Upload confidence is advisory, not independently verified quality.

**Recommendation details** opens one book's suggestions and contributor/series
context. Full descriptions compare side by side and permit editing before staging.
Evidence/raw values are collapsed. Editing and checkbox selection do not stage or
approve anything. **Stage selected changes locally** calls the existing importer
with its locked validation; imported recommendations remain untrusted.

**Stage and apply** opens for outstanding work and reuses the existing guarded
application controls. Its field table uses the same selection behavior. One chosen
staged book renders its editors; collapsed sections no longer create every book's
editor eagerly. Field approval, exact preview/payload, digest phrase, acknowledgement,
ten-book cap, journal, live guards, readback and separately confirmed rollback remain.
Write-verified field counts derive from consumed findings; later inventory observation
remains separate. Calibre correction and ABS synchronization are distinct.

**Quality exceptions / manual metadata review** loads assessment only when requested.
It preserves the exception queue, Current / Reference / Desired editor, Previous/Next,
explicit evidence acquisition, edition/narrator review, saved-cover gallery (up to 12),
scoped category preferences, evidence-derived proposals and owner completion export.
No report is generated during unrelated recommendation table reruns. Existing high
confidence quality passes and advanced tools retain their semantics.

Library contains refresh/resume/import comparison, export for ChatGPT, the offline
sample and diagnostics. Activity contains history, historical snapshots, journal
inspection, reconciliation and guarded rollback. Unresolved recovery warnings remain
prominent on every page. Normal pages use the latest completed inventory; a newer
incomplete attempt still blocks staging/approval through existing service guards.

This is a bounded consolidation, not a unified evidence engine. Evidence-derived
proposal review remains under Quality exceptions, and its individual editor remains
the established typed field editor. Series filtering and table-cell-driven detail
navigation are deferred; explicit native detail selectors are used. No custom CSS,
new routing system, schema, dependency or writer capability was introduced.

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
