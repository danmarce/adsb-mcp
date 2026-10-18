# NEXT — adsb-mcp work queue / backlog

For the next Claude working in this repo. Read `CLAUDE.md` first (the *why*); this is the *what next*.
Follow `~/.claude/mcp-server-standard.md`. Deployment details (hosts, paths, secrets) live in the homelab
repo, never here.

## Done

- **Live tier** — `aircraft_overhead`, `aircraft_near`, `find_aircraft`. Validated against a real feeder.
- **Privacy** — `observer` (the exact site) removed from results; `reference: {"kind": ...}` instead.
- **Historical tier** — background recorder (`ADSB_POLL_SECONDS`, off by default), passes store (SQLite,
  write-through, restart-safe, outage-safe), airport reference fallback, arrival / presumed arrival /
  go-around / departure tracking, live tools reusing the recorder's snapshot, and two history tools:
  `traffic_stats`, `aircraft_history`.
- **Pass merge + approximate location** — coverage gaps reopen the pass; `ADSB_EXACT_LOCATION` opt-in.
- **Flight board** — `/board` + `/board.json` (see below).
- **OpenSky enrichment** — yesterday's passes get origin/destination (one call per airframe-day, budgeted);
  `usual_route` on live aircraft and the board, `unusual_route` on history, `traffic_stats(group_by="route")`.

## To validate on real data

- Run the recorder for a few days and check the arrival heuristics against what actually happened
  (thresholds are constants at the top of `passes.py`). Watch for: arrivals counted as `overflight`
  (approach not detected — descent threshold too strict?), `arrival_presumed` that were really
  overflights descending elsewhere, and go-arounds.
- Baro altitude is pressure altitude, not QNH: at a high airport the AGL estimate can be off by a few
  hundred feet. Landing is confirmed by `on_ground`, not altitude, for that reason.

## Backlog

- **Dedicated analytics tools** (if callers benefit — `traffic_stats` covers them today):
  `most_common_flights`, `seen_before`, `busiest_times`, `rarest_visitors`, `aircraft_stats`.
- **Operator / owner** — OpenSky publishes an aircraft database as a downloadable file (hex → operator,
  owner, model). Importing it periodically would name the airline with ZERO API calls. Check it's still
  offered and its licence first.
- **Validate enrichment on real data** — field names (`estDepartureAirport` / `estArrivalAirport` /
  `firstSeen` / `lastSeen`) come from OpenSky's Python client; confirm against real responses, and check
  how often airports come back null around a high-altitude airport with sparse coverage.
- **Fewer OpenSky calls as history grows** (Daniel's idea; check after a few weeks of data) — skip the
  lookup for a callsign whose usual route is already reliable (e.g. ≥ 5 passes, ≥ 90 % one route) and only
  sample it now and then (say weekly) so changes and diversions are still caught. Measure first: credits
  per day vs. how many lookups just confirmed the usual route.
- **Live route hint on the board** is the callsign's usual route; a real-time source would need a
  different (earned) API — FlightAware AeroAPI for feeders is worth a look, FR24's new API is restrictive.
- **Retention / backup tooling** — the store accumulates; a `backup` subcommand (SQLite online backup) may
  be worth it. If you add one-shot subcommands run via `docker run --rm`, bind `/data` explicitly (the
  image declares `VOLUME /data`; an unbound `--rm` run writes to a throwaway anonymous volume).

## The ambient FIDS board — `board.json` + `board.html` (the "something interesting"; Daniel, 2026-10-17)

**Built** (2026-10-18): `/board` + `/board.json` as below, `ADSB_BOARD=open|token|off`, `ADSB_AIRPORT_NAME`,
`?lang=es`, `?interval=`, `?sections=`. Arrivals include held (`arrival_presumed`) and `landed` rows from the
store. Still open: the HA RESTful-sensor idea under *Later*, and checking it on the real wall device.

An **airport arrivals / FIDS board**: an ambient display of what's overhead and arriving, to run on an old
phone/tablet on a wall **and** embedded in Home Assistant. Minimal, **client-side render**, one data source.

**Two tiny additions, both served by the existing ASGI app (alongside `/healthz`):**

1. **`GET /board.json`** — a read-only snapshot for a display, **airport-referenced** (the public reference
   point), so it carries **no site/home PII** and can stay **open** (no bearer), like `/healthz`. Built from
   the live snapshot classified by the pass/airport logic. Shape (draft):
   - `now`, `airport: {icao, name?}`
   - `overhead`: in range now, nearest first — `{hex, callsign, type, registration, altitude_ft,
     ground_speed_kt, track_deg, distance_km, bearing_deg, vertical_rate_fpm, category}`
   - `arrivals`: on approach — `{callsign, type, distance_km, altitude_ft, eta_min?, status:
     approaching|arrival_presumed}`
   - `departures`: climbing out — `{callsign, type, altitude_ft}`
   - `recent`: last few **closed** passes (just landed / departed / overflew) — the board's "just happened" row.

2. **`GET /board` (board.html)** — one **self-contained** HTML file (HTML+CSS+JS, **no build, no deps**) that
   polls `/board.json` every ~5–10 s and renders a split-flap / FIDS board **client-side** (the old device
   does the work; the server only serves JSON + the static file). **Responsive by requirement:** works
   **landscape** (wall), **portrait** (phone), and a **small iframe** (HA card) — CSS grid/flex that reflows,
   font scales, drop low-priority columns at narrow widths. One file, one breakpoint set.

**That completes it — one `board.json`, one `board.html`, two homes:**
- **Standalone:** point an old phone/tablet at `…/board` → full-screen FIDS board (the Kurena wall-panel idea
  in the homelab `someday/todo.md`).
- **In HA:** an `iframe`/webpage Lovelace card embedding the same `…/board` URL — it fits because board.html is
  responsive. (If the HA dashboard is https, serve `/board` via an https name so the browser doesn't block an
  http iframe.)

**Design rules:** airport-referenced (keeps `/board.json` PII-free + openable) · client-side render (scales to
N displays at ~zero server cost) · no front-end build or deps (sovereign, runs on anything with a browser).
*Later:* the same `/board.json` can also feed HA **entities** via a RESTful sensor — for the proactive triggers
(747-on-final push, squawk-watch) that need aircraft as entities, not a picture.
