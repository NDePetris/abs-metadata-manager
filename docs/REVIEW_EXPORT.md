# Metadata review bundle v1

In **Import**, select the library in the sidebar. Check the name, snapshot
number, completion time and book count beside **Export metadata for ChatGPT**.
Click it, then **Download metadata review JSON**. Review the file before
uploading it to ChatGPT. Click the export action again to include any local
evidence or proposal changes saved since generation.

The export uses the latest completed snapshot for the selected library, even
if a newer import is incomplete. Every saved book is included regardless of
quality or acceptance. Generation reads local data under the workspace operation
lock and creates the download in memory. It makes no network requests and
does not change metadata, evidence, decisions or approvals.

## Fields and provenance

| Location | Contents |
| --- | --- |
| `schema_version`, `exported_at`, `content_digest` | Format version, UTC generation time and SHA-256 of the bundle excluding these two volatile/integrity fields (`exported_at` and `content_digest`). |
| `source` | Application, snapshot ID, library ID and name, snapshot start and completion times, offline import flag, book count and SHA-256 of the ordered pair of saved server URL and library ID. |
| `authority` | ABS is the observed state. The guarded audiobook writer supports `title`, `subtitle`, `authors`, `narrators` and `series` for eligible live snapshots. All other audiobook fields are native or manual. Calibre owns ebook corrections. |
| `category_preferences` | Library-scoped exact genre/tag values and saved preferences. |
| `books[]` | Ordered by current ABS item ID. Each entry includes the per-book media type, persistent identity digest, digest of the five normalized `Book.values` fields and saved ABS observation context. |
| `books[].current.metadata` | The expanded ABS `media.metadata` object, with privacy filtering. Includes title and subtitle, contributor arrays and roles, series and sequences, description and variants, genres, publisher, publication year and dates, language, ISBN, ASIN and other identifiers, abridgment, explicit and edition fields wherever present. Additional keys and nested metadata are preserved by default. |
| Other `books[].current` fields | Separate media-level `tags`, `duration`, `numTracks`, `numAudioFiles`, `numChapters`, `numEbooks`, `ebookFormat` when saved. Chapters preserve `id`, `title`, `start`, `end`, `position`, `index` as supplied. Cover includes presence and a recognized extension hint. Ebook file format and extension are included when available. |
| `books[].evidence.source_files` | Audio-file order, supplied index, embedded track/disc positions, format, duration, bit rate, language, codec, time base, channels/layout, sample rate, bit depth, file-level chapters and embedded cover presence. Bibliographic tags include titles, albums, contributor credits, publication, description, genre, series, language, identifiers, track/disc, copyright, edition and abridgment tags already exposed by ABS. Legacy offline tags are marked separately. |
| `books[].evidence.references` | Existing field-level corroboration from the verification report, with source, reference value, match level and saved capture timestamps/statuses where available. Calibre includes catalog ID, timestamp and digest. A compatible saved binding includes Calibre ID, UUID and a hash of the Calibre library context. |
| `books[].evidence.quality` | Significant derived field statuses with disposition, reason code, applicability and category hygiene. Routine optional absences, deferred inspections and generic insufficient-evidence entries are omitted. |
| `books[].existing_corrections` | Distinct field and `proposed` value pairs from outstanding staged targets, including matching durable manual/Calibre targets. The list carries no write authorization. |

`observation.fresh_abs` describes whether the book was read from ABS for the
snapshot being exported. Partial observations can carry older metadata and identify
the source audit. Offline imports are marked separately and may have only a subset
of the expanded metadata.

### Abbreviated synthetic book

```json
{
  "item_id": "example-item",
  "media_type": "audiobook",
  "identity_digest": "<sha256>",
  "managed_values_digest": "<sha256>",
  "observation": {"source_audit": 6, "fresh_abs": true, "captured": "2026-11-05T18:00:00+00:00"},
  "current": {
    "metadata": {
      "title": "Étoile",
      "authors": [{"name": "Áda", "role": "author"}],
      "narrators": ["Renée"],
      "series": [{"name": "Example", "sequence": "2.50"}],
      "description": "Complete saved description.",
      "genres": ["Fantasy"],
      "customEditionNote": null
    },
    "tags": ["Favorite"],
    "cover": {"present": true, "format_hint": "jpg"}
  },
  "evidence": {"references": {}, "quality": {}, "source_files": {}},
  "existing_corrections": [{"field": "subtitle", "proposed": "A proposed subtitle"}]
}
```

## Privacy and limits

The exporter projects selected media fields and recursively removes recognized
private keys within retained objects. Obvious absolute paths, inline credentials,
credential-bearing URLs and data URIs in retained strings are replaced with
`[redacted]`. The file omits raw item and provider responses, server addresses,
filesystem paths and inodes, user and playback records, file and image bytes,
Calibre paths, approvals and recovery data. Hashes and stable IDs are useful for
binding suggestions to the same library and book. They provide no anonymity
guarantee. Descriptions and custom metadata can contain private text that automated
filtering cannot recognize.

Only data captured in the snapshot or already saved as evidence can appear.
Missing native metadata keys stay missing, while explicit null and empty values
are preserved. Audio tags never establish authoritative current values.
Unavailable chapters, file tags, contributor roles, description variants,
cover dimensions or ebook details cannot be recovered by export. An extension
or presence flag does not establish cover correctness. A Calibre catalog
observation does not establish ABS synchronization.

## Digests and future proposals

All digests use the existing `models.digest` representation, SHA-256 over UTF-8
JSON with recursively sorted object keys, literal Unicode, compact separators
and no trailing newline. The downloaded JSON has a trailing newline. Arrays keep
their original order unless explicitly sorted by the bundle schema. Native series
positions retain their stored representation, including decimal strings.
`content_digest` binds the sanitized export including its evidence and proposals.
The same local state produces the same digest across generation times. Identity
and managed-value digests bind the original local model values before redaction.
Digests are fingerprints, not signatures or approvals.

Keep the original bundle intact. Ask ChatGPT for a separate list of proposed
changes with the source snapshot ID, library scope fingerprint, bundle digest,
item ID, identity and managed-value digests, field, expected old value, desired
value and rationale. Use exact desired values for each book, including the
complete desired series list where relevant. A future importer will define its
accepted format and must validate all proposals against local state. Suggestions
remain untrusted until reviewed and validated in the guarded workflow.
