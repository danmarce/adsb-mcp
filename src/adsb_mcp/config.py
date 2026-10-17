"""Runtime settings, all from environment variables.

No API key anywhere — the source is your OWN ADS-B receiver's `aircraft.json` on the LAN (keyless). The
only real secret posture here is the **receiver's coordinates**, which are home-location PII: they come
from the environment (sourced from 1Password at deploy), never hardcoded in the repo. Leaving them unset
is fine — distance/overhead degrade gracefully."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# The feeder's live aircraft.json. adsb.im / tar1090 / readsb serve it here by default. The lab's feeder
# (hosts/tatiana-wisla.md) exposes it on the IoT VLAN; override via env for another receiver.
DEFAULT_ADSB_URL = "http://10.25.1.90:8080/data/aircraft.json"


def _opt_float(name: str) -> float | None:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    adsb_url: str = field(default_factory=lambda: os.environ.get("ADSB_URL", DEFAULT_ADSB_URL))
    http_timeout: float = field(default_factory=lambda: float(os.environ.get("ADSB_HTTP_TIMEOUT", "10")))
    # Receiver location — HOME-LOCATION PII. Set via env (from 1Password) to enable nearest-first "overhead"
    # with real distances/bearings. Unset = overhead still lists aircraft, just without distance.
    site_lat: float | None = field(default_factory=lambda: _opt_float("ADSB_SITE_LAT"))
    site_lon: float | None = field(default_factory=lambda: _opt_float("ADSB_SITE_LON"))
    # Bearer token required on streamable-http when set (sent as an Authorization header).
    http_token: str | None = field(default_factory=lambda: os.environ.get("ADSB_MCP_TOKEN") or None)
