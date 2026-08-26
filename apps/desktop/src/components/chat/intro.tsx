/**
 * New-chat empty state — 1:1 with design/mocks/ii-assistent.html (Iris AI).
 * Suggestion buttons insert prompts into the main composer.
 */

import { requestComposerFocus, requestComposerInsert } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'

export type IntroProps = {
  personality?: string
  seed?: number
}

type Example = { label: string; prompt: string }

function chooseSuggestion(prompt: string) {
  requestComposerInsert(prompt, { mode: 'block', target: 'main' })
  requestComposerFocus('main')
}

export function Intro(_props: IntroProps) {
  const { t } = useI18n()
  const i = t.composer.hermesOneIntro

  const examples: Example[] = [
    { label: i.exMargin, prompt: i.exMarginPrompt },
    { label: i.exWriteoffs, prompt: i.exWriteoffsPrompt },
    { label: i.exCall, prompt: i.exCallPrompt },
    { label: i.exCompare, prompt: i.exComparePrompt },
    { label: i.exAds, prompt: i.exAdsPrompt }
  ]

  const channels = [
    { name: i.chSite, pct: '38%', money: '824 000 ₽', tone: 'g', width: '92%', icon: '⌂' },
    { name: i.chYandex, pct: '31%', money: '512 000 ₽', tone: 'r', width: '74%', icon: 'Я' },
    { name: i.chFlowwow, pct: '24%', money: '498 000 ₽', tone: 'o', width: '72%', icon: 'F' },
    { name: i.chOffline, pct: '35%', money: '402 000 ₽', tone: 'p', width: '58%', icon: '◎' },
    { name: i.chSocial, pct: '22%', money: '286 000 ₽', tone: 'b', width: '42%', icon: '💬' }
  ]

  const sources = [i.srcOrders, i.srcCatalog, i.srcFinance, i.srcWarehouse, i.srcMarketing, i.srcReviews]

  return (
    <div className="iris-ai-intro" data-hermes-one-intro="" data-iris-ai-intro="" data-slot="aui_intro">
      <div className="iris-ai-main">
        <header className="iris-ai-topbar">
          <div className="iris-ai-title">
            <span aria-hidden="true" className="iris-ai-spark">
              <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z" />
              </svg>
            </span>
            <h1>{i.title}</h1>
          </div>
          <div className="iris-ai-topbar-actions">
            <button aria-label={i.notifications} className="iris-ai-icon-btn" type="button">
              <svg fill="none" height="18" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
                <path d="M15 18a3 3 0 0 1-6 0" />
                <path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5 1.5 5H4.5S6 14 6 10z" />
              </svg>
            </button>
            <button className="iris-ai-btn-history" type="button">
              <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                <circle cx="12" cy="12" r="8" />
                <path d="M12 8v4l3 2" />
              </svg>
              {i.history}
            </button>
          </div>
        </header>

        <div className="iris-ai-chat">
          <div className="iris-ai-msg-user">
            <div className="iris-ai-bubble-user">{i.sampleQuestion}</div>
            <div className="iris-ai-msg-meta">{i.sampleUserTime}</div>
          </div>

          <div className="iris-ai-msg-ai">
            <div className="iris-ai-ai-head">
              <div aria-hidden="true" className="iris-ai-avatar">
                <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
                  <path d="M12 3c2 4 2 6 0 9-2-3-2-5 0-9z" />
                  <path d="M12 12v9" />
                </svg>
              </div>
              <span className="iris-ai-name">{i.agentName}</span>
              <span className="iris-ai-time">{i.sampleAiTime}</span>
            </div>
            <p className="iris-ai-text">{i.sampleAnswer}</p>

            <div className="iris-ai-insight iris-ai-insight-ok">
              <div className="iris-ai-insight-icon">✓</div>
              <div>
                <div className="iris-ai-insight-title">{i.insightTitle}</div>
                <div className="iris-ai-insight-sub">{i.insightSub}</div>
              </div>
            </div>

            <div className="iris-ai-insight iris-ai-insight-rec">
              <div className="iris-ai-insight-icon">
                <svg fill="none" height="14" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="14">
                  <path d="M9 18h6" />
                  <path d="M10 21h4" />
                  <path d="M12 3a6 6 0 0 1 4 10c-.8.7-1.2 1.4-1.4 2.2H9.4C9.2 14.4 8.8 13.7 8 13a6 6 0 0 1 4-10z" />
                </svg>
              </div>
              <div>
                <div className="iris-ai-insight-title">{i.recTitle}</div>
                <div className="iris-ai-insight-sub">{i.recBody}</div>
              </div>
            </div>

            <div className="iris-ai-chart">
              <div className="iris-ai-chart-title">{i.chartTitle}</div>
              {channels.map(ch => (
                <div className="iris-ai-bar-row" key={ch.name}>
                  <div aria-hidden="true" className={`iris-ai-ch-icon iris-ai-ch-${ch.tone}`}>
                    {ch.icon}
                  </div>
                  <div className="iris-ai-bar-mid">
                    <div className="iris-ai-bar-label">
                      <span>{ch.name}</span>
                      <span>{ch.pct}</span>
                    </div>
                    <div className="iris-ai-bar-track">
                      <div className={`iris-ai-bar-fill iris-ai-bar-${ch.tone}`} style={{ width: ch.width }} />
                    </div>
                  </div>
                  <div className="iris-ai-bar-money">{ch.money}</div>
                </div>
              ))}
              <div className="iris-ai-chart-actions">
                <button className="iris-ai-btn-more" type="button">
                  {i.showMore}
                  <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                </button>
                <button aria-label={i.download} className="iris-ai-btn-dl" type="button">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <path d="M12 3v12" />
                    <path d="m7 10 5 5 5-5" />
                    <path d="M5 19h14" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <aside className="iris-ai-right">
        <section className="iris-ai-card">
          <h3>{i.examplesTitle}</h3>
          <div className="iris-ai-ex-list">
            {examples.map(ex => (
              <button
                className="iris-ai-ex-item"
                key={ex.label}
                onClick={() => chooseSuggestion(ex.prompt)}
                type="button"
              >
                <span className="iris-ai-ex-ico" aria-hidden="true">
                  <svg fill="none" height="12" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="12">
                    <path d="M4 6h16v11H8l-4 3V6z" />
                  </svg>
                </span>
                {ex.label}
              </button>
            ))}
          </div>
        </section>

        <section className="iris-ai-tip">
          <div className="iris-ai-tip-head">
            <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
              <path d="M9 18h6" />
              <path d="M10 21h4" />
              <path d="M12 3a6 6 0 0 1 4 10c-.8.7-1.2 1.4-1.4 2.2H9.4C9.2 14.4 8.8 13.7 8 13a6 6 0 0 1 4-10z" />
            </svg>
            {i.tipTitle}
          </div>
          <p>{i.tipBody}</p>
        </section>

        <section className="iris-ai-card">
          <h3>{i.sourcesTitle}</h3>
          <div className="iris-ai-src-list">
            {sources.map(src => (
              <div className="iris-ai-src-item" key={src}>
                {src}
                <span className="iris-ai-src-ok">✓</span>
              </div>
            ))}
          </div>
          <div className="iris-ai-src-footer">
            <span>{i.sourcesUpdated}</span>
            <button aria-label={i.refresh} type="button">
              <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
                <path d="M21 12a9 9 0 1 1-2.6-6.4" />
                <path d="M21 3v6h-6" />
              </svg>
            </button>
          </div>
        </section>
      </aside>
    </div>
  )
}
