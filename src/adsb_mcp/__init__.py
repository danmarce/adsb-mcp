"""adsb-mcp: local ADS-B flight data (your receiver's aircraft.json) exposed as MCP tools."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import Settings

log = logging.getLogger("adsb_mcp")


async def _serve(settings: Settings, transport: str, host: str, port: int) -> None:
    from .server import BearerAuth, build_server

    mcp = build_server(settings)
    if transport == "stdio":
        await mcp.run_stdio_async()
    else:
        import uvicorn

        if not settings.http_token and host not in ("127.0.0.1", "localhost", "::1"):
            log.warning("serving on %s without ADSB_MCP_TOKEN - anyone on the network can call it", host)
        app = BearerAuth(mcp.streamable_http_app(host=host), settings)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()


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
