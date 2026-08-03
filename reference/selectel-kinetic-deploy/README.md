# Selectel deploy — snapshot from `client_segmentation_deepseek`

Copied from `/Users/user/client_segmentation_deepseek` (Kinetic CRM / Iris CRM).
**Reference only** — not wired into Hermes runtime.

Source repo: `https://github.com/fuwiak/client_segmentation_deepseek.git`  
Prod domain: `https://kinetic-ai.ru`  
VDS app dir: `/opt/app`

## Flow

```
push main
  → .github/workflows/deploy-selectel.yml
  → SSH (SELECTEL_HOST / SELECTEL_USER / SELECTEL_SSH_KEY)
  → git sync /opt/app
  → deploy/remote_deploy.sh
  → docker compose -f deploy/docker-compose.yml
  → Caddy :80/:443 → web:8000
```

Railway (`railway.toml.deprecated`) is legacy; DNS does **not** point there.

## Where env comes from

| File | Role |
|------|------|
| `/root/deploy.env` on VDS | Canonical prod secrets |
| `deploy/.env` (on VDS = copy of above) | Loaded by compose `env_file` |
| Local `root.env` (this snapshot) | Selectel IAM/API + AUTH (dev/local) |
| Local `deploy.env` (this snapshot) | Full app secrets used on VDS |

`remote_deploy.sh` step 2:

1. If `/root/deploy.env` exists → copy to `deploy/.env`
2. Fail if `deploy/.env` missing
3. Ensure `AUTH_*` defaults
4. Require non-empty `POSTGRES_PASSWORD`
5. Mirror `deploy/.env` back to `/root/deploy.env`

Compose injects (not in `.env`):

- `DATABASE_URL=postgresql://app:${POSTGRES_PASSWORD}@postgres:5432/app`
- `REDIS_URL=redis://redis:6379/0`
- `DB_PERSIST_ENABLED=true`
- AI concurrency caps for 4GB VDS

## GitHub secrets (Actions)

- `SELECTEL_HOST`
- `SELECTEL_USER`
- `SELECTEL_SSH_KEY`

## VDS paths

- App: `/opt/app`
- Secrets: `/root/deploy.env`
- Deploy log: `/var/log/kinetic-deploy.log`
- Status: `/var/run/kinetic-deploy.status`
- Optional DB dump: `/opt/migrate/rail.dump`

## Layout in this folder

```
reference/selectel-kinetic-deploy/
├── README.md
├── root.env                 # SECRETS — gitignored (Selectel API + AUTH)
├── deploy.env               # SECRETS — gitignored (prod app env)
├── env.example              # Safe template
├── railway.toml.deprecated
├── .github/workflows/deploy-selectel.yml
└── deploy/
    ├── .env                 # SECRETS — gitignored (same as deploy.env)
    ├── Caddyfile            # kinetic-ai.ru → web:8000
    ├── Dockerfile
    ├── docker-compose.yml   # redis + postgres18 + web + caddy
    ├── entrypoint.sh
    └── remote_deploy.sh
```

## Stack

- `redis:7-alpine` (256m)
- `postgres:18-alpine` (768m, user/db `app`)
- `web` — build from `deploy/Dockerfile`, 1536m, port 8000 internal
- `caddy:2-alpine` — 80/443 public

## Ops

```bash
# On VDS
tail -f /var/log/kinetic-deploy.log
cat /var/run/kinetic-deploy.status
curl -fsS https://kinetic-ai.ru/health
```
