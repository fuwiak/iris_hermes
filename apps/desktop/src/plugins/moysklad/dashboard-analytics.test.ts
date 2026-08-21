import { describe, expect, test } from 'vitest'

import { formatPct } from './dashboard-analytics'
import {
  linePoints,
  nearestPeriodIndex,
  niceMax,
  stackColumns,
  xAt
} from './dashboard-chart-model'
import { filterChannels, sortChannels, sortDayRows } from './dashboard-table-ops'

describe('formatPct (Excel growth =(new/old)-1)', () => {
  test('renders signed percent', () => {
    expect(formatPct(0.81)).toBe('+81%')
    expect(formatPct(-0.25)).toBe('-25%')
    expect(formatPct(null)).toBe('')
    expect(formatPct(0)).toBe('0%')
  })
})

describe('chart geometry', () => {
  test('nearest period maps cursor to column', () => {
    expect(nearestPeriodIndex(52, 720, 4)).toBe(0)
    expect(nearestPeriodIndex(720 - 16, 720, 4)).toBe(3)
  })

  test('line path has one point per period', () => {
    const pts = linePoints([0, 50, 100], 100, 720, 248)
    expect(pts.split(' ')).toHaveLength(3)
    expect(xAt(0, 3, 720)).toBeLessThan(xAt(2, 3, 720))
  })

  test('stack columns accumulate per period', () => {
    const cols = stackColumns(
      [
        { key: 'a', values: [10, 0] },
        { key: 'b', values: [5, 20] }
      ],
      2
    )
    expect(cols[0].map(s => s.key)).toEqual(['a', 'b'])
    expect(cols[0][1].y1).toBe(15)
    expect(cols[1][0]).toMatchObject({ key: 'b', value: 20, y0: 0, y1: 20 })
    expect(niceMax(8500)).toBe(10000)
  })
})

describe('dashboard table filter/sort', () => {
  const channels = [
    { key: 'flowwow', label: 'Флау вау', turnover: [10, 80], orders: [1, 4] },
    { key: 'direct', label: 'Прямые продажи', turnover: [50, 20], orders: [2, 1] }
  ]

  test('filter keeps matching channel label', () => {
    expect(filterChannels(channels, 'флау').map(c => c.key)).toEqual(['flowwow'])
  })

  test('sort by last-period turnover desc', () => {
    expect(sortChannels(channels, 'turnover', 'desc', 1).map(c => c.key)).toEqual(['flowwow', 'direct'])
    expect(sortChannels(channels, 'label', 'asc', 1)[0].key).toBe('direct')
  })

  test('sort day rows by turnover', () => {
    const rows = [
      { id: '2026-08-01', label: '1 авг', channels: { flowwow: { orders: 1, turnover: 100 } } },
      { id: '2026-08-02', label: '2 авг', channels: { flowwow: { orders: 2, turnover: 500 } } }
    ]
    expect(sortDayRows(rows, 'turnover', 'desc', ['flowwow'])[0].id).toBe('2026-08-02')
    expect(sortDayRows(rows, 'date', 'asc', ['flowwow'])[0].id).toBe('2026-08-01')
  })
})

