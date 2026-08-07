"""Async client for the supplementary LocalVolts v1 interval API."""
from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp

from .api import LocalVoltsApiError, LocalVoltsAuthError, normalize_api_key

V1_API_BASE_URL = "https://api.localvolts.com"
V1_API_INTERVAL_PATH = "/v1/customer/interval"


class LocalVoltsV1Client:
    """Read LocalVolts v1 settled interval data using separate v1 credentials.

    v1 is supplementary only. The supplied reverse-engineered comparison notes
    identify v2 as the invoice-total source, while selected v1 fields remain
    useful for rate reconciliation.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        partner_id: str,
        base_url: str = V1_API_BASE_URL,
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
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch v1 interval data without affecting v2 operation on errors."""
        params = {"NMI": nmi}
        if from_date is not None:
            params["from"] = from_date.isoformat()
        if to_date is not None:
            params["to"] = to_date.isoformat()

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
