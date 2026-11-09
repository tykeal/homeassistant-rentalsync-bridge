#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guard synthetic test fixtures against committed private data.

Provider payloads in the test suite (Guesty, Cloudbeds) are mostly inline
Python dicts, so both ``.py`` and ``.json`` files under ``tests/`` are
scanned. Findings report only path, line, and rule so that a hit never
echoes the private value it detected.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
TESTS_DIR: Final = PROJECT_ROOT / "tests"
FIXTURES_DIR: Final = TESTS_DIR / "fixtures"

DOC_DOMAINS: Final = frozenset({"example.com", "example.org", "example.net"})
RESERVED_TLDS: Final = frozenset({"example", "invalid", "localhost", "test"})
OWNER_RE: Final = re.compile(r"tykeal|bardicgrove", re.IGNORECASE)
EMAIL_RE: Final = re.compile(r"[A-Z0-9._%+-]+@([A-Z0-9-]+(?:\.[A-Z0-9-]+)+)", re.I)
BEARER_RE: Final = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
# NANP numbers written as E.164 (+1NPANXXXXXX) or punctuated (NPA) NXX-XXXX.
PHONE_RE: Final = re.compile(
    r"(?<![\w+])(?:\+1[ .-]?\(?(\d{3})\)?[ .-]?(\d{3})[ .-]?\d{4}"
    r"|\((\d{3})\)[ .-]?(\d{3})[ .-]\d{4})(?!\d)"
)
INTL_PHONE_RE: Final = re.compile(r"(?<![\w+])\+[02-9]\d{7,14}(?!\d)")
FICTIONAL_PHONE_PART: Final = "555"
_QUOTED_KEY: Final = r"""["']({keys})["']\s*:\s*"""
COORD_RE: Final = re.compile(
    _QUOTED_KEY.format(keys="latitude|longitude|lat|lng|lon")
    + r"""["']?(-?\d+(?:\.\d+)?)"""
)
POSTCODE_RE: Final = re.compile(
    _QUOTED_KEY.format(keys="postcode|postalCode|postal_code|zip|zipcode|zipCode")
    + r"""["']([^"']+)["']"""
)
# Dict keys, keyword arguments, constants, and annotated assignments or
# typed parameter defaults holding a string value, e.g.
# ``"guestPhone": "..."``, ``phone="..."``, ``GUEST_ADDRESS = "..."``, or
# ``phone: str | None = "..."``.
_STRING_FIELD: Final = (
    r"""(?<!\w)["']?(?:{keys})["']?"""
    r"""(?:\s*:\s*[\w.\[\]| ]+?)?\s*[:=]\s*["']([^"'\n]*)["']"""
)
PHONE_FIELD_RE: Final = re.compile(_STRING_FIELD.format(keys=r"\w*phone\w*"), re.I)
STREET_RE: Final = re.compile(
    _STRING_FIELD.format(keys=r"street|(?:guest_?)?address[12]?|full|display"),
    re.I,
)
ALLOWED_POSTCODES: Final = frozenset({"00000", "12345", "90210"})
ALLOWED_STREET_RE: Final = re.compile(
    r"^\d{1,5} (?:Beach|Example|Main|Sentinel|Test)\b"
)
MIN_PHONE_DIGITS: Final = 7
NANP_DIGITS: Final = 10
SYNTHETIC_MIN_LAT: Final = 37.0
SYNTHETIC_MAX_LAT: Final = 38.0
SYNTHETIC_MIN_LON: Final = -123.0
SYNTHETIC_MAX_LON: Final = -122.0


@dataclass(frozen=True)
class Hit:
    """A sanitized fixture policy violation."""

    path: str
    line: int
    rule: str


def _line_for(text: str, index: int) -> int:
    """Return a one-based line number for a character offset.

    Args:
        text: Full text that was scanned.
        index: Match start offset.

    Returns:
        One-based line number.
    """
    return text.count("\n", 0, index) + 1


def _line_text(text: str, index: int) -> str:
    """Return the full line containing a character offset.

    Args:
        text: Full text that was scanned.
        index: Character offset within the text.

    Returns:
        The line without its trailing newline.
    """
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)]


def _is_doc_domain(domain: str) -> bool:
    """Return whether an email domain is reserved for documentation.

    Args:
        domain: Email domain to check.

    Returns:
        True for RFC 2606 domains, their subdomains, and reserved TLDs.
    """
    domain = domain.casefold().rstrip(".")
    if domain.rsplit(".", 1)[-1] in RESERVED_TLDS:
        return True
    return any(domain == d or domain.endswith(f".{d}") for d in DOC_DOMAINS)


def _is_fictional_phone(area: str, exchange: str) -> bool:
    """Return whether a NANP number uses the fictional 555 range.

    Args:
        area: Three-digit area code.
        exchange: Three-digit central office code.

    Returns:
        True when either code is 555.
    """
    return FICTIONAL_PHONE_PART in (area, exchange)


def _is_fictional_phone_value(value: str) -> bool:
    """Return whether a phone field value is synthetic or not a number.

    Handles local formats such as ``4155550100`` or ``415-555-0100``
    as well as country-code prefixed ones. Values with too few digits
    to be a phone number, such as stored last-four digits, pass.

    Args:
        value: Raw phone field value.

    Returns:
        True when the value is too short to be a phone number or is a
        NANP number using the 555 area code or exchange.
    """
    digits = re.sub(r"\D", "", value)
    # Drop a NANP country code from 1 + NXX-XXXX or 1 + NPA-NXX-XXXX.
    if digits.startswith("1") and len(digits) in {8, 11}:
        digits = digits[1:]
    if len(digits) < MIN_PHONE_DIGITS:
        return True
    if len(digits) == MIN_PHONE_DIGITS:
        return digits[:3] == FICTIONAL_PHONE_PART
    if len(digits) == NANP_DIGITS:
        return FICTIONAL_PHONE_PART in (digits[:3], digits[3:6])
    return False


def _in_coordinate_box(key: str, value: float) -> bool:
    """Return whether a coordinate lies in the synthetic bounding box.

    Args:
        key: Coordinate key name.
        value: Coordinate value.

    Returns:
        True when the value is inside the synthetic box.
    """
    if key.startswith("lat"):
        return SYNTHETIC_MIN_LAT <= value <= SYNTHETIC_MAX_LAT
    return SYNTHETIC_MIN_LON <= value <= SYNTHETIC_MAX_LON


def _identity_findings(text: str) -> Iterator[tuple[int, str]]:
    """Yield email, owner, and bearer token findings.

    Args:
        text: Fixture content to scan.

    Yields:
        Match offset and rule name for each finding.
    """
    for match in EMAIL_RE.finditer(text):
        if not _is_doc_domain(match.group(1)):
            yield match.start(), "email-domain"
    for match in OWNER_RE.finditer(text):
        yield match.start(), "owner-identity"
    for match in BEARER_RE.finditer(text):
        yield match.start(), "bearer-token"


def _phone_findings(text: str) -> Iterator[tuple[int, str]]:
    """Yield phone numbers outside the fictional 555 range.

    Args:
        text: Fixture content to scan.

    Yields:
        Match offset and rule name for each finding.
    """
    for match in PHONE_RE.finditer(text):
        area = match.group(1) or match.group(3)
        exchange = match.group(2) or match.group(4)
        if not _is_fictional_phone(area, exchange):
            yield match.start(), "phone-number"
    for match in INTL_PHONE_RE.finditer(text):
        yield match.start(), "phone-number"
    for match in PHONE_FIELD_RE.finditer(text):
        if not _is_fictional_phone_value(match.group(1)):
            yield match.start(), "phone-number"


def _location_findings(text: str) -> Iterator[tuple[int, str]]:
    """Yield coordinates and addresses that are not synthetic.

    Args:
        text: Fixture content to scan.

    Yields:
        Match offset and rule name for each finding.
    """
    for match in COORD_RE.finditer(text):
        if not _in_coordinate_box(match.group(1), float(match.group(2))):
            yield match.start(), "coordinate-box"
    for match in POSTCODE_RE.finditer(text):
        if match.group(2) not in ALLOWED_POSTCODES:
            yield match.start(), "address-allowlist"
    for match in STREET_RE.finditer(text):
        if not ALLOWED_STREET_RE.match(match.group(1)):
            yield match.start(), "address-allowlist"


def scan_text(path: str, text: str) -> list[Hit]:
    """Scan fixture text for private or non-synthetic values.

    Args:
        path: Path label to include in sanitized findings.
        text: Fixture content to scan.

    Returns:
        Sanitized findings containing file, line, and rule only.
    """
    findings = chain(
        _identity_findings(text), _phone_findings(text), _location_findings(text)
    )
    return sorted(
        {
            Hit(path=path, line=_line_for(text, index), rule=rule)
            for index, rule in findings
            # Licence headers legitimately carry the copyright holder's identity.
            if "SPDX-FileCopyrightText:" not in _line_text(text, index)
        },
        key=lambda hit: (hit.line, hit.rule),
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether a path lives under a parent directory.

    Args:
        path: Resolved path to test.
        parent: Resolved parent directory.

    Returns:
        True when path is inside parent.
    """
    return path == parent or parent in path.parents


def scan_paths(paths: list[str]) -> list[Hit]:
    """Scan fixture paths and report stray JSON outside the fixture tree.

    Args:
        paths: Paths to inspect.

    Returns:
        Sanitized findings.
    """
    hits: list[Hit] = []
    for raw_path in paths:
        path = Path(raw_path)
        label = path.as_posix()
        if path.suffix not in {".json", ".py"} or not path.is_file():
            continue
        resolved = path.resolve()
        if (
            path.suffix == ".json"
            and _is_relative_to(resolved, TESTS_DIR)
            and not _is_relative_to(resolved, FIXTURES_DIR)
        ):
            hits.append(Hit(path=label, line=1, rule="fixture-location"))
            continue
        hits.extend(scan_text(label, path.read_text(encoding="utf-8")))
    return hits


def format_hit(hit: Hit) -> str:
    """Format one sanitized finding without echoing matched content.

    Args:
        hit: Finding to render.

    Returns:
        Sanitized finding string.
    """
    return f"{hit.path}:{hit.line}: {hit.rule}"


def _repository_json_paths() -> list[str]:
    """Return JSON paths under the test tree.

    Returns:
        JSON paths under tests, so stray fixtures are always checked.
    """
    return [str(path) for path in sorted(TESTS_DIR.rglob("*.json"))]


def main(argv: list[str] | None = None) -> int:
    """Run the fixture PII checker.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    seen: dict[Path, str] = {}
    for raw_path in [*args.paths, *_repository_json_paths()]:
        seen.setdefault(Path(raw_path).resolve(), raw_path)
    hits = scan_paths(list(seen.values()))
    for hit in hits:
        print(format_hit(hit))
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
