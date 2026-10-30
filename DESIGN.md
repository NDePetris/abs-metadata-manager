---
name: Audiobookshelf Metadata Manager
description: A practical native Streamlit desktop workspace for audiobook metadata review.
colors:
  primary: "#235B54"
  background: "#FAFAF7"
  secondary-background: "#EFF1ED"
  text: "#202B29"
typography:
  body:
    fontFamily: "sans serif"
---

# Design System: Audiobookshelf Metadata Manager

## Overview

The design follows the confirmed brief: a practical desktop workspace built with native Streamlit components. Searchable metadata tables, full field inspectors, clear saved decisions, and explicit confirmation take priority over visual polish. There is no separate brand metaphor.

The implementation uses Streamlit's light theme and standard component hierarchy. Connection state, snapshot provenance, read-only restrictions, and operation results appear alongside the work they explain. Review, confirmation, and recovery have distinct views.

**Key Characteristics:**

- Dense, searchable tables with inspectors below them.
- Native controls, typography, messages, forms, and expanders.
- Visible provenance and approval status.
- Exact before/after previews and separately confirmed recovery actions.

## Colors

The four configured theme colors come from `.streamlit/config.toml`; their values in the frontmatter are authoritative.

### Primary

- **Deep green:** The native primary accent for primary buttons and selected controls. Streamlit owns their state treatments.

### Neutral

- **Light background:** The main workspace surface.
- **Muted secondary background:** The native sidebar and input surface treatment.
- **Dark text:** The configured foreground for the light theme.

Native information, warning, success, and error messages retain Streamlit's semantic colors. The app defines no replacement semantic palette.

## Typography

Streamlit's configured `sans serif` family supplies the interface typography. This is a framework font setting, not a custom font stack. The app defines no font sizes, weights, line heights, or letter spacing.

### Hierarchy

- **Page title:** `st.title` names the current view.
- **Section title:** `st.subheader` names the selected item, finding, preview, or settings section.
- **Body:** `st.write` presents instructions, summaries, and reasons. Inline emphasis distinguishes labels and counts.
- **Supporting text:** `st.caption` presents provenance, saved decisions, restrictions, and explanatory context.
- **Structured values:** `st.code` and `st.json` preserve exact values, payloads, and evidence using native formatting.

**The Native Hierarchy Rule.** Use the existing Streamlit text roles rather than introducing custom display typography.

## Layout

The app uses `layout="wide"` and `initial_sidebar_state="auto"`. The native sidebar contains a Workspace radio navigation, connection status, a divider, and the operation sequence. Its views are Library, Verification, Review, Apply, History, and Settings.

Main views use a vertical working sequence: page title and explanation, source/snapshot context, filters or summary, a full-width data table, then a selected-record inspector. Tables use `width="stretch"` and hide the row index. Wide tables retain native horizontal scrolling; information is not removed to fit the viewport.

Library places the metadata inspector and optional cover preview in columns with a 2:1 ratio. Review uses three equal filter columns, two equal decision-action columns, and two equal columns for current/proposed values. Settings uses two equal columns for timing controls. Exact pixel widths, spacing scales, and responsive breakpoints are framework-owned and are not app tokens. The smaller supplied capture demonstrates the native collapsed sidebar, without establishing a separate mobile design.

## Elevation & Depth

The app defines no shadows or elevation tokens. Native sidebar/input surface tones, table boundaries, dividers, forms, and expanders organize the workspace. Preserve Streamlit's built-in popup and focus treatments.

## Shapes

Controls, tables, forms, and expanders use native Streamlit shapes and borders. No custom radius, clipping, or border token is defined. The screenshot evidence supports softly rounded native controls; it does not establish a numerical radius.

## Components

### Navigation

A labeled sidebar radio selects the working view. A nearby caption identifies the connected server or disconnected local workspace. Keep the workflow sequence visible as supporting text.

### Tables and inspectors

Library exposes title, authors, narrators, series, media type, and cover presence. A separate selectbox opens the full observed field values, path, normalized evidence, chapter names, and original JSON. A cover is loaded only through the explicit native preview control when available.

Review exposes the field, current/proposed values, kind, confidence, status, and reason in its findings table. The selected finding repeats the values side by side, with evidence in an expander. Preserve the full data through scrolling and inspectors rather than truncating the underlying values.

### Inputs and filters

Verification extends the same native table-and-inspector layout with field coverage, a full-library status matrix, source coverage disclosure, and side-by-side saved/embedded/folder/candidate values. Status and ambiguity filters are explicit native controls. Online discovery, run preparation, start/resume, cancellation, catalog import, manual review, and pending proposals use labeled forms. External status and human review outcomes remain separate text columns. The synthetic six-book view was inspected in the actual app panel; tables scroll without hiding the complete underlying values.

Use labeled native text inputs, text areas, selectboxes, multiselects, checkboxes, and number inputs. Library has a text search. Review filters by field, confidence, and review status, with a correction-only checkbox. Status is expressed in text and table columns, not only through color.

### Buttons and forms

Primary native buttons identify connection checks, whole-library audits, and the confirmed Apply action. Other buttons retain the default native treatment. Disabled controls show that prerequisites are unmet. Streamlit owns hover, focus, active, and disabled styling.

The finding form displays the saved decision above its editor and starts its decision selector at pending. Saving an edited value requires the user to choose approval explicitly. Bulk approval and rejection require explicit proposal selection.

### Preview and confirmation

Apply shows server, library, item identity/path, and before/after field tables. The exact CLI payload is available in an expander. The form shows an exact preview-bound phrase, a text input, an explicit acknowledgement checkbox, and a submit button. Apply requires the source server and selected library to match. Blocking recovery state appears as a native warning.

### Recovery and history

History lists operation states and provides a selected-operation inspector. Read-only reconciliation has its own labeled action. Rollback sits in a separate expander with its own preview, confirmation phrase, checkbox, and submit control. Keep these controls distinct from ordinary approval and Apply.

### Messages and disclosure

Use native information, warning, success, and error messages for connection requirements, read-only snapshots, blocked operations, and results. Expanders hold detailed evidence and exact JSON without hiding the main decision context. Sequential operations use native progress and spinner components.

## Do's and Don'ts

### Do:

- **Do** use native Streamlit components and its configured light theme.
- **Do** preserve searchable tables and complete selected-record inspectors.
- **Do** show provenance, saved decisions, read-only state, and blocking requirements in text.
- **Do** keep exact previews and explicit confirmation together.
- **Do** present reconciliation and rollback separately from approval and Apply.

### Don't:

- **Don't** add custom CSS, decorative assets, generated design comps, animation, or a separate mobile UI.
- **Don't** invent custom font, spacing, radius, shadow, or breakpoint tokens from framework defaults.
- **Don't** replace detailed metadata review with decorative cards or imagery.
- **Don't** make approval status depend only on color or imply that editing a value automatically approves it.
