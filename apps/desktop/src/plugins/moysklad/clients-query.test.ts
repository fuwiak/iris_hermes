import { describe, expect, it } from 'vitest'

import {
  digitsPhone,
  filterClientRowsByQuery,
  isBenignRequestAbort,
  pickLocalClientsSeed,
  rowMatchesClientQuery
} from './clients-query'

describe('isBenignRequestAbort', () => {
  it('treats Chromium abort string as soft cancel', () => {
    expect(isBenignRequestAbort(new DOMException('signal is aborted without reason', 'AbortError'))).toBe(
      true
    )
    expect(isBenignRequestAbort(new Error('signal is aborted without reason'))).toBe(true)
    expect(isBenignRequestAbort(new Error('catalog unavailable'))).toBe(false)
  })
})

describe('rowMatchesClientQuery', () => {
  it('matches Павел by name substring', () => {
    expect(
      rowMatchesClientQuery({ name: 'Павел Иванов', phone: '+79001112233' }, 'Павел')
    ).toBe(true)
    expect(rowMatchesClientQuery({ name: 'Дмитрий', phone: '' }, 'Павел')).toBe(false)
  })

  it('matches phone with formatting noise', () => {
    expect(
      rowMatchesClientQuery({ name: 'X', phone: '+7 (900) 111-22-33' }, '79001112233')
    ).toBe(true)
  })
})

describe('filterClientRowsByQuery', () => {
  it('filters list without waiting for network', () => {
    const rows = [
      { id: '1', name: 'Павел' },
      { id: '2', name: 'Дмитрий' },
      { id: '3', name: 'Павелка' }
    ]
    expect(filterClientRowsByQuery(rows, 'Павел').map(r => r.id)).toEqual(['1', '3'])
  })
})

describe('pickLocalClientsSeed', () => {
  it('filters empty-q base cache when exact q miss', () => {
    const base = { clients: [{ id: '1', name: 'Павел' }, { id: '2', name: 'Оля' }], q: '' }
    const seed = pickLocalClientsSeed({
      q: 'Павел',
      readExact: () => null,
      readBase: () => base,
      readAllBase: () => null,
      filterRows: (s, q) => ({
        ...s,
        q,
        clients: filterClientRowsByQuery(s.clients, q)
      })
    })
    expect(seed?.clients.map(c => c.id)).toEqual(['1'])
  })
})

describe('digitsPhone', () => {
  it('normalizes leading 8 to 7', () => {
    expect(digitsPhone('8 (900) 111-22-33')).toBe('79001112233')
  })
})
