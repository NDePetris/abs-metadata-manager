# Milestone 1 specification

Maintain a personal Audiobookshelf audiobook library through an offline review and explicit CLI approval.

- Export a whole ABS library using authenticated, paginated HTTP GETs, expanded items, and two membership observations. Preserve the original scoped snapshot privately for concurrency checks and recovery. Share the allowlisted export with the external reviewer.
- Review one schema v2 corrections file. The manifest binds exact values to the snapshot, server, library, and item. The standalone browser report selects and edits changes, exporting digest-bound change IDs and optional raw edit text. Python validates and interprets all approvals.
- Allow title, subtitle, authors, narrators, series, description, genres, tags, publication year, and ASIN. Mixed media can be selected with a shared-metadata flag. Both membership and order remain visible when authors are reviewed. Order-only changes require manual correction in ABS. Series, genre, and tag updates carry complete lists.
- Bind interactive confirmation to the complete post-edit change set. Verify identity, paths, expected values, and unrelated metadata against a fresh ABS read. Never write ebook-only items. Persist intent, send one allowlisted PATCH per eligible audiobook, read back, and stop on an uncertain outcome.
- The append-only journal records verified, not-applied, conflicting, already-applied, uncertain, and explicitly abandoned operations. Only unresolved intents and uncertain outcomes block subsequent writes. Corrupt journals require investigation. Recovery uses read-only ABS access.
- Future milestones cover subset and date exports, correction merging, automated description acquisition, undo, persistent exceptions, and read-only Calibre reports. Milestone 1 ends at the live-validation checkpoint.
