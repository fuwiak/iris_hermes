import { describe, expect, it } from 'vitest'

import {
  MASS_SEND_CHUNK,
  chunkIds,
  massSendConfirmText,
  massSendProgressLabel,
  massSendStepHint,
  mergeUniqueIds,
  needsMassSendConfirm,
  resolveMassSendStep
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
})
