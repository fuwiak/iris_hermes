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
6. First-page **снимок** (100 rows) is served instantly when present; full catalog
   rebuilds in the background (`revalidating` only while MoySklad fetch runs).
7. **Telegram Desktop export** — place `data/telegram_export.json` (or
   `$HERMES_HOME/moysklad/telegram_export.json`). Chats map onto **Клиенты**
   by phone / **Наименование** and fill the **TG conversation** column +
   client-card history (AI context). Empty **ТГ ник** is filled when an
   `@username` is found. There is **no separate «ТГ архив» menu** — use
   **Импорт Telegram** on the Clients page (or
   `POST /clients/telegram-export/import?force=true`). Overlay + threads
   persist: **Redis** → `$HERMES_HOME/moysklad/telegram_export_overlay.json`
   + `conversations.json` → memory. Catalog auto-stamps on load.
8. **Предложить группы** → dry-run → **Записать в МойСклад** (heuristic merge, does not wipe unrelated tags)
9. **AI fill (lazy)** — Clients table **auto-fills** empty **Группы / Статус / Пол /
   роль / ТГ ник / Тип контрагента** for every **currently shown** row (batched
   as the list grows on scroll). No button. Results persist in **Redis** (when
   `REDIS_URL` set) + `$HERMES_HOME/moysklad/ai_fill_cache/` (and legacy
   `ai_fill.json`); cached entries skip LLM on reload. Cells show a **green
   outline + AI badge**. Never overwrites MoySklad-owned non-empty cells.
   Endpoint: `POST /clients/ai-fill` with `ids` for the visible set.
10. Client card: **Sync Telegram** pulls gateway session history **and** the
   personal MTProto account thread (inbound replies), then regenerates AI
   summary/recommendation via **DeepSeek** by default
   (`openrouter` + `deepseek/deepseek-chat`). «Саммари AI» shows DeepSeek LLM
   output only (no experimental GPT/Haiku picker).
11. Рассылки: same audience filters as Clients + event calendar
   (``event_date_from`` / ``event_date_to``) with optional lead window
   ``days_before_event`` (e.g. 5 days before 8 March / событие марта).
12. `GET /clients/integrity` — audit tab partition (hybrid / no-orders /
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
5. **TG conversation** — durable thread per client (Redis when
   `REDIS_URL` set, else `$HERMES_HOME/moysklad/conversations.json`; keys:
   `client_id` / phone / tg nick). Column + client card + facts panel show
   the thread. Button
   **Отправить в Telegram** calls `POST /campaigns/mark-sent` with
   `deliver=true`: Bot API `sendMessage` via
   `MOYSKLAD_TELEGRAM_BOT_TOKEN` (Business bot, e.g.
   `@BoberSystemsAssistant_bot`) + optional
   `MOYSKLAD_TELEGRAM_BUSINESS_CONNECTION_ID` (or seller_settings). On
   success the deep-link is skipped; on failure the text is still stored
   and a deep-link may open for manual send.
   `POST /clients/{id}/conversation`, `POST /campaigns/mark-sent`.
   Inbound replies: append with ``direction=inbound``, or **Sync Telegram**
   which merges Hermes gateway sessions + personal MTProto history
   (``telegram_user.fetch_history`` / egress ``POST …/history``) and
   regenerates the AI recommendation.

### Личный Telegram (MTProto) — «мои контакты»

The Bot API cannot list your contacts and cannot write to someone who never
messaged the bot, so Рассылки can also drive the operator's **own** account
over MTProto (Telethon, lazy-installed as `platform.telegram_user`).

* Connect in Рассылки → **Личный Telegram**: phone → code → 2FA. Nothing
  else — no api keys in the UI. App credentials resolve server-side:
  `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` from `.env`, else stored config,
  else built-in public Telegram Desktop keys (opt out with
  `TELEGRAM_BUILTIN_API=0`).
  Endpoints: `POST /campaigns/telegram-user/{credentials,login,code,password,session,logout}`,
  `GET /campaigns/telegram-user` (`/credentials` stays for API-only setups).
* **Selectel / RU IP:** Telegram MTProto DCs are often unreachable. Preferred
  fix: deploy `deploy/telegram-user-egress/` on Railway and set
  `TELEGRAM_USER_GATEWAY_URL=https://<host>/t/<EGRESS_TOKEN>` so login /
  contacts / send run on a non-RU IP. Alternatives:
  `TELEGRAM_PROXY=socks5://user:pass@host:1080`, or paste a Telethon
  **StringSession** in the Connect form / `POST .../session` (session on
  Selectel still needs MTProto for later ops unless gateway is used).
  Without egress, login fails in ~20s with a clear error instead of hanging.
* Session + credentials: `$HERMES_HOME/telegram_user/config.json` (0600).
  Env overrides: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
  `TELEGRAM_USER_SESSION`.
* Telethon is lazy-installed; if the venv is cold the panel shows
  «Установить telethon» (`POST /campaigns/telegram-user/install`). Manual
  path: `uv pip install telethon==1.44.0`. Note `uv sync` drops it again
  unless you install the `telegram-user` extra.
* The sync merges `contacts.GetContacts` (saved address book) **and** private
  dialogs — most people you message were never saved as contacts, so the
  address book alone leaves the picker nearly empty.
* Contacts sync into `$HERMES_HOME/telegram_user/contacts.json` and show up
  in the «Кому отправить» picker as `tg:<user id>` (`source: telegram`),
  deduped against catalog / overlay peers. Refresh:
  `POST /campaigns/telegram-user/contacts/refresh`.
* Send routing — `MOYSKLAD_TELEGRAM_SEND_VIA`: `auto` (default; personal
  account first, Business bot as fallback), `user`, `bot`.
* Peers with no MoySklad card (`custom:…` / `tg:…`) get a contact-only row,
  so card / AI draft / send run instead of 404-ing.

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

### Рассылки (индивидуально)

Фильтр-билдер тот же (кэш + дедуп каталога):

- Channel kind: только Telegram / только WhatsApp
- Tags/occasions (chip cloud) + «ДР / события» — chips work on **Прямые** и
  **Маркетплейс** (shared occasion allowlist; `букет от 10000` /
  `событие март` normalize to one key)
- VIP / есть телефон / есть Telegram
- Live `matched_total` as filters change
- Все фильтры в одном окне (`ms-filter-window`): канал продаж, доставка,
  VIP/телефон/TG, календарь событий, сегменты, группы MS/AI
- Audience picker: search + infinite scroll / «Ещё клиенты» — клик по клиенту
  открывает 1:1 текст + отправку (`POST /campaigns/mark-sent`)
- Массовый select / пачки / personalize **пока скрыты** — сначала стабильный
  individual flow; batch вернём отдельно
- **Собрать ответы** → `POST /campaigns/replies/collect` syncs MTProto/gateway
  history and lists threads where the client spoke last (awaiting operator)

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
| `order_compositions.py` | Lazy MoySklad positions → «состав заказа» for card/AI |
| `dashboard/` | Tab UI + `plugin_api.py` |
| `SKILL.md` | Agent usage guide |

## Pitfalls

- Token is a secret — keep it in `.env`, never paste into chat/memory.
- `moysklad_push_tags` **replaces** the tag list; confirm before write.
- Large accounts: raise `max_orders` on `moysklad_clients_by_sales_type` if `orders_scanned` looks capped.
- Rate limits: keep `MOYSKLAD_REQUEST_DELAY_MS` ≥ 250 on bulk reads.
