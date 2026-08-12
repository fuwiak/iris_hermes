import { describe, expect, it } from 'vitest'

import {
  applyCalendarDayClick,
  formatRuRange,
  parseIsoDate,
  toIsoDate
} from './event-calendar'

describe('event-calendar date helpers', () => {
  it('toIsoDate / parseIsoDate round-trip local calendar days', () => {
    const d = new Date(2026, 7, 15) // Aug 15 local
    expect(toIsoDate(d)).toBe('2026-08-15')
    const back = parseIsoDate('2026-08-15')
    expect(back?.getFullYear()).toBe(2026)
    expect(back?.getMonth()).toBe(7)
    expect(back?.getDate()).toBe(15)
  })

  it('formatRuRange shows single day and ranges', () => {
    expect(formatRuRange('2026-08-15', '2026-08-15')).toMatch(/15/)
    expect(formatRuRange('2026-08-10', '2026-08-20')).toMatch(/—/)
    expect(formatRuRange(null, null)).toBe('')
  })
})

describe('applyCalendarDayClick', () => {
  it('first click = single day; second click = range; third = new single', () => {
    let state = { anchor: null as string | null, dateFrom: null as string | null, dateTo: null as string | null }

    state = applyCalendarDayClick('2026-08-12', state)
    expect(state).toEqual({
      anchor: '2026-08-12',
      dateFrom: '2026-08-12',
      dateTo: '2026-08-12'
    })

    state = applyCalendarDayClick('2026-08-20', state)
    expect(state).toEqual({
      anchor: null,
      dateFrom: '2026-08-12',
      dateTo: '2026-08-20'
    })

    // New selection after a completed range
    state = applyCalendarDayClick('2026-08-05', state)
    expect(state).toEqual({
      anchor: '2026-08-05',
      dateFrom: '2026-08-05',
      dateTo: '2026-08-05'
    })
  })

  it('normalizes reverse click order', () => {
    let state = applyCalendarDayClick('2026-08-20', {
      anchor: null,
      dateFrom: null,
      dateTo: null
    })
    state = applyCalendarDayClick('2026-08-10', state)
    expect(state.dateFrom).toBe('2026-08-10')
    expect(state.dateTo).toBe('2026-08-20')
  })
})
