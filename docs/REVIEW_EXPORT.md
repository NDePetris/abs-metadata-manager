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
| Other `books[].current` fields | Separate media-level `tags`, `duration`, `numTracks`, `numAudioFiles`, `numChapters`, `numEbooks`, `ebookFormat` when saved. Chapters preserve `id`, `title`, `start`, `end`, `position`, `index` as supplied. Cover includes only the saved presence flag. Ebook file format and extension are included when available. |
| `books[].review_values` | Bibliographic review projection with the guarded audiobook field shapes, plus manual review fields described below. |
| `books[].evidence.source_files` | Audio-file order, supplied index, embedded track/disc positions, format, duration, bit rate, language, codec, time base, channels/layout, sample rate, bit depth and file-level chapters. Bibliographic tags include titles, albums, contributor credits, publication, description, genre, series, language, identifiers, track/disc, copyright, edition and abridgment tags already exposed by ABS. Legacy offline tags are marked separately. |
| `books[].evidence.references` | Existing field-level corroboration from the verification report, with source, reference value, match level and saved capture timestamps/statuses where available. Calibre includes catalog ID, timestamp and digest. A compatible saved binding includes Calibre ID, UUID and a hash of the Calibre library context. |
| `books[].evidence.quality` | Significant derived field statuses with disposition, reason code, applicability and category hygiene. Routine optional absences, deferred inspections and generic insufficient-evidence entries are omitted. |
| `books[].existing_corrections` | Distinct field and `proposed` value pairs from outstanding metadata targets, including matching durable manual/Calibre targets. Artwork proposals are omitted. The list carries no write authorization. |

`observation.fresh_abs` describes whether the book was read from ABS for the
snapshot being exported. Partial observations can carry older metadata and identify
the source audit. Offline imports are marked separately and may have only a subset
of the expanded metadata.

## Review priorities and field authority

Start with `review_values` for title, subtitle, authors, audiobook narrators,
series, description, genres and tags. Use `current` for the original ABS
structures and additional metadata. Missing fields stay omitted and supplied
nulls, blank strings and empty arrays stay distinct.

- `title` and `subtitle` are saved strings or null. `authors` and `narrators`
  are ordered arrays of names. `series` is an ordered array of
  `{"name": "...", "sequence": "2.50"}` objects. Each sequence is a string or
  null, using the existing `Book.values` representation. A missing sequence
  and an explicit null both use null in this representation. The original
  ABS series records preserve that distinction in `current.metadata`.
- A nonempty `description` uses the book-relative JSON Pointer
  `{"$ref": "#/current/metadata/description"}`. Read the full text at that
  location in the same book object. `genres` and `tags` keep their saved
  values and order.
- Secondary fields include `translators` and `contributors` as arrays of
  credit objects. A supplied string becomes a `name` and a supplied object
  retains its roles and other attributes after privacy filtering. The saved
  `translator` field is used when `translators` is absent. `publication_year`
  and `publication_date` map directly to ABS `publishedYear` and `publishedDate`.
  `publisher`, `language`, `isbn`, `asin`, `identifiers`, `edition` and
  `abridged` use the supplied values. Audiobook `chapters` keep their saved
  order, titles and any supplied positions, indices and start/end times.
- `cover_present` is true for a saved cover and false for an explicitly
  null or blank cover path. False also produces a `Missing` flag in
  `evidence.quality.cover`. Both fields are omitted when ABS did not supply
  a cover path field. The export uses the snapshot for this check and does
  not retrieve artwork.

Only fields in `authority.audiobook.guarded_write_fields` can be applied
through the existing guarded workflow. Their projection matches the writer's
input shape, while any proposed target must still pass writer validation.
Suggestions for other audiobook fields require native or manual edits.
Ebook suggestions are for manual review and correction in Calibre.

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
    "cover": {"present": true}
  },
  "review_values": {
    "title": "Étoile",
    "authors": ["Áda"],
    "narrators": ["Renée"],
    "series": [{"name": "Example", "sequence": "2.50"}],
    "description": {"$ref": "#/current/metadata/description"},
    "genres": ["Fantasy"],
    "tags": ["Favorite"],
    "cover_present": true
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
or ebook details cannot be recovered by export. A Calibre catalog
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
complete desired series list where relevant. See [Import AI corrections](REVIEW_IMPORT.md)
for the accepted recommendation format and local validation rules.
