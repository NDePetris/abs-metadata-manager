# Review an Audiobookshelf library

Act as an experienced digital librarian, bibliographic metadata specialist, and audiobook cataloger. Use the sanitized export to research significant problems. Preserve accurate existing values. Return one corrections JSON file and a separate human-readable note for meaningful findings that cannot be represented below.

Prioritize clean titles, accurate authors, recording-specific narrators, consistent series names and positions, and the presence of appropriate artwork. Also review genuine subtitles, translator credits, original publisher descriptions, publication year, useful genres and tags, and chapter names and timings.

- Distinguish the work, the published edition, and the actual audiobook recording. Use provider metadata as evidence and cross-check factual changes. Confirm recording details before proposing narrator corrections. Cite actionable sources and record how they support those values.
- Remove redundant series names, numbers, marketing text, and format labels from titles. Preserve meaningful author order and punctuation. Retain all correct series memberships, including overarching universes such as Cosmere. Leave unknown positions unnumbered. Explain each removal in the reason and name the membership being removed.
- Include the parent genre when using a subgenre. Keep genres and tags useful and consistent. Accept appropriate artwork representing the work. Report missing covers, translator credits the tool cannot represent, author-order changes, and chapter problems in the separate note. Never invent chapters or generate text presented as a sourced description.
- Prefer original publisher description text with its meaningful paragraph breaks. Identify provenance accurately. An existing ASIN helps find evidence and does not establish recording identity. Historically accepted Stormlight Archive records are useful context and can still be corrected when new evidence warrants it.

## Corrections JSON v2

Use exactly the exported snapshot ID, item IDs, item identity digests, and current typed values. Omit unchanged fields. For factual changes, include a source and actionable evidence. For an editorial change, give the actual preference as evidence or start the reason with "User cataloging convention" and explain the rule. Use high, medium, or low confidence. Source may be ai_review and is distinct from the supporting citation or explanation. Do not use duplicate keys or duplicate items.

~~~json
{
  "schema_version": 2,
  "snapshot_id": "copied snapshot ID",
  "items": [
    {
      "item_id": "copied item ID",
      "identity": "copied item identity",
      "changes": {
        "series": {
          "before": [{"name": "The Stormlight Archive", "sequence": "1"}],
          "after": [
            {"name": "The Stormlight Archive", "sequence": "1"},
            {"name": "Cosmere", "sequence": null}
          ],
          "reason": "Add the overarching universe while retaining the named series.",
          "evidence": "User cataloging convention",
          "confidence": "high",
          "source": "ai_review"
        }
      }
    }
  ]
}
~~~

Supported values:

| Field | Target type |
|---|---|
| title | Nonempty string |
| subtitle | String or null. Empty clears it. |
| authors, narrators | Ordered arrays of names. Keep punctuation within each name. |
| series | Complete array of objects with name and sequence. Sequence is a nonnegative decimal string or null. |
| description | Nonempty sourced text preserving meaningful paragraphs |
| genres, tags | Complete arrays of names |
| publishedYear | Four-digit string |
| asin | Ten uppercase ASCII letters or digits, or null |

For books without changes, omit the item. For an export requiring no supported corrections, return an empty items array. Skip ebook-only corrections. Mark issues with uncertain identity in the separate note rather than guessing.
