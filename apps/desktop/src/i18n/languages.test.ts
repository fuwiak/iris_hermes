import { describe, expect, it } from 'vitest'

import {
  DEFAULT_LOCALE,
  isLocale,
  isSupportedLocaleValue,
  localeConfigValue,
  normalizeLocale,
  PRODUCT_DEFAULT_LOCALE
} from './languages'

describe('desktop i18n languages', () => {
  it('keeps Iris product default Russian while Vitest harness forces English', () => {
    expect(PRODUCT_DEFAULT_LOCALE).toBe('ru')
    // vitest.config sets HERMES_UI_TEST_LOCALE=en for UI role/name queries.
    expect(DEFAULT_LOCALE).toBe('en')
  })

  it('normalizes supported locale aliases', () => {
    expect(normalizeLocale('en')).toBe('en')
    expect(normalizeLocale('EN-US')).toBe('en')
    expect(normalizeLocale('ru')).toBe('ru')
    expect(normalizeLocale('ru-RU')).toBe('ru')
    expect(normalizeLocale(' Russian ')).toBe('ru')
    expect(normalizeLocale('русский')).toBe('ru')
  })

  it('falls back to DEFAULT_LOCALE for empty or unsupported values', () => {
    expect(normalizeLocale(null)).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('de')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('zh')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('ja')).toBe(DEFAULT_LOCALE)
  })

  it('distinguishes exact locale ids from supported config aliases', () => {
    expect(isSupportedLocaleValue('ru-RU')).toBe(true)
    expect(isSupportedLocaleValue('zh-CN')).toBe(false)
    expect(isSupportedLocaleValue('ja-JP')).toBe(false)
    expect(isSupportedLocaleValue('de')).toBe(false)
    expect(isLocale('ru-RU')).toBe(false)
    expect(isLocale('ru')).toBe(true)
    expect(isLocale('en')).toBe(true)
    expect(isLocale('zh')).toBe(false)
  })

  it('returns the persisted config value for supported locales', () => {
    expect(localeConfigValue('en')).toBe('en')
    expect(localeConfigValue('ru')).toBe('ru')
  })
})
