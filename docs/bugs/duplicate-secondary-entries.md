# Bug Report: Duplicate Secondary Entries in ImageDescription

## Summary

When cross-processor deduplication produces an ImageDescription with multiple secondary sources, entries from the same source (platform/username/service) can appear multiple times instead of being deduplicated. This results in redundant output like:

```
Source: Discord/example_user
  - in "DM with example_user2"
+Instagram/example_user/posts: "I got tazed..." (2018-08-20)
+Google/example_user/photos
+Instagram/example_user/posts: "I got tazed..." (2018-08-19)
+Instagram/example_user/posts: "I got tazed..." (2018-08-20)
```

The same Instagram post appears 3 times when it should appear once.

---

## Verified Findings (current code)

This section reflects what the current code actually does (as of 2026-01-04), and why it produces the duplicate secondary entries shown in the Summary.

### Bug 1 (confirmed): Variable truncation breaks string-based deduplication

**Root cause:** `build_image_description()` deduplicates `different_source_entries` using the *rendered* string (`seen_secondary_lines`), but the per-line truncation budget (`source_budget`) is computed from `remaining`, which decreases after each appended line. If a line is long enough to be truncated, the *same logical entry* can format into *different strings* depending on when it is encountered, so exact-string deduplication fails.

**Location:** `common/dedup/description.py` (`build_image_description()` loop over `different_source_entries`)

**Problem:** The `seen_secondary_lines` deduplication relies on exact string matching, but the truncation budget varies per iteration:

```python
for entry in different_source_entries:
    source_budget = min(max_per_source, remaining)  # remaining DECREASES each iteration
    ...
    line = _format_secondary_with_relative_path(entry, relative_path, source_budget)

    if line in seen_secondary_lines:  # Exact string match
        continue
```

In `_format_secondary_with_relative_path` (it always truncates to `max_chars`):

```python
return truncate_text(result, max_chars)  # max_chars = source_budget (varies!)
```

When the same content appears multiple times:

- First entry gets `source_budget = 250`
- After adding lines, `remaining` decreases
- Later entry gets `source_budget = 180` (for example)
- If the line is long enough to hit truncation, different budgets produce different strings
- String comparison fails, duplicate is emitted

**Impact:** Entries with identical content produce different truncated strings and bypass deduplication.

### Bug 2 (confirmed): Context-based deduplication is only applied relative to the primary

**Root cause:** `build_image_description()` splits secondaries into `same_source_entries` and `different_source_entries` by calling `_same_source(primary, entry)`. That means “same source” is defined as “same as the primary,” not “same as each other.” As a result, multiple secondaries from the *same* (platform, username, service) are never context-deduped if the primary is from a different platform (e.g., Discord primary + multiple Instagram exports of the same account).

**Location:** `common/dedup/description.py` (`build_image_description()` grouping logic and loops)

**Problem:** Secondary entries are split into two groups:

1. `same_source_entries` - uses context-based deduplication via `_has_unique_context()`
2. `different_source_entries` - only uses string-based deduplication

When the primary is Discord, all Instagram entries are "different source." But multiple Instagram entries from **different exports of the same account** share the same source as *each other* - they should be deduplicated among themselves.

Current logic:

```python
for entry in secondary:
    if _same_source(primary, entry):  # Only compares to PRIMARY
        same_source_entries.append(entry)
    else:
        different_source_entries.append(entry)
```

**Impact:** Multiple exports of the same Instagram account (e.g., `instagram-ethantastic_-20210725` and `instagram-ethantastic_-20221116`) produce duplicate secondary entries for the same post.

### Bug 3 (contributing factor): Secondary timestamps come from per-export context, not canonical merged metadata

**What’s true in current code:** The dedup manifest computes a canonical timestamp (`merged_metadata["timestamp"]`) as the earliest timestamp across sources, but `get_merged_description_for_file()` builds `SourceEntry` objects from each secondary’s own `context` (including its own timestamp), and passes those into `build_image_description()`.

This is why two exports of the same account can display the “same post” with different dates in the description. This does **not** create duplicates by itself, but it makes duplicates more confusing and (once Bug 2 is fixed) it means the “one kept” timestamp will be whichever export’s entry happened to be kept, unless we explicitly normalize.

**Location:** `common/dedup/service.py` (`get_merged_description_for_file()` secondary entry construction)

**Problem:** When building secondary source entries, each uses its original timestamp from its own export context:

```python
for sec in group.get("secondary", []):
    sec_context = sec.get("context", {})  # Original context with original timestamp
    entry = create_source_entry(
        processor=sec_platform,
        metadata=sec_context,
        ...
    )
```

The merged/canonical timestamp (earliest across all sources) is stored in `merged_metadata` (see `DeduplicationService._merge_metadata()`), but is NOT applied to description entries.

**Impact:** The same Instagram post shows as `(2018-08-19)` from one export and `(2018-08-20)` from another, likely due to timezone handling differences between export dates.

### Bug 4 (confirmed): Redundant dates are not suppressed when they match the file’s EXIF timestamp

**Expected behavior:** If the output file’s EXIF timestamp date matches a secondary entry’s date, the secondary entry should omit the redundant `({YYYY-MM-DD})` suffix to save space and reduce visual noise.

**Observed behavior:** Secondary lines always include `({YYYY-MM-DD})` whenever the `SourceEntry` has a timestamp, even when it matches the file’s EXIF timestamp date.

Example secondary line (anonymized):

```
+Instagram/example_user/posts: "I got tazed probably over 50 times last night ..." (2018-08-20)
```

Example EXIF timestamp on the file:

```
08/20/2018 02:40:10.000 AM
```

So `(2018-08-20)` is redundant and should be omitted in the description.

**Root cause:** The description formatter appends dates unconditionally when `entry.timestamp` exists (e.g., `_format_secondary_with_relative_path()` and `format_secondary_source()`), but the formatting functions are not provided with (and do not look up) the output file’s EXIF timestamp/date, so they cannot decide to suppress it.

- Formatting location: `common/dedup/description.py` (`_format_secondary_with_relative_path()`, `format_secondary_source()`, also `_format_message_line()` for same-source “+details” lines)
- EXIF writing layer: `common/media/exiftool.py` writes datetime tags but has no “strip redundant date from description” policy hook.

**Suggested fix direction:** Thread a `reference_date` (or `reference_timestamp`) into `build_image_description()` (and the helpers) representing the output file’s EXIF timestamp (or the timestamp being written to EXIF). When formatting any secondary (and secondary message-detail) line, omit `({YYYY-MM-DD})` if `format_timestamp(entry.timestamp) == format_timestamp(reference_timestamp)` (or date-only comparison), taking care around timezone normalization.

---

## Expected Behavior

1. Entries from the same (platform, username, service) should be deduplicated by context, regardless of which source is primary
2. When multiple exports of the same account contain the same post, only ONE entry should appear
3. If timestamps differ due to export quirks (timezone handling), the canonical (earliest) timestamp should be used for same-source entries

---

## Proposed Fixes

### Fix 1: Consistent Truncation Before Deduplication

Truncate all secondary lines to the same budget BEFORE comparison:

```python
# Calculate consistent truncation budget upfront
max_line_budget = max_per_source  # Fixed, not decreasing

for entry in different_source_entries:
    line = _format_secondary_with_relative_path(entry, relative_path, max_line_budget)

    if line in seen_secondary_lines:
        continue
    ...
```

Or better: deduplicate BEFORE formatting by using a context key.

### Fix 2: Group Different-Source Entries by Source Identity

After separating same/different source entries, further group different-source entries by their own (platform, username, service) and apply context-based deduplication within each group:

```python
# Group different-source entries by their source identity
source_groups: Dict[Tuple[str, str, str], List[SourceEntry]] = {}
for entry in different_source_entries:
    key = (entry.processor, entry.username, entry.service)
    source_groups.setdefault(key, []).append(entry)

# Deduplicate within each source group
deduplicated_different_source = []
for source_key, entries in source_groups.items():
    seen_contexts = set()
    for entry in entries:
        if _has_unique_context(entry, seen_contexts):
            seen_contexts.add(_get_context_key(entry))
            deduplicated_different_source.append(entry)
```

### Fix 3: Use Canonical Timestamp for Same-Source Secondary Entries

When multiple secondary entries share the same source identity, normalize their timestamps to a stable canonical value (preferably `merged_metadata["timestamp"]`, or at least the earliest among that identity-group) so the kept entry doesn’t depend on list ordering:

```python
# For same-source secondary entries, use canonical timestamp
canonical_ts = group.get("merged_metadata", {}).get("timestamp")

for sec in group.get("secondary", []):
    if _same_source_identity(sec, other_sec_in_group):
        entry.timestamp = canonical_ts  # Normalize to earliest
```

---

## Test Case

**Setup:**

- Two Instagram exports for the same account: `instagram-example_user-20210725` and `instagram-example_user-20221116`
- Same post appears in both exports with slightly different timestamps
- This file is also present in Discord and Google Photos

**Expected Output:**

```
Source: Discord/example_user
  - in "DM with example_user2"
+Instagram/example_user/posts: "I got tazed..." (2018-08-19)
+Google/example_user/photos
```

**Actual Output:**

```
Source: Discord/example_user
  - in "DM with example_user2"
+Instagram/example_user/posts: "I got tazed..." (2018-08-20)
+Google/example_user/photos
+Instagram/example_user/posts: "I got tazed..." (2018-08-19)
+Instagram/example_user/posts: "I got tazed..." (2018-08-20)
```

---

## Priority

**Medium-High** - Affects data quality and readability of embedded metadata. Does not cause data loss but creates confusing/redundant provenance information.

---

## Resolution (2026-01-04, updated 2026-01-05)

All four bugs have been fixed. A follow-up refactoring unified the deduplication logic between `build_image_description()` and `_get_description_for_file()`.

### Bug 1 Fix: Key-Based Deduplication Before Formatting

Added `_get_secondary_dedup_key()` helper that computes a stable, hashable key combining source identity and context. Deduplication now happens BEFORE formatting, using these keys rather than rendered strings with variable truncation budgets.

**Key changes:**

- New `_get_source_identity_key()` and `_get_secondary_dedup_key()` helpers in `common/dedup/description.py`
- Added `deduplicate_secondary_entries()` as the **canonical deduplication helper** used by both paths
- Formatting uses a fixed `max_per_source` budget (entries that don't fit are skipped entirely)

### Bug 2 Fix: Group Different-Source Entries by Their Own Source Identity

`deduplicate_secondary_entries()` groups entries by source identity and applies context-based deduplication within each group, not just relative to the primary.

**Key changes:**

- Source grouping via `_get_source_identity_key()`
- For consolidating processors (e.g., iMessage), uses `_has_unique_context()` for partial matching
- Multiple exports of the same account now correctly collapse to one entry
- Both `build_image_description()` and `_get_description_for_file()` use `deduplicate_secondary_entries()`

### Bug 3 Fix: Normalize Timestamps to Canonical Merged Metadata

Both description paths normalize secondary entry timestamps to the canonical value from `merged_metadata["timestamp"]`.

**Key changes:**

- `get_merged_description_for_file()` extracts canonical timestamp from `merged_metadata`
- `_get_description_for_file()` in exiftool.py also normalizes timestamps
- Both primary and secondary entries have their timestamps normalized

### Bug 4 Fix: Suppress Redundant Dates Matching EXIF-Written Date

Added `reference_timestamp` parameter to formatting functions. When a secondary entry's date matches the reference date, the `({YYYY-MM-DD})` suffix is omitted.

**Key changes:**

- Added `reference_timestamp` parameter to:
  - `build_image_description()`
  - `_format_secondary_with_relative_path()`
  - `format_secondary_source()`
  - `_format_message_line()` (for `is_secondary=True`)
- Date comparison uses `format_timestamp()` output for consistent date-only matching

### Regression Tests

Added `TestDuplicateSecondaryEntriesFixes` class in `tests/test_dedup/test_cross_processor_description.py` with the following tests:

- `test_bug1_truncation_order_does_not_affect_dedup` - verifies identical entries with long content dedupe to one line
- `test_bug2_same_source_secondaries_dedupe_among_themselves` - verifies multiple exports of same account collapse to one entry
- `test_bug3_timestamps_normalized_to_canonical` - verifies per-export timestamps are not used
- `test_bug3_canonical_date_shown_when_different_from_reference` - positively asserts canonical date appears when reference differs
- `test_bug3_manifest_canonical_date_shown_when_exif_differs` - integration test for manifest path
- `test_bug4_redundant_dates_suppressed` - verifies dates matching reference are omitted
- `test_bug4_format_secondary_source_suppresses_redundant_date` - unit test for format function
- `test_same_source_detail_line_date_suppression` - verifies same-source detail lines suppress redundant dates
- `test_bug2_different_source_groups_kept_separate` - verifies distinct sources are NOT merged
- `test_integration_manifest_to_description_deduplicates` - end-to-end test through manifest
- `test_deduplicate_secondary_entries_uses_canonical_helper` - verifies canonical helper behavior

---

## Related Code

- `common/dedup/description.py`:
  - `deduplicate_secondary_entries()` - canonical deduplication helper
  - `_get_source_identity_key()`, `_get_secondary_dedup_key()`, `_get_context_key()` - key helpers
  - `build_image_description()`, `filter_secondary_sources()` - callers
- `common/dedup/service.py`: `get_merged_description_for_file()`, `_merge_metadata()`
- `common/media/exiftool.py`: `_get_description_for_file()` - uses `deduplicate_secondary_entries()`
- `common/dedup/config.py`: `msg_truncate`, `desc_max_per_source`
- `tests/test_dedup/test_cross_processor_description.py`: `TestDuplicateSecondaryEntriesFixes`
