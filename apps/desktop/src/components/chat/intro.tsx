import { requestComposerFocus, requestComposerInsert } from '@/app/chat/composer/focus'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'

export type IntroProps = {
  personality?: string
  seed?: number
}

export function Intro(_props: IntroProps) {
  const { t } = useI18n()

  const suggestions = [
    { icon: 'search', label: t.composer.hermesOneIntro.searchWeb, prompt: t.composer.hermesOneIntro.searchWebPrompt },
    { icon: 'bell', label: t.composer.hermesOneIntro.reminder, prompt: t.composer.hermesOneIntro.reminderPrompt },
    { icon: 'mail', label: t.composer.hermesOneIntro.emails, prompt: t.composer.hermesOneIntro.emailsPrompt },
    { icon: 'code', label: t.composer.hermesOneIntro.script, prompt: t.composer.hermesOneIntro.scriptPrompt },
    { icon: 'clock', label: t.composer.hermesOneIntro.cron, prompt: t.composer.hermesOneIntro.cronPrompt },
    { icon: 'graph', label: t.composer.hermesOneIntro.data, prompt: t.composer.hermesOneIntro.dataPrompt }
  ]

  const chooseSuggestion = (prompt: string) => {
    requestComposerInsert(prompt, { mode: 'block', target: 'main' })
    requestComposerFocus('main')
  }

  return (
    <div
      className="flex w-full min-w-0 flex-col items-center justify-center px-4 py-6 text-center"
      data-hermes-one-intro=""
      data-slot="aui_intro"
    >
      <div aria-hidden="true" className="hermes-one-mark">
        <span>HERMES</span>
        <strong>ONE</strong>
      </div>
      <h1>{t.composer.hermesOneIntro.title}</h1>
      <p>{t.composer.hermesOneIntro.subtitle}</p>
      <div className="hermes-one-suggestions">
        {suggestions.map(suggestion => (
          <button key={suggestion.label} onClick={() => chooseSuggestion(suggestion.prompt)} type="button">
            <Codicon name={suggestion.icon} size="1.1rem" />
            <span>{suggestion.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
