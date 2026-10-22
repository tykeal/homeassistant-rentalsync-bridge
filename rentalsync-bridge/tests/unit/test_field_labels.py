# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for field label humanising helpers (issue #127)."""

import pytest
from src.repositories.available_field_repository import _camel_to_display
from src.utils.field_labels import (
    camel_to_display,
    fallback_label,
    humanize_label,
    is_legacy_derived_label,
    is_object_id,
    legacy_display_name,
)

# The ObjectId from the original bug report.
OBJECT_ID = "6a5a9f38050dfd00134fe27e"
MANGLED = "6 A 5 A 9 F 38050 Dfd 00134 Fe 27 E"


class TestObjectIdPassthrough:
    """An unresolved ObjectId must not be exploded into noise."""

    def test_object_id_passes_through_verbatim(self):
        """Test a 24-char hex key is returned unchanged."""
        assert camel_to_display(OBJECT_ID) == OBJECT_ID

    def test_repository_helper_passes_object_id_through(self):
        """Test the repository's helper inherits the passthrough."""
        assert _camel_to_display(OBJECT_ID) == OBJECT_ID

    def test_uppercase_object_id_passes_through(self):
        """Test hex case does not matter."""
        upper = OBJECT_ID.upper()
        assert camel_to_display(upper) == upper

    def test_legacy_rules_still_mangle_object_ids(self):
        """Test the legacy helper reproduces the pre-fix output.

        Label repair relies on recognising this exact string.
        """
        assert legacy_display_name(OBJECT_ID) == MANGLED

    @pytest.mark.parametrize(
        "value",
        [
            "6a5a9f38050dfd00134fe27",  # 23 chars
            "6a5a9f38050dfd00134fe27ef",  # 25 chars
            "6a5a9f38050dfd00134fe27z",  # non-hex character
        ],
    )
    def test_near_miss_keys_are_not_object_ids(self, value):
        """Test only exactly 24 hex characters count as an ObjectId."""
        assert is_object_id(value) is False

    def test_normal_keys_are_still_humanised(self):
        """Test ordinary keys keep their existing behaviour."""
        assert camel_to_display("guestFirstName") == "Guest First Name"
        assert camel_to_display("door_code") == "Door Code"


class TestHumanizeLabel:
    """Provider-supplied labels are prose or slugs; both must work."""

    def test_prose_label_is_used_verbatim(self):
        """Test a label containing spaces keeps provider capitalisation."""
        assert humanize_label("Test door lock code") == "Test door lock code"

    def test_snake_case_slug_is_title_cased(self):
        """Test the Guesty variable form becomes a readable label."""
        assert humanize_label("test_door_lock_code") == "Test Door Lock Code"

    def test_camel_case_slug_is_split(self):
        """Test camelCase slugs are split into words."""
        assert humanize_label("testDoorLockCode") == "Test Door Lock Code"

    def test_kebab_case_slug_is_title_cased(self):
        """Test kebab-case slugs are humanised too."""
        assert humanize_label("test-door-lock-code") == "Test Door Lock Code"

    def test_blank_label_yields_empty_string(self):
        """Test whitespace-only labels are rejected, not emitted."""
        assert humanize_label("   ") == ""

    def test_surrounding_whitespace_is_trimmed(self):
        """Test padding does not turn a slug into prose."""
        assert humanize_label("  test_door_lock_code  ") == "Test Door Lock Code"


class TestLegacyLabelDetection:
    """Only auto-generated labels may be replaced."""

    def test_legacy_mangled_label_is_recognised(self):
        """Test the pre-fix ObjectId mangling counts as auto-generated."""
        assert is_legacy_derived_label(OBJECT_ID, MANGLED) is True

    def test_current_derived_label_is_recognised(self):
        """Test a label matching today's rules is still repairable."""
        assert is_legacy_derived_label(OBJECT_ID, OBJECT_ID) is True
        assert is_legacy_derived_label("door_code", "Door Code") is True

    def test_user_written_label_is_not_recognised(self):
        """Test a label a person chose is never treated as derived."""
        assert is_legacy_derived_label(OBJECT_ID, "Door Code") is False


class TestFallbackLabel:
    """Without a definition, an ObjectId is shown raw rather than mangled."""

    def test_object_id_falls_back_to_the_raw_identifier(self):
        """Test the honest-but-ugly fallback is the raw key."""
        assert fallback_label(OBJECT_ID) == OBJECT_ID

    def test_readable_keys_need_no_fallback(self):
        """Test ordinary keys humanise fine and are left alone."""
        assert fallback_label("door_code") is None
