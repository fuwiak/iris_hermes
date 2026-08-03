# Slim Selectel image — uses prebuilt hermes_cli/web_dist from the repo
# (no in-image npm build). Faster cold deploys on VDS.

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_HOME=/opt/data \
    HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist \
    PATH="/opt/hermes/.venv/bin:/root/.local/bin:${PATH}" \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=/usr/local/bin/python3.13

WORKDIR /opt/hermes

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/0.11.6/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv

COPY . .
RUN test -f /opt/hermes/hermes_cli/web_dist/index.html \
    || (echo "FATAL: hermes_cli/web_dist missing — run web build before image build" >&2; exit 1)

RUN chmod +x /opt/hermes/docker/railway-entrypoint.sh \
    && uv venv /opt/hermes/.venv \
    && uv pip install --no-cache-dir -e ".[web,mcp]" \
    && mkdir -p /opt/data \
    && useradd --system --uid 10000 --home-dir /opt/data --shell /usr/sbin/nologin hermes \
    && chown -R hermes:hermes /opt/data /opt/hermes

EXPOSE 8080

USER hermes
WORKDIR /opt/data
ENTRYPOINT ["/opt/hermes/docker/railway-entrypoint.sh"]
