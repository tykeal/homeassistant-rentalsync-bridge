# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty PMS provider — full Guesty Open API integration."""

import asyncio
import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from src.providers.base import (
    AbsenceResult,
    PMSAuthenticationError,
    PMSConnectionError,
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSProviderError,
    PMSRateLimitError,
    PMSReservation,
    PMSRoom,
    TokenResult,
)
from src.providers.guesty.auth import GUESTY_TOKEN_URL, GuestyTokenManager
from src.providers.guesty.description_fields import retired_fields_for
from src.providers.guesty.legacy_reservations import (
    DEFAULT_PAGE_LIMIT,
    LegacyReservationSource,
)
from src.providers.guesty.reservations_v3 import V3ReservationSource
from src.providers.guesty.sources import GuestyHTTPError
from src.providers.registry import provider
from src.utils.field_labels import humanize_label

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.models.oauth_credential import OAuthCredential
    from src.providers.guesty.sources import ReservationSource
    from src.repositories.credential_repository import CredentialRepository

logger = logging.getLogger(__name__)

GUESTY_BASE_URL = "https://open-api.guesty.com"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0

# Custom-field definitions (issue #127).
#
# ``GET /v1/reservations-v3/{id}/custom-fields`` returns only
# ``{_id, fieldId, value}`` — never a name — so values arrive keyed by a raw
# ObjectId.  The names live on a separate, unpaginated account-level
# endpoint which returns every definition for the account in one call;
# ``reservation`` definitions must be separated from ``listing`` ones
# client-side because the endpoint takes no discriminator.
#
# The definitions path is account-scoped and needs the real account ID, so
# it is resolved first from ``GET /v1/accounts/me``.  Both paths are the
# ones our Home Assistant Guesty integration uses against the live API
# (commit ``62f58cc`` there); the ``me`` alias is not known to work on the
# ``custom-fields`` sub-path.
ACCOUNT_URL = "/v1/accounts/me"
CUSTOM_FIELD_DEFINITIONS_URL = "/v1/accounts/{account_id}/custom-fields"
CUSTOM_FIELD_OBJECT_RESERVATION = "reservation"

# Per-cycle request accounting (FR-043): every request attempt, retries
# included, is counted under the first resource whose path pattern matches;
# anything unmatched (account lookups, definitions) counts as ``other``.
REQUEST_RESOURCES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("search", re.compile(r"/v1/reservations(-v3/search)?")),
    ("by_ids", re.compile(r"/v1/reservations-v3")),
    ("guests", re.compile(r"/v1/guests(-crud)?/[^/]+")),
    ("custom_fields", re.compile(r"/v1/reservations-v3/[^/]+/custom-fields")),
    ("listings", re.compile(r"/v1/listings(/[^/]+)?")),
)
REQUEST_RESOURCE_OTHER = "other"

# A definition scoped to ``both`` applies to listings *and* reservations, so
# it carries a label a reservation value can need.  Dropping it was why some
# fields still rendered as a raw ObjectId after issue #127.
CUSTOM_FIELD_OBJECT_BOTH = "both"

# ``object`` values whose definitions can label a reservation custom field.
CUSTOM_FIELD_RESERVATION_OBJECTS = frozenset(
    {CUSTOM_FIELD_OBJECT_RESERVATION, CUSTOM_FIELD_OBJECT_BOTH}
)

# Definitions change only when an account owner edits them in the Guesty
# dashboard, and carry no ``updatedAt`` or ETag, so staleness cannot be
# detected cheaply.  They are cached process-wide for a day, which keeps
# this at well under one call per sync against Guesty's account-wide
# limits (15/sec, 120/min, 5000/hour — see issue #128).
CUSTOM_FIELD_DEFINITIONS_TTL = timedelta(hours=24)

# A failed fetch is cached too, so a token lacking Guesty's custom-field
# permission cannot generate a request (or a log line) on every sync.  The
# shorter window lets a permission grant take effect the same hour.
CUSTOM_FIELD_DEFINITIONS_FAILURE_TTL = timedelta(hours=1)

# Cache entries: key -> (expires_at, labels by fieldId).
_definitions_cache: dict[str, tuple[datetime, dict[str, str]]] = {}
_definitions_lock = asyncio.Lock()

# Resolved account IDs: key -> (expires_at, account ID).
#
# An account ID never changes for a given set of credentials, so it is
# cached on the same schedule as the definitions it is needed for.  Without
# this the account-scoped definitions path would cost an extra request on
# every sync, which this account cannot afford (issue #128).  A failed
# resolution is not cached here: it takes the definitions failure path, so
# the short failure TTL already suppresses the retry.
_account_id_cache: dict[str, tuple[datetime, str]] = {}


def clear_custom_field_definitions_cache() -> None:
    """Drop every cached custom-field definition set.

    Also drops the resolved account IDs the definitions fetch depends on.

    Exposed for tests and for callers that know the account's definitions
    have changed.
    """
    _definitions_cache.clear()
    _account_id_cache.clear()


@provider("guesty")
class GuestyProvider(PMSProvider):
    """PMSProvider implementation backed by the Guesty Open API."""

    def __init__(
        self,
        credential_repo: CredentialRepository | None = None,
        credential_id: int | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_manager: GuestyTokenManager | None = None,
        http_client: httpx.AsyncClient | None = None,
        reservations_api: str = "v3",
        **_kwargs: Any,
    ) -> None:
        """Initialize GuestyProvider.

        Args:
            credential_repo: Repository for credential persistence.
            credential_id: Database ID of the credential row.
            client_id: Guesty OAuth2 client ID.
            client_secret: Guesty OAuth2 client secret.
            token_manager: Optional pre-configured token manager.
            http_client: Optional httpx client for API calls.
            reservations_api: Reservation source: ``"v3"`` (default; one
                combined Reservations V3 search per sync cycle, see
                ``begin_cycle``) or ``"legacy"`` (rollback only).
            **_kwargs: Ignored — accepted for registry compatibility.
        """
        self._http_client = http_client
        self._owns_client = http_client is None
        self._request_counts: Counter[str] = Counter()

        self._token_manager: GuestyTokenManager | None
        if token_manager is not None:
            self._token_manager = token_manager
        elif (
            credential_repo is not None
            and credential_id is not None
            and client_id is not None
            and client_secret is not None
        ):
            self._token_manager = GuestyTokenManager(
                client_id=client_id,
                client_secret=client_secret,
                credential_repo=credential_repo,
                credential_id=credential_id,
                http_client=http_client,
            )
        else:
            self._token_manager = None

        sources: dict[
            str, type[LegacyReservationSource] | type[V3ReservationSource]
        ] = {
            "legacy": LegacyReservationSource,
            "v3": V3ReservationSource,
        }
        self._reservations_api = reservations_api
        source_cls = sources.get(reservations_api)
        if source_cls is None:
            msg = (
                f"Unknown reservations_api {reservations_api!r}; "
                f"expected one of {sorted(sources)}"
            )
            raise ValueError(msg)
        self._reservations: ReservationSource = source_cls(
            request=self._request,
            listing_scopes=self._listing_scopes,
        )

    async def aclose(self) -> None:
        """Log this cycle's request counts, then close the owned client.

        The scheduler builds one provider per sync cycle and closes it at
        the end, so one INFO line summarises each cycle's request budget
        (FR-043).  The counters restart afterwards.
        """
        self.log_request_counts()
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> GuestyProvider:
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager."""
        await self.aclose()

    def log_request_counts(self) -> None:
        """Log the requests made since the last report, then reset them."""
        if not self._request_counts:
            return
        summary = " ".join(
            f"{name}={self._request_counts[name]}"
            for name in (
                *(name for name, _ in REQUEST_RESOURCES),
                REQUEST_RESOURCE_OTHER,
            )
        )
        logger.info("Guesty requests this cycle: %s", summary)
        self._request_counts.clear()

    @staticmethod
    def _request_resource(url: str) -> str:
        """Classify a request URL into its accounting resource."""
        path = httpx.URL(url).path
        for name, pattern in REQUEST_RESOURCES:
            if pattern.fullmatch(path):
                return name
        return REQUEST_RESOURCE_OTHER

    # -- helpers ---------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx AsyncClient.

        Returns:
            An httpx.AsyncClient instance.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=GUESTY_BASE_URL,
                timeout=httpx.Timeout(30.0),
            )
            self._owns_client = True
        return self._http_client

    async def _get_auth_headers(self) -> dict[str, str]:
        """Build authorization headers using the token manager.

        Returns:
            Dict with Authorization header.

        Raises:
            PMSAuthenticationError: If no token manager is configured.
        """
        if self._token_manager is None:
            msg = "GuestyProvider requires a token manager"
            raise PMSAuthenticationError(msg)
        token = await self._token_manager.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        raise_on_404: bool = True,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic for 429 rate limits.

        Implements exponential backoff (1s → 2s → 4s, max 30s) and
        respects the Retry-After header.  Max 3 retries.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Request URL (relative to base URL).
            params: Optional query parameters.
            raise_on_404: If False, return the 404 response instead
                of raising.  Defaults to True.

        Returns:
            The httpx.Response object.

        Raises:
            PMSRateLimitError: If retries are exhausted on 429.
            PMSAuthenticationError: On 401/403 responses.
            PMSConnectionError: On network-level failures.
            PMSProviderError: On other HTTP errors.
        """
        client = self._get_client()
        headers = await self._get_auth_headers()
        backoff = INITIAL_BACKOFF

        resource = self._request_resource(url)
        for attempt in range(MAX_RETRIES + 1):
            self._request_counts[resource] += 1
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                msg = f"Network error calling Guesty API: {exc}"
                raise PMSConnectionError(msg) from exc

            if response.status_code == 429:  # noqa: PLR2004
                if attempt >= MAX_RETRIES:
                    retry_after = _parse_retry_after(response)
                    msg = f"Guesty API rate limit exceeded after {MAX_RETRIES} retries"
                    raise PMSRateLimitError(msg, retry_after=retry_after)

                retry_after = _parse_retry_after(response)
                wait = min(
                    retry_after if retry_after is not None else backoff, MAX_BACKOFF
                )
                logger.warning(
                    "Guesty API rate limited (429), retrying in %.1fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            if response.status_code in (401, 403):
                msg = (
                    f"Guesty authentication failed "
                    f"(HTTP {response.status_code}): {response.text}"
                )
                raise PMSAuthenticationError(msg)

            if response.status_code == 404 and not raise_on_404:  # noqa: PLR2004
                return response

            if response.status_code >= 400:  # noqa: PLR2004
                msg = f"Guesty API error (HTTP {response.status_code}): {response.text}"
                raise GuestyHTTPError(
                    msg, status_code=response.status_code, body=response.text
                )

            return response

        # Should not be reached, but satisfy type checker
        msg = "Request loop exited without returning"  # pragma: no cover
        raise PMSProviderError(msg)  # pragma: no cover

    # -- PMSProvider interface -------------------------------------------------

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier string.

        Returns:
            Literal ``"guesty"``.
        """
        return "guesty"

    @property
    def has_separate_custom_fields(self) -> bool:
        """Guesty exposes custom fields via a dedicated v3 endpoint."""
        return True

    @property
    def retired_description_fields(self) -> dict[str, str]:
        """Description fields Reservations V3 no longer supplies (FR-036).

        Returns:
            The retired ``money_*`` fields and their reason in V3 mode;
            empty in legacy mode.
        """
        return dict(retired_fields_for("guesty", self._reservations_api))

    async def get_listings(self) -> list[PMSListing]:
        """Fetch all listings from the Guesty API.

        Handles skip-based pagination (limit=100).  Stops when
        the number of results returned is less than the limit.

        Returns:
            List of PMSListing DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        listings: list[PMSListing] = []
        skip = 0

        while True:
            response = await self._request(
                "GET",
                "/v1/listings",
                params={"limit": DEFAULT_PAGE_LIMIT, "skip": skip},
            )
            data = response.json()
            results = data.get("results", [])

            for item in results:
                pms_id = str(item.get("_id", ""))
                if not pms_id:
                    logger.warning("Skipping Guesty listing with missing _id")
                    continue
                listings.append(
                    PMSListing(
                        pms_id=pms_id,
                        name=item.get("title") or item.get("nickname") or "",
                        timezone=item.get("timezone") or "UTC",
                        address=_format_address(item.get("address")),
                    )
                )

            if len(results) < DEFAULT_PAGE_LIMIT:
                break
            skip += DEFAULT_PAGE_LIMIT

        return listings

    def begin_cycle(self, listing_pms_ids: Sequence[str]) -> None:
        """Declare the listings a sync cycle is about to sync (research R2).

        The V3 source searches every declared listing's reservations in
        one combined search; the legacy source fetches per listing and
        ignores the declaration.

        Args:
            listing_pms_ids: PMS IDs of every enabled, sync-enabled listing.
        """
        if isinstance(self._reservations, V3ReservationSource):
            self._reservations.begin_cycle(listing_pms_ids)

    async def confirm_absent(
        self,
        listing_pms_id: str,
        reservation_ids: Sequence[str],
    ) -> dict[str, AbsenceResult]:
        """Confirm stored reservations missing from a snapshot are gone.

        V3 mode asks the V3 source; legacy mode keeps the base behaviour
        (every ID confirmed, as before spec 004).

        Args:
            listing_pms_id: Listing being synced.
            reservation_ids: Base reservation IDs absent from the snapshot.

        Returns:
            One result per requested ID.
        """
        if isinstance(self._reservations, V3ReservationSource):
            return await self._reservations.confirm_absent(
                listing_pms_id, reservation_ids
            )
        return await super().confirm_absent(listing_pms_id, reservation_ids)

    async def get_reservations(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Fetch reservations for a Guesty listing from the selected source.

        Args:
            listing_pms_id: Guesty listing ID.
            start_date: Start of the stay window (source default if None).
            end_date: End of the stay window (source default if None).

        Returns:
            List of PMSReservation DTOs belonging to this listing whose stay
            overlaps the window.

        Raises:
            PMSProviderError: Whenever the source cannot return a complete
                snapshot; see ``LegacyReservationSource.fetch``.
        """
        return await self._reservations.fetch(
            listing_pms_id, start_date=start_date, end_date=end_date
        )

    async def _listing_scopes(
        self,
        listing_pms_id: str,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Resolve the fetch scope and ownership scope for a listing.

        The two scopes differ on purpose:

        * The **fetch** scope widens the server-side ``listingId`` filter to
          include the parent unit type when the synced listing is itself an
          imported child.  Guesty computes a reservation's ``listingId`` as
          ``unitId ?? unitTypeId``, but not every payload carries ``unitId``
          at the top level -- an assigned stay can report the parent there
          and name the child only in ``stay[].unitId`` or ``mtl._id``.  If
          the server honours the filter, a parent-keyed record would be
          excluded from the snapshot and the sync prune logic would cancel a
          live booking.
        * The **ownership** scope stays specific to the synced listing and
          its own sub-units, so widening the fetch never lets a sibling
          sub-unit's reservation be stored against this listing.

        Args:
            listing_pms_id: Guesty listing ID being synced.

        Returns:
            Tuple of ``(fetch_ids, scope_ids)``.

        Raises:
            PMSProviderError: On API failure.
        """
        data = await self._listing_document(listing_pms_id)
        rooms = _rooms_from_document(data, listing_pms_id)
        scope_ids = frozenset({listing_pms_id, *(room.pms_room_id for room in rooms)})
        return frozenset(scope_ids | _parent_listing_ids(data)), scope_ids

    async def _listing_document(self, listing_pms_id: str) -> dict[str, Any]:
        """Fetch the raw Guesty listing document.

        Args:
            listing_pms_id: Guesty listing ID.

        Returns:
            Decoded listing document.

        Raises:
            PMSProviderError: On API failure.
        """
        response = await self._request("GET", f"/v1/listings/{listing_pms_id}")
        data: dict[str, Any] = response.json()
        return data

    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]:
        """Fetch rooms/units for a Guesty listing.

        For multi-unit listings, maps ``childListings`` to PMSRoom.
        For single-unit listings, creates one implicit PMSRoom.

        Args:
            listing_pms_id: Guesty listing ID.

        Returns:
            List of PMSRoom DTOs.

        Raises:
            PMSProviderError: On API failure.
        """
        data = await self._listing_document(listing_pms_id)
        return _rooms_from_document(data, listing_pms_id)

    async def get_guest(self, guest_id: str) -> PMSGuest | None:
        """Fetch guest details from the Guesty API.

        V3 mode reads ``/v1/guests-crud/{id}`` projected to the three
        fields used (FR-001); legacy mode reads ``/v1/guests/{id}``.

        Args:
            guest_id: Guesty guest ID.

        Returns:
            PMSGuest if found, else None (on 404).

        Raises:
            PMSProviderError: On non-404 API failures.
        """
        if self._reservations_api == "v3":
            response = await self._request(
                "GET",
                f"/v1/guests-crud/{guest_id}",
                params={"fields": "fullName phone email"},
                raise_on_404=False,
            )
        else:
            response = await self._request(
                "GET",
                f"/v1/guests/{guest_id}",
                raise_on_404=False,
            )

        if response.status_code == 404:  # noqa: PLR2004
            return None

        data = response.json()
        return PMSGuest(
            guest_id=guest_id,
            full_name=data.get("fullName") or "",
            phone=data.get("phone"),
            email=data.get("email"),
        )

    async def get_custom_fields(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        """Fetch custom fields for a Guesty reservation.

        Uses the v3 endpoint: GET /v1/reservations-v3/{id}/custom-fields.

        Args:
            reservation_id: Guesty reservation ID.

        Returns:
            Dictionary of fieldId → value mappings.

        Raises:
            PMSProviderError: On API failure.
        """
        response = await self._request(
            "GET",
            f"/v1/reservations-v3/{reservation_id}/custom-fields",
        )
        data = response.json()

        # Response is a list of {fieldId, value} objects
        if isinstance(data, list):
            return {
                item["fieldId"]: item.get("value")
                for item in data
                if isinstance(item, dict) and "fieldId" in item
            }

        # Fallback if response is a dict with "results" or similar
        results = data.get("results", data.get("customFields", []))
        if isinstance(results, list):
            return {
                item["fieldId"]: item.get("value")
                for item in results
                if isinstance(item, dict) and "fieldId" in item
            }

        return {}

    async def get_custom_field_definitions(self) -> dict[str, str]:
        """Fetch reservation custom-field labels for the Guesty account.

        Uses ``GET /v1/accounts/{account_id}/custom-fields``, which is
        unpaginated and returns every definition on the account in a
        single call.  The account ID comes from ``GET /v1/accounts/me``
        and is cached alongside the definitions, so a sync pays for
        neither request.  Results are cached process-wide per account
        (see ``CUSTOM_FIELD_DEFINITIONS_TTL``) because the value endpoint
        keys by ObjectId on every reservation but the names never change.

        Never raises: a 403 (the token lacks Guesty's custom-field
        permission), an unresolvable account ID, any other API error, or
        a malformed payload logs a single warning and yields an empty
        mapping, leaving labels to fall back to the raw field key.

        Returns:
            Mapping of ``fieldId`` to display label, possibly empty.
        """
        cache_key = self._definitions_cache_key()
        if cache_key is None:
            # Without a trustworthy account identity the result cannot be
            # cached: sharing one entry across accounts would serve one
            # account's labels to another.  Fetch uncached instead.
            logger.debug(
                "Guesty account identity unavailable; "
                "custom-field definitions will not be cached"
            )
            return await self._fetch_custom_field_definitions()

        now = datetime.now(UTC)
        async with _definitions_lock:
            cached = _definitions_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return dict(cached[1])

            try:
                labels = await self._fetch_or_raise_definitions()
            except Exception as exc:  # labels are cosmetic, never fatal
                _warn_definitions_unavailable(exc)
                _definitions_cache[cache_key] = (
                    now + CUSTOM_FIELD_DEFINITIONS_FAILURE_TTL,
                    {},
                )
                return {}

            _definitions_cache[cache_key] = (
                now + CUSTOM_FIELD_DEFINITIONS_TTL,
                labels,
            )
            return dict(labels)

    async def _fetch_custom_field_definitions(self) -> dict[str, str]:
        """Fetch definitions without consulting or populating the cache.

        Returns:
            Mapping of ``fieldId`` to display label; empty on any failure.
        """
        try:
            return await self._fetch_or_raise_definitions()
        except Exception as exc:  # labels are cosmetic, never fatal
            _warn_definitions_unavailable(exc)
            return {}

    async def _fetch_or_raise_definitions(self) -> dict[str, str]:
        """Request and parse the account's custom-field definitions.

        Resolves the account ID first (cached, so this is normally not an
        extra request) because the definitions path is account-scoped.

        Returns:
            Mapping of ``fieldId`` to display label.  An account with no
            reservation custom fields legitimately yields an empty
            mapping, which is a success.

        Raises:
            PMSProviderError: If either request fails, the account ID
                cannot be resolved, or the payload is not shaped as
                documented.
        """
        account_id = await self._resolve_account_id()
        response = await self._request(
            "GET",
            CUSTOM_FIELD_DEFINITIONS_URL.format(account_id=account_id),
        )
        return _parse_custom_field_definitions(response.json())

    async def _resolve_account_id(self) -> str:
        """Resolve the Guesty account ID behind the current credentials.

        The ID is stable, so it is cached per account for the same day as
        the definitions that need it; a sync therefore does not pay for
        this request (issue #128).  A failure propagates and is handled
        exactly like a definitions failure: a warning, raw-identifier
        labels, and a short failure cache.

        Returns:
            The account ID.

        Raises:
            PMSProviderError: If the response is not a JSON object or
                carries no usable ``_id``.
        """
        cache_key = self._definitions_cache_key()
        now = datetime.now(UTC)
        if cache_key is not None:
            cached = _account_id_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]

        response = await self._request("GET", ACCOUNT_URL)
        account_id = _parse_account_id(response.json())

        if cache_key is not None:
            _account_id_cache[cache_key] = (
                now + CUSTOM_FIELD_DEFINITIONS_TTL,
                account_id,
            )
        return account_id

    def _definitions_cache_key(self) -> str | None:
        """Build the cache key identifying this provider's Guesty account.

        Definitions are account-wide, and an installation can be
        repointed at a different Guesty account without its credential
        row changing, so the key comes from the token manager's hashed
        account identity rather than the row ID.

        Returns:
            Stable account key, or None when the identity is unknown and
            caching therefore has to be skipped.
        """
        manager = self._token_manager
        if manager is None:
            return None
        account_key = manager.account_key
        if not isinstance(account_key, str) or not account_key:
            return None
        return f"account:{account_key}"

    async def refresh_token(
        self,
        credential: OAuthCredential,
    ) -> TokenResult:
        """Refresh the Guesty OAuth token.

        Guesty uses client-credentials flow, so "refresh" means
        requesting a new token.  Delegates to the token manager when
        available; otherwise falls back to a direct HTTP request using
        the credential's ``client_id`` / ``client_secret`` (e.g. when
        constructed by ``OAuthService`` with no kwargs).

        Args:
            credential: Current credential record.

        Returns:
            TokenResult with new access token and expiry.

        Raises:
            PMSAuthenticationError: If token request fails.
            PMSConnectionError: If unable to reach Guesty API.
        """
        if self._token_manager is not None:
            await self._token_manager.invalidate_cache()
            token = await self._token_manager.get_token()
            expires_at = self._token_manager.cached_expires_at or datetime.now(UTC)
            return TokenResult(
                access_token=token,
                refresh_token=None,
                expires_at=expires_at,
            )

        # No token manager — direct client-credentials request.
        # Note: rate-limit tracking is not available here because
        # credential_repo is only held by the token manager.
        if not credential.client_id or not credential.client_secret:
            msg = "Guesty refresh requires client_id and client_secret"
            raise PMSAuthenticationError(msg)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    GUESTY_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "scope": "open-api",
                        "client_id": credential.client_id,
                        "client_secret": credential.client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            msg = f"Failed to connect to Guesty OAuth endpoint: {exc}"
            raise PMSConnectionError(msg) from exc

        if response.status_code in (401, 403):
            msg = (
                f"Guesty authentication failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        if response.status_code != 200:  # noqa: PLR2004
            msg = (
                f"Guesty token request failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            msg = "Token response missing access_token"
            raise PMSAuthenticationError(msg)

        expires_in = data.get("expires_in", 86400)
        try:
            expires_in = int(expires_in)
        except TypeError, ValueError:
            expires_in = 86400  # Default 24h

        return TokenResult(
            access_token=access_token,
            refresh_token=None,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def test_connection(self) -> bool:
        """Test Guesty API reachability.

        Calls ``get_listings()`` and verifies the API responds.
        Zero listings produces a warning but is not a failure.

        Returns:
            True if the API responds successfully.

        Raises:
            PMSProviderError: On connection failure.
        """
        listings = await self.get_listings()
        if not listings:
            logger.warning(
                "Guesty connection test succeeded but returned "
                "zero listings — verify account configuration"
            )
        return True


# -- module-level helpers ------------------------------------------------------


# Keys on a *listing* document that name its parent unit type.  A Guesty
# child listing points back at its parent through ``mtl.p``; the others are
# defensive aliases seen across API generations.
_LISTING_PARENT_KEYS = ("parentListingId", "parentId", "unitTypeId")


def _warn_definitions_unavailable(exc: BaseException) -> None:
    """Log the single warning emitted when definitions cannot be read.

    Args:
        exc: The failure that prevented resolution.
    """
    logger.warning(
        "Unable to resolve Guesty custom-field names (%s); "
        "field labels fall back to raw field identifiers",
        exc,
    )


def _parse_account_id(data: Any) -> str:
    """Extract the account ID from a ``GET /v1/accounts/me`` payload.

    Guesty returns the account document itself, whose identifier is the
    top-level Mongo ``_id``.

    Args:
        data: Decoded JSON body of the account request.

    Returns:
        The account ID, stripped of surrounding whitespace.

    Raises:
        PMSProviderError: If the payload is not an object or carries no
            usable ``_id``.
    """
    if not isinstance(data, dict):
        msg = f"unexpected Guesty account payload ({type(data).__name__})"
        raise PMSProviderError(msg)

    account_id = data.get("_id")
    if not isinstance(account_id, str) or not account_id.strip():
        msg = "Guesty account payload is missing an _id"
        raise PMSProviderError(msg)

    return account_id.strip()


def _parse_custom_field_definitions(data: Any) -> dict[str, str]:
    """Build a fieldId → label map from an account custom-fields payload.

    Definitions scoped to ``reservation`` or ``both`` are kept: the
    endpoint returns listing definitions from the same list with no
    server-side discriminator, and a ``both`` definition applies to
    reservations too.

    ``key`` holds the human-readable label the account owner typed (e.g.
    ``"Test door lock code"``) and ``displayName`` holds the slugified
    variable name derived from it (e.g. ``"test_door_lock_code"``).  That
    is verified against the live API by our Home Assistant Guesty
    integration, which has been in production use since April 2026 (see
    commit ``62f58cc`` in that project, "Fix(api): correct custom fields
    endpoint").  ``key`` is therefore preferred and ``displayName`` is the
    fallback for a definition whose ``key`` is missing or blank.  The
    chosen value is still humanised, so a slug-shaped fallback — or a
    slug-shaped ``key`` — renders readably while a 24-hex ObjectId passes
    through verbatim.

    An empty list is a legitimate success (an account with no reservation
    custom fields); an unexpected *shape* is a failure, so it raises and
    takes the caller's short failure-cache path rather than being cached
    as a successful empty result for a day.

    Args:
        data: Decoded JSON body, expected to be a list of definitions
            (or a dict wrapping one under ``results``/``data``).

    Returns:
        Mapping of ``fieldId`` to display label; empty when the account
        defines no reservation custom fields.

    Raises:
        PMSProviderError: If the payload is not shaped as documented.
    """
    if isinstance(data, dict):
        for wrapper in ("results", "data", "customFields", "fields"):
            nested = data.get(wrapper)
            if isinstance(nested, list):
                data = nested
                break

    if not isinstance(data, list):
        msg = (
            "unexpected Guesty custom-field definitions payload "
            f"({type(data).__name__})"
        )
        raise PMSProviderError(msg)

    labels: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        object_type = item.get("object")
        if (
            not isinstance(object_type, str)
            or object_type not in CUSTOM_FIELD_RESERVATION_OBJECTS
        ):
            continue
        field_id = item.get("fieldId") or item.get("_id")
        if not isinstance(field_id, str) or not field_id:
            continue

        raw_label = item.get("key")
        if not isinstance(raw_label, str) or not raw_label.strip():
            raw_label = item.get("displayName")
        if not isinstance(raw_label, str):
            continue
        label = humanize_label(raw_label)
        if label:
            labels[field_id] = label

    return labels


def _rooms_from_document(data: dict[str, Any], listing_pms_id: str) -> list[PMSRoom]:
    """Map a Guesty listing document to its rooms.

    Args:
        data: Raw listing document from ``GET /v1/listings/{id}``.
        listing_pms_id: Guesty listing ID the document describes.

    Returns:
        One PMSRoom per child sub-unit, or a single implicit room for a
        single-unit listing.
    """
    children = data.get("childListings") or []
    if children:
        rooms: list[PMSRoom] = []
        for child in children:
            child_id = str(child.get("_id", ""))
            if not child_id:
                continue
            rooms.append(
                PMSRoom(
                    pms_room_id=child_id,
                    name=child.get("title") or child.get("nickname") or "",
                    room_type=child.get("roomType"),
                )
            )
        return rooms

    return [
        PMSRoom(
            pms_room_id=listing_pms_id,
            name=data.get("title") or data.get("nickname") or "Main",
            room_type=data.get("propertyType"),
        )
    ]


def _parent_listing_ids(data: dict[str, Any]) -> set[str]:
    """Collect the parent unit type IDs a listing document points at.

    Args:
        data: Raw listing document from ``GET /v1/listings/{id}``.

    Returns:
        Set of parent listing identifiers, empty for a listing with no
        parent.
    """
    parents: set[str] = set()
    for key in _LISTING_PARENT_KEYS:
        value = data.get(key)
        if value:
            parents.add(str(value))

    mtl = data.get("mtl")
    if isinstance(mtl, dict) and mtl.get("p"):
        parents.add(str(mtl["p"]))

    return parents


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse the Retry-After header from an HTTP response.

    Args:
        response: The httpx response to inspect.

    Returns:
        Seconds to wait, or None if header is absent/unparsable.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError, TypeError:
        return None


def _format_address(address: dict[str, Any] | None) -> str | None:
    """Format a Guesty address object into a string.

    Args:
        address: Guesty address dict with street, city, state, etc.

    Returns:
        Formatted address string, or None if no address data.
    """
    if not address:
        return None

    full = address.get("full")
    if full:
        return str(full)

    parts = [
        address.get("street"),
        address.get("city"),
        address.get("state"),
        address.get("zipcode"),
        address.get("country"),
    ]
    formatted = ", ".join(str(p) for p in parts if p)
    return formatted or None
