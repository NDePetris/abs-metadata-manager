# Design

Practical native Streamlit desktop workspace. Retain the configured light theme; no custom CSS, animation, branding assets, or mobile-specific UI.

The sidebar holds Library, five navigation choices, and connection status. Normal pages resolve the latest completed inventory internally. Historical snapshots are Advanced history tools.

Match & Verify uses a searchable book selector with Previous/Next and a concise progress line. A compact candidate selector, plain-language classification, returned edition details and a shared action strip precede the Field / Current in ABS / Reference / Desired grid. Fields begin unselected; edits/copy/restore select just that field. One Confirm or Confirm & Next action atomically accepts selected drafts. Notes and secondary decisions disclose in More review actions. All diagnostics, raw/historical evidence, covers and source synchronization share Advanced / Evidence. Drafts are separate from widget state and scoped to server/library/audit/item/field. Human acceptance and independent assessment use compact text labels.

Changes groups confirmed targets and legacy human proposals by book. Selection, field write approval, exact preview, phrase, and acknowledgement precede guarded execution. Calibre validation is separate and read-only. History emphasizes chronological activity; journals and rollback are advanced controls, expanded when recovery is required.

Desktop acceptance sizes: 1024×800 and 1280×800. Keep all three value columns readable together. The native sidebar starts collapsed to preserve desktop width; it holds the single global Library selector. Long scalar text wraps; structured series editing retains decimal strings and multiple rows. Missing file inspection displays Not checked and does not create a correction.
