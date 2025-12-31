#!/usr/bin/env python3
"""Unicode text normalization utilities."""

# Unicode characters to normalize to ASCII equivalents
_TEXT_REPLACEMENTS = {
    # Quotation marks
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
    "\u201a": "'",  # SINGLE LOW-9 QUOTATION MARK
    "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK
    "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    "\u2032": "'",  # PRIME (single)
    "\u2033": '"',  # DOUBLE PRIME
    # Dashes
    "\u2013": "--",  # EN DASH
    "\u2014": "--",  # EM DASH
    # Whitespace
    "\u2028": " ",  # LINE SEPARATOR
    "\u2029": " ",  # PARAGRAPH SEPARATOR
    # Emoji modifiers
    "\ufe0f": "",  # VARIATION SELECTOR-16 (emoji modifier)
}


def normalize_text(text: str) -> str:
    """Normalize Unicode characters in text to ASCII equivalents.

    Replaces common "smart" or "curly" quotes, dashes, and special whitespace
    with standard ASCII equivalents. Also removes emoji modifiers for
    consistent text storage and display across systems.

    Args:
        text: The input text to normalize.

    Returns:
        Text with Unicode characters replaced by ASCII equivalents.
    """
    if not text:
        return text

    for unicode_char, ascii_char in _TEXT_REPLACEMENTS.items():
        text = text.replace(unicode_char, ascii_char)

    return text
