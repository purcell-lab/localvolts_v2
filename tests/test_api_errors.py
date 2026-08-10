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
