# Selectel deploy — Iris Hermes (`bettercallbober.ru`)

New VDS (option 3 — does **not** touch `kinetic-ai.ru` / `kinetic-prod`).

| | |
|--|--|
| Server | `iris-hermes` (Selectel cloud, region `ru-7`) |
| Public IP | `185.161.66.162` |
| Domain | `bettercallbober.ru` → point **A** record here |
| Stack | Caddy + Hermes (Dockerfile.railway) + Redis 7 + Postgres 16 |
| App dir | `/opt/iris_hermes` |
| Secrets | `/root/deploy.env` → `deploy/selectel/.env` |

## DNS

`bettercallbober.ru` currently resolves to OVH (`141.94.95.99`). Update the apex **A** record to `185.161.66.162` (and `www` CNAME/A as needed). Selectel Domains API for this account returned errors; change DNS where the zone is actually hosted (OVH panel if NS are OVH).

## Deploy

GitHub Actions: `.github/workflows/deploy-selectel-iris.yml`

Secrets:

- `SELECTEL_IRIS_HOST`
- `SELECTEL_IRIS_USER`
- `SELECTEL_IRIS_SSH_KEY`
- `SELECTEL_IRIS_DEPLOY_ENV` (full `/root/deploy.env` body)

Manual on VDS:

```bash
# /root/deploy.env must exist
bash /opt/iris_hermes/deploy/selectel/remote_deploy.sh
docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml ps
curl -fsS -H 'Host: bettercallbober.ru' http://127.0.0.1/
```

## Create VDS (OpenStack / Selectel API)

Already provisioned once via Keystone + Nova (`ru-7`):

- flavor `VDS1.4-8192-80` (4 vCPU / 8 GB / 80 GB)
- image Ubuntu 24.04 LTS
- network `vds-net` + floating IP
- keypair `bober-selectel`
- security group `iris-web` (22/80/443)

Kinetic CRM (`kinetic-prod` / `155.212.181.116`) left untouched.
