# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for application configuration."""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    """Tests for Settings configuration class."""

    def test_default_settings(self):
        """Test that default settings are applied."""
        # Clear cache to get fresh settings
        from src.config import Settings

        settings = Settings()

        # Note: conftest sets DATABASE_URL=sqlite:///./test.db, so we check the
        # pattern rather than exact value. Other defaults come from Settings class.
        assert "sqlite" in settings.database_url
        assert settings.sync_interval_minutes == 5
        assert settings.host == "0.0.0.0"
        assert settings.port == 8099
        assert settings.standalone_mode is True  # Set in conftest

    def test_sync_interval_validation(self):
        """Test sync interval validation bounds."""
        from src.config import Settings

        # Valid values
        settings = Settings(sync_interval_minutes=1)
        assert settings.sync_interval_minutes == 1

        settings = Settings(sync_interval_minutes=60)
        assert settings.sync_interval_minutes == 60

    def test_sync_interval_out_of_bounds(self):
        """Test sync interval rejects out of bounds values."""
        from pydantic import ValidationError
        from src.config import Settings

        with pytest.raises(ValidationError):
            Settings(sync_interval_minutes=0)

        with pytest.raises(ValidationError):
            Settings(sync_interval_minutes=61)

    def test_environment_override(self):
        """Test environment variables override defaults."""
        from src.config import Settings

        with patch.dict(os.environ, {"HOST": "127.0.0.1", "PORT": "9000"}):
            settings = Settings()
            assert settings.host == "127.0.0.1"
            assert settings.port == 9000

    # -- T015 / T017: pms_type auto-detection --------------------------

    def test_pms_type_explicit_wins(self):
        """Explicit PMS_TYPE env var takes precedence over detection."""
        from src.config import Settings

        with patch.dict(
            os.environ,
            {"PMS_TYPE": "guesty", "GUESTY_CLIENT_ID": ""},
            clear=False,
        ):
            settings = Settings()
            assert settings.pms_type == "guesty"

    def test_pms_type_inferred_guesty(self):
        """GUESTY_CLIENT_ID triggers auto-detection of guesty."""
        from src.config import Settings

        with patch.dict(
            os.environ,
            {"GUESTY_CLIENT_ID": "some-id", "PMS_TYPE": ""},
            clear=False,
        ):
            settings = Settings()
            assert settings.pms_type == "guesty"

    def test_pms_type_default_cloudbeds(self):
        """Default pms_type is cloudbeds when nothing hints otherwise."""
        from src.config import Settings

        with patch.dict(
            os.environ,
            {"PMS_TYPE": "", "GUESTY_CLIENT_ID": ""},
            clear=False,
        ):
            settings = Settings()
            assert settings.pms_type == "cloudbeds"

    def test_guesty_client_id_field(self):
        """guesty_client_id is an optional string defaulting to ''."""
        from src.config import Settings

        settings = Settings()
        assert settings.guesty_client_id == ""

        with patch.dict(os.environ, {"GUESTY_CLIENT_ID": "gid"}):
            settings = Settings()
            assert settings.guesty_client_id == "gid"

    def test_guesty_client_secret_field(self):
        """guesty_client_secret is an optional string defaulting to ''."""
        from src.config import Settings

        settings = Settings()
        assert settings.guesty_client_secret == ""

        with patch.dict(os.environ, {"GUESTY_CLIENT_SECRET": "gsec"}):
            settings = Settings()
            assert settings.guesty_client_secret == "gsec"


class TestGuestyReservationsApi:
    """Tests for the guesty_reservations_api toggle (spec 004, FR-041)."""

    def test_default_is_v3(self, monkeypatch):
        """The default is v3; legacy is an explicit rollback choice."""
        from src.config import Settings

        monkeypatch.delenv("GUESTY_RESERVATIONS_API", raising=False)
        assert Settings().guesty_reservations_api == "v3"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(" Legacy ", "legacy"), ("V3", "v3"), ("\tv3\n", "v3")],
    )
    def test_value_is_normalised(self, raw, expected):
        """Values are stripped and lower-cased."""
        from src.config import Settings

        assert Settings(guesty_reservations_api=raw).guesty_reservations_api == (
            expected
        )

    @pytest.mark.parametrize("raw", ["v2", "", "legacyy", "auto"])
    def test_invalid_value_raises(self, raw):
        """An unknown value fails at construction."""
        from pydantic import ValidationError
        from src.config import Settings

        with pytest.raises(ValidationError, match="guesty_reservations_api"):
            Settings(guesty_reservations_api=raw)

    def test_env_var_is_read(self, monkeypatch):
        """GUESTY_RESERVATIONS_API selects the value."""
        from src.config import Settings

        monkeypatch.setenv("GUESTY_RESERVATIONS_API", " V3 ")
        assert Settings().guesty_reservations_api == "v3"
        monkeypatch.setenv("GUESTY_RESERVATIONS_API", "legacy")
        assert Settings().guesty_reservations_api == "legacy"

    def test_invalid_env_var_raises(self, monkeypatch):
        """An unknown env value fails at startup, like pms_type."""
        from pydantic import ValidationError
        from src.config import Settings

        monkeypatch.setenv("GUESTY_RESERVATIONS_API", "v4")
        with pytest.raises(ValidationError):
            Settings()


class TestGetSettings:
    """Tests for get_settings function."""

    def test_get_settings_returns_settings_instance(self):
        """Test get_settings returns a Settings instance."""
        from src.config import Settings, get_settings

        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self):
        """Test that get_settings returns the same cached instance."""
        from src.config import get_settings

        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2
