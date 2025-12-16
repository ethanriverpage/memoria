#!/usr/bin/env python3
"""
vCard Parser for iMessage Contact Matching

Parses Apple Contacts.app vCard exports to build a handle-to-name mapping
for resolving iMessage sender/recipient identities.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VCardParser:
    """Parser for vCard (.vcf) files exported from Apple Contacts.

    Extracts phone numbers and email addresses mapped to display names
    for matching against iMessage handle IDs.

    Attributes:
        vcf_path: Path to the vCard file
        handle_to_name: Mapping of normalized handles to display names
    """

    def __init__(self, vcf_path: Path):
        """Initialize parser with path to vCard file.

        Args:
            vcf_path: Path to the .vcf file to parse
        """
        self.vcf_path = vcf_path
        self.handle_to_name: Dict[str, str] = {}

    def parse(self) -> Dict[str, str]:
        """Parse the vCard file and build handle-to-name mapping.

        Returns:
            Dictionary mapping normalized handle IDs (phone/email) to display names.
            Phone numbers are normalized to digits only (preserving leading +).
            Email addresses are lowercased.
        """
        if not self.vcf_path.exists():
            logger.warning(f"vCard file not found: {self.vcf_path}")
            return {}

        try:
            content = self._read_and_unfold(self.vcf_path)
            vcards = self._split_vcards(content)

            for vcard_lines in vcards:
                self._parse_vcard(vcard_lines)

            logger.info(
                f"Parsed {len(vcards)} contacts, "
                f"{len(self.handle_to_name)} handles mapped"
            )
            return self.handle_to_name

        except Exception as e:
            logger.error(f"Failed to parse vCard file {self.vcf_path}: {e}")
            return {}

    def _read_and_unfold(self, path: Path) -> str:
        """Read vCard file and handle line folding.

        vCard uses "folding" for long lines where continuation lines
        start with a space or tab. This method joins folded lines.

        Args:
            path: Path to the vCard file

        Returns:
            Content with folded lines joined
        """
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Handle line folding: lines starting with space/tab are continuations
        # Replace "\r\n " or "\n " with empty string to join
        content = re.sub(r"\r?\n[ \t]", "", content)

        return content

    def _split_vcards(self, content: str) -> List[List[str]]:
        """Split content into individual vCard entries.

        Args:
            content: Full vCard file content (already unfolded)

        Returns:
            List of vCard entries, each as a list of property lines
        """
        vcards = []
        current_vcard: List[str] = []
        in_vcard = False

        for line in content.splitlines():
            line = line.strip()

            if line.upper() == "BEGIN:VCARD":
                in_vcard = True
                current_vcard = []
            elif line.upper() == "END:VCARD":
                if current_vcard:
                    vcards.append(current_vcard)
                in_vcard = False
                current_vcard = []
            elif in_vcard and line:
                current_vcard.append(line)

        return vcards

    def _parse_vcard(self, lines: List[str]) -> None:
        """Parse a single vCard entry and update handle mapping.

        Args:
            lines: List of property lines for one vCard
        """
        display_name: Optional[str] = None
        phones: List[str] = []
        emails: List[str] = []

        for line in lines:
            prop_name, value = self._parse_property(line)

            if prop_name == "FN" and value:
                display_name = value
            elif prop_name == "TEL" and value:
                phones.append(value)
            elif prop_name == "EMAIL" and value:
                emails.append(value)

        # If no display name, skip this contact
        if not display_name:
            return

        # Map all phone numbers to this display name
        # Store both US variants (+1XXXXXXXXXX and XXXXXXXXXX) for better matching
        for phone in phones:
            normalized = self._normalize_phone(phone)
            if normalized:
                self.handle_to_name[normalized] = display_name

                # For US numbers, also store the alternate format
                if normalized.startswith("+1") and len(normalized) == 12:
                    # +1XXXXXXXXXX -> also store XXXXXXXXXX
                    self.handle_to_name[normalized[2:]] = display_name
                elif len(normalized) == 10 and not normalized.startswith("+"):
                    # XXXXXXXXXX -> also store +1XXXXXXXXXX
                    self.handle_to_name[f"+1{normalized}"] = display_name

        # Map all email addresses to this display name
        for email in emails:
            normalized = self._normalize_email(email)
            if normalized:
                self.handle_to_name[normalized] = display_name

    def _parse_property(self, line: str) -> Tuple[str, str]:
        """Parse a vCard property line into name and value.

        Handles property parameters (e.g., TEL;TYPE=CELL:+1234567890).

        Args:
            line: A single vCard property line

        Returns:
            Tuple of (property_name, value)
        """
        # Find the colon separating property from value
        colon_idx = line.find(":")
        if colon_idx == -1:
            return ("", "")

        prop_part = line[:colon_idx]
        value = line[colon_idx + 1 :]

        # Property name is before any semicolon (parameters follow)
        semicolon_idx = prop_part.find(";")
        if semicolon_idx != -1:
            prop_name = prop_part[:semicolon_idx].upper()
        else:
            prop_name = prop_part.upper()

        return (prop_name, value)

    def _normalize_phone(self, phone: str) -> Optional[str]:
        """Normalize phone number for consistent matching.

        Strips all non-digit characters except leading +.

        Args:
            phone: Raw phone number string

        Returns:
            Normalized phone number or None if invalid

        Examples:
            "+1 (404) 555-1234" -> "+14045551234"
            "(404) 555-1234" -> "4045551234"
            "404-555-1234" -> "4045551234"
        """
        if not phone:
            return None

        # Check if number starts with +
        has_plus = phone.startswith("+")

        # Remove all non-digit characters
        digits = re.sub(r"\D", "", phone)

        if not digits:
            return None

        # Restore leading + if present
        if has_plus:
            return f"+{digits}"

        return digits

    def _normalize_email(self, email: str) -> Optional[str]:
        """Normalize email address for consistent matching.

        Lowercases the entire email address.

        Args:
            email: Raw email address string

        Returns:
            Normalized (lowercased) email or None if empty
        """
        if not email:
            return None

        return email.lower().strip()


def parse_contacts_vcf(vcf_path: Path) -> Dict[str, str]:
    """Convenience function to parse a vCard file.

    Args:
        vcf_path: Path to the .vcf file

    Returns:
        Dictionary mapping normalized handles to display names
    """
    parser = VCardParser(vcf_path)
    return parser.parse()
