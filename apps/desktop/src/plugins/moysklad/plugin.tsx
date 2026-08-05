import './moysklad.css'

import { useCallback, useEffect, useState, type FormEvent } from 'react'

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
  { key: 'tg_conversation', label: 'TG conversation', render: r => r.tg_conversation || '' }
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

function FactsPanel({ facts, notes }: { facts: ClientFacts | null; notes?: string }) {
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
                <span>{client.tg_nick || client.tg_conversation || '—'}</span>
                <span className="ms-muted">Тип</span>
                <span>{client.company_type || '—'}</span>
                <span className="ms-muted">Осн. канал</span>
                <span>{client.primary_channel || msg.primary_channel || '—'}</span>
              </div>
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
                  disabled={!msg.whatsapp_url}
                  onClick={() => msg.whatsapp_url && window.open(msg.whatsapp_url, '_blank', 'noopener')}
                  type="button"
                >
                  WhatsApp
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={!msg.telegram_url}
                  onClick={() => msg.telegram_url && window.open(msg.telegram_url, '_blank', 'noopener')}
                  type="button"
                >
                  Telegram
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
              {note ? <p className="ms-note">{note}</p> : null}
              <p className="ms-muted">{msg.hint || ''}</p>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}

function ClientsPage() {
  const call = useMsRest()
  const [salesFilter, setSalesFilter] = useState('direct')
  const [q, setQ] = useState('')
  const [group, setGroup] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [clients, setClients] = useState<ClientRow[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [matched, setMatched] = useState(0)
  const [groupOptions, setGroupOptions] = useState<Array<{ name: string; count: number }>>([])
  const [syncedLabel, setSyncedLabel] = useState('')
  const [fromCache, setFromCache] = useState(false)
  const [cardClientId, setCardClientId] = useState<string | null>(null)

  const load = useCallback(
    async (opts?: { refresh?: boolean }) => {
      setLoading(true)
      setError('')
      try {
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          q,
          group,
          limit: '50',
          offset: '0'
        })
        if (opts?.refresh) params.set('refresh', 'true')
        const data = await call<{
          clients?: ClientRow[]
          counts?: Counts
          matched_total?: number
          group_options?: Array<{ name: string; count: number }>
          cached?: boolean
          synced_at_label?: string
          synced_at?: number
        }>(`/clients?${params}`)
        setClients(data.clients || [])
        setCounts(data.counts || null)
        setMatched(data.matched_total || 0)
        setGroupOptions(data.group_options || [])
        setFromCache(Boolean(data.cached))
        setSyncedLabel(data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    },
    [call, group, q, salesFilter]
  )

  useEffect(() => {
    void load()
  }, [load])

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
      <p className="ms-muted">Найдено: {matched}</p>
      {loading && !clients.length ? (
        <p className="ms-muted">Загрузка клиентов…</p>
      ) : (
        <div className="ms-table-wrap">
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
  const [offer, setOffer] = useState('')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [audience, setAudience] = useState(0)
  const [audiencePreview, setAudiencePreview] = useState<ClientRow[]>([])
  const [selectedClientId, setSelectedClientId] = useState<string | null>(null)
  const [facts, setFacts] = useState<ClientFacts | null>(null)
  const [groundingNotes, setGroundingNotes] = useState('')
  const [genSource, setGenSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [prefillReady, setPrefillReady] = useState(false)

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

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [list, page] = await Promise.all([
        call<{ campaigns?: Campaign[] }>('/campaigns'),
        call<{ counts?: Counts; matched_total?: number; clients?: ClientRow[] }>(
          `/clients?sales_filter=${encodeURIComponent(salesFilter)}&limit=12`
        )
      ])
      setCampaigns(list.campaigns || [])
      setCounts(page.counts || null)
      setAudience(page.matched_total || 0)
      setAudiencePreview(page.clients || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [call, salesFilter])

  useEffect(() => {
    void refresh()
  }, [refresh])

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
          }>('/campaigns/generate', {
            method: 'POST',
            body: { client_id: clientId, channel: nextChannel, refresh_ai: true }
          })
          setFacts(data.facts || null)
          setGroundingNotes(data.grounding_notes || '')
          setGenSource(data.source || '')
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
            ai_source: detail.ai?.source
          }
          setFacts(panel)
          setGroundingNotes('')
          setGenSource('')
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
    [call, channel, mode, selectedClientId]
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
          client_id: selectedClientId || '',
          generate_ai: mode === 'auto' && !offer.trim()
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

  return (
    <div className="ms-page" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>Рассылки</h1>
          <p className="ms-muted">Черновики Telegram / WhatsApp · аудитория = кэш Клиентов</p>
        </div>
        <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
          ← Клиенты
        </button>
      </div>
      <FilterTabs counts={counts} disabled={loading} onChange={setSalesFilter} salesFilter={salesFilter} />
      <p className="ms-muted">
        Аудитория: <strong>{audience}</strong>
        {selectedClientId ? (
          <>
            {' '}
            · выбран{' '}
            <strong>{facts?.name || selectedClientId}</strong>
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
              сбросить
            </button>
          </>
        ) : null}
      </p>
      {audiencePreview.length ? (
        <div className="ms-audience-pick">
          <p className="ms-muted">Клиенты из того же фильтра (клик → персональный черновик):</p>
          <div className="ms-chips">
            {audiencePreview.map(row => (
              <button
                className={`ms-chip${selectedClientId === row.id ? ' is-active' : ''}`}
                key={row.id || row.name}
                onClick={() => selectAudienceClient(row)}
                type="button"
              >
                {row.name || row.id}
                {row.order_count != null ? <span>{row.order_count}</span> : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
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
            Канал
            <select onChange={e => setChannel(e.target.value)} value={channel}>
              <option value="telegram">Telegram (личные)</option>
              <option value="telegram_channel">Telegram-канал</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </label>
          <label>
            Текст сообщения
            <textarea
              onChange={e => setOffer(e.target.value)}
              placeholder={
                mode === 'auto'
                  ? 'Нажмите «Сгенерировать AI» или выберите клиента…'
                  : 'Текст рассылки…'
              }
              rows={8}
              value={offer}
            />
          </label>
          <div className="ms-compose-actions">
            {mode === 'auto' || selectedClientId ? (
              <button
                className="ms-btn"
                disabled={generating || !selectedClientId}
                onClick={() => void regenerateAi()}
                type="button"
              >
                {generating ? 'Генерация…' : 'Сгенерировать AI'}
              </button>
            ) : null}
            <button className="ms-btn ms-btn-primary" disabled={saving || loading || generating} type="submit">
              {mode === 'auto' ? 'Создать авто-черновик' : 'Создать черновик'}
            </button>
          </div>
          {genSource ? <p className="ms-muted">Источник текста: {genSource}</p> : null}
        </form>
        <FactsPanel facts={facts} notes={groundingNotes} />
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
