#!/usr/bin/env bash
# Pull latest main and rebuild the Docker stack on the Selectel VDS.
# Progress → stdout (Actions) + /var/log/kinetic-deploy.log + /var/run/kinetic-deploy.status
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/app}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.yml}"
REPO_URL="${REPO_URL:-https://github.com/fuwiak/client_segmentation_deepseek.git}"
BRANCH="${BRANCH:-main}"
PG_IMAGE_MAJOR="${PG_IMAGE_MAJOR:-18}"
PG_VOLUME="${PG_VOLUME:-deploy_pg_data}"
DEPLOY_LOG="${DEPLOY_LOG:-/var/log/kinetic-deploy.log}"
DEPLOY_STATUS="${DEPLOY_STATUS:-/var/run/kinetic-deploy.status}"
TOTAL_STEPS=9
GIT_FETCH_TIMEOUT="${GIT_FETCH_TIMEOUT:-120}"

export PYTHONUNBUFFERED=1
export BUILDKIT_PROGRESS=plain
export COMPOSE_PROGRESS=plain
export GIT_TERMINAL_PROMPT=0

DEPLOY_START=$(date +%s)
mkdir -p "$(dirname "$DEPLOY_LOG")" /var/run
: >>"$DEPLOY_LOG"

ts() { date '+%H:%M:%S'; }
elapsed() { echo "$(( $(date +%s) - DEPLOY_START ))s"; }

log() {
  printf '%s\n' "[$(ts)] $*" | tee -a "$DEPLOY_LOG"
}

status() {
  printf '%s\n' "$*" >"$DEPLOY_STATUS"
  log "STATUS: $*"
}

step() {
  local n="$1"
  shift
  status "STEP ${n}/${TOTAL_STEPS} (+$(elapsed)) $*"
  log "======== STEP ${n}/${TOTAL_STEPS} (+$(elapsed)) $* ========"
}

ok() { log "OK $*"; }
warn() { log "WARN $*"; }
fail() { log "FAIL $*"; status "FAILED: $*"; }

run_git_fetch() {
  log "git fetch (timeout ${GIT_FETCH_TIMEOUT}s)…"
  # Drop stale locks from cancelled deploys.
  rm -f "$APP_DIR/.git/index.lock" "$APP_DIR/.git/HEAD.lock" 2>/dev/null || true
  if command -v timeout >/dev/null 2>&1; then
    timeout "$GIT_FETCH_TIMEOUT" git -C "$APP_DIR" fetch origin --prune
  else
    git -C "$APP_DIR" fetch origin --prune
  fi
}

ensure_env_default() {
  local key="$1" value="$2"
  if ! grep -qE "^${key}=" deploy/.env; then
    printf '%s=%s\n' "$key" "$value" >>deploy/.env
    ok "added default $key"
  fi
}

ensure_postgres_volume_compatible() {
  if ! docker volume inspect "$PG_VOLUME" >/dev/null 2>&1; then
    ok "Postgres volume $PG_VOLUME does not exist yet — will be created fresh"
    return 0
  fi
  local layout need_reset=0
  layout="$(
    docker run --rm -v "${PG_VOLUME}:/pgvol:ro" alpine:3.20 \
      sh -c '
        if [ -f /pgvol/18/docker/PG_VERSION ]; then echo "ok18:$(cat /pgvol/18/docker/PG_VERSION)"
        elif [ -f /pgvol/*/docker/PG_VERSION ]; then
          v=$(cat /pgvol/*/docker/PG_VERSION 2>/dev/null | head -1 | tr -d "[:space:]")
          echo "okmaj:$v"
        elif [ -f /pgvol/data/PG_VERSION ]; then echo "legacy:$(cat /pgvol/data/PG_VERSION)"
        elif [ -f /pgvol/PG_VERSION ]; then echo "legacy:$(cat /pgvol/PG_VERSION)"
        else echo "empty"
        fi
      ' | tr -d "[:space:]"
  )"
  log "Postgres volume layout=$layout image_major=$PG_IMAGE_MAJOR"
  case "$layout" in
    empty) return 0 ;;
    ok18:"$PG_IMAGE_MAJOR") return 0 ;;
    okmaj:"$PG_IMAGE_MAJOR") return 0 ;;
    legacy:*|ok18:*|okmaj:*) need_reset=1 ;;
    *) need_reset=1 ;;
  esac
  if [[ "$need_reset" -eq 1 ]]; then
    warn "resetting incompatible postgres volume ($layout)"
    docker compose -f "$COMPOSE_FILE" stop web postgres 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" rm -f postgres 2>/dev/null || true
    docker volume rm "$PG_VOLUME"
  fi
}

wait_postgres_healthy() {
  local i st
  for i in $(seq 1 36); do
    st="$(docker inspect -f '{{.State.Health.Status}}' deploy-postgres-1 2>/dev/null || echo missing)"
    log "postgres health: $st ($i/36)"
    status "WAIT_PG $st ($i/36)"
    if [[ "$st" == "healthy" ]]; then
      return 0
    fi
    if [[ "$st" == "unhealthy" ]]; then
      docker logs deploy-postgres-1 --tail 40 || true
      return 1
    fi
    sleep 5
  done
  docker logs deploy-postgres-1 --tail 40 || true
  return 1
}

maybe_restore_dump() {
  local dump=/opt/migrate/rail.dump count rows pgpass
  if [[ ! -f "$dump" ]]; then
    ok "no dump at $dump — skip restore"
    return 0
  fi
  count="$(
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
      psql -U app -d app -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='customers';" \
      2>/dev/null || echo 0
  )"
  count="$(echo "$count" | tr -d '[:space:]')"
  if [[ "$count" == "1" ]]; then
    rows="$(
      docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U app -d app -Atc "SELECT count(*) FROM customers;" 2>/dev/null || echo 0
    )"
    rows="$(echo "$rows" | tr -d '[:space:]')"
    if [[ "${rows:-0}" -gt 100 ]]; then
      ok "DB already has $rows customers — skip dump restore"
      return 0
    fi
  fi
  log "Restoring $dump into postgres..."
  pgpass="$(grep -E '^POSTGRES_PASSWORD=' deploy/.env | cut -d= -f2-)"
  docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql -U app -d app -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO app;"
  docker run --rm --network deploy_default \
    -v /opt/migrate:/out:ro \
    -e PGPASSWORD="$pgpass" \
    "postgres:${PG_IMAGE_MAJOR}-alpine" \
    pg_restore --no-owner --no-acl -d "postgresql://app:${pgpass}@postgres:5432/app" /out/rail.dump \
    || warn "pg_restore finished with warnings/errors (often OK for partial objects)"
}

log "############################################################"
log "SELECTEL DEPLOY START"
log "log=$DEPLOY_LOG status=$DEPLOY_STATUS app=$APP_DIR"
log "############################################################"
status "START"

# Serialize deploys on this host. Two overlapping runs (e.g. a workflow_dispatch
# racing a push, or a process orphaned by a cancelled Actions job before
# concurrency.cancel-in-progress was turned off) used to git reset --hard and
# docker build at the same time, pegging the VDS until a fresh SSH session
# couldn't even get scheduled long enough to print its first echo. Wait for
# any prior instance to finish instead of racing it.
exec 200>/var/run/kinetic-deploy.lock
if ! flock -n 200; then
  log "another deploy is already running on this host — waiting for it to finish…"
  status "WAITING_FOR_PRIOR_DEPLOY"
  flock -w 1200 200 || {
    fail "timed out after 20m waiting for prior deploy's lock"
    exit 1
  }
  log "prior deploy released the lock — proceeding"
fi

mkdir -p "$(dirname "$APP_DIR")"

step 1 "Sync git repo"
if [[ ! -d "$APP_DIR/.git" ]]; then
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
run_git_fetch
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
chmod +x deploy/remote_deploy.sh
ok "HEAD=$(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"

step 2 "Prepare env (AUTH + secrets)"
mkdir -p deploy
if [[ -f /root/deploy.env ]]; then
  cp /root/deploy.env deploy/.env
fi
if [[ ! -f deploy/.env ]]; then
  fail "deploy/.env missing (copy from /root/deploy.env)"
  exit 1
fi
ensure_env_default AUTH_ENABLED true
ensure_env_default AUTH_USERNAME admin
ensure_env_default AUTH_PASSWORD admin
ensure_env_default AUTH_SECRET_KEY iris-crm-session-secret
if ! grep -qE '^POSTGRES_PASSWORD=.+' deploy/.env; then
  fail "POSTGRES_PASSWORD missing/empty in deploy/.env"
  exit 1
fi
cp deploy/.env /root/deploy.env
ok "env ready ($(wc -l < deploy/.env) keys)"

step 3 "Host firewall (22/80/443)"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null 2>&1 || true
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
  ufw status verbose || true
elif command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 80 -j ACCEPT
  iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
  ok "iptables rules for 80/443"
else
  warn "no ufw/iptables — relying on Selectel security group"
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  fail "$COMPOSE_FILE not found"
  exit 1
fi

step 4 "Check Postgres volume layout"
ensure_postgres_volume_compatible

step 5 "Start redis + postgres"
log "Deploying $(git log -1 --oneline)"
docker compose -f "$COMPOSE_FILE" up -d redis postgres
ok "infra containers started"

step 6 "Wait for Postgres healthy"
if ! wait_postgres_healthy; then
  fail "postgres did not become healthy"
  exit 1
fi
ok "postgres healthy"

step 7 "Optional DB dump restore"
maybe_restore_dump

step 8 "Build & start web + caddy"
log "docker compose build — plain progress…"
docker compose -f "$COMPOSE_FILE" build --progress=plain web
ok "web image built"
status "BUILD_DONE starting containers"
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
ok "stack up"

step 9 "Health checks"
sleep 3
docker compose -f "$COMPOSE_FILE" ps
log "--- listening ports ---"
ss -lntp 2>/dev/null | grep -E ':80|:443' || netstat -lntp 2>/dev/null | grep -E ':80|:443' || true
log "--- curl web (compose network) ---"
docker run --rm --network deploy_default curlimages/curl:8.5.0 \
  -fsS -m 10 http://web:8000/health || true
echo
log "--- curl caddy ---"
curl -fsS -m 10 -H 'Host: kinetic-ai.ru' http://127.0.0.1/health || true
echo
ok "health checks done"

status "DEPLOY_OK $(git rev-parse --short HEAD) total=$(elapsed)"
log "############################################################"
log "DEPLOY_OK $(git rev-parse --short HEAD)  total=$(elapsed)"
log "full log: $DEPLOY_LOG"
log "############################################################"
