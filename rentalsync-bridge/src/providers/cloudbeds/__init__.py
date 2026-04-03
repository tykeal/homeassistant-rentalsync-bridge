# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds PMS provider package."""

from src.providers.cloudbeds.provider import CloudbedsProvider


def register_cloudbeds_provider() -> None:
    """Ensure the Cloudbeds provider is registered.

    Importing the ``provider`` module triggers the ``@provider``
    decorator, so this is effectively a no-op kept for backward
    compatibility.
    """


__all__ = ["CloudbedsProvider", "register_cloudbeds_provider"]
