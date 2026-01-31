# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuthCredential model for Cloudbeds API authentication."""

from datetime import UTC, datetime

from cryptography.fernet import Fernet
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config import get_settings
from src.database import Base


def _utc_now() -> datetime:
    """Get current UTC datetime for SQLAlchemy defaults."""
    return datetime.now(UTC)


def get_cipher() -> Fernet:
    """Get Fernet cipher for token encryption.

    Returns:
        Fernet cipher instance.

    Raises:
        ValueError: If encryption key is not configured.
    """
    settings = get_settings()
    if not settings.encryption_key:
        msg = "ENCRYPTION_KEY environment variable is required"
        raise ValueError(msg)
    return Fernet(settings.encryption_key.encode())


def encrypt_value(value: str | None) -> str | None:
    """Encrypt a value using Fernet.

    Args:
        value: Plain text value to encrypt.

    Returns:
        Encrypted value as string, or None if input is None.
    """
    if value is None:
        return None
    cipher = get_cipher()
    return cipher.encrypt(value.encode()).decode()


def decrypt_value(value: str | None) -> str | None:
    """Decrypt a value using Fernet.

    Args:
        value: Encrypted value to decrypt.

    Returns:
        Decrypted plain text value, or None if input is None.
    """
    if value is None:
        return None
    cipher = get_cipher()
    return cipher.decrypt(value.encode()).decode()


class OAuthCredential(Base):
    """OAuth credentials for Cloudbeds API access.

    Stores encrypted OAuth tokens with automatic encryption/decryption.
    Singleton pattern - only one record should exist.
    """

    __tablename__ = "oauth_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    _client_secret: Mapped[str] = mapped_column(
        "client_secret", String(255), nullable=False
    )
    _api_key: Mapped[str | None] = mapped_column("api_key", Text, nullable=True)
    _access_token: Mapped[str | None] = mapped_column(
        "access_token", Text, nullable=True
    )
    _refresh_token: Mapped[str | None] = mapped_column(
        "refresh_token", Text, nullable=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    @property
    def client_secret(self) -> str | None:
        """Get decrypted client secret."""
        return decrypt_value(self._client_secret)

    @client_secret.setter
    def client_secret(self, value: str | None) -> None:
        """Set encrypted client secret."""
        self._client_secret = encrypt_value(value)  # type: ignore[assignment]

    @property
    def api_key(self) -> str | None:
        """Get decrypted API key."""
        return decrypt_value(self._api_key)

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        """Set encrypted API key."""
        if value is None:
            self._api_key = None
        else:
            encrypted = encrypt_value(value)
            if encrypted is None:
                msg = "encrypt_value returned None for non-None input"
                raise ValueError(msg)
            self._api_key = encrypted

    @property
    def access_token(self) -> str | None:
        """Get decrypted access token."""
        return decrypt_value(self._access_token)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        """Set encrypted access token."""
        self._access_token = encrypt_value(value)

    @property
    def refresh_token(self) -> str | None:
        """Get decrypted refresh token."""
        return decrypt_value(self._refresh_token)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        """Set encrypted refresh token."""
        self._refresh_token = encrypt_value(value)

    def has_api_key(self) -> bool:
        """Check if API key authentication is configured.

        Returns:
            True if API key is set.
        """
        return self._api_key is not None

    def is_token_expired(self) -> bool:
        """Check if the access token is expired.

        Returns:
            True if token is expired or expiration is unknown.
        """
        if self.token_expires_at is None:
            return True
        # Handle timezone-naive datetimes from database
        expires_at = self.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) >= expires_at

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<OAuthCredential(id={self.id}, client_id={self.client_id})>"
