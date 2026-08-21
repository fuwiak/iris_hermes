import { describe, expect, test } from 'vitest'

import { formatPct } from './dashboard-analytics'

describe('formatPct (Excel growth =(new/old)-1)', () => {
  test('renders signed percent', () => {
    expect(formatPct(0.81)).toBe('+81%')
    expect(formatPct(-0.25)).toBe('-25%')
    expect(formatPct(null)).toBe('')
    expect(formatPct(0)).toBe('0%')
  })
})
