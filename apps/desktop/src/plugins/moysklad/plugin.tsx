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
  sales_type?: string
  tags?: string[]
  order_count?: number
  avg_check?: number
}

interface Campaign {
  id: string
  title: string
  channel: string
  mode: string
  offer?: string
  status?: string
  audience_count?: number
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

  const load = useCallback(async () => {
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
      const data = await call<{
        clients?: ClientRow[]
        counts?: Counts
        matched_total?: number
        group_options?: Array<{ name: string; count: number }>
      }>(`/clients?${params}`)
      setClients(data.clients || [])
      setCounts(data.counts || null)
      setMatched(data.matched_total || 0)
      setGroupOptions(data.group_options || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [call, group, q, salesFilter])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="ms-page">
      <div className="ms-page-header">
        <div>
          <h1>Клиенты</h1>
          <p className="ms-muted">МойСклад · Маркетплейс / Прямые (как kinetic-ai.ru/clients)</p>
        </div>
        <div className="ms-actions">
          <button className="ms-btn" disabled={loading} onClick={() => void load()} type="button">
            Обновить
          </button>
          <button className="ms-btn ms-btn-primary" onClick={() => host.navigate('/campaigns')} type="button">
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
        <p className="ms-muted">Загрузка клиентов из МойСклад…</p>
      ) : (
        <div className="ms-table-wrap">
          <table className="ms-table">
            <thead>
              <tr>
                <th>Клиент</th>
                <th>Контакт</th>
                <th>Тип</th>
                <th>Заказы</th>
                <th>Ср. чек</th>
              </tr>
            </thead>
            <tbody>
              {clients.map(row => (
                <tr key={row.id || row.name}>
                  <td>{row.name || '—'}</td>
                  <td>{row.phone || '—'}</td>
                  <td>{row.sales_type || '—'}</td>
                  <td>{row.order_count ?? 0}</td>
                  <td>{money(row.avg_check)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [list, page] = await Promise.all([
        call<{ campaigns?: Campaign[] }>('/campaigns'),
        call<{ counts?: Counts; matched_total?: number }>(
          `/clients?sales_filter=${encodeURIComponent(salesFilter)}&limit=1`
        )
      ])
      setCampaigns(list.campaigns || [])
      setCounts(page.counts || null)
      setAudience(page.matched_total || 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [call, salesFilter])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const createDraft = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await call('/campaigns', {
        method: 'POST',
        body: { title, channel, mode, offer, sales_filter: salesFilter }
      })
      setOffer('')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="ms-page">
      <div className="ms-page-header">
        <div>
          <h1>Рассылки</h1>
          <p className="ms-muted">Черновики Telegram / WhatsApp · аудитория из МойСклад</p>
        </div>
        <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
          ← Клиенты
        </button>
      </div>
      <FilterTabs counts={counts} disabled={loading} onChange={setSalesFilter} salesFilter={salesFilter} />
      <p className="ms-muted">
        Аудитория: <strong>{audience}</strong>
      </p>
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
        {mode === 'manual' ? (
          <label>
            Текст сообщения
            <textarea onChange={e => setOffer(e.target.value)} placeholder="Текст рассылки…" rows={4} value={offer} />
          </label>
        ) : (
          <p className="ms-muted">Текст подставится из AI-шаблона при создании черновика.</p>
        )}
        <button className="ms-btn ms-btn-primary" disabled={saving || loading} type="submit">
          {mode === 'auto' ? 'Создать авто-черновик' : 'Создать черновик'}
        </button>
      </form>
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
                  onClick={() => void call(`/campaigns/${encodeURIComponent(c.id)}`, { method: 'DELETE' }).then(refresh)}
                  type="button"
                >
                  Удалить
                </button>
              </div>
              <div className="ms-muted">
                {c.channel} · {c.mode} · аудитория {c.audience_count || 0} · {c.status || 'draft'}
              </div>
              {c.offer ? <p className="ms-campaign-offer">{c.offer}</p> : null}
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
