#!/bin/sh
# Railway temporary entrypoint: bind dashboard to $PORT (Railway injects it).
set -e

export HOME="${HERMES_HOME:-/opt/data}"
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export HERMES_WEB_DIST="${HERMES_WEB_DIST:-/opt/hermes/hermes_cli/web_dist}"
# venv activate references $OSTYPE; keep nounset off around it.
export OSTYPE="${OSTYPE:-linux-gnu}"

PORT="${PORT:-${HERMES_DASHBOARD_PORT:-8080}}"
HOST="${HERMES_DASHBOARD_HOST:-0.0.0.0}"

cd "$HERMES_HOME"

# Prefer absolute binary — avoids relying on activate under dash + set -u.
if [ -x /opt/hermes/.venv/bin/hermes ]; then
  exec /opt/hermes/.venv/bin/hermes dashboard \
    --host "$HOST" \
    --port "$PORT" \
    --no-open \
    --skip-build
fi

# shellcheck disable=SC1091
. /opt/hermes/.venv/bin/activate
exec hermes dashboard \
  --host "$HOST" \
  --port "$PORT" \
  --no-open \
  --skip-build
