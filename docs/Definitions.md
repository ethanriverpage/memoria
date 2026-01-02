# Glossary

Core terminology used across Memoria’s codebase and docs.

## Core entities

### Platform

User-facing ecosystem producing exportable data (e.g., Apple/iMessage, Google, Instagram, Snapchat, Discord). May include multiple products/domains (see **Service**).

### Source export (export)

Raw, unmodified folder/file tree produced by a platform export (or trusted export tool); input dataset for a Memoria run.

### Export identity

Identifier for who/what an export belongs to. Needed because some platforms are account-scoped (username) while others are device-scoped (device name).

### Export username (username)

Account handle/identifier for account-scoped exports (e.g., `example_user`).

### Device

For device-scoped exports (notably iMessage), the device label used as the export identity (e.g., `example_iphone`).

### Service

Product/data domain within a platform processed as a distinct unit (e.g., Google Photos vs Google Chat; Instagram Messages vs Instagram Posts). In output paths, maps to `Platform/User/Service` when a platform has multiple services.

### Processor module (export handler)

Implementation for one source export format, typically under `processors/<name>/`. Includes (at minimum) a preprocessor (`preprocess.py`) and processor stage (`processor.py`). A platform may have multiple processors for distinct export formats (e.g., iMessage tools). “Service” and “processor module” are often close; when you mean “the code that runs”, prefer **processor stage**.

### Preprocessor (`preprocess.py`)

“Catalog + matching” stage:

- Matches media files → metadata entries
- Builds per-item context
- Performs processor-internal deduplication/consolidation (within that export)
- Produces structured metadata artifacts used by the processor stage
- Does not embed metadata into files or copy/transform outputs; it prepares data for the processor stage

### Processor stage (`processor.py`)

Takes preprocessed items and performs file actions:

- Copies/creates output files
- Embeds metadata (e.g., `ImageDescription`, `IPTC:Caption-Abstract`, timestamps)
- Emits reports/artifacts as part of the processor output

## Pipeline phases

### Consolidation

Merges multiple source exports with overlapping data into a single combined export for preprocessing to:

- Avoid duplicate outputs when the same underlying items appear in multiple exports
- Improve matching by letting a preprocessor see all relevant metadata/media together

### Cataloging

Builds a structured index of media and metadata for downstream decisions, especially cross-processor deduplication; typically includes:

- Indexing media/metadata entries
- Computing hashes/fingerprints
- Building lookup tables for matching and dedup decisions

## Metadata terms

### Context

All metadata around a media item: provenance, timestamps, relationships, participants/sender, conversation/thread info, captions/message text, albums/posts, etc. Context is the superset; **Description** is derived from context.

### Description

Canonical text embedded into image metadata fields (notably `ImageDescription` and `IPTC:Caption-Abstract`). Records provenance and human-useful context in a compact, standardized form (see `docs/Deduplication.md` under “ImageDescription Format”).

## Matching / completeness terms

Mismatches between disk media and metadata; commonly surfaced under `issues/` and in failure reports.

### Orphaned media

- Definition: Media on disk not referenced by any metadata entry.
- Typical impact: Often lacks authoritative context (timestamps, sender/conversation association, provenance), so processing may be best-effort or skipped depending on the processor.

### Orphaned metadata

- Definition: Metadata referencing media not present on disk.
- Typical impact: Nothing to process; strongly suggests an incomplete or inconsistent export.

### Unmatched media

- Definition: Media still processable (enough metadata for a concrete output, including stable source paths/filenames and timestamps) but missing expected source context (e.g., missing conversation participants/sender/thread attribution for message attachments).
- Note: Distinct from orphaned media—unmatched media has some metadata match (enough to process), but the match is incomplete.
- Policy/recording: `docs/Failure-Handling.md` (“Terminology: orphaned vs unmatched”).
