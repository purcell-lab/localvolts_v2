"""Ask the LocalVolts API directly how fresh its data is, and why it is not.

Reads LOCALVOLTS_API_KEY and LOCALVOLTS_PARTNER_ID from the environment, and
LOCALVOLTS_NMI if set (default ``*``, every NMI on the account). Standard
library only; nothing is written, and the credentials are never printed.

    python scripts/check_api_freshness.py

Steps, each reported on its own so a failure says where it happened:

1. ``GET /version`` without credentials: is the host reachable at all.
2. ``GET /v2/customer/interval`` with them: the HTTP status, the Cloudflare
   ray id and server on an error, and an API error carried in a 200 body.
3. The rows: per direction counts by quality, the current interval, the
   distinct ``lastUpdate`` values and the age of the newest one.

Exit codes: 0 fresh, 3 reachable but stale, 1 request or API error, 2 not set up.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api2.localvolts.com"
# The same User-Agent the integration sends. urllib's default, Python-urllib,
# is answered with 403 by Cloudflare in front of the API before the key is read.
USER_AGENT = "Home Assistant"
# Matches SOURCE_STALE_AFTER in the integration.
STALE_AFTER_MINUTES = 15.0


def _get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes, float]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return response.status, dict(response.headers), body, time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(), time.monotonic() - started


def _report_http_error(status: int, headers: dict[str, str], body: bytes) -> None:
    lowered = {key.lower(): value for key, value in headers.items()}
    print(f"  HTTP {status}")
    for key in ("server", "cf-ray", "cf-mitigated", "content-type"):
        if key in lowered:
            print(f"  {key}: {lowered[key]}")
    snippet = body[:300].decode("utf-8", "replace").replace("\n", " ")
    print(f"  body: {snippet}")
    if status == 403 and "cloudflare" in lowered.get("server", "").lower():
        print("  -> refused by Cloudflare in front of the API, before the key was checked")
    elif status in (401, 403):
        print("  -> the API refused the credentials")
    elif status >= 500:
        print("  -> the API or its gateway failed; retry, and report if it persists")


def main() -> int:
    key = os.environ.get("LOCALVOLTS_API_KEY", "").strip()
    partner = os.environ.get("LOCALVOLTS_PARTNER_ID", "").strip()
    nmi = os.environ.get("LOCALVOLTS_NMI", "*")
    now = datetime.now(timezone.utc)
    print(f"checked at        {now.isoformat(timespec='seconds')}")
    print(f"api key           {'set' if key else 'NOT SET'}")
    print(f"partner id        {'set' if partner else 'NOT SET'}")
    print(f"nmi               {'*' if nmi == '*' else 'set'}")

    print("\n1. GET /version (no credentials)")
    try:
        status, headers, body, seconds = _get(f"{BASE_URL}/version", {})
    except OSError as exc:
        print(f"  unreachable: {type(exc).__name__}: {exc}")
        return 1
    if status != 200:
        _report_http_error(status, headers, body)
        return 1
    print(f"  HTTP 200 in {seconds:.2f}s: {body.decode('utf-8', 'replace').strip()}")

    if not key or not partner:
        print("\nSet LOCALVOLTS_API_KEY and LOCALVOLTS_PARTNER_ID to go further.")
        return 2

    # The same normalisation as api.normalize_api_key.
    if key.lower().startswith("apikey "):
        key = key.split(None, 1)[1]
    query = urllib.parse.urlencode({"NMI": nmi, "to": "1day"})
    print("\n2. GET /v2/customer/interval (with credentials)")
    try:
        status, headers, body, seconds = _get(
            f"{BASE_URL}/v2/customer/interval?{query}",
            {"Authorization": f"apikey {key}", "partner": partner},
        )
    except OSError as exc:
        print(f"  unreachable: {type(exc).__name__}: {exc}")
        return 1
    if status != 200:
        _report_http_error(status, headers, body)
        return 1
    try:
        payload = json.loads(body)
    except ValueError:
        print(f"  HTTP 200 but not JSON: {body[:300]!r}")
        return 1
    first = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(payload, list) or (isinstance(first, dict) and first.get("error")):
        # The API reports some failures, bad credentials among them, in a 200 body.
        print(f"  HTTP 200 carrying an API error: {first}")
        return 1
    print(f"  HTTP 200 in {seconds:.2f}s, {len(payload)} rows")

    print("\n3. Rows")
    rows = [row for row in payload if isinstance(row, dict)]
    for direction in ("Buy", "Sell"):
        own = [row for row in rows if row.get("direction") == direction]
        quality = dict(Counter(str(row.get("quality")) for row in own))
        current = next(
            (
                row
                for row in sorted(own, key=lambda r: str(r.get("intervalEnd")))
                if str(row.get("intervalEnd")) > now.strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            None,
        )
        print(f"  {direction:<4} rows {len(own):>3}  quality {quality}")
        if current:
            print(
                f"       current interval ends {current.get('intervalEnd')}"
                f"  quality {current.get('quality')}  lastUpdate {current.get('lastUpdate')}"
                f"  flexUp {current.get('flexUp')}"
            )

    stamps = Counter(str(row["lastUpdate"]) for row in rows if row.get("lastUpdate"))
    if not stamps:
        print("  no row carries lastUpdate")
        return 1
    print("  distinct lastUpdate values (newest first):")
    for stamp, count in sorted(stamps.items(), reverse=True)[:5]:
        print(f"    {stamp}  {count} rows")
    newest = max(
        datetime.fromisoformat(stamp.replace("Z", "+00:00")) for stamp in stamps
    )
    age = (now - newest).total_seconds() / 60.0
    print(f"\nnewest update     {newest.isoformat()}")
    print(f"age               {age:.1f} min")
    if age > STALE_AFTER_MINUTES:
        print(
            f"verdict           provider_stale: reachable, but LocalVolts has not "
            f"rebuilt the data for over {STALE_AFTER_MINUTES:.0f} min"
        )
        return 3
    print("verdict           ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
