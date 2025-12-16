# vCard Schema

This document describes the vCard (.vcf) file format as used by Apple Contacts, relevant for contact matching and photo extraction in Memoria.

## Overview

| Property | Value |
|----------|-------|
| Format | Plain text (UTF-8) |
| Extension | `.vcf` |
| Standard | vCard 3.0 (RFC 2426) |
| Source | Apple Contacts.app export |

---

## File Structure

A vCard file contains one or more contact entries, each delimited by `BEGIN:VCARD` and `END:VCARD` markers:

```text
BEGIN:VCARD
VERSION:3.0
FN:John Smith
TEL;TYPE=CELL:+1 (404) 555-1234
EMAIL;TYPE=HOME:john@example.com
PHOTO;ENCODING=BASE64;TYPE=JPEG:/9j/4AAQSkZJRg...
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Jane Doe
TEL:+1 (404) 555-5678
END:VCARD
```

### Line Continuation

Long values (especially photos) may span multiple lines using "folding":

- Continuation lines begin with a space or tab character
- These lines should be concatenated with the previous line (stripping the leading whitespace)

```text
PHOTO;ENCODING=BASE64;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMC
 AgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMCwsKCwsNDhIQDQ4RDgsLEBYQER
 MUFRUVDAwXGBcZGBQWFBQ=
```

---

## Relevant Properties

### FN (Full Name)

The formatted display name of the contact. Used to identify senders in message metadata.

```text
FN:John Smith
FN;CHARSET=UTF-8:John Smith
```

**Parsing:**

- Property name may include parameters (separated by `;`)
- Value follows the colon (`:`)
- All text after the first colon is treated as the name value

---

### TEL (Telephone)

Phone numbers associated with the contact. Used to match iMessage handles.

```text
TEL:+14045551234
TEL;TYPE=CELL:+1 (404) 555-1234
TEL;TYPE=WORK,VOICE:+1-404-555-1234
```

**Parsing:**

- TYPE parameters can be ignored (all phone numbers treated equally)
- Phone numbers should be normalized for matching (remove spaces, dashes, parentheses)

---

### EMAIL (Email Address)

Email addresses associated with the contact. Used to match iMessage handles.

```text
EMAIL:john@example.com
EMAIL;TYPE=WORK:john.smith@company.com
```

**Parsing:**

- TYPE parameters can be ignored
- Emails should be normalized to lowercase for matching

---

### PHOTO (Contact Photo)

Base64-encoded photograph of the contact. Can be extracted as a media file.

```text
PHOTO;ENCODING=BASE64;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD...
PHOTO;ENCODING=b;TYPE=PNG:iVBORw0KGgoAAAANSUhEUgAAA...
```

**Parsing:**

- Look for `ENCODING=BASE64` or `ENCODING=b` parameter
- Photo data may span multiple lines (using line continuation)
- Strip whitespace before base64 decoding
- Common formats: JPEG, PNG, GIF

---

## Normalization

### Phone Numbers

Phone numbers should be normalized for consistent matching with iMessage handles:

| Input | Normalized |
|-------|------------|
| `+1 (404) 555-1234` | `+14045551234` |
| `(404) 555-1234` | `4045551234` |
| `404-555-1234` | `4045551234` |

### Email Addresses

Email addresses should be lowercase:

| Input | Normalized |
|-------|------------|
| `John.Smith@Example.COM` | `john.smith@example.com` |

---

## Example vCard Entry

```text
BEGIN:VCARD
VERSION:3.0
PRODID:-//Apple Inc.//macOS 14.0//EN
N:Smith;John;Michael;;
FN:John Smith
ORG:Acme Corporation;
TEL;TYPE=CELL:+1 (404) 555-1234
TEL;TYPE=WORK:+1 (404) 555-5678
EMAIL;TYPE=HOME:john.smith@gmail.com
EMAIL;TYPE=WORK:jsmith@acme.com
PHOTO;ENCODING=BASE64;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMC
 AgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMCwsKCwsNDhIQDQ4RDgsLEBYQER
 MUFRUVDAwXGBcZGBQWFBQ=
END:VCARD
```

**What Memoria extracts:**

- `FN:John Smith` - display name (for metadata on media from this contact)
- `TEL` entries - phone numbers (for matching to iMessage handles)
- `EMAIL` entries - email addresses (for matching to iMessage handles)
- `PHOTO` - contact photo (as extractable media)

**Ignored properties:**

- VERSION, PRODID, N, ORG, TITLE, ADR, BDAY, NOTE, REV, UID, URL, X-* extensions

---

## Related Documentation

- [iMessage Schema](iMessage-Schema.md) - Database schema for iMessage exports that use vCard contacts for handle matching
