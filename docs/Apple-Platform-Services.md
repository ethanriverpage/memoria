# Apple platform + services refactor (Option A)

This document compiles the codebase findings needed to make Memoria’s “platform” semantics match the real-world taxonomy when adding an iCloud Photos processor:

- **Platform**: `Apple`
- **Services**: `imessage`, `photos`
- **Canonical output path**: `Apple/{export_username}/{service}`
- **Canonical provenance path in ImageDescription**: `Source: Apple/{export_username}/{service}`

This makes iMessage a **service** (not a platform) and allows iCloud Photos to live alongside it under the same platform umbrella.

## Why this matters in Memoria

Across the codebase, three concepts are tightly coupled:

- **Output layout** (filesystem): built from `Platform/User/Service`
- **Preprocessed export identity** (detection): encoded in `metadata.json` → `export_info.platform` + `export_info.service`
- **Cross-processor dedup provenance** (`ImageDescription` “Source:” lines): rendered as `Platform/username/service` with relative `./` notation

Changing iMessage from a platform to a service must be done **end-to-end** across these three layers.

## Current behavior (today)

### Output layout

- The generic output path builder returns `base/{platform}/{username}` (see `common/output/paths.py`).
- The caller (`memoria.py`) then appends `/{service}` when `OutputInfo.service` is set.
- iMessage currently sets `OutputInfo(platform="iMessage", service=None, username_prefix="imessage")`, so its outputs land under:

```text
.../iMessage/{device_id}/
```

Separately, both iMessage processors also hard-code a `messages/` output subfolder inside the processor implementation.

### Preprocessed export markers

Preprocessors emit a standardized `metadata.json` with:

- `export_info.export_username`
- `export_info.platform`
- `export_info.service`

These markers are used by processors’ `detect()` methods via `ProcessorBase.is_preprocessed_for_platform(...)` to unambiguously recognize preprocessed directories.

iMessage preprocessing currently marks itself as:

- `export_info.platform = "iMessage"`
- `export_info.service = null`

### Dedup/ImageDescription “Source:” paths

Cross-processor dedup provenance is built in `common/dedup/description.py` using:

- a processor-name → `(platform, service)` mapping
- and relative-path logic that compares `(platform, username, service)` tuples

iMessage is currently treated as a platform (`"iMessage"`) with **no service**.

Additionally, there is special-case “consolidation” logic for iMessage that treats different device IDs as the same logical source for “same source” checks in descriptions.

## Target behavior (Option A)

### Canonical identity

- **Platform**: `Apple`
- **Service**: `imessage` (for iMessage processors), `photos` (for iCloud Photos processor)
- **Username**:
  - iMessage remains **device-scoped** (device label is stored in `export_username`)
  - iCloud Photos will likely be **account-scoped** (Apple ID / email / handle), depending on export detection

### Output layout (examples)

```text
.../Apple/{device_id}/imessage/
.../Apple/{apple_id}/photos/
```

### Dedup/ImageDescription “Source:” examples

```text
Source: Apple/iph13p/imessage
Source: Apple/john.doe@icloud.com/photos
```

## Required code changes

### 1) Output routing: make iMessage a service under Apple

Update iMessage processors’ `OutputInfo`:

- From:
  - `platform="iMessage"`
  - `service=None`
- To:
  - `platform="Apple"`
  - `service="imessage"`

Impacted files:

- `processors/imessage/processor.py` (`IMessageProcessor.get_output_info()`)
- `processors/imessage_imazing/processor.py` (`ImazingProcessor.get_output_info()`)

#### Remove/rename internal `messages/` output subfolder

Today, both iMessage processors write into a `messages/` subdirectory *inside* their `output_dir`.

With the new semantic model, the processor’s “service folder” should be `imessage/`, not `messages/`.

Implementation options (pick one and apply consistently to both iMessage processors):

- **Preferred**: rename the internal output subfolder from `messages/` → `imessage/`.
  - This keeps “service” consistent across filesystem layout, album naming, and ImageDescription.
- **Avoid**: keeping `messages/` as the actual folder while calling the service `imessage`.
  - That would create a mismatch between:
    - output structure (`.../Apple/{user}/messages`)
    - and semantic service (`imessage`)

### 2) Preprocessed export detection markers: platform/service must match new taxonomy

Update iMessage preprocessing markers:

- From: `platform="iMessage"`, `service=None`
- To: `platform="Apple"`, `service="imessage"`

Impacted file:

- `processors/imessage/preprocess.py` (`IMessagePreprocessor.get_platform()`, `get_service()`)

Then update processor detection code that checks preprocessed directories:

- From: `is_preprocessed_for_platform(..., platform="iMessage", service=None)`
- To: `is_preprocessed_for_platform(..., platform="Apple", service="imessage")`

Impacted files:

- `processors/imessage/processor.py`
- `processors/imessage_imazing/processor.py`

### 3) Dedup provenance mapping: iMessage now renders as Apple/.../imessage

Update `common/dedup/description.py`:

- Replace `PLATFORM_IMESSAGE = "iMessage"` with a new constant (or reuse an existing pattern):
  - `PLATFORM_APPLE = "Apple"`
- Ensure `"iMessage"` and `"iMessage-iMazing"` map to:
  - `(PLATFORM_APPLE, "imessage")`

This will update:

- primary `Source: ...` lines
- secondary `+...` lines
- relative `./` notation decisions (which depend on matching platform/username/service)

### 4) Consolidation semantics: keep “device consolidation” scoped to Apple/imessage

Important: today, description building treats iMessage as a “consolidating processor” and ignores username differences (device IDs) when deciding if two sources are the “same source.”

After the change, if this logic is keyed only on `platform == "Apple"`, it would accidentally apply to *all* Apple services (including `photos`).

Required refactor:

- Change the “consolidating source” check from being **platform-only** to being **(platform, service)** specific.
- Scope it to:

```text
(platform="Apple", service="imessage")
```

Impacted file:

- `common/dedup/description.py` (the “same source” logic and consolidating constants)

## Docs to update

To keep terminology consistent with `docs/Definitions.md` and the output tree examples:

- `docs/Definitions.md`
  - Update examples to treat iMessage as a **service** under platform Apple.
- `docs/Usage.md`
  - Update “Output Organization” examples:
    - from `iMessage/<device_id>/`
    - to `Apple/<device_id>/imessage/`
- `docs/Deduplication.md`
  - Update ImageDescription format examples from `iMessage/...` to `Apple/.../imessage`

## Tests to update

The following tests assert the current `iMessage` platform identity and/or consolidation behavior:

- `tests/test_common/test_output_paths.py`
  - Update expectations for iMessage OutputInfo and resulting paths.
- `tests/test_dedup/test_description_builder.py`
  - Update processor mapping tests (`get_platform_service("iMessage")` etc.).
  - Update consolidation tests to reflect consolidation being scoped to `Apple/imessage` (not all Apple).
- `tests/test_dedup/test_cross_processor_description.py`
  - Update any `PLATFORM_IMESSAGE` usage and expected “Source:” strings.

## Implementation checklist (Option A)

- [ ] Add `Apple` platform constants and `imessage/photos` service constants where appropriate
- [ ] Update iMessage processors’ `OutputInfo` to `platform="Apple"`, `service="imessage"`
- [ ] Rename internal iMessage output subfolder `messages/` → `imessage/` (both processors)
- [ ] Update iMessage preprocessor markers to `platform="Apple"`, `service="imessage"`
- [ ] Update `is_preprocessed_for_platform(...)` checks accordingly
- [ ] Update dedup processor mapping (`common/dedup/description.py`) so iMessage renders as `Apple/.../imessage`
- [ ] Refactor “consolidation” logic to be scoped to `(platform, service) == (Apple, imessage)`
- [ ] Update docs (`Definitions`, `Usage`, `Deduplication`) examples
- [ ] Update tests listed above

## Notes for the new iCloud Photos processor (Option A)

When implementing the new processor:

- Set `OutputInfo(platform="Apple", service="photos", username_prefix="apple" or appropriate detector)`
- Ensure its preprocessor emits `export_info.platform="Apple"`, `export_info.service="photos"`
- Ensure its catalog entries include `username` and `service` in dedup contexts (per `docs/Deduplication.md` expectations), so relative `./` notation works reliably.


