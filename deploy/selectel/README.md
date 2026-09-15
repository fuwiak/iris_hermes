# Iris Hermes production — Yandex Cloud VDS (`hermes-agent-ai.ru`)

> **2026-09-15:** full cutover from Selectel `iris-hermes` (`185.161.66.162`)
> to Yandex `iris-sasha` (`158.160.195.2`). Selectel VM is **SHUTOFF**;
> DNS A/`www` → Yandex. Paths below still live under `deploy/selectel/`
> (compose project name) for backwards compatibility.

| | |
|--|--|
| Server | Yandex Cloud VDS `iris` / SSH host `iris-sasha` |
| Public IP | `158.160.195.2` |
| Domain | `hermes-agent-ai.ru` |
| DNS | Selectel DNS zone (project «сайт») — A records point at Yandex |
| Stack | Caddy + Hermes + Redis 7 + Postgres 16 |
| App dir | `/opt/iris_hermes` |
| Secrets | `/root/deploy.env` → compose `env_file` |

## DNS

Zone ID `c0985d99-2847-4d1b-a727-a52b15a1a532` (project «сайт»):

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `@` | `158.160.195.2` | 300 |
| A | `www` | `158.160.195.2` | 300 |

NS at registrar: `a/b/c/d.ns.selectel.ru` (unchanged).

## Deploy

GitHub Actions: `.github/workflows/deploy-selectel-iris.yml`

Secrets are still named `SELECTEL_IRIS_*` but **must** target the Yandex VDS:

- `SELECTEL_IRIS_HOST` = `158.160.195.2`
- `SELECTEL_IRIS_USER` = `deploy`
- `SELECTEL_IRIS_SSH_KEY` = `~/.ssh/iris_yandex_deploy` private key
- `SELECTEL_IRIS_DEPLOY_ENV` = `/root/deploy.env` body
- `SELECTEL_IRIS_OPENROUTER_BASE_URL` = Railway telegram-user-egress `/t/<token>/api/v1`
- `SELECTEL_IRIS_OPENROUTER_API_KEY`, Telegram / MoySklad / marketplace tokens as before

Local SSH:

```bash
ssh iris-sasha   # IdentityFile iris_yandex_deploy (passwordless)
```

Manual redeploy on the VDS:

```bash
sudo bash /opt/iris_hermes/deploy/selectel/remote_deploy.sh
docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml ps
```

## Chat smoke (real WS path)

On the VDS (inside the hermes container):

```bash
docker exec -u hermes -w /opt/data selectel-hermes-1 \
  python /tmp/iris_chat_e2e.py --base-url http://127.0.0.1:8080 \
  --question 'What is 2+2? Reply with only the digit.' --expect 4
```

Script: `scripts/iris_chat_e2e.py` (login → ws-ticket → `prompt.submit`).

## OpenRouter / egress

Yandex RU IP cannot dial `openrouter.ai` (HTTP 403). Keep
`OPENROUTER_BASE_URL` on the Railway telegram-user-egress proxy. Boot sync
must keep volume `$HERMES_HOME/.env` BASE_URL in sync with compose
(dotenv `override=True` otherwise reverts to a stale `/t/<token>/` → HTTP 401).

## Selectel (legacy)

- VM name `iris-hermes` / IP `185.161.66.162` — **powered off** after migration.
- Do **not** point DNS back without restarting that VM.
- Volumes were copied to Yandex (`selectel_hermes_data`); Postgres on Selectel
  was empty (sessions live in Hermes volume / SQLite).
