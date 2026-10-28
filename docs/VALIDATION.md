# Validation record

Development date: 2026-10-28, Windows x64, Python 3.13.14.

## Verified locally

- Official abs-cli 1.2.0 Windows x64 binary: `--version`, full help for list/get/update/libraries, tagged transport and config source, and published SHA-256 comparison.
- Actual CLI subprocesses against a loopback HTTP simulator: connection, version status, library list, paginated enumeration, expanded item parsing, approved update, verification, and guarded rollback. Simulator PATCH bodies contained only the approved metadata fields.
- Final automated suite: **82 passed in 95.45 seconds**. Ruff checks and `pip check` also passed. No skipped tests in this workspace's final run; both the private-export regression and the official CLI simulator ran.
- Streamlit AppTest: disconnected startup, all five views, repeated reruns with no CLI calls, read-only sample, individual approval selection, exact-preview confirmation, omitted checkbox refusing writes, verified mock apply, rerun idempotence, history, guarded rollback, and editing an approved target returning it to pending unless deliberately approved again.
- Windows launcher `start.ps1 -Check`, real Streamlit startup on 127.0.0.1:8501, and browser inspection of populated Library and Review views at 1440px desktop and the actual 663px app panel. Native sidebar collapses and tables scroll in the narrow panel.
- The private previous 110-book export was imported locally as a read-only regression snapshot. All 110 records parsed; rules generated 58 advisory proposals (26 title, 17 series, 8 authors, 7 narrators) and 68 read-only observations. This is fresh analysis, not a copy of old approvals or old journal entries.
- Private export, credentials, SQLite databases, and CLI binary are ignored by Git. Public tests synthesize the named regression cases.

## Safety cases covered

Metadata normalization, decimal positions 2.5/12.5/17.5, legitimate subtitles, contributors and multiple editions, old/new IDs, pagination boundaries and inconsistent responses, approval persistence and edits, audit invalidation, audit/preview/rerun write absence, source/path/library validation, stale-value refusal, exact preview fingerprint, pre-send durable intent, successful writes, timeouts before/after acceptance, interrupted/prepared recovery, mixed/unrelated-state blocking, selective guarded rollback, uncertain rollback reconciliation, null subtitle restoration, and cross-process operation exclusion.

## Not verified on production

- Your actual server's version, authentication/API-key permissions, library counts, response variants, cover retrieval, or metadata-update behavior.
- The effects of server plugins, concurrent external edits, scanner activity, or sidecar persistence in your deployment.
- No production PATCH, rollback, scan, file write, or filesystem rename was attempted.

Complete [LIVE_VALIDATION.md](LIVE_VALIDATION.md) to establish compatibility with your account and server. Passing the local simulator demonstrates the adapter protocol; it does not establish live production compatibility.
