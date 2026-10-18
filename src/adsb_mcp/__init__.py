"""adsb-mcp: local ADS-B flight data (your receiver's aircraft.json) exposed as MCP tools."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import Settings

log = logging.getLogger("adsb_mcp")


async def _poll_loop(settings: Settings, snapshots) -> None:
    from .passes import open_tracker, poll_once

    tracker = open_tracker(settings)
    log.info("recording passes every %ss (radius %s km from the %s; arrivals %s)", settings.poll_seconds,
             settings.pass_radius_km, tracker.ref_kind, "on" if settings.arrivals_enabled else "off")
    while True:
        try:
            await poll_once(snapshots, tracker)
        except Exception:  # keep serving even if a recorder tick blows up
            log.exception("recorder tick failed")
        await asyncio.sleep(settings.poll_seconds)


async def _enrich_loop(settings: Settings) -> None:
    from .enrich import ENRICH_EVERY_S, OpenSky, enrich_once
    from .store import connect

    conn = connect(settings.db_path)
    client = OpenSky(settings)
    log.info("OpenSky enrichment on: yesterday's passes, at most one call per airframe per day "
             "(cap %d credits/day, reserve %d)", settings.opensky_daily_credits, settings.opensky_reserve)
    await asyncio.sleep(60)
    while True:
        try:
            await enrich_once(settings, conn, client)
        except Exception:  # keep serving even if an enrichment run blows up
            log.exception("enrichment run failed")
        await asyncio.sleep(ENRICH_EVERY_S)


async def _serve(settings: Settings, transport: str, host: str, port: int) -> None:
    from .board import Board
    from .feeder import FeederClient, Snapshots
    from .server import BearerAuth, build_server

    recording = settings.poll_seconds > 0
    if recording and settings.reference is None:
        log.warning("ADSB_POLL_SECONDS is set but there is no reference point (ADSB_SITE_LAT/LON or "
                    "ADSB_AIRPORT_LAT/LON): passes can't be tracked, recorder stays off")
        recording = False
    poll_s = max(1.0, settings.poll_seconds) if recording else 0
    # While recording, the live tools reuse the recorder's snapshot if it's at most ~2 polls old.
    snapshots = Snapshots(FeederClient(settings), max_age_s=2 * poll_s)
    mcp = build_server(settings, snapshots)
    tasks = []
    if recording:
        from dataclasses import replace

        tasks.append(asyncio.create_task(_poll_loop(replace(settings, poll_seconds=poll_s), snapshots)))
        if settings.enrichment_enabled:
            tasks.append(asyncio.create_task(_enrich_loop(settings)))
    elif settings.enrichment_enabled:
        log.warning("OpenSky credentials are set but the recorder is off: enrichment needs recorded passes")

    if transport == "stdio":
        await mcp.run_stdio_async()
    else:
        import uvicorn

        if not settings.http_token and host not in ("127.0.0.1", "localhost", "::1"):
            log.warning("serving on %s without ADSB_MCP_TOKEN - anyone on the network can call it", host)
        board = Board(settings, snapshots) if settings.board in ("open", "token") else None
        if board and settings.airport is None:
            log.warning("ADSB_BOARD is %s but no airport is set: /board.json will refuse", settings.board)
        app = BearerAuth(mcp.streamable_http_app(host=host), settings, board)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
    for task in tasks:
        task.cancel()


def main() -> None:
    # Use the OS trust store so a LAN TLS-inspecting middlebox doesn't break outbound HTTPS (if the feeder
    # URL is ever HTTPS). The default feeder is plain-http on the LAN.
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - truststore is best-effort
        pass

    parser = argparse.ArgumentParser(prog="adsb-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the MCP server")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    # stderr only: stdout is the MCP channel in stdio mode
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    asyncio.run(_serve(Settings(), args.transport, args.host, args.port))
