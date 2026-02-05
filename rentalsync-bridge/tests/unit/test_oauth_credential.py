# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for OAuthCredential model."""

from unittest.mock import patch

import pytest
from src.models.oauth_credential import OAuthCredential


class TestApiKeySetter:
    """Tests for api_key property setter."""

    def test_api_key_setter_with_none(self):
        """Test setting api_key to None."""
        credential = OAuthCredential(client_id="test")
        credential._client_secret = "encrypted_secret"  # Required field

        credential.api_key = None

        assert credential._api_key is None

    def test_api_key_setter_with_value(self):
        """Test setting api_key to a valid value."""
        credential = OAuthCredential(client_id="test")
        credential._client_secret = "encrypted_secret"

        with patch("src.models.oauth_credential.encrypt_value") as mock_encrypt:
            mock_encrypt.return_value = "encrypted_api_key"
            credential.api_key = "my_api_key"

        mock_encrypt.assert_called_once_with("my_api_key")
        assert credential._api_key == "encrypted_api_key"

    def test_api_key_setter_raises_on_unexpected_none(self):
        """Test setter raises ValueError if encrypt_value returns None unexpectedly."""
        credential = OAuthCredential(client_id="test")
        credential._client_secret = "encrypted_secret"

        with patch("src.models.oauth_credential.encrypt_value") as mock_encrypt:
            mock_encrypt.return_value = None  # Simulate unexpected None

            with pytest.raises(ValueError, match="encrypt_value returned None"):
                credential.api_key = "my_api_key"


class TestIsTokenExpired:
    """Tests for is_token_expired method."""

    def test_is_token_expired_returns_false_when_api_key_set(self):
        """Test that API key authentication never reports expired."""
        credential = OAuthCredential(client_id="test")
        credential._client_secret = "encrypted_secret"
        credential._api_key = "encrypted_api_key"  # Has API key
        credential.token_expires_at = None  # No OAuth token

        assert credential.is_token_expired() is False

    def test_is_token_expired_returns_true_when_no_expiry_and_no_api_key(self):
        """Test that missing expiry with no API key reports expired."""
        credential = OAuthCredential(client_id="test")
        credential._client_secret = "encrypted_secret"
        credential._api_key = None  # No API key
        credential.token_expires_at = None

        assert credential.is_token_expired() is True
