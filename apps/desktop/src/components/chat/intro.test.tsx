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

describe('Hermes One intro — English catalog matches the reference screenshot', () => {
  it('keeps the headline and subtitle verbatim', () => {
    expect(en.composer.hermesOneIntro.title).toBe(INTRO_SCREEN.title)
    expect(en.composer.hermesOneIntro.subtitle).toBe(INTRO_SCREEN.subtitle)
  })

  it('keeps all six suggestion labels verbatim', () => {
    const i = en.composer.hermesOneIntro

    expect([i.searchWeb, i.reminder, i.emails, i.script, i.cron, i.data]).toEqual([...INTRO_SCREEN.suggestions])
  })

  it('still offers the screenshot composer placeholder', () => {
    expect(en.composer.newSessionPlaceholders).toContain(COMPOSER_SCREEN.placeholder)
  })
})

describe('Hermes One intro — rendered screen matches the reference screenshot', () => {
  it('paints the round HERMES ONE mark', async () => {
    const { container } = await renderIntro()
    const mark = container.querySelector('.hermes-one-mark')

    expect(mark).not.toBeNull()
    expect(mark?.querySelector('span')?.textContent).toBe(INTRO_SCREEN.mark[0])
    expect(mark?.querySelector('strong')?.textContent).toBe(INTRO_SCREEN.mark[1])
  })

  it('paints the headline and subtitle', async () => {
    await renderIntro()

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(INTRO_SCREEN.title)
    expect(screen.getByText(INTRO_SCREEN.subtitle)).toBeTruthy()
  })

  it('paints the six suggestion chips in screenshot order', async () => {
    const { container } = await renderIntro()
    const chips = [...container.querySelectorAll('.hermes-one-suggestions button')]

    expect(chips.map(chip => chip.textContent?.trim())).toEqual([...INTRO_SCREEN.suggestions])
  })

  it('sends a chip prompt to the main composer', async () => {
    await renderIntro()

    fireEvent.click(screen.getByRole('button', { name: INTRO_SCREEN.suggestions[0] }))

    expect(requestComposerInsert).toHaveBeenCalledWith(en.composer.hermesOneIntro.searchWebPrompt, {
      mode: 'block',
      target: 'main'
    })
    expect(requestComposerFocus).toHaveBeenCalledWith('main')
  })
})

/**
 * The intro is styled by plain class names, not Tailwind utilities, so jsdom
 * cannot prove it looks right. What it CAN prove is that the rules live in the
 * app's own sheet — they used to exist only in the dashboard's
 * `hermes-one-web.css`, which left the Electron intro completely unstyled.
 */
describe('Hermes One intro — styles ship with the desktop app', () => {
  // Vitest runs with the desktop workspace as cwd.
  const css = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')

  it.each(['[data-hermes-one-intro]', '.hermes-one-mark', '.hermes-one-suggestions button'])(
    'styles.css defines %s',
    selector => {
      expect(css).toContain(selector)
    }
  )

  it('draws the mark as a filled violet circle', () => {
    const block = css.slice(css.indexOf('.hermes-one-mark {'))

    expect(block).toContain('border-radius: 999px')
    expect(block).toContain('background: var(--theme-primary, #c084fc)')
    expect(block).not.toContain('background: #050505')
  })

  it('keeps suggestion chip text readable (cream ink on aubergine)', () => {
    const block = css.slice(css.indexOf('.hermes-one-suggestions button {'))

    expect(block).toContain('color: var(--ui-text-primary, #f4ede4)')
    expect(block).not.toContain('background: #1a1a1a')
  })
})
