import { describe, expect, it } from 'vitest'

import {
  isSalesFilterId,
  planAudienceChipClick,
  salesFilterTabsDisabled,
  seedFactsFromAudienceRow
} from './audience-pick'

describe('planAudienceChipClick', () => {
  it('single mode focuses compose and replaces selection', () => {
    const r = planAudienceChipClick({
      pickMode: 'single',
      rowId: 'cp-dmitry',
      rowName: 'Дмитрий Врублевский основной',
      rowPhone: '+79299770334',
      selectedIds: ['other']
    })
    expect(r.ok).toBe(true)
    expect(r.focusId).toBe('cp-dmitry')
    expect(r.focusName).toBe('Дмитрий Врублевский основной')
    expect(r.selectedIds).toEqual(['cp-dmitry'])
    expect(r.channel).toBe('whatsapp')
  })

  it('multi mode still focuses compose (does not no-op)', () => {
    const r = planAudienceChipClick({
      pickMode: 'multi',
      rowId: 'cp-dmitry',
      rowName: 'Дмитрий Врублевский основной',
      rowPhone: '+79299770334',
      rowTgNick: '',
      selectedIds: []
    })
    expect(r.ok).toBe(true)
    expect(r.focusId).toBe('cp-dmitry')
    expect(r.selectedIds).toEqual(['cp-dmitry'])
  })

  it('multi mode accumulates without dropping focus', () => {
    const r = planAudienceChipClick({
      pickMode: 'multi',
      rowId: 'cp-2',
      rowName: 'Второй',
      rowTgNick: '@two',
      selectedIds: ['cp-1']
    })
    expect(r.ok).toBe(true)
    expect(r.focusId).toBe('cp-2')
    expect(r.selectedIds).toEqual(['cp-1', 'cp-2'])
    expect(r.channel).toBe('telegram')
  })

  it('re-click in multi keeps id selected and still focuses', () => {
    const r = planAudienceChipClick({
      pickMode: 'multi',
      rowId: 'cp-dmitry',
      rowName: 'Дмитрий',
      selectedIds: ['cp-dmitry']
    })
    expect(r.ok).toBe(true)
    expect(r.focusId).toBe('cp-dmitry')
    expect(r.selectedIds).toEqual(['cp-dmitry'])
  })

  it('missing id is an explicit failure (not silent)', () => {
    const r = planAudienceChipClick({
      pickMode: 'multi',
      rowId: '',
      rowName: 'Дмитрий Врублевский основной',
      selectedIds: []
    })
    expect(r.ok).toBe(false)
    expect(r.reason).toBe('missing_id')
    expect(r.focusId).toBe('')
  })

  it('chip click always requests immediate facts load', () => {
    const r = planAudienceChipClick({
      pickMode: 'multi',
      rowId: 'cp-dmitry',
      rowName: 'Дмитрий',
      selectedIds: []
    })
    expect(r.loadFacts).toBe(true)
  })
})

describe('seedFactsFromAudienceRow', () => {
  it('seeds Facts panel fields from chip row without waiting for AI', () => {
    const facts = seedFactsFromAudienceRow({
      id: 'cp-dmitry',
      name: 'Дмитрий Врублевский основной',
      phone: '+79299770334',
      order_count: 6,
      avg_check: 4500,
      channels: ['whatsapp'],
      sales_type: 'direct',
      last_order_at: '2026-08-01'
    })
    expect(facts.client_id).toBe('cp-dmitry')
    expect(facts.name).toBe('Дмитрий Врублевский основной')
    expect(facts.order_count).toBe(6)
    expect(facts.avg_check).toBe(4500)
    expect(facts.channels).toEqual(['whatsapp'])
    expect(facts.last_order?.date).toBe('2026-08-01')
  })
})

describe('salesFilterTabsDisabled', () => {
  it('tabs stay enabled while reloading when counts already known', () => {
    expect(salesFilterTabsDisabled({ loading: true, hasCounts: true })).toBe(false)
  })

  it('tabs disabled only on first empty load', () => {
    expect(salesFilterTabsDisabled({ loading: true, hasCounts: false })).toBe(true)
    expect(salesFilterTabsDisabled({ loading: false, hasCounts: false })).toBe(false)
  })
})

describe('isSalesFilterId', () => {
  it('accepts all / marketplace / direct', () => {
    expect(isSalesFilterId('all')).toBe(true)
    expect(isSalesFilterId('marketplace')).toBe(true)
    expect(isSalesFilterId('direct')).toBe(true)
    expect(isSalesFilterId('other')).toBe(false)
  })
})
