# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty PMS provider package."""

from src.providers.guesty.service import GuestyProvider


def register_guesty_provider() -> None:
    """Ensure the Guesty provider is registered.

    Importing the ``service`` module triggers the ``@provider``
    decorator, so this is effectively a no-op kept for backward
    compatibility.
    """


__all__ = ["GuestyProvider", "register_guesty_provider"]
