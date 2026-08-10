# flowwow plugin

Opt-in standalone plugin: 3 read tools over the Flowwow seller API. Same
pattern as `moysklad` (`client.py` sync HTTP client, `tools.py` schemas +
handlers, `plugin.yaml` manifest).

## Quick start

```bash
# 1. Token: Flowwow → личный кабинет продавца → API / интеграции
# 2. Put in ~/.hermes/.env
FLOWWOW_API_TOKEN=your_token_here
# optional, only if the default base URL 404s for your account:
# FLOWWOW_API_URL=https://api.flowwow.com/v1

# 3. Enable plugin + toolset
hermes plugins enable flowwow
hermes tools   # ensure «flowwow» is on for CLI / messaging / api_server

# 4. Verify
hermes plugins list | grep flowwow   # expect: enabled
# In chat: ask Hermes to call flowwow_health
```

## ⚠️ Endpoints are unverified

`client.py` targets `/orders` and `/clients` under the seller-API base URL —
these follow common Flowwow integration conventions, but this environment has
no access to Flowwow's live API docs to confirm the exact paths, pagination
shape, or field names for your account. **Run `flowwow_health` first.** If it
404s or the response shape looks wrong:

1. Check the actual base URL / API version in your Flowwow seller cabinet
   docs and set `FLOWWOW_API_URL`.
2. Adjust the paths in `client.py` (`orders()` / `clients()` / `health()`) to
   match — the client, retry, and pagination plumbing underneath is generic
   and does not need to change.

## Agent tools

| Tool | Purpose |
|---|---|
| `flowwow_health` | Probe token + base URL (one order fetch) |
| `flowwow_orders` | List orders, optional `status` filter |
| `flowwow_clients` | List buyers/clients known to the shop |

## Pitfalls

- Token is a secret — keep it in `.env`, never paste into chat/memory.
- Read-only: no push/write path — nothing here mutates Flowwow orders or
  clients.
