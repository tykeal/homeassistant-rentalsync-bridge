# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cloudbeds PMS provider package."""

from src.providers.cloudbeds.provider import CloudbedsProvider
from src.providers.registry import register_provider

register_provider("cloudbeds", CloudbedsProvider)

__all__ = ["CloudbedsProvider"]
