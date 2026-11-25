# Development boundaries

Keep Audiobookshelf audiobook metadata accurate with minimal manual effort. Preserve validated behavior through selective reuse. Prefer cohesive Python code and a small offline HTML report over new dependencies or abstractions.

- Python 3.12+, requests, pytest and Ruff. Run pytest with skip reporting, Ruff, pip check, and the Windows installed console smoke. Keep a simple Windows/Linux CI matrix.
- Use fake adapters or loopback HTTP with synthetic data. Never use production credentials in tests. Production writes require explicit approval of exact changes. Stop at the live checkpoint unless the owner approves proceeding.
- The HTTP adapter owns ABS access. Preserve the single allowlisted PATCH path, live identity and stale-state checks, backup acknowledgment, exact confirmation, durable intent, readback, locking, and recovery. Never retry writes automatically or edit the journal to clear uncertainty.
- The HTML report is self-contained, treats external metadata as text, and has no network access. Python owns all validation and write authorization.
- Keep private data and credentials out of Git and AI exports. Preserve historical data. Calibre remains authoritative for ebooks. Leave later milestones, provider matching, databases, app servers, embedded metadata, and media-file changes outside this implementation.
