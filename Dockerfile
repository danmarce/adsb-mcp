# adsb-mcp — thin MCP proxy over a local ADS-B receiver's aircraft.json.
#   serve: adsb-mcp serve --transport http --host 0.0.0.0   (ADSB_MCP_TOKEN set; ADSB_URL -> the feeder;
#   ADSB_SITE_LAT/LON from 1Password for nearest-first "overhead")
# Stateless; holds no key (the feeder is keyless). Returns live structured flight data.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock LICENSE README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status!=200)"
ENTRYPOINT ["adsb-mcp"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
