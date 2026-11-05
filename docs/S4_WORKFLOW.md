# S4 library correction workflow

Implemented on `codex/s4-library-corrections`, based on `9d04c75` (merged S3 PR #20).
The owner requested S4A/B and reconciliation in one session, superseding the older
issue descriptions' separate-session sequencing. No production database, credentials,
library metadata or operation 25 was opened or changed. #16 requires owner validation;
#17 remains deferred. This document describes software capability, not production completion.

## Operator path and issue mapping

1. Select the library and its completed inventory. Match & Verify continues to
   assess correct fields automatically through S2. No accept-every-field step is added.
2. Open **Library correction proposals**. Every book is counted exactly once:
   Already acceptable, Proposed corrections (including existing staged work), Needs
   more evidence, or Manual/native remediation. A proposed book can still have
   unrelated evidence/artwork gaps; categories are next actions, not full quality passes.
3. Filter proposals by media, field and evidence strength. Inspect exact current and
   desired values and the collapsed source/identity details. Native dataframes support
   sorting, column hiding, searching and CSV export. The global selector scopes the library.
4. Select supported audiobook fields; inspect the selected before/after table; press
   **Accept selected local targets**. This atomically records existing per-field human
   decisions and stages differences. It does not approve a write. Unselected proposals
   remain unresolved. Existing staged fields are suppressed from new proposals.
5. In **Changes**, select staged fields together, approve them for preview, inspect
   the exact selected items/fields/payload, type the digest phrase, check the explicit
   acknowledgement and submit. At most ten books are permitted. The selected/excluded
   field counts remain visible; ebook/manual corrections stay in the individual panel.
6. Each item is guarded and journaled separately. Stop on any failed/unsafe/uncertain
   result. Saved batch results distinguish Verified applied, Failed without application,
   Blocked, Uncertain, Reverted and Not attempted, including after restart.
7. Use **Observe external changes (read-only)** for 1–25 selected books, or Import
   for a full membership check. Revalidate Calibre separately. Export **Library
   completion and owner sign-off** and resolve remaining exceptions before #16 sign-off.

- **#21:** proposal and inventory views, evidence/identity provenance, local grouped
  staging, stale-plan rejection, idempotent suppression and existing evidence acquisition.
- **#14:** bounded explicit ABS observation using import reconciliation; correction
  history and changed-value/identity reopening; cover/category observations retain S3 semantics.
- **#15:** separate Calibre comparison/sync states, fresh-catalog invalidation of
  previously verified targets and strict existing library/ID/UUID bindings. Native steps below.
- **#22:** existing sequential writer extended with cap, protected-state/evidence
  preview binding, per-item revalidation, approval expiry and journal-derived batch results.
- **#16:** exportable quality, acceptance, correction, sync, coverage, artwork,
  category and recovery counts plus owner checklist. Actual owner sign-off remains open.

## Reused components and safety

`correction_plan.py` projects existing `verification_report` fields/matches and audit
findings; it is not another matcher. `correction_plan_ui.py` adds native controls to
Match & Verify. No schema, provider, framework, scheduler or dependency was added.
The small `_confirm_targets` extraction permits one SQLite transaction for grouped
staging while preserving existing field validation, decision history and correction rows.
The journal remains the only write ledger; each operation stores its bounded batch
preview so results can be reconstructed without another batch state machine.

Proposal fingerprints include server/library, snapshot, identity, current and desired
values and the full cached evidence/decision report. Canonical item order prevents a
display-sort change from invalidating the plan. Under the workspace OS lock, all
selected targets must still match the regenerated plan before any local stage commits.
Partial/unavailable provider attempts and contradictory recording identities cannot
produce grouped recording-backed targets. Failed searches retain compatible earlier
evidence; they do not fabricate a different value or silently erase captured evidence.

Write previews bind exact finding IDs, approval timestamps, full managed before values,
protected metadata, effective captured evidence, decisions and targets. Empty explicit
selections, duplicates, unsupported/unapproved/stale fields and batches above ten fail
closed. The existing default empty single-item preview remains a harmless no-op for
backward compatibility. Effective failed-search evidence is intentionally excluded from
write fingerprint churn when it adds no observation; new successful/partial evidence
and candidate decisions invalidate the preview.

One cross-process lock covers the whole execution. Before each item, the original
server/library, approvals/targets/evidence, item/path, all managed values and protected
metadata are rechecked. Intent is synchronously persisted before `adapter.update`;
the production adapter still uses only `abs-cli items update --id ... --stdin` with
allowlisted metadata. Readback must match requested values and protected state.
There is no retry of a write. Failures reset remaining approvals; already verified
operations stay verified. Prepared/applied/uncertain journals block subsequent writes
until existing read-only reconciliation. Rollback remains separately conditional,
confirmed and journaled. Failed preflight produces no update; interrupted readback
remains uncertain. A process crash before first intent implies no submitted item;
after intent, recovery uses the journal and never infers success.

The former language-change regression allowed a write after a protected value drifted.
It now requires refusal before submission, preserving its subsequent re-import/reopen
assertion. No existing protection or test was relaxed to accommodate S4.

## Read-only observation and authority

Selected observation validates every requested read and identity before creating a
new snapshot. Other books are copied with explicit `fresh_abs=False` provenance;
they are not fresh observations. New snapshots expire write approvals. This bounded
refresh cannot discover unrelated new/removed inventory; use a full Import for that.
Unresolved journals block bounded refresh, and full import cannot close correction
rows for unresolved item operations. Production operation 25 must be reconciled by
the owner through History, never cleared through native edits or database manipulation.

Only observed target values close corrections. A later changed value or identity
reopens review, retaining historic timestamps. Write readback verification and
subsequent inventory observation remain separately reported. Work evidence cannot
verify recording narrators or artwork. Missing optional metadata remains optional.

For ebooks, record a desired correction, edit it in native Calibre, and mark it edited
as a reminder. **Validate Calibre Changes** lists the configured catalog read-only;
the library context, numeric ID and unique UUID must remain compatible. A reported
edit is not validation. Fresh catalogs can reopen formerly verified targets. A
catalog discrepancy without an accepted validated correction says to review authority
or sync, rather than asserting that Calibre must already be correct.

After validating Calibre, use the appropriate native ABS library scan/refresh for
the owner's metadata precedence settings, then explicitly observe ABS. This app
does not start scans or assume their behavior. Fresh ABS agreement and earlier Calibre
verification have separate timestamps. The standard catalog exposes no trustworthy
cover bytes/custom-column mapping, so cover synchronization is unavailable; no image
or category equivalence is guessed. Calibre category/cover writers remain absent.

For native ABS cover edits, observe metadata and explicitly retrieve the cover again
in S3's gallery. Changed path/version invalidates prior review; changed bytes at the
same path require retrieval to detect the hash. A saved path is never artwork proof.
Fresh genres and tags use existing library-scoped preferences and hygiene analysis.
There is no custom cover, genre/tag, file, chapter or ebook writer.

## Synthetic benefit and performance

Reproduce with `.venv/Scripts/python.exe scripts/demonstrate_s4.py`. It constructs an
isolated 100-audiobook/650-ebook library, fake provider, scoped synthetic Calibre catalog
and fake writer. No app settings or real services are used.

| Result | Count |
| --- | ---: |
| Books accounted for | 750 |
| Independently verified current fields | 330 |
| Calibre-authority field passes (separate basis) | 2,400 |
| Optional-absence passes (not independent verification) | 3,760 |
| Specific proposed field changes | 35 |
| Group-eligible recording-backed proposals | 30 |
| Suggestions requiring individual inspection | 5 |
| Ambiguous audiobook evidence cases | 10 |
| Local staging submissions: previous per-book / grouped | 30 / 1 |
| Explicit successful write-batch authorizations | 3 |
| Simulated guarded items verified | 30 |
| Additional interrupted-batch states | 1 Uncertain, 1 Not attempted |

All 750 books still have uninspected artwork; zero full-book passes are claimed.
The next-action categories are 35 proposal books, 10 needing more evidence and 705
manual/native books. Manual artwork obligations overlap all categories. Staging and
synthetic command returns are never counted as production correctness. The uncertain
simulation submits one fake write and prevents the second. Recovery blocks further
writes without retry. The 30 successful items use 30 separate intent/readback operations.

Measured Windows/Python 3.13.14 observations: rich-fixture report 0.526 s and plan
projection 0.127 s. Existing `scripts/profile_review.py --ui` measured 750 books,
setup 11.991 s, report 0.716 s / 13 SELECTs, queue 0.007 s, first render 3.035 s,
rerender 1.200 s. S3's recorded categorized run was 0.591 s / 13 SELECTs, first
render 2.250 s and rerender 0.759 s. These are separate runs, not a controlled speedup
claim: added proposal/report/export controls increase rendering cost. S3 batching
remains intact. Calibre import now reads corrections once per catalog, instead of once
per ebook. No live-library, provider or installed-Calibre performance is inferred.

## Validation and owner acceptance

The synthetic suite covers selection/exclusion, atomic failure, deduplication, stale
evidence/imports, missing narrators, contradictory editions, Edgedancer, whole-library
accounting, exact preview binding, protected preflight, stop/no-retry, restart history,
native partial edits, changed covers/categories, Calibre UUID replacement and ABS lag.
Existing transport, single-item, rollback, concurrency and recovery suites remain.
The final delivery records actual pytest, Ruff, dependency, launcher and CI results.
Final local Windows/Python 3.13.14 full run: **400 passed, 4 skipped in 112.76 s**.
Ruff across the repository, `pip check`, `start.ps1 -Check` and `git diff --check`
passed. GitHub Actions is a separate check, recorded on the delivery PR.
The independent visual review scored its recapture coverage issues resolved after
reopening six correctly sized desktop images; no additional fix was required for
that coverage list. This is not keyboard/contrast measurement or production validation.
Four local skips are expected: three official-CLI cases protect an existing personal
OS profile; the private historical export is excluded. Skips are coverage gaps.
The initial sandbox fixture run failed before tests because its temp directory was
inaccessible; completed runs used normal temporary/loopback access with isolation and
the suite's external-network guard intact.

Browser fixtures: `.venv/Scripts/python.exe -m streamlit run scripts/visual_s4.py
--server.port 8506`. Synthetic desktop captures at 1024×800 and 1280×800 cover proposals
and Changes, including selected local targets, exact two-item previews and unsubmitted
write confirmation. See [selected proposals](screenshots/s4-proposals-1024.jpg),
[batch preview](screenshots/s4-changes-1024.jpg) and
[separate confirmation](screenshots/s4-confirmation-1280.jpg).
AppTest covers no-click/rerender, grouped local staging and individual
workflow regression. No production write or real cover/provider validation is claimed.

Owner acceptance steps, in order:

1. Stop workspace users and follow README's SQLite backup/restore procedure. Keep
   one active workspace/server. Preserve the existing baseline and all later journals.
2. Open History on the original server. Reconcile operation 25 read-only. If mixed
   or unexpected, stop; keep writes blocked and record the issue in #16.
3. Review both libraries' current inventory totals and source coverage. Refresh only
   deliberately. Export aggregate completion reports privately; compare actual totals.
4. Acquire remaining evidence using existing explicit bounded batches. Inspect wrong
   or ambiguous editions individually. Check narrator roles and multiple decimal series.
5. Inspect missing/questionable artwork and unwanted categories; fix them in native
   ABS, then observe and retrieve covers again. All essential exceptions must resolve
   with adequate evidence or a documented compatible human confirmation.
6. Validate several ebooks across distinct Calibre libraries, IDs and UUIDs. Test a
   correction in Calibre, validate its catalog, verify ABS remains separately pending,
   perform a native refresh/scan and observe ABS agreement. Record unavailable cover
   and custom-column comparisons instead of assuming synchronization.
7. Only after separate owner authorization, use the existing live single-item acceptance
   and rollback checklist in LIVE_VALIDATION.md. Check unrelated metadata, files,
   chapters, covers, paths and playback progress directly in ABS. Do not inject faults.
8. Review a conservative selected batch, approve only shown fields, confirm its digest
   and apply. On any uncertainty stop and reconcile read-only. Check saved results and
   fresh ABS values; do not retry automatically or bypass an unresolved journal.
9. Export final aggregate reports. Record unresolved exceptions, remaining evidence and
   artwork limitations separately from execution/sync. Record the owner's actual outcome
   in #16; only the owner can declare Phase 1 complete. #17 remains open/deferred.
