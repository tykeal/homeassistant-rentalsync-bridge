# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds PMS provider package."""

from src.providers.cloudbeds.provider import CloudbedsProvider
from src.providers.registry import register_provider


def register_cloudbeds_provider() -> None:
    """Explicitly register the Cloudbeds provider.

    Safe to call multiple times — raises ``ValueError`` only if a
    *different* provider has already claimed the ``"cloudbeds"`` type.
    This function is provided for callers that need deterministic,
    explicit registration instead of relying on import side-effects.
    """
    register_provider("cloudbeds", CloudbedsProvider)


# Side-effect registration: importing this package auto-registers.
register_cloudbeds_provider()

__all__ = ["CloudbedsProvider", "register_cloudbeds_provider"]
