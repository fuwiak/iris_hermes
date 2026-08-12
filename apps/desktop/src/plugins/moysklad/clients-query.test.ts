import { describe, expect, it } from 'vitest'

import {
  clientSalesChannelTokens,
  digitsPhone,
  filterClientRowsByAudience,
  filterClientRowsByQuery,
  forEachRowProgressive,
  isBenignRequestAbort,
  normalizeGroupKey,
  pickLocalClientsSeed,
  rowMatchesClientQuery,
  rowMatchesGroupFilter,
  rowMatchesSalesChannelColumnFilter
} from './clients-query'

describe('clientSalesChannelTokens', () => {
  it('lists every channel, not only the joined display string', () => {
    const row = {
      channels: ['Витрина', 'Telegram'],
      channel: 'Витрина, Telegram'
    }
    expect(clientSalesChannelTokens(row)).toEqual(['Витрина', 'Telegram'])
  })

  it('filters column by a single channel token', () => {
    const row = {
      channels: ['Витрина', 'Ozon'],
      channel: 'Витрина, Ozon'
    }
    expect(
      rowMatchesSalesChannelColumnFilter(row, '', ['Витрина'], '(пусто)')
    ).toBe(true)
    expect(
      rowMatchesSalesChannelColumnFilter(row, '', ['Ozon'], '(пусто)')
    ).toBe(true)
    expect(
      rowMatchesSalesChannelColumnFilter(row, '', ['Telegram'], '(пусто)')
    ).toBe(false)
  })
})

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

  it('narrows by salesFilter direct vs marketplace', () => {
    const rows = [
      { id: '1', name: 'A', sales_type: 'Прямые', audience: { direct: true, marketplace: false } },
      { id: '2', name: 'B', sales_type: 'Маркетплейс', audience: { direct: false, marketplace: true } },
      { id: '3', name: 'C', sales_type: 'Прямые + Маркетплейс', audience: { direct: true, marketplace: true } }
    ]
    expect(
      filterClientRowsByAudience(rows, { salesFilter: 'direct' }).map(r => r.id)
    ).toEqual(['1'])
    expect(
      filterClientRowsByAudience(rows, { salesFilter: 'marketplace' }).map(r => r.id)
    ).toEqual(['2', '3'])
  })

  it('applies phone / telegram / vip extras locally', () => {
    const rows = [
      { id: '1', name: 'A', phone: '+79001112233', tg_nick: '', tags: [] },
      { id: '2', name: 'B', phone: '', tg_nick: '@bob', tags: ['VIP'] },
      { id: '3', name: 'C', phone: '89005556677', tg_nick: '@c', tags: [] }
    ]
    expect(
      filterClientRowsByAudience(rows, { requirePhone: true }).map(r => r.id)
    ).toEqual(['1', '3'])
    expect(
      filterClientRowsByAudience(rows, { requireTelegram: true }).map(r => r.id)
    ).toEqual(['2', '3'])
    expect(filterClientRowsByAudience(rows, { vipOnly: true }).map(r => r.id)).toEqual([
      '2'
    ])
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
  it('paints rows and stops when cancelled', async () => {
    const seen: string[] = []
    const n = await forEachRowProgressive(
      [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      row => {
        seen.push(String(row.id))
      },
      {
        delayMs: 0,
        chunkSize: 1,
        isCancelled: () => seen.length >= 2
      }
    )
    expect(seen).toEqual(['a', 'b'])
    expect(n).toBe(2)
  })

  it('paints the whole page in one chunk by default', async () => {
    const seen: string[] = []
    const n = await forEachRowProgressive([{ id: 'a' }, { id: 'b' }, { id: 'c' }], row => {
      seen.push(String(row.id))
    })
    expect(seen).toEqual(['a', 'b', 'c'])
    expect(n).toBe(3)
  })
})

describe('digitsPhone', () => {
  it('normalizes leading 8 to 7', () => {
    expect(digitsPhone('8 (900) 111-22-33')).toBe('79001112233')
  })
})
