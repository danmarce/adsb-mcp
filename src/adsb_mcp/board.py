"""The ambient FIDS board: `GET /board.json` (the data) and `GET /board` (one self-contained HTML page).

Airport-referenced on purpose: every distance and bearing is from the PUBLIC airport, aircraft positions
are left out, and nothing is site-relative — so the board carries no home-location PII and can be served
without the bearer (`ADSB_BOARD=open`, the default), like `/healthz`. `token` puts it behind the bearer,
`off` disables it. It needs the airport position; the arrivals / departures columns also need the field
elevation (the same heuristics as the recorder, from `passes.py`).

Client-side render: the server only answers JSON + one static file, and many displays polling share one
snapshot (`BOARD_MAX_AGE_S`), so N wall panels cost the feeder nothing extra.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import struct
import time
import zlib
from datetime import datetime
from importlib.resources import files
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .enrich import usual_route
from .feeder import FeederError, Snapshots, bearing_deg, haversine_km
from .passes import LANDED_KM, airport_frame, climbing_out, fresh_position, on_approach

BOARD_MAX_AGE_S = 5          # displays polling every few seconds share one snapshot at least this long
OVERHEAD_MAX = 12
RECENT_WINDOW_S = 3 * 3600
RECENT_MAX = 8
EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}
# The page, its data, and what "Add to Home Screen" needs (manifest + icons). Served at the root and under
# /board/ so the page's relative links work either way.
_FILES = ("board.json", "board.webmanifest", "board-icon-192.png", "board-icon-512.png")
PATHS = ("/board", "/board/", *(f"/{f}" for f in _FILES), *(f"/board/{f}" for f in _FILES))


def manifest(settings: Settings) -> dict[str, Any]:
    """Web app manifest: launched from the home screen, the board opens full screen with no browser bars
    (Android Chrome installs it as an app over https; over http it's a plain shortcut)."""
    name = f"{settings.airport_icao} flight board" if settings.airport_icao else "Flight board"
    return {"name": name, "short_name": settings.airport_icao or "Board", "start_url": "board", "scope": "./",
            "display": "fullscreen", "display_override": ["fullscreen", "standalone"],
            "orientation": "any", "background_color": "#08090b", "theme_color": "#08090b",
            "icons": [{"src": f"board-icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any"}
                      for n in (192, 512)]}


@functools.lru_cache(maxsize=2)
def icon_png(size: int) -> bytes:
    """A tiny FIDS-board icon (amber header bar + three rows), drawn here so the repo holds no binaries."""
    bg, amber, dim = b"\x08\x09\x0b", b"\xf1\xc2\x32", b"\x8a\x7a\x45"
    m = 0.12
    bars = [(0.14, 0.30, m, 1 - m, amber)]
    for y0 in (0.40, 0.58, 0.76):
        bars += [(y0, y0 + 0.10, m, 0.46, amber), (y0, y0 + 0.10, 0.54, 1 - m, dim)]
    raw = bytearray()
    for y in range(size):
        fy = (y + 0.5) / size
        raw.append(0)                                   # PNG filter: none
        row = [bg] * size
        for y0, y1, x0, x1, colour in bars:
            if y0 <= fy < y1:
                for x in range(int(x0 * size), int(x1 * size)):
                    row[x] = colour
        raw += b"".join(row)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _store(settings: Settings) -> sqlite3.Connection | None:
    if not settings.db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{settings.db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def build_board(settings: Settings, snap: dict[str, Any], conn: sqlite3.Connection | None,
                now: float | None = None) -> dict[str, Any]:
    """The board document. Pure: snapshot (+ the store, for held arrivals and recent passes) -> dict."""
    now = float(now if now is not None else snap.get("now") or time.time())
    tz = _tz(settings)
    lat0, lon0 = settings.airport

    def local(epoch: float | None) -> str | None:
        return None if epoch is None else datetime.fromtimestamp(epoch, tz).isoformat(timespec="seconds")

    def ident(a: dict[str, Any]) -> dict[str, Any]:
        return {"hex": a["hex"], "callsign": a["callsign"], "type": a["type"], "registration": a["registration"]}

    overhead, arrivals, departures, live_hex = [], [], [], set()
    for a in snap["aircraft"]:
        if not fresh_position(a):
            continue
        d = haversine_km(lat0, lon0, a["lat"], a["lon"])
        d_ap, agl = airport_frame(a, settings)
        vr = a["vertical_rate_fpm"]
        phase = ("approaching" if on_approach(d_ap, agl, vr, settings)
                 else "departing" if climbing_out(d_ap, agl, vr, settings) else None)
        live_hex.add(a["hex"])
        if a["on_ground"]:
            continue
        emergency = a["emergency"] or (a["squawk"] if a["squawk"] in EMERGENCY_SQUAWKS else None)
        if d <= settings.pass_radius_km:
            overhead.append({**ident(a), "altitude_ft": a["altitude_ft"], "ground_speed_kt": a["ground_speed_kt"],
                             "track_deg": a["track_deg"], "vertical_rate_fpm": vr, "category": a["category"],
                             "distance_km": round(d, 1), "bearing_deg": round(bearing_deg(lat0, lon0, a["lat"], a["lon"])),
                             "phase": phase, "squawk": a["squawk"], "emergency": emergency})
        if phase == "approaching":
            gs = a["ground_speed_kt"]
            eta = round(d / (gs * 1.852) * 60) if gs and gs > 50 else None
            arrivals.append({**ident(a), "status": "approaching", "distance_km": round(d, 1),
                             "altitude_ft": a["altitude_ft"], "ground_speed_kt": gs, "eta_min": eta})
        elif phase == "departing":
            departures.append({**ident(a), "status": "departing", "distance_km": round(d, 1),
                               "altitude_ft": a["altitude_ft"], "vertical_rate_fpm": vr})
    if conn is not None:
        # Where each callsign usually flies (learned from enriched passes) — only when it's consistent.
        cache: dict[str, Any] = {}
        for r in (*overhead, *arrivals, *departures):
            cs = r.get("callsign")
            if cs not in cache:
                u = usual_route(conn, cs)
                cache[cs] = ({"origin": u["origin"], "destination": u["destination"], "seen": u["seen"]}
                             if u and u["seen"] >= 2 and u["seen"] / u["of"] >= 0.6 else None)
            if cache[cs]:
                r["usual_route"] = cache[cs]
    overhead.sort(key=lambda r: r["distance_km"])
    arrivals.sort(key=lambda r: (r["eta_min"] is None, r["eta_min"] or 0, r["distance_km"]))
    departures.sort(key=lambda r: r["distance_km"])

    recent: list[dict[str, Any]] = []
    if conn is not None:
        # Held open by the recorder: lost on approach (presumably landing below coverage), or seen landing.
        for r in conn.execute("SELECT * FROM passes WHERE open = 1 AND (on_approach = 1 OR landed_at IS NOT NULL) "
                              "ORDER BY last_seen DESC"):
            if r["landed_at"] is not None:
                arrivals.append({"hex": r["hex"], "callsign": r["callsign"], "type": r["type"],
                                 "registration": r["reg"], "status": "landed", "time": local(r["landed_at"])})
            elif r["hex"] not in live_hex:
                arrivals.append({"hex": r["hex"], "callsign": r["callsign"], "type": r["type"],
                                 "registration": r["reg"], "status": "arrival_presumed",
                                 "lost_min": max(0, round((now - r["last_seen"]) / 60))})
        for r in conn.execute("SELECT * FROM passes WHERE open = 0 AND last_seen >= ? ORDER BY last_seen DESC LIMIT ?",
                              (now - RECENT_WINDOW_S, RECENT_MAX)):
            recent.append({"hex": r["hex"], "callsign": r["callsign"], "type": r["type"], "registration": r["reg"],
                           "outcome": r["outcome"], "go_arounds": r["go_arounds"],
                           "origin": r["origin"], "destination": r["destination"],
                           "time": local(r["landed_at"] or r["last_seen"]), "min_alt_ft": r["min_alt_ft"]})

    return {"status": "ok", "source": "adsb-board", "now": local(now), "tz": tz.key,
            "airport": {"icao": settings.airport_icao, "name": settings.airport_name},
            "arrivals_enabled": settings.arrivals_enabled, "radius_km": settings.pass_radius_km,
            "recording": conn is not None, "overhead": overhead[:OVERHEAD_MAX], "arrivals": arrivals,
            "departures": departures, "recent": recent}


class Board:
    """ASGI handler for the board paths (mounted by `BearerAuth` in front of the MCP app)."""

    def __init__(self, settings: Settings, snapshots: Snapshots) -> None:
        self.s = settings
        self.snapshots = snapshots
        self.public = settings.board == "open"

    async def document(self) -> tuple[int, dict[str, Any]]:
        if self.s.airport is None:
            return 200, {"status": "refused", "reason": "the board is airport-referenced and no airport is set",
                         "advice": "set ADSB_AIRPORT_ICAO / ADSB_AIRPORT_LAT / ADSB_AIRPORT_LON "
                                   "(+ ADSB_AIRPORT_ELEV_FT for arrivals and departures)"}
        try:
            snap, _ = await self.snapshots.get(min_age_s=BOARD_MAX_AGE_S)
        except FeederError as e:
            return 502, {"status": "error", "kind": "feeder", "detail": str(e)}
        conn = _store(self.s)
        try:
            return 200, build_board(self.s, snap, conn)
        finally:
            if conn is not None:
                conn.close()

    async def __call__(self, scope, receive, send) -> None:
        path, status, cache = scope["path"], 200, b"no-store"
        if path.endswith("board.json"):
            status, doc = await self.document()
            body, ctype = json.dumps(doc, ensure_ascii=False).encode(), b"application/json; charset=utf-8"
        elif path.endswith(".webmanifest"):
            body, ctype = json.dumps(manifest(self.s)).encode(), b"application/manifest+json"
        elif path.endswith(".png"):
            body, ctype, cache = icon_png(512 if "512" in path else 192), b"image/png", b"max-age=86400"
        else:
            body, ctype = files("adsb_mcp").joinpath("board.html").read_bytes(), b"text/html; charset=utf-8"
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()),
                                (b"cache-control", cache)]})
        await send({"type": "http.response.body", "body": body})
