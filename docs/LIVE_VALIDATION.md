# Manual validation against your Audiobookshelf server

Development made no production metadata writes. Follow the read-only checks first; the first write below is a deliberate action you perform in the app.

## Setup and read-only checks

- [ ] Have a recent backup of the server's Audiobookshelf `/config` and `/metadata` volumes.
- [ ] Launch `.\start.ps1`; open http://127.0.0.1:8501 in a desktop browser.
- [ ] In **Settings**, enter the actual server URL and an API key with access to your audiobook library. Select the detected official abs-cli 1.2.0 executable.
- [ ] Click **Connect & check compatibility**. Check the server URL, ABS version, CLI version, and warnings. Validate any version outside the CLI's declared 2.37.0–2.37.1 range cautiously; the app's production compatibility is not yet established.
- [ ] Select the audiobook library by its name and check its ID. ABS labels both audiobook and ebook libraries as `book`.
- [ ] Open **Library**, click **Audit whole library**, and wait for completion. Check that the total matches Audiobookshelf's current count (approximately 110; use your actual count).
- [ ] Confirm title, subtitle, multiple contributors, series positions, folder path, cover presence/preview, and chapter samples for several books against Audiobookshelf. Embedded evidence should reflect the tags ABS exposes.
- [ ] Review the two Dresden reimport cases and at least one Harry Potter recording. Confirm that differing editions and narrators are preserved.
- [ ] Check that chapter and cover observations offer no write action. Confirm no production metadata changed during connection, auditing, filtering, or browsing.

## Approval persistence and exact preview

- [ ] In **Review**, inspect a simple title-only correction, preferably `The Gate of the Feral Gods: Dungeon Crawler Carl, Book 4` → `The Gate of the Feral Gods` if still present.
- [ ] Approve only that one title field. Reject or leave all others pending. Close/restart the app and verify that this decision persists.
- [ ] Reconnect and select the same library. Select the same completed snapshot; do not start a new audit unless you intend to reset approvals.
- [ ] In **Apply**, check the server, library ID, current item ID, path, before/after values, and exact JSON payload. It must contain only `metadata.title` for this first test. Browsing the preview must change nothing remotely.
- [ ] If a refresh-token warning blocks writes, back up `~/.abs-cli/config.json`, configure an API key, and remove its nonempty `refreshToken` property manually. Reconnect. The app never makes this change for you.

## First explicit write

- [ ] In **Apply**, type the exact `APPLY <batch fingerprint>` phrase, tick the write confirmation checkbox, and press **Apply confirmed changes**. This is your explicit authorization for the fields shown.
- [ ] Verify the book directly in Audiobookshelf: corrected title; unchanged subtitle, contributors, series, cover, chapters, tags, files, and playback progress.
- [ ] In **History**, check `verified`, the original and applied values, persisted pre-update state, and observed result. If uncertain, stop here and use read-only reconciliation. Do not reapply the same operation.
- [ ] Reload and navigate the app several times; verify that no second update is sent and the applied field is no longer pending.

## Guarded rollback and stale-state checks

- [ ] Choose that verified operation in **History**. Inspect its guarded rollback payload, enter the exact `ROLLBACK <fingerprint>` phrase, check the confirmation, and press **Restore confirmed fields**.
- [ ] Verify the old title returned in Audiobookshelf, the operation is `reverted`, and unrelated metadata is unchanged. A fresh audit is needed for a new correction.
- [ ] For a later test, deliberately edit a reviewed field in Audiobookshelf before applying its old proposal. The app must refuse the stale change and stop before writing that book. Run a new audit rather than bypassing the guard.
- [ ] For a later verified operation, edit a primary field externally before rollback. The rollback must refuse to overwrite the external edit.
- [ ] Start another audit and confirm no old approval is silently retained. Interrupt an audit with Streamlit's Stop control and confirm its partial snapshot cannot be applied.

## Recovery without production fault injection

Timeout-before, timeout-after, crash recovery, mixed results, uncertain rollback, and blocked retries are covered by mocks. Do not create artificial production timeouts just to test them. If an uncertain operation occurs naturally, reconnect to its original server and use **Reconcile using live metadata (read-only)**. Matching after state becomes verified; matching before state becomes not_applied and resets approval; mixed/changed state stays blocked for manual inspection.

Only increase the explicitly selected batch after the single-book apply and rollback checks succeed.
