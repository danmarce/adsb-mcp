# adsb-mcp

Live local **ADS-B flight data** as MCP tools, over your own receiver's `aircraft.json` (readsb / tar1090 /
dump1090) — **keyless, live, your own data**. Answers "what's flying overhead right now?" and, with the
optional recorder on, "have we seen this one before?" / "what flies here most?".

## Tools

| Tool | What it does |
|---|---|
| **aircraft_overhead** | Aircraft in range of the receiver, nearest first. Distances from the site (`ADSB_SITE_LAT/LON`), else from the airport (approximate); without either, no distances. |
| **aircraft_near** | Aircraft within a radius of a point you pass (no receiver location needed). |
| **find_aircraft** | Match by callsign / ICAO hex / registration (partial, case-insensitive). |
| **traffic_stats** | Recorded passes over a window, grouped by airframe, callsign, type, outcome or local hour; most or least common. |
| **aircraft_history** | Past passes of an airframe / callsign / registration: "seen before?", when, how low, landed or went around. |

Data is structured (hex, callsign, registration, type, altitude_ft, ground_speed_kt, track_deg, lat/lon,
distance_km + bearing_deg from the reference point, seen_s). The site's coordinates are never echoed. Every result has a `status`: `ok` / `refused`
(caller-fixable, with `advice`) / `error` (receiver unreachable). ADS-B carries no route/airline — that's a
separate optional enrichment tier (not these tools).

## Run

```bash
uv sync                      # (--system-certs behind a TLS-inspecting proxy)
# stdio:
uv run adsb-mcp serve
# streamable-http (behind mcpo / on the LAN):
ADSB_MCP_TOKEN=... ADSB_URL=http://<feeder-host>:8080/data/aircraft.json \
  ADSB_SITE_LAT=... ADSB_SITE_LON=... ADSB_POLL_SECONDS=15 \
  uv run adsb-mcp serve --transport http --host 0.0.0.0 --port 8000
uv run pytest                # no network: the feeder is faked
```

## Config (env)

| Var | Default | Notes |
|---|---|---|
| `ADSB_URL` | `http://localhost:8080/data/aircraft.json` | Your feeder's live snapshot (readsb / tar1090 path). |
| `ADSB_SITE_LAT` / `ADSB_SITE_LON` | — | Receiver location: the exact reference point. **Home-location PII — from a secret store, never committed, never echoed in results.** |
| `ADSB_AIRPORT_ICAO` / `ADSB_AIRPORT_LAT` / `ADSB_AIRPORT_LON` | — | The nearby airport (e.g. `EGLL`). An approximate, public reference point when the site isn't set. |
| `ADSB_AIRPORT_ELEV_FT` | — | Field elevation. With the airport position, turns on arrival / go-around / departure tracking. |
| `ADSB_MCP_TOKEN` | — | Bearer required on streamable-http when set. |
| `ADSB_HTTP_TIMEOUT` | `10` | Seconds. |
| `ADSB_POLL_SECONDS` | `0` | `>0` turns on the recorder: poll the feeder this often and record passes. `0` = live tools only. 10–15 is a good value. |
| `ADSB_DB` | `data/adsb.db` | SQLite store of passes (`/data/adsb.db` in the image). |
| `ADSB_PASS_RADIUS_KM` | `50` | A pass = an airborne aircraft within this radius of the reference point, or on approach to the airport. |
| `ADSB_PASS_GRACE_SECONDS` | `90` | Close a pass after the aircraft has been gone, or out of the zone, this long. |
| `ADSB_ARRIVAL_GRACE_SECONDS` | `900` | An aircraft lost **on approach** stays open this long (it's presumably landing below coverage). |
| `ADSB_APPROACH_KM` | `30` | How far from the airport a descent counts as an approach. |
| `ADSB_TZ` | `UTC` | IANA zone for local times and hour-of-day stats (e.g. `Europe/London`). |

## History: passes

With `ADSB_POLL_SECONDS>0` and a reference point (the site, else the airport), the server polls the feeder
in the background, whether or not anyone is asking, and records one row per **pass**: a visit, not a poll.
A ~6-minute overflight is 1 row, not ~24. Each pass keeps first / last seen, min / max altitude, closest
approach, max RSSI and the number of snapshots. The live tools reuse the recorder's latest snapshot while
it's fresh, so the feeder isn't hit twice.

With the airport elevation set, each pass also gets an **outcome**: `arrival` (seen landing),
`arrival_presumed` (lost on approach: coverage usually ends before the runway, so the pass is held open
`ADSB_ARRIVAL_GRACE_SECONDS`), `go_around`, `departure` or `overflight`, plus a `go_arounds` count. The
heuristics are simple and documented in [`passes.py`](src/adsb_mcp/passes.py).

A failed poll never closes a pass, and open passes are written through, so a restart resumes them. With
neither a site nor an airport, passes can't be tracked and the recorder stays off.

### Claude Desktop / Claude Code (stdio)

```json
{
  "mcpServers": {
    "adsb": {
      "command": "uv",
      "args": ["--directory", "/path/to/adsb-mcp", "run", "adsb-mcp", "serve"],
      "env": { "ADSB_URL": "http://<feeder-host>:8080/data/aircraft.json" }
    }
  }
}
```

## Docker

```bash
docker build -t adsb-mcp .
docker run --rm -p 8000:8000 -e ADSB_MCP_TOKEN=... \
  -e ADSB_URL=http://<feeder-host>:8080/data/aircraft.json \
  -e ADSB_SITE_LAT=... -e ADSB_SITE_LON=... adsb-mcp
```

It needs to reach your feeder over the LAN. Inside the container `localhost` is the container itself, so
set `ADSB_URL` to the feeder's address. With the recorder on, mount `/data` on **local disk** (SQLite WAL
and network filesystems don't mix) and back it up: the history accumulates and can't be re-fetched.

```bash
docker run -d -p 8000:8000 -v /srv/adsb-mcp-data:/data -e ADSB_POLL_SECONDS=15 \
  -e ADSB_AIRPORT_ICAO=... -e ADSB_AIRPORT_LAT=... -e ADSB_AIRPORT_LON=... -e ADSB_AIRPORT_ELEV_FT=... \
  -e ADSB_URL=http://<feeder-host>:8080/data/aircraft.json -e ADSB_MCP_TOKEN=... adsb-mcp
```

## Auth and exposure

Auth is deliberately simple: stdio, or streamable-HTTP with a single bearer token (`ADSB_MCP_TOKEN`) on a
private network, for example behind [mcpo](https://github.com/open-webui/mcpo). `/healthz` stays open. There
is **no OAuth, no users and no audit log**, by design. To publish it more widely, put a management layer in
front of it: an MCP gateway or auth proxy that handles OAuth, users and audit for all your servers at once.
`ADSB_SITE_LAT/LON` is your home location. It is never echoed in a result, and the store holds distances,
not coordinates. Distances and bearings to aircraft whose positions are also returned still let a
determined caller work it out, so expose the server only to callers you trust with that. If that matters,
set only the airport: results are then measured from a public place.

## AI assistance

adsb-mcp is developed openly with the help of Claude (Anthropic). Commits Claude helped write carry a
`Co-Authored-By: Claude` trailer. The code and design are open source so the work can be inspected, reused,
and given back.

## License

[MPL-2.0](LICENSE) © Daniel Núñez.
