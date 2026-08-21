import { type ReactNode, useMemo, useState } from 'react'

import { DashboardCharts } from './dashboard-charts'

export type DashCell = {
  orders?: number
  turnover?: number
  revenue?: number
  margin?: number
  avg_check?: number | null
  commission?: number
  sokolniki_orders?: number
  sokolniki_turnover?: number
  universitet_orders?: number
  universitet_turnover?: number
}

export type DashChannelSeries = {
  key: string
  label: string
  commission_rate?: number
  turnover?: Array<number | null>
  revenue?: Array<number | null>
  margin?: Array<number | null>
  orders?: Array<number | null>
  avg_check?: Array<number | null>
  growth?: Record<string, Array<number | null | undefined>>
}

export type DashMatrix = {
  periods?: { id: string; label: string }[]
  channels?: DashChannelSeries[]
  totals?: {
    turnover?: Array<number | null>
    revenue?: Array<number | null>
    margin?: Array<number | null>
    orders?: Array<number | null>
    avg_check?: Array<number | null>
    growth?: Record<string, Array<number | null | undefined>>
  }
}

export type DashAnalytics = {
  formulas?: Record<string, string>
  commission_rates?: Record<string, number>
  channel_labels?: Record<string, string>
  metric_labels?: Record<string, string>
  kpi?: {
    turnover?: number
    revenue?: number
    orders?: number
    avg_check?: number | null
    margin?: number
    mom_turnover?: number | null
    period?: string
  }
  by_day?: { rows?: { id: string; kind?: string; label: string; channels?: Record<string, DashCell> }[]; channels?: string[] }
  by_week?: DashMatrix
  by_month?: DashMatrix
  flowwow?: {
    periods?: { id: string; label: string; year?: number }[]
    metrics?: Record<string, Array<number | null | undefined> | Record<string, Array<number | null | undefined>>>
    year_totals?: Record<string, Record<string, number | null>>
    unavailable?: string[]
  }
  order_count?: number
  notes?: string[]
  insights?: {
    id?: string
    tone?: string
    title?: string
    body?: string
    channel?: string | null
    metric?: string
    scope?: string
  }[]
}

const METRIC_KEYS = ['turnover', 'revenue', 'margin', 'orders', 'avg_check'] as const
const METRIC_RU: Record<string, string> = {
  turnover: 'Оборот',
  revenue: 'Выручка',
  margin: 'Маржа',
  orders: 'Заказы',
  avg_check: 'Ср чек',
  commission: 'Комиссия',
  new_clients: 'Новые клиенты',
  second_purchase: 'Вторая покупка',
  third_purchase: 'Третья покупка',
  regular_clients: 'Постоянные клиенты',
  platform_commission: 'Комиссия площадки'
}

const FLOWWOW_ROWS = [
  'turnover',
  'orders',
  'avg_check',
  'commission',
  'revenue',
  'new_clients',
  'second_purchase',
  'third_purchase',
  'regular_clients',
  'platform_commission'
] as const

type Tab = 'charts' | 'overview' | 'day' | 'week' | 'month' | 'flowwow'

function money(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(Number(n))) {
    return '—'
  }
  return Number(n).toLocaleString('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits
  })
}

function qty(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) {
    return '—'
  }
  return String(Math.round(Number(n)))
}

export function formatPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) {
    return ''
  }
  const pct = Number(n) * 100
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toLocaleString('ru-RU', { maximumFractionDigits: 1, minimumFractionDigits: 0 })}%`
}

function Pct({ value }: { value: number | null | undefined }) {
  const text = formatPct(value)
  if (!text) {
    return null
  }
  const cls = (value || 0) > 0.0005 ? 'is-up' : (value || 0) < -0.0005 ? 'is-down' : 'is-flat'
  return <span className={`ms-dash-pct ${cls}`}>{text}</span>
}

function seriesAt(ch: DashChannelSeries, metric: string, i: number): number | null | undefined {
  const raw = ch[metric as keyof DashChannelSeries]
  return Array.isArray(raw) ? (raw[i] as number | null | undefined) : undefined
}

function metricValue(metric: string, n: number | null | undefined): string {
  if (metric === 'orders' || metric === 'new_clients' || metric === 'second_purchase' || metric === 'third_purchase' || metric === 'regular_clients') {
    return qty(n)
  }
  if (metric === 'platform_commission') {
    return n == null ? '—' : formatPct(n)
  }
  if (metric === 'avg_check') {
    return money(n, 0)
  }
  return money(n, 0)
}

function MatrixTable({ matrix, title }: { matrix?: DashMatrix; title: string }) {
  const periods = matrix?.periods || []
  const channels = matrix?.channels || []
  const totals = matrix?.totals
  if (!periods.length) {
    return <p className="ms-muted">Нет оплаченных заказов за период.</p>
  }
  return (
    <div className="ms-table-wrap ms-dash-table-wrap">
      <table className="ms-table ms-dash-table">
        <thead>
          <tr>
            <th className="ms-dash-sticky">{title}</th>
            <th className="ms-dash-sticky-2">Показатель</th>
            {periods.map(p => (
              <th key={p.id}>{p.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {channels.map(ch =>
            METRIC_KEYS.map((metric, mi) => (
              <tr key={`${ch.key}-${metric}`} className={mi === 0 ? 'ms-dash-channel-start' : undefined}>
                {mi === 0 ? (
                  <th className="ms-dash-sticky" rowSpan={METRIC_KEYS.length}>
                    {ch.label}
                    <div className="ms-muted ms-dash-rate">
                      комиссия {(Number(ch.commission_rate || 0) * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%
                    </div>
                  </th>
                ) : null}
                <td className="ms-dash-sticky-2 ms-dash-metric">{METRIC_RU[metric]}</td>
                {periods.map((_, i) => (
                  <td key={`${ch.key}-${metric}-${i}`} className="ms-dash-num">
                    <div>{metricValue(metric, seriesAt(ch, metric, i))}</div>
                    <Pct value={ch.growth?.[metric]?.[i]} />
                  </td>
                ))}
              </tr>
            ))
          )}
          {totals
            ? METRIC_KEYS.map((metric, mi) => (
                <tr key={`total-${metric}`} className={mi === 0 ? 'ms-dash-total-start' : 'ms-dash-total'}>
                  {mi === 0 ? (
                    <th className="ms-dash-sticky" rowSpan={METRIC_KEYS.length}>
                      Итого
                    </th>
                  ) : null}
                  <td className="ms-dash-sticky-2 ms-dash-metric">{METRIC_RU[metric]}</td>
                  {periods.map((_, i) => (
                    <td key={`total-${metric}-${i}`} className="ms-dash-num">
                      <div>{metricValue(metric, totals[metric]?.[i] ?? null)}</div>
                      <Pct value={totals.growth?.[metric]?.[i]} />
                    </td>
                  ))}
                </tr>
              ))
            : null}
        </tbody>
      </table>
    </div>
  )
}

function DayTable({ analytics }: { analytics: DashAnalytics }) {
  const keys = analytics.by_day?.channels || []
  const labels = analytics.channel_labels || {}
  const rows = (analytics.by_day?.rows || []).filter(r => {
    if (r.kind === 'month') {
      return true
    }
    return keys.some(k => Number(r.channels?.[k]?.orders || 0) > 0)
  })
  if (!rows.length) {
    return <p className="ms-muted">Нет оплаченных заказов за выбранные дни.</p>
  }
  return (
    <div className="ms-table-wrap ms-dash-table-wrap">
      <table className="ms-table ms-dash-table">
        <thead>
          <tr>
            <th className="ms-dash-sticky" rowSpan={2}>
              Дата
            </th>
            {keys.map(k => (
              <th key={k} colSpan={2}>
                {labels[k] || k}
              </th>
            ))}
          </tr>
          <tr>
            {keys.flatMap(k => [
              <th key={`${k}-o`}>Заказы</th>,
              <th key={`${k}-t`}>Оборот</th>
            ])}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id} className={r.kind === 'month' ? 'ms-dash-month-row' : undefined}>
              <th className="ms-dash-sticky">{r.label}</th>
              {keys.flatMap(k => {
                const cell = r.channels?.[k]
                const sok = Number(cell?.sokolniki_orders || 0)
                const uni = Number(cell?.universitet_orders || 0)
                const split = sok > 0 && uni > 0
                return [
                  <td key={`${r.id}-${k}-o`} className="ms-dash-num">
                    {qty(cell?.orders)}
                    {split ? (
                      <div className="ms-muted ms-dash-store">
                        С {qty(sok)} / У {qty(uni)}
                      </div>
                    ) : null}
                  </td>,
                  <td key={`${r.id}-${k}-t`} className="ms-dash-num">
                    {money(cell?.turnover)}
                    {r.kind === 'month' && cell?.revenue ? (
                      <div className="ms-muted ms-dash-store">выр. {money(cell.revenue)}</div>
                    ) : null}
                  </td>
                ]
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FlowwowTable({ analytics }: { analytics: DashAnalytics }) {
  const fw = analytics.flowwow
  const periods = fw?.periods || []
  const metrics = fw?.metrics || {}
  const years = Object.keys(fw?.year_totals || {}).sort()
  if (!periods.length) {
    return <p className="ms-muted">Нет заказов FlowWow.</p>
  }
  const growth = (metrics.growth || {}) as Record<string, Array<number | null | undefined>>
  return (
    <>
      <div className="ms-table-wrap ms-dash-table-wrap">
        <table className="ms-table ms-dash-table">
          <thead>
            <tr>
              <th className="ms-dash-sticky">Показатель</th>
              {periods.map(p => (
                <th key={p.id}>{p.label}</th>
              ))}
              {years.map(y => (
                <th key={`y${y}`}>Итого {y}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {FLOWWOW_ROWS.map(metric => (
              <tr key={metric}>
                <th className="ms-dash-sticky ms-dash-metric">{METRIC_RU[metric]}</th>
                {periods.map((_, i) => {
                  const series = metrics[metric]
                  const val = Array.isArray(series) ? series[i] : null
                  return (
                    <td key={`${metric}-${i}`} className="ms-dash-num">
                      <div>{metricValue(metric, val as number | null)}</div>
                      <Pct value={growth[metric]?.[i]} />
                    </td>
                  )
                })}
                {years.map(y => {
                  const block = fw?.year_totals?.[y] || {}
                  const mapped: Record<string, number | null | undefined> = {
                    turnover: block.turnover,
                    orders: block.orders,
                    avg_check: block.avg_check,
                    commission: block.commission,
                    revenue: block.revenue,
                    platform_commission: block.platform_commission
                  }
                  return (
                    <td key={`${metric}-y${y}`} className="ms-dash-num">
                      {mapped[metric] == null ? '—' : metricValue(metric, mapped[metric])}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(fw?.unavailable || []).length ? (
        <p className="ms-muted">
          Нет в МойСклад (только кабинет FlowWow): {(fw?.unavailable || []).join(', ')}.
        </p>
      ) : null}
    </>
  )
}

export function DashboardAnalytics({
  analytics,
  overview
}: {
  analytics?: DashAnalytics | null
  overview: ReactNode
}) {
  const [tab, setTab] = useState<Tab>('charts')
  const kpi = analytics?.kpi
  const tabs: { id: Tab; label: string }[] = useMemo(
    () => [
      { id: 'charts', label: 'Графики' },
      { id: 'month', label: 'Месяц' },
      { id: 'week', label: 'Неделя' },
      { id: 'day', label: 'По дням' },
      { id: 'flowwow', label: 'Флау' },
      { id: 'overview', label: 'База' }
    ],
    []
  )

  return (
    <>
      {kpi ? (
        <div className="ms-stats-grid ms-dashboard-grid">
          <div>
            <div className="ms-stat-val">{money(kpi.turnover)}</div>
            <div className="ms-muted">Оборот · {kpi.period || 'месяц'}</div>
          </div>
          <div>
            <div className="ms-stat-val">{money(kpi.revenue)}</div>
            <div className="ms-muted">Выручка (после комиссии)</div>
          </div>
          <div>
            <div className="ms-stat-val">{qty(kpi.orders)}</div>
            <div className="ms-muted">Заказы</div>
          </div>
          <div>
            <div className="ms-stat-val">{kpi.avg_check != null ? money(kpi.avg_check) : '—'}</div>
            <div className="ms-muted">Средний чек</div>
          </div>
          <div>
            <div className="ms-stat-val">{money(kpi.margin)}</div>
            <div className="ms-muted">Маржа</div>
          </div>
          <div>
            <div className="ms-stat-val">
              <Pct value={kpi.mom_turnover} />
              {!formatPct(kpi.mom_turnover) ? '—' : null}
            </div>
            <div className="ms-muted">Прирост оборота к прошлому месяцу</div>
          </div>
        </div>
      ) : null}

      <div className="ms-filter-tabs" role="tablist">
        {tabs.map(t => (
          <button
            className={`ms-filter-tab${tab === t.id ? ' is-active' : ''}`}
            key={t.id}
            onClick={() => setTab(t.id)}
            role="tab"
            type="button"
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'charts' && analytics ? <DashboardCharts analytics={analytics} /> : null}
      {tab === 'overview' ? overview : null}
      {tab === 'day' ? <DayTable analytics={analytics || {}} /> : null}
      {tab === 'week' ? <MatrixTable matrix={analytics?.by_week} title="Канал" /> : null}
      {tab === 'month' ? <MatrixTable matrix={analytics?.by_month} title="Канал" /> : null}
      {tab === 'flowwow' ? <FlowwowTable analytics={analytics || {}} /> : null}

      {tab !== 'overview' && analytics?.notes?.length ? (
        <p className="ms-muted ms-dash-notes">{analytics.notes.join(' ')}</p>
      ) : null}
    </>
  )
}
