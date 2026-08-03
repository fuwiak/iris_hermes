---
name: moysklad-crm-tabs
description: "Filter MoySklad clients as CRM Маркетплейс/Прямые tabs."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [moysklad, crm, flowers, segmentation]
    category: productivity
---

# MoySklad CRM Tabs Skill

Ask Hermes which clients are **Маркетплейс** vs **Прямые** using the same
rules as Iris CRM (`client_segmentation_deepseek`), not ad-hoc guessing.

Dashboard tab **Клиенты** (`/clients`) appears when the plugin is enabled —
group cloud + table + heuristic «Предложить группы». Disable removes the tab.

## When to Use

- User asks who is marketplace / direct / «Маркетплейс» / «Прямые»
- Need CRM-tab audience for campaigns or messaging
- Comparing channel mix (FlowWow vs WhatsApp/Telegram/Витрина/сайт)
- User wants the Clients screen or MoySklad group chips

## Prerequisites

- Plugin enabled: `hermes plugins enable moysklad`
- Toolset on: `hermes tools enable moysklad`
- `MOYSKLAD_API_TOKEN` in `~/.hermes/.env`
- Dashboard: `hermes dashboard` → nav **Клиенты**

## How to Run

### Agent tools

Call `moysklad_clients_by_sales_type` (do not invent channel rules):

- Прямые → `sales_filter="direct"`
- Маркетплейс → `sales_filter="marketplace"`
- Counts for both → `sales_filter="all"` then read `counts`

### Dashboard UI

1. `hermes plugins enable moysklad`
2. Open dashboard → **Клиенты**
3. Tabs Все / Маркетплейс / Прямые, chip cloud «Группы (МойСклад)»
4. **Предложить группы** → dry-run → **Записать в МойСклад** (heuristics)

### Disable

- `hermes plugins disable moysklad` — tab + `/api/plugins/moysklad` gone
- Or hide in Plugins UI → `dashboard.hidden_plugins`

## Quick Reference

| Intent | Tool args |
|--------|-----------|
| List direct clients | `sales_filter=direct`, `limit=50` |
| List marketplace | `sales_filter=marketplace`, `limit=50` |
| Totals only | `sales_filter=all`, `limit=5` → use `counts` |

## Rules (do not re-derive)

- **Прямые**: only pure direct channels (Telegram, WhatsApp/MAX, Витрина, сайт, прямые продажи). Any FlowWow/Ozon/WB channel excludes the client.
- **Маркетплейс**: FlowWow channel allowlist ∪ statuses (`новый`, `постоянный маркетплейсы`) ∪ groups (`флау вау`, `скайлофт`, …).
- Status/tags alone can put a client in Маркетплейс without FlowWow orders.
- Group chips = MoySklad tags only; assign uses avg check / order count / channel / keywords / order month.

## Pitfalls

- Do not classify from a single order sample — use the tool (full scan).
- `sales_type` on a row is channel-derived; tab membership uses audience filters.
- Large accounts: raise `max_orders` if `orders_scanned` looks capped.
- Heuristic assign **merges** tags; it does not wipe unrelated MoySklad tags.

## Verification

1. `moysklad_health` → `ok: true`
2. `moysklad_clients_by_sales_type` with `sales_filter=direct` returns `matched_total`
3. Spot-check: client with only WhatsApp is direct; FlowWow + WhatsApp is not direct
4. Dashboard: enable plugin → **Клиенты** visible; disable → tab gone
