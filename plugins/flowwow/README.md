# flowwow plugin

Opt-in standalone plugin: read tools over the official Flowwow seller API
(«Открытое API для продавцов 0.0.1»). Same pattern as `moysklad`
(`client.py` sync HTTP client, `tools.py` schemas + handlers, `plugin.yaml`
manifest). **Endpoints verified live** against `https://apis.flowwow.com`.

## Quick start

```bash
# 1. Token: issued by Flowwow support (or seller cabinet → API / интеграции)
# 2. Put in ~/.hermes/.env
FLOWWOW_API_TOKEN=your_token_here
# optional, only to override the verified default base URL:
# FLOWWOW_API_URL=https://apis.flowwow.com

# 3. Enable plugin + toolset
hermes plugins enable flowwow
hermes tools   # ensure «flowwow» is on for CLI / messaging / api_server

# 4. Verify
hermes plugins list | grep flowwow   # expect: enabled
# In chat: ask Hermes to call flowwow_health
```

## API facts (verified 21.08.2026)

- Base URL: `https://apis.flowwow.com`
- Auth: `Authorization: Bearer <token>` (255-char token, non-JWT format works)
- Everything except ping is `POST` with a JSON body; product/stock/price
  endpoints also need `?shopId=<int>` as a query parameter
- Docs: [seller-docs.flowwow.com → 5.1 Документация и поддержка по API](https://seller-docs.flowwow.com/5.-instrumenty-prodavca/5.1-dokumentaciya-i-podderzhka-po-api)
  (OpenAPI spec embedded in the «Открытое API для продавцов (0.0.1)» page)
- ⚠️ `api.flowwow.com` (without the `s`) is a different host behind an
  anti-bot WAF — it 403s everything; do not use it
- ⚠️ The open API **has no orders/clients endpoints** as of 0.0.1 — only
  shops, products, stocks, prices. Orders flow needs another channel.

## Agent tools

| Tool | Purpose |
|---|---|
| `flowwow_health` | Ping + one shops page (token check) |
| `flowwow_shops` | List seller's shops (shopId, name, address, status) |
| `flowwow_products` | Products of one shop: name, description, price, discount, images |

`client.py` also exposes `stocks()` / `prices()` (per-offerId lookups) for
future card-sync work — not registered as agent tools yet.

## Pitfalls

- Token is a secret — keep it in `.env`, never paste into chat/memory.
- Read-only tools: nothing here mutates Flowwow (the API itself does have
  write endpoints — products/create, products/update, prices/put,
  stocks/put — reserved for the card-autopublish feature).
- `/apiseller/shops` pages are capped at 50 rows; products at 1000
  (client uses 100 to keep tool output chat-sized).
