#!/usr/bin/env bash
# Bootstrap Iris Hermes on a fresh Selectel VDS (cloud-init / first boot).
# Expects repo already at /opt/iris_hermes OR clones from REPO_URL.
set -euo pipefail

DOMAIN="${DOMAIN:-hermes-agent-ai.ru}"
REPO_URL="${REPO_URL:-https://github.com/fuwiak/iris_hermes.git}"
APP_DIR="${APP_DIR:-/opt/iris_hermes}"
DEPLOY_DIR="${APP_DIR}/deploy/selectel"
LOG=/var/log/iris-hermes-deploy.log

exec > >(tee -a "$LOG") 2>&1
echo "=== iris deploy $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

export DEBIAN_FRONTEND=noninteractive
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Selectel images pin http://mirror.selectel.ru/ubuntu — IPv6 ENETUNREACH (101)
# and apt then claims "no longer has a Release file". Rewrite to archive.ubuntu.com
# and force IPv4 before the first apt-get. CI rsyncs this tree before we run.
if [ -f "$REPO_ROOT/scripts/rewrite_selectel_apt_mirrors.py" ]; then
  python3 "$REPO_ROOT/scripts/rewrite_selectel_apt_mirrors.py" || true
fi

host_tools_ok() {
  command -v curl >/dev/null 2>&1 && command -v git >/dev/null 2>&1 \
    && command -v docker >/dev/null 2>&1
}

if apt-get -o Acquire::ForceIPv4=true update -y; then
  apt-get -o Acquire::ForceIPv4=true install -y ca-certificates curl git ufw
elif host_tools_ok; then
  echo "WARN: apt-get update failed; curl/git/docker already present — skipping apt"
else
  echo "FATAL: apt-get update failed and host tools are missing" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

mkdir -p /opt/data
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin main || true
  git -C "$APP_DIR" reset --hard origin/main || true
elif [ -f "$APP_DIR/deploy/selectel/docker-compose.yml" ]; then
  # CI rsyncs the tree without .git — use the synced checkout as-is.
  echo "Using existing app tree at $APP_DIR (no .git)"
elif [ -e "$APP_DIR" ]; then
  echo "FATAL: $APP_DIR exists but is not a git checkout and has no deploy/selectel" >&2
  exit 1
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

mkdir -p "$DEPLOY_DIR"
if [ -f /root/deploy.env ]; then
  cp /root/deploy.env "$DEPLOY_DIR/.env"
elif [ ! -f "$DEPLOY_DIR/.env" ]; then
  echo "FATAL: missing /root/deploy.env (and $DEPLOY_DIR/.env)" >&2
  exit 1
fi

# Ensure postgres password exists
if ! grep -q '^POSTGRES_PASSWORD=.\+' "$DEPLOY_DIR/.env"; then
  PW=$(openssl rand -hex 24)
  echo "POSTGRES_PASSWORD=$PW" >> "$DEPLOY_DIR/.env"
fi

cd "$DEPLOY_DIR"
docker compose pull || true
docker compose build hermes
# Force recreate hermes so compose env_file + entrypoint volume .env sync
# pick up rotated OPENROUTER_API_KEY (plain `up -d` can leave a stale container).
docker compose up -d --force-recreate hermes
docker compose up -d

# The /opt/data volume survives every deploy. A stale user-plugin copy at
# /opt/data/plugins/moysklad SHADOWS the freshly baked bundled plugin
# (user source wins in the dashboard plugin scan), so pushes stop being
# visible in the UI. The moysklad plugin ships with the repo — a volume
# copy is never intentional. Remove it and show what the server will serve.
docker exec selectel-hermes-1 sh -c '
  if [ -d /opt/data/plugins/moysklad ]; then
    echo "!! removing stale shadow copy /opt/data/plugins/moysklad"
    rm -rf /opt/data/plugins/moysklad
  fi
  echo "user plugins on volume:"; ls /opt/data/plugins 2>/dev/null || echo "(none)"
  echo "bundled moysklad manifest version:"
  grep -o "\"version\": \"[^\"]*\"" /opt/hermes/plugins/moysklad/dashboard/manifest.json 2>/dev/null \
    || find /opt -name manifest.json -path "*moysklad*" -exec grep -o "\"version\": \"[^\"]*\"" {} \; 2>/dev/null | head -2
' || true

# Persist Telegram Desktop export into the hermes volume when present on host.
# Place the file at /var/lib/iris/telegram_export.json (outside rsync --delete).
EXPORT_SRC="${TELEGRAM_EXPORT_SRC:-/var/lib/iris/telegram_export.json}"
if [ -f "$EXPORT_SRC" ]; then
  echo "Installing Telegram export into hermes volume from $EXPORT_SRC"
  docker cp "$EXPORT_SRC" selectel-hermes-1:/opt/data/moysklad/telegram_export.json
  docker exec -u root selectel-hermes-1 \
    chown hermes:hermes /opt/data/moysklad/telegram_export.json || true
fi

# Basic health
sleep 5
docker compose ps
curl -fsS -H "Host: $DOMAIN" http://127.0.0.1/ || true
echo "=== done ==="
