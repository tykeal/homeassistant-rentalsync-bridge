# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the reno-to-Markdown release note renderer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent


def _load_renderer() -> Any:
    """Import scripts/render_release_notes.py by path.

    Loading by path, rather than as ``scripts.render_release_notes``,
    keeps the test independent of whether ``scripts`` is importable as
    a package, and defers the import into each test body so a missing
    script fails the test rather than its collection.
    """
    path = PROJECT_ROOT / "scripts" / "render_release_notes.py"
    spec = importlib.util.spec_from_file_location("render_release_notes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SECTIONS = [
    ("upgrade", "Upgrade Notes", 1),
    ("features", "New Features", 1),
    ("fixes", "Bug Fixes", 1),
]
SECTION_NAMES = [name for name, _, _ in SECTIONS]


class FakeLoader:
    """In-memory stand-in for ``reno.loader.Loader``."""

    def __init__(self, notes: dict[str, dict[str, dict[str, Any]]]) -> None:
        """Store ``{version: {filename: parsed content}}``."""
        self._notes = notes

    @property
    def versions(self) -> list[str]:
        """Return versions newest first, as reno does."""
        return list(self._notes)

    def __getitem__(self, version: str) -> list[tuple[str, str | None]]:
        """Return the note files of one version."""
        return [(name, "sha") for name in self._notes[version]]

    def parse_note_file(self, filename: str, sha: str | None) -> dict[str, Any]:
        """Return the parsed content of one note file."""
        for files in self._notes.values():
            if filename in files:
                return files[filename]
        raise KeyError(filename)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, ["*working-copy*", "v0.2.0-2"]),
        ("v0.2.0", ["v0.2.0"]),
        ("v0.0.1", []),
    ],
)
def test_select_versions(version: str | None, expected: list[str]) -> None:
    """Unreleased notes stop at the newest tag; a named tag stands alone."""
    rrn = _load_renderer()
    versions = ["*working-copy*", "v0.2.0-2", "v0.2.0", "v0.1.0"]
    tags = {"v0.2.0", "v0.1.0", "v0.0.1"}

    assert rrn.select_versions(versions, tags, version) == expected


def test_select_versions_head_on_tag_has_no_unreleased_notes() -> None:
    """Right after a release, nothing is unreleased."""
    rrn = _load_renderer()

    assert rrn.select_versions(["v0.2.0", "v0.1.0"], {"v0.2.0", "v0.1.0"}, None) == []


def test_select_versions_without_tags_takes_everything() -> None:
    """Before the first release tag, every note is unreleased."""
    rrn = _load_renderer()

    assert rrn.select_versions(["*working-copy*", "0.0.0"], set(), None) == [
        "*working-copy*",
        "0.0.0",
    ]


def test_render_orders_sections_and_formats_multiline_items() -> None:
    """Sections follow config order; continuation lines stay in the bullet."""
    rrn = _load_renderer()
    loader = FakeLoader(
        {
            "v0.2.0-1": {
                "a.yaml": {
                    "fixes": ["Fixed a thing.\n"],
                    "upgrade": [
                        "First line\nsecond line.\n\n```yaml\nkey: value\n```\n"
                    ],
                },
                "b.yaml": {
                    "prelude": "A summary\nparagraph.\n",
                    "fixes": "A single string item.\n",
                },
            }
        }
    )

    notes = rrn.collect_notes(loader, ["v0.2.0-1"], SECTION_NAMES, "prelude")

    assert rrn.render_markdown(notes, SECTIONS) == (
        "A summary\nparagraph.\n\n"
        "### Upgrade Notes\n\n"
        "- First line\n  second line.\n\n  ```yaml\n  key: value\n  ```\n\n"
        "### Bug Fixes\n\n"
        "- Fixed a thing.\n- A single string item."
    )


def test_render_without_notes_is_empty() -> None:
    """Empty or blank items produce no output at all."""
    rrn = _load_renderer()
    loader = FakeLoader({"v0.2.0-1": {"a.yaml": {"fixes": ["  \n"]}}})

    notes = rrn.collect_notes(loader, ["v0.2.0-1"], SECTION_NAMES, "prelude")

    assert rrn.render_markdown(notes, SECTIONS) == ""


def test_drafter_config_unchanged_without_notes() -> None:
    """With no notes the committed config is used as is."""
    rrn = _load_renderer()
    base = "---\nheader: |+\n  badge\n\ntemplate: |\n  $CHANGES\n"

    assert rrn.build_drafter_config(base, "") == base


def test_drafter_config_places_notes_before_changes() -> None:
    """Notes form the header above $CHANGES; everything else is preserved."""
    rrn = _load_renderer()
    base_text = (REPO_ROOT / ".github" / "release-drafter.yml").read_text()
    base = yaml.safe_load(base_text)

    generated = yaml.safe_load(
        rrn.build_drafter_config(base_text, "### Bug Fixes\n\n- Fixed.")
    )

    body = generated["header"] + generated["template"]
    assert body.startswith(
        (base.get("header") or "") + "### Bug Fixes\n\n- Fixed.\n\n$CHANGES"
    )
    assert {k: v for k, v in generated.items() if k != "header"} == {
        k: v for k, v in base.items() if k != "header"
    }


def test_main_rejects_unknown_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit version must be an existing tag."""
    rrn = _load_renderer()
    monkeypatch.setattr(rrn, "git_tags", lambda _root: {"v0.2.0"})

    assert rrn.main(["--version", "v9.9.9"]) == 2
    assert "not a git tag" in capsys.readouterr().err


def test_main_writes_drafter_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI merges rendered notes into the given config file."""
    rrn = _load_renderer()
    monkeypatch.setattr(rrn, "load_release_notes", lambda _root, _v: "- Note.")
    base = tmp_path / "base.yml"
    base.write_text("header: |\n  badge\ntemplate: $CHANGES\n")
    out = tmp_path / "out.yml"

    assert rrn.main(["--release-drafter-config", str(base), "--output", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["header"] == "badge\n\n- Note.\n\n"


def test_main_defaults_repo_root_to_git_toplevel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run from the uv project directory, reno still scans the repo root."""
    rrn = _load_renderer()
    seen: list[Path] = []

    def _record(root: Path, _version: str | None) -> str:
        """Capture the repository root the CLI resolved."""
        seen.append(root)
        return "- Note."

    monkeypatch.setattr(rrn, "load_release_notes", _record)
    monkeypatch.chdir(PROJECT_ROOT)

    assert rrn.main([]) == 0
    assert seen == [REPO_ROOT]
    assert capsys.readouterr().out == "- Note.\n"


def test_collect_rejects_non_list_section() -> None:
    """A section that is neither a string nor a list names the bad file.

    reno lint only warns about it, so the renderer is the gate.
    """
    rrn = _load_renderer()
    loader = FakeLoader({"v0.2.0-1": {"bad.yaml": {"features": 42}}})

    with pytest.raises(ValueError, match=r"bad\.yaml.*features"):
        rrn.collect_notes(loader, ["v0.2.0-1"], SECTION_NAMES, "prelude")


def test_collect_rejects_non_string_item() -> None:
    """A non-string item would be lost from the release notes, so fail."""
    rrn = _load_renderer()
    loader = FakeLoader({"v0.2.0-1": {"bad.yaml": {"fixes": ["ok", 42]}}})

    with pytest.raises(ValueError, match=r"bad\.yaml.*fixes"):
        rrn.collect_notes(loader, ["v0.2.0-1"], SECTION_NAMES, "prelude")


def test_collect_rejects_non_string_prelude() -> None:
    """The prelude is a single block of text."""
    rrn = _load_renderer()
    loader = FakeLoader({"v0.2.0-1": {"bad.yaml": {"prelude": ["a", "b"]}}})

    with pytest.raises(ValueError, match=r"bad\.yaml.*prelude"):
        rrn.collect_notes(loader, ["v0.2.0-1"], SECTION_NAMES, "prelude")


def test_main_reports_invalid_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid note is a clean error exit, not a traceback."""
    rrn = _load_renderer()

    def _invalid(_root: Path, _version: str | None) -> str:
        """Fail as collect_notes does on a malformed note."""
        raise ValueError("bad.yaml: section 'features' is not a list")

    monkeypatch.setattr(rrn, "load_release_notes", _invalid)

    assert rrn.main([]) == 1
    assert "bad.yaml" in capsys.readouterr().err
