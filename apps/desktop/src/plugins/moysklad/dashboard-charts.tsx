import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import type { EChartsType } from 'echarts/core'
import Plotly from 'plotly.js-dist-min'

import { type DashAnalytics } from './dashboard-analytics'
import {
  buildHeatmapTrace,
  buildLineOption,
  buildPieOption,
  buildStackOption,
  channelColor,
  type ChartMetric
} from './dashboard-chart-model'

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer
])

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

function EChart({
  option,
  onLegend
}: {
  option: EChartsOption
  onLegend?: (selected: Record<string, boolean>) => void
}) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<EChartsType | null>(null)
  const onLegendRef = useRef(onLegend)
  onLegendRef.current = onLegend

  useEffect(() => {
    const el = elRef.current
    if (!el) {
      return
    }
    const chart = echarts.init(el, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const onSelect = (ev: { selected?: Record<string, boolean> }) => {
      if (ev.selected) {
        onLegendRef.current?.(ev.selected)
      }
    }
    chart.on('legendselectchanged', onSelect)
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.off('legendselectchanged', onSelect)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div className="ms-dash-echart" ref={elRef} role="img" />
}

function PlotlyHeatmap({
  x,
  y,
  z,
  title
}: {
  x: string[]
  y: string[]
  z: number[][]
  title: string
}) {
  const elRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = elRef.current
    if (!el) {
      return
    }
    void Plotly.react(
      el,
      [
        {
          type: 'heatmap',
          x,
          y,
          z,
          colorscale: [
            [0, '#2f1236'],
            [0.45, '#7c3a8c'],
            [1, '#e8b86d']
          ],
          hoverongaps: false,
          colorbar: { tickfont: { color: '#f0daf5' }, outlinecolor: '#592466' }
        }
      ],
      {
        title: { text: title, font: { color: '#f4ede4', size: 13 } },
        margin: { l: 110, r: 48, t: 36, b: 64 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: '#2f1236',
        font: { color: '#f0daf5' },
        xaxis: { ticks: '', side: 'bottom' },
        yaxis: { ticks: '', automargin: true }
      },
      { responsive: true, displaylogo: false }
    )
    const ro = new ResizeObserver(() => {
      void Plotly.Plots.resize(el)
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      void Plotly.purge(el)
    }
  }, [title, x, y, z])

  return <div className="ms-dash-plotly" ref={elRef} />
}

export function DashboardCharts({ analytics }: { analytics: DashAnalytics }) {
  const insights = analytics.insights || []
  const [metric, setMetric] = useState<ChartMetric>('turnover')
  const [scope, setScope] = useState<'month' | 'week'>('month')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
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
      setHidden(new Set(channels.map(c => c.key).filter(k => k !== row.channel)))
    } else {
      setHidden(new Set())
    }
  }

  const onLegend = useCallback((selected: Record<string, boolean>) => {
    setActiveInsight(null)
    setHidden(new Set(channels.filter(ch => selected[ch.label] === false).map(ch => ch.key)))
  }, [channels])

  const lineOption = useMemo(
    () => (matrix ? buildLineOption(periods, channels, metric, hidden) : {}),
    [channels, hidden, matrix, metric, periods]
  )
  const stackOption = useMemo(
    () => (matrix ? buildStackOption(periods, channels, metric, hidden) : {}),
    [channels, hidden, matrix, metric, periods]
  )
  const pieOption = useMemo(
    () => (matrix ? buildPieOption(channels, metric, hidden) : {}),
    [channels, hidden, matrix, metric]
  )
  const heat = useMemo(
    () => (matrix ? buildHeatmapTrace(periods, channels, metric, hidden) : { x: [], y: [], z: [] }),
    [channels, hidden, matrix, metric, periods]
  )

  if (!matrix || !periods.length) {
    return <p className="ms-muted">Мало данных для графиков — нужен тёплый каталог заказов.</p>
  }

  const metricLabel = METRIC_OPTS.find(o => o.id === metric)?.label || metric

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
        <button
          className="ms-link-btn"
          onClick={() => {
            setHidden(new Set())
            setActiveInsight(null)
          }}
          type="button"
        >
          Все каналы
        </button>
      </div>

      <div className="ms-dash-legend">
        {channels.map(ch => (
          <button
            className={`ms-dash-leg${hidden.has(ch.key) ? ' is-off' : ''}`}
            key={ch.key}
            onClick={() => {
              setActiveInsight(null)
              setHidden(prev => {
                const next = new Set(prev)
                if (next.has(ch.key)) {
                  next.delete(ch.key)
                } else {
                  next.add(ch.key)
                }
                return next
              })
            }}
            type="button"
          >
            <i style={{ background: channelColor(ch.key) }} />
            {ch.label}
          </button>
        ))}
      </div>

      <div className="ms-dash-chart-grid">
        <section>
          <h3>Динамика · ECharts</h3>
          <p className="ms-muted">Zoom колесом, легенда кликабельна, tooltip по периоду.</p>
          <div className="ms-dash-chart">
            <EChart onLegend={onLegend} option={lineOption} />
          </div>
        </section>
        <section>
          <h3>Состав · ECharts</h3>
          <p className="ms-muted">Стек каналов. DataZoom тот же, что на линии.</p>
          <div className="ms-dash-chart">
            <EChart onLegend={onLegend} option={stackOption} />
          </div>
        </section>
      </div>

      <div className="ms-dash-chart-grid">
        <section>
          <h3>Доли сейчас · ECharts</h3>
          <p className="ms-muted">Последний {scope === 'week' ? 'недельный' : 'месячный'} столбец.</p>
          <div className="ms-dash-chart">
            <EChart option={pieOption} />
          </div>
        </section>
        <section>
          <h3>Тепловая карта · Plotly</h3>
          <p className="ms-muted">Канал × период. Hover — точное значение {metricLabel.toLowerCase()}.</p>
          <div className="ms-dash-chart">
            <PlotlyHeatmap title={metricLabel} x={heat.x} y={heat.y} z={heat.z} />
          </div>
        </section>
      </div>
    </div>
  )
}
