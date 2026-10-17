"""Tool-level: call each MCP tool by name through the server against a fake feeder (no network).

Pins the title + all four hints (openWorld FALSE — own LAN receiver), the ok/refused/error contract, the
nearest-first distance maths, the radius filter, callsign/hex/reg matching, and graceful no-site behaviour."""

import asyncio
import json

import httpx
import pytest

from adsb_mcp import feeder as feeder_mod
from adsb_mcp.config import Settings
from adsb_mcp.server import build_server

# A snapshot: near-Quito airborne, far airborne, grounded near, and position-less-with-callsign.
SNAPSHOT = {"now": 1760650000.0, "aircraft": [
    {"hex": "e80abc", "flight": "LAN401 ", "r": "CC-BLA", "t": "A320", "alt_baro": 39000, "gs": 450,
     "track": 320, "baro_rate": 0, "lat": -0.20, "lon": -78.50, "rssi": -12.3, "seen": 1.2, "seen_pos": 1.2},
    {"hex": "a12345", "flight": "AAL100 ", "r": "N1AA", "t": "B738", "alt_baro": 35000, "gs": 470,
     "track": 10, "lat": 10.0, "lon": -70.0, "rssi": -20.0, "seen": 3.0, "seen_pos": 3.0},
    {"hex": "e8aaaa", "flight": "TAME5 ", "t": "AT76", "alt_baro": "ground", "gs": 12,
     "lat": -0.17, "lon": -78.48, "rssi": -8.0, "seen": 0.5, "seen_pos": 0.5},
    {"hex": "e8bbbb", "flight": "AZU999 ", "t": "E295", "alt_baro": 28000, "seen": 0.9},  # no position yet
]}


class FakeFeeder:
    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req.url.path)
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        return httpx.Response(200, json=SNAPSHOT)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ADSB_SITE_LAT", "-0.18")   # test-only "receiver" location (near Quito)
    monkeypatch.setenv("ADSB_SITE_LON", "-78.47")
    fake = FakeFeeder()
    real = httpx.AsyncClient
    monkeypatch.setattr(feeder_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(fake), **kw))
    return fake


def _call(settings: Settings, name: str, args: dict) -> dict:
    res = asyncio.run(build_server(settings).call_tool(name, args))
    texts = [c for c in res.content if c.type == "text"]
    return json.loads(texts[-1].text)


def test_every_tool_declares_title_and_all_four_hints(env):
    tools = asyncio.run(build_server(Settings()).list_tools())
    assert {t.name for t in tools} == {"aircraft_overhead", "aircraft_near", "find_aircraft"}
    for t in tools:
        wire = t.annotations.model_dump(by_alias=True)
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert isinstance(wire.get(hint), bool), f"{t.name}.{hint} unset"
        assert t.title and wire.get("title") == t.title
        assert wire["readOnlyHint"] and not wire["destructiveHint"]
        assert wire["openWorldHint"] is False   # own LAN receiver, not the open world


def test_overhead_sorts_by_distance_and_excludes_grounded_and_posless(env):
    d = _call(Settings(), "aircraft_overhead", {})
    assert d["status"] == "ok" and d["site_configured"] is True and d["observer"]["lat"] == -0.18
    hexes = [a["hex"] for a in d["aircraft"]]
    assert hexes[0] == "e80abc"                      # nearest (near-Quito) first
    assert "e8aaaa" not in hexes and "e8bbbb" not in hexes   # grounded + position-less excluded
    assert d["aircraft"][0]["distance_km"] < 10 and "bearing_deg" in d["aircraft"][0]


def test_overhead_include_grounded(env):
    d = _call(Settings(), "aircraft_overhead", {"include_grounded": True})
    assert "e8aaaa" in [a["hex"] for a in d["aircraft"]]


def test_overhead_without_site_is_graceful(env, monkeypatch):
    monkeypatch.delenv("ADSB_SITE_LAT", raising=False)
    monkeypatch.delenv("ADSB_SITE_LON", raising=False)
    d = _call(Settings(), "aircraft_overhead", {})
    assert d["status"] == "ok" and d["site_configured"] is False and d["observer"] is None
    assert "note" in d and "distance_km" not in d["aircraft"][0]


def test_near_filters_by_radius(env):
    d = _call(Settings(), "aircraft_near", {"latitude": -0.18, "longitude": -78.47, "radius_km": 25})
    hexes = [a["hex"] for a in d["aircraft"]]
    assert "e80abc" in hexes and "a12345" not in hexes   # the far one is excluded
    assert d["aircraft"][0]["distance_km"] <= 25


def test_find_by_callsign_hex_and_posless(env):
    assert "e80abc" in [a["hex"] for a in _call(Settings(), "find_aircraft", {"query": "lan"})["aircraft"]]
    assert "a12345" in [a["hex"] for a in _call(Settings(), "find_aircraft", {"query": "a123"})["aircraft"]]
    posless = _call(Settings(), "find_aircraft", {"query": "azu"})["aircraft"]   # matches despite no position
    assert posless and posless[0]["hex"] == "e8bbbb"


def test_empty_query_refused(env):
    d = _call(Settings(), "find_aircraft", {"query": "  "})
    assert d["status"] == "refused"


def test_feeder_down_is_error(env):
    env.down = True
    d = _call(Settings(), "aircraft_overhead", {})
    assert d["status"] == "error" and d["kind"] == "feeder"
