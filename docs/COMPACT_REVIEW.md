# Compact review acceptance — 2026-11-02

Continues open [PR #4](https://github.com/NDePetris/abs-metadata-manager/pull/4) on `codex/streamlined-metadata-ux`, from `4441a76`. Functional hardening commit: `8429a35`. No merge. The working tree was clean before this refinement; no unrelated work was replaced.

## Functional changes

- Calibre comparisons retain a server/ABS-library/item identity binding to an exact Calibre library, numeric ID and unique UUID. Initial bindings require corroborating folder/identifier and compatible work evidence, or explicit human ID/UUID association. Accepted author and title targets count as compatible evidence, including simultaneous corrections before the first validation. Contradictory records, duplicate UUIDs, ambiguous matches and a different library context fail closed. Unscoped exports are archived only; they cannot associate books or verify fields/corrections. A small additive binding table retains identity independently of correction lifecycle. Calibre remains read-only.
- A single `review_fields` rule governs displayed fields, selection, target confirmation, acceptance progress and normal Changes entries. Ebooks exclude narrators; historical evidence remains available.
- Accepted targets, ABS readback/import observation, embedded files and folders remain separate. ABS success never verifies files or folders. Uninspected sources say **Not checked** in Advanced / Evidence and do not create actionable inspection tasks.
- Missing, inaccessible, corrupt and undecryptable DPAPI blobs leave startup usable. Recovery messages withhold secret contents. Settings can replace the encrypted key or forget it; valid saved credentials still reconnect without unsolicited requests. No plaintext fallback.
- The sidebar Library selector remains authoritative across Settings, Import, Match & Verify and Changes. Its state is scoped to the server, with a stable initialization from saved/current context. Drafts, selections and confirmations cannot cross library or snapshot boundaries.

## UI and reused backend

One native comparison grid replaces repeated field headings, saved-value blocks, per-field evidence expanders, per-field confirmation buttons and duplicate selected/all confirmation forms. The expanded provider dictionary and large edition-evidence form are replaced by a candidate selector, returned details, plain-language classification and shared actions. Technical IDs, timestamps, diagnostics, rejected editions, covers and source evidence share Advanced / Evidence.

Fields begin unselected. Editing/copy/restore selects just that field; Select all uses the applicability rule. Confirm and Confirm & Next submit exactly the displayed selected targets atomically through the existing `Store.confirm_targets`. Confirmation records acceptance or stages a pending correction, without write approval. Only successful confirmation advances. More review actions contains notes, Reopen Selected and Review Later.

The existing candidate decisions, verification tasks/cache, durable field decisions/corrections, audit reconciliation, preview, approval, digest confirmation, write journal, rollback and workspace lock are reused. `adapter.py` and `workflow.py` were not changed. No new dependency, frontend component or configuration layer was added. The supported Streamlit minimum now matches the native APIs used (1.66; the existing lock already pins 1.66.1).

Draft values live separately from disposable Streamlit widget keys. Their scope contains server, library, audit and item, with per-field entries. Restoration order is exact-session draft, accepted pending correction, imported current value. Reference operations, source switching, page navigation and temporarily selecting another book preserve them. A new import has a different scope. Native contributor controls preserve names, punctuation and ordering; series editors retain multiple entries and decimal positions. Null/absent imported values are not silently converted into empty write targets. Historical provider defaults are checked against original returned keys; null contributors are not empty donor lists. Copying a supplied identifier preserves unsupplied identifiers in the same draft. Structured edits apply to the latest field draft, so multiple edits in one rerun cannot overwrite each other.

## Browser measurements

The committed baseline was run from an ignored `git archive` of `4441a76`, with the same synthetic book/reference fixture and theme as the final build. The sidebar was collapsed for both captures. Viewport dimensions were set explicitly and verified from `innerWidth`/`innerHeight` and the saved JPEGs. Layout measurements came from rendered DOM bounding rectangles, not PDF page counts or AppTest.

The comparison boundary includes field review and its review actions: the old five-field form versus the new grid through the bottom of the unified action strip. Optional notes are disclosed in the new strip. The new grid alone is 465.59 px.

| Viewport | Before review + actions | After review + actions | Reduction | Five rows together | Confirm & Next visible | Horizontal overflow |
| --- | ---: | ---: | ---: | --- | --- | --- |
| 1280 × 800 | 2484.13 px | 513.59 px | **79.3%** | Yes | Yes; bottom 783.99 px | None |
| 1024 × 800 | 2484.13 px | 513.59 px | **79.3%** | Yes | Yes; bottom 783.99 px | None |

All Current / Reference / Desired columns remain readable together for the ordinary fixture. Main client/scroll widths were equal: 1270/1270 and 1014/1014. The sidebar must be collapsed for the one-viewport result; opening it intentionally reduces working width. Long titles, contradictions and complex contributors/series can require vertical scrolling.

The first inspection found excess height from row actions and sidebar occupation. Actions were consolidated, native layout gaps adjusted and the reference summary placed alongside its selector. The subsequent inspection confirmed the measured fit. Native labels, keyboard controls and focus behavior remain; statuses use words rather than color alone. This was a focused desktop inspection, not a formal screen-reader or contrast certification.

| Before | After |
| --- | --- |
| [1280 screenshot](screenshots/compact-before-1280.jpg) | [1280 screenshot](screenshots/compact-after-1280.jpg) |
| [1024 screenshot](screenshots/compact-before-1024.jpg) | [1024 screenshot](screenshots/compact-after-1024.jpg) |

[Original field controls](screenshots/compact-before-fields-1280.jpg), [complex book at 1024](screenshots/compact-complex-1024.jpg), [complex book at 1280](screenshots/compact-complex-1280.jpg), [recording uncertainty](screenshots/compact-uncertain-1024.jpg), [validation state](screenshots/compact-validation-1280.jpg), [ebook at 1024](screenshots/compact-ebook-1024.jpg), [ebook at 1280](screenshots/compact-ebook-1280.jpg), [provider switch with retained draft](screenshots/compact-provider-switch-1024.jpg), [individual contributor editing](screenshots/compact-contributors-1280.jpg), and [raw measurements](screenshots/compact-measurements.json).

## Validation

**232 tests passed in 647.34 seconds (10:47), with no skips.** A subsequent focused Calibre/verification run passed after tightening context-free imports to archive-only evidence. Ruff (`app.py abs_manager tests` and the visual harness), `pip check`, `start.ps1 -Check`, and `git diff --check` passed. The native UI detector returned no findings. The first full run found one legacy test that tried to click the disabled Confirm button before processing the checkbox rerun; that test now follows real interaction timing. The final complete run passed.

Real browser interaction exercised reference-copy beside an unsaved subtitle, fake-provider switching and edition rejection with a retained draft/book, Next/Previous restoration, validation failure without advancement, successful selected confirmation/advancement, pending corrections, accepted fields, no reference, work-only uncertainty, long titles, contributor ordering controls, multiple series with position 2.5, and ebook applicability. Sources in the visual harness are synthetic for every source choice. A successful selected confirmation created only a pending local subtitle correction and no write journal. Automated tests also cover rejected identities/provider refreshes and all existing guarded write/recovery boundaries.

Windows DPAPI encrypted/decrypted an isolated synthetic key, rejected a synthetic corrupt blob with a redacted recovery error, and forgot it successfully. No production credential was read for development. Tests use isolated settings, synthetic databases, fake sources/adapters and the local protocol simulator. No production ABS write, Calibre write, media rewrite, scan, file rename or folder rename was performed.

Run the reproducible harness with:

```powershell
.\.venv\Scripts\python.exe -m streamlit run scripts/visual_fixture.py --server.port 8505
```

It creates ignored synthetic storage under `.test-tmp-compact`. Do not point it at a production database.

## Existing-database inspection

1. Stop the old app and back up its SQLite database, including any WAL files if present. Keep the original database and journal history.
2. Launch this branch with the same `ABS_MM_DB` path (or the normal default) for additive migration. No metadata request or write occurs just by opening the screen. Valid saved keys may reconnect locally.
3. Open the native sidebar, choose Library, then Match & Verify. Collapse the sidebar for the full desktop grid. The latest completed import supplies Current; its date is in Advanced / Evidence.
4. Edit one value, move Next/Previous, and verify that the draft returns. Confirm selected unchanged fields to accept them, or changed fields to stage them in Changes. Neither operation approves or sends metadata writes.
5. For a disconnected private-copy inspection, set `ABS_MM_DB` to the copy and `ABS_MM_SETTINGS` to a separate directory containing only a copy of the nonsecret `settings.json`, with no `.dpapi` files. This preserves server/library context without saved-key reconnection. Do not run independent database copies against the same server concurrently.

## Limits

Drafts are browser-session state; closing/reloading the session is not durable draft storage. Accepted targets are durable. Long/complex books may scroll. Files and folders remain Not checked; no inspection/rewriting engine was introduced. Real installed-Calibre compatibility and live provider/production-library behavior were not tested. A contradictory existing Calibre binding is conservatively refused. The referenced `Audiobookshelf Metadata Manager.pdf` was absent from both the supplied attachment directory and project; the supplied request, committed UI and actual browser baseline were used instead.
