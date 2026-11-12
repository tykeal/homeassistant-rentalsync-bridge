# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the provider registry module."""

from datetime import UTC, datetime
from typing import Any

import pytest
from src.providers.base import (
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSReservation,
    PMSRoom,
    TokenResult,
)
from src.providers.registry import (
    _clear_registry,
    create_provider,
    get_provider_class,
    list_providers,
    provider,
    register_provider,
)

# ---------------------------------------------------------------------------
# Concrete stub for tests
# ---------------------------------------------------------------------------


class _StubProvider(PMSProvider):
    """Minimal concrete PMSProvider for registry tests."""

    def __init__(self, **kwargs):
        """Initialize the stub provider with keyword arguments."""
        self.init_kwargs = kwargs

    async def get_listings(self) -> list[PMSListing]:
        """Return an empty list of listings."""
        return []

    async def get_reservations(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Return an empty list of reservations."""
        return []

    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]:
        """Return an empty list of rooms."""
        return []

    async def get_guest(self, guest_id: str) -> PMSGuest | None:
        """Return None for any guest lookup."""
        return None

    async def get_custom_fields(self, reservation_id: str) -> dict[str, Any]:
        """Return an empty dict of custom fields."""
        return {}

    async def refresh_token(self, credential: Any) -> TokenResult:
        """Return a stub token result."""
        return TokenResult(
            access_token="a", refresh_token=None, expires_at=datetime.now(UTC)
        )

    async def test_connection(self) -> bool:
        """Return True for connection test."""
        return True

    @property
    def provider_type(self) -> str:
        """Return the stub provider type identifier."""
        return "stub"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure registry is empty before and after each test."""
    _clear_registry()
    yield
    _clear_registry()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterProvider:
    """Tests for the register_provider and get_provider_class functions."""

    def test_register_and_get(self):
        """Test that a registered provider can be retrieved."""
        register_provider("stub", _StubProvider)
        assert get_provider_class("stub") is _StubProvider

    def test_duplicate_same_class_is_idempotent(self):
        """Test that re-registering the same class is a no-op."""
        register_provider("stub", _StubProvider)
        register_provider("stub", _StubProvider)
        assert get_provider_class("stub") is _StubProvider

    def test_duplicate_different_class_raises_value_error(self):
        """Test that registering a different class for the same type raises."""

        class _OtherProvider(_StubProvider):
            @property
            def provider_type(self) -> str:
                return "other"

        register_provider("stub", _StubProvider)
        with pytest.raises(ValueError, match="already registered"):
            register_provider("stub", _OtherProvider)

    def test_unknown_type_raises_value_error(self):
        """Test that getting an unregistered type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown provider type"):
            get_provider_class("nonexistent")


class TestCreateProvider:
    """Tests for the create_provider factory function."""

    def test_create_provider_returns_instance(self):
        """Test that create_provider returns a PMSProvider instance."""
        register_provider("stub", _StubProvider)
        provider = create_provider("stub")
        assert isinstance(provider, _StubProvider)
        assert isinstance(provider, PMSProvider)

    def test_create_provider_forwards_kwargs(self):
        """Test that create_provider passes keyword arguments to the constructor."""
        register_provider("stub", _StubProvider)
        provider = create_provider("stub", access_token="tok123")
        assert isinstance(provider, _StubProvider)
        assert provider.init_kwargs == {"access_token": "tok123"}

    def test_create_unknown_raises_value_error(self):
        """Test that creating an unregistered type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown provider type"):
            create_provider("missing")


class TestListProviders:
    """Tests for the list_providers function."""

    def test_list_empty(self):
        """Test that an empty registry returns an empty list."""
        assert list_providers() == []

    def test_list_returns_metadata(self):
        """Test that list_providers returns provider metadata."""
        register_provider("stub", _StubProvider)
        result = list_providers()
        assert len(result) == 1
        assert result[0]["pms_type"] == "stub"
        assert result[0]["provider_class"] == "_StubProvider"

    def test_list_multiple_providers(self):
        """Test that list_providers returns all registered providers."""

        class AnotherStub(_StubProvider):
            @property
            def provider_type(self) -> str:
                return "another"

        register_provider("stub", _StubProvider)
        register_provider("another", AnotherStub)
        result = list_providers()
        assert len(result) == 2
        types = {r["pms_type"] for r in result}
        assert types == {"stub", "another"}


class TestProviderDecorator:
    """Tests for the @provider decorator."""

    def test_decorator_registers_and_returns_class(self):
        """Test that @provider registers the class and returns it."""

        @provider("decorated")
        class DecoratedStub(_StubProvider):
            @property
            def provider_type(self) -> str:
                return "decorated"

        assert get_provider_class("decorated") is DecoratedStub

    def test_decorator_duplicate_raises(self):
        """Test that applying @provider with a duplicate type raises."""
        register_provider("taken", _StubProvider)
        with pytest.raises(ValueError, match="already registered"):

            @provider("taken")
            class DuplicateStub(_StubProvider):
                pass


class TestConcurrency:
    """Thread-safety tests for the provider registry."""

    def test_concurrent_registrations(self):
        """Concurrent register_provider calls must not lose entries."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        num_threads = 10

        def _make_and_register(idx: int) -> str:
            name = f"concurrent-{idx}"

            # Create a unique subclass per thread
            cls = type(f"Stub{idx}", (_StubProvider,), {})
            register_provider(name, cls)
            return name

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_make_and_register, i) for i in range(num_threads)]
            names = [f.result() for f in as_completed(futures)]

        registered = {p["pms_type"] for p in list_providers()}
        assert set(names) == registered

    def test_concurrent_reads_no_error(self):
        """Concurrent get_provider_class calls must not raise."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        register_provider("reader", _StubProvider)

        def _read(_: int) -> type:
            return get_provider_class("reader")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_read, i) for i in range(10)]
            results = [f.result() for f in as_completed(futures)]

        assert all(r is _StubProvider for r in results)


# ---------------------------------------------------------------------------
# Guesty reservations API toggle wiring (spec 004, FR-041)
# ---------------------------------------------------------------------------


@pytest.fixture
def _settings_env(monkeypatch):
    """Isolate get_settings() so a test can set GUESTY_RESERVATIONS_API."""
    from src.config import get_settings

    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _credential(pms_type: str):
    """Build an unsaved credential for *pms_type*."""
    from src.models.oauth_credential import OAuthCredential

    if pms_type == "guesty":
        return OAuthCredential(
            pms_type="guesty", client_id="synthetic-id", client_secret="synthetic"
        )
    return OAuthCredential(pms_type=pms_type, api_key="synthetic-key")


class TestReservationsApiWiring:
    """create_provider_for_credential and GuestyProvider honour the toggle."""

    @pytest.mark.parametrize("value", ["v3", "legacy"])
    def test_factory_passes_setting_to_guesty(self, _settings_env, value):
        """The Guesty branch forwards reservations_api from settings."""
        from unittest.mock import MagicMock, patch

        from src.providers.factory import create_provider_for_credential

        _settings_env.setenv("GUESTY_RESERVATIONS_API", value)
        with patch("src.providers.factory.create_provider") as create:
            create_provider_for_credential(_credential("guesty"), MagicMock())
        assert create.call_args.args == ("guesty",)
        assert create.call_args.kwargs["reservations_api"] == value

    def test_factory_does_not_pass_setting_to_cloudbeds(self, _settings_env):
        """Cloudbeds ignores the toggle: no reservations_api kwarg is sent."""
        from unittest.mock import MagicMock, patch

        from src.providers.factory import create_provider_for_credential

        _settings_env.setenv("GUESTY_RESERVATIONS_API", "v3")
        with patch("src.providers.factory.create_provider") as create:
            create_provider_for_credential(_credential("cloudbeds"), MagicMock())
        assert create.call_args.args == ("cloudbeds",)
        assert "reservations_api" not in create.call_args.kwargs

    def test_cloudbeds_constructs_with_setting(self):
        """CloudbedsProvider tolerates the kwarg (registry compatibility)."""
        from src.providers.cloudbeds.provider import CloudbedsProvider

        assert CloudbedsProvider(api_key="k", reservations_api="v3")

    def test_guesty_legacy_selects_legacy_source(self):
        """reservations_api='legacy' selects LegacyReservationSource."""
        from src.providers.guesty.legacy_reservations import (
            LegacyReservationSource,
        )
        from src.providers.guesty.service import GuestyProvider

        provider = GuestyProvider(reservations_api="legacy")
        assert isinstance(provider._reservations, LegacyReservationSource)

    def test_guesty_default_is_legacy(self):
        """Without the kwarg GuestyProvider stays on the legacy source."""
        from src.providers.guesty.legacy_reservations import (
            LegacyReservationSource,
        )
        from src.providers.guesty.service import GuestyProvider

        assert isinstance(GuestyProvider()._reservations, LegacyReservationSource)

    def test_guesty_v3_selects_v3_source(self):
        """reservations_api='v3' selects the V3ReservationSource stub."""
        from src.providers.guesty.reservations_v3 import V3ReservationSource
        from src.providers.guesty.service import GuestyProvider

        provider = GuestyProvider(reservations_api="v3")
        assert isinstance(provider._reservations, V3ReservationSource)

    def test_guesty_unknown_value_raises(self):
        """An unknown reservations_api is a programming error."""
        from src.providers.guesty.service import GuestyProvider

        with pytest.raises(ValueError, match="reservations_api"):
            GuestyProvider(reservations_api="v2")
