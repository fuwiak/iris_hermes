#!/usr/bin/env bash
# Bootstrap Iris Hermes on a fresh Selectel VDS (cloud-init / first boot).
# Expects repo already at /opt/iris_hermes OR clones from REPO_URL.
set -euo pipefail

DOMAIN="${DOMAIN:-bettercallbober.ru}"
REPO_URL="${REPO_URL:-https://github.com/fuwiak/iris_hermes.git}"
APP_DIR="${APP_DIR:-/opt/iris_hermes}"
DEPLOY_DIR="${APP_DIR}/deploy/selectel"
LOG=/var/log/iris-hermes-deploy.log

exec > >(tee -a "$LOG") 2>&1
echo "=== iris deploy $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git ufw
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

mkdir -p /opt/data
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch --depth 1 origin main || true
  git -C "$APP_DIR" reset --hard origin/main || true
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
docker compose up -d

# Basic health
sleep 5
docker compose ps
curl -fsS -H "Host: $DOMAIN" http://127.0.0.1/ || true
echo "=== done ==="
