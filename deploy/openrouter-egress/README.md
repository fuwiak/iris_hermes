# OpenRouter egress (Railway)

Selectel VDS is in **Russia (`ru-7`)**. OpenRouter often returns:

```text
HTTP 403  {"success": false, "error": "Access denied by security policy."}
```

when the **source IP** is a RU datacenter (or a flagged VPN/proxy). Hermes on
Selectel keeps serving the UI; only the **LLM egress** moves to Railway
(US/EU IP).

```
Browser → Selectel Hermes → Railway egress → openrouter.ai
                              ↑
                         egress IP (not RU)
```

## Deploy on Railway

1. New Railway service, **Root Directory** = `deploy/openrouter-egress`.
2. Variable `EGRESS_TOKEN` = long random secret (`openssl rand -hex 24`).
3. Deploy; note public HTTPS URL, e.g. `https://openrouter-egress-xxx.up.railway.app`.

Health: `GET /healthz` → `ok`.

## Point Selectel at it

In `/root/deploy.env` (or GitHub secret `SELECTEL_IRIS_OPENROUTER_BASE_URL`):

```bash
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter-egress-xxx.up.railway.app/t/<EGRESS_TOKEN>/api/v1
```

Then recreate Hermes:

```bash
docker compose -f /opt/iris_hermes/deploy/selectel/docker-compose.yml \
  up -d --force-recreate hermes
```

Entrypoint syncs `OPENROUTER_BASE_URL` into the volume `.env`.

## Auth model

- Path `/t/<EGRESS_TOKEN>/…` is the shared secret (not a public open proxy).
- `Authorization: Bearer <OPENROUTER_API_KEY>` is forwarded unchanged.
- OpenRouter still bills **your** key; Railway only changes the egress IP.

## Alternatives

| Approach | When |
|----------|------|
| This egress | Need OpenRouter models from Selectel RU |
| Native DeepSeek (`DEEPSEEK_API_KEY` + `hermes model`) | OpenRouter not required |
| Rotate key only | 403 is account/key ban, not geo |
