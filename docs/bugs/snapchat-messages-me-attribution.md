# Snapchat Messages: sender attribution should normalize export-owner to `"me"`

## Summary

In Snapchat Messages exports, Memoria may record the sender as the literal username string (from the export) even when the sender is the export owner. This leads to inconsistent attribution compared to other processors that normalize the export owner to `"me"`.

## Symptoms

- Description lines for Snapchat Messages may show the export username as if it were a third-party sender.
- Example (placeholders):

```text
Source: Snapchat/<export_username>/messages
  - <export_username> in "DM with friend_username": "..."
```

Expected:

```text
Source: Snapchat/<export_username>/messages
  - me in "DM with friend_username": "..."
```

## Why this happens

The raw `chat_history.json` includes both:

- `From`: sender username string (often the Snapchat username)
- `IsSender`: boolean indicating whether **you** (the export owner) sent the message

Memoria currently preserves `From` as `sender` without normalizing export-owner messages to `"me"`.

## Relevant raw export fields

Per `docs/Snapchat-Export.md`, message entries may include:

- `From`: Sender username
- `IsSender`: `true` if the export owner sent it

## Expected behavior (proposed)

When building message context / sender attribution for Snapchat Messages:

- If `IsSender` is `true`, normalize sender to `"me"`
- Otherwise, keep sender as-is (usually the other participant username)

Optionally, as a fallback where `IsSender` is missing or unreliable:

- If `From` equals the export username (case-insensitive), normalize sender to `"me"`

## Notes / constraints

- **No backwards compatibility requirement**: implement directly (no old-output shims).
- **No PII in docs**: use placeholders in examples.


