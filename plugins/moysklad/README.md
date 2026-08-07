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
4. Chip clouds **Группы: Мой склад** / **Группы: ИИ** — separate filters by source
5. **Синхронизация** — force re-download from MoySklad into cache (page views otherwise serve cache)
6. **Предложить группы** → dry-run → **Записать в МойСклад** (heuristic merge, does not wipe unrelated tags)
7. **AI fill (lazy)** — Clients table **auto-fills** empty **Группы / Статус / Пол /
   роль / ТГ ник / Тип контрагента** for every **currently shown** row (batched
   as the list grows on scroll). No button. Results persist in **Redis** (when
   `REDIS_URL` set) + `$HERMES_HOME/moysklad/ai_fill_cache/` (and legacy
   `ai_fill.json`); cached entries skip LLM on reload. Cells show a **green
   outline + AI badge**. Never overwrites MoySklad-owned non-empty cells.
   Endpoint: `POST /clients/ai-fill` with `ids` for the visible set.
8. Client card: **Sync Telegram** pulls gateway session history; AI summary
   supports model picker (`provider`/`model` on `POST /clients/{id}/ai`).
9. Рассылки: same audience filters as Clients + smart window
   `days_before_event` (e.g. 5 days before 8 March / событие марта).
10. `GET /clients/integrity` — audit tab partition (hybrid / no-orders /
    marker-only) explaining historic «lost clients» counting.

API mounts under `/api/plugins/moysklad/` (`GET /clients`, `GET /clients/{id}`,
`POST /clients/{id}/ai`, `POST /clients/ai-fill`, `POST /sync`, `GET|POST /campaigns`,
`POST /campaigns/generate`, `POST /campaigns/rewrite`,
`POST /campaigns/sanity`, `GET|PUT /campaigns/seller-settings`, groups).
Seller signature fields (`seller_name`, `seller_facts`) persist in
`$HERMES_HOME/moysklad/seller_settings.json`.

### Рассылки ↔ Клиенты

Audience filters on **Рассылки** use the **same durable catalog cache** and
marketplace/direct classification as **Клиенты**. Personalized drafts:

1. Open a client card → **Черновик рассылки** (or pick a chip in Рассылки).
2. Selecting a client loads **Redis/file draft cache** (`GET /campaigns/draft-cache`)
   — no auto-LLM. Manual edits debounce-save back to cache.
3. **Сгенерировать AI** / bouquet / rewrite / paraphrase force a new pass and
   write the result to cache. Batch **Персонализировать** serves cache hits
   first (`from_cache`) so re-runs do not re-LLM the same clients.
4. **Авто (AI)** calls `POST /campaigns/generate` with client facts + card
   recommendation; text is editable before save.
5. Side **Факты** panel shows orders / avg check / channels / tags / last order
   plus three audit blocks (**История и профиль**, **Повод и intent**,
   **Риски / ограничения**) so a human can audit grounding (no invented
   discounts/phones/debt).
6. After generate/rewrite a **sanity** pass runs (LLM + heuristic fallback):
   if facts show debt / unpaid orders, flower upsell is rejected and the text
   is revised toward payment reconcile. Button **Проверить смысл** calls
   `POST /campaigns/sanity` explicitly.
5. **TG conversation** — local thread per client under
   `$HERMES_HOME/moysklad/conversations.json` (keys: `client_id` / phone /
   tg nick). Column + client card + facts panel show the thread. Button
   **Отправить в Telegram** calls `POST /campaigns/mark-sent` with
   `deliver=true`: Bot API `sendMessage` via
   `MOYSKLAD_TELEGRAM_BOT_TOKEN` (Business bot, e.g.
   `@BoberSystemsAssistant_bot`) + optional
   `MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID` (or seller_settings). On
   success the deep-link is skipped; on failure the text is still stored
   and a deep-link may open for manual send.
   `POST /clients/{id}/conversation`, `POST /campaigns/mark-sent`.
   Inbound replies can be appended with `direction=inbound`. Full live pull
   from Hermes gateway Telegram sessions is a follow-up: match
   `session_key` / peer phone or `@nick` to the same index keys and merge.

### Clients catalog cache

MoySklad counterparties + orders are expensive. The Clients tab uses
CDN-style **stale-while-revalidate**:

| Trigger | Behavior |
|---|---|
| Page load / filter / search | Serve durable cache if present (fresh or stale) |
| Fresh hit | No MoySklad call |
| TTL expiry (stale peek) | Serve stale immediately + background rebuild |
| Cold miss + page snapshot | Serve first **100** rows instantly + background rebuild |
| Desktop remount | Paint `localStorage` snapshot, then revalidate via API |
| Scroll near bottom | Fetch next `offset` page (`limit=100`); already-loaded rows stay in UI |
| Cold miss (no durable bytes) | Blocking download from MoySklad (seeds catalog + page snapshot) |
| **Синхронизация** button / `POST /sync` / `?refresh=true` | Force refresh + rewrite cache |

- **TTL (freshness):** `MOYSKLAD_CACHE_TTL_SECONDS` (default `21600` = 6 hours)
- **Redis retention:** `MOYSKLAD_CACHE_REDIS_RETENTION_SECONDS` (default ≥7× TTL)
  so expired keys stay peekable on ephemeral disks
- **Page snapshot:** first 100 clients per filter key under
  `moysklad:clients:page:v1:…` (Redis/file) — independent of full catalog
- **Backend:** Redis when `REDIS_URL` is set **and** the `redis` Python package
  is installed; otherwise JSON files under `$HERMES_HOME/moysklad/cache/`
  (Selectel compose already sets `REDIS_URL=redis://redis:6379/0` — file
  fallback still works without the package).
- Cache key includes a hash of the API token + query bounds
  (`max_orders` / `max_counterparties` / archived).
- `/clients` returns `matched_total`, `has_more`, `next_offset`, plus
  `cached` / `stale` / `revalidating` / `snapshot` — counts are **post-dedupe**.

### Multi-stage client dedupe

Applied when building/merging the catalog (`dedupe.py`):

1. **Canonical id** — MoySklad counterparty `id` / href (update-in-place)
2. **Contact keys** — normalized phone / email / telegram handle
3. **Fuzzy name+phone** — same normalized name + phone stem in the batch
4. **Cache merge** — never append duplicates; merge richer row into existing

### Mass Рассылки filters

Рассылки filter builder uses the same cached + deduped catalog:

- Channel kind: только Telegram / только WhatsApp
- Tags/occasions (chip cloud) + «ДР / события» — chips work on **Прямые** and
  **Маркетплейс** (shared occasion allowlist; `букет от 10000` /
  `событие март` normalize to one key)
- VIP / есть телефон / есть Telegram
- Live `matched_total` as filters change; mass draft = shared template for the
  group (`personalize` flag queues per-client personalization for later)
- Audience picker: search + infinite scroll / «Ещё клиенты» (not a hard 12-row
  cap) — any client in the filtered audience is reachable

### Пересчитать группы (LLM)

Клиенты → **Пересчитать группы**: LLM proposes taxonomy → edit names → preview
→ write merged tags to MoySklad (`POST /groups/recalculate/propose|apply`).
Falls back to heuristic tag frequencies when LLM is unavailable.

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
| `dedupe.py` / `audience.py` | Multi-stage dedupe + mass audience filters |
| `groups.py` / `assign_groups.py` | Group cloud + heuristic assign |
| `recalculate_groups.py` | LLM/heuristic taxonomy propose + reassign |
| `dashboard/` | Tab UI + `plugin_api.py` |
| `SKILL.md` | Agent usage guide |

## Pitfalls

- Token is a secret — keep it in `.env`, never paste into chat/memory.
- `moysklad_push_tags` **replaces** the tag list; confirm before write.
- Large accounts: raise `max_orders` on `moysklad_clients_by_sales_type` if `orders_scanned` looks capped.
- Rate limits: keep `MOYSKLAD_REQUEST_DELAY_MS` ≥ 250 on bulk reads.
