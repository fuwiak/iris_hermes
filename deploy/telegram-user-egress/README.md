# Telegram user (MTProto) egress — Railway

Selectel RU cannot open Telegram DCs. This service runs Telethon on a non-RU
Railway egress IP. Hermes on Selectel calls it over HTTPS.

## Deploy

Repo root `railway.toml` points at OpenRouter egress — do **not** `railway up`
from the monorepo root for this service.

```bash
# from repo root
railway link -p 52e19c85-2903-4e56-a969-35dcc63ae21f -e production
railway add --service telegram-user-egress
railway service link telegram-user-egress
railway variables set EGRESS_TOKEN=$(openssl rand -hex 24)
railway volume add -m /data
railway up deploy/telegram-user-egress --path-as-root -d -y -s telegram-user-egress
railway domain  # https://telegram-user-egress-….up.railway.app
```

## Point Selectel Hermes

In `/root/deploy.env` (or GitHub secret `SELECTEL_IRIS_TELEGRAM_USER_GATEWAY_URL`):

```bash
TELEGRAM_USER_GATEWAY_URL=https://<host>/t/<EGRESS_TOKEN>
```

Then recreate hermes. Login / contacts / send go through Railway.

## Auth

- Path prefix `/t/<EGRESS_TOKEN>/…`, or
- `Authorization: Bearer <EGRESS_TOKEN>`

Health: `GET /healthz` → `ok`.
