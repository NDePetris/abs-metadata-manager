# Description and journal performance

Based on main after PR #31 (`3d431e7`). No production inventory, settings,
recommendation files, credentials, or ABS requests were used for measurement.

## Cause and changes

`operation(id)` previously read and decoded every journal entry. History loaded
that history repeatedly for status checks, the activity list, the journal table,
and the selected operation. Changes also decoded every saved batch plan on every
rerun. These plans repeat full descriptions for up to ten books per journal.
Collapsed Streamlit expanders still constructed full tables, description editors,
raw JSON, and rollback previews. Recommendation filters reparsed the upload.

Separate standard-library `cProfile` runs of the same benchmark corroborated this:
full `history()` calls fell from 39 to 1 (the remaining call is synthetic import
setup before journals exist). Cumulative `json.decoder.raw_decode` time fell from
6.84 s to 0.24 s over setup plus all measured interactions. These profiled totals
are separate from the unprofiled per-operation medians below.

- Retrieve one complete operation by primary key. Keep `history()` compatible;
  filter `unresolved()` in SQL while still returning complete records.
- Use metadata-free history summaries and SQL existence checks for presentation.
  Filter journal states in SQL. Keep all states and all operations accessible.
- Load historical snapshot details, raw journal JSON, rollback previews, and saved
  batch results explicitly. Saved batch choices/results use SQL projections; only
  the chosen operation supplies the complete saved plan.
- Limit description overview cells and checkbox labels to a 200-character prefix
  with a truncation notice. Inspect complete original/proposed descriptions for a
  selected correction or recommendation, and original/intended/observed text for
  a selected operation. Disabled text areas preserve full whitespace and Unicode.
  Only the inspected description correction constructs a full description editor.
- Reuse only the last validated upload parse in session state, keyed by snapshot
  and upload SHA-256. Recompute dispositions against saved state on every rerun;
  staging and live checks retain their independent locked revalidation.
- Avoid repeated findings queries for each selected staged field.

No schema, dependencies, write transports, approval rules, journal transitions,
HTML-equivalence rules, or ASCII-escaped CLI serialization changed. Truncation
occurs only during presentation. Exact write/rollback previews, persisted records,
and apply/readback/reconciliation/rollback guards continue using complete values.

## Synthetic measurements

Run `python scripts/profile_descriptions.py` from a development environment.
The script creates temporary settings/database files and uses a fake adapter.
Use the same script on the baseline and modified checkout. Windows Python 3.13,
110 audiobooks, 55 staged description corrections and 55 unstaged editorial
description recommendations, 220 mixed
verified/uncertain/prepared/applied entries, 6,550-character Unicode descriptions,
and complete ten-book plans repeated in journals. Medians of five backend calls
and three Streamlit AppTest reruns; these are measurements, not CI thresholds.

| Operation | Before (ms) | After (ms) |
| --- | ---: | ---: |
| Load history for summaries | 315.05 | 5.18 |
| Retrieve selected operation | 325.77 | 2.17 |
| Build description comparison | 0.27 | 0.13 |
| Changes overview rerun | 1,478.40 | 380.77 |
| History rerun | 2,014.21 | 104.33 |
| Recommendation rerun | 77.56 | 67.80 |
| Filter recommendations by description | 84.72 | 67.37 |
| Select editorial recommendation for staging | 92.16 | 68.63 |
| Select full recommendation details | 92.03 | 72.30 |

| Serialized data | Before (bytes) | After (bytes) |
| --- | ---: | ---: |
| History query result JSON | 55,584,763 | 38,943 |
| Description comparison JSON | 20,172 | 829 |
| Changes element protobufs | 1,863,998 | 100,673 |
| History element protobufs | 1,577,199 | 69,031 |
| Recommendation overview element protobufs | 1,127,176 | 67,488 |
| Filtered recommendations element protobufs | 1,127,185 | 67,497 |
| Selected recommendation element protobufs | 1,127,183 | 67,495 |
| Selected recommendation details element protobufs | 1,147,810 | 128,233 |

Element protobuf sizes are the sum of AppTest element messages, a UI-payload
proxy rather than network traffic or browser paint measurements. Comparison CPU
was already small; its payload was the issue. Full selected recommendation details
remain similar in time because they deliberately expose complete values.

Remaining costs: Changes constructs per-correction selection widgets; recommendation
dispositions still reread and verify saved state; explicitly loaded saved batch
queries scan JSON in SQLite; exact previews and explicitly requested raw JSON can
still be large. These are deliberately retained rather than caching safety state
or introducing a schema or persistence layer.

## Validation

Final local Windows run on Python 3.13: `python -m pytest -ra --durations=10`
with an isolated synthetic base directory: **510 passed, 4 skipped** (26m 28s).
Three optional official-CLI tests require a clean OS profile; the private historical
export is excluded. The new focused regressions passed **5/5**. Ruff, `pip check`,
`start.ps1 -Check`, and `git diff --check` passed. The existing Windows full-suite
and Linux backend CI jobs also include the new description/journal regressions;
their results are reported on the PR.
