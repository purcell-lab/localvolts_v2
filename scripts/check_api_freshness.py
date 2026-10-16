"""Ask the LocalVolts API directly how fresh its data is.

Reads LOCALVOLTS_API_KEY and LOCALVOLTS_PARTNER_ID from the environment, and
LOCALVOLTS_NMI if set (default ``*``, every NMI on the account). Prints the
newest ``lastUpdate`` on the rows and its age, so a stall upstream can be told
apart from one in Home Assistant. Standard library only; nothing is written.

    python scripts/check_api_freshness.py
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://api2.localvolts.com"


def main() -> int:
    key = os.environ.get("LOCALVOLTS_API_KEY")
    partner = os.environ.get("LOCALVOLTS_PARTNER_ID")
    if not key or not partner:
        print("Set LOCALVOLTS_API_KEY and LOCALVOLTS_PARTNER_ID first.", file=sys.stderr)
        return 2
    # The same normalisation as api.normalize_api_key.
    key = key.strip()
    if key.lower().startswith("apikey "):
        key = key.split(None, 1)[1]
    authorization = f"apikey {key}"
    query = urllib.parse.urlencode(
        {"NMI": os.environ.get("LOCALVOLTS_NMI", "*"), "to": "1day"}
    )
    request = urllib.request.Request(
        f"{BASE_URL}/v2/customer/interval?{query}",
        # The same headers the integration sends. Without a User-Agent, urllib
        # announces itself as Python-urllib, which Cloudflare in front of the
        # API answers with 403 even when the key is good.
        headers={
            "Authorization": authorization,
            "partner": partner.strip(),
            "User-Agent": "Home Assistant",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    first = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(payload, list) or (isinstance(first, dict) and first.get("error")):
        print(f"API error: {first}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    stamps = sorted(
        {
            datetime.fromisoformat(str(row["lastUpdate"]).replace("Z", "+00:00"))
            for row in payload
            if isinstance(row, dict) and row.get("lastUpdate")
        }
    )
    print(f"checked at      {now.isoformat(timespec='seconds')}")
    print(f"rows            {len(payload)}")
    if not stamps:
        print("no row carries lastUpdate")
        return 1
    newest = stamps[-1]
    print(f"newest update   {newest.isoformat()}")
    print(f"age             {(now - newest).total_seconds() / 60:.1f} min")
    print(f"distinct stamps {len(stamps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
