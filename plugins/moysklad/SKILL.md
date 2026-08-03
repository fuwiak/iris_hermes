---
name: moysklad
description: "Query MoySklad clients, orders, and CRM tabs."
version: 1.2.0
author: Hermes Agent
license: MIT
platforms:
  - linux
  - macos
  - windows
metadata:
  hermes:
    tags: [moysklad, МойСклад, CRM, counterparties, orders]
    category: productivity
    related_skills: [moysklad-crm-tabs]
---

# moysklad

Talk to **МойСклад** Remap 1.2 via plugin tools (not raw curl). List
counterparties and orders, classify Маркетплейс/Прямые, push tags, open the
dashboard **Клиенты** tab.

## When to use

- User asks about clients / контрагенты / заказы in МойСклад
- Need Маркетплейс vs Прямые audiences (same rules as Iris CRM)
- Push segment tags onto a counterparty
- Health-check whether the token works
- User wants the Clients screen or MoySklad group chips

## Prerequisites

```bash
hermes plugins enable moysklad
hermes tools   # moysklad toolset on for the surface in use
```

In `${HERMES_HOME:-~/.hermes}/.env`:

```
MOYSKLAD_API_TOKEN=...
# optional: MOYSKLAD_API_URL, MOYSKLAD_REQUEST_DELAY_MS, MOYSKLAD_API_RETRY_MAX
```

Dashboard: `hermes dashboard` → nav **Клиенты**.

## How to run

Prefer these tools over inventing channel rules:

| Intent | Tool |
|--------|------|
| Token OK? | `moysklad_health` |
| List / search clients | `moysklad_counterparties` |
| Orders (optionally by client) | `moysklad_orders` |
| Order line items | `moysklad_positions` |
| Sales channels | `moysklad_channels` |
| Direct clients | `moysklad_clients_by_sales_type` `sales_filter="direct"` |
| Marketplace clients | `moysklad_clients_by_sales_type` `sales_filter="marketplace"` |
| Counts both | `moysklad_clients_by_sales_type` `sales_filter="all"` → `counts` |
| Write tags | `moysklad_push_tags` (confirm with user first) |

### Dashboard

1. Enable plugin (above)
2. Open **Клиенты**
3. Tabs Все / Маркетплейс / Прямые; chip cloud «Группы (МойСклад)»
4. **Предложить группы** → dry-run → **Записать в МойСклад**

## Rules (do not re-derive)

- **Прямые**: only pure direct channels (Telegram, WhatsApp/MAX, Витрина, сайт, прямые продажи). Any FlowWow/Ozon/WB channel excludes the client.
- **Маркетплейс**: FlowWow allowlist ∪ statuses (`новый`, `постоянный маркетплейсы`) ∪ groups (`флау вау`, `скайлофт`, …).
- Call `moysklad_clients_by_sales_type` — do not classify from a single order sample.

## Pitfalls

- Token is secret — never paste into summaries or memory.
- `moysklad_push_tags` replaces tags; it does not merge.
- Large accounts: raise `max_orders` if `orders_scanned` looks capped.
- Heuristic group assign **merges** tags; it does not wipe unrelated MoySklad tags.

## Verification

1. `moysklad_health` → `ok: true`
2. `moysklad_clients_by_sales_type` with `sales_filter="direct"` returns `matched_total`
3. Spot-check: WhatsApp-only → direct; FlowWow + WhatsApp → not direct
4. Dashboard: enable → **Клиенты** visible; `hermes plugins disable moysklad` → tools off
