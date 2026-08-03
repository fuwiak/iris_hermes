# moysklad plugin — МойСклад Remap 1.2

Opt-in standalone plugin: agent tools + dashboard tab **Клиенты** (`/clients`
with in-page **Рассылки**) + Hermes One sidebar **Клиенты** / **Рассылки** /
**Plugins**.
Same enable pattern as `google_meet`.

## Quick start

```bash
# 1. Token: МойСклад → Настройки → Пользователи и права → Токены
# 2. Put in ~/.hermes/.env
MOYSKLAD_API_TOKEN=your_token_here
# optional:
# MOYSKLAD_API_URL=https://api.moysklad.ru/api/remap/1.2
# MOYSKLAD_REQUEST_DELAY_MS=250
# MOYSKLAD_API_RETRY_MAX=4

# 3. Enable plugin + toolset
hermes plugins enable moysklad
hermes tools   # ensure «moysklad» is on for CLI / messaging / api_server

# 4. Verify
hermes plugins list | grep moysklad   # expect: enabled
# In chat: ask Hermes to call moysklad_health
# Dashboard: hermes dashboard → nav «Клиенты»
```

Disable:

```bash
hermes plugins disable moysklad
```

## What ships

| Piece | Role |
|---|---|
| 7 model tools (`toolset=moysklad`) | Health, counterparties, orders, positions, channels, push tags, CRM tabs |
| Dashboard tab **Клиенты** | Маркетплейс / Прямые + in-page **Рассылки** drafts |
| Hermes One plugin | Sidebar Клиенты / Рассылки / Plugins (Settings) |
| `skills/productivity/moysklad-crm-tabs` | Agent guidance for tab rules |

## Agent tools

| Tool | Purpose |
|---|---|
| `moysklad_health` | Probe token (one counterparty row) |
| `moysklad_counterparties` | List / search clients |
| `moysklad_orders` | Customer orders (optional `agent_id`) |
| `moysklad_positions` | Line items for one order |
| `moysklad_channels` | Sales channels |
| `moysklad_push_tags` | Replace counterparty tags (write) |
| `moysklad_clients_by_sales_type` | CRM audiences: `direct` / `marketplace` / `all` |

Example prompts:

- «Проверь МойСклад» → `moysklad_health`
- «Кто прямые клиенты?» → `moysklad_clients_by_sales_type(sales_filter="direct")`
- «Заказы контрагента `<uuid>`» → `moysklad_orders(agent_id=...)`

## Dashboard UI

1. `hermes plugins enable moysklad`
2. `hermes dashboard` → **Клиенты**
3. Tabs: Все / Маркетплейс / Прямые
4. Chip cloud **Группы (МойСклад)** filters by tags
5. **Предложить группы** → dry-run → **Записать в МойСклад** (heuristic merge, does not wipe unrelated tags)

API mounts under `/api/plugins/moysklad/`.

## CRM tab rules (do not re-derive)

- **Прямые** — only pure direct channels (Telegram, WhatsApp/MAX, Витрина, сайт, прямые продажи). Any FlowWow / Ozon / WB channel excludes the client.
- **Маркетплейс** — FlowWow channel allowlist ∪ statuses (`новый`, `постоянный маркетплейсы`) ∪ groups (`флау вау`, `скайлофт`, …).
- Status/tags alone can put a client in Маркетплейс without FlowWow orders.

Full agent procedure: `skills/productivity/moysklad-crm-tabs/SKILL.md` and colocated `SKILL.md`.

## Files

| Path | Purpose |
|---|---|
| `plugin.yaml` | Manifest (`kind: standalone`) |
| `__init__.py` | `register(ctx)` — 7 tools |
| `client.py` | Remap 1.2 HTTP client |
| `tools.py` | Schemas + handlers |
| `sales_channels.py` / `classify.py` | Channel + CRM tab logic |
| `groups.py` / `assign_groups.py` | Group cloud + heuristic assign |
| `dashboard/` | Tab UI + `plugin_api.py` |
| `SKILL.md` | Agent usage guide |

## Pitfalls

- Token is a secret — keep it in `.env`, never paste into chat/memory.
- `moysklad_push_tags` **replaces** the tag list; confirm before write.
- Large accounts: raise `max_orders` on `moysklad_clients_by_sales_type` if `orders_scanned` looks capped.
- Rate limits: keep `MOYSKLAD_REQUEST_DELAY_MS` ≥ 250 on bulk reads.
