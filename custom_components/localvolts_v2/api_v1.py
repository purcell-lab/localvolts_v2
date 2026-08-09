"""Async client for the supplementary LocalVolts v1 interval API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiohttp

from .api import LocalVoltsApiError, LocalVoltsAuthError, normalize_api_key
from .const import API_BASE_URL

# The v1 payload is served by the v2 host under the v1 path, and the v2
# credential authenticates it. Checked on 2026-08-09 over a 23 hour window:
# api2.localvolts.com/v1 with the v2 credential and api.localvolts.com/v1 with a
# separate v1 credential both returned 277 records carrying the same 49 fields,
# and every field of every record matched except lastUpdate, a response stamp
# that differed by 7 seconds because the two calls were not simultaneous.
#
# So a second credential pair buys nothing, and the legacy host is kept only as
# an override for anyone who needs to point at it.
V1_API_BASE_URL = "https://api.localvolts.com"
V1_API_INTERVAL_PATH = "/v1/customer/interval"


def _as_utc_stamp(moment: datetime) -> str:
    """Render an instant the way v1 accepts it, as a UTC Z timestamp."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LocalVoltsV1Client:
    """Read LocalVolts v1 settled interval data using the primary credentials.

    v1 is supplementary only. The supplied reverse-engineered comparison notes
    identify v2 as the invoice-total source, while selected v1 fields remain
    useful for rate reconciliation.
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

    async def fetch_interval(
        self,
        nmi: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch v1 interval data without affecting v2 operation on errors.

        Takes instants, not dates. v1 refuses any window of 24 hours or wider,
        including a bare pair of dates one day apart, so the caller has to name
        the exact endpoints of the window it wants. Instants are sent as UTC.
        """
        params = {"NMI": nmi}
        if start is not None:
            params["from"] = _as_utc_stamp(start)
        if end is not None:
            params["to"] = _as_utc_stamp(end)

        async with self._session.get(
            f"{self._base_url}{V1_API_INTERVAL_PATH}",
            params=params,
            headers=self._headers,
        ) as response:
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as exc:
                raise LocalVoltsApiError(
                    f"LocalVolts v1 returned non-JSON response ({response.status})"
                ) from exc

            # v1 standard failures were observed as HTTP errors. This defensive
            # check also handles an error object in a successful response body.
            candidate: Any = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(candidate, dict) and candidate.get("error"):
                error = str(candidate["error"])
                if error in {"Not Authenticated", "Not Authorised"}:
                    raise LocalVoltsAuthError(error)
                message = candidate.get("message")
                raise LocalVoltsApiError(
                    f"{error}: {message}" if message else error
                )
            if response.status != 200:
                raise LocalVoltsApiError(
                    f"LocalVolts v1 request failed with HTTP {response.status}"
                )
            if not isinstance(payload, list):
                raise LocalVoltsApiError("LocalVolts v1 interval response was not a JSON array")
            if not all(isinstance(record, dict) for record in payload):
                raise LocalVoltsApiError("LocalVolts v1 interval response contained an invalid record")
            return payload
