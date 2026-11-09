# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the fixture PII guard script.

Sample private values are assembled at runtime so this module does not
itself trip the guard that scans the test tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_fixture_pii.py"


def _load_module() -> ModuleType:
    """Import the script module from its file path."""
    spec = importlib.util.spec_from_file_location("check_fixture_pii", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pii = _load_module()

REAL_DOMAIN = "mail" + "box.org"
REAL_PHONE = "+1" + "4152220000"


def _assign(name: str, value: str) -> str:
    """Return source for a string constant assignment."""
    return f'{name} = "{value}"'


def _rules(text: str) -> list[str]:
    """Return the rule names reported for a snippet."""
    return [hit.rule for hit in pii.scan_text("sample.py", text)]


@pytest.mark.parametrize(
    "text",
    [
        '{"email": "guest@example.com"}',
        '{"email": "guest@mail.example.org"}',
        '{"email": "x@example-sentinel.invalid"}',
        '{"phone": "+15551234567"}',
        '{"phone": "+1 (555) 123-4567"}',
        '{"phone": "+1-415-555-0100"}',
        "{'address': {'full': '123 Beach Rd, Miami, FL'}}",
        '{"street": "456 Main St", "zip": "90210"}',
        '{"latitude": 37.5, "longitude": -122.5}',
        '{"guestPhone": "5551234567"}',
        '{"guestPhone": "555-123-4567"}',
        'phone="+1-555-1234"',
        'phone_last4="1234"',
        '{"guestAddress": "4417 Sentinel Hollow Road"}',
        '{"email_address": "a@example.com"}',
        'headers = {"Authorization": "Bearer test_token"}',
        "# SPDX-FileCopyrightText: 2026 Owner <tyk" + "eal@bardic" + "grove.org>",
    ],
)
def test_synthetic_values_pass(text: str) -> None:
    """Synthetic placeholders and licence headers are not reported."""
    assert _rules(text) == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f'{{"email": "jane@{REAL_DOMAIN}"}}', "email-domain"),
        ('{"note": "tyk' + 'eal"}', "owner-identity"),
        ('"Bearer ' + 'abcdefghijklmnop123"', "bearer-token"),
        (f'{{"phone": "{REAL_PHONE}"}}', "phone-number"),
        ('{"phone": "(415' + ') 222-0000"}', "phone-number"),
        ('{"phone": "+44' + '2071234567"}', "phone-number"),
        ("{'lat'" + ": 51.5}", "coordinate-box"),
        ('{"longitude"' + ': "-0.12"}', "coordinate-box"),
        ('{"postalCode"' + ': "94107"}', "address-allowlist"),
        ("{'street'" + ": '1 Infinite Loop'}", "address-allowlist"),
        ('{"guestPhone"' + ': "4152220000"}', "phone-number"),
        ('{"guestPhone"' + ': "415-222-0000"}', "phone-number"),
        (_assign("GUEST_PHONE", "222-0000"), "phone-number"),
        ('{"guestAddress"' + ': "1 Infinite Loop"}', "address-allowlist"),
        (_assign("GUEST_ADDRESS", "1 Infinite Loop"), "address-allowlist"),
    ],
)
def test_private_values_reported(text: str, rule: str) -> None:
    """Each non-synthetic value is reported under its rule."""
    assert _rules(text) == [rule]


def test_hits_report_line_without_value() -> None:
    """Formatted findings carry path, line, and rule but not the value."""
    secret = f"jane@{REAL_DOMAIN}"
    hits = pii.scan_text("tests/x.py", f"a = 1\nb = '{secret}'\n")
    assert [pii.format_hit(hit) for hit in hits] == ["tests/x.py:2: email-domain"]
    assert secret not in pii.format_hit(hits[0])


def test_json_outside_fixtures_dir_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON under tests/ must live in tests/fixtures/."""
    tests_dir = tmp_path / "tests"
    fixtures = tests_dir / "fixtures"
    fixtures.mkdir(parents=True)
    stray = tests_dir / "payload.json"
    stray.write_text("{}", encoding="utf-8")
    good = fixtures / "payload.json"
    good.write_text(json.dumps({"email": "a@example.com"}), encoding="utf-8")
    monkeypatch.setattr(pii, "TESTS_DIR", tests_dir.resolve())
    monkeypatch.setattr(pii, "FIXTURES_DIR", fixtures.resolve())

    hits = pii.scan_paths([str(stray), str(good)])

    assert [(hit.path, hit.rule) for hit in hits] == [
        (stray.as_posix(), "fixture-location")
    ]


def test_main_scans_fixture_json_and_sets_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() always scans fixture JSON and fails on any finding."""
    fixtures = tmp_path / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    leak = fixtures / "guesty.json"
    leak.write_text(json.dumps({"phone": REAL_PHONE}), encoding="utf-8")
    monkeypatch.setattr(pii, "TESTS_DIR", (tmp_path / "tests").resolve())
    monkeypatch.setattr(pii, "FIXTURES_DIR", fixtures.resolve())

    assert pii.main([]) == 1
    out = capsys.readouterr().out
    assert "phone-number" in out
    assert REAL_PHONE not in out

    leak.write_text(json.dumps({"phone": "+15551234567"}), encoding="utf-8")
    assert pii.main([str(leak)]) == 0
