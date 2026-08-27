import { describe, expect, it } from 'vitest'
import { tgActiveCellTitle, tgActiveStatusWord } from './tg-active-label'

describe('tgActiveStatusWord', () => {
  it('matches irbots_clients_status.txt status= words', () => {
    expect(tgActiveStatusWord({ tg_active: true })).toBe('АКТИВНЫЙ')
    expect(tgActiveStatusWord({ tg_active: false })).toBe('НЕАКТИВНЫЙ')
    expect(tgActiveStatusWord({ tg_active: null })).toBe('НЕ ПРОВЕРЕН')
    expect(tgActiveStatusWord({})).toBe('НЕ ПРОВЕРЕН')
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

  it('does not claim privacy hide for definitive inactive', () => {
    const title = tgActiveCellTitle({ tg_active: false })
    expect(title.toLowerCase()).not.toContain('приват')
  })
})
