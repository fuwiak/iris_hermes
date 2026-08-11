import { describe, expect, it } from 'vitest'

import {
  digitsPhone,
  filterClientRowsByAudience,
  filterClientRowsByQuery,
  forEachRowProgressive,
  isBenignRequestAbort,
  normalizeGroupKey,
  pickLocalClientsSeed,
  rowMatchesClientQuery,
  rowMatchesGroupFilter
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

describe('rowMatchesGroupFilter', () => {
  it('narrows MS group chip to matching contacts', () => {
    const withGroup = {
      id: '1',
      name: 'A',
      ms_groups: 'букет от 10 000',
      tags: ['букет от 10 000']
    }
    const without = { id: '2', name: 'B', ms_groups: 'сайт', tags: ['сайт'] }
    expect(rowMatchesGroupFilter(withGroup, 'букет от 10 000', 'ms')).toBe(true)
    expect(rowMatchesGroupFilter(without, 'букет от 10 000', 'ms')).toBe(false)
    expect(normalizeGroupKey('букет от 10000')).toBe('букет от 10 000')
  })

  it('scopes AI groups separately from MS', () => {
    const row = {
      id: '1',
      ms_groups: '8 марта',
      tags: ['8 марта'],
      ai_groups: ['премиум']
    }
    expect(rowMatchesGroupFilter(row, '8 марта', 'ms')).toBe(true)
    expect(rowMatchesGroupFilter(row, '8 марта', 'ai')).toBe(false)
    expect(rowMatchesGroupFilter(row, 'премиум', 'ai')).toBe(true)
  })
})

describe('filterClientRowsByAudience', () => {
  it('applies group chip then search', () => {
    const rows = [
      { id: '1', name: 'Павел', ms_groups: 'букет от 10 000', tags: ['букет от 10 000'] },
      { id: '2', name: 'Павел', ms_groups: 'сайт', tags: ['сайт'] },
      { id: '3', name: 'Оля', ms_groups: 'букет от 10 000', tags: ['букет от 10 000'] }
    ]
    expect(
      filterClientRowsByAudience(rows, {
        group: 'букет от 10 000',
        groupSource: 'ms',
        q: 'Павел'
      }).map(r => r.id)
    ).toEqual(['1'])
  })
})

describe('pickLocalClientsSeed', () => {
  it('filters unfiltered cache when group chip has no exact hit', () => {
    const unfiltered = {
      clients: [
        { id: '1', name: 'A', ms_groups: 'букет от 10 000', tags: ['букет от 10 000'] },
        { id: '2', name: 'B', ms_groups: 'сайт', tags: ['сайт'] }
      ],
      q: '',
      group: ''
    }
    const seed = pickLocalClientsSeed({
      q: '',
      group: 'букет от 10 000',
      groupSource: 'ms',
      readExact: () => null,
      readBase: () => null,
      readUnfilteredBases: () => [unfiltered],
      filterRows: (s, q, group, groupSource) => {
        const clients = filterClientRowsByAudience(s.clients, { q, group, groupSource })
        if (!clients.length) return null
        return { ...s, group, clients, matched_total: clients.length }
      }
    })
    expect(seed?.clients.map(c => c.id)).toEqual(['1'])
  })
})

describe('forEachRowProgressive', () => {
  it('paints one row at a time and stops when cancelled', async () => {
    const seen: string[] = []
    const n = await forEachRowProgressive(
      [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      row => {
        seen.push(String(row.id))
      },
      {
        delayMs: 0,
        isCancelled: () => seen.length >= 2
      }
    )
    expect(seen).toEqual(['a', 'b'])
    expect(n).toBe(2)
  })
})

describe('digitsPhone', () => {
  it('normalizes leading 8 to 7', () => {
    expect(digitsPhone('8 (900) 111-22-33')).toBe('79001112233')
  })
})
