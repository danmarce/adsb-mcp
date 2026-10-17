# NEXT — adsb-mcp work queue / handoff brief

For the next Claude working in this repo. Written 2026-10-17 from the homelab deploy+validation session.
Read `CLAUDE.md` first (the *why*); this is the *what next*. Follow `~/.claude/mcp-server-standard.md`;
`igepn-mcp` is the twin to copy for everything in Task 2 (SQLite accumulation + in-process poller).

## Where it stands (done)

The **MVP stateless live tier is built, deployed, and validated.** Tools `aircraft_overhead`,
`aircraft_near`, `find_aircraft` over the feeder's keyless `aircraft.json`. Deployed on Yuki via the
homelab repo (`containers/stacks/adsb-mcp/`), fronted by mcpo at `/adsb`. **Validated live 2026-10-17:**
all three tools `ok`, feeder reachable (VLAN70→IoT routing confirmed), pulled a real LATAM Ecuador A320neo
(`LNE1454`/`CC-BHD`) climbing out of UIO — distance/bearing/altitude/vertical-rate all correct, and the
live snapshot proven (values changed call-to-call). Tests green (8).

## Task 1 — privacy: drop `observer` from `aircraft_overhead` (small; do first)

`aircraft_overhead` currently echoes `observer: {lat, lon}` = the **exact receiver/home coordinates** =
location PII, into every result (and therefore every transcript/log of a call). It isn't needed: each
aircraft already carries `distance_km` + `bearing_deg`. **Remove `observer`; keep `site_configured`
(bool)** so a caller still knows whether distances are real. ~2-line change in `server.py` +
`test_overhead_sorts_by_distance_and_excludes_grounded_and_posless` asserts `observer["lat"]` — update it.
Rationale: Daniel's location-PII-is-secret-tier stance; coords stay out of outputs.

## Task 2 — historical data: the accumulation + analytics tier (the big build)

The "own it over time" layer — the twin of igepn-mcp's accumulating catalog. **Additive: keep the
stateless live tier exactly as-is.** Read `igepn-mcp`'s poller + schema + bind-mount handling first.

### 2a. Background poller with a CONFIGURABLE interval  ← Daniel's explicit requirement
- New env **`ADSB_POLL_SECONDS`** — how often the server queries the Pi/feeder. The MVP only fetches
  on-demand (per tool call); the historical tier needs a **continuous in-process poller** (like igepn's
  `IGEPN_POLL_MINUTES`, but seconds-scale) that runs regardless of tool traffic — that's what makes capture
  independent of whether anyone's asking.
- **Default ~10–15 s.** ADS-B positions update ~1 Hz, so 10–15 s captures a pass (entry/exit, min/max alt,
  closest approach) richly without hammering a Pi-class feeder (1 s ≈ 86k fetches/day; 15 s ≈ 5.7k). Tunable:
  finer = better closest-approach precision + more load. **`ADSB_POLL_SECONDS=0` disables the poller**
  (pure-stateless, today's behaviour) — keep that escape hatch.

### 2b. Sessionize PASSES (one row per visit, NOT per poll)
- Watermark on `hex`: **open** a pass on first-seen; **update** `last_seen`/`min_alt`/`max_alt`/closest-
  approach (`min_dist`)/`max_rssi` while present; **close + write** when unseen for > a grace window
  (~60–120 s, make it config). A ~6-min visit must be **1 row, not ~180** — else "most common" counts
  poll-samples, not visits, and the DB bloats.

### 2c. Schema (SQLite, bind-mount, ACCUMULATES → back it up; NOT reproducible)
- `passes(hex, callsign, reg, type, first_seen, last_seen, min_alt, max_alt, min_dist, max_rssi, points)`
  + optional `aircraft` dim (`hex → reg/type/operator`; operator filled from AeroAPI once).
- Bind-mount `/data` → `/docker/container-data/adsb-mcp-data` (igepn pattern). **LOCAL disk, not NFS**
  (WAL + network FS don't mix). If you add a `backfill`-style subcommand, beware igepn's **anonymous-volume
  `--rm` trap** (bind `/data` explicitly).

### 2d. Analytical tools (readers over the store)
`most_common_flights`, `aircraft_stats`, `seen_before`, `busiest_times`, `rarest_visitors`. Mind the **three
axes** (each a different question): `hex`/reg = the *airframe* ("seen this tail before?"), `callsign` = the
*route/service* ("CM159 passed 47×"), `t` = the *model* ("60% A320-family"). All to-standard: four hints
(read-only, **openWorld=false** — local store), `ok/refused/error`.

### Deploy coupling (homelab repo — coordinate when 2a–2c land)
`containers/stacks/adsb-mcp/docker-compose.yml` will need: the **`ADSB_POLL_SECONDS` env** and a
**bind-mount volume** `/docker/container-data/adsb-mcp-data:/data` (+ `mkdir -p` it on Yuki). Flag this so
the homelab stack is updated in the same breath.

## Then — "something really interesting" (Daniel, TBD)

Daniel has a further feature in mind for this repo *after* the historical tier. He'll specify it when we get
there — **leave room, don't pre-build.** (Likely builds on the accumulating store, so keep the schema clean
and the poller/reader seam tidy.)

## Standing rules (don't undo)
- **Enrichment** (route / origin-dest / airline / registration-owner) is a **separate, optional, keyed tier
  with openWorld=TRUE tools** (FlightAware AeroAPI / OpenSky) — never mixed into the local tools, which stay
  keyless + openWorld=false.
- **Receiver coordinates = PII from env**, never the repo or a default. The public repo defaults `ADSB_URL`
  to `localhost`; the real feeder host lives in the homelab deploy config (standard §6).
