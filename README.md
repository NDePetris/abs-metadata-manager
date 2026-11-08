# Audiobookshelf Metadata Manager

A local Windows desktop workspace using Python 3.12+, native Streamlit controls, SQLite, and abs-cli.

See [PRODUCT.md](PRODUCT.md) for Phase 1 goals, [DESIGN.md](DESIGN.md) for current
and planned workflows, and [AGENTS.md](AGENTS.md) for implementation boundaries.

## Start

```powershell
.\start.ps1 -Install -InstallCli
.\start.ps1
```

The app binds to loopback at http://127.0.0.1:8501. No startup metadata requests or writes occur. Use Settings to connect, then select the library in the sidebar.

## Everyday workflow

The default **Review** workspace keeps correction review, local staging and guarded
application together. Select the library once in the sidebar.

1. **Review:** open **Import AI suggestions** to upload recommendations JSON. Search
   title/author, filter by field, author, workflow status or confidence, and sort by
   book, author, field or priority. Check individual rows or **Select eligible rows
   in view**. Hidden selections survive filtering and sorting; visible and total
   selected counts are separate. Confidence supplied by the upload is advisory.
   Bulk selection excludes editorial descriptions and narrator holds. Open
   **Recommendation details** for one book's suggestions, full description comparison
   and description editing. Evidence and source details start collapsed. Save edits,
   select descriptions individually, then **Stage selected changes locally**.
2. **Review → Stage and apply:** this section opens by default when pending work
   exists. Select staged fields in its table, **Approve selected fields for ABS
   preview**, inspect the exact before/after values and CLI payload, then enter the
   displayed phrase and acknowledgement. Submit **Apply confirmed changes** only
   after that review. The existing ten-book limit, live guards, durable journal,
   readback and recovery remain mandatory. A verified write and a later library
   observation are separate. The staged book inspector renders only one book's
   editors; ebook corrections and Calibre validation remain read-only here.
3. **Review → Quality exceptions / manual metadata review:** explicitly load the
   existing evidence assessment, searchable book queue and Current / Reference /
   Desired editor. Strong evidence can establish Pass without accepting every field.
   Confirm saves local targets; it never approves a write. Previous/Next, contributor
   roles/order, multiple decimal series, Review Later, provider searches, cover review,
   category preferences, library correction proposals and completion export remain
   available. Work evidence cannot establish recording credits. Evidence acquisition
   and saved-cover retrieval require explicit actions. Drafts retain their existing
   session/server/library/snapshot/book scope. Quality exceptions count separately
   from uploaded recommendations and staged corrections.
4. **Library:** refresh the inventory, resume interrupted imports and export metadata
   for ChatGPT. A newer incomplete import still prevents confirmation and writes.
5. **Activity:** chronological imports, decisions and write results. Historical snapshots
   and journals remain advanced tools. Recovery warnings appear on every page.
   Reconcile reads live state without retrying writes; rollback requires its own
   confirmation and unchanged applied state.
6. **Settings:** connection and Calibre configuration.

The scope of this first consolidation is AI recommendations, lazy book inspection,
and the existing guarded application flow. Evidence-derived proposals retain their
existing panel under Quality exceptions; they are not mixed into the AI table.
Series-specific filtering and clicking an arbitrary table cell to open details are
not implemented; use the explicit native detail selector. Uploads and edits live in
the browser session; save the original JSON privately to reopen it later.

File metadata and folder verification display **Not checked** unless reliable evidence exists. Missing file inspection is not itself an outstanding correction. ABS updates never establish file-embedded correctness.

### Export for ChatGPT review

Select your library in the sidebar and open **Library**. Check the snapshot
date and book count, then click **Export metadata for ChatGPT** and
**Download metadata review JSON**. This uses the existing completed snapshot
without refreshing ABS or searching providers. Review the file for private
descriptions or custom metadata before uploading. Treat ChatGPT suggestions as
untrusted until reviewed and validated. See the [bundle schema](docs/REVIEW_EXPORT.md).

## Existing local database

Stop the old app and follow [Backup, restore and uncertain writes](#backup-restore-and-uncertain-writes) for `data/metadata.sqlite3` (or `ABS_MM_DB`). Start this version with the same database path. Schema changes are additive; snapshots, generic annotations, accepted decisions, corrections, and recovery journals remain. Do not delete the database to bypass recovery. Generic legacy annotations and proposals never become human acceptance automatically.

For a cautious first inspection, set `ABS_MM_DB` to a private copy and `ABS_MM_SETTINGS` to a new private directory. Copy only the nonsecret `settings.json` into that directory (not any `.dpapi` files) before launching; this retains the server/library context while preventing saved-key reconnection. Review Review, Library, Activity, and Settings. Return to the original database for actual operation; a copied database has an independent lock, so do not operate both against the same server concurrently. No production write is necessary to inspect the redesign.

Fresh imports expire write approvals, while compatible human acceptance and accepted correction targets persist. Exact verified narrator corrections and library/UUID-bound Calibre identifier corrections preserve unrelated valid field acceptance. Unexpected identity/path changes reopen review. Old item IDs are never resolved to new write targets.

## Credentials and preferences

Nonsecret settings live in `%LOCALAPPDATA%\ABS Metadata Manager\settings.json`. API keys are stored in separate Windows user-scoped DPAPI-encrypted blobs, with no plaintext fallback and no additional credential dependency. The password input never displays the saved key. Forget saved API key deletes that server’s encrypted blob. Do not copy `.env` or personal abs-cli credentials into the repository; this UI no longer discovers tokens from them.

DPAPI protects against accidental file/Git disclosure. It does **not** reliably protect against an AI coding tool executing with the same Windows identity and sufficient access: that code may decrypt the key or access the application while it is using it. Isolate untrusted tooling under a separate restricted identity to create a meaningful boundary.

Credentials remain in process memory while used and enter the CLI through its child environment, never arguments. Automatic reconnection reconstructs valid saved settings; remote validation still requires an explicit action. Missing, inaccessible, corrupt or undecryptable saved keys leave startup usable and show a recovery message. In Settings, enter a new key to reconnect (and replace the encrypted blob), or choose Forget saved API key. Error messages withhold key contents; persistence never falls back to plaintext. Production development/testing must use synthetic credentials, separate settings directories, and fake or loopback transports.

## Safety and source authority

Audiobook title, subtitle, authors, narrators, series, description, genres, and publication year are writable through `items update --id CURRENT_ID --stdin`. The last three are staged through [Import AI corrections](docs/REVIEW_IMPORT.md). Explicit field approval, exact digest confirmation, live identity/path/value guards, unrelated-media verification, FULL-synchronous pre-send journals, sequential execution, and the workspace lock remain mandatory. Prepared/applied/uncertain entries block further writes pending reconciliation. No automatic write retries occur.

abs-cli 1.2.0 remains the validated writer. A configured abs-cli refresh token blocks writes because upstream authentication refresh can retry PATCH. This app never removes that token for you. Playback progress, covers, chapters, paths, and files are not written.

Calibre remains authoritative for ebooks. Configure the local executable and library folder, then validate with the fixed `calibredb list --for-machine` command. Numeric IDs are scoped to that library. Persistent library/ID/UUID bindings and corroborating folder/identifier plus work evidence prevent ambiguous verification. Accepted title and author targets are valid corroboration, including when both changed before the first comparison. Contradictory or ambiguous records remain unverified. For an initial ambiguous binding, use Review / Stage and apply / Advanced catalog input to explicitly associate the ebook with a catalog ID and UUID in the exact library context. Unscoped exports are archived evidence only; they cannot associate books or verify fields/corrections. Ebook review excludes narrators. Close Calibre if local access is busy. No unlock, repair, database editing, EPUB editing, or folder mutation is attempted. Advanced catalog upload remains available. Custom columns and unsupported metadata remain manual.

Automatic audio tag rewriting, remuxing, direct file inspection, renaming, new providers, scheduling, and AI adjudication are deferred.

## Validation

Fresh development environment (the launcher uses the same locked dependencies):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt -e '.[dev]'
```

```powershell
.\.venv\Scripts\python.exe -m pytest -ra --durations=10
.\.venv\Scripts\python.exe -m ruff check app.py abs_manager tests
.\.venv\Scripts\python.exe -m pip check
.\start.ps1 -Check
git diff --check
```

Tests use synthetic databases, fake providers and adapters, and the local HTTP simulator. UI tests replace the credential boundary with a fake. See [validation evidence](docs/VALIDATION.md) and [production safeguards](AGENTS.md).

CI runs on PRs and pushes to main. Windows/Python 3.12 runs the full suite, Ruff,
`pip check` and `start.ps1 -Check`; Linux/Python 3.12 runs focused transport,
audit, guarded-write, locking and recovery regressions plus `pip check`. Prior
full Windows runs took about 11–15 minutes, so Linux does not duplicate all
Streamlit UI tests. No services, production secrets, CLI download or Calibre
installation are needed. Both jobs install `requirements.lock.txt` with `.[dev]`;
pip resolution and `pip check` catch inconsistent declarations.

The harness clears inherited ABS/provider/Calibre/proxy environment settings,
sets temporary Python home, app database/settings/local-app-data and netrc paths,
replaces credential and CLI-config readers, and blocks Python DNS/connect/connect_ex outside
loopback. This is a regression guard, not an OS sandbox for arbitrary subprocesses.
Official CLI tests supply only synthetic tokens and loopback URLs and refuse to
execute if the actual OS profile contains CLI config; Windows' known profile
folder cannot safely be redirected by changing HOME/USERPROFILE.

`-ra` reports skipped tests; JUnit artifacts retain their reasons. Without the
official binary, the three `test_official_cli_*` tests in `test_adapter.py` skip:
provider/search GET contract, apply/readback/rollback simulator, and transient
read/resume protocol. Fake-adapter and direct-GET simulator tests still run.
The historical private-export test is excluded by default even if a local file
exists; its separate explicit opt-in is `ABS_MM_TEST_PRIVATE_EXPORT=1` and is
never enabled in CI. Skips are coverage gaps, not passes. Do not claim Actions
passed from local results. Recommend requiring both stable CI job checks in main
branch protection after the first successful run; this PR does not configure it.


To exercise the desktop fixtures without credentials or external requests:

```powershell
.\.venv\Scripts\python.exe -m streamlit run scripts/visual_fixture.py --server.port 8505
```

This harness uses only a fake ABS adapter, fake provider responses and `.test-tmp-compact` storage. See [compact-screen acceptance evidence](docs/COMPACT_REVIEW.md).

## Backup, restore and uncertain writes

For library-wide proposals, grouped targets, bounded guarded application, external
observation, completion reports and owner acceptance, see [S4 workflow](docs/S4_WORKFLOW.md).
Production operation 25 remains owner-controlled recovery work; synthetic validation
does not resolve it or complete Phase 1.

Stop every app process using the workspace before backup or restore. Wait for any
submitted operation to stop; interruption can leave durable intent requiring
reconciliation. Retain the entire database, not just snapshots: it also contains
decisions, evidence, approvals and write/rollback journals. The OS lock releases
on process exit; it does not resolve journal state.

Use SQLite's backup API to produce a consistent single-file copy. It includes
committed WAL contents; copying only a live `.sqlite3` file can omit them. Do not
delete `-wal`, `-shm` or `-journal` files. For a raw filesystem archive, stop all
users and copy the database and every present sidecar as one set. A SHM file is
coordination state, not a substitute for the WAL. Do not mix sets or copy them
independently while the database is active.

Example in PowerShell, with a **new private backup destination**. Substitute the
actual source if `ABS_MM_DB` is configured. This opens the source read-only,
refuses an existing destination and verifies the resulting backup without Store
initialization or remote requests:

```powershell
@'
import sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
target.parent.mkdir(parents=True, exist_ok=True)
with target.open('xb'):
    pass
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute('PRAGMA integrity_check').fetchall()
        if result != [('ok',)]:
            raise RuntimeError(result)
print('Consistent backup verified:', target)
'@ | .\.venv\Scripts\python.exe - 'data\metadata.sqlite3' 'data\backups\before-s1.sqlite3'
```

Also archive nonsecret `settings.json` from `ABS_MM_SETTINGS` or
`%LOCALAPPDATA%\ABS Metadata Manager`. Record the original database path and app
revision. Credentials are separate `.dpapi` blobs: retain them only in a protected
same-user Windows backup if desired, or deliberately omit and re-enter the key.
They are not portable credentials; never decrypt/export plaintext or commit any
backup. Local DB backup does not replace backups of ABS `/config` and `/metadata`
or the authoritative Calibre library.

Restore while stopped. First preserve the current database and sidecars as a
separate recovery set, even if damaged or uncertain. Verify the backup using the
integrity check above (or read-only `PRAGMA integrity_check`). Copy the verified
single-file API backup to a **new, unused** database path with no stale sidecars,
then point `ABS_MM_DB` there before launching. For example:

```powershell
$restoredDb = Join-Path $PWD 'data\restored-s1.sqlite3'
if (Test-Path -LiteralPath $restoredDb) { throw 'Choose an unused restore path.' }
Copy-Item -LiteralPath 'data\backups\before-s1.sqlite3' -Destination $restoredDb
$env:ABS_MM_DB = $restoredDb
.\start.ps1
```

Keep only one active workspace against the server. Restore matching settings
separately or reconnect explicitly. Inspect Activity, decisions and journals before
any operation. A backup preserves history only up to its capture time: never use
an older copy to bypass a later uncertain write. If later journals/decisions exist,
retain the newer recovery set and resolve that history first; do not merge SQLite
tables manually. If the latest intent is unavailable, stop and report the gap.

Prepared/applied/uncertain operations require **Activity → Reconcile using live
metadata (read-only)** against the original server. The workflow guards identity,
library/path, all managed values and unrelated metadata/media. Intended after-state
becomes verified; exact before-state becomes not_applied and resets its approval;
mixed/unexpected state remains uncertain and blocks writes. A failed readback stays
unresolved. Reconciliation updates the local journal but never resubmits the write.
Any later correction needs deliberate approval and a fresh preview/confirmation;
rollback is also a separately confirmed conditional write.

Description comparison accepts attribute-free `<p>` wrappers and one layer of HTML
entity encoding only when the complete text and paragraph boundaries match.
Plain-text paragraphs use blank lines; words, punctuation, whitespace within a
paragraph and other HTML formatting remain significant. The approved payload is
sent unchanged. Activity labels accepted representation differences **Semantically
equivalent**. For reported description operation **136**, reconnect to the original
server and explicitly reconcile that entry read-only: it can become verified only
if the live description, identity, managed values and protected metadata/media all
pass. This compatibility fix does not inspect or clear production operations on
startup, retry a write, or establish that operation 136 has been resolved.

CLI update payloads use ASCII JSON Unicode escapes to preserve approved characters
across Windows stdin code pages; JSON parsing restores the exact original text.
For example, `Roshar’s` must not become `RosharΓÇÖs`. Such corrupted text remains a
material difference, including during read-only reconciliation. This transport
fix does not repair existing ABS descriptions: any correction needs a fresh live
snapshot, explicit field approval and preview-bound confirmation. Do not retry an
uncertain operation to repair encoding.

A successful CLI submission/exit is not end-to-end verification. Require guarded
readback and the operator's ABS check, including unrelated state. The last recorded
subtitle incident is **operation 25**, an approved empty subtitle returned as
null. A scoped equivalence fix exists, but the operation remains uncertain in the
recorded evidence; S1 does not inspect or resolve production state. Follow
[the incident recovery steps](docs/PRODUCTION_FIXES.md#existing-database-and-operation-25)
and [live validation](docs/LIVE_VALIDATION.md). Complete reconciliation before
considering any separately authorized live write validation.
