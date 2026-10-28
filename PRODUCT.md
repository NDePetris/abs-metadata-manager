# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
User specified Python 3.12+, Streamlit, SQLite, pytest, and abs-cli subprocess integration.

## Users
A personal Audiobookshelf library owner reviewing metadata on a Windows desktop.

## Product Purpose
Audit an entire audiobook library, review field-level proposals, and apply explicitly approved corrections with live validation and recovery history.

## Operating Context
The server runs in Docker on an Intel N150. Requests must be sequential and paced. Approximately 110 audiobooks and 656 Calibre-managed ebooks. No production writes are authorized during development.

## Capabilities and Constraints
Four primary views: Library, Review, Apply, History. A separate connection/settings view. Decisions persist locally. Each write requires approval and exact-preview confirmation. No online normalization, media rewriting, folder changes, cover replacement, or chapter generation. Favor a practical desktop layout over visual polish.

## Product Principles
Explicit approval, visible evidence, conservative rules, durable recovery, and edition preservation.
