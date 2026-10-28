# Validation record

Development date: 2026-10-28, Windows x64, Python 3.13.14.

## Audit reliability update — 2026-10-28

- Full suite: **128 passed in 20.44 seconds**. Ruff (`python -m ruff check app.py abs_manager tests`) and `git diff --check` passed.
- Added interrupted/resumed snapshot coverage, process-restart recovery (including abandoned `running` status), repeated exhausted retries, membership/identity/path and folder changes, legacy schema migration, checkpoint completeness, lock exclusion, and permanent/malformed response failures.
- Streamlit AppTest covers saved counts, durable latest errors, Resume success, changed-library refusal and explanation, no remote requests on navigation/reruns, and ebook warnings without changing the selection. The native UI mechanical detector reported no findings.
- Official abs-cli 1.2.0 was checked against a synthetic loopback HTTP 502: it exits 2 and emits `API request failed: 502 Bad Gateway` on stderr. The new protocol regression exhausts three transient reads for the second book, preserves the first book, reopens the Store, resumes without re-fetching the first expanded book, and completes using GETs only.
- Tests cover credential/response-body exclusion from errors and rotating logs, operation/page context, request timeout configuration, exponential backoff, and single-attempt metadata updates. Existing approval, durable journal, reconciliation, rollback, and unrelated-metadata protection tests remain passing.
- The sandbox's temporary-directory/loopback restrictions prevented the first test invocation from completing normally. The final full suite ran with normal local permissions; transports used only fake adapters or a loopback simulator and synthetic tokens.
- Existing production SQLite progress was inspected using a read-only connection: audit #2 has 51 saved Audiobooks records; audit #3 has 56 saved Books records. Neither has the membership checkpoint required to prove unchanged membership. Their history is retained; the UI explains the one-time fresh-audit requirement. Recovery steps are in [README.md](../README.md#interrupted-read-only-audits).
- No production metadata request or production write was performed. Cloudflare connection routing is unchanged. No snapshot status, approval, production journal, or production metadata was edited during development.

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
