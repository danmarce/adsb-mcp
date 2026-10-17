# CLAUDE.md — adsb-mcp

Context for Claude Code sessions in this repo. Read `~/.claude/mcp-server-standard.md` first — this server
follows it; `C:\Code\forge-mcp` is the reference implementation and `C:\Code\igepn-mcp` is the closest twin
(a local structured source → FastMCP → mcpo → OWUI). This file records the *why*.

> **▶ Current work queue / handoff brief: [`NEXT.md`](NEXT.md).** The MVP live tier is built, deployed
> and validated (2026-10-17). Next up: (1) drop `observer` from `aircraft_overhead` (PII); (2) the
> historical-data tier — a configurable-interval background poller + the passes DB + analytics (the
> `igepn-mcp` twin). Read NEXT.md before starting.

## What this is

A thin MCP server over a **local ADS-B receiver's `aircraft.json`** (readsb / tar1090 / dump1090 shape —
keyless, live, your OWN data). Answers "what aircraft are overhead / near X / matching this callsign" with
structured data for a model to reason over. `ADSB_URL` points at the feeder's `aircraft.json` (default
`http://localhost:8080/data/aircraft.json`); the real feeder host lives in the homelab repo's deploy config,
never here (standard §6 — repo stays generic and public). The star query: *"¿qué avión es ese?"*

Deploys behind mcpo → OWUI, exactly like igepn-mcp / news-mcp.

## Deliberate choices (don't undo)

- **Keyless local source, with the grain.** It's your receiver; nothing to bypass, no API key, no scraping
  (data-access *with the grain*). `ADSB_URL` points at the feeder's `aircraft.json`.
- **openWorldHint = FALSE** on all three tools (standard §1). Judged by behaviour: they talk only to the
  lab's **own LAN receiver** — a fixed, closed dependency, not the public web or a third party. The future
  enrichment tier (FlightAware AeroAPI / OpenSky) WOULD be a separate `openWorld=true` tool.
- **Receiver location is home-location PII.** `ADSB_SITE_LAT/LON` come from the environment (1Password at
  deploy), **never** hardcoded here (homelab repo: `location-pii-is-secret-tier`). Unset is handled
  gracefully: `aircraft_overhead` still lists aircraft, just without distances, and says so. `aircraft_near`
  needs no site — it measures from a point the caller passes.
- **Stateless** (standard §5): every call fetches the live snapshot; no DB, no accumulation. Inputs pass
  through verbatim; the observer coordinates used are echoed.
- **All four hints explicit**, **ok/refused/error** contract (empty query = refused with advice; receiver
  unreachable = error, never a silent empty list).

## Layout

`src/adsb_mcp/`: `config.py` (env settings incl. the optional site PII), `feeder.py` (fetch + normalize
aircraft.json, haversine/bearing — the MockTransport target), `server.py` (tools + status + `BearerAuth`),
`__init__.py` (`main()`). Tools: **aircraft_overhead**, **aircraft_near**, **find_aircraft**.

## Tests

`tests/test_tools.py` — each tool by name through the server against a `FakeFeeder` (`httpx.MockTransport`,
no network): title + four hints (openWorld false), nearest-first distance maths, radius filter,
callsign/hex/reg match, graceful no-site, feeder-down → error. `uv run --system-certs pytest`.

## Not built yet — the accumulation / analytics tier (documented, add when wanted)

The sketch (homelab `someday/todo.md` → adsb-mcp) calls for an *own-it-over-time* layer, the twin of
igepn/football's accumulating store. **Not in this scaffold** (which is the stateless live tier). When added:
- **One row per PASS, not per poll** (a ~6-min visit must be 1 row, not ~180). Sessionize on `hex`: open a
  pass on first-seen, update `last_seen`/`min_alt`/`max_alt`/closest-approach/max-RSSI while present, close +
  write when unseen > ~60–120 s (watermark logic like igepn's "stop at first already-seen").
- **Schema:** `passes` (`hex, callsign, reg, type, first_seen, last_seen, min_alt, max_alt, min_dist,
  max_rssi, points`) + optional `aircraft` dim (`hex → reg/type/operator`, operator from AeroAPI once).
- **Three "most common" axes** (each a different question): `hex`/reg = the airframe, `callsign` = the
  route/service, `t` = the model. Analytical tools (`most_common_flights`, `aircraft_stats`, `seen_before`,
  `busiest_times`, `rarest_visitors`) read the store — like igepn's planned `seismicity_stats`.
- **Enrichment tier (optional, keyed, openWorld=true):** route / origin-dest / airline / registration —
  FlightAware AeroAPI (feeder credits) or OpenSky. The layer ADS-B itself lacks.
