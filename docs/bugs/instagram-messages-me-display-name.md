# Instagram Messages: derive `"me"` from `personal_information.html` (display name vs username)

## Summary

In Instagram Messages exports, the **message sender shown in `message_*.html` is typically the account’s display name**, not the `@username`.

Memoria currently treats the export owner identity as the export username (usually derived from the directory name), which causes outgoing messages authored under the **display name** to not be recognized as `"me"`.

## Symptoms

- Message sender values may look like a human name (display name), not an `@handle`.
- In a DM where the export owner’s display name differs from their username, Memoria may attribute messages to the display name string instead of `"me"`.
- Downstream effects include inconsistent `Sender:` fields in embedded descriptions and worse cross-source alignment (e.g., with iMessage where outgoing is `"me"`).

## Why this happens

Instagram’s HTML export for Messages does not reliably embed the owner’s `@username` inside conversation messages. Instead, it uses the **display name** string when rendering sender identity.

However, the export includes a separate personal information page containing both:

- **Username** (the `@handle`, without `@`)
- **Name** (the display name used throughout UI and often used in Messages sender fields)

## Where to find the owner identity in the source export

Both current and legacy exports include `personal_information.html`, but the path differs.

### Current format (2023+)

```text
<export_root>/personal_information/personal_information/personal_information.html
```

### Legacy format (pre-2023)

```text
<export_root>/personal_information/personal_information.html
```

## What the HTML looks like (current format)

The `personal_information.html` page includes a `User Information` section with rows similar to:

- `Username` → `<username>`
- `Name` → `<display name>`

Example (placeholders):

```text
Username: jane_doe
Name: Jane Doe
```

Legacy exports may not include `Name` (display name) in the same way; in observed legacy samples, `Username` is present but `Name` may be absent.

## Expected behavior (proposed)

For Instagram Messages processing, treat the export owner’s identity as a set of **aliases**:

- The canonical **export username** (directory-derived / detected username)
- The personal-information **Username** (if present)
- The personal-information **Name** (display name, if present)

Then, when parsing a message sender:

- If the sender matches any of those aliases (after basic trimming / normalization), rewrite the sender to `"me"`.

This makes outgoing messages consistent with other processors that label the export owner as `"me"` (e.g., iMessage).

## Notes / constraints

- **No backwards compatibility requirement**: this change should be applied directly when implemented; no legacy output-format shims.
- **No PII in docs**: use placeholders in examples.


