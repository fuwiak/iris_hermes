import { describe, expect, it } from 'vitest'
import {
  tgActiveCellTitle,
  tgActiveStatusWord,
  tgStatusFilterParam
} from './tg-active-label'

describe('tgActiveStatusWord', () => {
  it('only two statuses — never НЕ ПРОВЕРЕН', () => {
    expect(tgActiveStatusWord({ tg_active: true })).toBe('АКТИВНЫЙ')
    expect(tgActiveStatusWord({ tg_active: false })).toBe('НЕАКТИВНЫЙ')
    expect(tgActiveStatusWord({ tg_active: null })).toBe('НЕАКТИВНЫЙ')
    expect(tgActiveStatusWord({})).toBe('НЕАКТИВНЫЙ')
  })
})

describe('tgActiveCellTitle', () => {
  it('surfaces IRbots detail for inactive numbers', () => {
    expect(
      tgActiveCellTitle({
        tg_active: false,
        tg_active_detail: 'неактивный (не зарегистрирован)'
      })
    ).toBe('неактивный (не зарегистрирован)')
  })
})

describe('tgStatusFilterParam', () => {
  it('maps checkbox / query to API tg_status', () => {
    expect(tgStatusFilterParam({ selected: ['НЕАКТИВНЫЙ'] })).toBe('inactive')
    expect(tgStatusFilterParam({ selected: ['АКТИВНЫЙ'] })).toBe('active')
    expect(tgStatusFilterParam({ selected: ['АКТИВНЫЙ', 'НЕАКТИВНЫЙ'] })).toBe('')
    expect(tgStatusFilterParam({ query: 'неактивный' })).toBe('inactive')
    expect(tgStatusFilterParam({ query: 'активный' })).toBe('active')
  })
})
