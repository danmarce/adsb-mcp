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
- **`aircraft` dimension table** — `hex → reg/type/operator`, operator filled once from enrichment.
- **Enrichment tier** (route / origin-dest / airline / registration-owner) — a separate, optional, keyed
  tier with **openWorld=TRUE** tools (FlightAware AeroAPI / OpenSky). Never mixed into the local tools.
- **Retention / backup tooling** — the store accumulates; a `backup` subcommand (SQLite online backup) may
  be worth it. If you add one-shot subcommands run via `docker run --rm`, bind `/data` explicitly (the
  image declares `VOLUME /data`; an unbound `--rm` run writes to a throwaway anonymous volume).

## Then — "something really interesting" (Daniel, TBD)

Daniel has a further feature in mind *after* the historical tier. He'll specify it — **leave room, don't
pre-build.** It likely builds on the store, so keep the schema clean and the recorder/reader seam tidy.
