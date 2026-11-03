# abs-cli 1.2.0 metadata search contract investigation

## Established evidence

The operator's successful read-only probe, using the correct unchanged application
API key, returned HTTP 200, application/json, a valid JSON array, one candidate,
3,518 bytes, no content encoding, and no redirect. An earlier 401 was caused by a
typing error in the probe key. **That 401 is not evidence of the original CLI
failure's cause.** No response body, API key, credential-bearing URL or authorization
header was shared or recorded in the investigation.

The exact producer of the original malformed CLI stdout remains unconfirmed. A
JSON parse failure and exit code zero alone cannot distinguish a non-JSON HTTP
body, redirect/interstitial, stale auth response, stdout contamination or another
transport difference. We did not read saved credentials or contact the server.

The official source establishes the following precise contract:

1. [MetadataService.SearchAsync](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Services/MetadataService.cs)
   URL-encodes provider, title and optional author and calls GET api/search/books.
   [ApiEndpoints](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Api/ApiEndpoints.cs)
   defines that relative path. No metadata mutation endpoint is involved.
2. [MetadataCommand](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Commands/MetadataCommand.cs)
   passes the resulting string to WriteRawJson and returns zero.
   [ConsoleOutput.WriteRawJson](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Output/ConsoleOutput.cs)
   calls Console.Out.WriteLine verbatim. It does not parse, escape, pretty-print,
   truncate, validate content type or validate the candidate-array schema. Thus
   **exit zero does not guarantee JSON stdout**. There is no established formatter
   defect for a correctly received valid JSON string.
3. [AbsApiClient.GetAsync and EnsureSuccessOrHandleAuthAsync](https://github.com/thomaslazar/abs-cli/blob/v1.2.0/src/AbsCli/Api/AbsApiClient.cs)
   contain a separate confirmed response-selection defect: after a 401, the helper
   refreshes and retries but returns no replacement response. GetAsync subsequently
   reads its original response. A successful retry can therefore yield the old 401
   body on stdout with exit zero. This mechanism is established by the source;
   it is **not established as the trigger in this production incident**.
4. [ABS 2.37.1 web UI](https://github.com/advplyr/audiobookshelf/blob/v2.37.1/client/components/modals/item/tabs/Match.vue)
   uses GET /api/search/books with the same provider/title/optional author. It also
   sends fallbackTitleOnly=1 and optionally the library item ID.
   [SearchController](https://github.com/advplyr/audiobookshelf/blob/v2.37.0/server/controllers/SearchController.js)
   ignores fallbackTitleOnly; the optional ID loads the item for
   [BookFinder](https://github.com/advplyr/audiobookshelf/blob/v2.37.0/server/finders/BookFinder.js)
   confidence/duration ranking. Neither explains malformed JSON. There is no
   established obsolete search endpoint in the inspected supported versions.
   The deployed web UI/version and its exact network request were not inspected.

## Application correction

Only ABS metadata searches now use one direct, authenticated GET to that same
endpoint in adapter.py. It uses the explicitly configured application key, strict
JSON parsing and an array-of-objects schema. It never refreshes tokens, follows
redirects, loads netrc/proxy credentials, retries or falls back to another source
or to the CLI. HTTP authorization, upstream/rate-limit, timeout/connection,
non-JSON and schema errors remain distinct. Safe diagnostics retain status,
transport, shape, size and JSON error coordinates; no body fragments are retained.

Provider discovery and other existing CLI operations remain unchanged. Every
production metadata write still goes exclusively through the guarded CLI update
path. No approval, source-state, identity, media, journal, reconciliation, rollback
or locking protection changed. Search remains an explicit user action; this is a
single transport choice, not an automatic retry/fallback after a failed CLI call.
The response/source identity is unchanged, preserving decisions and candidates.

The narrow upstream correction would make the auth helper return the successful
HttpResponseMessage and have GetAsync read that returned response. Metadata search
should additionally validate JSON/array shape before reporting CLI success. We
did not patch the installed executable or post changes upstream.

## Explicit, safe diagnostic procedure

Run from the repository, with the app idle:

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_metadata_search.py
```

Enter the exact base URL, provider, title/ASIN and author used by the working web
UI. The key prompt is hidden; no stored key/config is read. This performs one GET
under the workspace lock and prints only safe shape JSON. Redirects are reported,
not followed. The diagnostic does not use browser cookies, netrc or environment
proxy credentials. It reads at most 2 MB of decoded response and labels truncation.
Do not share keys, raw stdout, copied request headers or debug logs.

For an exact official CLI comparison, use a **clean OS account or Windows Sandbox**
with Python, the project dependencies and official abs-cli 1.2.0 installed, then:

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_metadata_search.py --with-cli
```

This prompts for the same parameters/key and additionally captures the exact
metadata search command in memory, emitting only candidate count or sanitized
error/shape. The CLI receives the key through its child environment, never argv.
It can make its normal GET /status version probe; with no stored config/refresh
token it cannot silently authenticate with a different saved session. The CLI may
create a nonsecret version-check cache; it does not save the supplied key.

The command refuses before prompting/network access if the actual .NET CLI
profile already contains config.json, without opening it. Overriding HOME or
USERPROFILE does not reliably isolate .NET on Windows: it uses the Windows known
profile folder. Do not move/delete a personal credential file merely to run this
test. We verified that refusal in the development profile and did not run the
official executable against either the live server or personal configuration.

The missing evidence for the original production root cause is the shape-only
CLI result from that clean-profile comparison using the correct, identical
parameters/key. The successful direct probe already verifies the API route and
JSON response contract, independently of the CLI's stdout/auth handling.

## Validation

Final focused run: **75 passed, 3 deselected in 106.73 seconds**. Ruff, dependency
consistency, Windows launcher and whitespace checks pass. No dependencies or
schema migrations were added.

Regression coverage includes exit-zero HTML/empty/plain-text/BOM/truncated JSON,
valid large Unicode JSON, strict rejection without prefix stripping or salvage,
401/302/429/503, valid candidate arrays, unsupported JSON schemas, one GET per
action, no redirects/CLI fallback/retries, timeout/connection redaction, existing
credential-config refusal, and provider/decision/write-protection regressions.
Tests use fake subprocesses and synthetic loopback HTTP only. The three pre-existing
official-binary tests are excluded for this investigation because their normal
CLI startup can load personal configuration. This is distinct from the prior
267-test full run; no fresh full-suite claim is made here.
