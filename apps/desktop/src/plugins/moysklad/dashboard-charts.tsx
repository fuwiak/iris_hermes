import { useMemo, useState } from 'react'

import { type DashAnalytics, type DashChannelSeries, type DashMatrix } from './dashboard-analytics'
import {
  CHART_PAD,
  type ChartMetric,
  channelColor,
  linePoints,
  nearestPeriodIndex,
  niceMax,
  stackColumns,
  xAt,
  yAt
} from './dashboard-chart-model'

export type DashInsight = {
  id?: string
  tone?: string
  title?: string
  body?: string
  channel?: string | null
  metric?: string
  scope?: string
}

const METRIC_OPTS: { id: ChartMetric; label: string }[] = [
  { id: 'turnover', label: 'Оборот' },
  { id: 'revenue', label: 'Выручка' },
  { id: 'orders', label: 'Заказы' },
  { id: 'avg_check', label: 'Ср чек' }
]

const W = 720
const H = 248

function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) {
    return '—'
  }
  return Number(n).toLocaleString('ru-RU', { maximumFractionDigits: 0 })
}

function metricVal(metric: ChartMetric, n: number | null | undefined): string {
  if (metric === 'orders') {
    return n == null ? '—' : String(Math.round(Number(n)))
  }
  return money(n)
}

function seriesOf(ch: DashChannelSeries, metric: ChartMetric): number[] {
  const raw = ch[metric] || []
  return raw.map(v => Number(v) || 0)
}

function DashboardLineChart({
  matrix,
  metric,
  hidden,
  hover,
  onHover,
  onLeave
}: {
  matrix: DashMatrix
  metric: ChartMetric
  hidden: Set<string>
  hover: number | null
  onHover: (i: number) => void
  onLeave: () => void
}) {
  const periods = matrix.periods || []
  const channels = (matrix.channels || []).filter(ch => !hidden.has(ch.key))
  const n = periods.length
  const values = channels.map(ch => seriesOf(ch, metric))
  const max = niceMax(Math.max(0, ...values.flat()))
  const ticks = [0, 0.5, 1].map(t => t * max)

  return (
    <div className="ms-dash-chart">
      <svg
        aria-label="Динамика по периодам"
        className="ms-dash-svg"
        onMouseLeave={onLeave}
        onMouseMove={e => {
          const box = e.currentTarget.getBoundingClientRect()
          const x = ((e.clientX - box.left) / box.width) * W
          onHover(nearestPeriodIndex(x, W, n))
        }}
        role="img"
        viewBox={`0 0 ${W} ${H}`}
      >
        {ticks.map(t => (
          <g key={t}>
            <line
              className="ms-dash-grid"
              x1={CHART_PAD.l}
              x2={W - CHART_PAD.r}
              y1={yAt(t, max, H)}
              y2={yAt(t, max, H)}
            />
            <text className="ms-dash-axis" x={8} y={yAt(t, max, H) + 4}>
              {metric === 'orders' ? String(Math.round(t)) : money(t)}
            </text>
          </g>
        ))}
        {periods.map((p, i) =>
          i % Math.max(1, Math.ceil(n / 8)) === 0 || i === n - 1 ? (
            <text className="ms-dash-axis" key={p.id} textAnchor="middle" x={xAt(i, n, W)} y={H - 12}>
              {p.label.replace(/ \d{4}$/, '')}
            </text>
          ) : null
        )}
        {hover != null && n > 0 ? (
          <line
            className="ms-dash-hover-line"
            x1={xAt(hover, n, W)}
            x2={xAt(hover, n, W)}
            y1={CHART_PAD.t}
            y2={H - CHART_PAD.b}
          />
        ) : null}
        {channels.map(ch => (
          <polyline
            fill="none"
            key={ch.key}
            points={linePoints(seriesOf(ch, metric), max, W, H)}
            stroke={channelColor(ch.key)}
            strokeLinejoin="round"
            strokeWidth={hover != null ? 2.4 : 2}
          />
        ))}
        {hover != null
          ? channels.map(ch => (
              <circle
                cx={xAt(hover, n, W)}
                cy={yAt(seriesOf(ch, metric)[hover] || 0, max, H)}
                fill={channelColor(ch.key)}
                key={`${ch.key}-dot`}
                r={4}
              />
            ))
          : null}
      </svg>
    </div>
  )
}

function DashboardStackChart({
  matrix,
  metric,
  hidden,
  hover,
  onHover,
  onLeave
}: {
  matrix: DashMatrix
  metric: ChartMetric
  hidden: Set<string>
  hover: number | null
  onHover: (i: number) => void
  onLeave: () => void
}) {
  const periods = matrix.periods || []
  const channels = (matrix.channels || []).filter(ch => !hidden.has(ch.key))
  const n = periods.length
  const cols = stackColumns(
    channels.map(ch => ({ key: ch.key, values: seriesOf(ch, metric) })),
    n
  )
  const max = niceMax(Math.max(0, ...cols.map(c => (c.length ? c[c.length - 1].y1 : 0))))
  const innerW = W - CHART_PAD.l - CHART_PAD.r
  const gap = 4
  const barW = n <= 0 ? 0 : Math.max(6, innerW / n - gap)

  return (
    <div className="ms-dash-chart">
      <svg
        aria-label="Состав оборота по каналам"
        className="ms-dash-svg"
        onMouseLeave={onLeave}
        onMouseMove={e => {
          const box = e.currentTarget.getBoundingClientRect()
          const x = ((e.clientX - box.left) / box.width) * W
          onHover(nearestPeriodIndex(x, W, n))
        }}
        role="img"
        viewBox={`0 0 ${W} ${H}`}
      >
        {cols.map((segs, i) => {
          const x = xAt(i, n, W) - barW / 2
          return (
            <g key={periods[i]?.id || i} opacity={hover == null || hover === i ? 1 : 0.35}>
              {segs.map(seg => {
                const y1 = yAt(seg.y1, max, H)
                const y0 = yAt(seg.y0, max, H)
                return (
                  <rect
                    fill={channelColor(seg.key)}
                    height={Math.max(1, y0 - y1)}
                    key={seg.key}
                    rx={2}
                    width={barW}
                    x={x}
                    y={y1}
                  />
                )
              })}
            </g>
          )
        })}
        {periods.map((p, i) =>
          i % Math.max(1, Math.ceil(n / 8)) === 0 || i === n - 1 ? (
            <text className="ms-dash-axis" key={p.id} textAnchor="middle" x={xAt(i, n, W)} y={H - 12}>
              {p.label.replace(/ \d{4}$/, '')}
            </text>
          ) : null
        )}
      </svg>
    </div>
  )
}

function ShareBars({
  matrix,
  metric,
  hidden,
  onToggle
}: {
  matrix: DashMatrix
  metric: ChartMetric
  hidden: Set<string>
  onToggle: (key: string) => void
}) {
  const channels = matrix.channels || []
  const last = Math.max(0, (matrix.periods || []).length - 1)
  const rows = channels
    .map(ch => ({
      key: ch.key,
      label: ch.label,
      value: seriesOf(ch, metric)[last] || 0
    }))
    .filter(r => r.value > 0)
    .sort((a, b) => b.value - a.value)
  const max = rows[0]?.value || 1
  if (!rows.length) {
    return <p className="ms-muted">Нет оборота в последнем периоде.</p>
  }
  return (
    <div className="ms-dash-share">
      {rows.map(r => (
        <button
          className={`ms-dash-share-row${hidden.has(r.key) ? ' is-off' : ''}`}
          key={r.key}
          onClick={() => onToggle(r.key)}
          type="button"
        >
          <span className="ms-dash-share-lab">
            <i style={{ background: channelColor(r.key) }} />
            {r.label}
          </span>
          <span className="ms-dash-share-track">
            <span style={{ width: `${(r.value / max) * 100}%`, background: channelColor(r.key) }} />
          </span>
          <span className="ms-dash-share-val">{metricVal(metric, r.value)}</span>
        </button>
      ))}
    </div>
  )
}

export function DashboardCharts({ analytics }: { analytics: DashAnalytics }) {
  const insights = analytics.insights || []
  const [metric, setMetric] = useState<ChartMetric>('turnover')
  const [scope, setScope] = useState<'month' | 'week'>('month')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [hover, setHover] = useState<number | null>(null)
  const [activeInsight, setActiveInsight] = useState<string | null>(null)

  const matrix = scope === 'week' ? analytics.by_week : analytics.by_month
  const periods = matrix?.periods || []
  const channels = matrix?.channels || []

  const applyInsight = (row: DashInsight) => {
    setActiveInsight(row.id || null)
    if (row.metric === 'turnover' || row.metric === 'revenue' || row.metric === 'orders' || row.metric === 'avg_check') {
      setMetric(row.metric)
    }
    if (row.scope === 'week' || row.scope === 'month') {
      setScope(row.scope)
    }
    if (row.channel) {
      const others = new Set((matrix?.channels || []).map(c => c.key).filter(k => k !== row.channel))
      setHidden(others)
    } else {
      setHidden(new Set())
    }
  }

  const toggle = (key: string) => {
    setActiveInsight(null)
    setHidden(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const tooltip = useMemo(() => {
    if (hover == null || !matrix || !periods[hover]) {
      return null
    }
    const rows = channels
      .filter(ch => !hidden.has(ch.key))
      .map(ch => ({
        key: ch.key,
        label: ch.label,
        value: seriesOf(ch, metric)[hover] || 0
      }))
      .filter(r => r.value > 0)
      .sort((a, b) => b.value - a.value)
    return { label: periods[hover].label, rows }
  }, [channels, hidden, hover, matrix, metric, periods])

  if (!matrix || !periods.length) {
    return <p className="ms-muted">Мало данных для графиков — нужен тёплый каталог заказов.</p>
  }

  return (
    <div className="ms-dash-board">
      {insights.length ? (
        <div className="ms-dash-takes">
          {insights.map(row => (
            <button
              className={`ms-dash-take is-${row.tone || 'info'}${activeInsight === row.id ? ' is-active' : ''}`}
              key={row.id}
              onClick={() => applyInsight(row)}
              type="button"
            >
              <strong>{row.title}</strong>
              <span>{row.body}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="ms-muted">Hot take появятся, когда будет хотя бы два периода с заказами.</p>
      )}

      <div className="ms-dash-chart-toolbar">
        <div className="ms-filter-tabs" role="tablist">
          {METRIC_OPTS.map(opt => (
            <button
              className={`ms-filter-tab${metric === opt.id ? ' is-active' : ''}`}
              key={opt.id}
              onClick={() => setMetric(opt.id)}
              type="button"
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="ms-filter-tabs" role="tablist">
          <button
            className={`ms-filter-tab${scope === 'month' ? ' is-active' : ''}`}
            onClick={() => setScope('month')}
            type="button"
          >
            Месяцы
          </button>
          <button
            className={`ms-filter-tab${scope === 'week' ? ' is-active' : ''}`}
            onClick={() => setScope('week')}
            type="button"
          >
            Недели
          </button>
        </div>
        <button className="ms-link-btn" onClick={() => { setHidden(new Set()); setActiveInsight(null) }} type="button">
          Все каналы
        </button>
      </div>

      <div className="ms-dash-legend">
        {channels.map(ch => (
          <button
            className={`ms-dash-leg${hidden.has(ch.key) ? ' is-off' : ''}`}
            key={ch.key}
            onClick={() => toggle(ch.key)}
            type="button"
          >
            <i style={{ background: channelColor(ch.key) }} />
            {ch.label}
          </button>
        ))}
      </div>

      <div className="ms-dash-chart-grid">
        <section>
          <h3>Динамика</h3>
          <p className="ms-muted">Наведите — период. Клик по легенде прячет канал.</p>
          <DashboardLineChart
            hidden={hidden}
            hover={hover}
            matrix={matrix}
            metric={metric}
            onHover={setHover}
            onLeave={() => setHover(null)}
          />
        </section>
        <section>
          <h3>Состав</h3>
          <p className="ms-muted">Стек каналов. Затемнение = не выбранный период.</p>
          <DashboardStackChart
            hidden={hidden}
            hover={hover}
            matrix={matrix}
            metric={metric}
            onHover={setHover}
            onLeave={() => setHover(null)}
          />
        </section>
      </div>

      {tooltip ? (
        <div className="ms-dash-tip">
          <strong>{tooltip.label}</strong>
          {tooltip.rows.length ? (
            tooltip.rows.map(r => (
              <div key={r.key}>
                <i style={{ background: channelColor(r.key) }} />
                {r.label}: {metricVal(metric, r.value)}
              </div>
            ))
          ) : (
            <div>пусто</div>
          )}
        </div>
      ) : null}

      <section>
        <h3>Доли сейчас</h3>
        <p className="ms-muted">Последний {scope === 'week' ? 'недельный' : 'месячный'} столбец. Клик — вкл/выкл на графиках.</p>
        <ShareBars hidden={hidden} matrix={matrix} metric={metric} onToggle={toggle} />
      </section>
    </div>
  )
}
