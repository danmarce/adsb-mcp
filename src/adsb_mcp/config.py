"""Runtime settings, all from environment variables.

No API key anywhere — the source is your OWN ADS-B receiver's `aircraft.json` on the LAN (keyless). The
only real secret posture here is the **receiver's coordinates**, which are home-location PII: they come
from the environment (sourced from a secret store at deploy), never hardcoded in the repo. Leaving them
unset is fine — distance/overhead degrade gracefully, and a nearby airport can stand in as an approximate
(public, non-PII) reference point."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The feeder's live aircraft.json. adsb.im / tar1090 / readsb serve it at this path by default; set
# ADSB_URL to point at your receiver (deployment-specific hosts live in the deploy config, not here).
DEFAULT_ADSB_URL = "http://localhost:8080/data/aircraft.json"


def _opt_float(name: str) -> float | None:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _float(name: str, default: float) -> float:
    v = _opt_float(name)
    return default if v is None else v


@dataclass(frozen=True)
class Settings:
    adsb_url: str = field(default_factory=lambda: os.environ.get("ADSB_URL", DEFAULT_ADSB_URL))
    http_timeout: float = field(default_factory=lambda: _float("ADSB_HTTP_TIMEOUT", 10))
    # Receiver location — HOME-LOCATION PII. Set via env (from a secret store) to enable nearest-first
    # "overhead" with real distances/bearings. Never echoed in tool results.
    site_lat: float | None = field(default_factory=lambda: _opt_float("ADSB_SITE_LAT"))
    site_lon: float | None = field(default_factory=lambda: _opt_float("ADSB_SITE_LON"))
    # The nearby airport: an approximate, public reference point when the site isn't given, and the target
    # for arrival / landing / go-around tracking (which also needs the field elevation).
    airport_icao: str | None = field(default_factory=lambda: (os.environ.get("ADSB_AIRPORT_ICAO") or "").strip().upper() or None)
    airport_lat: float | None = field(default_factory=lambda: _opt_float("ADSB_AIRPORT_LAT"))
    airport_lon: float | None = field(default_factory=lambda: _opt_float("ADSB_AIRPORT_LON"))
    airport_elev_ft: float | None = field(default_factory=lambda: _opt_float("ADSB_AIRPORT_ELEV_FT"))

    # --- historical tier (the recorder) ---
    # >0 makes `serve` poll the feeder in the background and record passes. 0 = off (pure live tier).
    poll_seconds: float = field(default_factory=lambda: _float("ADSB_POLL_SECONDS", 0))
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("ADSB_DB", "data/adsb.db")))
    # A pass = an airborne aircraft within this radius of the reference point (or on approach to the airport).
    pass_radius_km: float = field(default_factory=lambda: _float("ADSB_PASS_RADIUS_KM", 50))
    # Close a pass once the aircraft has been gone (or out of the zone) this long.
    pass_grace_s: float = field(default_factory=lambda: _float("ADSB_PASS_GRACE_SECONDS", 90))
    # ...but an aircraft lost while on approach is kept open this long: it's presumably landing below coverage.
    arrival_grace_s: float = field(default_factory=lambda: _float("ADSB_ARRIVAL_GRACE_SECONDS", 900))
    approach_km: float = field(default_factory=lambda: _float("ADSB_APPROACH_KM", 30))
    # IANA zone for hour-of-day stats and the local times in history results.
    tz: str = field(default_factory=lambda: os.environ.get("ADSB_TZ") or "UTC")

    # Bearer token required on streamable-http when set (sent as an Authorization header).
    http_token: str | None = field(default_factory=lambda: os.environ.get("ADSB_MCP_TOKEN") or None)

    @property
    def site(self) -> tuple[float, float] | None:
        return (self.site_lat, self.site_lon) if self.site_lat is not None and self.site_lon is not None else None

    @property
    def airport(self) -> tuple[float, float] | None:
        return (self.airport_lat, self.airport_lon) if self.airport_lat is not None and self.airport_lon is not None else None

    @property
    def reference(self) -> tuple[str, tuple[float, float]] | None:
        """The point distances and passes are measured from: the exact site, else the airport, else none."""
        if self.site:
            return "site", self.site
        if self.airport:
            return "airport", self.airport
        return None

    @property
    def arrivals_enabled(self) -> bool:
        return self.airport is not None and self.airport_elev_ft is not None
