"""The FIDS board: /board.json content (airport-referenced, no site PII), the held-arrival + recent rows read
from the store, and access (open / token / off) through the real ASGI gate — no network."""

import asyncio
import json

import httpx
import pytest

from adsb_mcp import feeder as feeder_mod
from adsb_mcp.board import Board, build_board
from adsb_mcp.config import Settings
from adsb_mcp.feeder import FeederClient, Snapshots
from adsb_mcp.passes import Tracker
from adsb_mcp.server import BearerAuth
from adsb_mcp.store import connect

from test_passes import ac, snap   # same synthetic airport / aircraft helpers

REAL_CLIENT = httpx.AsyncClient   # the feeder patch below replaces httpx.AsyncClient module-wide


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ADSB_SITE_LAT", "-0.18")
    monkeypatch.setenv("ADSB_SITE_LON", "-78.47")
    monkeypatch.setenv("ADSB_AIRPORT_ICAO", "SEQM")
    monkeypatch.setenv("ADSB_AIRPORT_NAME", "Test Intl")
    monkeypatch.setenv("ADSB_AIRPORT_LAT", "-0.129")
    monkeypatch.setenv("ADSB_AIRPORT_LON", "-78.358")
    monkeypatch.setenv("ADSB_AIRPORT_ELEV_FT", "7900")
    monkeypatch.setenv("ADSB_DB", str(tmp_path / "adsb.db"))
    monkeypatch.setenv("ADSB_MCP_TOKEN", "test-token")
    monkeypatch.delenv("ADSB_BOARD", raising=False)
    return Settings()


LIVE = [{**ac("e80001", 15, 12000, -1000, flight="ARR1"), "gs": 180},   # on approach
        ac("e80002", 6, 10500, 2500, flight="DEP1"),          # climbing out
        ac("e80003", 30, 35000, 0, flight="OVR1"),            # cruising overhead
        ac("e80004", 0.5, "ground", flight="GND1"),           # parked: not shown
        {**ac("e80005", 20, 20000, 0, flight="SOS1"), "squawk": "7700"}]


def test_board_document(settings):
    d = build_board(settings, snap(10_000, *LIVE), None)
    assert d["status"] == "ok" and d["airport"] == {"icao": "SEQM", "name": "Test Intl"}
    assert [r["callsign"] for r in d["arrivals"]] == ["ARR1"] and d["arrivals"][0]["eta_min"] >= 1
    assert [r["callsign"] for r in d["departures"]] == ["DEP1"]
    ovh = [r["callsign"] for r in d["overhead"]]
    assert ovh == ["DEP1", "ARR1", "SOS1", "OVR1"] and "GND1" not in ovh       # nearest the airport first
    assert next(r for r in d["overhead"] if r["callsign"] == "SOS1")["emergency"] == "7700"
    assert d["recording"] is False and d["recent"] == []


def test_board_carries_no_positions_or_site(settings):
    text = json.dumps(build_board(settings, snap(10_000, *LIVE), None))
    assert '"lat"' not in text and '"lon"' not in text and "-0.18" not in text and "-78.47" not in text


def test_board_reads_held_arrivals_and_recent_from_the_store(settings):
    tr = Tracker(settings, connect(settings.db_path))
    tr.ingest(snap(1000, ac("e80010", 25, 15000, -1200, flight="LOST1")))
    tr.ingest(snap(1060, ac("e80010", 12, 11000, -900, flight="LOST1")))      # then lost on approach
    tr.ingest(snap(1000, ac("e80011", 10, 35000, flight="GONE1")))
    tr.ingest(snap(1300))                                                     # GONE1 closes: overflight
    d = build_board(settings, snap(1300), connect(settings.db_path))
    held = [r for r in d["arrivals"] if r["status"] == "arrival_presumed"]
    assert [r["callsign"] for r in held] == ["LOST1"] and held[0]["lost_min"] == 4
    assert [(r["callsign"], r["outcome"]) for r in d["recent"]] == [("GONE1", "overflight")]


class Feed:
    def __call__(self, req):
        return httpx.Response(200, json={"now": 10_000, "aircraft": LIVE})


async def _get(app, path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, headers=headers)


def _gate(settings, monkeypatch):
    monkeypatch.setattr(feeder_mod.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(Feed()), **kw))

    async def mcp_app(scope, receive, send):    # stands in for the MCP app behind the gate
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"mcp"})
    board = Board(settings, Snapshots(FeederClient(settings))) if settings.board != "off" else None
    return BearerAuth(mcp_app, settings, board)


def test_board_is_open_by_default_but_mcp_still_needs_the_bearer(settings, monkeypatch):
    app = _gate(settings, monkeypatch)
    r = asyncio.run(_get(app, "/board.json"))
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.headers["cache-control"] == "no-store"
    page = asyncio.run(_get(app, "/board"))
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert b"board.json" in page.content
    assert asyncio.run(_get(app, "/mcp")).status_code == 401


def test_board_token_mode(settings, monkeypatch):
    monkeypatch.setenv("ADSB_BOARD", "token")
    app = _gate(Settings(), monkeypatch)
    assert asyncio.run(_get(app, "/board.json")).status_code == 401
    assert asyncio.run(_get(app, "/board.json", token="test-token")).status_code == 200


def test_board_off(settings, monkeypatch):
    monkeypatch.setenv("ADSB_BOARD", "off")
    app = _gate(Settings(), monkeypatch)
    r = asyncio.run(_get(app, "/board.json", token="test-token"))
    assert r.content == b"mcp"            # not served: falls through to the MCP app


def test_board_without_airport_refuses(settings, monkeypatch):
    for k in ("ADSB_AIRPORT_LAT", "ADSB_AIRPORT_LON"):
        monkeypatch.delenv(k)
    d = asyncio.run(_get(_gate(Settings(), monkeypatch), "/board.json")).json()
    assert d["status"] == "refused" and "ADSB_AIRPORT_LAT" in d["advice"]
