import './moysklad.css'

import { useCallback, useEffect, useRef, useState, type FormEvent, type UIEvent } from 'react'

import {
  type HermesPlugin,
  host,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution
} from '@hermes/plugin-sdk'

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
  conversation?: ClientConversation
  data_thin?: boolean
}

type Rest = <T>(path: string, opts?: { method?: string; body?: unknown }) => Promise<T>

let rest: null | Rest = null

function useMsRest(): Rest {
  return useCallback(async <T,>(path: string, opts?: { method?: string; body?: unknown }) => {
    if (!rest) {
      throw new Error('MoySklad plugin REST not bound')
    }
    return rest<T>(path, opts)
  }, [])
}

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
  order_count?: number
  avg_check?: number
  last_order_at?: string
  bonus_points?: string | number
  role?: string
  actual_address?: string
  actual_address_comment?: string
  company_type?: string
  sex?: string
  tg_nick?: string
  tg_conversation?: string
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
    render: r => r.channel || (r.channels || []).join(', ')
  },
  { key: 'avg_display', label: 'Средний чек', render: r => money(r.avg_check) },
  {
    key: 'last_order_display',
    label: 'Дата последнего заказа',
    render: r => (r.last_order_at || '').slice(0, 16).replace('T', ' ')
  },
  { key: 'orders_display', label: 'Всего заказов', render: r => String(r.order_count ?? 0) },
  { key: 'bonus_points', label: 'Баллы начисленные', render: r => String(r.bonus_points ?? '') },
  {
    key: 'groups_display',
    label: 'Группы',
    render: r => r.groups || (r.tags || []).join(', ')
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
      if (!preview) return ''
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

interface DraftPrefill {
  clientId: string
  channel?: string
  salesFilter?: string
}

function readDraftPrefill(): DraftPrefill | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_PREFILL_KEY)
    if (!raw) return null
    sessionStorage.removeItem(DRAFT_PREFILL_KEY)
    const parsed = JSON.parse(raw) as DraftPrefill
    if (!parsed?.clientId) return null
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
  if (p.includes('whatsapp')) return 'whatsapp'
  return 'telegram'
}

function money(n: number | undefined) {
  const v = Number(n) || 0
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
  if (!items?.length) return null
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
  if (!block) return null
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
      <FactBlockView block={historyBlock} />
      <FactBlockView block={occasionBlock} />
      <FactBlockView block={risksBlock} />
      <ConversationThread
        conversation={facts.conversation}
        compact
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
      {facts.recommendation ? (
        <>
          <p className="ms-ai-label">Рекомендация (контекст)</p>
          <p className="ms-facts-rec">{facts.recommendation}</p>
        </>
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
  const [error, setError] = useState('')
  const [ordersOpen, setOrdersOpen] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!clientId) return
    let cancelled = false
    setLoading(true)
    setError('')
    setDetail(null)
    setOrdersOpen(false)
    setNote('')
    void call<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`)
      .then(payload => {
        if (!cancelled) setDetail(payload)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [call, clientId])

  if (!clientId) return null

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
        { method: 'POST' }
      )
      setDetail(prev => (prev ? { ...prev, ai: payload.ai || prev.ai } : prev))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAiLoading(false)
    }
  }

  const sendAndRecord = async (channel: 'whatsapp' | 'telegram') => {
    const text = note.trim()
    if (!text) {
      setError('Введите текст в поле ниже — он уйдёт в историю TG conversation.')
      return
    }
    setError('')
    try {
      const data = await call<{
        conversation?: ClientConversation
        deep_link?: string
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
      if (data.deep_link) window.open(data.deep_link, '_blank', 'noopener')
      setNote('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) onClose()
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
                <button className="ms-btn" disabled={aiLoading} onClick={() => void refreshAi()} type="button">
                  {aiLoading ? 'Генерация…' : 'Обновить AI'}
                </button>
              </div>
              {ai.data_thin ? <p className="ms-muted">Данных мало — выводы осторожные.</p> : null}
              <p className="ms-ai-label">История и профиль</p>
              <p>{ai.history_profile || '—'}</p>
              <p className="ms-ai-label">Повод и intent покупки</p>
              <p>{ai.occasion_intent || '—'}</p>
              <h4>Рекомендация AI</h4>
              <p>{ai.recommendation || '—'}</p>
              <p className="ms-muted">Источник: {ai.source || 'heuristic'}</p>
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

const CLIENTS_PAGE_SIZE = 50

function mergeClientPages(prev: ClientRow[], incoming: ClientRow[]): ClientRow[] {
  const seen = new Set<string>()
  const out: ClientRow[] = []
  for (const row of [...prev, ...incoming]) {
    const id = String(row.id || '').trim()
    if (id) {
      if (seen.has(id)) continue
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
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [clients, setClients] = useState<ClientRow[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [matched, setMatched] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [nextOffset, setNextOffset] = useState(0)
  const [groupOptions, setGroupOptions] = useState<Array<{ name: string; count: number }>>([])
  const [syncedLabel, setSyncedLabel] = useState('')
  const [fromCache, setFromCache] = useState(false)
  const [cardClientId, setCardClientId] = useState<string | null>(null)
  const [recalcOpen, setRecalcOpen] = useState(false)
  const [recalcLoading, setRecalcLoading] = useState(false)
  const [recalcGroups, setRecalcGroups] = useState('')
  const [recalcSource, setRecalcSource] = useState('')
  const [recalcPreview, setRecalcPreview] = useState<{ changed?: number; total?: number } | null>(
    null
  )
  const [recalcError, setRecalcError] = useState('')
  const loadGen = useRef(0)
  const loadingMoreRef = useRef(false)

  const load = useCallback(
    async (opts?: { refresh?: boolean; append?: boolean; offset?: number }) => {
      const append = Boolean(opts?.append)
      const offset = append ? (opts?.offset ?? nextOffset) : 0
      const gen = append ? loadGen.current : ++loadGen.current
      if (append) {
        if (loadingMoreRef.current || !hasMore) return
        loadingMoreRef.current = true
        setLoadingMore(true)
      } else {
        setLoading(true)
        setError('')
      }
      try {
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          q,
          group,
          limit: String(CLIENTS_PAGE_SIZE),
          offset: String(offset)
        })
        if (opts?.refresh) params.set('refresh', 'true')
        const data = await call<{
          clients?: ClientRow[]
          counts?: Counts
          matched_total?: number
          has_more?: boolean
          next_offset?: number
          returned?: number
          group_options?: Array<{ name: string; count: number }>
          cached?: boolean
          synced_at_label?: string
          synced_at?: number
        }>(`/clients?${params}`)
        if (gen !== loadGen.current) return
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
          setGroupOptions(data.group_options || [])
          setFromCache(Boolean(data.cached))
          setSyncedLabel(data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''))
        }
      } catch (err) {
        if (gen !== loadGen.current) return
        setError(err instanceof Error ? err.message : String(err))
        if (!append) setClients([])
      } finally {
        if (append) {
          loadingMoreRef.current = false
          setLoadingMore(false)
        } else if (gen === loadGen.current) {
          setLoading(false)
        }
      }
    },
    [call, group, hasMore, nextOffset, q, salesFilter]
  )

  useEffect(() => {
    void load()
  }, [salesFilter, group, q]) // eslint-disable-line react-hooks/exhaustive-deps -- reset list on filter change

  const onTableScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 160) return
      if (!hasMore || loading || loadingMoreRef.current) return
      void load({ append: true, offset: nextOffset })
    },
    [hasMore, load, loading, nextOffset]
  )

  const cacheHint = syncedLabel
    ? `${fromCache ? 'из кэша' : 'свежая выгрузка'} · синхр. ${syncedLabel}`
    : fromCache
      ? 'из кэша'
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
      {groupOptions.length > 0 ? (
        <div className="ms-chips">
          {groupOptions.slice(0, 24).map(opt => (
            <button
              className={`ms-chip${group === opt.name ? ' is-active' : ''}`}
              key={opt.name}
              onClick={() => setGroup(group === opt.name ? '' : opt.name)}
              type="button"
            >
              {opt.name} <span>{opt.count}</span>
            </button>
          ))}
        </div>
      ) : null}
      {error ? <div className="ms-error">{error}</div> : null}
      <p className="ms-muted">
        Найдено: {matched}
        {clients.length ? ` · показано ${clients.length}` : ''}
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
      {loading && !clients.length ? (
        <p className="ms-muted">Загрузка клиентов…</p>
      ) : (
        <div className="ms-table-wrap" onScroll={onTableScroll}>
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
                    return <td key={col.key}>{value || '—'}</td>
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
  const [salesFilter, setSalesFilter] = useState('direct')
  const [mode, setMode] = useState<'manual' | 'auto'>('manual')
  const [title, setTitle] = useState('Рассылка по фильтрам')
  const [channel, setChannel] = useState('telegram')
  const [channelKind, setChannelKind] = useState('')
  const [group, setGroup] = useState('')
  const [requirePhone, setRequirePhone] = useState(false)
  const [requireTelegram, setRequireTelegram] = useState(false)
  const [vipOnly, setVipOnly] = useState(false)
  const [birthdaySoon, setBirthdaySoon] = useState(false)
  const [personalize, setPersonalize] = useState(false)
  const [offer, setOffer] = useState('')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [audience, setAudience] = useState(0)
  const [audiencePreview, setAudiencePreview] = useState<ClientRow[]>([])
  const [audienceQ, setAudienceQ] = useState('')
  const [audienceQDebounced, setAudienceQDebounced] = useState('')
  const [audienceHasMore, setAudienceHasMore] = useState(false)
  const [audienceNextOffset, setAudienceNextOffset] = useState(0)
  const [audienceLoadingMore, setAudienceLoadingMore] = useState(false)
  const [groupOptions, setGroupOptions] = useState<Array<{ name: string; count: number }>>([])
  const [selectedClientId, setSelectedClientId] = useState<string | null>(null)
  const [facts, setFacts] = useState<ClientFacts | null>(null)
  const [groundingNotes, setGroundingNotes] = useState('')
  const [genSource, setGenSource] = useState('')
  const [sellerName, setSellerName] = useState('')
  const [sellerFacts, setSellerFacts] = useState('')
  const [sellerLoaded, setSellerLoaded] = useState(false)
  const [contactsOpen, setContactsOpen] = useState(true)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [rewriting, setRewriting] = useState(false)
  const [checkingSanity, setCheckingSanity] = useState(false)
  const [sanity, setSanity] = useState<SanityResult | null>(null)
  const [error, setError] = useState('')
  const [prefillReady, setPrefillReady] = useState(false)
  const audienceLoadMoreRef = useRef(false)
  const sellerSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const prefill = readDraftPrefill()
    if (prefill) {
      setSelectedClientId(prefill.clientId)
      if (prefill.channel) setChannel(prefill.channel)
      if (prefill.salesFilter) setSalesFilter(prefill.salesFilter)
      setMode('auto')
      setTitle('Черновик · клиент')
    }
    setPrefillReady(true)
  }, [])

  useEffect(() => {
    void call<{ seller_name?: string; seller_facts?: string }>('/campaigns/seller-settings')
      .then(data => {
        setSellerName(data.seller_name || '')
        setSellerFacts(data.seller_facts || '')
      })
      .catch(() => undefined)
      .finally(() => setSellerLoaded(true))
  }, [call])

  const persistSellerSettings = useCallback(
    (name: string, factsText: string) => {
      if (sellerSaveTimer.current) clearTimeout(sellerSaveTimer.current)
      sellerSaveTimer.current = setTimeout(() => {
        void call('/campaigns/seller-settings', {
          method: 'PUT',
          body: { seller_name: name, seller_facts: factsText }
        }).catch(() => undefined)
      }, 450)
    },
    [call]
  )

  useEffect(() => {
    const t = setTimeout(() => setAudienceQDebounced(audienceQ.trim()), 280)
    return () => clearTimeout(t)
  }, [audienceQ])

  const audienceFilterParams = useCallback(
    (opts?: { limit?: number; offset?: number; q?: string }) => {
      const params = new URLSearchParams({
        sales_filter: salesFilter,
        group,
        q: opts?.q ?? audienceQDebounced,
        limit: String(opts?.limit ?? 40),
        offset: String(opts?.offset ?? 0)
      })
      if (channelKind) params.set('channel_kind', channelKind)
      if (requirePhone) params.set('require_phone', 'true')
      if (requireTelegram) params.set('require_telegram', 'true')
      if (vipOnly) params.set('vip_only', 'true')
      if (birthdaySoon) params.set('birthday_soon', 'true')
      return params
    },
    [
      audienceQDebounced,
      birthdaySoon,
      channelKind,
      group,
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
        if (audienceLoadMoreRef.current || !audienceHasMore) return
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
          group_options?: Array<{ name: string; count: number }>
          has_more?: boolean
          next_offset?: number
        }>(`/clients?${audienceFilterParams({ offset, limit: 40 })}`)
        const rows = page.clients || []
        setAudiencePreview(prev => (append ? mergeClientPages(prev, rows) : rows))
        setAudience(page.matched_total || 0)
        setCounts(page.counts || null)
        if (!append) setGroupOptions(page.group_options || [])
        const next = page.next_offset != null ? page.next_offset : offset + rows.length
        setAudienceNextOffset(next)
        setAudienceHasMore(
          page.has_more != null ? Boolean(page.has_more) : next < (page.matched_total || 0)
        )
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        if (!append) setAudiencePreview([])
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
    channelKind,
    requirePhone,
    requireTelegram,
    vipOnly,
    birthdaySoon,
    audienceQDebounced
  ]) // eslint-disable-line react-hooks/exhaustive-deps -- reload audience on filter/search

  useEffect(() => {
    void call<{ campaigns?: Campaign[] }>('/campaigns')
      .then(list => setCampaigns(list.campaigns || []))
      .catch(() => undefined)
  }, [call])

  const loadOutreach = useCallback(
    async (clientId: string, nextChannel = channel, runAi = mode === 'auto') => {
      setGenerating(true)
      setError('')
      try {
        if (runAi) {
          const data = await call<{
            message?: string
            grounding_notes?: string
            source?: string
            facts?: ClientFacts
            client_name?: string
            sanity?: SanityResult
          }>('/campaigns/generate', {
            method: 'POST',
            body: {
              client_id: clientId,
              channel: nextChannel,
              refresh_ai: true,
              seller_name: sellerName,
              seller_facts: sellerFacts
            }
          })
          setFacts(data.facts || null)
          setGroundingNotes(data.grounding_notes || '')
          setGenSource(data.source || '')
          setSanity(data.sanity || null)
          if (data.message) setOffer(data.message)
          if (data.client_name) setTitle(`Черновик · ${data.client_name}`)
          if (data.facts?.ai_source || data.source) {
            setFacts(prev =>
              prev ? { ...prev, ai_source: data.source || prev.ai_source } : prev
            )
          }
        } else {
          const detail = await call<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`)
          const panel: ClientFacts = {
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
            data_thin: detail.data_thin,
            recommendation: detail.ai?.recommendation,
            history_profile: detail.ai?.history_profile,
            occasion_intent: detail.ai?.occasion_intent,
            ai_source: detail.ai?.source,
            conversation: detail.conversation
          }
          setFacts(panel)
          setGroundingNotes('')
          setGenSource('')
          setSanity(null)
          if (detail.client?.name) setTitle(`Черновик · ${detail.client.name}`)
          const preferred = channelFromMessaging(detail.messaging?.primary_channel)
          if (!selectedClientId) setChannel(preferred)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setGenerating(false)
      }
    },
    [call, channel, mode, selectedClientId, sellerFacts, sellerName]
  )

  useEffect(() => {
    if (!prefillReady || !selectedClientId) return
    void loadOutreach(selectedClientId, channel, true)
    // only on client pick / prefill — not on every channel keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillReady, selectedClientId])

  const selectAudienceClient = (row: ClientRow) => {
    if (!row.id) return
    setSelectedClientId(row.id)
    setMode('auto')
    if (row.phone && !row.tg_nick) setChannel('whatsapp')
    else setChannel('telegram')
  }

  const regenerateAi = async () => {
    if (!selectedClientId) {
      setError('Сначала выберите клиента из аудитории или карточки.')
      return
    }
    await loadOutreach(selectedClientId, channel, true)
  }

  const humanizeDraft = async () => {
    if (!offer.trim()) {
      setError('Сначала введите или сгенерируйте текст сообщения.')
      return
    }
    setRewriting(true)
    setError('')
    try {
      const data = await call<{
        message?: string
        grounding_notes?: string
        source?: string
        facts?: ClientFacts
        sanity?: SanityResult
      }>('/campaigns/rewrite', {
        method: 'POST',
        body: {
          message: offer,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts
        }
      })
      if (data.message) setOffer(data.message)
      if (data.grounding_notes) setGroundingNotes(data.grounding_notes)
      if (data.source) setGenSource(data.source)
      if (data.facts) setFacts(data.facts)
      if (data.sanity) setSanity(data.sanity)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRewriting(false)
    }
  }

  const runSanityCheck = async () => {
    if (!offer.trim()) {
      setError('Сначала введите или сгенерируйте текст сообщения.')
      return
    }
    setCheckingSanity(true)
    setError('')
    try {
      const data = await call<{
        message?: string
        sanity?: SanityResult
        facts?: ClientFacts
      }>('/campaigns/sanity', {
        method: 'POST',
        body: {
          message: offer,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts,
          apply_revision: true
        }
      })
      if (data.message) setOffer(data.message)
      if (data.sanity) setSanity(data.sanity)
      if (data.facts && Object.keys(data.facts).length) setFacts(data.facts)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCheckingSanity(false)
    }
  }

  const markSentToConversation = async () => {
    if (!selectedClientId) {
      setError('Выберите клиента — исходящее пишется в его TG conversation.')
      return
    }
    if (!offer.trim()) {
      setError('Сначала введите или сгенерируйте текст сообщения.')
      return
    }
    setCheckingSanity(true)
    setError('')
    try {
      const data = await call<{
        conversation?: ClientConversation
        facts?: ClientFacts
        deep_link?: string
      }>('/campaigns/mark-sent', {
        method: 'POST',
        body: {
          message: offer,
          channel,
          client_id: selectedClientId,
          open_deep_link: true
        }
      })
      if (data.facts) setFacts(data.facts)
      else if (data.conversation) {
        setFacts(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
      }
      if (data.deep_link) window.open(data.deep_link, '_blank', 'noopener')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setCheckingSanity(false)
    }
  }

  const createDraft = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
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
          personalize,
          client_id: selectedClientId || '',
          generate_ai: mode === 'auto' && !offer.trim(),
          seller_name: sellerName,
          seller_facts: sellerFacts
        }
      })
      if (!selectedClientId) setOffer('')
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
              · выбран <strong>{facts?.name || selectedClientId}</strong>
              <button
                className="ms-link-btn"
                onClick={() => {
                  setSelectedClientId(null)
                  setFacts(null)
                  setGroundingNotes('')
                  setTitle('Рассылка по фильтрам')
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
        {groupOptions.length > 0 ? (
          <div className="ms-filter-block">
            <span className="ms-filter-label">Тег / повод (группы МойСклад)</span>
            <div className="ms-chips">
              {groupOptions.slice(0, 20).map(opt => (
                <button
                  className={`ms-chip${group === opt.name ? ' is-active' : ''}`}
                  key={opt.name}
                  onClick={() => setGroup(group === opt.name ? '' : opt.name)}
                  type="button"
                >
                  {opt.name} <span>{opt.count}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className="ms-audience-pick">
          <div className="ms-audience-pick-head">
            <p className="ms-muted">
              Клиенты аудитории (поиск / подгрузка — доступны все {audience}):
            </p>
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
          {contactsOpen ? (
            <>
              <div className="ms-search">
                <input
                  onChange={e => setAudienceQ(e.target.value)}
                  placeholder="Найти клиента в аудитории по имени / телефону…"
                  type="search"
                  value={audienceQ}
                />
              </div>
              {audiencePreview.length ? (
                <div
                  className="ms-audience-list"
                  onScroll={event => {
                    const el = event.currentTarget
                    if (el.scrollHeight - el.scrollTop - el.clientHeight > 120) return
                    if (!audienceHasMore || audienceLoadMoreRef.current) return
                    void loadAudience({ append: true })
                  }}
                >
                  <div className="ms-chips">
                    {audiencePreview.map(row => (
                      <button
                        className={`ms-chip${selectedClientId === row.id ? ' is-active' : ''}`}
                        key={row.id || row.name}
                        onClick={() => selectAudienceClient(row)}
                        type="button"
                      >
                        {row.name || row.phone || row.id}
                        {row.order_count != null ? <span>{row.order_count}</span> : null}
                      </button>
                    ))}
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
              onChange={e => setOffer(e.target.value)}
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
          <label className="ms-check">
            <input
              checked={personalize}
              disabled={Boolean(selectedClientId)}
              onChange={e => setPersonalize(e.target.checked)}
              type="checkbox"
            />
            Персонализировать по клиентам (очередь — позже)
          </label>
          <div className="ms-compose-actions">
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={generating || rewriting || checkingSanity}
                onClick={() => void regenerateAi()}
                type="button"
              >
                {generating ? 'Генерация…' : 'Сгенерировать AI'}
              </button>
            ) : null}
            <button
              className="ms-btn"
              disabled={rewriting || generating || checkingSanity || !offer.trim()}
              onClick={() => void humanizeDraft()}
              type="button"
            >
              {rewriting ? 'Переписываем…' : 'Продающе и по-человечески'}
            </button>
            <button
              className="ms-btn"
              disabled={checkingSanity || generating || rewriting || !offer.trim()}
              onClick={() => void runSanityCheck()}
              type="button"
            >
              {checkingSanity ? 'Проверяем…' : 'Проверить смысл'}
            </button>
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={checkingSanity || generating || rewriting || !offer.trim()}
                onClick={() => void markSentToConversation()}
                type="button"
              >
                Отправить → в TG историю
              </button>
            ) : null}
            <button
              className="ms-btn ms-btn-primary"
              disabled={
                saving || loading || generating || rewriting || checkingSanity || audience < 1
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
          {genSource ? <p className="ms-muted">Источник текста: {genSource}</p> : null}
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

const plugin: HermesPlugin = {
  id: 'moysklad',
  name: 'МойСклад CRM',
  // Iris default-on. Settings ▸ Plugins can disable. Live data needs
  // MOYSKLAD_API_TOKEN in .env.
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    ctx.onDispose(() => {
      rest = null
    })

    ctx.registerMany([
      {
        id: 'clients-page',
        area: ROUTES_AREA,
        data: { path: '/clients' } satisfies RouteContribution,
        render: () => <ClientsPage />
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
        id: 'campaigns-nav',
        area: SIDEBAR_NAV_AREA,
        order: 41,
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
