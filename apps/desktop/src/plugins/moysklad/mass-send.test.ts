import { describe, expect, it } from 'vitest'

import {
  chunkIds,
  isMassJobActive,
  MASS_SEND_CHUNK,
  massJobPercent,
  massRecipientDisplay,
  massRowStatusLabel,
  massSendConfirmText,
  massSendProgressLabel,
  massSendStepHint,
  mergeUniqueIds,
  needsMassSendConfirm,
  newestFirstByTs,
  overlayMassRows,
  recencyMs,
  resolveMassSendStep,
  terminalPrefixLength
} from './mass-send'

describe('mass-send helpers', () => {
  it('chunks ids and drops blanks / dupes', () => {
    const ids = ['a', 'b', 'a', '', 'c', '  ', 'd']
    const chunks = chunkIds(ids, 2)
    expect(chunks).toEqual([
      ['a', 'b'],
      ['c', 'd']
    ])
    expect(chunkIds(ids).every(c => c.length <= MASS_SEND_CHUNK)).toBe(true)
  })

  it('progress label covers mid-batch and done', () => {
    expect(massSendProgressLabel(0, 120, 50)).toBe('Отправка 1–50 из 120…')
    expect(massSendProgressLabel(50, 120, 50)).toBe('Отправка 51–100 из 120…')
    expect(massSendProgressLabel(100, 120, 50)).toBe('Отправка 101–120 из 120…')
    expect(massSendProgressLabel(120, 120, 50)).toBe('Готово: 120/120')
    expect(massSendProgressLabel(0, 0, 50)).toBe('Нет получателей')
  })

  it('confirm gate and copy mention chunk size + replies', () => {
    expect(needsMassSendConfirm(19)).toBe(false)
    expect(needsMassSendConfirm(20)).toBe(true)
    expect(massSendConfirmText(87)).toContain('87')
    expect(massSendConfirmText(87)).toContain(String(MASS_SEND_CHUNK))
    expect(massSendConfirmText(87)).toContain('Собрать ответы')
  })

  it('mergeUniqueIds preserves order', () => {
    expect(mergeUniqueIds(['a', 'b'], ['b', 'c', 'a', ''])).toEqual(['a', 'b', 'c'])
  })

  it('resolveMassSendStep follows audience → text → send → replies', () => {
    expect(resolveMassSendStep({ selectedCount: 0, hasDraft: false, sentCount: 0 })).toBe(1)
    expect(resolveMassSendStep({ selectedCount: 10, hasDraft: false, sentCount: 0 })).toBe(2)
    expect(resolveMassSendStep({ selectedCount: 10, hasDraft: true, sentCount: 0 })).toBe(3)
    expect(resolveMassSendStep({ selectedCount: 10, hasDraft: true, sentCount: 10 })).toBe(4)
  })

  it('step hints tell operator the next click', () => {
    expect(massSendStepHint(1, { audience: 90, selectedCount: 0, chunk: 50 })).toContain(
      'Выбрать всех'
    )
    expect(massSendStepHint(3, { audience: 90, selectedCount: 90, chunk: 50 })).toContain('90')
  })

  it('terminalPrefixLength counts only the finalized head', () => {
    expect(terminalPrefixLength([])).toBe(0)
    expect(
      terminalPrefixLength([
        { status: 'ok' },
        { status: 'failed' },
        { status: 'sending' },
        { status: 'ok' }
      ])
    ).toBe(2)
    expect(terminalPrefixLength([{ status: 'pending' }])).toBe(0)
  })

  it('overlayMassRows replaces the polled window and keeps the tail', () => {
    const prev = [{ status: 'ok' }, { status: 'sending' }, { status: 'pending' }]
    const next = overlayMassRows(prev, [{ status: 'ok' }, { status: 'failed' }], 1)
    expect(next.map(r => r.status)).toEqual(['ok', 'ok', 'failed'])

    const grown = overlayMassRows([], [{ status: 'ok' }], 0)
    expect(grown).toHaveLength(1)
    expect(overlayMassRows(prev, [], 1)).toBe(prev)
  })

  it('job/row status helpers cover the lifecycle', () => {
    expect(isMassJobActive('running')).toBe(true)
    expect(isMassJobActive('queued')).toBe(true)
    expect(isMassJobActive('done')).toBe(false)
    expect(massJobPercent(50, 200)).toBe(25)
    expect(massJobPercent(3, 0)).toBe(0)
    expect(massRowStatusLabel('ok')).toContain('отправлено')
    expect(massRowStatusLabel('failed')).toContain('ошибка')
    expect(massRecipientDisplay({ client_name: 'Петр' })).toBe('Петр')
    expect(massRecipientDisplay({ tg_nick: '@petr' })).toBe('@petr')
    expect(massRecipientDisplay({ client_id: 'x1' })).toBe('x1')
    expect(massRecipientDisplay({})).toBe('—')
  })

  it('newestFirstByTs puts latest timestamps first', () => {
    expect(recencyMs('2026-08-15T12:00:00Z')).toBeGreaterThan(recencyMs('2020-01-01T00:00:00Z'))
    const rows = newestFirstByTs(
      [
        { id: 'old', ts: '2020-01-01T00:00:00Z' },
        { id: 'new', ts: '2026-08-15T12:00:00Z' },
        { id: 'mid', ts: '2024-06-01T08:00:00Z' }
      ],
      r => r.ts
    )
    expect(rows.map(r => r.id)).toEqual(['new', 'mid', 'old'])
  })
})
