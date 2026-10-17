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

The server is stateless: no database, no volume. It needs to reach your feeder over the LAN. Inside the
container `localhost` is the container itself, so set `ADSB_URL` to the feeder's address.

## Auth and exposure

Auth is deliberately simple: stdio, or streamable-HTTP with a single bearer token (`ADSB_MCP_TOKEN`) on a
private network, for example behind [mcpo](https://github.com/open-webui/mcpo). `/healthz` stays open. There
is **no OAuth, no users and no audit log**, by design. To publish it more widely, put a management layer in
front of it: an MCP gateway or auth proxy that handles OAuth, users and audit for all your servers at once.
Keep in mind that `ADSB_SITE_LAT/LON` is your home location, and `aircraft_overhead` echoes it back as
`observer`: anyone who can call the tool can read it.

## AI assistance

adsb-mcp is developed openly with the help of Claude (Anthropic). Commits Claude helped write carry a
`Co-Authored-By: Claude` trailer. The code and design are open source so the work can be inspected, reused,
and given back.

## License

[MPL-2.0](LICENSE) © Daniel Núñez.
