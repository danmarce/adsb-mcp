# adsb-mcp

Live local **ADS-B flight data** as MCP tools, over your own receiver's `aircraft.json` (readsb / tar1090 /
dump1090) — **keyless, live, your own data**. Answers "what's flying overhead right now?"

## Tools

| Tool | What it does |
|---|---|
| **aircraft_overhead** | Aircraft in range of the receiver, nearest first (needs `ADSB_SITE_LAT/LON` for distances; degrades gracefully without). |
| **aircraft_near** | Aircraft within a radius of a point you pass (no receiver location needed). |
| **find_aircraft** | Match by callsign / ICAO hex / registration (partial, case-insensitive). |

Data is structured (hex, callsign, registration, type, altitude_ft, ground_speed_kt, track_deg, lat/lon,
distance_km + bearing_deg when the site is set, seen_s). Every result has a `status`: `ok` / `refused`
(caller-fixable, with `advice`) / `error` (receiver unreachable). ADS-B carries no route/airline — that's a
separate optional enrichment tier (not these tools).

## Run

```bash
uv sync                      # (--system-certs behind a TLS-inspecting proxy)
# stdio:
uv run adsb-mcp serve
# streamable-http (behind mcpo / on the LAN):
ADSB_MCP_TOKEN=... ADSB_URL=http://<feeder-host>:8080/data/aircraft.json \
  ADSB_SITE_LAT=... ADSB_SITE_LON=... \
  uv run adsb-mcp serve --transport http --host 0.0.0.0 --port 8000
```

## Config (env)

| Var | Default | Notes |
|---|---|---|
| `ADSB_URL` | `http://localhost:8080/data/aircraft.json` | Your feeder's live snapshot (readsb / tar1090 path). |
| `ADSB_SITE_LAT` / `ADSB_SITE_LON` | — | Receiver location for nearest-first overhead. **Home-location PII — from 1Password, never committed.** Unset = overhead without distances. |
| `ADSB_MCP_TOKEN` | — | Bearer required on streamable-http when set. |
| `ADSB_HTTP_TIMEOUT` | `10` | Seconds. |

## Auth & deployment

Transports: **stdio**, or **streamable-http + bearer** on a private network (behind mcpo). No OAuth in the
server. `/healthz` is open; everything else needs the bearer when a token is set. The feeder is your
own receiver; point `ADSB_URL` at it.

## AI assistance

adsb-mcp is developed openly with the help of Claude (Anthropic). Commits Claude helped write carry a
`Co-Authored-By: Claude` trailer. The code and design are open source so the work can be inspected, reused,
and given back.

## License

MPL-2.0.
