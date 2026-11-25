# Audiobookshelf Metadata Manager

Export your audiobook catalog, have an external reviewer research meaningful corrections, review them in a local HTML file, and apply the changes you explicitly confirm. Use Audiobookshelf Match for individual lookups.

## Install on Windows

Install Python 3.12 or newer. From the CLI worktree in PowerShell:

~~~powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\absmm.exe --help
$env:ABS_URL = 'https://your-abs.example'
$env:ABS_TOKEN = Read-Host 'Audiobookshelf API key'
$absmm = (Resolve-Path .\.venv\Scripts\absmm.exe).Path
& $absmm export --list-libraries
& $absmm export --library LIBRARY_ID
~~~

Set $env:ABS_LIBRARY to reuse a library ID. With one accessible book library, export can infer it. An editable install is convenient for this checkout. A regular pip install also includes the report template.

For HTTPS, ABS must present a valid certificate and chain trusted by Requests' default CA bundle. The CLI disables Requests' environment-based configuration, including CA bundle overrides. If TLS validation fails, record the error for live validation.

The CLI always uses the current OS user's ~/.absmm directory, typically %USERPROFILE%\.absmm on Windows. The path is independent of the current directory and the checkout. Keep the same OS account and data directory for all operations on your server. Back up this directory and never replace a journal with an older copy to clear a pending operation.

## Review and apply

1. Share only the printed export JSON and [PROMPT.md](PROMPT.md) with the external reviewer. Keep the private snapshot, manifest, report, approvals, and journal local. Keep ABS_TOKEN set in the review shell so the CLI can detect that credential if it appears in the corrections.
2. Run the commands below. Open the printed HTML path directly in your browser. Search for a book, author, or field, inspect exceptions, expand descriptions, and select the desired rows. Edit titles and descriptions as ordinary text. Enter JSON for lists and nullable values, for example ["Fantasy", "High Fantasy"] or null. After editing, you can deselect the row. Download approvals before closing the report.
3. Apply from an interactive terminal. Inspect the exact values shown and type the displayed APPLY phrase. Before the first eligible update on that server, confirm a current ABS backup. If a result is uncertain, run check to reconcile it before applying more changes.

~~~powershell
& $absmm review .\corrections.json
& $absmm apply "$env:USERPROFILE\Downloads\approvals-DIGEST.json" --limit 1
& $absmm check
~~~

The limit counts distinct selected books in ascending ABS item ID order, regardless of the order shown in the HTML report or approval file. The CLI groups selected fields into one update per book. Items beyond the limit are reported as unattempted. A stale live item is skipped. An uncertain response stops the run. Once the journal shows a resolution, inspect the results before starting another run.

Only after personally inspecting a still-unresolved operation in ABS, use the following command to explicitly abandon it. This records your reason and does not change ABS.

~~~powershell
& $absmm check --abandon OP_ID --reason 'Manual inspection and resolution details'
~~~

The tool guards the GET and PATCH with a local lock. ABS can still change between those requests. Avoid simultaneous manual ABS edits, scanning, or other writers while an apply is running. A corrupt journal requires investigation.

## Production cutover and first live validation

Record completion in the live-validation notes or PR. The checklist is an operator prerequisite.

- Back up ABS /config and /metadata and historical local app data.
- Check both historical journals using their archived recovery instructions. For the Streamlit SQLite journal, see **Backup, restore and uncertain writes** in the README at `legacy-final` (`9f51a87`). Run `git show legacy-final:README.md`. For the React/FastAPI bulk JSONL journal, see **Failed writes and restart** in `docs/BULK.md` at `bulk-final` (`db2904a`). Run `git show bulk-final:docs/BULK.md`. Resolve outstanding writes using read-only reconciliation and preserve both journals.
- Verify the earlier bulk title correction in ABS and retire the historical writers for this server.
- Confirm the intended library with export --list-libraries. Check the installed version and the previously verified item using read-only GETs. Substitute the current ID from that journal. Confirm the title, library, expanded authors, narrators, series, tags, and audio or ebook files.

~~~powershell
$absHeaders = @{ Authorization = "Bearer $env:ABS_TOKEN" }
$absBase = $env:ABS_URL.TrimEnd('/')
(Invoke-RestMethod "$absBase/status" -MaximumRedirection 0).serverVersion
$absBook = Invoke-RestMethod "$absBase/api/items/CURRENT_ITEM_ID?expanded=1" -Headers $absHeaders -MaximumRedirection 0
$absBook.libraryId
$absBook.media.metadata.title
$absBook.media.metadata | Select-Object authors, narrators, series, asin, publishedYear
$absBook.media | Select-Object tags, audioFiles, ebookFile
Remove-Variable absHeaders, absBook
~~~

1. Run `absmm check` and resolve any outstanding CLI operations by readback. Export the real library and generate the HTML review for one legitimate title correction.
2. In the HTML report, deselect any other changes and select exactly that one title correction. Confirm the selected count is one, then download the approval JSON.
3. After explicit approval of the exact change, run `absmm apply` with that approval file. Verify that the preview names the intended item and contains only the intended change. Enter the displayed confirmation and acknowledge the backup if prompted.
4. Check the journal and independently verify the title and protected metadata in ABS. If the result is uncertain, run `absmm check` before proceeding. Review and approve a five-book batch separately. Skip production experimentation when no legitimate correction is needed.

## Development

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\ruff.exe check
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\absmm.exe --help
~~~

Automated tests use synthetic data and loopback HTTP. See [SPEC.md](SPEC.md) for the workflow and [AGENTS.md](AGENTS.md) for development boundaries.
