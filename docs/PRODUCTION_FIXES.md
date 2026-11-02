# Production validation fixes

## Causes and scope

The write verifier compared the complete managed values dictionary strictly, so
an explicitly approved empty subtitle failed readback when ABS returned null.
Read-only inspection of operation 25's persisted observation confirmed this exact
case: subtitle was the only requested change, identity and protected metadata
matched, strict managed equality failed and scoped target equivalence succeeded.
This is historical evidence, not a live verification or authorization to resolve it.
Intended-result comparison now permits that one directional equivalence, only
for a changed subtitle whose requested target is the empty string. Before-state
checks remain strict. Identity, library, paths, other managed fields and protected
media/metadata still must match. Read-only reconciliation uses the same rule.
Accepted correction import reconciliation recognizes that target and persists
the actual observed value as the human acceptance. Rollback previews use the
verified observed managed values and still require exact current source state.
History shows changed fields and material managed, identity and protected-field
differences; semantic equivalence is labeled. Raw intent and observations are
available under Advanced. Failed readback retains the observation for diagnosis.

Provider normalization already preserved a single narrator string as one credit
to avoid splitting names such as `Smith, John`. Comparison now tests a combined
credit against complete separately known names and requires a unique complete
partition. Multiword names with punctuation, spacing and reversed order can agree.
Single-word components, duplicate names and unresolved combined credits remain
ambiguous. Neither candidate raw data nor desired contributor order is changed.
Names alone still cannot establish recording identity: the existing duration,
ASIN, work/provider and contradiction requirements remain in force. Contributor
comparison does not alter persistent candidate IDs or human decisions.
Read-only inspection of the existing stored Audible payload confirmed a single
string, `Kate Reading, Michael Kramer`, including a reversed-order capture.

The search workflow discarded adapter exceptions into a generic unavailable
message. It now persists controlled category, recognized stderr vocabulary, HTTP
status if present, CLI exit code, provider and operation. Authorization, timeout,
connection, upstream HTTP, JSON and schema failures remain distinct. It does not
store arbitrary exception text, command arguments, environments or headers.
An HTTP response does not prove the provider's upstream completed: the diagnostic
explicitly distinguishes known response receipt from unknown ABS reachability.
An ABS-hidden upstream failure returned as an empty array remains indistinguishable
from a genuine empty result.

Restored connections previously had no provider discovery state. Match & Verify
now says Not discovered until the explicit Discover / Refresh ABS Providers action.
Settings still discovers providers after a successful connection; discovery failure
does not discard the valid connection. Source names distinguish ABS Open Library
from direct public Open Library and label recording/bibliographic scope. Independent
Google Books needs GOOGLE_BOOKS_API_KEY and is disabled when it is missing.
Search feedback reports candidate counts, no matches or a safe failure diagnostic.
ABS metadata requests have one attempt per explicit action, with no automatic
retry. Rendering, source switching and navigation perform no discovery/search.
Book selection, desired drafts, acceptance, rejection and write approvals remain
independent of these read-only controls.

## Transport evidence and remaining unknowns

The installed official abs-cli 1.2.0 full help was inspected without contacting a
server. `metadata providers` documents `providers.books[{value,text}]`;
`metadata search` supports `--provider`, `--title` (including Audible ASIN) and
optional `--author`. These agree with the adapter. Existing local simulated-HTTP
tests exercise the official executable's GET-only provider/search contract,
candidate arrays and minute-to-second normalization. Synthetic provider payloads
exercise structured narrator arrays and combined display strings. The stored
production narrator representation was confirmed through SQLite mode=ro with
query_only enabled. The upstream failure cause is not established: nine saved
ABS Audible and one ABS Open Library unavailable attempts contain no categorized
diagnostic. The old workflow discarded it.

No live diagnostic was run. Prior unavailable records lack the discarded cause;
the precise missing evidence is a fresh explicit failed search's categorized
diagnostic (provider, operation, HTTP status/exit category and response receipt).
After deployment, use Match & Verify → Search / Refine → Discover / Refresh ABS
Providers, select ABS Audible or ABS Open Library, then click Search once. Open
Safe search diagnostic if it fails. Do not infer discovery caused search failure
or silently substitute another provider. Authentication failures require checking
both ABS access and the provider configuration; timeout/HTTP categories alone do
not locate the failing hop.

## Existing database and operation 25

1. Stop the app. Back up the existing database, including any SQLite WAL/SHM
   sidecars if present, as a consistent set. Keep the original journals. Install
   this branch and restart using the same database. These changes add no schema
   migrations. Do not operate independent database copies against the same server.
2. In Settings, connect to the original ABS server and select the original library.
   Connection checks and provider discovery are reads; do not approve/apply changes.
3. Open History → Write journals and guarded recovery. Select operation **25** and
   inspect its persisted subtitle intent and any recorded observation. Existing
   uncertain entries are not changed just by opening the app.
4. Click **Reconcile using live metadata (read-only)** once. This reads the current
   item and verifies identity/library/path, every managed field and protected
   metadata/media. An empty or null subtitle with all other guards satisfied can
   become verified. No update is resubmitted.
5. If any material difference remains, leave the entry unresolved and inspect the
   concise comparison and Advanced JSON. Do not manually edit the journal or retry
   the write. A later explicit Import can reconcile the accepted cleared correction
   with the observed null value; it resets write approvals as usual.
6. A rollback is a separate write: review its conditional preview and exact
   confirmation if deliberately needed. Read-only recovery does not authorize it.

Automated validation used synthetic databases/adapters and local HTTP simulation
only. Existing production evidence was inspected through read-only SQL, without
Store initialization, mutations or remote requests. Operation 25 remains uncertain;
neither it nor any production metadata was modified during development.

## Validation results

- Full final suite: **267 passed in 898.63 seconds**, no skips, using the project
  virtual environment and an isolated temporary directory. This includes 35 new
  production-fix regressions. Existing stale-state, guarded rollback, durable
  intent, locking, decision and recording-identity tests pass.
- Ruff, pip dependency consistency, `start.ps1 -Check` and staged whitespace checks
  pass. No dependencies or schema migrations were added.
- Native Streamlit AppTest exercised equivalent narrator credits, discovery failure
  and successful explicit refresh, an Audible timeout with safe diagnostics,
  source switching with an unsaved subtitle, an unresolved clear operation and
  successful simulated read-only reconciliation. Existing UI tests also verified
  rejected editions and selected books survive source switching and reruns fetch
  nothing. These are automated UI interactions, not new browser screenshots.
- The official CLI's local HTTP simulator tests pass. Restricted-sandbox attempts
  encountered temporary-directory/loopback restrictions; the final suite ran with
  normal local access, synthetic credentials and fake/local transports. Two older
  UI fixtures were updated to seed explicit discovery state before the final run.
