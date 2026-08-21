import { describe, expect, test } from 'vitest'

import { formatPct } from './dashboard-analytics'
import {
  linePoints,
  nearestPeriodIndex,
  niceMax,
  stackColumns,
  xAt
} from './dashboard-chart-model'

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
