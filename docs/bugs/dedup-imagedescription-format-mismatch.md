## Bug: Merged `ImageDescription` formatting does not match `docs/Deduplication.md` spec

### Summary

Merged `ImageDescription` strings produced by cross-processor deduplication can violate the format rules documented in `docs/Deduplication.md` (see “ImageDescription Format”, around lines 482–556). The issues include:

- Missing `export_username` on secondary lines (notably Instagram and some Google entries).
- Redundant duplicate `+...` lines for the same secondary source.
- Missing caption/message context for Instagram posts.
- Dates shown even when redundant (same date as the primary media).
- Relative-path “dot notation” (`././service`, `./username/service`) not applied.

Example observed output:

```text
Source: Google/ethanriverpage/chat
  - Ethan Page in "Let me get uhhhh"
+Instagram/posts: (2016-08-05)
+Google/photos
+Instagram/posts: (2016-08-05)
+Google/chat: Ethan Page (2016-08-10)
```

### Expected behavior (per spec)

Given a primary source of `Google/ethanriverpage/chat`, secondaries should:

- Include the export username component for Instagram (`Instagram/<export_username>/posts`), unless the source truly lacks one.
- Never repeat identical secondary entries.
- Include caption context when available for posts.
- Omit redundant dates when the secondary date is equal to the primary media date.
- Use `././service` for same platform + same username + different service (e.g. `+././photos`).
- Use `./username/service` for same platform + different username.

### Root cause analysis (mapped to the 6 reported issues)

#### 1) Why is there not export username for the Instagram lines?

Because the merged description is built from **manifest “context”** dicts that frequently do not contain `username`.

The manifest construction stores `context` as `primary.get("metadata", {})` and `f.get("metadata", {})` for secondaries:

```345:370:common/dedup/service.py
        entry = {
            "match_type": match_type,
            "primary": {
                "processor": primary.get("_processor"),
                "source_path": primary.get("source_path"),
                "content_hash": primary_hash,
                "file_size": primary.get("file_size"),
                "context": primary.get("metadata", {}),
            },
            // ...
            "secondary": [
                {
                    "processor": f.get("_processor"),
                    "source_path": f.get("source_path"),
                    "content_hash": f.get("content_hash"),
                    "context": f.get("metadata", {}),
                }
                for f in secondary_files
            ],
            "merged_metadata": merged,
        }
```

Then `get_merged_description_for_file()` reads `context["username"]` (and only that key) when building `SourceEntry` objects:

```1377:1406:common/dedup/service.py
        primary_entry = SourceEntry(
            processor=primary_platform,
            username=primary_context.get("username", ""),
            service=primary_service,
            is_primary=True,
            sender=primary_context.get("sender", ""),
            conversation=primary_context.get("conversation", ""),
            content=primary_context.get("content", ""),
        )
        // ...
        entry = SourceEntry(
            processor=sec_platform,
            username=sec_context.get("username", ""),
            service=sec_service or sec_context.get("service", ""),
            is_primary=False,
            sender=sec_context.get("sender", ""),
            conversation=sec_context.get("conversation", ""),
            content=sec_context.get("content", ""),
        )
```

For Instagram Public Media and Instaloader, the catalog metadata extractors currently **do not populate** `username` (or `service`) at all:

```218:226:processors/instagram_public_media/processor.py
        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "caption" in entry:
                result["caption"] = entry["caption"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result
```

```171:178:processors/instaloader/processor.py
        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "caption" in entry:
                result["caption"] = entry["caption"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result
```

So when dedup later renders, `sec_context.get("username")` is empty → the `/username/` path component is omitted.

#### 2) `+Instagram/posts: (2016-08-05)` is repeated twice (redundant)

`build_image_description()` only deduplicates **same-source message detail lines**, not duplicated "different-source entries" that resolve to identical rendered lines.

The function splits secondaries into `same_source_entries` and `different_source_entries`, but then unconditionally emits a line per `different_source_entries` element:

```709:753:common/dedup/description.py
    # 3. Group secondaries by source identity
    same_source_entries = []
    different_source_entries = []
    // ...
    # 5. Add different-source entries with relative path notation
    for entry in different_source_entries:
        // ...
        line = _format_secondary_with_relative_path(entry, relative_path, source_budget)
        lines.append(line)
```

When multiple secondaries map to the same `(platform, username, service)` (or collapse there due to missing usernames), the renderer will repeat them.

#### 3) `+Instagram/posts: (2016-08-05)` isn’t showing caption context

The catalog extractors store Instagram post text as `caption`, but the merged-description builder expects `content`.

- Instagram catalog extractors emit `caption` (see above).
- `get_merged_description_for_file()` only reads `sec_context["content"]`:

```1397:1406:common/dedup/service.py
            entry = SourceEntry(
                processor=sec_platform,
                username=sec_context.get("username", ""),
                service=sec_service or sec_context.get("service", ""),
                is_primary=False,
                sender=sec_context.get("sender", ""),
                conversation=sec_context.get("conversation", ""),
                content=sec_context.get("content", ""),
            )
```

So caption text is effectively dropped on the floor during the manifest → `SourceEntry` conversion.

#### 4) If the date for a `+` entry is the same as the photo, it shouldn’t be shown

The merged-description builder currently has **no logic to suppress redundant dates**, because:

- `get_merged_description_for_file()` does not populate `primary_entry.timestamp` at all (it only sets sender/conversation/content/username/service).
- `_format_secondary_with_relative_path()` always appends `({date})` for secondaries when `entry.timestamp` is set, with no comparison against a “primary media date”.

(The spec rule exists in documentation, but no implementation currently enforces it.)

#### 5) `+Google/chat` should be `+./{export_username}/...` (username component is missing)

Same underlying issue as (1): `username` is frequently absent from the secondary’s manifest `context`, so the formatter cannot emit `/username/`.

For Google Chat, the catalog extractor also omits `username`:

```171:180:processors/google_chat/processor.py
        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "conversation_name" in entry:
                result["conversation"] = entry["conversation_name"]
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result
```

So when a Google Chat source is rendered as a secondary, it may degrade to `+Google/chat` (or another non-spec path) because the renderer has no username to compare and no username to print.

#### 6) `+Google/photos` should be `+././photos`

The “dot notation” logic in `format_relative_source_path()` depends on being able to compare `(platform, username, service)` between primary and secondary.

If `secondary.username` is missing, the function cannot determine “same platform + same username”, so it cannot confidently output `././service`. The correct relative output requires `username` to be present and accurate for both entries.

### Contributing design issue

The dedup-description pipeline implicitly assumes all processors’ catalog metadata is normalized into the same keys (`username`, `service`, `content`, `conversation`, `sender`, `timestamp`). In practice, many processors’ `metadata_extractor` functions:

- omit `username` entirely,
- use `caption` instead of `content`,
- and don’t provide `service` (so service derives only from processor mapping, not from entry context).

As a result, `common/dedup/description.py` cannot faithfully implement the documented format rules, even though its relative-path logic is correct when given complete `SourceEntry` data.

### Impact

- Provenance strings become ambiguous (cannot attribute to a specific export user).
- Redundant lines bloat EXIF fields and reduce readability.
- Instagram post captions are silently dropped in merged provenance.
- Spec-defined formatting rules (relative paths + date suppression) aren’t enforced consistently.

### Suggested fixes (implementation direction)

- **Normalize manifest context fields** at catalog time:
  - Always include `username` (export username) and `service` in the per-file catalog metadata that becomes manifest `context`.
  - Normalize `caption` → `content` (or teach the manifest-to-`SourceEntry` step to treat `caption` as fallback for `content`).
- **Deduplicate “different source” rendered entries** in `build_image_description()`:
  - Track a `seen_secondary_lines` or a stable dedup key like `(relative_path, sender, conversation, content_prefix, date)` and skip repeats.
- **Implement “redundant date suppression”**:
  - Populate `primary_entry.timestamp` (primary media timestamp) and only include `({date})` for a secondary if it differs from the primary date.

### Reproduction (minimal)

1. Produce a dedup manifest where a media file appears in both:
   - a Google source (e.g. Chat or Photos), and
   - an Instagram posts source (Instagram Public Media and/or Instaloader),
   and at least two secondaries collapse into the same `Instagram/<missing-username>/posts` identity.
2. Call `common.dedup.service.get_merged_description_for_file(manifest, primary_source_path)`.
3. Observe missing usernames, missing caption context, repeated Instagram lines, and non-relative Google paths.
