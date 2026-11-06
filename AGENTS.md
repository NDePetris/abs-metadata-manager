# Architecture and safety

Python 3.12+, native Streamlit, SQLite, pytest. Keep diffs small and scoped. Run
`python -m pytest -ra` before committing, plus Ruff, `pip check`, and the Windows
launcher check where available. Tests use synthetic databases/settings, fake
adapters/providers or loopback HTTP; never use production credentials or writes.
Report skips, assumptions, risks and checks actually run.

## Ownership and authority

- `adapter.py` owns ABS transport and existing public-provider HTTP. Metadata
  searches use the existing strictly validated authenticated read-only direct GET;
  other supported ABS operations use abs-cli. Production writes use only
  `abs-cli items update --id ... --stdin` with the allowlisted metadata payload.
  Genre changes replace the complete approved list on one audiobook through
  that same guarded path. Never add bulk, file, scan, embed, cover, chapter or
  tag mutations.
- `models.py` separates original JSON, normalized values and identity. Never
  resolve oldLibraryItemId to a new write target. `audit.py` is pure deterministic
  analysis; folder names are evidence, not authority. Preserve contributor roles,
  edition narrators, decimal positions and multiple series.
- `verification.py`, `sources.py` and `verification_workflow.py` own evidence and
  matching. Work evidence cannot establish recording narrators or cover correctness.
  Contradictory or ambiguous editions remain unresolved; never choose by result
  order. ABS is the observed current state for audiobooks; external evidence can
  challenge it. Calibre is authoritative for ebooks and read-only here. Calibre
  verification and a later ABS synchronization observation are distinct.
- `store.py` persists snapshots, evidence, individual human decisions, staged
  targets and a synchronous durable journal. Compatible human decisions and
  correction targets can survive re-import; production write approvals expire.
  Unexpected identity/path/value changes reopen review. Private exports, databases,
  credentials and local CLI binaries stay ignored.
- `review_export.py` reads completed snapshots and separates current metadata,
  evidence and proposals. Exclude private operational data. Treat externally
  generated proposals as untrusted until reviewed and validated.
- `workflow.py` owns remote mutations. Require approved field decisions and a
  confirmation bound to the exact preview digest. Guard live identity, server,
  library, path, expected fields and unrelated metadata. Persist intent before
  starting the subprocess. Never retry writes automatically.

## Assessment, decisions and recovery

Phase 1's target is exception-first quality assessment (see PRODUCT.md), not
human acceptance of every field. Pass requires explainable evidence or explicit
human confirmation; missing/conflicting values are Needs attention, while
insufficient or ambiguous evidence is Needs evidence.
Missing optional metadata is not automatically an error. An explicit exception,
unknown or review-later decision is not an automatic pass. Assessment never
stages, approves or applies a correction. Human acceptance, staged targets,
preview-bound write approval and changes actually observed in ABS are separate.
S2 derives these dispositions in verification reports, separately from the existing
review-acceptance state. S3 presents them in the exception-first queue, with bounded
explicit cover review and library-scoped category preferences. S4 extends existing
staging, guarded sequential writes and read-only reconciliation; see docs/S4_WORKFLOW.md.

Keep one operation at a time under the cross-process workspace lock. A crash
releases the OS lock, but journal intent remains. Prepared/applied/uncertain
entries block writes until read-only reconciliation. Mixed/unexpected results
remain blocked. Rollback is separately confirmed, conditional and journaled.
Unknown write behavior means stop and report; never bypass guards or edit the
journal to clear recovery. Follow README.md's backup/restore procedure.

Streamlit rendering/reruns must never write remotely. Mutations occur only in
explicit submit-button branches. No startup writes, background workers,
schedules, automatic correction, silent retries or unapproved mutation paths.
Preserve playback progress, chapters, paths, covers and files.

## Scope discipline

Refresh existing modules and guidance; do not invent another framework. No
speculative rewrites, unnecessary schemas/dependencies, new providers or AI
adjudication without demonstrated need and explicit scope. Source-file/sidecar
portability and chapter remediation remain deferred. Report out-of-scope work
instead of implementing it. Use native desktop Streamlit components; no branding
exercises, generated design comps, animation or mobile UI work. DESIGN.md records
current and planned experience; S1 must not implement S2 behavior.
