# Architecture and safety

Python 3.12+, Streamlit, SQLite, pytest. Run `python -m pytest` before committing.

- `adapter.py` is the only production metadata transport. Writes use only `abs-cli items update --id ... --stdin` with an allowlisted metadata payload. Never use bulk, file, scan, embed, cover, or chapter mutations.
- `models.py` separates original JSON, normalized fields, and identity. Never resolve oldLibraryItemId to a new write target.
- `audit.py` is pure deterministic analysis. Folder names are evidence; contributor roles and edition narrators must survive.
- `store.py` persists snapshots, individual decisions, and a synchronous durable journal. New audits reset all approvals. Private exports, SQLite, tokens, and local CLI binaries are ignored.
- `workflow.py` owns all mutations. It requires approved field decisions and a confirmation bound to an exact preview digest. Live identity, server, library, path, expected fields, and unrelated metadata are guarded. Persist intent before starting the subprocess. Never retry writes automatically.
- Treat prepared/applied/uncertain entries as unresolved until read-only reconciliation. A mixed or unexpected result blocks subsequent writes. Rollback is a separately confirmed, conditional journaled operation.
- Streamlit renders and reruns must never write remotely. Mutations occur only inside explicit submit-button branches. No startup writes, schedules, background workers, or automatic correction.
- Tests use fake adapters or a local simulated HTTP server. Never use production credentials in tests. No live metadata writes during development.
- Preserve playback progress, chapters, paths, covers, and files. Calibre is authoritative for ebooks, which are read-only here.
- Keep one operation at a time using the cross-process workspace lock. A crash releases the OS lock, but journal intent remains and requires reconciliation.

The user requested a practical desktop layout and autonomous implementation. Use native Streamlit components; no branding exercises, generated design comps, animation, or mobile UI work.
