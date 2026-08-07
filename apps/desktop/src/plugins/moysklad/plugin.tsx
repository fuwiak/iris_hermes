import './moysklad.css'

import {
  type HermesPlugin,
  host,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution
} from '@hermes/plugin-sdk'
import { type FormEvent, type UIEvent, useCallback, useEffect, useRef, useState } from 'react'

interface GroupChipOption {
  name: string
  count: number
  source?: 'ms' | 'ai' | 'both' | string
  filter_source?: 'ms' | 'ai' | string
  ms_count?: number
  ai_count?: number
  hue?: number
}

function groupChipSrcLabel(source?: string): string {
  if (source === 'ai') return 'AI'
  if (source === 'both') return 'МС+AI'
  return 'МС'
}

function groupChipSrcClass(source?: string): string {
  if (source === 'ai') return 'is-ai'
  if (source === 'both') return 'is-both'
  return 'is-ms'
}

function GroupCloudSection({
  title,
  items,
  activeGroup,
  activeSource,
  sourceKey,
  onToggle,
  limit = 24
}: {
  title: string
  items: GroupChipOption[]
  activeGroup: string
  activeSource: string
  sourceKey: 'ms' | 'ai'
  onToggle: (name: string, source: 'ms' | 'ai') => void
  limit?: number
}) {
  if (!items.length) {return null}

  return (
    <div className="ms-filter-block ms-group-cloud">
      <div className="ms-group-cloud-head">
        <span className="ms-group-cloud-title">{title}</span>
        <span className="ms-muted">{items.length}</span>
      </div>
      <div className="ms-group-chips">
        {items.slice(0, limit).map(opt => {
          const src = opt.filter_source || opt.source || sourceKey
          const active = activeGroup === opt.name && activeSource === sourceKey

          return (
            <button
              className={`ms-group-chip ${groupChipSrcClass(src)}${active ? ' is-active' : ''}`}
              key={`${sourceKey}:${opt.name}`}
              onClick={() => onToggle(opt.name, sourceKey)}
              title={`${opt.count} · ${groupChipSrcLabel(src)}`}
              type="button"
            >
              <span className={`ms-chip-src ms-chip-src-${src}`}>
                {groupChipSrcLabel(sourceKey)}
              </span>
              {opt.name}
              <span className="ms-group-chip-count">{opt.count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

interface ClientOrder {
  id?: string
  name?: string
  date?: string
  sum?: number
  channel?: string
  product_snippet?: string
}

interface ConversationMessage {
  id?: string
  direction?: string
  channel?: string
  label?: string
  text?: string
  ts?: string
  source?: string
}

interface ClientConversation {
  client_id?: string
  messages?: ConversationMessage[]
  message_count?: number
  preview?: string
  empty?: boolean
  updated_at?: string | null
}

interface ClientDetail {
  client?: ClientRow & {
    vip?: boolean
    loyalty_points?: number | null
    primary_channel?: string
    tag_buckets?: {
      marketplace?: string[]
      loyalty?: string[]
      events?: string[]
      other?: string[]
    }
  }
  orders?: ClientOrder[]
  stats?: {
    avg_check?: number
    order_count?: number
    vip?: boolean
    loyalty_points?: number | null
    last_order?: ClientOrder
  }
  messaging?: {
    whatsapp_url?: string
    telegram_url?: string
    primary_channel?: string
    hint?: string
  }
  ai?: {
    history_profile?: string
    occasion_intent?: string
    recommendation?: string
    source?: string
    data_thin?: boolean
  }
  risks?: ClientRisks
  fact_blocks?: {
    history_profile?: FactBlock
    occasion_intent?: FactBlock
    risks?: FactBlock
  }
  conversation?: ClientConversation
  data_thin?: boolean
}

type Rest = <T>(path: string, opts?: { method?: string; body?: unknown; timeoutMs?: number }) => Promise<T>
type RestStream = (
  path: string,
  opts: {
    method?: string
    body?: unknown
    timeoutMs?: number
    onEvent: (event: unknown) => void
    signal?: AbortSignal
  }
) => Promise<void>

let rest: null | Rest = null
let restStream: null | RestStream = null

function useMsRest(): Rest {
  return useCallback(async <T,>(path: string, opts?: { method?: string; body?: unknown; timeoutMs?: number }) => {
    if (!rest) {
      throw new Error('MoySklad plugin REST not bound')
    }

    return rest<T>(path, opts)
  }, [])
}

function useMsRestStream(): RestStream {
  return useCallback(
    async (
      path: string,
      opts: {
        method?: string
        body?: unknown
        timeoutMs?: number
        onEvent: (event: unknown) => void
        signal?: AbortSignal
      }
    ) => {
      if (!restStream) {
        throw new Error('MoySklad plugin REST stream not bound')
      }

      return restStream(path, opts)
    },
    []
  )
}

/** Pull outreach text from generate/rewrite/sanity payloads (tolerant to nesting). */
function pickOutreachMessage(data: unknown): string {
  if (!data || typeof data !== 'object') {
    return ''
  }

  const row = data as Record<string, unknown>
  const nested = row.result && typeof row.result === 'object' ? (row.result as Record<string, unknown>) : null
  const sanity =
    row.sanity && typeof row.sanity === 'object' ? (row.sanity as Record<string, unknown>) : null
  const candidates = [
    row.message,
    row.text,
    row.offer,
    row.draft,
    sanity?.revised_text,
    nested?.message,
    nested?.text
  ]

  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) {
      return c
    }
  }

  return ''
}

const OUTREACH_AI_TIMEOUT_MS = 120_000

interface Counts {
  total?: number
  marketplace?: number
  direct?: number
}

interface ClientRow {
  id?: string
  name?: string
  tg_conversation_preview?: string
  conversation_count?: number
  phone?: string
  email?: string
  state?: string
  sales_type?: string
  channel?: string
  channels?: string[]
  tags?: string[]
  groups?: string
  ms_groups?: string
  ai_groups?: string[]
  order_count?: number | null
  avg_check?: number | null
  last_order_at?: string | null
  bonus_points?: string | number
  role?: string
  actual_address?: string
  actual_address_comment?: string
  company_type?: string
  sex?: string
  tg_nick?: string
  tg_conversation?: string
  ai_fields?: string[]
  ai_fill_source?: string
}

/** Public keys stamped by POST /clients/ai-fill for green AI markers. */
const AI_COLUMN_KEYS: Record<string, string> = {
  state: 'state',
  groups_display: 'groups',
  role: 'role',
  sex: 'sex',
  tg_nick: 'tg_nick',
  company_type: 'company_type'
}

function AiCell({
  value,
  ai
}: {
  value: string
  ai?: boolean
}) {
  if (!value) {
    return <>{'—'}</>
  }

  if (!ai) {
    return <>{value}</>
  }

  return (
    <span className="ms-ai-cell" title="Заполнено AI (кэш Redis/файл)">
      <span className="ms-ai-badge" aria-hidden="true">
        AI
      </span>
      <span className="ms-ai-value">{value}</span>
    </span>
  )
}

/** Empty fillable CRM slots that AI may complete (lazy page eval). */
function rowNeedsLazyAiFill(row: ClientRow): boolean {
  if (!row.id) {
    return false
  }

  if ((row.ai_fields || []).length > 0) {
    return false
  }

  const groups = (row.groups || (row.tags || []).join(', ')).trim()
  const empty = (v: string | undefined | null) => !v || !String(v).trim() || String(v).trim() === '—'

  return (
    empty(row.state) ||
    empty(groups) ||
    empty(row.role) ||
    empty(row.sex) ||
    empty(row.company_type)
  )
}

const LAZY_AI_BATCH = 50

function applyAiFillResults(
  clients: ClientRow[],
  results: Array<{
    id?: string
    filled?: Record<string, unknown>
    fields?: Record<string, unknown>
    ai_fields?: string[]
    source?: string
  }>
): ClientRow[] {
  if (!results.length) {
    return clients
  }

  const byId = new Map(
    results
      .filter(r => r.id)
      .map(r => [String(r.id), r] as const)
  )

  return clients.map(row => {
    const id = String(row.id || '')
    const hit = byId.get(id)

    if (!hit) {
      return row
    }

    const fields = {
      ...(hit.fields || {}),
      ...(hit.filled || {})
    } as Record<string, unknown>
    const next: ClientRow = { ...row }
    const aiFields = [
      ...new Set([
        ...(hit.ai_fields || []),
        ...(row.ai_fields || []),
        ...Object.keys(fields)
      ])
    ]

    for (const key of aiFields) {
      const val = fields[key]

      if (val == null || val === '') {
        continue
      }

      if (key === 'groups') {
        if (Array.isArray(val)) {
          next.groups = val.map(String).filter(Boolean).join(', ')
          next.tags = val.map(String).filter(Boolean)
        } else {
          next.groups = String(val)
        }
      } else if (key === 'state') {
        next.state = String(val)
      } else if (key === 'sex') {
        next.sex = String(val)
      } else if (key === 'role') {
        next.role = String(val)
      } else if (key === 'tg_nick') {
        next.tg_nick = String(val)
      } else if (key === 'company_type') {
        next.company_type = String(val)
      }
    }

    next.ai_fields = aiFields.length ? aiFields : row.ai_fields
    next.ai_fill_source = hit.source || row.ai_fill_source

    return next
  })
}

const CLIENT_COLUMNS: Array<{
  key: keyof ClientRow | 'channel_display' | 'groups_display' | 'avg_display' | 'last_order_display' | 'orders_display'
  label: string
  render: (row: ClientRow) => string
}> = [
  { key: 'name', label: 'Наименование', render: r => r.name || '' },
  { key: 'phone', label: 'Телефон', render: r => r.phone || '' },
  { key: 'state', label: 'Статус', render: r => r.state || '' },
  { key: 'sales_type', label: 'Тип канала продаж', render: r => r.sales_type || '' },
  {
    key: 'channel_display',
    label: 'Канал продаж',
    render: r =>
      (r.channels || []).length ? (r.channels || []).join(', ') : r.channel || ''
  },
  { key: 'avg_display', label: 'Средний чек', render: r => money(r.avg_check) },
  {
    key: 'last_order_display',
    label: 'Дата последнего заказа',
    render: r =>
      r.last_order_at
        ? (r.last_order_at || '').slice(0, 16).replace('T', ' ')
        : '—'
  },
  {
    key: 'orders_display',
    label: 'Всего заказов',
    render: r => (r.order_count == null ? '—' : String(r.order_count))
  },
  { key: 'bonus_points', label: 'Баллы начисленные', render: r => String(r.bonus_points ?? '') },
  {
    key: 'groups_display',
    label: 'Группы',
    render: r => {
      const ms = String(r.ms_groups || '').trim()
      const ai = (r.ai_groups || []).filter(Boolean)
      if (ms && ai.length) return `МС: ${ms} · AI: ${ai.join(', ')}`
      if (ai.length) return `AI: ${ai.join(', ')}`
      return r.groups || (r.tags || []).join(', ')
    }
  },
  { key: 'role', label: 'Заказчик или получатель', render: r => r.role || '' },
  { key: 'actual_address', label: 'Фактический адрес', render: r => r.actual_address || '' },
  {
    key: 'actual_address_comment',
    label: 'Фактический адрес (Комментарий)',
    render: r => r.actual_address_comment || ''
  },
  { key: 'company_type', label: 'Тип контрагента', render: r => r.company_type || '' },
  { key: 'sex', label: 'Пол', render: r => r.sex || '' },
  { key: 'email', label: 'E-mail', render: r => r.email || '' },
  { key: 'tg_nick', label: 'ТГ ник', render: r => r.tg_nick || '' },
  {
    key: 'tg_conversation',
    label: 'TG conversation',
    render: r => {
      const preview = r.tg_conversation_preview || r.tg_conversation || ''
      const n = r.conversation_count

      if (!preview) {
        return ''
      }

      return n && n > 0 ? `${preview}` : preview
    }
  }
]

interface Campaign {
  id: string
  title: string
  channel: string
  mode: string
  offer?: string
  status?: string
  audience_count?: number
  client_id?: string
  client_name?: string
  recommendation?: string
  grounding_notes?: string
  ai_source?: string
  facts?: ClientFacts
  personalize_pending?: boolean
  audience_filters?: Record<string, unknown>
}

interface FactBlockLine {
  label?: string
  value?: string
}

interface FactBlock {
  title?: string
  empty?: boolean
  lines?: FactBlockLine[]
  note?: string | null
  do_not_upsell?: boolean
}

interface ClientRisks {
  has_debt?: boolean
  debt_amount?: number | null
  balance?: number | null
  unpaid_order_count?: number
  unpaid_total?: number
  do_not_upsell?: boolean
  flags?: string[]
}

interface ClientFacts {
  client_id?: string
  name?: string
  phone?: string | null
  email?: string | null
  tg_nick?: string | null
  sales_type?: string | null
  channels?: string[]
  primary_channel?: string | null
  order_count?: number
  avg_check?: number
  vip?: boolean
  loyalty_points?: number | null
  last_order?: ClientOrder | null
  tags?: string[]
  event_tags?: string[]
  orders_preview?: ClientOrder[]
  data_thin?: boolean
  recommendation?: string | null
  history_profile?: string | null
  occasion_intent?: string | null
  ai_source?: string | null
  risks?: ClientRisks
  block_history_profile?: FactBlock
  block_occasion_intent?: FactBlock
  block_risks?: FactBlock
  fact_blocks?: {
    history_profile?: FactBlock
    occasion_intent?: FactBlock
    risks?: FactBlock
  }
  conversation?: ClientConversation
}

interface SanityResult {
  ok?: boolean
  issues?: string[]
  revised_text?: string | null
  source?: string
  auto_revised?: boolean
}

const DRAFT_PREFILL_KEY = 'moysklad.draftPrefill'
/** Status shown after successful generate (and restored from Redis/file cache). */
const AI_GENERATED_STATUS = 'AI сгенерировал креативный текст — можно править вручную.'

interface DraftPrefill {
  clientId: string
  channel?: string
  salesFilter?: string
}

function readDraftPrefill(): DraftPrefill | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_PREFILL_KEY)

    if (!raw) {
      return null
    }

    sessionStorage.removeItem(DRAFT_PREFILL_KEY)
    const parsed = JSON.parse(raw) as DraftPrefill

    if (!parsed?.clientId) {
      return null
    }

    return parsed
  } catch {
    return null
  }
}

function writeDraftPrefill(prefill: DraftPrefill) {
  try {
    sessionStorage.setItem(DRAFT_PREFILL_KEY, JSON.stringify(prefill))
  } catch {
    /* ignore quota / private mode */
  }
}

function channelFromMessaging(primary?: string): string {
  const p = (primary || '').toLowerCase()

  if (p.includes('whatsapp')) {
    return 'whatsapp'
  }

  return 'telegram'
}

function money(n: number | null | undefined) {
  if (n == null || Number.isNaN(Number(n))) {return '—'}
  const v = Number(n)

  if (!Number.isFinite(v) || v <= 0) {return '—'}

  try {
    return new Intl.NumberFormat('ru-RU', {
      style: 'currency',
      currency: 'RUB',
      maximumFractionDigits: 0
    }).format(v)
  } catch {
    return `${Math.round(v)} ₽`
  }
}

function FilterTabs({
  salesFilter,
  counts,
  onChange,
  disabled
}: {
  salesFilter: string
  counts: Counts | null
  onChange: (id: string) => void
  disabled?: boolean
}) {
  const tabs = [
    { id: 'all', label: 'Все', count: counts?.total },
    { id: 'marketplace', label: 'Маркетплейс', count: counts?.marketplace },
    { id: 'direct', label: 'Прямые', count: counts?.direct }
  ]

  return (
    <div className="ms-filter-tabs" role="tablist">
      {tabs.map(tab => (
        <button
          className={`ms-filter-tab${salesFilter === tab.id ? ' is-active' : ''}`}
          disabled={disabled}
          key={tab.id}
          onClick={() => onChange(tab.id)}
          role="tab"
          type="button"
        >
          {tab.label}
          {tab.count != null ? <span className="ms-tab-count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

function TagPills({ items, className }: { items?: string[]; className?: string }) {
  if (!items?.length) {
    return null
  }

  return (
    <div className={className || 'ms-tag-row'}>
      {items.map(t => (
        <span className="ms-tag-pill" key={t}>
          {t}
        </span>
      ))}
    </div>
  )
}

function ConversationThread({
  conversation,
  compact,
  title
}: {
  conversation?: ClientConversation | null
  compact?: boolean
  title?: string
}) {
  const messages = conversation?.messages || []

  if (!messages.length) {
    return (
      <div className="ms-conversation">
        <p className="ms-ai-label">{title || 'TG conversation'}</p>
        <p className="ms-muted">
          История пуста. После отправки текста сюда попадёт исходящее; полный sync с
          gateway Telegram — позже.
        </p>
      </div>
    )
  }

  const shown = compact ? messages.slice(-6) : messages

  return (
    <div className="ms-conversation">
      <p className="ms-ai-label">
        {title || 'TG conversation'}
        {conversation?.message_count != null
          ? ` · ${conversation.message_count}`
          : ''}
      </p>
      <div className={`ms-conversation-list${compact ? ' is-compact' : ''}`}>
        {shown.map((m, idx) => (
          <div
            className={`ms-conversation-msg is-${m.direction || 'outbound'}`}
            key={m.id || `${m.ts || ''}-${idx}`}
          >
            <div className="ms-muted">
              {(m.label || m.direction || 'сообщение') +
                (m.ts ? ` · ${String(m.ts).slice(0, 16).replace('T', ' ')}` : '')}
            </div>
            <div>{m.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function FactBlockView({ block }: { block?: FactBlock | null }) {
  if (!block) {return null}
  const riskClass = block.do_not_upsell ? ' ms-fact-block-risk' : ''

  return (
    <div className={`ms-fact-block${riskClass}`}>
      <p className="ms-ai-label">{block.title || 'Факты'}</p>
      {block.empty || !block.lines?.length ? (
        <p className="ms-muted">{block.note || 'Нет данных'}</p>
      ) : (
        <div className="ms-kv-grid ms-fact-block-grid">
          {block.lines.map((line, idx) => (
            <FragmentRow key={`${line.label || 'l'}-${idx}`} label={line.label} value={line.value} />
          ))}
        </div>
      )}
    </div>
  )
}

function FragmentRow({ label, value }: { label?: string; value?: string }) {
  return (
    <>
      <span className="ms-muted">{label || '—'}</span>
      <span>{value || '—'}</span>
    </>
  )
}

/** Map /clients/{id} detail → Facts panel payload (keeps AI prose + audit blocks). */
function factsFromDetail(detail: ClientDetail): ClientFacts {
  const blocks = detail.fact_blocks

  return {
    client_id: detail.client?.id,
    name: detail.client?.name,
    phone: detail.client?.phone,
    email: detail.client?.email,
    tg_nick: detail.client?.tg_nick,
    sales_type: detail.client?.sales_type,
    channels: detail.client?.channels,
    primary_channel: detail.client?.primary_channel || detail.messaging?.primary_channel,
    order_count: detail.stats?.order_count,
    avg_check: detail.stats?.avg_check,
    vip: detail.stats?.vip,
    loyalty_points: detail.stats?.loyalty_points,
    last_order: detail.stats?.last_order,
    tags: detail.client?.tags,
    event_tags: detail.client?.tag_buckets?.events,
    orders_preview: detail.orders,
    data_thin: detail.data_thin || detail.ai?.data_thin,
    recommendation: detail.ai?.recommendation || null,
    history_profile: detail.ai?.history_profile || null,
    occasion_intent: detail.ai?.occasion_intent || null,
    ai_source: detail.ai?.source || null,
    risks: detail.risks,
    block_history_profile: blocks?.history_profile,
    block_occasion_intent: blocks?.occasion_intent,
    block_risks: blocks?.risks,
    fact_blocks: blocks,
    conversation: detail.conversation
  }
}

function FactsPanel({
  facts,
  notes,
  sanity
}: {
  facts: ClientFacts | null
  notes?: string
  sanity?: SanityResult | null
}) {
  if (!facts) {
    return (
      <aside className="ms-facts-panel">
        <h3>Факты клиента</h3>
        <p className="ms-muted">
          Выберите клиента из аудитории или откройте черновик из карточки — здесь появятся заказы,
          чек и теги для сверки с AI-текстом.
        </p>
      </aside>
    )
  }

  const last = facts.last_order
  const historyProse = (facts.history_profile || '').trim()
  const occasionProse = (facts.occasion_intent || '').trim()
  const recommendation = (facts.recommendation || '').trim()
  const hasAiSummary = Boolean(historyProse || occasionProse || recommendation)

  const historyBlock =
    facts.block_history_profile || facts.fact_blocks?.history_profile || null

  const occasionBlock =
    facts.block_occasion_intent || facts.fact_blocks?.occasion_intent || null

  const risksBlock = facts.block_risks || facts.fact_blocks?.risks || null

  return (
    <aside className="ms-facts-panel">
      <h3>Факты · {facts.name || 'клиент'}</h3>
      {facts.data_thin ? <p className="ms-muted">Данных мало — текст должен быть осторожным.</p> : null}
      <div className="ms-kv-grid">
        <span className="ms-muted">Заказов</span>
        <span>{facts.order_count ?? 0}</span>
        <span className="ms-muted">Средний чек</span>
        <span>{money(facts.avg_check)}</span>
        <span className="ms-muted">Каналы</span>
        <span>{(facts.channels || []).join(', ') || facts.primary_channel || '—'}</span>
        <span className="ms-muted">Тип</span>
        <span>{facts.sales_type || '—'}</span>
        <span className="ms-muted">VIP</span>
        <span>{facts.vip ? 'да' : 'нет'}</span>
        <span className="ms-muted">Лояльность</span>
        <span>{facts.loyalty_points != null ? String(facts.loyalty_points) : '—'}</span>
        <span className="ms-muted">Телефон</span>
        <span>{facts.phone || '—'}</span>
        <span className="ms-muted">Telegram</span>
        <span>{facts.tg_nick || '—'}</span>
      </div>
      {hasAiSummary ? (
        <div className="ms-fact-block ms-facts-ai-summary">
          <p className="ms-ai-label">Саммари AI</p>
          {historyProse ? (
            <>
              <p className="ms-ai-label">История и профиль клиента</p>
              <p className="ms-facts-rec">{historyProse}</p>
            </>
          ) : null}
          {occasionProse ? (
            <>
              <p className="ms-ai-label">Повод и intent покупки</p>
              <p className="ms-facts-rec">{occasionProse}</p>
            </>
          ) : null}
          {recommendation ? (
            <>
              <p className="ms-ai-label">Рекомендация AI</p>
              <p className="ms-facts-rec">{recommendation}</p>
            </>
          ) : null}
        </div>
      ) : null}
      {!historyProse ? <FactBlockView block={historyBlock} /> : null}
      {!occasionProse ? <FactBlockView block={occasionBlock} /> : null}
      <FactBlockView block={risksBlock} />
      <ConversationThread
        compact
        conversation={facts.conversation}
        title="TG conversation"
      />
      {last ? (
        <div className="ms-last-order">
          <strong>Последний заказ</strong>
          <div className="ms-muted">
            {(last.date || '').slice(0, 16).replace('T', ' ')} · {money(last.sum)}
            {last.channel ? ` · ${last.channel}` : ''}
          </div>
          {last.product_snippet ? <div>{last.product_snippet}</div> : null}
        </div>
      ) : null}
      <TagPills className="ms-tag-row ms-tag-event" items={facts.event_tags || []} />
      <TagPills items={facts.tags?.slice(0, 12)} />
      {(facts.orders_preview || []).length ? (
        <div className="ms-orders-list ms-facts-orders">
          {(facts.orders_preview || []).slice(0, 5).map((o, idx) => (
            <div className="ms-order-row" key={`${o.date || ''}-${idx}`}>
              <div className="ms-muted">
                {(o.date || '').slice(0, 16).replace('T', ' ')} · {money(o.sum)}
                {o.channel ? ` · ${o.channel}` : ''}
              </div>
              {o.product_snippet ? <div>{o.product_snippet}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
      {sanity ? (
        <div className={`ms-sanity${sanity.ok ? ' is-ok' : ' is-bad'}`}>
          <p className="ms-ai-label">Проверка смысла</p>
          <p className="ms-muted">
            {sanity.ok
              ? 'Ок — явных конфликтов с долгом/рисками нет.'
              : (sanity.issues || []).join(' ') || 'Есть замечания к тексту.'}
            {sanity.auto_revised ? ' Текст автоматически скорректирован.' : ''}
          </p>
        </div>
      ) : null}
      {notes ? <p className="ms-muted ms-grounding">{notes}</p> : null}
      {facts.ai_source ? <p className="ms-muted">AI: {facts.ai_source}</p> : null}
    </aside>
  )
}

function ClientCardModal({
  clientId,
  onClose,
  call
}: {
  clientId: string | null
  onClose: () => void
  call: Rest
}) {
  const [detail, setDetail] = useState<ClientDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [syncLoading, setSyncLoading] = useState(false)
  const [aiProvider, setAiProvider] = useState('')
  const [aiModel, setAiModel] = useState('')
  const [error, setError] = useState('')
  const [ordersOpen, setOrdersOpen] = useState(true)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!clientId) {return}
    let cancelled = false
    setLoading(true)
    setError('')
    setDetail(null)
    setOrdersOpen(false)
    setNote('')
    void call<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`)
      .then(payload => {
        if (!cancelled) {setDetail(payload)}
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setLoading(false)}
      })

    return () => {
      cancelled = true
    }
  }, [call, clientId])

  if (!clientId) {return null}

  const client = detail?.client || {}
  const stats = detail?.stats || {}
  const orders = detail?.orders || []
  const ai = detail?.ai || {}
  const msg = detail?.messaging || {}
  const conversation = detail?.conversation
  const buckets = client.tag_buckets || {}
  const name = client.name || 'Клиент'
  const shownOrders = ordersOpen ? orders : orders.slice(0, 5)

  const refreshAi = async () => {
    setAiLoading(true)
    setError('')

    try {
      const payload = await call<{ ai?: ClientDetail['ai'] }>(
        `/clients/${encodeURIComponent(clientId)}/ai`,
        {
          method: 'POST',
          body: {
            provider: aiProvider || undefined,
            model: aiModel || undefined
          }
        }
      )

      setDetail(prev => (prev ? { ...prev, ai: payload.ai || prev.ai } : prev))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAiLoading(false)
    }
  }

  const syncTelegramHistory = async () => {
    setSyncLoading(true)
    setError('')

    try {
      const data = await call<{
        conversation?: ClientConversation & {
          sync?: { imported?: number; matched_sessions?: number; ok?: boolean; reason?: string }
        }
      }>(`/clients/${encodeURIComponent(clientId)}/conversation/sync`, {
        method: 'POST'
      })

      if (data.conversation) {
        setDetail(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
        const sync = data.conversation.sync
        if (sync?.reason === 'no_tg_nick_or_phone') {
          setError('Нет ТГ ника / телефона — sync невозможен')
        } else if (sync && sync.ok === false && sync.reason) {
          setError(`Sync: ${sync.reason}`)
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncLoading(false)
    }
  }

  const sendAndRecord = async (channel: 'whatsapp' | 'telegram') => {
    const text = note.trim()

    if (!text) {
      setError('Введите текст в поле ниже — он уйдёт в Telegram / историю.')

      return
    }

    setError('')

    try {
      const data = await call<{
        conversation?: ClientConversation
        deep_link?: string
        delivery?: { ok?: boolean; detail?: string; error?: string }
      }>(`/clients/${encodeURIComponent(clientId)}/conversation`, {
        method: 'POST',
        body: {
          text,
          direction: 'outbound',
          channel,
          source: 'client_card_send',
          open_deep_link: true
        }
      })

      if (data.conversation) {
        setDetail(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
      }

      if (channel === 'telegram' && data.delivery?.ok) {
        setNote('')

        return
      }

      if (channel === 'telegram' && data.delivery && data.delivery.ok === false && !data.delivery.error?.includes('skipped')) {
        const detail = data.delivery.detail || data.delivery.error || 'не отправлено'
        setError(`Telegram Bot: ${detail}. Откроется deep-link, если есть.`)
      }

      if (data.deep_link) {window.open(data.deep_link, '_blank', 'noopener')}
      setNote('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
    >
      <div aria-modal="true" className="ms-modal ms-client-card" role="dialog">
        <div className="ms-card-head">
          <h3>Заказы · {name}</h3>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        {error ? <div className="ms-error">{error}</div> : null}
        {loading ? (
          <p className="ms-muted">Загрузка карточки…</p>
        ) : (
          <div className="ms-card-body">
            <section className="ms-card-section">
              <div className="ms-card-name">{name}</div>
              <div className="ms-muted">
                {(client.role || '—') +
                  (client.sex ? ` · ${client.sex}` : '') +
                  (client.state ? ` · ${client.state}` : '')}
              </div>
              <TagPills
                className="ms-tag-row ms-tag-channel"
                items={[...(buckets.marketplace || []), ...(client.channels || [])]}
              />
              <TagPills items={[...(buckets.loyalty || []), ...(buckets.other || [])]} />
              <TagPills className="ms-tag-row ms-tag-event" items={buckets.events || []} />
            </section>
            <section className="ms-card-section">
              <h4>Контакты</h4>
              <div className="ms-kv-grid">
                <span className="ms-muted">Телефон</span>
                <span>{client.phone || '—'}</span>
                <span className="ms-muted">Email</span>
                <span>{client.email || '—'}</span>
                <span className="ms-muted">Telegram</span>
                <span>{client.tg_nick || '—'}</span>
                <span className="ms-muted">Тип</span>
                <span>{client.company_type || '—'}</span>
                <span className="ms-muted">Осн. канал</span>
                <span>{client.primary_channel || msg.primary_channel || '—'}</span>
              </div>
            </section>
            <section className="ms-card-section">
              <div className="ms-card-head">
                <h4>Переписка</h4>
                <button
                  className="ms-btn"
                  disabled={syncLoading}
                  onClick={() => void syncTelegramHistory()}
                  title="Подтянуть историю из gateway Telegram по нику/телефону"
                  type="button"
                >
                  {syncLoading ? 'Sync…' : 'Sync Telegram'}
                </button>
              </div>
              <ConversationThread conversation={conversation} title="TG conversation" />
            </section>
            <section className="ms-card-section">
              <h4>Статистика</h4>
              <div className="ms-stats-grid">
                <div>
                  <div className="ms-stat-val">{money(stats.avg_check)}</div>
                  <div className="ms-muted">Средний чек</div>
                </div>
                <div>
                  <div className="ms-stat-val">{String(stats.order_count || 0)}</div>
                  <div className="ms-muted">Заказов</div>
                </div>
                <div>
                  <div className="ms-stat-val">{stats.vip ? 'да' : 'нет'}</div>
                  <div className="ms-muted">ВИП</div>
                </div>
                <div>
                  <div className="ms-stat-val">
                    {stats.loyalty_points != null ? String(stats.loyalty_points) : '—'}
                  </div>
                  <div className="ms-muted">Лояльность</div>
                </div>
              </div>
              {stats.last_order ? (
                <div className="ms-last-order">
                  <strong>Последний заказ</strong>
                  <div className="ms-muted">
                    {(stats.last_order.date || '').slice(0, 16).replace('T', ' ')} ·{' '}
                    {money(stats.last_order.sum)}
                    {stats.last_order.channel ? ` · ${stats.last_order.channel}` : ''}
                  </div>
                  {stats.last_order.product_snippet ? (
                    <div>{stats.last_order.product_snippet}</div>
                  ) : null}
                </div>
              ) : null}
            </section>
            <section className="ms-card-section">
              <button className="ms-section-toggle" onClick={() => setOrdersOpen(v => !v)} type="button">
                Все заказы ({orders.length}) {ordersOpen ? '▾' : '▸'}
              </button>
              <div className="ms-orders-list">
                {shownOrders.length ? (
                  shownOrders.map((o, idx) => (
                    <div className="ms-order-row" key={`${o.id || ''}-${idx}`}>
                      <strong>{o.name || o.id || 'Заказ'}</strong>
                      <div className="ms-muted">
                        {(o.date || '').slice(0, 16).replace('T', ' ')} · {money(o.sum)}
                        {o.channel ? ` · ${o.channel}` : ''}
                      </div>
                      {o.product_snippet ? <div>{o.product_snippet}</div> : null}
                    </div>
                  ))
                ) : (
                  <p className="ms-muted">Заказов в кэше нет.</p>
                )}
              </div>
            </section>
            <section className="ms-card-section">
              <div className="ms-card-head">
                <h4>Саммари AI</h4>
                <div className="ms-chips">
                  <button className="ms-btn" disabled={aiLoading} onClick={() => void refreshAi()} type="button">
                    {aiLoading ? 'Генерация…' : 'Обновить AI'}
                  </button>
                </div>
              </div>
              <div className="ms-filter-block">
                <span className="ms-filter-label">Модель (эксперимент)</span>
                <div className="ms-chips">
                  {[
                    { provider: '', model: '', label: 'default' },
                    { provider: 'openrouter', model: 'deepseek/deepseek-chat', label: 'DeepSeek' },
                    { provider: 'openrouter', model: 'openai/gpt-4o-mini', label: 'GPT-4o-mini' },
                    { provider: 'openrouter', model: 'anthropic/claude-3.5-haiku', label: 'Haiku' }
                  ].map(opt => {
                    const active =
                      (aiProvider || '') === opt.provider && (aiModel || '') === opt.model

                    return (
                      <button
                        className={`ms-chip${active ? ' is-active' : ''}`}
                        key={opt.label}
                        onClick={() => {
                          setAiProvider(opt.provider)
                          setAiModel(opt.model)
                        }}
                        type="button"
                      >
                        {opt.label}
                      </button>
                    )
                  })}
                </div>
              </div>
              {ai.data_thin ? <p className="ms-muted">Данных мало — выводы осторожные.</p> : null}
              <p className="ms-ai-label">История и профиль</p>
              <p>{ai.history_profile || '—'}</p>
              <p className="ms-ai-label">Повод и intent покупки</p>
              <p>{ai.occasion_intent || '—'}</p>
              <h4>Рекомендация AI</h4>
              <p>{ai.recommendation || '—'}</p>
              <p className="ms-muted">
                Источник: {ai.source || 'heuristic'}
                {aiProvider || aiModel
                  ? ` · ${[aiProvider, aiModel].filter(Boolean).join('/')}`
                  : ''}
              </p>
            </section>
            <section className="ms-card-section">
              <h4>Быстрые действия</h4>
              <div className="ms-quick-actions">
                <button
                  className="ms-btn"
                  onClick={() =>
                    setNote(
                      `Напоминание: связаться с ${name} (~5 дней до повода). Чек ≈ ${money(stats.avg_check)}`
                    )
                  }
                  type="button"
                >
                  Напоминание
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={!note.trim()}
                  onClick={() => void sendAndRecord('whatsapp')}
                  type="button"
                >
                  WhatsApp → история
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={!note.trim()}
                  onClick={() => void sendAndRecord('telegram')}
                  type="button"
                >
                  Telegram → история
                </button>
                <button
                  className="ms-btn"
                  onClick={() => {
                    setOrdersOpen(true)
                    setNote(`События: ${(buckets.events || []).join(', ') || 'нет тегов событий'}`)
                  }}
                  type="button"
                >
                  События
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  onClick={() => {
                    const sales =
                      (client.sales_type || '').toLowerCase().includes('маркет')
                        ? 'marketplace'
                        : (client.sales_type || '').toLowerCase().includes('прям')
                          ? 'direct'
                          : 'all'

                    writeDraftPrefill({
                      clientId,
                      channel: channelFromMessaging(msg.primary_channel || client.primary_channel),
                      salesFilter: sales
                    })
                    onClose()
                    host.navigate('/campaigns')
                  }}
                  type="button"
                >
                  Черновик рассылки
                </button>
              </div>
              <label className="ms-send-note">
                Текст для отправки / записи в историю
                <textarea
                  onChange={e => setNote(e.target.value)}
                  placeholder="Напишите исходящее — кнопка WhatsApp/Telegram запишет его в TG conversation и откроет чат…"
                  rows={3}
                  value={note}
                />
              </label>
              <p className="ms-muted">{msg.hint || ''}</p>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}

const CLIENTS_PAGE_SIZE = 100
const CLIENTS_LOCAL_CACHE_PREFIX = 'hermes.moysklad.clients.v2:'
const CLIENTS_LOCAL_CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
const CLIENTS_REVALIDATE_POLL_MS = 4000
const CLIENTS_REVALIDATE_POLL_MAX_MS = 90_000

interface ClientsLocalCachePayload {
  saved_at: number
  sales_filter: string
  q: string
  group: string
  group_source: string
  clients: ClientRow[]
  counts: Counts | null
  matched_total: number
  has_more: boolean
  next_offset: number
  group_options_ms: GroupChipOption[]
  group_options_ai: GroupChipOption[]
  synced_at_label: string
  from_cache: boolean
}

function clientsLocalCacheKey(parts: {
  salesFilter: string
  q: string
  group: string
  groupSource: string
}): string {
  return (
    CLIENTS_LOCAL_CACHE_PREFIX +
    [parts.salesFilter, parts.groupSource, parts.group, parts.q].join('|')
  )
}

function readClientsLocalCache(key: string): ClientsLocalCachePayload | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) {return null}
    const parsed = JSON.parse(raw) as ClientsLocalCachePayload
    if (!parsed || !Array.isArray(parsed.clients) || !parsed.clients.length) {
      return null
    }
    if (
      typeof parsed.saved_at !== 'number' ||
      Date.now() - parsed.saved_at > CLIENTS_LOCAL_CACHE_MAX_AGE_MS
    ) {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writeClientsLocalCache(key: string, payload: ClientsLocalCachePayload): void {
  try {
    localStorage.setItem(key, JSON.stringify(payload))
  } catch {
    // Quota / private mode — ignore; network path still works.
  }
}

function mergeClientPages(prev: ClientRow[], incoming: ClientRow[]): ClientRow[] {
  const seen = new Set<string>()
  const out: ClientRow[] = []

  for (const row of [...prev, ...incoming]) {
    const id = String(row.id || '').trim()

    if (id) {
      if (seen.has(id)) {continue}
      seen.add(id)
    }

    out.push(row)
  }

  return out
}

function ClientsPage() {
  const call = useMsRest()
  const [salesFilter, setSalesFilter] = useState('direct')
  const [q, setQ] = useState('')
  const [group, setGroup] = useState('')
  const [groupSource, setGroupSource] = useState<'any' | 'ms' | 'ai'>('any')
  const initialLocal = (() => {
    if (typeof localStorage === 'undefined') {return null}
    return readClientsLocalCache(
      clientsLocalCacheKey({
        salesFilter: 'direct',
        q: '',
        group: '',
        groupSource: 'any'
      })
    )
  })()
  const [loading, setLoading] = useState(!initialLocal)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [clients, setClients] = useState<ClientRow[]>(() => initialLocal?.clients || [])
  const [counts, setCounts] = useState<Counts | null>(() => initialLocal?.counts || null)
  const [matched, setMatched] = useState(() => initialLocal?.matched_total || 0)
  const [hasMore, setHasMore] = useState(() => Boolean(initialLocal?.has_more))
  const [nextOffset, setNextOffset] = useState(() => initialLocal?.next_offset || 0)
  const [groupOptionsMs, setGroupOptionsMs] = useState<GroupChipOption[]>(
    () => initialLocal?.group_options_ms || []
  )
  const [groupOptionsAi, setGroupOptionsAi] = useState<GroupChipOption[]>(
    () => initialLocal?.group_options_ai || []
  )
  const [integrityNote, setIntegrityNote] = useState('')
  const [syncedLabel, setSyncedLabel] = useState(() => initialLocal?.synced_at_label || '')
  const [fromCache, setFromCache] = useState(() => Boolean(initialLocal?.from_cache ?? initialLocal))
  const [staleHint, setStaleHint] = useState(Boolean(initialLocal))
  const [cardClientId, setCardClientId] = useState<string | null>(null)
  const [recalcOpen, setRecalcOpen] = useState(false)
  const [recalcLoading, setRecalcLoading] = useState(false)
  const [recalcGroups, setRecalcGroups] = useState('')
  const [recalcSource, setRecalcSource] = useState('')

  const [recalcPreview, setRecalcPreview] = useState<{ changed?: number; total?: number } | null>(
    null
  )

  const [recalcError, setRecalcError] = useState('')
  const [aiFillStatus, setAiFillStatus] = useState('')
  const loadGen = useRef(0)
  const loadingMoreRef = useRef(false)
  const lazyAiTriedRef = useRef(new Set<string>())
  const lazyAiInFlightRef = useRef(false)
  const clientsRef = useRef<ClientRow[]>([])
  clientsRef.current = clients

  /** Auto-fill empty CRM fields for every currently shown row (batched). */
  const drainLazyAiFill = useCallback(() => {
    if (lazyAiInFlightRef.current) {
      return
    }

    const pending = clientsRef.current
      .filter(rowNeedsLazyAiFill)
      .map(r => String(r.id || '').trim())
      .filter(id => id && !lazyAiTriedRef.current.has(id))

    if (!pending.length) {
      return
    }

    const ids = pending.slice(0, LAZY_AI_BATCH)

    for (const id of ids) {
      lazyAiTriedRef.current.add(id)
    }

    lazyAiInFlightRef.current = true
    const shown = clientsRef.current.length
    setAiFillStatus(`AI: заполняю ${ids.length} из ${shown} показанных…`)

    void call<{
      updated?: number
      cached?: number
      filled_field_count?: number
      source?: string
      ai_fill_cache_backend?: string
      cache_backend?: string
      results?: Array<{
        id?: string
        filled?: Record<string, unknown>
        fields?: Record<string, unknown>
        ai_fields?: string[]
        source?: string
      }>
    }>('/clients/ai-fill', {
      method: 'POST',
      body: {
        ids,
        limit: ids.length,
        use_llm: true,
        force: false
      },
      timeoutMs: 120_000
    })
      .then(data => {
        setClients(prev => applyAiFillResults(prev, data.results || []))
        const backend = data.ai_fill_cache_backend || data.cache_backend || ''
        const left = Math.max(0, pending.length - ids.length)
        setAiFillStatus(
          `✓ AI: +${data.updated || 0}` +
            (data.cached ? ` · кэш ${data.cached}` : '') +
            (data.filled_field_count ? ` · полей ${data.filled_field_count}` : '') +
            (backend ? ` · ${backend}` : '') +
            (left ? ` · ещё ${left}…` : ` · показано ${shown}`)
        )
      })
      .catch(err => {
        // Keep ids in tried to avoid tight retry loops; filter/refresh clears tried.
        setAiFillStatus(`AI ошибка: ${err instanceof Error ? err.message : String(err)}`)
      })
      .finally(() => {
        lazyAiInFlightRef.current = false
        // Next batch for remaining shown rows (e.g. 50→100 after scroll).
        queueMicrotask(() => drainLazyAiFill())
      })
  }, [call])

  const load = useCallback(
    async (opts?: { refresh?: boolean; append?: boolean; offset?: number }) => {
      const append = Boolean(opts?.append)
      const offset = append ? (opts?.offset ?? nextOffset) : 0
      const gen = append ? loadGen.current : ++loadGen.current
      const cacheKey = clientsLocalCacheKey({ salesFilter, q, group, groupSource })

      if (append) {
        if (loadingMoreRef.current || !hasMore) {return}
        loadingMoreRef.current = true
        setLoadingMore(true)
      } else {
        // CDN-style: paint local snapshot immediately, then revalidate.
        if (!opts?.refresh) {
          const local = readClientsLocalCache(cacheKey)
          if (local) {
            setClients(local.clients)
            setCounts(local.counts)
            setMatched(local.matched_total || 0)
            setNextOffset(local.next_offset || local.clients.length)
            setHasMore(Boolean(local.has_more))
            setGroupOptionsMs(local.group_options_ms || [])
            setGroupOptionsAi(local.group_options_ai || [])
            setFromCache(true)
            setStaleHint(true)
            setSyncedLabel(local.synced_at_label || '')
            setLoading(false)
          } else {
            setClients([])
            setCounts(null)
            setMatched(0)
            setHasMore(false)
            setNextOffset(0)
            setLoading(true)
            setStaleHint(false)
          }
        } else {
          setLoading(true)
          setStaleHint(false)
        }
        setError('')
        lazyAiTriedRef.current.clear()
        setAiFillStatus('')
      }

      try {
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          q,
          group,
          group_source: groupSource,
          limit: String(CLIENTS_PAGE_SIZE),
          offset: String(offset)
        })

        if (opts?.refresh) {params.set('refresh', 'true')}

        const data = await call<{
          clients?: ClientRow[]
          counts?: Counts
          matched_total?: number
          has_more?: boolean
          next_offset?: number
          returned?: number
          group_options?: GroupChipOption[]
          group_options_by_source?: { ms?: GroupChipOption[]; ai?: GroupChipOption[] }
          cached?: boolean
          stale?: boolean
          revalidating?: boolean
          synced_at_label?: string
          synced_at?: number
        }>(`/clients?${params}`)

        if (gen !== loadGen.current) {return}
        const page = data.clients || []
        setClients(prev => (append ? mergeClientPages(prev, page) : page))
        setCounts(data.counts || null)
        setMatched(data.matched_total || 0)

        const computedNext =
          data.next_offset != null ? data.next_offset : offset + page.length

        setNextOffset(computedNext)
        setHasMore(
          data.has_more != null
            ? Boolean(data.has_more)
            : computedNext < (data.matched_total || 0)
        )

        if (!append) {
          const bySrc = data.group_options_by_source
          let msOpts: GroupChipOption[] = []
          let aiOpts: GroupChipOption[] = []
          if (bySrc) {
            msOpts = bySrc.ms || []
            aiOpts = bySrc.ai || []
            setGroupOptionsMs(msOpts)
            setGroupOptionsAi(aiOpts)
          } else {
            const all = data.group_options || []
            msOpts = all.filter(o => (o.source || 'ms') !== 'ai')
            aiOpts = all.filter(o => o.source === 'ai' || o.source === 'both')
            setGroupOptionsMs(msOpts)
            setGroupOptionsAi(aiOpts)
          }
          setFromCache(Boolean(data.cached))
          setStaleHint(Boolean(data.stale || data.revalidating))
          setSyncedLabel(data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''))
          if (data.counts) {
            const t = data.counts.total || 0
            const d = data.counts.direct || 0
            const m = data.counts.marketplace || 0
            setIntegrityNote(
              t === d + m
                ? `Вкладки: ${d}+${m}=${t} (partition OK)`
                : `⚠ вкладки ${d}+${m}≠${t}`
            )
          }
          writeClientsLocalCache(cacheKey, {
            saved_at: Date.now(),
            sales_filter: salesFilter,
            q,
            group,
            group_source: groupSource,
            clients: page,
            counts: data.counts || null,
            matched_total: data.matched_total || 0,
            has_more:
              data.has_more != null
                ? Boolean(data.has_more)
                : computedNext < (data.matched_total || 0),
            next_offset: computedNext,
            group_options_ms: msOpts,
            group_options_ai: aiOpts,
            synced_at_label:
              data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''),
            from_cache: Boolean(data.cached)
          })
        }
      } catch (err) {
        if (gen !== loadGen.current) {return}
        setError(err instanceof Error ? err.message : String(err))

        // Keep painted local/stale rows on soft refresh failure.
        if (!append && !clientsRef.current.length) {setClients([])}
      } finally {
        if (append) {
          loadingMoreRef.current = false
          setLoadingMore(false)
        } else if (gen === loadGen.current) {
          setLoading(false)
        }
      }
    },
    [call, group, groupSource, hasMore, nextOffset, q, salesFilter]
  )

  useEffect(() => {
    void load()
  }, [salesFilter, group, groupSource, q]) // eslint-disable-line react-hooks/exhaustive-deps -- reset list on filter change

  // While server rebuilds in background, poll until fresh (clears sticky «обновляем…»).
  useEffect(() => {
    if (!staleHint || loading) {
      return
    }

    const started = Date.now()
    const timer = window.setInterval(() => {
      if (Date.now() - started > CLIENTS_REVALIDATE_POLL_MAX_MS) {
        window.clearInterval(timer)
        setStaleHint(false)
        return
      }
      void load()
    }, CLIENTS_REVALIDATE_POLL_MS)

    return () => window.clearInterval(timer)
  }, [staleHint, loading, load])

  // Lazy AI: whenever shown set grows/changes, fill empty cells for those rows.
  useEffect(() => {
    if (!clients.length || loading) {
      return
    }

    drainLazyAiFill()
  }, [clients, drainLazyAiFill, loading])

  const onTableScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget

      if (el.scrollHeight - el.scrollTop - el.clientHeight > 160) {return}

      if (!hasMore || loading || loadingMoreRef.current) {return}
      void load({ append: true, offset: nextOffset })
    },
    [hasMore, load, loading, nextOffset]
  )

  const cacheHint = syncedLabel
    ? `${fromCache ? (staleHint ? 'снимок (обновляем…)' : 'из кэша') : 'свежая выгрузка'} · синхр. ${syncedLabel}`
    : fromCache
      ? staleHint
        ? 'снимок (обновляем…)'
        : 'из кэша'
      : loading && clients.length
        ? 'обновляем…'
        : ''

  return (
    <div className="ms-page" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>Клиенты</h1>
          <p className="ms-muted">МойСклад · Маркетплейс / Прямые</p>
          {cacheHint ? <p className="ms-muted ms-sync-meta">{cacheHint}</p> : null}
        </div>
        <div className="ms-actions">
          <button className="ms-btn" disabled={loading} onClick={() => void load()} type="button">
            Обновить
          </button>
          <button
            className="ms-btn ms-btn-primary"
            disabled={loading}
            onClick={() => void load({ refresh: true })}
            title="Принудительно скачать данные из МойСклад и обновить кэш"
            type="button"
          >
            Синхронизация
          </button>
          <button
            className="ms-btn"
            disabled={loading || recalcLoading}
            onClick={() => {
              setRecalcOpen(true)
              setRecalcError('')
              setRecalcPreview(null)
              setRecalcLoading(true)
              void call<{ groups?: string[]; source?: string }>('/groups/recalculate/propose', {
                method: 'POST',
                body: { sales_filter: salesFilter, group, q }
              })
                .then(data => {
                  setRecalcGroups((data.groups || []).join('\n'))
                  setRecalcSource(data.source || '')
                })
                .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                .finally(() => setRecalcLoading(false))
            }}
            title="LLM предложит новые имена групп; подтверждение переназначит теги"
            type="button"
          >
            Пересчитать группы
          </button>
          <button className="ms-btn" onClick={() => host.navigate('/campaigns')} type="button">
            Рассылка
          </button>
          <button
            className="ms-btn"
            onClick={() => host.navigate('/clients/playground')}
            title="AI тест: монитор качества Саммари / Повод / Рекомендация"
            type="button"
          >
            AI тест
          </button>
        </div>
      </div>
      <FilterTabs counts={counts} disabled={loading} onChange={setSalesFilter} salesFilter={salesFilter} />
      <div className="ms-search">
        <input
          onChange={e => setQ(e.target.value)}
          placeholder="Поиск по имени / телефону…"
          type="search"
          value={q}
        />
      </div>
      <GroupCloudSection
        activeGroup={group}
        activeSource={groupSource}
        items={groupOptionsMs}
        limit={28}
        onToggle={(name, source) => {
          if (group === name && groupSource === source) {
            setGroup('')
            setGroupSource('any')
          } else {
            setGroup(name)
            setGroupSource(source)
          }
        }}
        sourceKey="ms"
        title="Группы: Мой склад"
      />
      <GroupCloudSection
        activeGroup={group}
        activeSource={groupSource}
        items={groupOptionsAi}
        limit={28}
        onToggle={(name, source) => {
          if (group === name && groupSource === source) {
            setGroup('')
            setGroupSource('any')
          } else {
            setGroup(name)
            setGroupSource(source)
          }
        }}
        sourceKey="ai"
        title="Группы: ИИ"
      />
      {error ? <div className="ms-error">{error}</div> : null}
      <p className="ms-muted">
        Найдено: {matched}
        {clients.length ? ` · показано ${clients.length}` : ''}
        {integrityNote ? ` · ${integrityNote}` : ''}
      </p>
      {recalcOpen ? (
        <div className="ms-modal-backdrop" onClick={() => setRecalcOpen(false)}>
          <div
            className="ms-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
          >
            <div className="ms-card-head">
              <h2 className="ms-section-title">Пересчитать группы</h2>
              <button className="ms-btn" onClick={() => setRecalcOpen(false)} type="button">
                Закрыть
              </button>
            </div>
            <p className="ms-muted">
              Отредактируйте названия (по одному на строку), затем подтвердите. Источник:{' '}
              {recalcSource || '…'}
            </p>
            {recalcError ? <div className="ms-error">{recalcError}</div> : null}
            <textarea
              disabled={recalcLoading}
              onChange={e => setRecalcGroups(e.target.value)}
              rows={12}
              style={{ width: '100%' }}
              value={recalcGroups}
            />
            {recalcPreview ? (
              <p className="ms-muted">
                Превью: изменится {recalcPreview.changed ?? 0} из {recalcPreview.total ?? 0}
              </p>
            ) : null}
            <div className="ms-compose-actions">
              <button
                className="ms-btn"
                disabled={recalcLoading || !recalcGroups.trim()}
                onClick={() => {
                  setRecalcLoading(true)
                  setRecalcError('')

                  const groups = recalcGroups
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean)

                  void call<{ changed?: number; total?: number }>('/groups/recalculate/apply', {
                    method: 'POST',
                    body: {
                      groups,
                      sales_filter: salesFilter,
                      group,
                      q,
                      dry_run: true,
                      push: false
                    }
                  })
                    .then(data => setRecalcPreview({ changed: data.changed, total: data.total }))
                    .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                    .finally(() => setRecalcLoading(false))
                }}
                type="button"
              >
                Превью
              </button>
              <button
                className="ms-btn ms-btn-primary"
                disabled={recalcLoading || !recalcGroups.trim()}
                onClick={() => {
                  setRecalcLoading(true)
                  setRecalcError('')

                  const groups = recalcGroups
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean)

                  void call('/groups/recalculate/apply', {
                    method: 'POST',
                    body: {
                      groups,
                      sales_filter: salesFilter,
                      group,
                      q,
                      dry_run: false,
                      push: true
                    }
                  })
                    .then(() => {
                      setRecalcOpen(false)
                      void load({ refresh: true })
                    })
                    .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                    .finally(() => setRecalcLoading(false))
                }}
                type="button"
              >
                Записать в МойСклад
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {aiFillStatus ? <p className="ms-action-status">{aiFillStatus}</p> : null}
      {loading && !clients.length ? (
        <p className="ms-muted">Загрузка клиентов…</p>
      ) : (
        <div className="ms-table-wrap" onScroll={onTableScroll}>
          {loading && clients.length ? (
            <p className="ms-muted ms-sync-meta">Обновляем список в фоне…</p>
          ) : null}
          <table className="ms-table">
            <thead>
              <tr>
                {CLIENT_COLUMNS.map(col => (
                  <th key={col.key}>{col.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {clients.map(row => (
                <tr key={row.id || row.name}>
                  {CLIENT_COLUMNS.map(col => {
                    const value = col.render(row)
                    const aiKey = AI_COLUMN_KEYS[col.key]
                    const isAi = Boolean(
                      aiKey && (row.ai_fields || []).includes(aiKey) && value
                    )

                    if (col.key === 'name') {
                      return (
                        <td key={col.key}>
                          <button
                            className="ms-link-btn"
                            onClick={() => row.id && setCardClientId(row.id)}
                            title="Открыть карточку клиента"
                            type="button"
                          >
                            {value || '—'}
                          </button>
                        </td>
                      )
                    }

                    return (
                      <td className={isAi ? 'ms-ai-added' : undefined} key={col.key}>
                        <AiCell ai={isAi} value={value} />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {loadingMore ? <p className="ms-muted ms-load-more">Подгружаем ещё…</p> : null}
          {!hasMore && clients.length > 0 ? (
            <p className="ms-muted ms-load-more">Все {matched} клиентов загружены</p>
          ) : null}
        </div>
      )}
      <ClientCardModal call={call} clientId={cardClientId} onClose={() => setCardClientId(null)} />
    </div>
  )
}

function CampaignsPage() {
  const call = useMsRest()
  const callStream = useMsRestStream()
  const [salesFilter, setSalesFilter] = useState('direct')
  const [mode, setMode] = useState<'manual' | 'auto'>('manual')
  const [title, setTitle] = useState('Рассылка по фильтрам')
  const [channel, setChannel] = useState('telegram')
  const [channelKind, setChannelKind] = useState('')
  const [group, setGroup] = useState('')
  const [groupSource, setGroupSource] = useState<'any' | 'ms' | 'ai'>('any')
  const [requirePhone, setRequirePhone] = useState(false)
  const [requireTelegram, setRequireTelegram] = useState(false)
  const [vipOnly, setVipOnly] = useState(false)
  const [birthdaySoon, setBirthdaySoon] = useState(false)
  const [daysBeforeEvent, setDaysBeforeEvent] = useState(0)
  const [personalize, setPersonalize] = useState(false)
  const [batchProgress, setBatchProgress] = useState('')
  const [offer, setOffer] = useState('')
  const [offerTick, setOfferTick] = useState(0)
  const [actionStatus, setActionStatus] = useState('')
  const offerRef = useRef('')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [audience, setAudience] = useState(0)
  const [audiencePreview, setAudiencePreview] = useState<ClientRow[]>([])
  const [audienceQ, setAudienceQ] = useState('')
  const [audienceQDebounced, setAudienceQDebounced] = useState('')
  const [audienceHasMore, setAudienceHasMore] = useState(false)
  const [audienceNextOffset, setAudienceNextOffset] = useState(0)
  const [audienceLoadingMore, setAudienceLoadingMore] = useState(false)
  const [groupOptionsMs, setGroupOptionsMs] = useState<GroupChipOption[]>([])
  const [groupOptionsAi, setGroupOptionsAi] = useState<GroupChipOption[]>([])
  const [selectedClientId, setSelectedClientId] = useState<string | null>(null)
  const [selectedClientName, setSelectedClientName] = useState('')
  const [facts, setFacts] = useState<ClientFacts | null>(null)
  const [groundingNotes, setGroundingNotes] = useState('')
  const [genSource, setGenSource] = useState('')
  const [sellerName, setSellerName] = useState('')
  const [sellerFacts, setSellerFacts] = useState('')
  const [sellerLoaded, setSellerLoaded] = useState(false)
  const [bizConnectionId, setBizConnectionId] = useState('')
  const [telegramAccount, setTelegramAccount] = useState<{
    configured?: boolean
    bot_username?: string | null
    business_connection_configured?: boolean
    business_connection_id?: string | null
    account?: {
      ok?: boolean
      username?: string | null
      first_name?: string | null
      can_reply?: boolean
      can_read_messages?: boolean
      is_enabled?: boolean
      error?: string
      detail?: string
    } | null
  } | null>(null)
  const [bizSaving, setBizSaving] = useState(false)
  const [pickMode, setPickMode] = useState<'single' | 'multi'>('single')
  const [selectedClientIds, setSelectedClientIds] = useState<string[]>([])
  const [contactsOpen, setContactsOpen] = useState(true)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [rewriting, setRewriting] = useState(false)
  const [suggestingBouquet, setSuggestingBouquet] = useState(false)
  const [paraphrasing, setParaphrasing] = useState(false)
  const [checkingSanity, setCheckingSanity] = useState(false)
  const [sanity, setSanity] = useState<SanityResult | null>(null)
  const [error, setError] = useState('')
  const [prefillReady, setPrefillReady] = useState(false)
  const audienceLoadMoreRef = useRef(false)
  const sellerSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Bumps on each client switch / generate — stale stream events are ignored. */
  const outreachGenRef = useRef(0)
  const outreachAbortRef = useRef<AbortController | null>(null)
  const selectedClientNameRef = useRef('')
  const selectedClientIdRef = useRef<string | null>(null)
  const channelRef = useRef(channel)
  const titleRef = useRef(title)
  const factsRef = useRef<ClientFacts | null>(null)
  const groundingNotesRef = useRef('')
  const genSourceRef = useRef('')
  const sanityRef = useRef<SanityResult | null>(null)

  useEffect(() => {
    offerRef.current = offer
  }, [offer])

  useEffect(() => {
    selectedClientNameRef.current = selectedClientName
  }, [selectedClientName])

  useEffect(() => {
    selectedClientIdRef.current = selectedClientId
  }, [selectedClientId])

  useEffect(() => {
    channelRef.current = channel
  }, [channel])

  useEffect(() => {
    titleRef.current = title
  }, [title])

  useEffect(() => {
    factsRef.current = facts
  }, [facts])

  useEffect(() => {
    groundingNotesRef.current = groundingNotes
  }, [groundingNotes])

  useEffect(() => {
    genSourceRef.current = genSource
  }, [genSource])

  useEffect(() => {
    sanityRef.current = sanity
  }, [sanity])

  const applyOfferText = useCallback((next: string, status: string) => {
    const text = (next || '').trim() ? next : ''
    setOffer(text)
    offerRef.current = text
    setOfferTick(t => t + 1)
    setActionStatus(status)
  }, [])

  const restoreServerDraft = useCallback(
    (draft: {
      message?: string
      grounding_notes?: string
      source?: string
      status?: string
      client_name?: string
      title?: string
      facts?: ClientFacts | null
      sanity?: SanityResult | null
    }) => {
      const name = (draft.client_name || selectedClientNameRef.current || '').trim()

      if (name) {
        setSelectedClientName(name)
        selectedClientNameRef.current = name
        setTitle(draft.title || `Черновик · ${name}`)
      } else if (draft.title) {
        setTitle(draft.title)
      }

      setFacts(draft.facts || null)
      setSanity(draft.sanity || null)
      setGroundingNotes(draft.grounding_notes || '')
      setGenSource(draft.source || 'redis-cache')
      setError('')
      applyOfferText(draft.message || '', draft.status || AI_GENERATED_STATUS)
    },
    [applyOfferText]
  )

  /** Sync title + clear draft fields immediately (no auto LLM). */
  const applyClientSelectionUi = useCallback(
    (clientId: string, clientName: string) => {
      const name = (clientName || '').trim()
      setSelectedClientId(clientId)
      setSelectedClientName(name)
      selectedClientNameRef.current = name
      setTitle(name ? `Черновик · ${name}` : 'Черновик · клиент')
      setFacts(null)
      setSanity(null)
      setGroundingNotes('')
      setGenSource('')
      setError('')
      applyOfferText('', 'Загружаем кэш…')
    },
    [applyOfferText]
  )

  useEffect(() => {
    const prefill = readDraftPrefill()

    if (prefill) {
      setSelectedClientId(prefill.clientId)

      if (prefill.channel) {setChannel(prefill.channel)}

      if (prefill.salesFilter) {setSalesFilter(prefill.salesFilter)}
      setMode('auto')
      setTitle('Черновик · клиент')
    }

    setPrefillReady(true)
  }, [])

  useEffect(() => {
    void call<{
      seller_name?: string
      seller_facts?: string
      telegram_business_connection_id?: string
      telegram_account?: typeof telegramAccount
    }>('/campaigns/seller-settings')
      .then(data => {
        setSellerName(data.seller_name || '')
        setSellerFacts(data.seller_facts || '')
        const biz =
          data.telegram_business_connection_id ||
          data.telegram_account?.business_connection_id ||
          ''
        setBizConnectionId(biz)
        if (data.telegram_account) {
          setTelegramAccount(data.telegram_account)
        }
      })
      .catch(() => undefined)
      .finally(() => setSellerLoaded(true))
  }, [call])

  const persistSellerSettings = useCallback(
    (name: string, factsText: string, bizId?: string | null) => {
      if (sellerSaveTimer.current) {clearTimeout(sellerSaveTimer.current)}
      sellerSaveTimer.current = setTimeout(() => {
        const body: Record<string, string> = {
          seller_name: name,
          seller_facts: factsText
        }

        if (bizId !== undefined && bizId !== null) {
          body.telegram_business_connection_id = bizId
        }

        void call<{ telegram_account?: typeof telegramAccount }>(
          '/campaigns/seller-settings',
          {
            method: 'PUT',
            body
          }
        )
          .then(data => {
            if (data.telegram_account) {
              setTelegramAccount(data.telegram_account)
            }
          })
          .catch(() => undefined)
      }, 450)
    },
    [call]
  )

  const refreshTelegramAccount = useCallback(async () => {
    try {
      const data = await call<{
        configured?: boolean
        bot_username?: string | null
        business_connection_id?: string | null
        account?: typeof telegramAccount extends { account?: infer A } ? A : never
      }>('/campaigns/telegram-account')
      setTelegramAccount(data)
      if (data.business_connection_id) {
        setBizConnectionId(data.business_connection_id)
      }
    } catch {
      /* ignore probe errors — UI shows last known */
    }
  }, [call])

  const saveBusinessConnection = async () => {
    setBizSaving(true)
    setError('')

    try {
      const data = await call<{
        telegram_business_connection_id?: string
        telegram_account?: typeof telegramAccount
      }>('/campaigns/seller-settings', {
        method: 'PUT',
        body: {
          seller_name: sellerName,
          seller_facts: sellerFacts,
          telegram_business_connection_id: bizConnectionId.trim()
        }
      })
      setBizConnectionId(data.telegram_business_connection_id || bizConnectionId.trim())
      if (data.telegram_account) {
        setTelegramAccount(data.telegram_account)
      }
      setActionStatus(
        data.telegram_account?.account?.ok
          ? `✓ Business: @${data.telegram_account.account.username || 'аккаунт'} подключён`
          : 'Connection ID сохранён — проверьте права Reply в Telegram Business'
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBizSaving(false)
    }
  }

  useEffect(() => {
    const t = setTimeout(() => setAudienceQDebounced(audienceQ.trim()), 280)

    return () => clearTimeout(t)
  }, [audienceQ])

  const audienceFilterParams = useCallback(
    (opts?: { limit?: number; offset?: number; q?: string }) => {
      const params = new URLSearchParams({
        sales_filter: salesFilter,
        group,
        group_source: groupSource,
        q: opts?.q ?? audienceQDebounced,
        limit: String(opts?.limit ?? 40),
        offset: String(opts?.offset ?? 0)
      })

      if (channelKind) {params.set('channel_kind', channelKind)}

      if (requirePhone) {params.set('require_phone', 'true')}

      if (requireTelegram) {params.set('require_telegram', 'true')}

      if (vipOnly) {params.set('vip_only', 'true')}

      if (birthdaySoon) {params.set('birthday_soon', 'true')}

      if (daysBeforeEvent > 0) {
        params.set('days_before_event', String(daysBeforeEvent))
      }

      return params
    },
    [
      audienceQDebounced,
      birthdaySoon,
      channelKind,
      daysBeforeEvent,
      group,
      groupSource,
      requirePhone,
      requireTelegram,
      salesFilter,
      vipOnly
    ]
  )

  const loadAudience = useCallback(
    async (opts?: { append?: boolean }) => {
      const append = Boolean(opts?.append)
      const offset = append ? audienceNextOffset : 0

      if (append) {
        if (audienceLoadMoreRef.current || !audienceHasMore) {return}
        audienceLoadMoreRef.current = true
        setAudienceLoadingMore(true)
      } else {
        setLoading(true)
        setError('')
      }

      try {
        const page = await call<{
          counts?: Counts
          matched_total?: number
          clients?: ClientRow[]
          group_options?: GroupChipOption[]
          group_options_by_source?: { ms?: GroupChipOption[]; ai?: GroupChipOption[] }
          has_more?: boolean
          next_offset?: number
        }>(`/clients?${audienceFilterParams({ offset, limit: 40 })}`)

        const rows = page.clients || []
        setAudiencePreview(prev => (append ? mergeClientPages(prev, rows) : rows))
        setAudience(page.matched_total || 0)
        setCounts(page.counts || null)

        if (!append) {
          const bySrc = page.group_options_by_source
          if (bySrc) {
            setGroupOptionsMs(bySrc.ms || [])
            setGroupOptionsAi(bySrc.ai || [])
          } else {
            const all = page.group_options || []
            setGroupOptionsMs(all.filter(o => (o.source || 'ms') !== 'ai'))
            setGroupOptionsAi(all.filter(o => o.source === 'ai' || o.source === 'both'))
          }
        }
        const next = page.next_offset != null ? page.next_offset : offset + rows.length
        setAudienceNextOffset(next)
        setAudienceHasMore(
          page.has_more != null ? Boolean(page.has_more) : next < (page.matched_total || 0)
        )
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))

        if (!append) {setAudiencePreview([])}
      } finally {
        if (append) {
          audienceLoadMoreRef.current = false
          setAudienceLoadingMore(false)
        } else {
          setLoading(false)
        }
      }
    },
    [audienceFilterParams, audienceHasMore, audienceNextOffset, call]
  )

  const refresh = useCallback(async () => {
    setError('')

    try {
      const list = await call<{ campaigns?: Campaign[] }>('/campaigns')
      setCampaigns(list.campaigns || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }

    await loadAudience()
  }, [call, loadAudience])

  useEffect(() => {
    void loadAudience()
  }, [
    salesFilter,
    group,
    groupSource,
    channelKind,
    requirePhone,
    requireTelegram,
    vipOnly,
    birthdaySoon,
    daysBeforeEvent,
    audienceQDebounced
  ])  

  useEffect(() => {
    void call<{ campaigns?: Campaign[] }>('/campaigns')
      .then(list => setCampaigns(list.campaigns || []))
      .catch(() => undefined)
  }, [call])

  const loadCachedDraft = useCallback(
    async (clientId: string, nextChannel = channel) => {
      outreachAbortRef.current?.abort()
      outreachGenRef.current += 1
      const gen = outreachGenRef.current
      const isCurrent = () => gen === outreachGenRef.current

      setGenerating(false)
      setError('')
      setActionStatus('Загружаем кэш…')

      try {
        const q = new URLSearchParams({
          client_id: clientId,
          channel: nextChannel || 'telegram'
        })
        const data = await call<{
          hit?: boolean
          draft?: {
            message?: string
            grounding_notes?: string
            source?: string
            status?: string
            client_name?: string
            title?: string
            facts?: ClientFacts | null
            sanity?: SanityResult | null
          } | null
          cache_backend?: string
        }>(`/campaigns/draft-cache?${q}`)

        if (!isCurrent()) {
          return
        }

        if (data.hit && data.draft?.message?.trim()) {
          restoreServerDraft({
            ...data.draft,
            client_name: selectedClientNameRef.current || data.draft.client_name,
            title:
              selectedClientNameRef.current
                ? `Черновик · ${selectedClientNameRef.current}`
                : data.draft.title,
            source: data.draft.source || data.cache_backend || 'redis-cache'
          })

          return
        }

        applyOfferText(
          '',
          'Нет кэша — нажмите «Сгенерировать AI» / «Букет из истории» / другое.'
        )
        setGenSource('')
      } catch (err) {
        if (!isCurrent()) {
          return
        }

        setError(err instanceof Error ? err.message : String(err))
        applyOfferText('', 'Кэш недоступен — можно сгенерировать кнопкой.')
      }
    },
    [applyOfferText, call, channel, restoreServerDraft]
  )

  /** Force LLM generate (Сгенерировать AI). Result is saved to Redis on the server. */
  const loadOutreach = useCallback(
    async (clientId: string, nextChannel = channel) => {
      outreachAbortRef.current?.abort()
      const ac = new AbortController()
      outreachAbortRef.current = ac
      const gen = ++outreachGenRef.current
      const isCurrent = () => gen === outreachGenRef.current && !ac.signal.aborted

      setGenerating(true)
      setError('')

      const knownName = selectedClientNameRef.current.trim()

      if (knownName) {
        setTitle(`Черновик · ${knownName}`)
      }

      applyOfferText('', 'Генерируем креативный текст…')
      setSanity(null)
      setGroundingNotes('')
      setGenSource('')

      try {
        let streamed = ''

        await callStream('/campaigns/generate/stream', {
          method: 'POST',
          timeoutMs: OUTREACH_AI_TIMEOUT_MS,
          signal: ac.signal,
          body: {
            client_id: clientId,
            channel: nextChannel,
            refresh_ai: true,
            seller_name: sellerName,
            seller_facts: sellerFacts
          },
          onEvent: raw => {
            if (!isCurrent()) {
              return
            }

            if (!raw || typeof raw !== 'object') {
              return
            }

            const ev = raw as Record<string, unknown>
            const type = String(ev.type || '')

            if (type === 'status' && typeof ev.text === 'string') {
              setActionStatus(ev.text)
            } else if (type === 'delta' && typeof ev.text === 'string') {
              streamed += ev.text
              applyOfferText(streamed, 'Генерируем… (поток)')
            } else if (type === 'replace' && typeof ev.text === 'string') {
              streamed = ev.text
              applyOfferText(streamed, 'Текст обновлён')
            } else if (type === 'error') {
              const err = String(ev.error || 'stream error')
              setError(
                /403|security policy|access denied/i.test(err)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${err}`
                  : `LLM: ${err}`
              )
            } else if (type === 'done') {
              const nextFacts =
                ev.facts && typeof ev.facts === 'object' ? (ev.facts as ClientFacts) : null
              const nextSanity =
                ev.sanity && typeof ev.sanity === 'object'
                  ? (ev.sanity as SanityResult)
                  : null
              const nextNotes =
                typeof ev.grounding_notes === 'string' ? ev.grounding_notes : ''
              const nextSource = typeof ev.source === 'string' ? ev.source : ''

              if (nextFacts) {
                setFacts(nextFacts)
              }

              if (nextNotes) {
                setGroundingNotes(nextNotes)
              }

              if (nextSource) {
                setGenSource(nextSource)
              }

              if (nextSanity) {
                setSanity(nextSanity)
              }

              const msg = pickOutreachMessage(ev) || streamed
              const status = nextSanity?.auto_revised
                ? 'AI сгенерировал текст (sanity поправил формулировку).'
                : AI_GENERATED_STATUS

              if (msg) {
                streamed = msg
                applyOfferText(msg, status)
              } else {
                setError('Сервер не вернул текст сообщения. Попробуйте ещё раз.')
                setActionStatus('')
              }

              if (typeof ev.error === 'string' && ev.error) {
                setError(
                  /403|security policy|access denied/i.test(ev.error)
                    ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                    : `LLM: ${ev.error}`
                )
              }

              const remoteName =
                typeof ev.client_name === 'string' ? ev.client_name.trim() : ''
              const localName = selectedClientNameRef.current.trim()

              if (remoteName && !localName) {
                setSelectedClientName(remoteName)
                selectedClientNameRef.current = remoteName
                setTitle(`Черновик · ${remoteName}`)
              } else if (localName) {
                setTitle(`Черновик · ${localName}`)
              } else if (remoteName) {
                setTitle(`Черновик · ${remoteName}`)
              }

              if (ev.cached) {
                setGenSource(prev => prev || 'redis-cache')
              }
            }
          }
        })
      } catch (err) {
        if (!isCurrent()) {
          return
        }

        if (err instanceof Error && err.name === 'AbortError') {
          return
        }

        setError(err instanceof Error ? err.message : String(err))
        setActionStatus('')
      } finally {
        if (isCurrent()) {
          setGenerating(false)
        }
      }
    },
    [applyOfferText, callStream, channel, sellerFacts, sellerName]
  )

  useEffect(() => {
    if (!prefillReady || !selectedClientId) {
      return
    }

    // Default: load durable Redis/file cache — never auto-LLM on select/channel.
    void loadCachedDraft(selectedClientId, channel)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillReady, selectedClientId, channel])

  // Persist manual edits / button results to Redis/file so reopen skips LLM.
  useEffect(() => {
    const cid = selectedClientId
    const msg = offer.trim()

    if (!cid || !msg || generating || rewriting || paraphrasing || suggestingBouquet) {
      return
    }

    if (msg.length < 8) {
      return
    }

    const t = setTimeout(() => {
      void call('/campaigns/draft-cache', {
        method: 'PUT',
        body: {
          client_id: cid,
          channel,
          message: msg,
          grounding_notes: groundingNotes,
          source: genSource || 'manual',
          status: actionStatus || AI_GENERATED_STATUS,
          client_name: selectedClientNameRef.current || '',
          title: title || '',
          facts: facts || {},
          sanity: sanity || null
        }
      }).catch(() => undefined)
    }, 700)

    return () => clearTimeout(t)
  }, [
    actionStatus,
    call,
    channel,
    facts,
    genSource,
    generating,
    groundingNotes,
    offer,
    paraphrasing,
    rewriting,
    sanity,
    selectedClientId,
    suggestingBouquet,
    title
  ])

  const selectAudienceClient = (row: ClientRow) => {
    if (!row.id) {
      return
    }

    if (pickMode === 'multi') {
      setSelectedClientIds(prev =>
        prev.includes(row.id!)
          ? prev.filter(id => id !== row.id)
          : [...prev, row.id!]
      )

      return
    }

    const nextChannel = row.phone && !row.tg_nick ? 'whatsapp' : 'telegram'
    setMode('auto')
    setChannel(nextChannel)
    setSelectedClientIds([row.id])
    outreachAbortRef.current?.abort()
    outreachGenRef.current += 1
    applyClientSelectionUi(row.id, row.name || '')
  }

  const markSentToConversation = async () => {
    const draft = offerRef.current.trim()
    const multiIds =
      pickMode === 'multi'
        ? selectedClientIds
        : selectedClientId
          ? [selectedClientId]
          : []

    if (!multiIds.length) {
      setError('Выберите контакт(ы) в аудитории — исходящее уйдёт в Telegram / историю.')

      return
    }

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setCheckingSanity(true)
    setError('')
    setActionStatus(
      channel.startsWith('telegram')
        ? multiIds.length > 1
          ? `Отправка ${multiIds.length} контактам через Telegram Business…`
          : 'Отправка через Telegram Business bot…'
        : 'Пишем исходящее в историю…'
    )

    try {
      if (multiIds.length > 1) {
        const data = await call<{
          sent_ok?: number
          total?: number
          results?: Array<{
            client_id?: string
            ok?: boolean
            delivery?: { ok?: boolean; detail?: string; error?: string }
            error?: string
          }>
        }>('/campaigns/mark-sent-batch', {
          method: 'POST',
          body: {
            message: draft,
            channel,
            client_ids: multiIds,
            open_deep_link: false,
            deliver: true
          }
        })
        const ok = data.sent_ok || 0
        const total = data.total || multiIds.length
        const failDetails = (data.results || [])
          .filter(r => !r.delivery?.ok)
          .slice(0, 3)
          .map(
            r =>
              `${r.client_id}: ${r.delivery?.detail || r.delivery?.error || r.error || 'fail'}`
          )
          .join('; ')

        applyOfferText(
          draft,
          `✓ Batch: ${ok}/${total} отправлено через Business bot`
        )

        if (ok < total) {
          setError(`Часть не ушла (${total - ok}): ${failDetails || 'см. логи'}`)
        }

        return
      }

      const data = await call<{
        conversation?: ClientConversation
        facts?: ClientFacts
        deep_link?: string
        delivery?: { ok?: boolean; detail?: string; error?: string; skipped?: boolean }
      }>('/campaigns/mark-sent', {
        method: 'POST',
        body: {
          message: draft,
          channel,
          client_id: multiIds[0],
          open_deep_link: true,
          deliver: true
        }
      })

      if (data.facts) {
        setFacts(data.facts)
      } else if (data.conversation) {
        setFacts(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
      }

      if (data.delivery?.ok) {
        applyOfferText(draft, '✓ Отправлено в Telegram (Business bot) + история.')
      } else if (channel.startsWith('telegram') && data.delivery && !data.delivery.skipped) {
        const detail = data.delivery.detail || data.delivery.error || 'ошибка'
        applyOfferText(
          draft,
          `⚠ В историю записано; Bot API: ${detail}`
        )
        setError(`Telegram: ${detail}`)
      } else {
        applyOfferText(draft, '✓ Исходящее добавлено в историю (лейбл исходящее).')
      }

      if (data.deep_link) {
        window.open(data.deep_link, '_blank', 'noopener')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setCheckingSanity(false)
    }
  }

  const regenerateAi = async () => {
    if (!selectedClientId) {
      setError('Сначала выберите клиента из аудитории или карточки.')

      return
    }

    await loadOutreach(selectedClientId, channel)
  }

  const humanizeDraft = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setRewriting(true)
    setError('')
    // Wipe draft while rewriting — seller sees the new sales pass replace it.
    applyOfferText('', 'Переписываем продающе и по-человечески…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/rewrite/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Переписываем… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed || draft
            streamed = msg
            applyOfferText(
              msg,
              msg.trim() === draft
                ? 'Переписали тон (текст почти тот же — правки лёгкие).'
                : 'Текст обновлён: продающе и по-человечески.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      // Restore draft if rewrite failed after we cleared the field.
      applyOfferText(draft, '')
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setRewriting(false)
    }
  }

  const suggestHistoricalBouquet = async () => {
    if (!selectedClientId) {
      setError('Сначала выберите клиента — нужна история заказов.')

      return
    }

    setSuggestingBouquet(true)
    setError('')
    applyOfferText('', 'Подбираем конкретный букет из истории…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/suggest-bouquet/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          client_id: selectedClientId,
          channel,
          refresh_ai: false,
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Букет из истории… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed
            streamed = msg
            applyOfferText(
              msg || '',
              msg
                ? 'Предложен конкретный букет из истории заказов.'
                : 'Не удалось предложить букет — проверьте историю.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setSuggestingBouquet(false)
    }
  }

  const paraphraseDraft = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setParaphrasing(true)
    setError('')
    applyOfferText('', 'Полная парафраза — другой текст, тот же смысл…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/paraphrase/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Парафраза… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed || draft
            streamed = msg
            const same = msg.trim() === draft
            applyOfferText(
              msg,
              same
                ? 'Парафраза почти совпала с исходником — попробуйте ещё раз.'
                : 'Полная парафраза: формулировки сменены, факты те же.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      applyOfferText(draft, '')
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setParaphrasing(false)
    }
  }

  const runSanityCheck = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setCheckingSanity(true)
    setError('')
    setActionStatus('Проверяем смысл…')

    try {
      const data = await call<{
        message?: string
        sanity?: SanityResult
        facts?: ClientFacts
      }>('/campaigns/sanity', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts,
          apply_revision: true
        }
      })

      const msg = pickOutreachMessage(data) || draft
      const ok = data.sanity?.ok !== false
      const revised = Boolean(data.sanity?.auto_revised || (msg.trim() && msg.trim() !== draft))

      applyOfferText(
        msg,
        revised
          ? 'Смысл: текст скорректирован (см. замечания справа).'
          : ok
            ? 'Смысл в порядке — текст оставлен как есть.'
            : `Смысл: ${(data.sanity?.issues || []).join('; ') || 'есть замечания'}.`
      )

      if (data.sanity) {
        setSanity(data.sanity)
      }

      if (data.facts && Object.keys(data.facts).length) {
        setFacts(data.facts)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setCheckingSanity(false)
    }
  }

  const createDraft = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setBatchProgress('')

    try {
      // Batch personalize: stream per-client drafts as soon as each finishes.
      if (personalize && !selectedClientId) {
        const limit = Math.min(Math.max(audience || 0, 1), 20)
        setActionStatus(`Персонализация 0/${limit}…`)
        setBatchProgress(`Старт · до ${limit} клиентов`)
        let firstMsg = ''
        let okCount = 0
        const pendingCreates: Promise<unknown>[] = []

        await callStream('/campaigns/personalize/stream', {
          method: 'POST',
          timeoutMs: Math.max(OUTREACH_AI_TIMEOUT_MS, limit * 45_000),
          body: {
            channel,
            sales_filter: salesFilter,
            group,
            channel_kind: channelKind,
            require_phone: requirePhone,
            require_telegram: requireTelegram,
            vip_only: vipOnly,
            birthday_soon: birthdaySoon,
            group_source: groupSource,
            days_before_event: daysBeforeEvent,
            seller_name: sellerName,
            seller_facts: sellerFacts,
            limit,
            max_workers: 3
          },
          onEvent: raw => {
            if (!raw || typeof raw !== 'object') {
              return
            }

            const ev = raw as Record<string, unknown>
            const type = String(ev.type || '')

            if (type === 'batch_start') {
              const total = Number(ev.total || limit)
              setActionStatus(`Персонализация 0/${total}…`)
              setBatchProgress(`Параллельно до 3 · всего ${total}`)
            } else if (type === 'client_done') {
              const done = Number(ev.done || 0)
              const total = Number(ev.total || limit)
              const name = String(ev.client_name || ev.client_id || 'клиент')
              const msg = String(ev.message || '').trim()
              const fromCache = Boolean(ev.from_cache)
              setActionStatus(
                `Персонализация ${done}/${total} · ${name}` +
                  (fromCache ? ' · кэш' : '')
              )
              setBatchProgress(
                `${done}/${total} · последний: ${name}` + (fromCache ? ' (кэш)' : '')
              )

              if (msg && ev.ok !== false) {
                okCount += 1

                if (!firstMsg) {
                  firstMsg = msg
                  applyOfferText(
                    msg,
                    fromCache
                      ? `Из кэша · ${name}`
                      : `Первый черновик · ${name}`
                  )
                }

                pendingCreates.push(
                  call('/campaigns', {
                    method: 'POST',
                    body: {
                      title: `${title || 'Рассылка'} · ${name}`.slice(0, 120),
                      channel,
                      mode: 'auto',
                      offer: msg,
                      sales_filter: salesFilter,
                      group,
                      channel_kind: channelKind,
                      require_phone: requirePhone,
                      require_telegram: requireTelegram,
                      vip_only: vipOnly,
                      birthday_soon: birthdaySoon,
            group_source: groupSource,
            days_before_event: daysBeforeEvent,
                      personalize: false,
                      client_id: String(ev.client_id || ''),
                      generate_ai: false,
                      seller_name: sellerName,
                      seller_facts: sellerFacts
                    }
                  }).catch(() => undefined)
                )
              }
            } else if (type === 'batch_done') {
              const hits = Number(ev.cache_hits || 0)
              setBatchProgress(
                `Готово: ${Number(ev.ok_count ?? okCount)} из ${Number(ev.total || limit)}` +
                  (hits ? ` · из кэша ${hits}` : '')
              )
            } else if (type === 'error') {
              setError(String(ev.error || 'batch error'))
            }
          }
        })

        await Promise.all(pendingCreates)
        setActionStatus(
          okCount > 0
            ? `Персонализация: сохранено ${okCount} черновиков.`
            : 'Персонализация: нет готовых текстов.'
        )
        await refresh()

        return
      }

      await call('/campaigns', {
        method: 'POST',
        body: {
          title,
          channel,
          mode,
          offer,
          sales_filter: salesFilter,
          group,
          channel_kind: channelKind,
          require_phone: requirePhone,
          require_telegram: requireTelegram,
          vip_only: vipOnly,
          birthday_soon: birthdaySoon,
          group_source: groupSource,
          days_before_event: daysBeforeEvent,
          personalize,
          client_id: selectedClientId || '',
          generate_ai: mode === 'auto' && !offer.trim(),
          seller_name: sellerName,
          seller_facts: sellerFacts
        }
      })

      if (!selectedClientId) {setOffer('')}
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const syncDeliveryChannel = (kind: string) => {
    setChannelKind(kind)

    if (kind === 'telegram') {
      setChannel('telegram')
      setRequireTelegram(true)
      setRequirePhone(false)
    } else if (kind === 'whatsapp') {
      setChannel('whatsapp')
      setRequirePhone(true)
      setRequireTelegram(false)
    } else {
      setRequirePhone(false)
      setRequireTelegram(false)
    }
  }

  return (
    <div className="ms-page" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>Рассылки</h1>
          <p className="ms-muted">Массовые черновики · аудитория = дедуп-кэш Клиентов</p>
        </div>
        <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
          ← Клиенты
        </button>
      </div>
      <FilterTabs counts={counts} disabled={loading} onChange={setSalesFilter} salesFilter={salesFilter} />

      <section className="ms-audience-builder">
        <h2 className="ms-section-title">Аудитория массовой рассылки</h2>
        <p className="ms-muted">
          Найдено (после дедупа): <strong>{audience}</strong>
          {loading ? ' · обновляем…' : ''}
          {selectedClientId ? (
            <>
              {' '}
              · выбран <strong>{selectedClientName || facts?.name || selectedClientId}</strong>
              <button
                className="ms-link-btn"
                onClick={() => {
                  outreachAbortRef.current?.abort()
                  outreachGenRef.current += 1
                  setSelectedClientId(null)
                  setSelectedClientName('')
                  setSelectedClientIds([])
                  selectedClientNameRef.current = ''
                  setFacts(null)
                  setGroundingNotes('')
                  setGenSource('')
                  setSanity(null)
                  setActionStatus('')
                  applyOfferText('', '')
                  setTitle('Рассылка по фильтрам')
                  setGenerating(false)
                }}
                style={{ marginLeft: '0.5rem' }}
                type="button"
              >
                сбросить клиента
              </button>
            </>
          ) : null}
        </p>
        <div className="ms-filter-block">
          <span className="ms-filter-label">Канал доставки</span>
          <div className="ms-filter-tabs" role="group">
            {[
              { id: '', label: 'Любой' },
              { id: 'telegram', label: 'Только Telegram' },
              { id: 'whatsapp', label: 'Только WhatsApp' }
            ].map(opt => (
              <button
                className={`ms-filter-tab${channelKind === opt.id ? ' is-active' : ''}`}
                key={opt.id || 'any'}
                onClick={() => syncDeliveryChannel(opt.id)}
                type="button"
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div className="ms-filter-block">
          <span className="ms-filter-label">Дополнительно</span>
          <div className="ms-chips">
            <button
              className={`ms-chip${vipOnly ? ' is-active' : ''}`}
              onClick={() => setVipOnly(v => !v)}
              type="button"
            >
              VIP
            </button>
            <button
              className={`ms-chip${requirePhone ? ' is-active' : ''}`}
              onClick={() => setRequirePhone(v => !v)}
              type="button"
            >
              Есть телефон
            </button>
            <button
              className={`ms-chip${requireTelegram ? ' is-active' : ''}`}
              onClick={() => setRequireTelegram(v => !v)}
              type="button"
            >
              Есть Telegram
            </button>
            <button
              className={`ms-chip${birthdaySoon ? ' is-active' : ''}`}
              onClick={() => setBirthdaySoon(v => !v)}
              type="button"
            >
              ДР / события
            </button>
          </div>
        </div>
        <div className="ms-filter-block">
          <span className="ms-filter-label">
            Связаться за N дней до события
            {daysBeforeEvent > 0 ? ` · окно ${daysBeforeEvent}д` : ''}
          </span>
          <div className="ms-chips">
            {[0, 3, 5, 7, 14].map(n => (
              <button
                className={`ms-chip${daysBeforeEvent === n ? ' is-active' : ''}`}
                key={n}
                onClick={() => {
                  setDaysBeforeEvent(n)
                  if (n > 0) {setBirthdaySoon(false)}
                }}
                type="button"
              >
                {n === 0 ? 'Выкл' : `${n} дн`}
              </button>
            ))}
          </div>
        </div>
        <GroupCloudSection
          activeGroup={group}
          activeSource={groupSource}
          items={groupOptionsMs}
          onToggle={(name, source) => {
            if (group === name && groupSource === source) {
              setGroup('')
              setGroupSource('any')
            } else {
              setGroup(name)
              setGroupSource(source)
            }
          }}
          sourceKey="ms"
          title="Группы: Мой склад"
        />
        <GroupCloudSection
          activeGroup={group}
          activeSource={groupSource}
          items={groupOptionsAi}
          onToggle={(name, source) => {
            if (group === name && groupSource === source) {
              setGroup('')
              setGroupSource('any')
            } else {
              setGroup(name)
              setGroupSource(source)
            }
          }}
          sourceKey="ai"
          title="Группы: ИИ"
        />
        <div className="ms-audience-pick">
          <div className="ms-audience-pick-head">
            <p className="ms-muted">
              Контакты аудитории (поиск / подгрузка — доступны все {audience}):
              {pickMode === 'multi' && selectedClientIds.length ? (
                <>
                  {' '}
                  · выбрано <strong>{selectedClientIds.length}</strong>
                  <button
                    className="ms-link-btn"
                    onClick={() => setSelectedClientIds([])}
                    style={{ marginLeft: '0.5rem' }}
                    type="button"
                  >
                    сбросить выбор
                  </button>
                </>
              ) : null}
            </p>
            <div className="ms-audience-pick-actions">
              <div className="ms-filter-tabs" role="group">
                <button
                  className={`ms-filter-tab${pickMode === 'single' ? ' is-active' : ''}`}
                  onClick={() => setPickMode('single')}
                  type="button"
                >
                  1 контакт
                </button>
                <button
                  className={`ms-filter-tab${pickMode === 'multi' ? ' is-active' : ''}`}
                  onClick={() => {
                    setPickMode('multi')
                    if (selectedClientId && !selectedClientIds.includes(selectedClientId)) {
                      setSelectedClientIds([selectedClientId])
                    }
                  }}
                  type="button"
                >
                  Несколько
                </button>
              </div>
              {audiencePreview.length ? (
                <button
                  className="ms-link-btn"
                  onClick={() => setContactsOpen(open => !open)}
                  type="button"
                >
                  {contactsOpen ? 'Скрыть контакты' : 'Показать контакты'}
                </button>
              ) : null}
            </div>
          </div>
          {contactsOpen ? (
            <>
              <div className="ms-search">
                <input
                  onChange={e => setAudienceQ(e.target.value)}
                  placeholder="Найти клиента в аудитории по имени / телефону / @tg…"
                  type="search"
                  value={audienceQ}
                />
              </div>
              {audiencePreview.length ? (
                <div
                  className="ms-audience-list"
                  onScroll={event => {
                    const el = event.currentTarget

                    if (el.scrollHeight - el.scrollTop - el.clientHeight > 120) {return}

                    if (!audienceHasMore || audienceLoadMoreRef.current) {return}
                    void loadAudience({ append: true })
                  }}
                >
                  <div className="ms-chips">
                    {audiencePreview.map(row => {
                      const active =
                        pickMode === 'multi'
                          ? Boolean(row.id && selectedClientIds.includes(row.id))
                          : selectedClientId === row.id
                      const nick = (row.tg_nick || '').replace(/^@/, '')

                      return (
                        <button
                          className={`ms-chip${active ? ' is-active' : ''}`}
                          key={row.id || row.name}
                          onClick={() => selectAudienceClient(row)}
                          title={nick ? `@${nick}` : row.phone || row.id}
                          type="button"
                        >
                          {row.name || row.phone || row.id}
                          {nick ? <span>@{nick}</span> : null}
                          {row.order_count != null ? <span>{row.order_count}</span> : null}
                        </button>
                      )
                    })}
                  </div>
                  {audienceLoadingMore ? (
                    <p className="ms-muted ms-load-more">Подгружаем клиентов…</p>
                  ) : null}
                  {audienceHasMore ? (
                    <button
                      className="ms-btn"
                      disabled={audienceLoadingMore}
                      onClick={() => void loadAudience({ append: true })}
                      type="button"
                    >
                      Ещё клиенты
                    </button>
                  ) : audiencePreview.length ? (
                    <p className="ms-muted ms-load-more">
                      Показано {audiencePreview.length} из {audience}
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="ms-muted">
                  {loading ? 'Загрузка аудитории…' : 'Нет клиентов под текущие фильтры / поиск.'}
                </p>
              )}
            </>
          ) : (
            <p className="ms-muted">
              Контакты скрыты
              {audiencePreview.length
                ? ` · загружено ${audiencePreview.length} из ${audience}`
                : ''}
              .
            </p>
          )}
        </div>
      </section>

      <div className="ms-filter-tabs" role="tablist">
        <button
          className={`ms-filter-tab${mode === 'manual' ? ' is-active' : ''}`}
          onClick={() => setMode('manual')}
          type="button"
        >
          Ручная
        </button>
        <button
          className={`ms-filter-tab${mode === 'auto' ? ' is-active' : ''}`}
          onClick={() => setMode('auto')}
          type="button"
        >
          Авто (AI)
        </button>
      </div>
      <div className="ms-compose-split">
        <form className="ms-campaign-form" onSubmit={event => void createDraft(event)}>
          <div className="ms-tg-account">
            <div className="ms-tg-account-head">
              <strong>Telegram Business аккаунт</strong>
              <button
                className="ms-link-btn"
                disabled={!sellerLoaded || bizSaving}
                onClick={() => void refreshTelegramAccount()}
                type="button"
              >
                Проверить
              </button>
            </div>
            <p className="ms-muted ms-tg-account-status">
              {telegramAccount?.bot_username
                ? `Бот @${telegramAccount.bot_username}`
                : 'Бот не настроен (MOYSKLAD_TELEGRAM_BOT_TOKEN)'}
              {telegramAccount?.account?.ok ? (
                <>
                  {' '}
                  · аккаунт{' '}
                  <strong>
                    @{telegramAccount.account.username || '—'}
                  </strong>
                  {telegramAccount.account.can_reply ? ' · reply ✓' : ' · reply ✗'}
                  {telegramAccount.account.can_read_messages ? ' · read ✓' : ' · read ✗'}
                </>
              ) : telegramAccount?.account && telegramAccount.account.ok === false ? (
                <>
                  {' '}
                  ·{' '}
                  {telegramAccount.account.detail ||
                    telegramAccount.account.error ||
                    'connection error'}
                </>
              ) : telegramAccount?.business_connection_configured ? (
                ' · connection id есть, нажмите «Проверить»'
              ) : (
                ' · добавьте connection id ниже'
              )}
            </p>
            <label>
              Business connection ID
              <input
                disabled={!sellerLoaded || bizSaving}
                onChange={e => setBizConnectionId(e.target.value)}
                placeholder="из Telegram Business → Chatbots / env"
                value={bizConnectionId}
              />
            </label>
            <button
              className="ms-btn"
              disabled={!sellerLoaded || bizSaving}
              onClick={() => void saveBusinessConnection()}
              type="button"
            >
              {bizSaving ? 'Сохраняем…' : 'Сохранить аккаунт'}
            </button>
            <p className="ms-muted">
              Настройка токена и connection id — в Офис → Telegram Business
              (отдельно от обычного Telegram). Telegram → Настройки → Business →
              Чат-боты → подключите бота к @аккаунту, включите Reply.
            </p>
          </div>
          <label>
            Название
            <input onChange={e => setTitle(e.target.value)} required value={title} />
          </label>
          <label>
            Канал отправки
            <select onChange={e => setChannel(e.target.value)} value={channel}>
              <option value="telegram">Telegram (личные)</option>
              <option value="telegram_channel">Telegram-канал</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </label>
          <label>
            Имя продавца / подпись
            <input
              disabled={!sellerLoaded}
              onChange={e => {
                const v = e.target.value
                setSellerName(v)
                persistSellerSettings(v, sellerFacts)
              }}
              placeholder='Напр. «Анна из Iris» или название магазина'
              value={sellerName}
            />
          </label>
          <label>
            Факты о продавце / магазине
            <textarea
              disabled={!sellerLoaded}
              onChange={e => {
                const v = e.target.value
                setSellerFacts(v)
                persistSellerSettings(sellerName, v)
              }}
              placeholder="Адрес, специализация, спой тон, что можно упомянуть…"
              rows={3}
              value={sellerFacts}
            />
          </label>
          <label>
            Текст сообщения
            <textarea
              key={`offer-${offerTick}`}
              onChange={e => {
                const v = e.target.value
                setOffer(v)
                offerRef.current = v
              }}
              placeholder={
                selectedClientId
                  ? 'Нажмите «Сгенерировать AI» или введите текст…'
                  : mode === 'auto'
                    ? 'Оставьте пустым — общий шаблон для фильтрованной аудитории'
                    : 'Общий текст массовой рассылки…'
              }
              rows={8}
              value={offer}
            />
          </label>
          {actionStatus ? <p className="ms-action-status">{actionStatus}</p> : null}
          <label className="ms-check">
            <input
              checked={personalize}
              disabled={Boolean(selectedClientId)}
              onChange={e => setPersonalize(e.target.checked)}
              type="checkbox"
            />
            Персонализировать по клиентам (стрим, до 20, параллельно)
          </label>
          {batchProgress ? <p className="ms-muted">{batchProgress}</p> : null}
          <div className="ms-compose-actions">
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={
                  generating || rewriting || checkingSanity || suggestingBouquet || paraphrasing
                }
                onClick={() => void regenerateAi()}
                type="button"
              >
                {generating ? 'Генерация…' : 'Сгенерировать AI'}
              </button>
            ) : null}
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={
                  generating || rewriting || checkingSanity || suggestingBouquet || paraphrasing
                }
                onClick={() => void suggestHistoricalBouquet()}
                title="Назвать конкретный букет из прошлых заказов клиента"
                type="button"
              >
                {suggestingBouquet ? 'Подбираем букет…' : 'Букет из истории'}
              </button>
            ) : null}
            <button
              className="ms-btn"
              disabled={
                rewriting ||
                generating ||
                checkingSanity ||
                suggestingBouquet ||
                paraphrasing ||
                !offer.trim()
              }
              onClick={() => void humanizeDraft()}
              type="button"
            >
              {rewriting ? 'Переписываем…' : 'Продающе и по-человечески'}
            </button>
            <button
              className="ms-btn"
              disabled={
                paraphrasing ||
                generating ||
                rewriting ||
                checkingSanity ||
                suggestingBouquet ||
                !offer.trim()
              }
              onClick={() => void paraphraseDraft()}
              title="Полная парафраза: другой текст, не generate и не sales-rewrite"
              type="button"
            >
              {paraphrasing ? 'Парафраза…' : 'Полная парафраза'}
            </button>
            <button
              className="ms-btn"
              disabled={
                checkingSanity ||
                generating ||
                rewriting ||
                suggestingBouquet ||
                paraphrasing ||
                !offer.trim()
              }
              onClick={() => void runSanityCheck()}
              type="button"
            >
              {checkingSanity ? 'Проверяем…' : 'Проверить смысл'}
            </button>
            {selectedClientId || (pickMode === 'multi' && selectedClientIds.length > 0) ? (
              <button
                className="ms-btn"
                disabled={
                  checkingSanity ||
                  generating ||
                  rewriting ||
                  suggestingBouquet ||
                  paraphrasing ||
                  !offer.trim()
                }
                onClick={() => void markSentToConversation()}
                type="button"
              >
                {channel.startsWith('telegram')
                  ? pickMode === 'multi' && selectedClientIds.length > 1
                    ? `Отправить выбранным (${selectedClientIds.length})`
                    : 'Отправить в Telegram'
                  : 'Отправить → в историю'}
              </button>
            ) : null}
            <button
              className="ms-btn ms-btn-primary"
              disabled={
                saving ||
                loading ||
                generating ||
                rewriting ||
                checkingSanity ||
                suggestingBouquet ||
                paraphrasing ||
                audience < 1
              }
              type="submit"
            >
              {selectedClientId
                ? 'Создать 1:1 черновик'
                : mode === 'auto'
                  ? `Черновик на ${audience}`
                  : `Массовый черновик (${audience})`}
            </button>
          </div>
          {genSource ? (
            <p className="ms-muted">
              Источник текста: {genSource === 'redis-cache' || genSource === 'redis+file' || genSource === 'file'
                ? `кэш (${genSource})`
                : genSource}
            </p>
          ) : null}
        </form>
        <FactsPanel facts={facts} notes={groundingNotes} sanity={sanity} />
      </div>
      {error ? <div className="ms-error">{error}</div> : null}
      <h2 className="ms-section-title">Черновики</h2>
      {!campaigns.length ? (
        <p className="ms-muted">{loading ? 'Загрузка…' : 'Пока нет рассылок.'}</p>
      ) : (
        <ul className="ms-campaign-list">
          {campaigns.map(c => (
            <li className="ms-campaign-card" key={c.id}>
              <div className="ms-campaign-card-head">
                <strong>{c.title}</strong>
                <button
                  className="ms-btn"
                  onClick={() =>
                    void call(`/campaigns/${encodeURIComponent(c.id)}`, { method: 'DELETE' }).then(refresh)
                  }
                  type="button"
                >
                  Удалить
                </button>
              </div>
              <div className="ms-muted">
                {c.channel} · {c.mode} · аудитория {c.audience_count || 0}
                {c.client_name ? ` · ${c.client_name}` : ''} · {c.status || 'draft'}
                {c.ai_source ? ` · AI ${c.ai_source}` : ''}
                {c.personalize_pending ? ' · персонализация в очереди' : ''}
              </div>
              {c.offer ? <p className="ms-campaign-offer">{c.offer}</p> : null}
              {c.recommendation ? (
                <p className="ms-muted">Контекст: {c.recommendation}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type PlaygroundOutputKey =
  | 'history_profile'
  | 'occasion_intent'
  | 'recommendation'
  | 'fact_blocks'
  | 'system_prompt'
  | 'heuristic'
  | 'llm'
  | 'full'

const PLAYGROUND_DEBUG_TABS: Array<{ id: PlaygroundOutputKey; label: string }> = [
  { id: 'fact_blocks', label: 'Факты' },
  { id: 'heuristic', label: 'Heuristic' },
  { id: 'llm', label: 'LLM raw' },
  { id: 'system_prompt', label: 'System prompt' },
  { id: 'full', label: 'Все этапы' }
]

const PLAYGROUND_QUALITY_FIELDS: Array<{
  id: 'history_profile' | 'occasion_intent' | 'recommendation'
  label: string
  hint: string
}> = [
  {
    id: 'history_profile',
    label: 'Саммари',
    hint: 'История и профиль — есть ли конкретика по заказам?'
  },
  {
    id: 'occasion_intent',
    label: 'Повод / intent',
    hint: 'Повод, сезонность, окно касания — не выдумка?'
  },
  {
    id: 'recommendation',
    label: 'Рекомендация',
    hint: 'Что предложить продавцу — действие + якорь на фактах'
  }
]

interface GoldenClientSummary {
  id?: string
  name?: string
  order_count?: number
  avg_check?: number
  channels?: string[]
}

interface PlaygroundPanels {
  input_text?: string
  outputs?: Partial<Record<PlaygroundOutputKey, string>>
}

interface PlaygroundTrace {
  ok?: boolean
  client_id?: string
  client_name?: string
  source?: string
  data_thin?: boolean
  stages?: {
    active?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    }
    heuristic?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    }
    llm?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    } | null
  }
  panels?: PlaygroundPanels
}

function playgroundStats(text: string): { chars: number; words: number; lines: number } {
  const trimmed = text.trim()
  if (!trimmed) {
    return { chars: 0, words: 0, lines: 0 }
  }

  return {
    chars: trimmed.length,
    words: trimmed.split(/\s+/).filter(Boolean).length,
    lines: trimmed.split(/\n/).length
  }
}

function AiPlaygroundPage() {
  const call = useMsRest()
  const [clients, setClients] = useState<GoldenClientSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [inputText, setInputText] = useState('')
  const [outputs, setOutputs] = useState<Partial<Record<PlaygroundOutputKey, string>>>({})
  const [historyText, setHistoryText] = useState('')
  const [occasionText, setOccasionText] = useState('')
  const [recoText, setRecoText] = useState('')
  const [aiSource, setAiSource] = useState('')
  const [debugKey, setDebugKey] = useState<PlaygroundOutputKey>('fact_blocks')
  const [meta, setMeta] = useState('')
  const [error, setError] = useState('')
  const [loadingList, setLoadingList] = useState(false)
  const [running, setRunning] = useState(false)
  const [inputOpen, setInputOpen] = useState(true)
  const [compareOpen, setCompareOpen] = useState(false)

  const qualityMap = {
    history_profile: { value: historyText, set: setHistoryText },
    occasion_intent: { value: occasionText, set: setOccasionText },
    recommendation: { value: recoText, set: setRecoText }
  } as const

  const applyTrace = useCallback((trace: PlaygroundTrace) => {
    const panels = trace.panels || {}
    if (typeof panels.input_text === 'string') {
      setInputText(panels.input_text)
    }
    const nextOutputs = panels.outputs || {}
    setOutputs(nextOutputs)
    setHistoryText(String(nextOutputs.history_profile || trace.stages?.active?.history_profile || ''))
    setOccasionText(String(nextOutputs.occasion_intent || trace.stages?.active?.occasion_intent || ''))
    setRecoText(String(nextOutputs.recommendation || trace.stages?.active?.recommendation || ''))
    const src = trace.stages?.active?.source || '—'
    setAiSource(src)
    setMeta(
      [
        trace.client_name || 'клиент',
        `id=${trace.client_id || '—'}`,
        `source=${trace.source || '—'}`,
        `ai=${src}`,
        trace.data_thin ? 'данных мало' : null
      ]
        .filter(Boolean)
        .join(' · ')
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoadingList(true)
    setError('')
    void call<{ clients?: GoldenClientSummary[]; count?: number; generated_at?: string }>(
      '/eval/golden-clients'
    )
      .then(data => {
        if (cancelled) {return}
        const rows = data.clients || []
        setClients(rows)
        if (rows[0]?.id) {
          setSelectedId(String(rows[0].id))
        }
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setLoadingList(false)}
      })

    return () => {
      cancelled = true
    }
  }, [call])

  useEffect(() => {
    if (!selectedId) {return}
    let cancelled = false
    setRunning(true)
    setError('')
    void call<PlaygroundTrace & { client?: unknown }>(
      `/eval/golden-clients/${encodeURIComponent(selectedId)}`
    )
      .then(data => {
        if (!cancelled) {applyTrace(data)}
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setRunning(false)}
      })

    return () => {
      cancelled = true
    }
  }, [applyTrace, call, selectedId])

  const runPlayground = async (runLlm: boolean) => {
    setRunning(true)
    setError('')

    try {
      const trace = await call<PlaygroundTrace>('/eval/playground/run', {
        method: 'POST',
        body: {
          client_id: selectedId,
          input_json: inputText,
          run_llm: runLlm
        },
        timeoutMs: runLlm ? OUTREACH_AI_TIMEOUT_MS : 30_000
      })
      applyTrace(trace)
      if (runLlm) {
        setCompareOpen(true)
        setDebugKey('llm')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  const debugText = outputs[debugKey] || ''
  const heuristicPretty = outputs.heuristic || ''
  const llmPretty = outputs.llm || ''

  return (
    <div className="ms-page ms-playground" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>AI тест · клиенты</h1>
          <p className="ms-muted">
            Контроль качества: правьте входной JSON → Смотрите Саммари / Повод / Рекомендацию
          </p>
          {meta ? <p className="ms-muted ms-sync-meta">{meta}</p> : null}
        </div>
        <div className="ms-actions">
          <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
            ← Клиенты
          </button>
          <button
            className="ms-btn"
            disabled={running || !inputText.trim()}
            onClick={() => void runPlayground(false)}
            title="Heuristic по текущему JSON (без LLM)"
            type="button"
          >
            {running ? 'Считаю…' : 'Пересчитать'}
          </button>
          <button
            className="ms-btn ms-btn-primary"
            disabled={running || !inputText.trim()}
            onClick={() => void runPlayground(true)}
            title="Вызов auxiliary LLM — сравните с heuristic"
            type="button"
          >
            Запустить LLM
          </button>
        </div>
      </div>

      <div className="ms-playground-toolbar">
        <label className="ms-playground-pick">
          Клиент из golden dataset
          <select
            disabled={loadingList || running}
            onChange={e => setSelectedId(e.target.value)}
            value={selectedId}
          >
            {clients.length === 0 ? <option value="">Нет клиентов</option> : null}
            {clients.map(c => (
              <option key={c.id || c.name} value={c.id || ''}>
                {(c.name || '—') +
                  ` · заказов ${c.order_count ?? 0}` +
                  (c.avg_check ? ` · ≈ ${Math.round(c.avg_check)} ₽` : '')}
              </option>
            ))}
          </select>
        </label>
        <div className="ms-playground-badges">
          <span className={`ms-playground-badge${aiSource === 'llm' ? ' is-llm' : ' is-heur'}`}>
            {aiSource === 'llm' ? 'LLM' : 'Heuristic'}
          </span>
          {running ? <span className="ms-playground-badge is-run">идёт генерация…</span> : null}
        </div>
      </div>

      {error ? <div className="ms-error">{error}</div> : null}

      <section className="ms-playground-quality" aria-label="Качество генерации">
        <div className="ms-playground-quality-head">
          <h2 className="ms-section-title">Выход AI — монитор качества</h2>
          <p className="ms-muted">
            Три поля ниже — результат. Читайте как продавец: конкретика, без выдумок, ясный next step.
            Текст можно править вручную для заметок (на генерацию влияет только входной JSON).
          </p>
        </div>
        <div className="ms-playground-quality-grid">
          {PLAYGROUND_QUALITY_FIELDS.map(field => {
            const box = qualityMap[field.id]
            const stats = playgroundStats(box.value)
            const empty = stats.chars === 0

            return (
              <label className={`ms-playground-qcard${empty ? ' is-empty' : ''}`} key={field.id}>
                <span className="ms-playground-qcard-top">
                  <span className="ms-ai-label">{field.label}</span>
                  <span className="ms-playground-qmeta">
                    {stats.words} сл. · {stats.chars} зн.
                    {empty ? ' · пусто' : ''}
                  </span>
                </span>
                <span className="ms-muted ms-playground-qhint">{field.hint}</span>
                <textarea
                  className="ms-playground-prose"
                  onChange={e => box.set(e.target.value)}
                  placeholder={running ? 'Генерация…' : 'Нет текста — пересчитайте или запустите LLM'}
                  spellCheck
                  value={box.value}
                />
              </label>
            )
          })}
        </div>
      </section>

      <section className="ms-playground-control" aria-label="Управление входом">
        <button
          className="ms-playground-fold"
          onClick={() => setInputOpen(v => !v)}
          type="button"
        >
          {inputOpen ? '▾' : '▸'} Входные факты (JSON) — правьте → Пересчитать / LLM
        </button>
        {inputOpen ? (
          <label className="ms-playground-pane ms-playground-input">
            <span className="ms-muted">
              Единственный рычаг генерации. Меняйте client/orders/risks и жмите кнопки сверху.
            </span>
            <textarea
              onChange={e => setInputText(e.target.value)}
              placeholder="JSON клиента + заказов / risks…"
              spellCheck={false}
              value={inputText}
            />
          </label>
        ) : null}
      </section>

      <section className="ms-playground-debug" aria-label="Сравнение и отладка">
        <button
          className="ms-playground-fold"
          onClick={() => setCompareOpen(v => !v)}
          type="button"
        >
          {compareOpen ? '▾' : '▸'} Сравнение Heuristic ↔ LLM · отладка
        </button>
        {compareOpen ? (
          <div className="ms-playground-debug-body">
            <div className="ms-playground-compare">
              <label className="ms-playground-pane">
                <span className="ms-ai-label">Heuristic JSON</span>
                <textarea
                  className="ms-playground-mono"
                  readOnly
                  spellCheck={false}
                  value={heuristicPretty || '— сначала Пересчитать —'}
                />
              </label>
              <label className="ms-playground-pane">
                <span className="ms-ai-label">LLM JSON</span>
                <textarea
                  className="ms-playground-mono"
                  readOnly
                  spellCheck={false}
                  value={llmPretty || '— Запустите LLM —'}
                />
              </label>
            </div>
            <div className="ms-filter-tabs" role="tablist">
              {PLAYGROUND_DEBUG_TABS.map(tab => (
                <button
                  className={`ms-filter-tab${debugKey === tab.id ? ' is-active' : ''}`}
                  key={tab.id}
                  onClick={() => setDebugKey(tab.id)}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <label className="ms-playground-pane">
              <span className="ms-ai-label">
                Debug · {PLAYGROUND_DEBUG_TABS.find(t => t.id === debugKey)?.label || debugKey}
              </span>
              <textarea
                className="ms-playground-mono"
                readOnly
                spellCheck={false}
                value={debugText || (running ? 'Загрузка…' : '—')}
              />
            </label>
          </div>
        ) : null}
      </section>
    </div>
  )
}

const plugin: HermesPlugin = {
  id: 'moysklad',
  name: 'МойСклад CRM',
  // Iris default-on. Settings ▸ Plugins can disable. Live data needs
  // MOYSKLAD_API_TOKEN in .env.
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    restStream = ctx.restStream
    ctx.onDispose(() => {
      rest = null
      restStream = null
    })

    ctx.registerMany([
      {
        id: 'clients-page',
        area: ROUTES_AREA,
        data: { path: '/clients' } satisfies RouteContribution,
        render: () => <ClientsPage />
      },
      {
        id: 'clients-playground-page',
        area: ROUTES_AREA,
        data: { path: '/clients/playground' } satisfies RouteContribution,
        render: () => <AiPlaygroundPage />
      },
      {
        id: 'campaigns-page',
        area: ROUTES_AREA,
        data: { path: '/campaigns' } satisfies RouteContribution,
        render: () => <CampaignsPage />
      },
      {
        id: 'clients-nav',
        area: SIDEBAR_NAV_AREA,
        order: 40,
        data: {
          codicon: 'organization',
          label: 'Клиенты',
          path: '/clients'
        } satisfies SidebarNavContribution
      },
      {
        id: 'clients-playground-nav',
        area: SIDEBAR_NAV_AREA,
        order: 41,
        data: {
          codicon: 'beaker',
          label: 'AI тест',
          path: '/clients/playground'
        } satisfies SidebarNavContribution
      },
      {
        id: 'campaigns-nav',
        area: SIDEBAR_NAV_AREA,
        order: 42,
        data: {
          codicon: 'mail',
          label: 'Рассылки',
          path: '/campaigns'
        } satisfies SidebarNavContribution
      },
      {
        id: 'plugins-nav',
        area: SIDEBAR_NAV_AREA,
        order: 90,
        data: {
          codicon: 'extensions',
          label: 'Plugins',
          path: '/settings?tab=plugins'
        } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
