# Import AI corrections

1. Select the original audiobook library in the sidebar. Open **Changes → Import
   AI corrections** and upload the existing recommendations JSON.
2. Review the counts and filter by author, book, field, confidence or
   disposition. The table shows the expected, saved and desired values.
   **Recommendation details** shows the rationale and evidence. For example,
   choose **genres** to inspect all proposed genre sets together.
3. Select eligible fields individually or use **Select eligible rows in view**.
   Review each editorial description in **Recommendation details** and select
   it individually in **Fields selected for staging**. Confirm any held narrator
   credit against the actual recording before selecting it.
   You can check the selection against live ABS for up to ten books per request.
   This is read-only and leaves the saved snapshot intact.
4. Click **Stage selected changes locally**. Return to the existing Changes
   controls for separate field approval, exact preview and confirmation.
   Apply still uses the guarded writer with its existing ten-book cap, live
   checks, journal, readback and rollback. Unresolved recovery still blocks
   remote execution. Local review and staging are available during recovery
   when the latest live snapshot is complete and the selected fields are clear.
5. Select up to ten audiobooks in Changes, click **Approve selected fields for
   ABS preview**, and inspect **Exact CLI metadata payload**. Enter the displayed
   confirmation phrase, check the approval box and click **Apply confirmed
   changes**. Repeat for the next batch. Refresh the library after application
   to update the saved inventory. Save the original upload privately and upload
   it again in a new browser session as needed.

If a newer import was interrupted, finish a live import to enable staging.
The importer still uses the original saved snapshot to verify the proposal. It
can stage against a newer complete live snapshot when the selected books have
the same identity and managed values and the expected field still matches.
A moved item, changed recording, unexpected metadata or missing source
snapshot requires review. Item IDs are never migrated.

## Accepted review document

The UTF-8 JSON document may be up to 5 MiB. Duplicate JSON keys, non-finite
numbers, unknown fields and invalid value types fail validation. The supported
envelope is the original review format with `document_type`
`audiobookshelf_metadata_review_recommendations`, `document_version`
`1.0-review-proposal` and `import_ready: false`.

- `source_export` carries `application: abs-metadata-manager`, numeric
  `schema_version: 1`, `snapshot_id`, `library_id`,
  `library_scope_fingerprint`, `snapshot_started_at`, `snapshot_completed_at`,
  `book_count`, `offline_import` and `content_digest`. Digests are lowercase
  SHA-256 hex. The content digest records provenance. The app validates the
  actual saved snapshot and individual books, without recreating the export
  or using the content digest as authorization.
- `recommendations` is an array. Each entry carries `proposal_id`, `item_id`,
  `identity_digest`, `managed_values_digest`, `media_type`, `field`,
  `expected_original_value`, `complete_desired_value`, `rationale`,
  `confidence`, `status`, an `evidence` array of objects, and `write_path`.
  The app derives capability from the saved book and field. It treats
  `write_path`, evidence and confidence as review data.
- `existing_staged_corrections` is a separate array. Each entry has the item,
  field and digests above, `expected_original_value`,
  `already_staged_desired_value`, and status
  `already_staged_in_original_export_not_repeated`. A prior entry never becomes
  a fresh staging candidate, even if its old target was discarded. A new entry
  with a matching existing target is also excluded. A conflicting target
  leaves the existing correction and approval intact.
- `genre_policy` supplies `controlled_genres`, `genre_parent_rules`,
  `exclude_media_format_as_genre`, `allow_multiple_genres` and
  `must_include_broader_genre`. The importer validates complete ordered sets
  against those rules and carries the policy into the checklist. It does not
  change library preferences. `application_safety` is an advisory object.

Titles need nonblank text. Subtitles can be cleared with an empty string.
Authors and narrators use complete ordered arrays of nonempty names. Series
uses a complete array of unique names with decimal sequence strings or null.
Dropping existing series memberships is flagged for manual review. Author
changes with saved role information require manual review. Descriptions retain
their full text in the payload. ABS sanitizes description HTML. If ABS returns
different text after a write, the operation remains uncertain and requires
reconciliation. Publication-year targets are four-digit strings or integers
and are sent as strings in ABS `publishedYear`.
Ebook recommendations are manual Calibre guidance.

### Synthetic example

This entry appends a series membership while preserving the original decimal
position. The digest placeholders stand for the saved book's fingerprints.
The synthetic test builds a complete upload and validates it against a saved
snapshot.

```json
{
  "proposal_id": "example-series",
  "item_id": "example-book",
  "identity_digest": "<saved identity sha256>",
  "managed_values_digest": "<saved managed values sha256>",
  "media_type": "audiobook",
  "field": "series",
  "expected_original_value": [{"name": "Example series", "sequence": "2.50"}],
  "complete_desired_value": [
    {"name": "Example series", "sequence": "2.50"},
    {"name": "Collected editions", "sequence": null}
  ],
  "rationale": "Keep the original series and add the collection membership.",
  "confidence": "high",
  "status": "approved_recommendation",
  "evidence": [{"source": "Synthetic edition record"}],
  "write_path": "audiobook_guarded_writer"
}
```

## Audit #6 pilot

The pilot includes 206 new recommendations across 108 of 110 books. Once
snapshot checks pass, 203 can be selected for the guarded workflow, including
108 genre sets, 55 individually selected editorial descriptions and six
publication years. Three narrator changes require explicit recording
confirmation. The six previously staged corrections are listed separately and
excluded from staging. Uploading or inspecting the document leaves all
corrections and approvals as they were.
