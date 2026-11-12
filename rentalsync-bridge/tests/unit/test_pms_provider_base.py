# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for PMS provider base classes, DTOs, and exception hierarchy."""

from datetime import UTC, date, datetime

import pytest
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

# --- ABC tests ---------------------------------------------------------------


class TestPMSProviderABC:
    """Tests for the PMSProvider abstract base class contract."""

    def test_cannot_instantiate_directly(self):
        """Test that PMSProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            PMSProvider()  # type: ignore[abstract]

    def test_subclass_missing_methods_raises_type_error(self):
        """Test that a subclass with no methods raises TypeError."""

        class Incomplete(PMSProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_missing_one_method_raises_type_error(self):
        """Test that a subclass missing one abstract method raises TypeError."""

        class AlmostComplete(PMSProvider):
            async def get_listings(self):
                return []

            async def get_reservations(
                self, listing_pms_id, *, start_date=None, end_date=None
            ):
                return []

            async def get_rooms(self, listing_pms_id):
                return []

            async def get_guest(self, guest_id):
                return None

            async def get_custom_fields(self, reservation_id):
                return {}

            async def refresh_token(self, credential):
                return TokenResult(
                    access_token="a",
                    refresh_token=None,
                    expires_at=datetime.now(UTC),
                )

            # missing test_connection and provider_type

        with pytest.raises(TypeError):
            AlmostComplete()  # type: ignore[abstract]


# --- DTO frozen-dataclass tests ----------------------------------------------


class TestDTOFrozenBehavior:
    """Tests that DTO dataclasses are frozen and have correct defaults."""

    def test_pms_listing_frozen(self):
        """Test that PMSListing attributes cannot be reassigned."""
        listing = PMSListing(pms_id="1", name="A", timezone="UTC")
        with pytest.raises(AttributeError):
            listing.name = "B"  # type: ignore[misc]

    def test_pms_room_frozen(self):
        """Test that PMSRoom attributes cannot be reassigned."""
        room = PMSRoom(pms_room_id="r1", name="Room 1")
        with pytest.raises(AttributeError):
            room.name = "Room 2"  # type: ignore[misc]

    def test_pms_reservation_frozen(self):
        """Test that PMSReservation attributes cannot be reassigned."""
        res = PMSReservation(
            pms_booking_id="b1",
            listing_pms_id="l1",
            guest_name="Alice",
            guest_id="g1",
            check_in=datetime(2026, 1, 1, tzinfo=UTC),
            check_out=datetime(2026, 1, 5, tzinfo=UTC),
            status="confirmed",
            room_ids=("r1",),
            custom_data={},
        )
        with pytest.raises(AttributeError):
            res.status = "cancelled"  # type: ignore[misc]

    def test_pms_guest_frozen(self):
        """Test that PMSGuest attributes cannot be reassigned."""
        guest = PMSGuest(guest_id="g1", full_name="Bob")
        with pytest.raises(AttributeError):
            guest.full_name = "Charlie"  # type: ignore[misc]

    def test_token_result_frozen(self):
        """Test that TokenResult attributes cannot be reassigned."""
        tr = TokenResult(
            access_token="tok",
            refresh_token="ref",
            expires_at=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            tr.access_token = "new"  # type: ignore[misc]

    def test_pms_listing_defaults(self):
        """Test that PMSListing optional fields default correctly."""
        listing = PMSListing(pms_id="1", name="A", timezone="UTC")
        assert listing.address is None
        assert listing.rooms == ()

    def test_pms_room_defaults(self):
        """Test that PMSRoom optional fields default correctly."""
        room = PMSRoom(pms_room_id="r1", name="Room 1")
        assert room.room_type is None

    def test_pms_guest_defaults(self):
        """Test that PMSGuest optional fields default correctly."""
        guest = PMSGuest(guest_id="g1", full_name="Bob")
        assert guest.phone is None
        assert guest.email is None


# --- Exception hierarchy tests -----------------------------------------------


class TestExceptionHierarchy:
    """Tests for the PMS exception class hierarchy."""

    def test_pms_provider_error_is_exception(self):
        """Test that PMSProviderError is a subclass of Exception."""
        assert issubclass(PMSProviderError, Exception)

    def test_authentication_error_is_provider_error(self):
        """Test that PMSAuthenticationError inherits from PMSProviderError."""
        err = PMSAuthenticationError("bad creds")
        assert isinstance(err, PMSProviderError)

    def test_rate_limit_error_is_provider_error(self):
        """Test that PMSRateLimitError inherits from PMSProviderError."""
        err = PMSRateLimitError("slow down")
        assert isinstance(err, PMSProviderError)

    def test_token_rate_limit_error_is_provider_error(self):
        """Test that TokenRateLimitError inherits from PMSProviderError."""
        err = TokenRateLimitError("too many tokens")
        assert isinstance(err, PMSProviderError)

    def test_connection_error_is_provider_error(self):
        """Test that PMSConnectionError inherits from PMSProviderError."""
        err = PMSConnectionError("unreachable")
        assert isinstance(err, PMSProviderError)

    def test_rate_limit_error_retry_after(self):
        """Test that PMSRateLimitError stores retry_after value."""
        err = PMSRateLimitError("slow down", retry_after=30.0)
        assert err.retry_after == 30.0
        assert str(err) == "slow down"

    def test_rate_limit_error_retry_after_default(self):
        """Test that PMSRateLimitError retry_after defaults to None."""
        err = PMSRateLimitError("slow down")
        assert err.retry_after is None

    def test_token_rate_limit_error_reset_at(self):
        """Test that TokenRateLimitError stores reset_at value."""
        reset = datetime(2026, 6, 1, tzinfo=UTC)
        err = TokenRateLimitError("too many tokens", reset_at=reset)
        assert err.reset_at == reset
        assert str(err) == "too many tokens"

    def test_token_rate_limit_error_reset_at_default(self):
        """Test that TokenRateLimitError reset_at defaults to None."""
        err = TokenRateLimitError("too many tokens")
        assert err.reset_at is None

    def test_catch_provider_error_catches_subclasses(self):
        """Test that catching PMSProviderError catches all subclasses."""
        for exc_cls in (
            PMSAuthenticationError,
            PMSRateLimitError,
            PMSConnectionError,
            TokenRateLimitError,
        ):
            with pytest.raises(PMSProviderError):
                raise exc_cls("test")


# --- Spec 004 additions (data-model.md §1) -----------------------------------


def _reservation(**overrides):
    """Build a minimal PMSReservation."""
    values = {
        "pms_booking_id": "R1",
        "listing_pms_id": "L1",
        "guest_name": None,
        "guest_id": None,
        "check_in": datetime(2026, 1, 1, tzinfo=UTC),
        "check_out": datetime(2026, 1, 3, tzinfo=UTC),
        "status": "confirmed",
        "room_ids": ("L1",),
        "custom_data": {},
    }
    values.update(overrides)
    return PMSReservation(**values)


def _minimal_provider():
    """Return a concrete provider that overrides none of the new members."""

    class Minimal(PMSProvider):
        async def get_listings(self):
            return []

        async def get_reservations(
            self, listing_pms_id, *, start_date=None, end_date=None
        ):
            return []

        async def get_rooms(self, listing_pms_id):
            return []

        async def get_guest(self, guest_id):
            return None

        async def get_custom_fields(self, reservation_id):
            return {}

        async def refresh_token(self, credential):
            raise NotImplementedError

        async def test_connection(self):
            return True

        @property
        def provider_type(self):
            return "minimal"

    return Minimal()


class TestOccupancyInterval:
    """PMSOccupancyInterval and its derived booking suffix."""

    def test_first_interval_suffix_is_room(self):
        """Ordinal 1 keeps the legacy ``{rid}::{room}`` shape."""
        from src.providers.base import PMSOccupancyInterval

        interval = PMSOccupancyInterval(
            room_id="U1",
            ordinal=1,
            check_in=None,
            check_out=None,
            local_dates=(date(2026, 1, 1), date(2026, 1, 3)),
            guests_count=None,
        )
        assert interval.booking_suffix == "U1"

    @pytest.mark.parametrize("ordinal", [2, 3])
    def test_later_interval_suffix_has_ordinal(self, ordinal):
        """Ordinal n >= 2 appends ``#n``."""
        from src.providers.base import PMSOccupancyInterval

        interval = PMSOccupancyInterval(
            room_id="U1",
            ordinal=ordinal,
            check_in=datetime(2026, 1, 1, tzinfo=UTC),
            check_out=datetime(2026, 1, 3, tzinfo=UTC),
            local_dates=(date(2026, 1, 1), date(2026, 1, 3)),
            guests_count=2,
        )
        assert interval.booking_suffix == f"U1#{ordinal}"

    def test_ordinal_below_one_rejected(self):
        """Ordinals start at 1."""
        from src.providers.base import PMSOccupancyInterval

        with pytest.raises(ValueError, match="ordinal"):
            PMSOccupancyInterval(
                room_id="U1",
                ordinal=0,
                check_in=None,
                check_out=None,
                local_dates=None,
                guests_count=None,
            )


class TestReservationAdditions:
    """The three optional PMSReservation fields keep old callers working."""

    def test_new_fields_default_to_legacy_behaviour(self):
        """Defaults mean: room_ids path, no change key, no inline fields."""
        reservation = _reservation()
        assert reservation.intervals == ()
        assert reservation.change_key is None
        assert reservation.inline_custom_fields is None

    def test_inline_custom_fields_states(self):
        """present carries values; absent and malformed carry none."""
        from src.providers.base import InlineCustomFields

        present = InlineCustomFields(state="present", values={"f1": "1234"})
        assert dict(present.values) == {"f1": "1234"}
        assert InlineCustomFields(state="absent").values == {}
        assert InlineCustomFields(state="malformed").values == {}

    def test_inline_custom_fields_rejects_unknown_state(self):
        """Only the three R8 states exist."""
        from src.providers.base import InlineCustomFields

        with pytest.raises(ValueError, match="state"):
            InlineCustomFields(state="partial")  # type: ignore[arg-type]


class TestAbsence:
    """AbsenceVerdict and AbsenceResult (research R5)."""

    def test_verdict_members(self):
        """Exactly the four R5 verdicts exist."""
        from src.providers.base import AbsenceVerdict

        assert {v.name for v in AbsenceVerdict} == {
            "CONFIRMED_ABSENT",
            "UNCONFIRMED",
            "REATTRIBUTED",
            "INCONSISTENT",
        }

    def test_reattributed_carries_reservation(self):
        """REATTRIBUTED holds the rebuilt reservation."""
        from src.providers.base import AbsenceResult, AbsenceVerdict

        reservation = _reservation()
        result = AbsenceResult(
            AbsenceVerdict.REATTRIBUTED, reservation=reservation, detail="moved"
        )
        assert result.reservation is reservation

    def test_reattributed_requires_reservation(self):
        """A REATTRIBUTED verdict without its reservation is rejected."""
        from src.providers.base import AbsenceResult, AbsenceVerdict

        with pytest.raises(ValueError, match="reservation"):
            AbsenceResult(AbsenceVerdict.REATTRIBUTED)

    @pytest.mark.parametrize(
        "verdict", ["CONFIRMED_ABSENT", "UNCONFIRMED", "INCONSISTENT"]
    )
    def test_other_verdicts_reject_reservation(self, verdict):
        """Only REATTRIBUTED may carry a reservation."""
        from src.providers.base import AbsenceResult, AbsenceVerdict

        with pytest.raises(ValueError, match="reservation"):
            AbsenceResult(AbsenceVerdict[verdict], reservation=_reservation())
        assert AbsenceResult(AbsenceVerdict[verdict]).reservation is None

    def test_hold_statuses(self):
        """alteration is the only hold status (R7)."""
        from src.providers.base import HOLD_STATUSES

        assert frozenset({"alteration"}) == HOLD_STATUSES


class TestProviderDefaults:
    """Base defaults reproduce today's behaviour for every provider."""

    def test_begin_cycle_is_noop(self):
        """begin_cycle accepts any listing set and does nothing."""
        provider = _minimal_provider()
        assert provider.begin_cycle(["L1", "L2"]) is None
        assert provider.begin_cycle([]) is None

    async def test_confirm_absent_confirms_every_id(self):
        """Absence retires, exactly as before spec 004."""
        from src.providers.base import AbsenceVerdict

        results = await _minimal_provider().confirm_absent("L1", ["R1", "R2"])
        assert set(results) == {"R1", "R2"}
        assert all(
            r.verdict is AbsenceVerdict.CONFIRMED_ABSENT and r.reservation is None
            for r in results.values()
        )

    async def test_confirm_absent_empty(self):
        """No candidates, no verdicts."""
        assert await _minimal_provider().confirm_absent("L1", []) == {}

    def test_retired_description_fields_empty(self):
        """No provider retires description fields by default."""
        assert _minimal_provider().retired_description_fields == {}
