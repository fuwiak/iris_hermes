import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { fieldCopyForSchemaKey } from '@/app/settings/field-copy'

import { TRANSLATIONS } from './catalog'
import { en } from './en'
import { setRuntimeI18nLocale, translateNow } from './runtime'

describe('desktop i18n runtime translator', () => {
  beforeEach(() => {
    setRuntimeI18nLocale('en')
  })

  afterEach(() => {
    setRuntimeI18nLocale('en')
  })

  it('translates string paths for the active runtime locale', () => {
    setRuntimeI18nLocale('en')

    expect(translateNow('boot.ready')).toBe('Hermes Desktop is ready')
    expect(translateNow('notifications.voice.noSpeechDetected')).toBe('No speech detected')
    expect(translateNow('composer.lookupNoMatches')).toBe('No matches.')
    expect(translateNow('assistant.tool.statusRecovered')).toBe('Recovered')
  })

  it('passes arguments to function translations', () => {
    expect(translateNow('notifications.updateReadyMessage', 2)).toBe('2 new changes available.')
  })

  it('serves the Russian catalog when the runtime locale is ru', () => {
    setRuntimeI18nLocale('ru')
    expect(translateNow('common.save')).toBe('Сохранить')
    expect(translateNow('settings.appearance.title')).toBe(TRANSLATIONS.ru.settings.appearance.title)
    expect(translateNow('settings.nav.providers')).toBe(TRANSLATIONS.ru.settings.nav.providers)
  })

  it('keeps translated settings field copy addressable from schema keys', () => {
    const field = ['display', 'show_reasoning'].join('.')

    expect(fieldCopyForSchemaKey(en.settings.fieldLabels, field)).toBe('Reasoning Blocks')
    expect(fieldCopyForSchemaKey(en.settings.fieldDescriptions, field)).toBe(
      'Show reasoning sections when the backend provides them.'
    )
  })

  it('falls back to English when the active locale cannot resolve a key', () => {
    const boot = TRANSLATIONS.ru.boot as { ready?: string }
    const originalReady = boot.ready

    try {
      boot.ready = undefined
      setRuntimeI18nLocale('ru')

      expect(translateNow('boot.ready')).toBe('Hermes Desktop is ready')
    } finally {
      boot.ready = originalReady
    }
  })

  it('returns the key when no locale can resolve a path', () => {
    setRuntimeI18nLocale('ru')

    expect(translateNow('missing.path')).toBe('missing.path')
  })
})
