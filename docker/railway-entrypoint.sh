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

# Persist MoySklad secrets from Railway-injected process env into the volume
# .env when missing. Hermes loads $HERMES_HOME/.env; service vars alone are
# enough for os.environ, but tools/UI that read the file need the line present.
if [ -n "${MOYSKLAD_API_TOKEN:-}" ] && [ -x /opt/hermes/.venv/bin/python ]; then
  /opt/hermes/.venv/bin/python /opt/hermes/scripts/sync_moysklad_env_from_railway.py \
    --prefer-process-env \
    --env-file "$HERMES_HOME/.env" \
    >/dev/null 2>&1 || true
fi

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
