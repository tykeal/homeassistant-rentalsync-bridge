# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""PMS Provider registry — registration, lookup, and factory."""

from typing import Any

from src.providers.base import PMSProvider

_registry: dict[str, type[PMSProvider]] = {}


def register_provider(pms_type: str, provider_class: type[PMSProvider]) -> None:
    """Register a PMS provider implementation.

    Args:
        pms_type: Provider identifier string (e.g., "cloudbeds", "guesty").
        provider_class: Class implementing PMSProvider ABC.

    Raises:
        ValueError: If *pms_type* is already registered.
    """
    if pms_type in _registry:
        msg = f"Provider type '{pms_type}' is already registered"
        raise ValueError(msg)
    _registry[pms_type] = provider_class


def get_provider_class(pms_type: str) -> type[PMSProvider]:
    """Get the provider class for a given type.

    Args:
        pms_type: Provider identifier string.

    Returns:
        Provider class.

    Raises:
        ValueError: If *pms_type* is not registered.
    """
    if pms_type not in _registry:
        msg = f"Unknown provider type: '{pms_type}'"
        raise ValueError(msg)
    return _registry[pms_type]


def create_provider(pms_type: str, **kwargs: Any) -> PMSProvider:
    """Create and return a configured provider instance.

    Args:
        pms_type: Provider identifier string.
        **kwargs: Keyword arguments forwarded to the provider constructor.

    Returns:
        Configured provider instance.

    Raises:
        ValueError: If *pms_type* is not registered.
    """
    cls = get_provider_class(pms_type)
    return cls(**kwargs)


def list_providers() -> list[dict[str, Any]]:
    """List all registered providers with their metadata.

    Returns:
        List of provider info dicts for the ``/api/providers`` endpoint.
    """
    return [
        {"pms_type": pms_type, "provider_class": cls.__name__}
        for pms_type, cls in _registry.items()
    ]


def _clear_registry() -> None:
    """Clear the registry (testing helper)."""
    _registry.clear()
