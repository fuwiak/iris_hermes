import { lazy, type ReactNode, Suspense, useMemo, useState } from 'react'

import {
  type ChannelSortKey,
  filterChannels,
  filterDayRows,
  matchesQuery,
  nextSortDir,
  sortChannels,
  sortDayRows,
  type SortDir
} from './dashboard-table-ops'

const DashboardCharts = lazy(() => import('./dashboard-charts').then(m => ({ default: m.DashboardCharts })))

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

export function sparkPoints(
  series: Array<number | null | undefined>,
  width = 120,
  height = 36,
  pad = 3
): string {
  const vals = series.map(v => (v == null || Number.isNaN(Number(v)) ? null : Number(v)))
  const nums = vals.filter((v): v is number => v != null)
  if (nums.length < 2) {
    return ''
  }
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const span = max - min || 1
  const n = vals.length
  const pts: string[] = []
  vals.forEach((v, i) => {
    if (v == null) {
      return
    }
    const x = n > 1 ? pad + (i * (width - pad * 2)) / (n - 1) : width / 2
    const y = pad + (1 - (v - min) / span) * (height - pad * 2)
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  })
  return pts.join(' ')
}

export function lastGrowth(
  matrix: DashMatrix | undefined,
  metric: string
): number | null {
  const series = matrix?.totals?.growth?.[metric]
  if (!Array.isArray(series)) {
    return null
  }
  for (let i = series.length - 1; i >= 0; i--) {
    const v = series[i]
    if (v != null && !Number.isNaN(Number(v))) {
      return Number(v)
    }
  }
  return null
}

const KPI_ICONS: Record<string, ReactNode> = {
  turnover: (
    <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
      <path d="M4 19V9" />
      <path d="M10 19V5" />
      <path d="M16 19v-7" />
      <path d="M22 19V8" />
    </svg>
  ),
  revenue: (
    <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
      <rect height="13" rx="2" width="16" x="4" y="6" />
      <path d="M4 10h16" />
    </svg>
  ),
  orders: (
    <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
      <path d="M4 7h16v12H4z" />
      <path d="M8 7V5a4 4 0 0 1 8 0v2" />
    </svg>
  ),
  avg_check: (
    <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z" />
      <path d="M9 8h6M9 12h6" />
    </svg>
  ),
  margin: (
    <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
      <path d="M3 17l6-6 4 4 7-7" />
      <path d="M14 8h6v6" />
    </svg>
  )
}

const KPI_TONES: Record<string, string> = {
  turnover: 'pink',
  revenue: 'green',
  orders: 'purple',
  avg_check: 'orange',
  margin: 'blue'
}

const KPI_SPARK_STROKES: Record<string, string> = {
  pink: '#ff4165',
  green: '#36c878',
  purple: '#8b5cf6',
  orange: '#ff9e3d',
  blue: '#3b82f6'
}

function KpiCard({
  metric,
  label,
  value,
  growth,
  growthHint,
  spark,
  invert
}: {
  metric: string
  label: string
  value: string
  growth: number | null
  growthHint: string
  spark: Array<number | null | undefined>
  invert?: boolean
}) {
  const tone = KPI_TONES[metric] || 'purple'
  const points = sparkPoints(spark)
  const text = formatPct(growth)
  const up = (growth || 0) > 0.0005
  const down = (growth || 0) < -0.0005
  const good = invert ? down : up
  const bad = invert ? up : down
  return (
    <article className="ms-kpi-card">
      <div className="ms-kpi-head">
        <span aria-hidden="true" className={`ms-kpi-ico is-${tone}`}>
          {KPI_ICONS[metric]}
        </span>
        <span className="ms-kpi-label">{label}</span>
      </div>
      <div className="ms-kpi-val">{value}</div>
      {text ? (
        <div className={`ms-kpi-delta${good ? ' is-good' : bad ? ' is-bad' : ''}`}>
          <span className="ms-kpi-pct">
            {up ? '↑' : down ? '↓' : '·'} {text.replace('+', '').replace('-', '')}
          </span>
          <span className="ms-kpi-vs">{growthHint}</span>
        </div>
      ) : (
        <div className="ms-kpi-delta">
          <span className="ms-kpi-vs">{growthHint}</span>
        </div>
      )}
      {points ? (
        <svg aria-hidden="true" className="ms-kpi-spark" preserveAspectRatio="none" viewBox="0 0 120 36">
          <polyline points={points} stroke={KPI_SPARK_STROKES[tone]} />
        </svg>
      ) : null}
    </article>
  )
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

function TableToolbar({
  query,
  onQuery,
  sortLabel,
  onToggleSort,
  placeholder
}: {
  query: string
  onQuery: (q: string) => void
  sortLabel: string
  onToggleSort: () => void
  placeholder: string
}) {
  return (
    <div className="ms-dash-table-tools">
      <input
        aria-label="Фильтр таблицы"
        className="ms-dash-filter"
        onChange={e => onQuery(e.target.value)}
        placeholder={placeholder}
        type="search"
        value={query}
      />
      <button className="ms-link-btn" onClick={onToggleSort} type="button">
        {sortLabel}
      </button>
    </div>
  )
}

function MatrixTable({ matrix, title }: { matrix?: DashMatrix; title: string }) {
  const periods = matrix?.periods || []
  const totals = matrix?.totals
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<ChannelSortKey>('turnover')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const last = Math.max(0, periods.length - 1)
  const channels = useMemo(
    () => sortChannels(filterChannels(matrix?.channels || [], query), sortKey, sortDir, last),
    [matrix?.channels, query, sortDir, sortKey, last]
  )
  if (!periods.length) {
    return <p className="ms-muted">Нет оплаченных заказов за период.</p>
  }
  const toggle = (key: ChannelSortKey) => {
    if (sortKey === key) {
      setSortDir(nextSortDir(sortDir))
    } else {
      setSortKey(key)
      setSortDir(key === 'label' ? 'asc' : 'desc')
    }
  }
  const sortHint =
    sortKey === 'label'
      ? `Сорт: канал ${sortDir === 'asc' ? 'А→Я' : 'Я→А'}`
      : `Сорт: ${METRIC_RU[sortKey]} ${sortDir === 'desc' ? '↓' : '↑'}`
  return (
    <>
      <TableToolbar
        onQuery={setQuery}
        onToggleSort={() => toggle(sortKey === 'label' ? 'turnover' : sortKey)}
        placeholder="Фильтр канала…"
        query={query}
        sortLabel={sortHint}
      />
      <div className="ms-table-wrap ms-dash-table-wrap">
      <table className="ms-table ms-dash-table">
        <thead>
          <tr>
            <th className="ms-dash-sticky">
              <button className="ms-dash-th-btn" onClick={() => toggle('label')} type="button">
                {title}
              </button>
            </th>
            <th className="ms-dash-sticky-2">Показатель</th>
            {periods.map(p => (
              <th key={p.id}>{p.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {channels.map(ch =>
            METRIC_KEYS.map((metric, mi) => (
              <tr className={mi === 0 ? 'ms-dash-channel-start' : undefined} key={`${ch.key}-${metric}`}>
                {mi === 0 ? (
                  <th className="ms-dash-sticky" rowSpan={METRIC_KEYS.length}>
                    {ch.label}
                    <div className="ms-muted ms-dash-rate">
                      комиссия {(Number(ch.commission_rate || 0) * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%
                    </div>
                  </th>
                ) : null}
                <td className="ms-dash-sticky-2 ms-dash-metric">
                  <button className="ms-dash-th-btn" onClick={() => toggle(metric)} type="button">
                    {METRIC_RU[metric]}
                  </button>
                </td>
                {periods.map((_, i) => (
                  <td className="ms-dash-num" key={`${ch.key}-${metric}-${i}`}>
                    <div>{metricValue(metric, seriesAt(ch, metric, i))}</div>
                    <Pct value={ch.growth?.[metric]?.[i]} />
                  </td>
                ))}
              </tr>
            ))
          )}
          {totals
            ? METRIC_KEYS.map((metric, mi) => (
                <tr className={mi === 0 ? 'ms-dash-total-start' : 'ms-dash-total'} key={`total-${metric}`}>
                  {mi === 0 ? (
                    <th className="ms-dash-sticky" rowSpan={METRIC_KEYS.length}>
                      Итого
                    </th>
                  ) : null}
                  <td className="ms-dash-sticky-2 ms-dash-metric">{METRIC_RU[metric]}</td>
                  {periods.map((_, i) => (
                    <td className="ms-dash-num" key={`total-${metric}-${i}`}>
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
      {query && !channels.length ? <p className="ms-muted">Нет каналов по фильтру «{query}».</p> : null}
    </>
  )
}

function DayTable({ analytics }: { analytics: DashAnalytics }) {
  const keys = analytics.by_day?.channels || []
  const labels = analytics.channel_labels || {}
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<'date' | 'orders' | 'turnover'>('date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const visibleKeys = useMemo(
    () => keys.filter(k => matchesQuery(`${labels[k] || ''} ${k}`, query) || !query.trim()),
    [keys, labels, query]
  )
  const rows = useMemo(
    () => sortDayRows(filterDayRows(analytics.by_day?.rows || [], query, visibleKeys.length ? visibleKeys : keys), sortKey, sortDir, visibleKeys.length ? visibleKeys : keys),
    [analytics.by_day?.rows, keys, query, sortDir, sortKey, visibleKeys]
  )
  const channelKeys = visibleKeys.length ? visibleKeys : keys
  if (!rows.length) {
    return (
      <>
        <TableToolbar
          onQuery={setQuery}
          onToggleSort={() => setSortDir(nextSortDir(sortDir))}
          placeholder="Фильтр даты или канала…"
          query={query}
          sortLabel={`Сорт: ${sortKey === 'date' ? 'дата' : sortKey === 'orders' ? 'заказы' : 'оборот'} ${sortDir === 'desc' ? '↓' : '↑'}`}
        />
        <p className="ms-muted">Нет оплаченных заказов за выбранные дни.</p>
      </>
    )
  }
  const cycleSort = () => {
    const order: Array<'date' | 'orders' | 'turnover'> = ['date', 'turnover', 'orders']
    const next = order[(order.indexOf(sortKey) + 1) % order.length]
    setSortKey(next)
    setSortDir(next === 'date' ? 'desc' : 'desc')
  }
  return (
    <>
      <TableToolbar
        onQuery={setQuery}
        onToggleSort={cycleSort}
        placeholder="Фильтр даты или канала…"
        query={query}
        sortLabel={`Сорт: ${sortKey === 'date' ? 'дата' : sortKey === 'orders' ? 'заказы' : 'оборот'} ${sortDir === 'desc' ? '↓' : '↑'}`}
      />
    <div className="ms-table-wrap ms-dash-table-wrap">
      <table className="ms-table ms-dash-table">
        <thead>
          <tr>
            <th className="ms-dash-sticky" rowSpan={2}>
              Дата
            </th>
            {channelKeys.map(k => (
              <th colSpan={2} key={k}>
                {labels[k] || k}
              </th>
            ))}
          </tr>
          <tr>
            {channelKeys.flatMap(k => [
              <th key={`${k}-o`}>Заказы</th>,
              <th key={`${k}-t`}>Оборот</th>
            ])}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr className={r.kind === 'month' ? 'ms-dash-month-row' : undefined} key={r.id}>
              <th className="ms-dash-sticky">{r.label}</th>
              {channelKeys.flatMap(k => {
                const cell = r.channels?.[k]
                const sok = Number(cell?.sokolniki_orders || 0)
                const uni = Number(cell?.universitet_orders || 0)
                const split = sok > 0 && uni > 0
                return [
                  <td className="ms-dash-num" key={`${r.id}-${k}-o`}>
                    {qty(cell?.orders)}
                    {split ? (
                      <div className="ms-muted ms-dash-store">
                        С {qty(sok)} / У {qty(uni)}
                      </div>
                    ) : null}
                  </td>,
                  <td className="ms-dash-num" key={`${r.id}-${k}-t`}>
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
    </>
  )
}

function FlowwowTable({ analytics }: { analytics: DashAnalytics }) {
  const fw = analytics.flowwow
  const periods = fw?.periods || []
  const metrics = fw?.metrics || {}
  const years = Object.keys(fw?.year_totals || {}).sort()
  const [query, setQuery] = useState('')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const metricRows = useMemo(() => {
    const filtered = FLOWWOW_ROWS.filter(metric =>
      matchesQuery(`${METRIC_RU[metric]} ${metric}`, query)
    )
    return sortDir === 'asc' ? filtered : [...filtered].reverse()
  }, [query, sortDir])
  if (!periods.length) {
    return <p className="ms-muted">Нет заказов FlowWow.</p>
  }
  const growth = (metrics.growth || {}) as Record<string, Array<number | null | undefined>>
  return (
    <>
      <TableToolbar
        onQuery={setQuery}
        onToggleSort={() => setSortDir(nextSortDir(sortDir))}
        placeholder="Фильтр показателя…"
        query={query}
        sortLabel={`Сорт: ${sortDir === 'asc' ? 'как в Excel' : 'обратный'}`}
      />
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
            {metricRows.map(metric => (
              <tr key={metric}>
                <th className="ms-dash-sticky ms-dash-metric">{METRIC_RU[metric]}</th>
                {periods.map((_, i) => {
                  const series = metrics[metric]
                  const val = Array.isArray(series) ? series[i] : null
                  return (
                    <td className="ms-dash-num" key={`${metric}-${i}`}>
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
                    <td className="ms-dash-num" key={`${metric}-y${y}`}>
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
  overview,
  cacheMeta
}: {
  analytics?: DashAnalytics | null
  overview: ReactNode
  cacheMeta?: {
    cache_backend?: string
    cached?: boolean
    stale?: boolean
    synced_at_label?: string
    analytics_cached?: boolean
  }
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
      {cacheMeta?.cache_backend ? (
        <p className="ms-muted ms-dash-cache">
          Кэш {cacheMeta.cache_backend}
          {cacheMeta.synced_at_label ? ` · каталог ${cacheMeta.synced_at_label}` : ''}
          {cacheMeta.analytics_cached ? ' · аналитика из кэша' : ' · аналитика пересчитана'}
          {cacheMeta.stale ? ' · устарел, фоновое обновление' : ''}
          . API МойСклад не дергаем, пока жив кэш.
        </p>
      ) : null}
      {kpi ? (
        <div className="ms-kpi-grid">
          <KpiCard
            growth={kpi.mom_turnover ?? lastGrowth(analytics?.by_month, 'turnover')}
            growthHint="к прошлому месяцу"
            label={`Оборот · ${kpi.period || 'месяц'}`}
            metric="turnover"
            spark={analytics?.by_week?.totals?.turnover || []}
            value={`${money(kpi.turnover)} ₽`}
          />
          <KpiCard
            growth={lastGrowth(analytics?.by_month, 'revenue')}
            growthHint="к прошлому месяцу"
            label="Выручка (после комиссии)"
            metric="revenue"
            spark={analytics?.by_week?.totals?.revenue || []}
            value={`${money(kpi.revenue)} ₽`}
          />
          <KpiCard
            growth={lastGrowth(analytics?.by_month, 'orders')}
            growthHint="к прошлому месяцу"
            label="Заказы"
            metric="orders"
            spark={analytics?.by_week?.totals?.orders || []}
            value={qty(kpi.orders)}
          />
          <KpiCard
            growth={lastGrowth(analytics?.by_month, 'avg_check')}
            growthHint="к прошлому месяцу"
            label="Средний чек"
            metric="avg_check"
            spark={analytics?.by_week?.totals?.avg_check || []}
            value={kpi.avg_check != null ? `${money(kpi.avg_check)} ₽` : '—'}
          />
          <KpiCard
            growth={lastGrowth(analytics?.by_month, 'margin')}
            growthHint="к прошлому месяцу"
            label="Маржа"
            metric="margin"
            spark={analytics?.by_week?.totals?.margin || []}
            value={`${money(kpi.margin)} ₽`}
          />
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

      {tab === 'charts' && analytics ? (
        <Suspense fallback={<p className="ms-muted">Загружаем ECharts / Plotly…</p>}>
          <DashboardCharts analytics={analytics} />
        </Suspense>
      ) : null}
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
