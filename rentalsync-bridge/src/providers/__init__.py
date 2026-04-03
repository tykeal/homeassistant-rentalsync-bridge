# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""PMS Provider abstraction layer."""

from src.providers.base import (
    PMSAuthenticationError,
    PMSConnectionError,
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSProviderError,
    PMSRateLimitError,
    PMSReservation,
    PMSRoom,
    TokenRateLimitError,
    TokenResult,
)
from src.providers.registry import (
    create_provider,
    get_provider_class,
    list_providers,
    register_provider,
)

__all__ = [
    "PMSAuthenticationError",
    "PMSConnectionError",
    "PMSGuest",
    "PMSListing",
    "PMSProvider",
    "PMSProviderError",
    "PMSRateLimitError",
    "PMSReservation",
    "PMSRoom",
    "TokenRateLimitError",
    "TokenResult",
    "create_provider",
    "get_provider_class",
    "list_providers",
    "register_provider",
]
