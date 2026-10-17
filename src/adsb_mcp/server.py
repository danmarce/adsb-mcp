"""MCP server: local ADS-B flight data as tools, over your own receiver's aircraft.json.

Design contract:
  * Stateless proxy: every call fetches the live snapshot; no DB, no accumulation (that's a future tier).
    Inputs pass through verbatim; the observer coordinates used are echoed back.
  * ok / refused / error status on every result — branch on `status`, never on prose.
  * The receiver location is home-location PII: it comes from env (1Password at deploy), never the repo.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent, ToolAnnotations

from .config import Settings
from .feeder import FeederClient, FeederError, bearing_deg, haversine_km

INSTRUCTIONS = """\
Live aircraft near a local ADS-B receiver (its keyless aircraft.json — your OWN data). Tools:
aircraft_overhead (what's in range now, nearest first), aircraft_near (within a radius of a given point),
find_aircraft (by callsign / hex / registration). The star query is "¿qué avión es ese?" — what's flying
overhead right now.

Results are structured DATA (hex, callsign, registration, type, altitude_ft, ground_speed_kt, track_deg,
lat/lon, distance_km + bearing_deg when the receiver location is configured, seen_s). Answer in the user's
language; cite that it's live local ADS-B. Altitudes are feet, speed knots, track/bearing degrees.

ADS-B carries no route/airline/registration-owner — only what the aircraft transmits. Enrichment
(origin/destination, airline, schedule) is a separate optional keyed tier, not these tools.

Every result has a `status`: "ok", "refused" (caller-fixable input, with `advice`), or "error" (the
receiver is unreachable — `kind`+`detail`; not something a reworded query fixes). Branch on `status`.
"""


def _read_hints(title: str) -> ToolAnnotations:
    # Pure reads: non-destructive, idempotent (same snapshot -> same answer). openWorld = FALSE: it talks
    # only to the lab's OWN LAN receiver (a fixed, closed dependency), not the public web or a third party.
    # A future AeroAPI/OpenSky enrichment tool WOULD be openWorld=true.
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                           openWorldHint=False)


def _text(payload: dict[str, Any]) -> list[Any]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def _refused(reason: str, **extra: Any) -> list[Any]:
    """A caller-fixable input decision (not an error): adjust the request, don't retry unchanged."""
    return _text({"status": "refused", "reason": reason, **extra})


def _error(kind: str, detail: str) -> list[Any]:
    """A genuine fault (receiver down / bad body) — infra, not policy; a prompt change won't fix it."""
    return _text({"status": "error", "kind": kind, "detail": detail})


def build_server(settings: Settings) -> MCPServer:
    mcp = MCPServer(name="adsb", instructions=INSTRUCTIONS)
    feeder = FeederClient(settings)
    site = (settings.site_lat, settings.site_lon) if settings.site_lat is not None and settings.site_lon is not None else None

    def _positioned(aircraft: list[dict], include_grounded: bool) -> list[dict]:
        return [a for a in aircraft if a["lat"] is not None and a["lon"] is not None
                and (include_grounded or not a["on_ground"])]

    @mcp.tool(title="Aircraft overhead", annotations=_read_hints("Aircraft overhead"), structured_output=False)
    async def aircraft_overhead(max_results: int = 10, include_grounded: bool = False) -> list[Any]:
        """Aircraft currently in range of the receiver, nearest first. The "what's that plane overhead
        right now" query. With the receiver location configured, each result carries distance_km + bearing;
        without it, results are listed newest-position-first and distance is null (set ADSB_SITE_LAT/LON).

        Args:
            max_results: cap on aircraft returned (default 10).
            include_grounded: include aircraft on the ground (default False — only airborne).
        """
        try:
            snap = await feeder.fetch()
        except FeederError as e:
            return _error("feeder", str(e))
        acs = _positioned(snap["aircraft"], include_grounded)
        if site:
            for a in acs:
                a["distance_km"] = round(haversine_km(site[0], site[1], a["lat"], a["lon"]), 1)
                a["bearing_deg"] = round(bearing_deg(site[0], site[1], a["lat"], a["lon"]))
            acs.sort(key=lambda a: a["distance_km"])
        else:
            acs.sort(key=lambda a: (a["seen_pos_s"] is None, a["seen_pos_s"] or 0))
        out = {"status": "ok", "source": "adsb", "observer": {"lat": site[0], "lon": site[1]} if site else None,
               "site_configured": site is not None, "count": len(acs), "aircraft": acs[:max(1, max_results)]}
        if not site:
            out["note"] = "receiver location not set (ADSB_SITE_LAT/LON) — no distances; listed newest-position-first."
        return _text(out)

    @mcp.tool(title="Aircraft near a point", annotations=_read_hints("Aircraft near a point"), structured_output=False)
    async def aircraft_near(latitude: float, longitude: float, radius_km: float = 50.0,
                            max_results: int = 10, include_grounded: bool = False) -> list[Any]:
        """Aircraft currently within `radius_km` of a given point (lat/lon you supply — e.g. a city), nearest
        first. Unlike aircraft_overhead this needs no receiver location: distance is measured from the point
        you pass.

        Args:
            latitude/longitude: the centre point to search around.
            radius_km: search radius in kilometres (default 50).
            max_results: cap on aircraft returned (default 10).
            include_grounded: include aircraft on the ground (default False).
        """
        try:
            snap = await feeder.fetch()
        except FeederError as e:
            return _error("feeder", str(e))
        acs = []
        for a in _positioned(snap["aircraft"], include_grounded):
            dist = round(haversine_km(latitude, longitude, a["lat"], a["lon"]), 1)
            if dist <= radius_km:
                a = {**a, "distance_km": dist, "bearing_deg": round(bearing_deg(latitude, longitude, a["lat"], a["lon"]))}
                acs.append(a)
        acs.sort(key=lambda a: a["distance_km"])
        return _text({"status": "ok", "source": "adsb", "center": {"lat": latitude, "lon": longitude},
                      "radius_km": radius_km, "count": len(acs), "aircraft": acs[:max(1, max_results)]})

    @mcp.tool(title="Find aircraft", annotations=_read_hints("Find aircraft"), structured_output=False)
    async def find_aircraft(query: str, max_results: int = 10) -> list[Any]:
        """Find aircraft currently in range by a partial match on callsign, ICAO hex, or registration
        (case-insensitive). Returns matches even if they have no position yet.

        Args:
            query: text to match against callsign / hex / registration (e.g. "LAN", "e80", "CC-B").
            max_results: cap on matches returned (default 10).
        """
        q = query.strip().lower()
        if not q:
            return _refused("empty query", advice="pass a callsign, hex, or registration fragment")
        try:
            snap = await feeder.fetch()
        except FeederError as e:
            return _error("feeder", str(e))
        hits = [a for a in snap["aircraft"]
                if q in (a["callsign"] or "").lower() or q in (a["hex"] or "") or q in (a["registration"] or "").lower()]
        return _text({"status": "ok", "source": "adsb", "query": query, "count": len(hits),
                      "aircraft": hits[:max(1, max_results)]})

    return mcp


# --- streamable-http bearer gate -------------------------

import hmac  # noqa: E402


class BearerAuth:
    """ASGI gate for streamable-http:
      * /healthz        — always open (Docker healthcheck).
      * everything else — the MCP app, requiring `Authorization: Bearer <token>` when a token is set.
    """

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.expected = f"Bearer {settings.http_token}".encode() if settings.http_token else None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            if scope["path"] == "/healthz":
                await _respond(send, 200, b"ok")
                return
            if self.expected is not None:
                got = dict(scope["headers"]).get(b"authorization", b"")
                if not hmac.compare_digest(got, self.expected):
                    await _respond(send, 401, b"unauthorized")
                    return
        await self.app(scope, receive, send)


async def _respond(send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
