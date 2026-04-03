# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for CredentialRepository."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from src.models.oauth_credential import OAuthCredential
from src.repositories.credential_repository import CredentialRepository


class TestCredentialRepository:
    """Tests for CredentialRepository CRUD operations."""

    @pytest.fixture
    def repo(self, async_session):
        """Create a CredentialRepository bound to the test session."""
        return CredentialRepository(async_session)

    # -- get_credential ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_credential_none(self, repo):
        """Return None when no credential exists for the provider."""
        result = await repo.get_credential("cloudbeds")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_credential_cloudbeds(self, repo, async_session):
        """Fetch the cloudbeds credential."""
        cred = OAuthCredential(client_id="cb_client", pms_type="cloudbeds")
        cred.client_secret = "cb_secret"
        async_session.add(cred)
        await async_session.flush()

        result = await repo.get_credential("cloudbeds")
        assert result is not None
        assert result.client_id == "cb_client"
        assert result.pms_type == "cloudbeds"

    @pytest.mark.asyncio
    async def test_get_credential_guesty(self, repo, async_session):
        """Fetch the guesty credential without touching cloudbeds rows."""
        cb = OAuthCredential(client_id="cb_client", pms_type="cloudbeds")
        cb.client_secret = "cb_secret"
        gu = OAuthCredential(client_id="gu_client", pms_type="guesty")
        gu.client_secret = "gu_secret"
        async_session.add_all([cb, gu])
        await async_session.flush()

        result = await repo.get_credential("guesty")
        assert result is not None
        assert result.client_id == "gu_client"
        assert result.pms_type == "guesty"

    # -- save_credential --------------------------------------------------

    @pytest.mark.asyncio
    async def test_save_credential_new(self, repo, async_session):
        """Save a brand-new credential."""
        cred = OAuthCredential(client_id="new_client", pms_type="guesty")
        cred.client_secret = "new_secret"

        saved = await repo.save_credential(cred)
        assert saved.id is not None
        assert saved.client_id == "new_client"

    @pytest.mark.asyncio
    async def test_save_credential_merge(self, repo, async_session):
        """Merge updates an existing credential."""
        cred = OAuthCredential(client_id="merge_client", pms_type="cloudbeds")
        cred.client_secret = "old_secret"
        async_session.add(cred)
        await async_session.flush()
        cred_id = cred.id

        # Detach and modify
        cred.client_secret = "updated_secret"
        merged = await repo.save_credential(cred)
        assert merged.id == cred_id

    # -- update_token -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_update_token(self, repo, async_session):
        """Update access token and expiry."""
        cred = OAuthCredential(client_id="tok_client", pms_type="guesty")
        cred.client_secret = "secret"
        async_session.add(cred)
        await async_session.flush()

        new_expiry = datetime.now(UTC) + timedelta(hours=2)
        await repo.update_token(cred.id, "fresh_token", new_expiry)

        result = await async_session.execute(
            select(OAuthCredential).where(OAuthCredential.id == cred.id)
        )
        updated = result.scalar_one()
        assert updated.access_token == "fresh_token"
        assert updated.token_expires_at is not None

    @pytest.mark.asyncio
    async def test_update_token_not_found(self, repo):
        """ValueError when credential ID does not exist."""
        with pytest.raises(ValueError, match="not found"):
            await repo.update_token(9999, "tok", datetime.now(UTC))

    # -- token request counting -------------------------------------------

    @pytest.mark.asyncio
    async def test_get_token_request_count(self, repo, async_session):
        """Initial count is zero."""
        cred = OAuthCredential(client_id="cnt_client", pms_type="guesty")
        cred.client_secret = "secret"
        async_session.add(cred)
        await async_session.flush()

        count = await repo.get_token_request_count(cred.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_increment_token_request_count(self, repo, async_session):
        """Increment advances count and sets window start."""
        cred = OAuthCredential(client_id="inc_client", pms_type="guesty")
        cred.client_secret = "secret"
        async_session.add(cred)
        await async_session.flush()

        await repo.increment_token_request_count(cred.id)
        count = await repo.get_token_request_count(cred.id)
        assert count == 1

        # Window start should be set
        await async_session.refresh(cred)
        assert cred.token_request_window_start is not None

    @pytest.mark.asyncio
    async def test_increment_preserves_window_start(self, repo, async_session):
        """Second increment does not overwrite existing window start."""
        cred = OAuthCredential(client_id="pres_client", pms_type="guesty")
        cred.client_secret = "secret"
        async_session.add(cred)
        await async_session.flush()

        await repo.increment_token_request_count(cred.id)
        await async_session.refresh(cred)
        first_start = cred.token_request_window_start

        await repo.increment_token_request_count(cred.id)
        await async_session.refresh(cred)
        assert cred.token_request_window_start == first_start
        assert cred.token_request_count == 2

    @pytest.mark.asyncio
    async def test_reset_token_request_window(self, repo, async_session):
        """Reset clears count and window start."""
        cred = OAuthCredential(client_id="rst_client", pms_type="guesty")
        cred.client_secret = "secret"
        async_session.add(cred)
        await async_session.flush()

        await repo.increment_token_request_count(cred.id)
        await repo.increment_token_request_count(cred.id)
        await repo.reset_token_request_window(cred.id)

        count = await repo.get_token_request_count(cred.id)
        assert count == 0
        await async_session.refresh(cred)
        assert cred.token_request_window_start is None

    @pytest.mark.asyncio
    async def test_get_token_request_count_not_found(self, repo):
        """ValueError when credential ID does not exist."""
        with pytest.raises(ValueError, match="not found"):
            await repo.get_token_request_count(9999)

    @pytest.mark.asyncio
    async def test_increment_not_found(self, repo):
        """ValueError when credential ID does not exist."""
        with pytest.raises(ValueError, match="not found"):
            await repo.increment_token_request_count(9999)

    @pytest.mark.asyncio
    async def test_reset_not_found(self, repo):
        """ValueError when credential ID does not exist."""
        with pytest.raises(ValueError, match="not found"):
            await repo.reset_token_request_window(9999)
