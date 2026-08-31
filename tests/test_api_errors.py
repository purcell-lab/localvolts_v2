"""Tests for the mapping from v2 error bodies to exception types.

These sit at the client level on purpose. The config flow tests inject the
exception classes directly, so they prove the flow reacts correctly but prove
nothing about which exception a given response body produces. A mutation that
collapsed both error strings back onto one class passed the entire config flow
suite untouched, because that mapping is never exercised there. This file is
the missing half.

The bodies below were taken from the live v2 API on 2026-08-10, not invented:

  invalid key and partner id  ->  HTTP 200  [{"error": "Not Authenticated"}]
  NMI outside the key's scope ->  HTTP 200  [{"error": "Not Authorised"}]
"""
from __future__ import annotations

import pytest

from custom_components.localvolts_v2.api import (
    LocalVoltsApiError,
    LocalVoltsAuthError,
    LocalVoltsClient,
    LocalVoltsCredentialError,
    LocalVoltsNmiScopeError,
)


def test_not_authenticated_body_means_the_credentials_were_refused():
    """The v1-key-against-v2 case must be its own exception type."""
    with pytest.raises(LocalVoltsCredentialError):
        LocalVoltsClient._raise_for_payload_error([{"error": "Not Authenticated"}])


def test_not_authorised_body_means_the_nmi_is_out_of_scope():
    """The opposite case must be a different exception type."""
    with pytest.raises(LocalVoltsNmiScopeError):
        LocalVoltsClient._raise_for_payload_error([{"error": "Not Authorised"}])


def test_the_two_bodies_do_not_produce_the_same_exception():
    """Guards directly against collapsing the two back into one.

    Written as an explicit inequality because a test that only asserts each
    body raises "an auth error" would pass with the distinction removed, which
    is the mutation that slipped through.
    """
    with pytest.raises(LocalVoltsAuthError) as refused:
        LocalVoltsClient._raise_for_payload_error([{"error": "Not Authenticated"}])
    with pytest.raises(LocalVoltsAuthError) as out_of_scope:
        LocalVoltsClient._raise_for_payload_error([{"error": "Not Authorised"}])

    assert type(refused.value) is not type(out_of_scope.value)


def test_both_remain_catchable_as_one_auth_error():
    """Existing callers that catch the base class keep working."""
    for body in ({"error": "Not Authenticated"}, {"error": "Not Authorised"}):
        with pytest.raises(LocalVoltsAuthError):
            LocalVoltsClient._raise_for_payload_error([body])


def test_an_unrecognised_error_body_is_still_a_plain_api_error():
    """Only the two known strings get special treatment."""
    with pytest.raises(LocalVoltsApiError) as exc:
        LocalVoltsClient._raise_for_payload_error([{"error": "Teapot"}])

    assert not isinstance(exc.value, LocalVoltsAuthError)


def test_a_clean_body_raises_nothing():
    """A normal interval array must pass straight through."""
    LocalVoltsClient._raise_for_payload_error([{"intervalEnd": "2026-08-10T00:05:00Z"}])
    LocalVoltsClient._raise_for_payload_error([])


def test_window_bounds_are_rendered_as_iso_8601_utc_timestamps():
    """A datetime bound is sent in the form API Guide 0.9.8 section 3.2 asks for.

    The guide's example is 2023-05-15T02:40:00Z. A naive datetime is read as UTC
    rather than rejected, and an offset aware one is converted, so a caller
    cannot accidentally send a local wall clock time as though it were UTC.
    """
    from datetime import date, datetime, timedelta, timezone

    from custom_components.localvolts_v2.api import LocalVoltsClient

    render = LocalVoltsClient._format_bound

    assert render(datetime(2026, 8, 31, 7, 30, tzinfo=timezone(timedelta(hours=10)))) == (
        "2026-08-30T21:30:00Z"
    )
    assert render(datetime(2026, 8, 30, 21, 30, tzinfo=timezone.utc)) == "2026-08-30T21:30:00Z"
    assert render(datetime(2026, 8, 30, 21, 30)) == "2026-08-30T21:30:00Z"
    # A bare date still goes out as a bare date, which the API resolves at site
    # local midnight. Callers asking for whole days rely on that.
    assert render(date(2026, 8, 31)) == "2026-08-31"
