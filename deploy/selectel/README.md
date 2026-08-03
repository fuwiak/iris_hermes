# Selectel deploy — Iris Hermes (`hermes-agent-ai.ru`)

New VDS (option 3 — does **not** touch `kinetic-ai.ru` / `kinetic-prod`).

| | |
|--|--|
| Server | `iris-hermes` (Selectel cloud, region `ru-7`) |
| Public IP | `185.161.66.162` |
| Domain | `hermes-agent-ai.ru` |
| DNS zone ID | `c0985d99-2847-4d1b-a727-a52b15a1a532` (project «сайт») |
| Stack | Caddy + Hermes + Redis 7 + Postgres 16 |
| App dir | `/opt/iris_hermes` |
| Secrets | `/root/deploy.env` → `deploy/selectel/.env` |

## DNS

Zone is in project «сайт». A records already set via API:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `@` | `185.161.66.162` | 300 |
| A | `www` | `185.161.66.162` | 300 |

If the panel says **not delegated**, set NS at the registrar to:

`a.ns.selectel.ru`, `b.ns.selectel.ru`, `c.ns.selectel.ru`, `d.ns.selectel.ru`

Then wait for propagation and enable HTTPS in Caddy (remove `auto_https disable_redirects`).

## Iris defaults

Entrypoint (`docker/railway-entrypoint.sh`) on every boot:

- sets `dashboard.theme` / `display.skin` to **iris** when missing or `mono`/`default`
- ensures `plugins.enabled` includes **moysklad** (unless explicitly disabled)
- syncs `MOYSKLAD_API_TOKEN` from process env into `$HERMES_HOME/.env`

Require `MOYSKLAD_API_TOKEN` in `/root/deploy.env`. `MOYSKLAD_ENABLED` alone does not enable the Hermes plugin.

## Deploy

GitHub Actions: `.github/workflows/deploy-selectel-iris.yml`

Secrets:

- `SELECTEL_IRIS_HOST`
- `SELECTEL_IRIS_USER`
- `SELECTEL_IRIS_SSH_KEY`
- `SELECTEL_IRIS_DEPLOY_ENV` (full `/root/deploy.env` body)

Manual on VDS:

```bash
bash /opt/iris_hermes/deploy/selectel/remote_deploy.sh
docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml ps
curl -fsS -H 'Host: hermes-agent-ai.ru' http://127.0.0.1/
```

Kinetic CRM (`kinetic-prod` / `155.212.181.116`) left untouched.
