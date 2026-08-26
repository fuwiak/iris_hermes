import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { COMPOSER_SCREEN, INTRO_SCREEN } from '@/app/hermes-one-ui.fixture'
import { en } from '@/i18n/en'

const requestComposerInsert = vi.fn()
const requestComposerFocus = vi.fn()

vi.mock('@/app/chat/composer/focus', () => ({
  requestComposerFocus: (target: string) => requestComposerFocus(target),
  requestComposerInsert: (text: string, options: unknown) => requestComposerInsert(text, options)
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderIntro() {
  const { Intro } = await import('./intro')

  return render(<Intro />)
}

describe('Iris AI intro — English catalog matches the ii-assistent mock', () => {
  it('keeps the headline and subtitle verbatim', () => {
    expect(en.composer.hermesOneIntro.title).toBe(INTRO_SCREEN.title)
    expect(en.composer.hermesOneIntro.subtitle).toBe(INTRO_SCREEN.subtitle)
  })

  it('keeps example prompts verbatim', () => {
    const i = en.composer.hermesOneIntro

    expect([i.exMargin, i.exWriteoffs, i.exCall, i.exCompare, i.exAds]).toEqual([...INTRO_SCREEN.suggestions])
  })

  it('still offers the mock composer placeholder', () => {
    expect(en.composer.newSessionPlaceholders).toContain(COMPOSER_SCREEN.placeholder)
  })
})

describe('Iris AI intro — rendered screen matches the mock', () => {
  it('paints the Iris AI title', async () => {
    await renderIntro()

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(INTRO_SCREEN.title)
  })

  it('paints the sample question and agent name', async () => {
    await renderIntro()

    expect(screen.getByText(INTRO_SCREEN.sampleQuestion)).toBeTruthy()
    expect(screen.getByText(INTRO_SCREEN.agentName)).toBeTruthy()
  })

  it('paints right-rail sections', async () => {
    await renderIntro()

    expect(screen.getByText(INTRO_SCREEN.examplesTitle)).toBeTruthy()
    expect(screen.getByText(INTRO_SCREEN.tipTitle)).toBeTruthy()
    expect(screen.getByText(INTRO_SCREEN.sourcesTitle)).toBeTruthy()
  })

  it('paints example prompts as real buttons in mock order', async () => {
    const { container } = await renderIntro()
    const chips = [...container.querySelectorAll('.iris-ai-prompt-btn:not(.iris-ai-prompt-btn-sm)')]

    expect(chips.map(chip => chip.textContent?.trim())).toEqual([...INTRO_SCREEN.suggestions])
  })

  it('paints right-rail example buttons too', async () => {
    const { container } = await renderIntro()
    const chips = [...container.querySelectorAll('.iris-ai-ex-item')]

    expect(chips.map(chip => chip.textContent?.trim())).toEqual([...INTRO_SCREEN.suggestions])
  })

  it('sends an example prompt to the main composer', async () => {
    const { container } = await renderIntro()
    const first = container.querySelector('.iris-ai-prompt-btn:not(.iris-ai-prompt-btn-sm)')

    expect(first).not.toBeNull()
    fireEvent.click(first!)

    expect(requestComposerInsert).toHaveBeenCalledWith(en.composer.hermesOneIntro.exMarginPrompt, {
      mode: 'block',
      target: 'main'
    })
    expect(requestComposerFocus).toHaveBeenCalledWith('main')
  })
})

describe('Iris AI intro — styles ship with the desktop app', () => {
  const css = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')

  it.each(['[data-iris-ai-intro]', '.iris-ai-intro', '.iris-ai-ex-item', '.iris-ai-chart'])(
    'styles.css defines %s',
    selector => {
      expect(css).toContain(selector)
    }
  )
})
