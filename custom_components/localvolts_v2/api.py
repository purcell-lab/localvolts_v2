"""Async client for the reverse-engineered LocalVolts v2 API."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    API_INTERVAL_PATH,
    API_MARKET_STATS_PATH,
    API_VERSION_PATH,
)


class LocalVoltsApiError(Exception):
    """Raised when LocalVolts returns an API-level error."""


class LocalVoltsNoDataError(LocalVoltsApiError):
    """Raised when LocalVolts authenticates but returns no interval records.

    Separated from a plain API error because the remedy is different. Nothing
    is malformed and nothing was rejected; the credentials are accepted and the
    feed is simply empty, which points at the account rather than at
    connectivity.
    """


class LocalVoltsAuthError(LocalVoltsApiError):
    """Raised when supplied LocalVolts credentials or NMI scope are invalid.

    Kept as the base of the two specific cases below so that callers which only
    need to know that authorization failed can still catch one exception.
    """


class LocalVoltsCredentialError(LocalVoltsAuthError):
    """Raised when v2 does not accept the API key and partner ID at all.

    v2 reports this as ``Not Authenticated`` in a JSON array body with HTTP 200,
    not as a 401, which is why it is easy to mistake for a connectivity fault.

    Measured on 2026-08-10: a deliberately invalid key and partner ID against
    ``/v2/customer/interval`` returned ``[{"error": "Not Authenticated"}]`` with
    HTTP 200.

    The common cause is a v1 key. v1 and v2 issue separate credentials, and a
    v1 key presented to v2 is simply not recognised, so the remedy is to request
    v2 credentials rather than to re-check the NMI.
    """


class LocalVoltsNmiScopeError(LocalVoltsAuthError):
    """Raised when the credentials are valid for v2 but the NMI is not in scope.

    v2 reports this as ``Not Authorised``, again in a JSON array body with HTTP
    200. Measured on 2026-08-10 by requesting an NMI the key does not cover.

    The distinction from LocalVoltsCredentialError matters because the remedy is
    the opposite: the credentials are correct and the NMI is wrong.
    """


def normalize_api_key(api_key: str) -> str:
    """Ensure the authorization header value includes the required prefix."""
    value = api_key.strip()
    if value.lower().startswith("apikey "):
        return "apikey " + value.split(None, 1)[1]
    return f"apikey {value}"


def normalize_nmi(nmi: str) -> str:
    """Remove all whitespace from an NMI.

    A National Metering Identifier is often written with its checksum digit
    separated, for example "1234567890 8". The API tolerates the space and
    answers for the base NMI, but the raw value would otherwise leak into
    entity ids, the device name and the chart title.
    """
    return "".join(nmi.split())


def parse_interval_end(value: str) -> datetime:
    """Parse an API UTC timestamp into an aware datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class LocalVoltsClient:
    """Minimal HTTP client for documented LocalVolts v2 endpoints.

    The endpoint behavior is based on the supplied reverse-engineered API
    specification. In particular, authorization failures can be returned as a
    JSON array with HTTP 200, so every response body is inspected for errors.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        partner_id: str,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._authorization = normalize_api_key(api_key)
        self._partner_id = partner_id.strip()
        self._base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization,
            "partner": self._partner_id,
            "User-Agent": "Home Assistant",
        }

    @staticmethod
    def _error_from_payload(payload: Any) -> str | None:
        """Return an API error string when a response body contains one."""
        candidate: Any = payload
        if isinstance(payload, list) and payload:
            candidate = payload[0]
        if isinstance(candidate, dict) and candidate.get("error"):
            message = candidate.get("message")
            if message:
                return f"{candidate['error']}: {message}"
            return str(candidate["error"])
        return None

    @staticmethod
    def _raise_for_payload_error(payload: Any) -> None:
        candidate: Any = payload[0] if isinstance(payload, list) and payload else payload
        raw_error = (
            str(candidate["error"])
            if isinstance(candidate, dict) and candidate.get("error")
            else None
        )
        # These two are both reported with HTTP 200 and differ only by this
        # string, yet they call for opposite remedies, so they are kept apart
        # all the way out to the config flow message.
        if raw_error == "Not Authenticated":
            raise LocalVoltsCredentialError(raw_error)
        if raw_error == "Not Authorised":
            raise LocalVoltsNmiScopeError(raw_error)
        error = LocalVoltsClient._error_from_payload(payload)
        if error is None:
            return
        raise LocalVoltsApiError(error)

    async def _async_get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers = self._headers if authenticated else {"User-Agent": "Home Assistant"}
        url = f"{self._base_url}{path}"
        async with self._session.get(url, params=params, headers=headers) as response:
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as exc:
                text = await response.text()
                raise LocalVoltsApiError(
                    f"LocalVolts returned non-JSON response ({response.status}): {text[:200]}"
                ) from exc

            # Inspect this first because authentication errors are reported in a
            # successful HTTP response body by this API.
            self._raise_for_payload_error(payload)
            if response.status != 200:
                raise LocalVoltsApiError(
                    f"LocalVolts request failed with HTTP {response.status}"
                )
            return payload

    async def fetch_interval(
        self,
        nmi: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch interval records for an NMI and validate their UTC timestamps."""
        params = {"NMI": nmi}
        if from_date is not None:
            params["from"] = from_date.isoformat()
        if to_date is not None:
            params["to"] = to_date.isoformat()

        payload = await self._async_get_json(API_INTERVAL_PATH, params=params)
        if not isinstance(payload, list):
            raise LocalVoltsApiError("LocalVolts interval response was not a JSON array")

        records: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise LocalVoltsApiError("LocalVolts interval response contained an invalid record")
            interval_end = item.get("intervalEnd")
            if interval_end is not None:
                try:
                    parse_interval_end(str(interval_end))
                except (TypeError, ValueError) as exc:
                    raise LocalVoltsApiError(
                        f"LocalVolts returned invalid intervalEnd: {interval_end!r}"
                    ) from exc
            records.append(item)
        return records

    async def fetch_market_stats(self) -> dict[str, Any]:
        """Fetch the real-time, market-wide P2P statistics snapshot."""
        payload = await self._async_get_json(API_MARKET_STATS_PATH)
        if not isinstance(payload, dict):
            raise LocalVoltsApiError("LocalVolts market statistics response was not an object")
        result = payload.get("objResult", payload)
        if not isinstance(result, dict):
            raise LocalVoltsApiError("LocalVolts market statistics result was not an object")
        return result

    async def fetch_version(self) -> dict[str, Any]:
        """Fetch the API version endpoint for config-flow connectivity checks."""
        payload = await self._async_get_json(API_VERSION_PATH, authenticated=False)
        if not isinstance(payload, dict):
            raise LocalVoltsApiError("LocalVolts version response was not an object")
        return payload
