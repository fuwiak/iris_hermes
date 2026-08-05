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
- seeds chat model **`deepseek/deepseek-v4-flash-0731`** + `agent.reasoning_effort: medium` when unset (UI: Deepseek V4 Flash 0731 · Med)
- syncs `MOYSKLAD_*` and `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` from
  process env into `$HERMES_HOME/.env` (volume). Required because Hermes loads
  the volume `.env` with `override=True` — a stale OpenRouter key otherwise
  survives after you rotate `deploy.env`.
- optional Telegram Business outreach keys:
  `MOYSKLAD_TELEGRAM_BOT_TOKEN`, `MOYSKLAD_TELEGRAM_BOT_USERNAME`,
  `MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID` (GitHub secrets
  `SELECTEL_IRIS_MOYSKLAD_TELEGRAM_*`, patched into `/root/deploy.env` on deploy).

Require `MOYSKLAD_API_TOKEN` in `/root/deploy.env`. `MOYSKLAD_ENABLED` alone does not enable the Hermes plugin.

### OpenRouter `Access denied by security policy` (HTTP 403)

This is **OpenRouter’s API** rejecting the request — not Hermes dashboard auth,
CSRF, Caddy, or Cloudflare. Common causes on this VDS:

1. **RU / Selectel egress IP** (`185.161.66.162`, region `ru-7`) blocked by
   OpenRouter security policy (most likely when the key works from a laptop
   outside RU but fails from the server).
2. Stale / revoked `OPENROUTER_API_KEY` in the Hermes volume `.env`.
3. Account-level ban on the OpenRouter key.

**Preferred fix for (1) — Railway egress fallback** (Hermes stays on Selectel;
only LLM traffic leaves via Railway’s non-RU IP):

1. Deploy `deploy/openrouter-egress/` as a Railway service (see that README).
2. Set GitHub secrets:
   - `SELECTEL_IRIS_OPENROUTER_BASE_URL` =
     `https://<railway-host>/t/<EGRESS_TOKEN>/api/v1`
   - `SELECTEL_IRIS_OPENROUTER_API_KEY` (working key)
3. Push to `main` (deploy patches `/root/deploy.env`; entrypoint syncs volume
   `.env` **and** rewrites `config.yaml` `model.base_url` to the egress URL).

`OPENROUTER_BASE_URL` always wins over a stale `model.base_url:
https://openrouter.ai/api/v1` on the volume.

Other fixes:

1. Rotate key via `SELECTEL_IRIS_OPENROUTER_API_KEY` + push / recreate hermes.
2. Edit `/root/deploy.env` on the VDS, then
   `docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml up -d --force-recreate hermes`
   (plain `up -d` can leave a stale container env / volume key).
3. Switch to native DeepSeek (`DEEPSEEK_API_KEY` + `hermes model`) — no OpenRouter.

Updating only a local laptop `.env` does **not** fix production.
Laptop VPN does **not** change the Selectel server’s egress IP.

## Deploy

GitHub Actions: `.github/workflows/deploy-selectel-iris.yml`

On this Iris fork, **only Selectel deploy auto-runs on every push to `main`**.
Heavy CI / lint / JS tests / Docker publish / docs / autofix are
`workflow_dispatch` only (they were burning Actions minutes and blocking deploy).

Secrets:

- `SELECTEL_IRIS_HOST`
- `SELECTEL_IRIS_USER`
- `SELECTEL_IRIS_SSH_KEY`
- `SELECTEL_IRIS_DEPLOY_ENV` (full `/root/deploy.env` body)
- `SELECTEL_IRIS_OPENROUTER_API_KEY` (optional; patches OpenRouter key on each deploy)
- `SELECTEL_IRIS_OPENROUTER_BASE_URL` (optional; Railway egress proxy — see `deploy/openrouter-egress/`)
- `SELECTEL_IRIS_MOYSKLAD_TELEGRAM_BOT_TOKEN` (optional; Business bot for Рассылки)
- `SELECTEL_IRIS_MOYSKLAD_TELEGRAM_BOT_USERNAME` (optional)
- `SELECTEL_IRIS_MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID` (optional)

Manual on VDS:

```bash
bash /opt/iris_hermes/deploy/selectel/remote_deploy.sh
docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml ps
curl -fsS -H 'Host: hermes-agent-ai.ru' http://127.0.0.1/
```

Kinetic CRM (`kinetic-prod` / `155.212.181.116`) left untouched.
