"""The recorder: snapshots -> PASSES (one row per visit), arrival / go-around / departure outcomes, outage and
restart safety — and the two history tools called by name through the server over the store it writes.

No network: the tracker is fed hand-built snapshots on a synthetic feeder clock; `poll_once` runs against
an `httpx.MockTransport` feeder that can be switched off."""

import asyncio
import json
import math

import httpx
import pytest

from adsb_mcp import feeder as feeder_mod
from adsb_mcp.config import Settings
from adsb_mcp.feeder import FeederClient, Snapshots, normalize
from adsb_mcp.passes import Tracker, poll_once
from adsb_mcp.server import build_server
from adsb_mcp.store import connect

AP_LAT, AP_LON, AP_ELEV = -0.129, -78.358, 7900   # test-only airport (public place, not a site)


def east_of_airport(km: float) -> tuple[float, float]:
    return AP_LAT, AP_LON + km / (111.32 * math.cos(math.radians(AP_LAT)))


def ac(hx, km, alt, vr=0, flight="LNE1454", reg="CC-BHD", typ="A20N", seen=0.5):
    lat, lon = east_of_airport(km)
    return {"hex": hx, "flight": flight, "r": reg, "t": typ, "alt_baro": alt, "baro_rate": vr,
            "lat": lat, "lon": lon, "rssi": -10.0, "seen": seen, "seen_pos": seen}


def snap(now, *aircraft):
    return {"now": now, "aircraft": [normalize(a) for a in aircraft]}


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ADSB_SITE_LAT", "-0.18")      # test-only "receiver", ~13 km from the airport
    monkeypatch.setenv("ADSB_SITE_LON", "-78.47")
    monkeypatch.setenv("ADSB_AIRPORT_ICAO", "SEQM")
    monkeypatch.setenv("ADSB_AIRPORT_LAT", str(AP_LAT))
    monkeypatch.setenv("ADSB_AIRPORT_LON", str(AP_LON))
    monkeypatch.setenv("ADSB_AIRPORT_ELEV_FT", str(AP_ELEV))
    monkeypatch.setenv("ADSB_DB", str(tmp_path / "adsb.db"))
    monkeypatch.setenv("ADSB_POLL_SECONDS", "15")
    return Settings()


def rows(s: Settings) -> list[dict]:
    return [dict(r) for r in connect(s.db_path).execute("SELECT * FROM passes ORDER BY id")]


def run(tracker: Tracker, *snaps):
    for sn in snaps:
        tracker.ingest(sn)


def test_overflight_is_one_row_not_one_per_poll(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, *(snap(1000 + 15 * i, ac("e80001", 5 + i, 35000, flight="AAL100")) for i in range(6)))
    assert len(rows(settings)) == 1 and rows(settings)[0]["open"] == 1
    run(tr, snap(1200))                               # gone for > grace (90 s)
    (r,) = rows(settings)
    assert r["open"] == 0 and r["outcome"] == "overflight" and r["points"] == 6
    assert r["min_alt_ft"] == r["max_alt_ft"] == 35000 and r["callsign"] == "AAL100" and r["reference"] == "site"


def test_parked_aircraft_never_opens_a_pass(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, *(snap(1000 + 15 * i, ac("e80002", 0.5, "ground")) for i in range(5)))
    assert rows(settings) == []


def test_arrival_seen_landing(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80003", 25, 15000, -1200)),
        snap(1060, ac("e80003", 15, 12000, -1000)),
        snap(1120, ac("e80003", 6, 9500, -800)),
        snap(1180, ac("e80003", 0.5, "ground")),
        snap(1400))
    (r,) = rows(settings)
    assert r["outcome"] == "arrival" and r["landed_at"] == pytest.approx(1179.5) and r["go_arounds"] == 0


def test_lost_on_approach_is_kept_open_then_presumed_landed(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80004", 25, 15000, -1200)), snap(1060, ac("e80004", 12, 11000, -900)))
    run(tr, snap(1300))                               # past the normal grace, inside the arrival grace
    assert rows(settings)[0]["open"] == 1
    run(tr, snap(2100))                               # past the arrival grace (900 s)
    (r,) = rows(settings)
    assert r["open"] == 0 and r["outcome"] == "arrival_presumed"


def test_go_around_then_landing(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80005", 20, 13000, -1000)),
        snap(1060, ac("e80005", 3, 8400, -700)),          # ~500 ft above the field
        snap(1100, ac("e80005", 4, 9400, 1800)),          # climbing away: go-around
        snap(1300, ac("e80005", 18, 12000, -900)),        # second approach
        snap(1400, ac("e80005", 3, 8500, -700)),
        snap(1460, ac("e80005", 0.3, "ground")),
        snap(1700))
    (r,) = rows(settings)
    assert r["go_arounds"] == 1 and r["outcome"] == "arrival"


def test_go_around_then_leaving(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80006", 20, 13000, -1000)),
        snap(1060, ac("e80006", 3, 8400, -700)),
        snap(1100, ac("e80006", 6, 10000, 2000)),
        snap(1160, ac("e80006", 70, 16000, 1500)),        # out of the zone
        snap(1400))
    (r,) = rows(settings)
    assert r["go_arounds"] == 1 and r["outcome"] == "go_around"


def test_departure(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80007", 3, 9000, 2000)), snap(1060, ac("e80007", 20, 14000, 2500)),
        snap(1120, ac("e80007", 80, 20000, 2000)), snap(1400))
    (r,) = rows(settings)
    assert r["outcome"] == "departure" and r["departure"] == 1


def test_restart_resumes_the_open_pass(settings):
    run(Tracker(settings, connect(settings.db_path)),
        snap(1000, ac("e80008", 5, 30000)), snap(1015, ac("e80008", 6, 30000)))
    tr2 = Tracker(settings, connect(settings.db_path))   # a new process over the same file
    run(tr2, snap(1030, ac("e80008", 7, 30000)))
    (r,) = rows(settings)
    assert r["points"] == 3 and r["open"] == 1


def test_no_reference_point_means_no_tracking(monkeypatch, tmp_path):
    monkeypatch.setenv("ADSB_DB", str(tmp_path / "x.db"))
    for k in ("ADSB_SITE_LAT", "ADSB_SITE_LON", "ADSB_AIRPORT_LAT", "ADSB_AIRPORT_LON"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError):
        Tracker(Settings(), connect(Settings().db_path))


def test_airport_alone_is_enough_for_passes(settings, monkeypatch):
    monkeypatch.delenv("ADSB_SITE_LAT")
    monkeypatch.delenv("ADSB_SITE_LON")
    s = Settings()
    run(Tracker(s, connect(s.db_path)), snap(1000, ac("e80009", 10, 20000)))
    assert rows(s)[0]["reference"] == "airport"


class SwitchableFeeder:
    def __init__(self) -> None:
        self.down = False
        self.body, self.raw = {"now": 0}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        return httpx.Response(200, json={"now": self.body["now"], "aircraft": self.raw})


def test_feeder_outage_never_closes_or_splits_a_pass(settings, monkeypatch):
    fake = SwitchableFeeder()
    real = httpx.AsyncClient
    monkeypatch.setattr(feeder_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(fake), **kw))
    snaps = Snapshots(FeederClient(settings), max_age_s=30)
    tr = Tracker(settings, connect(settings.db_path))

    async def tick(now, *aircraft):
        fake.body, fake.raw = {"now": now}, list(aircraft)
        return await poll_once(snaps, tr)

    async def scenario():
        assert await tick(1000, ac("e80010", 5, 30000))
        fake.down = True
        assert not await tick(1100)                      # 5 failed polls spanning > grace...
        assert not await tick(1300)
        fake.down = False
        assert await tick(1310, ac("e80010", 9, 30000))  # ...and it's still the same pass
    asyncio.run(scenario())
    (r,) = rows(settings)
    assert r["open"] == 1 and r["points"] == 2


# --- the history tools, by name through the server ---------------------------------------------------

def _call(s: Settings, name: str, args: dict) -> dict:
    res = asyncio.run(build_server(s).call_tool(name, args))
    return json.loads([c for c in res.content if c.type == "text"][-1].text)


@pytest.fixture
def history(settings):
    import time
    t0 = time.time() - 3600
    tr = Tracker(settings, connect(settings.db_path))
    # LNE1454 twice (same airframe), one arrival; AAL100 once; an E295 once.
    run(tr, snap(t0, ac("e80003", 25, 15000, -1200)), snap(t0 + 60, ac("e80003", 6, 9500, -800)),
        snap(t0 + 120, ac("e80003", 0.5, "ground")), snap(t0 + 400))
    run(tr, snap(t0 + 1000, ac("e80003", 10, 30000)), snap(t0 + 1300))
    run(tr, snap(t0 + 1500, ac("a12345", 10, 35000, flight="AAL100", reg="N1AA", typ="B738")), snap(t0 + 1800))
    run(tr, snap(t0 + 2000, ac("e8bbbb", 10, 28000, flight="AZU999", reg="PR-AXA", typ="E295")))  # still open
    return settings


def test_traffic_stats_by_callsign_airframe_type(history):
    d = _call(history, "traffic_stats", {"group_by": "callsign"})
    assert d["status"] == "ok" and d["total_passes"] == 4 and d["total_airframes"] == 3
    assert d["rows"][0]["callsign"] == "LNE1454" and d["rows"][0]["passes"] == 2
    a = _call(history, "traffic_stats", {"group_by": "airframe"})["rows"][0]
    assert a["hex"] == "e80003" and a["registration"] == "CC-BHD" and a["callsigns"] == ["LNE1454"]
    t = _call(history, "traffic_stats", {"group_by": "type", "order": "least", "limit": 1})["rows"]
    assert len(t) == 1 and t[0]["passes"] == 1
    o = {r["outcome"]: r["passes"] for r in _call(history, "traffic_stats", {"group_by": "outcome"})["rows"]}
    assert o["arrival"] == 1 and o["overflight"] == 2 and o[None] == 1   # the open pass has no outcome yet
    h = _call(history, "traffic_stats", {"group_by": "hour"})
    assert sum(r["passes"] for r in h["rows"]) <= 4 and h["recorder"]["enabled"] is True


def test_traffic_stats_refuses_bad_input(history):
    assert _call(history, "traffic_stats", {"group_by": "airline"})["status"] == "refused"
    assert _call(history, "traffic_stats", {"order": "sideways"})["status"] == "refused"
    assert _call(history, "traffic_stats", {"since_days": 0})["status"] == "refused"


def test_aircraft_history_seen_before(history):
    d = _call(history, "aircraft_history", {"query": "cc-bhd"})
    assert d["status"] == "ok" and d["seen_before"] is True and d["total_passes"] == 2
    assert d["passes"][0]["outcome"] == "overflight" and d["passes"][1]["outcome"] == "arrival"
    assert d["passes"][1]["landed_at"] is not None and d["airframes"][0]["passes"] == 2
    assert _call(history, "aircraft_history", {"query": "azu"})["passes"][0]["in_progress"] is True
    assert _call(history, "aircraft_history", {"query": "zzz"})["seen_before"] is False
    assert _call(history, "aircraft_history", {"query": "%"})["total_passes"] == 0   # LIKE wildcards are literal
    assert _call(history, "aircraft_history", {"query": " "})["status"] == "refused"


def test_history_tools_refuse_when_not_recording(monkeypatch, tmp_path):
    monkeypatch.setenv("ADSB_DB", str(tmp_path / "none.db"))
    for name, args in (("traffic_stats", {}), ("aircraft_history", {"query": "lan"})):
        d = _call(Settings(), name, args)
        assert d["status"] == "refused" and "ADSB_POLL_SECONDS" in d["advice"]


def test_coverage_gap_reopens_the_same_pass(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80011", 10, 17500)), snap(1015, ac("e80011", 9, 17500)),
        snap(1200))                                       # gone > grace: closed (overflight, for now)
    assert rows(settings)[0]["open"] == 0
    run(tr, snap(1300, ac("e80011", 20, 14000, -1300)))   # back after a 285 s gap (< merge window)
    (r,) = rows(settings)
    assert r["open"] == 1 and r["outcome"] is None and r["points"] == 3


def test_a_late_return_is_a_new_pass(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80012", 10, 35000)), snap(1200), snap(2000, ac("e80012", 10, 35000)))
    assert [r["open"] for r in rows(settings)] == [0, 1]   # 1000 s later (> 600 s merge window): new visit


def test_a_landed_pass_is_never_reopened(settings):
    tr = Tracker(settings, connect(settings.db_path))
    run(tr, snap(1000, ac("e80013", 15, 12000, -1000)), snap(1060, ac("e80013", 0.5, "ground")),
        snap(1300), snap(1400, ac("e80013", 3, 9000, 2000)))   # quick turnaround: a new (departure) pass
    r = rows(settings)
    assert len(r) == 2 and r[0]["outcome"] == "arrival" and r[1]["departure"] == 1
