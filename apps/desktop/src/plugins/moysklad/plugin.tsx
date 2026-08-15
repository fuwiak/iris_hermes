import './moysklad.css'

import {
  atom,
  Button,
  cn,
  Codicon,
  haptic,
  type HermesPlugin,
  host,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  Tip,
  TITLEBAR_AREAS,
  useValue
} from '@hermes/plugin-sdk'
import {
  type FormEvent,
  type UIEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'
import { createPortal } from 'react-dom'

import {
  planAudienceChipClick,
  salesFilterTabsDisabled,
  seedFactsFromAudienceRow
} from './audience-pick'
import {
  audienceRetryDelayMs,
  clientSalesChannelTokens,
  filterClientRowsByAudience,
  isBenignRequestAbort,
  isCatalogWarmingError,
  pickLocalClientsSeed,
  rowMatchesSalesChannelColumnFilter
} from './clients-query'
import { EventCalendarPicker, formatRuRange } from './event-calendar'
import {
  isMassJobActive,
  MASS_AUDIENCE_SELECT_CAP,
  massJobPercent,
  massJobStatusLabel,
  type MassJobSummary,
  massRecipientDisplay,
  type MassRecipientRow,
  massRowStatusLabel,
  massSendConfirmText,
  mergeUniqueIds,
  needsMassSendConfirm,
  overlayMassRows,
  terminalPrefixLength
} from './mass-send'

// Chat refine model variants (OpenRouter ids — edit here to swap versions).
const CHAT_MODEL_IDS: Record<'deepseek' | 'grok' | 'gpt', string> = {
  deepseek: 'deepseek/deepseek-chat',
  grok: 'x-ai/grok-3',
  gpt: 'openai/gpt-5'
}

interface GroupChipOption {
  name: string
  count: number
  source?: 'ms' | 'ai' | 'both' | string
  filter_source?: 'ms' | 'ai' | string
  ms_count?: number
  ai_count?: number
  hue?: number
}

/** Campaigns outreach picker row — catalog / export / custom contacts. */
interface OutreachContact {
  id: string
  name?: string
  phone?: string
  tg_nick?: string
  tg_chat_id?: string
  label?: string
  source?: string
}

function groupChipSrcLabel(source?: string): string {
  if (source === 'ai') {
    return 'AI'
  }

  if (source === 'both') {
    return 'МС+AI'
  }

  return 'МС'
}

/** Full-screen error dialog — inline `.ms-error` scrolls off long CRM pages.
 *
 * Portaled to ``document.body`` so React never reconciles a ``position:fixed``
 * overlay against the long campaigns tree (that path throws
 * ``removeChild`` / NotFoundError when siblings churn).
 */
function MsErrorModal({ message, onClose }: { message: string; onClose: () => void }) {
  if (!message || typeof document === 'undefined') {return null}
  return createPortal(
    <div
      className="ms-modal-backdrop ms-error-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
      role="presentation"
    >
      <div
        aria-labelledby="ms-error-title"
        aria-modal="true"
        className="ms-modal ms-error-modal"
        onClick={e => e.stopPropagation()}
        role="alertdialog"
      >
        <div className="ms-card-head">
          <h3 id="ms-error-title">Ошибка</h3>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <div className="ms-error ms-error-modal-body">{message}</div>
      </div>
    </div>,
    document.body
  )
}

function TgProgressModal({ title, detail }: { title: string; detail: string }) {
  if (typeof document === 'undefined') {return null}
  return createPortal(
    <div className="ms-modal-backdrop ms-tg-progress-backdrop" role="status">
      <div className="ms-modal ms-tg-progress" onClick={e => e.stopPropagation()}>
        <div aria-hidden="true" className="ms-tg-progress-spinner" />
        <h3>{title}</h3>
        <p className="ms-muted">{detail}</p>
        <p className="ms-muted">Не закрывайте вкладку — ждём ответ Telegram / Telethon.</p>
      </div>
    </div>,
    document.body
  )
}

function MassConfirmModal({
  count,
  starting,
  onConfirm,
  onClose
}: {
  count: number
  starting: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  if (typeof document === 'undefined') {return null}
  return createPortal(
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
      role="presentation"
    >
      <div
        aria-labelledby="ms-mass-confirm-title"
        aria-modal="true"
        className="ms-modal ms-mass-confirm-modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
      >
        <div className="ms-card-head">
          <h3 id="ms-mass-confirm-title">Массовая рассылка</h3>
          <button className="ms-btn" onClick={onClose} type="button">
            Отмена
          </button>
        </div>
        <p className="ms-mass-confirm-text">{massSendConfirmText(count)}</p>
        <div className="ms-compose-actions">
          <button
            className="ms-btn ms-btn-primary"
            disabled={starting}
            onClick={onConfirm}
            type="button"
          >
            {starting ? 'Запускаем…' : `Отправить ${count} клиентам`}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

const MASS_ROW_FILTER_TABS: Array<{ id: 'all' | 'failed' | 'ok'; label: string }> = [
  { id: 'all', label: 'Все' },
  { id: 'failed', label: 'Ошибки' },
  { id: 'ok', label: 'Отправлено' }
]

function MassSendStatusModal({
  job,
  rows,
  rowFilter,
  cancelling,
  repliesBusy,
  onRowFilter,
  onCancel,
  onClose,
  onOpenDialog,
  onCollectReplies
}: {
  job: MassJobSummary
  rows: MassRecipientRow[]
  rowFilter: 'all' | 'failed' | 'ok'
  cancelling: boolean
  repliesBusy: boolean
  onRowFilter: (next: 'all' | 'failed' | 'ok') => void
  onCancel: () => void
  onClose: () => void
  onOpenDialog: (row: MassRecipientRow) => void
  onCollectReplies: () => void
}) {
  if (typeof document === 'undefined') {return null}
  const active = isMassJobActive(job.status)
  const total = job.total || 0
  const attempted = job.attempted || 0
  const percent = massJobPercent(attempted, total)
  const shown =
    rowFilter === 'all' ? rows : rows.filter(r => String(r.status) === rowFilter)
  const unseen = Math.max(0, total - rows.length)
  return createPortal(
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
      role="presentation"
    >
      <div
        aria-labelledby="ms-mass-status-title"
        aria-modal="true"
        className="ms-modal ms-mass-status-modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
      >
        <div className="ms-card-head">
          <h3 id="ms-mass-status-title">
            Рассылка · {massJobStatusLabel(job.status)}
          </h3>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <div className="ms-mass-progress">
          <div className="ms-mass-progress-track" role="presentation">
            <div
              className={`ms-mass-progress-fill${active ? ' is-active' : ''}`}
              style={{ width: `${percent}%` }}
            />
          </div>
          <p className="ms-muted">
            {attempted}/{total} · ✓ {job.sent_ok || 0}
            {job.sent_failed ? ` · ✕ ${job.sent_failed}` : ''}
            {job.cancel_requested && active ? ' · останавливаем…' : ''}
          </p>
        </div>
        <p className="ms-muted">
          {active
            ? 'Окно можно закрыть — рассылка продолжится на сервере, статус доступен по кнопке «Статус рассылки».'
            : job.error
              ? `Ошибка: ${job.error}`
              : 'Рассылка завершена. Соберите ответы — кто написал в ответ, появится в списке диалогов.'}
        </p>
        <div className="ms-mass-row-tabs" role="tablist">
          {MASS_ROW_FILTER_TABS.map(tab => (
            <button
              aria-selected={rowFilter === tab.id}
              className={`ms-chip${rowFilter === tab.id ? ' is-active' : ''}`}
              key={tab.id}
              onClick={() => onRowFilter(tab.id)}
              role="tab"
              type="button"
            >
              {tab.label}
              {tab.id === 'failed' && job.sent_failed ? ` · ${job.sent_failed}` : ''}
            </button>
          ))}
        </div>
        <div className="ms-mass-log" role="log">
          {shown.length ? (
            shown.map((row, idx) => (
              <div
                className={`ms-mass-log-row is-${row.status || 'pending'}`}
                key={row.client_id || idx}
              >
                <span className="ms-mass-log-status">
                  {massRowStatusLabel(row.status)}
                </span>
                <button
                  className="ms-link-btn ms-mass-log-name"
                  disabled={!row.client_id}
                  onClick={() => onOpenDialog(row)}
                  title="Открыть диалог с клиентом"
                  type="button"
                >
                  {massRecipientDisplay(row)}
                </button>
                {row.error ? (
                  <span className="ms-mass-log-error">
                    {row.detail || row.error}
                  </span>
                ) : null}
              </div>
            ))
          ) : (
            <p className="ms-muted">
              {rowFilter === 'failed'
                ? 'Ошибок нет.'
                : rowFilter === 'ok'
                  ? 'Пока ничего не отправлено.'
                  : 'Очередь формируется…'}
            </p>
          )}
          {rowFilter === 'all' && unseen > 0 ? (
            <p className="ms-muted">+{unseen} ещё в очереди…</p>
          ) : null}
        </div>
        <div className="ms-compose-actions">
          {active ? (
            <button
              className="ms-btn"
              disabled={cancelling || Boolean(job.cancel_requested)}
              onClick={onCancel}
              type="button"
            >
              {cancelling || job.cancel_requested
                ? 'Останавливаем…'
                : 'Остановить рассылку'}
            </button>
          ) : (
            <button
              className="ms-btn ms-btn-primary"
              disabled={repliesBusy}
              onClick={onCollectReplies}
              type="button"
            >
              {repliesBusy ? 'Собираю…' : 'Собрать ответы'}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

/** In-app TG dialog: full local thread + reply box, live-synced with Telegram. */
function ClientDialogModal({
  clientId,
  clientName,
  onClose
}: {
  clientId: string
  clientName?: string
  onClose: () => void
}) {
  const call = useMsRest()
  const [conversation, setConversation] = useState<ClientConversation | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [sending, setSending] = useState(false)
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [dialogError, setDialogError] = useState('')
  const threadRef = useRef<HTMLDivElement | null>(null)

  const refresh = useCallback(
    async (opts?: { sync?: boolean }) => {
      if (opts?.sync) {
        setSyncing(true)
      }
      try {
        const data = opts?.sync
          ? await call<{
              conversation?: ClientConversation & {
                sync?: { inbound_imported?: number; imported?: number }
              }
            }>(
              `/clients/${encodeURIComponent(clientId)}/conversation/sync?refresh_ai=false`,
              { method: 'POST', timeoutMs: 120_000 }
            )
          : await call<{ conversation?: ClientConversation }>(
              `/clients/${encodeURIComponent(clientId)}/conversation`
            )
        setConversation(data.conversation || null)
        if (opts?.sync) {
          const sync = (data.conversation as { sync?: { inbound_imported?: number } })
            ?.sync
          setNote(
            sync?.inbound_imported
              ? `+${sync.inbound_imported} новых входящих из Telegram`
              : 'Синхронизировано с Telegram'
          )
        }
        setDialogError('')
      } catch (err) {
        setDialogError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
        if (opts?.sync) {
          setSyncing(false)
        }
      }
    },
    [call, clientId]
  )

  useEffect(() => {
    setLoading(true)
    setConversation(null)
    setText('')
    setNote('')
    setDialogError('')
    // Local thread paints instantly; live TG pull follows right after.
    void refresh().then(() => refresh({ sync: true }))
    const timer = setInterval(() => {
      void refresh({ sync: true })
    }, 30_000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    const box = threadRef.current?.querySelector('.ms-conversation-list')
    if (box) {
      box.scrollTop = 0 // newest message renders on top now
    }
  }, [conversation])

  const send = async () => {
    const msg = text.trim()
    if (!msg || sending) {
      return
    }
    setSending(true)
    setDialogError('')
    try {
      const data = await call<{
        conversation?: ClientConversation
        delivery?: { ok?: boolean; skipped?: boolean; error?: string; detail?: string }
      }>(`/clients/${encodeURIComponent(clientId)}/conversation`, {
        method: 'POST',
        body: {
          text: msg,
          direction: 'outbound',
          channel: 'telegram',
          source: 'client_card_send',
          open_deep_link: false
        }
      })
      if (data.conversation) {
        setConversation(data.conversation)
      }
      setText('')
      if (data.delivery?.ok) {
        setNote('✓ Отправлено в Telegram')
      } else if (data.delivery && !data.delivery.skipped) {
        setNote('')
        setDialogError(
          `Telegram: ${data.delivery.detail || data.delivery.error || 'ошибка'} — сообщение записано в историю`
        )
      } else {
        setNote('Записано в историю (доставка не настроена)')
      }
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  if (typeof document === 'undefined') {return null}
  return createPortal(
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
      role="presentation"
    >
      <div
        aria-labelledby="ms-dialog-title"
        aria-modal="true"
        className="ms-modal ms-dialog-modal"
        onClick={e => e.stopPropagation()}
        role="dialog"
      >
        <div className="ms-card-head">
          <h3 id="ms-dialog-title">
            Диалог · {clientName || conversation?.client_id || clientId}
          </h3>
          <div className="ms-dialog-head-actions">
            <button
              className="ms-link-btn"
              disabled={syncing}
              onClick={() => void refresh({ sync: true })}
              title="Подтянуть свежие сообщения из Telegram"
              type="button"
            >
              {syncing ? 'Синхронизируем…' : 'Обновить из Telegram'}
            </button>
            <button className="ms-btn" onClick={onClose} type="button">
              Закрыть
            </button>
          </div>
        </div>
        <div className="ms-dialog-thread" ref={threadRef}>
          {loading ? (
            <p className="ms-muted">Загружаем историю…</p>
          ) : (
            <ConversationThread conversation={conversation} title="Диалог (TG)" />
          )}
        </div>
        {note ? <p className="ms-muted ms-dialog-note">{note}</p> : null}
        {dialogError ? <p className="ms-error">{dialogError}</p> : null}
        <div className="ms-dialog-reply">
          <textarea
            onChange={e => setText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder="Ответ клиенту… (Ctrl+Enter — отправить)"
            rows={3}
            value={text}
          />
          <button
            className="ms-btn ms-btn-primary"
            disabled={sending || !text.trim()}
            onClick={() => void send()}
            type="button"
          >
            {sending ? 'Отправляю…' : 'Отправить'}
          </button>
        </div>
        <p className="ms-muted ms-dialog-hint">
          Ответ уходит через ваш Telegram и остаётся в истории. Диалог
          автообновляется каждые 30 сек — можно продолжать и отсюда, и из
          Telegram.
        </p>
      </div>
    </div>,
    document.body
  )
}

const DEFAULT_AI_GROUP_CHIPS: GroupChipOption[] = [
  'новый',
  'премиум',
  'постоянный клиент',
  'несостоявшийся',
  'клиент',
  'букет от 10 000',
  'прямые продажи',
  'маркетплейс',
  'витрина',
  'telegram',
  'whatsapp',
  'сайт',
  'событие января',
  'событие февраля',
  'событие марта',
  'событие апреля',
  'событие мая',
  'событие июня',
  'событие июля',
  'событие августа',
  'событие сентября',
  'событие октября',
  'событие ноября',
  'событие декабря'
].map(name => ({
  name,
  count: 0,
  source: 'ai',
  filter_source: 'ai',
  ms_count: 0,
  ai_count: 0
}))

function mergeAiGroupOptions(ai: GroupChipOption[]): GroupChipOption[] {
  const byName = new Map<string, GroupChipOption>()
  for (const opt of ai || []) {
    const key = String(opt.name || '').trim().toLowerCase()
    if (!key) {continue}
    byName.set(key, { ...opt, filter_source: opt.filter_source || 'ai' })
  }
  for (const fallback of DEFAULT_AI_GROUP_CHIPS) {
    const key = fallback.name.toLowerCase()
    if (!byName.has(key)) {
      byName.set(key, fallback)
    }
  }
  return [...byName.values()].sort(
    (a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name, 'ru')
  )
}

function resolveGroupOptionsBySource(data: {
  group_options?: GroupChipOption[]
  group_options_by_source?: { ms?: GroupChipOption[]; ai?: GroupChipOption[] }
}): { ms: GroupChipOption[]; ai: GroupChipOption[] } {
  const bySrc = data.group_options_by_source
  let ms = bySrc?.ms || []
  let ai = bySrc?.ai || []
  if (!bySrc) {
    const all = data.group_options || []
    ms = all.filter(o => (o.source || 'ms') !== 'ai')
    ai = all.filter(o => o.source === 'ai' || o.source === 'both')
  }
  // Always keep «Группы: ИИ» populated (defaults + server chips).
  ai = mergeAiGroupOptions(ai)
  ms = [...ms].sort(
    (a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name, 'ru')
  )
  return { ms, ai }
}

function groupChipSrcClass(source?: string): string {
  if (source === 'ai') {return 'is-ai'}
  if (source === 'both') {return 'is-both'}
  return 'is-ms'
}

function GroupCloudSection({
  title,
  items,
  activeGroup,
  activeSource,
  sourceKey,
  onToggle,
  limit = 24,
  emptyHint
}: {
  title: string
  items: GroupChipOption[]
  activeGroup: string
  activeSource: string
  sourceKey: 'ms' | 'ai'
  onToggle: (name: string, source: 'ms' | 'ai') => void
  limit?: number
  emptyHint?: string
}) {
  if (!items.length) {
    return (
      <div className="ms-filter-block ms-group-cloud">
        <div className="ms-group-cloud-head">
          <span className="ms-group-cloud-title">{title}</span>
          <span className="ms-muted">0</span>
        </div>
        <p className="ms-muted">{emptyHint || 'Нет групп для фильтра'}</p>
      </div>
    )
  }

  return (
    <div className="ms-filter-block ms-group-cloud">
      <div className="ms-group-cloud-head">
        <span className="ms-group-cloud-title">{title}</span>
        <span className="ms-muted">{items.length}</span>
      </div>
      <div className="ms-group-chips">
        {items.slice(0, limit).map(opt => {
          const src = opt.filter_source || opt.source || sourceKey
          const active = activeGroup === opt.name && activeSource === sourceKey

          return (
            <button
              className={`ms-group-chip ${groupChipSrcClass(src)}${active ? ' is-active' : ''}`}
              key={`${sourceKey}:${opt.name}`}
              onClick={() => onToggle(opt.name, sourceKey)}
              title={`${opt.count} · ${groupChipSrcLabel(src)}`}
              type="button"
            >
              <span className={`ms-chip-src ms-chip-src-${src}`}>
                {groupChipSrcLabel(sourceKey)}
              </span>
              {opt.name}
              <span className="ms-group-chip-count">{opt.count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

interface ClientOrder {
  id?: string
  name?: string
  date?: string
  sum?: number
  channel?: string
  product_snippet?: string
  composition?: string | null
  line_items?: string[]
  state?: string | null
  payment_status?: string | null
  applicable?: boolean | null
}

interface ConversationMessage {
  id?: string
  direction?: string
  channel?: string
  label?: string
  text?: string
  ts?: string
  source?: string
}

interface ClientConversation {
  client_id?: string
  messages?: ConversationMessage[]
  message_count?: number
  preview?: string
  empty?: boolean
  updated_at?: string | null
}

interface ClientDetail {
  client?: ClientRow & {
    vip?: boolean
    loyalty_points?: number | null
    primary_channel?: string
    tag_buckets?: {
      marketplace?: string[]
      loyalty?: string[]
      events?: string[]
      other?: string[]
    }
  }
  orders?: ClientOrder[]
  stats?: {
    avg_check?: number
    order_count?: number
    paid_order_count?: number
    cancelled_order_count?: number
    unpaid_order_count?: number
    fulfilled_order_count?: number
    vip?: boolean
    loyalty_points?: number | null
    last_order?: ClientOrder
  }
  messaging?: {
    whatsapp_url?: string
    telegram_url?: string
    primary_channel?: string
    hint?: string
  }
  ai?: {
    history_profile?: string
    occasion_intent?: string
    recommendation?: string
    source?: string
    provider?: string
    model?: string
    data_thin?: boolean
  }
  risks?: ClientRisks
  fact_blocks?: {
    history_profile?: FactBlock
    occasion_intent?: FactBlock
    risks?: FactBlock
  }
  conversation?: ClientConversation
  data_thin?: boolean
}

type Rest = <T>(path: string, opts?: { method?: string; body?: unknown; timeoutMs?: number }) => Promise<T>
type RestStream = (
  path: string,
  opts: {
    method?: string
    body?: unknown
    timeoutMs?: number
    onEvent: (event: unknown) => void
    signal?: AbortSignal
  }
) => Promise<void>

let rest: null | Rest = null
let restStream: null | RestStream = null

function useMsRest(): Rest {
  return useCallback(async <T,>(path: string, opts?: { method?: string; body?: unknown; timeoutMs?: number }) => {
    if (!rest) {
      throw new Error('MoySklad plugin REST not bound')
    }

    return rest<T>(path, opts)
  }, [])
}

function useMsRestStream(): RestStream {
  return useCallback(
    async (
      path: string,
      opts: {
        method?: string
        body?: unknown
        timeoutMs?: number
        onEvent: (event: unknown) => void
        signal?: AbortSignal
      }
    ) => {
      if (!restStream) {
        throw new Error('MoySklad plugin REST stream not bound')
      }

      return restStream(path, opts)
    },
    []
  )
}

/** Pull outreach text from generate/rewrite/sanity payloads (tolerant to nesting). */
function pickOutreachMessage(data: unknown): string {
  if (!data || typeof data !== 'object') {
    return ''
  }

  const row = data as Record<string, unknown>
  const nested = row.result && typeof row.result === 'object' ? (row.result as Record<string, unknown>) : null
  const sanity =
    row.sanity && typeof row.sanity === 'object' ? (row.sanity as Record<string, unknown>) : null
  const candidates = [
    row.message,
    row.text,
    row.offer,
    row.draft,
    sanity?.revised_text,
    nested?.message,
    nested?.text
  ]

  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) {
      return c
    }
  }

  return ''
}

const OUTREACH_AI_TIMEOUT_MS = 120_000

interface Counts {
  total?: number
  marketplace?: number
  direct?: number
}

interface ClientRow {
  id?: string
  name?: string
  tg_conversation_preview?: string
  conversation_count?: number
  phone?: string
  email?: string
  state?: string
  sales_type?: string
  audience?: { direct?: boolean; marketplace?: boolean }
  channel?: string
  channels?: string[]
  tags?: string[]
  groups?: string
  ms_groups?: string
  ai_groups?: string[]
  order_count?: number | null
  avg_check?: number | null
  last_order_at?: string | null
  bonus_points?: string | number
  role?: string
  actual_address?: string
  actual_address_comment?: string
  company_type?: string
  sex?: string
  tg_nick?: string
  tg_active?: boolean | null
  tg_active_label?: string
  tg_active_nick?: string
  tg_conversation?: string
  client_stage?: string
  client_stage_reason?: string
  ai_fields?: string[]
  ai_fill_source?: string
}

interface AuditIssue {
  code: string
  label: string
  severity: 'error' | 'warn' | 'info'
  count: number
  hint?: string
  sample?: { id?: string; name?: string; detail?: string }[]
}

interface AuditReport {
  rows_total?: number
  issues?: AuditIssue[]
  issues_total?: number
  errors_total?: number
  clean?: boolean
  checked_at?: string
  stages?: Record<string, number>
}

interface SavedSegment {
  id: string
  name: string
  matched_total?: number
  filters?: {
    sales_filter?: string
    group?: string
    q?: string
    group_source?: string
    channel_kind?: string
    require_phone?: boolean
    require_telegram?: boolean
    vip_only?: boolean
    birthday_soon?: boolean
    days_before_event?: number
    event_date_from?: string
    event_date_to?: string
    stage?: string
  }
}

type StageKey = 'all' | 'failed' | 'customer' | 'no_orders' | 'unknown'
type StageCounts = Partial<Record<StageKey, number>>

/** Тип клиента chips — «не состоялся» = ни одной оплаты (или ждёт оплаты). */
const STAGE_CHIPS: { id: StageKey; label: string; title: string }[] = [
  { id: 'all', label: 'Все типы', title: 'Без фильтра по типу клиента' },
  {
    id: 'failed',
    label: 'Не состоялся',
    title: 'Заказы есть, но ни одной оплаты — включая свежие, что ждут оплаты'
  },
  { id: 'customer', label: 'Покупатель', title: 'Есть хотя бы один оплаченный заказ' },
  { id: 'no_orders', label: 'Нет заказов', title: 'Контрагент есть, заказов ноль' }
]

/** Public keys stamped by POST /clients/ai-fill for green AI markers. */
const AI_COLUMN_KEYS: Record<string, string> = {
  state: 'state',
  groups_display: 'groups',
  role: 'role',
  sex: 'sex',
  tg_nick: 'tg_nick',
  company_type: 'company_type'
}

function AiCell({
  value,
  ai
}: {
  value: string
  ai?: boolean
}) {
  if (!value) {
    return <>{'—'}</>
  }

  if (!ai) {
    return <>{value}</>
  }

  return (
    <span className="ms-ai-cell" title="Заполнено AI (кэш Redis/файл)">
      <span aria-hidden="true" className="ms-ai-badge">
        AI
      </span>
      <span className="ms-ai-value">{value}</span>
    </span>
  )
}

/** Empty fillable CRM slots that AI may complete (lazy page eval). */
function rowNeedsLazyAiFill(row: ClientRow): boolean {
  if (!row.id) {
    return false
  }

  if ((row.ai_fields || []).length > 0) {
    return false
  }

  const groups = (row.groups || (row.tags || []).join(', ')).trim()
  const empty = (v: string | undefined | null) => !v || !String(v).trim() || String(v).trim() === '—'

  return (
    empty(row.state) ||
    empty(groups) ||
    empty(row.role) ||
    empty(row.sex) ||
    empty(row.company_type)
  )
}

const LAZY_AI_BATCH = 50

function applyAiFillResults(
  clients: ClientRow[],
  results: Array<{
    id?: string
    filled?: Record<string, unknown>
    fields?: Record<string, unknown>
    ai_fields?: string[]
    source?: string
  }>
): ClientRow[] {
  if (!results.length) {
    return clients
  }

  const byId = new Map(
    results
      .filter(r => r.id)
      .map(r => [String(r.id), r] as const)
  )

  return clients.map(row => {
    const id = String(row.id || '')
    const hit = byId.get(id)

    if (!hit) {
      return row
    }

    const fields = {
      ...(hit.fields || {}),
      ...(hit.filled || {})
    } as Record<string, unknown>
    const next: ClientRow = { ...row }
    const aiFields = [
      ...new Set([
        ...(hit.ai_fields || []),
        ...(row.ai_fields || []),
        ...Object.keys(fields)
      ])
    ]

    for (const key of aiFields) {
      const val = fields[key]

      if (val == null || val === '') {
        continue
      }

      if (key === 'groups') {
        if (Array.isArray(val)) {
          next.groups = val.map(String).filter(Boolean).join(', ')
          next.tags = val.map(String).filter(Boolean)
        } else {
          next.groups = String(val)
        }
      } else if (key === 'state') {
        next.state = String(val)
      } else if (key === 'sex') {
        next.sex = String(val)
      } else if (key === 'role') {
        next.role = String(val)
      } else if (key === 'tg_nick') {
        next.tg_nick = String(val)
      } else if (key === 'company_type') {
        next.company_type = String(val)
      }
    }

    next.ai_fields = aiFields.length ? aiFields : row.ai_fields
    next.ai_fill_source = hit.source || row.ai_fill_source

    return next
  })
}

const CLIENT_COLUMNS: Array<{
  key: keyof ClientRow | 'channel_display' | 'groups_display' | 'avg_display' | 'last_order_display' | 'orders_display'
  label: string
  /** Underlying value for sort (numbers/dates preferred over display text). */
  sortValue?: (row: ClientRow) => string | number | null | undefined
  render: (row: ClientRow) => string
}> = [
  { key: 'name', label: 'Наименование', sortValue: r => r.name || '', render: r => r.name || '' },
  { key: 'phone', label: 'Телефон', sortValue: r => r.phone || '', render: r => r.phone || '' },
  { key: 'state', label: 'Статус', sortValue: r => r.state || '', render: r => r.state || '' },
  {
    key: 'sales_type',
    label: 'Тип канала продаж',
    sortValue: r => r.sales_type || '',
    render: r => r.sales_type || ''
  },
  {
    key: 'channel_display',
    label: 'Канал продаж',
    sortValue: r =>
      (r.channels || []).length
        ? (r.channels || []).join(', ')
        : r.channel || 'Без канала',
    render: r =>
      (r.channels || []).length
        ? (r.channels || []).join(', ')
        : r.channel || 'Без канала'
  },
  {
    key: 'avg_display',
    label: 'Средний чек',
    sortValue: r => (r.avg_check == null ? null : Number(r.avg_check)),
    render: r => money(r.avg_check)
  },
  {
    key: 'last_order_display',
    label: 'Дата последнего заказа',
    sortValue: r => r.last_order_at || '',
    render: r =>
      r.last_order_at
        ? (r.last_order_at || '').slice(0, 16).replace('T', ' ')
        : '—'
  },
  {
    key: 'orders_display',
    label: 'Всего заказов',
    sortValue: r => (r.order_count == null ? null : Number(r.order_count)),
    render: r => (r.order_count == null ? '—' : String(r.order_count))
  },
  {
    key: 'bonus_points',
    label: 'Баллы начисленные',
    sortValue: r => {
      const n = Number(r.bonus_points)
      return Number.isFinite(n) ? n : String(r.bonus_points ?? '')
    },
    render: r => String(r.bonus_points ?? '')
  },
  {
    key: 'groups_display',
    label: 'Группы',
    sortValue: r => {
      const ms = String(r.ms_groups || '').trim()
      const ai = (r.ai_groups || []).filter(Boolean)
      if (ms && ai.length) {return `МС: ${ms} · AI: ${ai.join(', ')}`}
      if (ai.length) {return `AI: ${ai.join(', ')}`}
      return r.groups || (r.tags || []).join(', ')
    },
    render: r => {
      const ms = String(r.ms_groups || '').trim()
      const ai = (r.ai_groups || []).filter(Boolean)
      if (ms && ai.length) {return `МС: ${ms} · AI: ${ai.join(', ')}`}
      if (ai.length) {return `AI: ${ai.join(', ')}`}
      return r.groups || (r.tags || []).join(', ')
    }
  },
  { key: 'role', label: 'Заказчик или получатель', sortValue: r => r.role || '', render: r => r.role || '' },
  {
    key: 'actual_address',
    label: 'Фактический адрес',
    sortValue: r => r.actual_address || '',
    render: r => r.actual_address || ''
  },
  {
    key: 'actual_address_comment',
    label: 'Фактический адрес (Комментарий)',
    sortValue: r => r.actual_address_comment || '',
    render: r => r.actual_address_comment || ''
  },
  {
    key: 'client_stage',
    label: 'Тип клиента',
    sortValue: r => r.client_stage || '',
    render: r => r.client_stage || ''
  },
  {
    key: 'company_type',
    label: 'Тип контрагента',
    sortValue: r => r.company_type || '',
    render: r => r.company_type || ''
  },
  { key: 'sex', label: 'Пол', sortValue: r => r.sex || '', render: r => r.sex || '' },
  { key: 'email', label: 'E-mail', sortValue: r => r.email || '', render: r => r.email || '' },
  { key: 'tg_nick', label: 'ТГ ник', sortValue: r => r.tg_nick || '', render: r => r.tg_nick || '' },
  {
    key: 'tg_active',
    label: 'TG активен',
    sortValue: r =>
      r.tg_active === true ? 2 : r.tg_active === false ? 0 : r.tg_nick ? 1 : -1,
    render: r => {
      if (r.tg_active === true) {
        return r.tg_active_nick || r.tg_active_label || '✓'
      }
      if (r.tg_active === false) {
        return r.tg_active_label || '✗'
      }
      if (r.tg_nick) {
        return r.tg_active_label || '?'
      }
      return '—'
    }
  },
  {
    key: 'tg_conversation',
    label: 'TG conversation',
    sortValue: r => r.tg_conversation_preview || r.tg_conversation || '',
    render: r => {
      const preview = r.tg_conversation_preview || r.tg_conversation || ''
      const n = r.conversation_count

      if (!preview) {
        return ''
      }

      return n && n > 0 ? `${preview}` : preview
    }
  }
]

type ClientColKey = (typeof CLIENT_COLUMNS)[number]['key']
type SortDir = 'asc' | 'desc'

interface ColumnSortSpec {
  key: ClientColKey
  dir: SortDir
}

interface ColumnFilterSpec {
  /** Case-insensitive substring match on display value. */
  query: string
  /**
   * Selected unique display values. ``null`` = all values allowed.
   * Empty array = match nothing.
   */
  selected: string[] | null
}

const EMPTY_FILTER: ColumnFilterSpec = { query: '', selected: null }
const BLANK_FILTER_LABEL = '(пусто)'
const UNIQUE_FILTER_CAP = 60

function columnDisplayValue(col: (typeof CLIENT_COLUMNS)[number], row: ClientRow): string {
  const raw = col.render(row)
  return raw == null ? '' : String(raw)
}

function columnSortRaw(col: (typeof CLIENT_COLUMNS)[number], row: ClientRow): string | number | null {
  if (col.sortValue) {
    const v = col.sortValue(row)
    if (v == null || v === '') {return null}
    return v
  }
  const s = columnDisplayValue(col, row)
  return s === '' || s === '—' ? null : s
}

function compareColumnValues(
  a: string | number | null,
  b: string | number | null,
  dir: SortDir
): number {
  const mul = dir === 'asc' ? 1 : -1
  if (a == null && b == null) {return 0}
  if (a == null) {return 1}
  if (b == null) {return -1}
  if (typeof a === 'number' && typeof b === 'number') {
    return (a - b) * mul
  }
  return String(a).localeCompare(String(b), 'ru', { sensitivity: 'base', numeric: true }) * mul
}

function filterLabel(value: string): string {
  return value === '' || value === '—' ? BLANK_FILTER_LABEL : value
}

function applyClientColumnFilters(
  rows: ClientRow[],
  filters: Partial<Record<ClientColKey, ColumnFilterSpec>>,
  sort: ColumnSortSpec | null
): ClientRow[] {
  let out = rows
  const active = CLIENT_COLUMNS.filter(col => {
    const f = filters[col.key]
    return Boolean(f && (f.query.trim() || f.selected != null))
  })
  if (active.length) {
    out = rows.filter(row =>
      active.every(col => {
        const f = filters[col.key] || EMPTY_FILTER
        if (col.key === 'channel_display') {
          return rowMatchesSalesChannelColumnFilter(row, f.query, f.selected, BLANK_FILTER_LABEL)
        }
        const display = columnDisplayValue(col, row)
        const label = filterLabel(display)
        if (f.query.trim()) {
          const q = f.query.trim().toLowerCase()
          if (!display.toLowerCase().includes(q) && !label.toLowerCase().includes(q)) {
            return false
          }
        }
        if (f.selected != null) {
          return f.selected.includes(label)
        }
        return true
      })
    )
  }
  if (sort) {
    const col = CLIENT_COLUMNS.find(c => c.key === sort.key)
    if (col) {
      out = [...out].sort((ra, rb) =>
        compareColumnValues(columnSortRaw(col, ra), columnSortRaw(col, rb), sort.dir)
      )
    }
  }
  return out
}

function uniqueColumnValues(rows: ClientRow[], col: (typeof CLIENT_COLUMNS)[number]): string[] {
  const counts = new Map<string, number>()
  for (const row of rows) {
    if (col.key === 'channel_display') {
      const tokens = clientSalesChannelTokens(row)
      const labels =
        tokens.length > 0 ? tokens.map(c => filterLabel(c)) : [BLANK_FILTER_LABEL]
      for (const label of labels) {
        counts.set(label, (counts.get(label) || 0) + 1)
      }
      continue
    }
    const label = filterLabel(columnDisplayValue(col, row))
    counts.set(label, (counts.get(label) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ru'))
    .map(([label]) => label)
}

function ClientsColumnHeader({
  col,
  sort,
  filter,
  uniqueValues,
  open,
  onToggleOpen,
  onSort,
  onFilterChange,
  onClearFilter
}: {
  col: (typeof CLIENT_COLUMNS)[number]
  sort: ColumnSortSpec | null
  filter: ColumnFilterSpec
  uniqueValues: string[]
  open: boolean
  onToggleOpen: () => void
  onSort: (dir: SortDir | null) => void
  onFilterChange: (next: ColumnFilterSpec) => void
  onClearFilter: () => void
}) {
  const menuRef = useRef<HTMLDivElement | null>(null)
  const activeSort = sort?.key === col.key ? sort.dir : null
  const filterActive = Boolean(filter.query.trim() || filter.selected != null)
  const showUniques = uniqueValues.length > 0 && uniqueValues.length <= UNIQUE_FILTER_CAP
  const [draftQuery, setDraftQuery] = useState(filter.query)
  const [draftSelected, setDraftSelected] = useState<string[] | null>(filter.selected)

  useEffect(() => {
    if (!open) {return}
    setDraftQuery(filter.query)
    setDraftSelected(filter.selected)
  }, [open, filter.query, filter.selected])

  useEffect(() => {
    if (!open) {return}
    const onDoc = (event: MouseEvent) => {
      const el = menuRef.current
      if (el && !el.contains(event.target as Node)) {
        onToggleOpen()
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open, onToggleOpen])

  const cycleHeaderSort = () => {
    if (activeSort === 'asc') {onSort('desc')}
    else if (activeSort === 'desc') {onSort(null)}
    else {onSort('asc')}
  }

  const toggleValue = (label: string) => {
    const base = draftSelected == null ? [...uniqueValues] : [...draftSelected]
    const idx = base.indexOf(label)
    if (idx >= 0) {base.splice(idx, 1)}
    else {base.push(label)}
    if (base.length === uniqueValues.length) {setDraftSelected(null)}
    else {setDraftSelected(base)}
  }

  return (
    <th className={`ms-th-filter${filterActive ? ' is-filtered' : ''}${activeSort ? ' is-sorted' : ''}`}>
      <div className="ms-th-inner" ref={menuRef}>
        <button
          className="ms-th-sort"
          onClick={cycleHeaderSort}
          title="Сортировка: клик = А→Я / Я→А / сброс"
          type="button"
        >
          <span className="ms-th-label">{col.label}</span>
          <span aria-hidden="true" className="ms-th-sort-mark">
            {activeSort === 'asc' ? '▲' : activeSort === 'desc' ? '▼' : '↕'}
          </span>
        </button>
        <button
          className={`ms-th-filter-btn${filterActive || open ? ' is-on' : ''}`}
          onClick={onToggleOpen}
          title="Фильтр как в Excel"
          type="button"
        >
          ▾
        </button>
        {open ? (
          <div className="ms-col-filter-menu" role="dialog">
            <div className="ms-col-filter-sorts">
              <button onClick={() => onSort('asc')} type="button">
                Сортировка А → Я
              </button>
              <button onClick={() => onSort('desc')} type="button">
                Сортировка Я → А
              </button>
            </div>
            <label className="ms-col-filter-search">
              Содержит
              <input
                autoFocus
                onChange={e => setDraftQuery(e.target.value)}
                placeholder="текст…"
                value={draftQuery}
              />
            </label>
            {showUniques ? (
              <div className="ms-col-filter-values">
                <label className="ms-col-filter-check">
                  <input
                    checked={draftSelected == null}
                    onChange={() => setDraftSelected(null)}
                    type="checkbox"
                  />
                  (Выделить всё)
                </label>
                {uniqueValues.map(label => {
                  const checked = draftSelected == null || draftSelected.includes(label)
                  return (
                    <label className="ms-col-filter-check" key={label}>
                      <input
                        checked={checked}
                        onChange={() => toggleValue(label)}
                        type="checkbox"
                      />
                      <span title={label}>{label}</span>
                    </label>
                  )
                })}
              </div>
            ) : uniqueValues.length > UNIQUE_FILTER_CAP ? (
              <p className="ms-muted ms-col-filter-hint">
                Уникальных значений слишком много ({uniqueValues.length}) — используйте «Содержит».
              </p>
            ) : null}
            <div className="ms-col-filter-actions">
              <button
                className="ms-btn"
                onClick={() => {
                  onClearFilter()
                  onToggleOpen()
                }}
                type="button"
              >
                Сбросить
              </button>
              <button
                className="ms-btn ms-btn-primary"
                onClick={() => {
                  onFilterChange({
                    query: draftQuery,
                    selected: draftSelected
                  })
                  onToggleOpen()
                }}
                type="button"
              >
                OK
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </th>
  )
}

interface Campaign {
  id: string
  title: string
  channel: string
  mode: string
  offer?: string
  status?: string
  audience_count?: number
  client_id?: string
  client_name?: string
  recommendation?: string
  grounding_notes?: string
  ai_source?: string
  facts?: ClientFacts
  personalize_pending?: boolean
  audience_filters?: Record<string, unknown>
}

interface FactBlockLine {
  label?: string
  value?: string
}

interface FactBlock {
  title?: string
  empty?: boolean
  lines?: FactBlockLine[]
  note?: string | null
  do_not_upsell?: boolean
}

interface ClientRisks {
  has_debt?: boolean
  debt_amount?: number | null
  balance?: number | null
  unpaid_order_count?: number
  unpaid_total?: number
  do_not_upsell?: boolean
  flags?: string[]
}

interface ClientFacts {
  client_id?: string
  name?: string
  phone?: string | null
  email?: string | null
  tg_nick?: string | null
  sales_type?: string | null
  channels?: string[]
  primary_channel?: string | null
  order_count?: number
  avg_check?: number
  vip?: boolean
  loyalty_points?: number | null
  last_order?: ClientOrder | null
  tags?: string[]
  event_tags?: string[]
  orders_preview?: ClientOrder[]
  data_thin?: boolean
  recommendation?: string | null
  history_profile?: string | null
  occasion_intent?: string | null
  ai_source?: string | null
  ai_model?: string | null
  ai_provider?: string | null
  risks?: ClientRisks
  block_history_profile?: FactBlock
  block_occasion_intent?: FactBlock
  block_risks?: FactBlock
  fact_blocks?: {
    history_profile?: FactBlock
    occasion_intent?: FactBlock
    risks?: FactBlock
  }
  conversation?: ClientConversation
}

interface SanityResult {
  ok?: boolean
  issues?: string[]
  revised_text?: string | null
  source?: string
  auto_revised?: boolean
}

const DRAFT_PREFILL_KEY = 'moysklad.draftPrefill'
/** Status shown after successful generate (and restored from Redis/file cache). */
const AI_GENERATED_STATUS = 'AI сгенерировал креативный текст — можно править вручную.'

/** Client-card summary / recommendation — DeepSeek only (default + sole UI option). */
const MS_AI_DEFAULT_PROVIDER = 'openrouter'
const MS_AI_DEFAULT_MODEL = 'deepseek/deepseek-chat'

function readMsAiChoice(): { provider: string; model: string } {
  if (typeof localStorage === 'undefined') {
    return { provider: MS_AI_DEFAULT_PROVIDER, model: MS_AI_DEFAULT_MODEL }
  }
  try {
    const provider = localStorage.getItem('ms.ai.provider')
    const model = localStorage.getItem('ms.ai.model')
    // Migrate empty «default» / non-DeepSeek picks to DeepSeek.
    if (!provider || !model || !/deepseek/i.test(model)) {
      localStorage.setItem('ms.ai.provider', MS_AI_DEFAULT_PROVIDER)
      localStorage.setItem('ms.ai.model', MS_AI_DEFAULT_MODEL)
      return { provider: MS_AI_DEFAULT_PROVIDER, model: MS_AI_DEFAULT_MODEL }
    }
    return { provider, model }
  } catch {
    return { provider: MS_AI_DEFAULT_PROVIDER, model: MS_AI_DEFAULT_MODEL }
  }
}

function isDeepseekAiSource(facts: {
  ai_source?: string | null
  ai_model?: string | null
} | null): boolean {
  if (!facts) {
    return false
  }
  const model = String(facts.ai_model || '').toLowerCase()
  if (model.includes('deepseek')) {
    return true
  }
  // LLM without model tag after DeepSeek-only default still counts.
  const src = String(facts.ai_source || '').toLowerCase()
  return src === 'llm' || src.includes('deepseek')
}

interface DraftPrefill {
  clientId: string
  channel?: string
  salesFilter?: string
}

function readDraftPrefill(): DraftPrefill | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_PREFILL_KEY)

    if (!raw) {
      return null
    }

    sessionStorage.removeItem(DRAFT_PREFILL_KEY)
    const parsed = JSON.parse(raw) as DraftPrefill

    if (!parsed?.clientId) {
      return null
    }

    return parsed
  } catch {
    return null
  }
}

function writeDraftPrefill(prefill: DraftPrefill) {
  try {
    sessionStorage.setItem(DRAFT_PREFILL_KEY, JSON.stringify(prefill))
  } catch {
    /* ignore quota / private mode */
  }
}

function channelFromMessaging(primary?: string): string {
  const p = (primary || '').toLowerCase()

  if (p.includes('whatsapp')) {
    return 'whatsapp'
  }

  return 'telegram'
}

function money(n: number | null | undefined) {
  if (n == null || Number.isNaN(Number(n))) {return '—'}
  const v = Number(n)

  if (!Number.isFinite(v) || v <= 0) {return '—'}

  try {
    return new Intl.NumberFormat('ru-RU', {
      style: 'currency',
      currency: 'RUB',
      maximumFractionDigits: 0
    }).format(v)
  } catch {
    return `${Math.round(v)} ₽`
  }
}

function FilterTabs({
  salesFilter,
  counts,
  onChange,
  disabled
}: {
  salesFilter: string
  counts: Counts | null
  onChange: (id: string) => void
  disabled?: boolean
}) {
  const tabs = [
    { id: 'all', label: 'Все', count: counts?.total },
    { id: 'marketplace', label: 'Маркетплейс', count: counts?.marketplace },
    { id: 'direct', label: 'Прямые', count: counts?.direct }
  ]

  return (
    <div className="ms-filter-tabs" role="tablist">
      {tabs.map(tab => (
        <button
          className={`ms-filter-tab${salesFilter === tab.id ? ' is-active' : ''}`}
          disabled={disabled}
          key={tab.id}
          onClick={() => onChange(tab.id)}
          role="tab"
          type="button"
        >
          {tab.label}
          {tab.count != null ? <span className="ms-tab-count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

function TagPills({ items, className }: { items?: string[]; className?: string }) {
  if (!items?.length) {
    return null
  }

  return (
    <div className={className || 'ms-tag-row'}>
      {items.map(t => (
        <span className="ms-tag-pill" key={t}>
          {t}
        </span>
      ))}
    </div>
  )
}

function ConversationThread({
  conversation,
  compact,
  title
}: {
  conversation?: ClientConversation | null
  compact?: boolean
  title?: string
}) {
  const messages = conversation?.messages || []

  if (!messages.length) {
    return (
      <div className="ms-conversation">
        <p className="ms-ai-label">{title || 'TG conversation'}</p>
        <p className="ms-muted">
          Нет истории. Нажмите «Импорт Telegram» на Клиентах (нужен файл
          telegram_export.json на сервере) — подтянутся старые личные чаты по
          телефону / Наименованию.
        </p>
      </div>
    )
  }

  // Full history, newest FIRST — operator reads the latest exchange without
  // scrolling; compact only shrinks the scroll box.
  const shown = [...messages].reverse()

  return (
    <div className="ms-conversation">
      <p className="ms-ai-label">
        {title || 'TG conversation'}
        {conversation?.message_count != null
          ? ` · ${conversation.message_count}`
          : ''}
      </p>
      <div className={`ms-conversation-list${compact ? ' is-compact' : ''}`}>
        {shown.map((m, idx) => (
          <div
            className={`ms-conversation-msg is-${m.direction || 'outbound'}`}
            key={m.id || `${m.ts || ''}-${idx}`}
          >
            <div className="ms-muted">
              {(m.label || m.direction || 'сообщение') +
                (m.ts ? ` · ${String(m.ts).slice(0, 16).replace('T', ' ')}` : '')}
            </div>
            <div>{m.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function FactBlockView({ block }: { block?: FactBlock | null }) {
  if (!block) {return null}
  const riskClass = block.do_not_upsell ? ' ms-fact-block-risk' : ''

  return (
    <div className={`ms-fact-block${riskClass}`}>
      <p className="ms-ai-label">{block.title || 'Факты'}</p>
      {block.empty || !block.lines?.length ? (
        <p className="ms-muted">{block.note || 'Нет данных'}</p>
      ) : (
        <div className="ms-kv-grid ms-fact-block-grid">
          {block.lines.map((line, idx) => (
            <FragmentRow key={`${line.label || 'l'}-${idx}`} label={line.label} value={line.value} />
          ))}
        </div>
      )}
    </div>
  )
}

function FragmentRow({ label, value }: { label?: string; value?: string }) {
  return (
    <>
      <span className="ms-muted">{label || '—'}</span>
      <span>{value || '—'}</span>
    </>
  )
}

/** Map /clients/{id} detail → Facts panel payload (keeps AI prose + audit blocks). */
function factsFromDetail(detail: ClientDetail): ClientFacts {
  const blocks = detail.fact_blocks

  return {
    client_id: detail.client?.id,
    name: detail.client?.name,
    phone: detail.client?.phone,
    email: detail.client?.email,
    tg_nick: detail.client?.tg_nick,
    sales_type: detail.client?.sales_type,
    channels: detail.client?.channels,
    primary_channel: detail.client?.primary_channel || detail.messaging?.primary_channel,
    order_count: detail.stats?.order_count,
    avg_check: detail.stats?.avg_check,
    vip: detail.stats?.vip,
    loyalty_points: detail.stats?.loyalty_points,
    last_order: detail.stats?.last_order,
    tags: detail.client?.tags,
    event_tags: detail.client?.tag_buckets?.events,
    orders_preview: detail.orders,
    data_thin: detail.data_thin || detail.ai?.data_thin,
    recommendation: detail.ai?.recommendation || null,
    history_profile: detail.ai?.history_profile || null,
    occasion_intent: detail.ai?.occasion_intent || null,
    ai_source: detail.ai?.source || null,
    ai_model: detail.ai?.model || null,
    ai_provider: detail.ai?.provider || null,
    risks: detail.risks,
    block_history_profile: blocks?.history_profile,
    block_occasion_intent: blocks?.occasion_intent,
    block_risks: blocks?.risks,
    fact_blocks: blocks,
    conversation: detail.conversation
  }
}

function FactsPanel({
  facts,
  notes,
  sanity
}: {
  facts: ClientFacts | null
  notes?: string
  sanity?: SanityResult | null
}) {
  if (!facts) {
    return (
      <aside className="ms-facts-panel">
        <h3>Факты клиента</h3>
        <p className="ms-muted">
          Выберите клиента из аудитории или откройте черновик из карточки — здесь появятся заказы,
          чек и теги для сверки с AI-текстом.
        </p>
      </aside>
    )
  }

  const last = facts.last_order
  const historyProse = (facts.history_profile || '').trim()
  const occasionProse = (facts.occasion_intent || '').trim()
  const recommendation = (facts.recommendation || '').trim()
  const showDeepseekSummary =
    isDeepseekAiSource(facts) &&
    Boolean(historyProse || occasionProse || recommendation)

  const historyBlock =
    facts.block_history_profile || facts.fact_blocks?.history_profile || null

  const occasionBlock =
    facts.block_occasion_intent || facts.fact_blocks?.occasion_intent || null

  const risksBlock = facts.block_risks || facts.fact_blocks?.risks || null

  return (
    <aside className="ms-facts-panel">
      <h3>Факты · {facts.name || 'клиент'}</h3>
      {facts.data_thin ? <p className="ms-muted">Данных мало — текст должен быть осторожным.</p> : null}
      <div className="ms-kv-grid">
        <span className="ms-muted">Заказов</span>
        <span>{facts.order_count ?? 0}</span>
        <span className="ms-muted">Средний чек</span>
        <span>{money(facts.avg_check)}</span>
        <span className="ms-muted">Каналы</span>
        <span>{(facts.channels || []).join(', ') || facts.primary_channel || '—'}</span>
        <span className="ms-muted">Тип</span>
        <span>{facts.sales_type || '—'}</span>
        <span className="ms-muted">VIP</span>
        <span>{facts.vip ? 'да' : 'нет'}</span>
        <span className="ms-muted">Лояльность</span>
        <span>{facts.loyalty_points != null ? String(facts.loyalty_points) : '—'}</span>
        <span className="ms-muted">Телефон</span>
        <span>{facts.phone || '—'}</span>
        <span className="ms-muted">Telegram</span>
        <span>{facts.tg_nick || '—'}</span>
      </div>
      {showDeepseekSummary ? (
        <div className="ms-fact-block ms-facts-ai-summary">
          <p className="ms-ai-label">Саммари AI · DeepSeek</p>
          {historyProse ? (
            <>
              <p className="ms-ai-label">История и профиль клиента</p>
              <p className="ms-facts-rec">{historyProse}</p>
            </>
          ) : null}
          {occasionProse ? (
            <>
              <p className="ms-ai-label">Повод и intent покупки</p>
              <p className="ms-facts-rec">{occasionProse}</p>
            </>
          ) : null}
          {recommendation ? (
            <>
              <p className="ms-ai-label">Рекомендация AI</p>
              <p className="ms-facts-rec">{recommendation}</p>
            </>
          ) : null}
        </div>
      ) : null}
      {!showDeepseekSummary && !historyProse ? <FactBlockView block={historyBlock} /> : null}
      {!showDeepseekSummary && !occasionProse ? <FactBlockView block={occasionBlock} /> : null}
      <FactBlockView block={risksBlock} />
      <ConversationThread
        compact
        conversation={facts.conversation}
        title="TG conversation"
      />
      {last ? (
        <div className="ms-last-order">
          <strong>Последний заказ</strong>
          <div className="ms-muted">
            {(last.date || '').slice(0, 16).replace('T', ' ')} · {money(last.sum)}
            {last.channel ? ` · ${last.channel}` : ''}
          </div>
          {last.product_snippet || last.composition || (last.line_items || []).length ? (
            <OrderCompositionBits
              composition={last.composition}
              lineItems={last.line_items}
              productSnippet={last.product_snippet}
            />
          ) : null}
        </div>
      ) : null}
      <TagPills className="ms-tag-row ms-tag-event" items={facts.event_tags || []} />
      <TagPills items={facts.tags?.slice(0, 12)} />
      {(facts.orders_preview || []).length ? (
        <div className="ms-orders-list ms-facts-orders">
          {(facts.orders_preview || []).slice(0, 5).map((o, idx) => (
            <div className="ms-order-row" key={`${o.date || ''}-${idx}`}>
              <div className="ms-muted">
                {(o.date || '').slice(0, 16).replace('T', ' ')} · {money(o.sum)}
                {o.channel ? ` · ${o.channel}` : ''}
              </div>
              {o.product_snippet || o.composition || (o.line_items || []).length ? (
                <OrderCompositionBits
                  composition={o.composition}
                  lineItems={o.line_items}
                  productSnippet={o.product_snippet}
                />
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {sanity ? (
        <div className={`ms-sanity${sanity.ok ? ' is-ok' : ' is-bad'}`}>
          <p className="ms-ai-label">Проверка смысла</p>
          <p className="ms-muted">
            {sanity.ok
              ? 'Ок — явных конфликтов с долгом/рисками нет.'
              : (sanity.issues || []).join(' ') || 'Есть замечания к тексту.'}
            {sanity.auto_revised ? ' Текст автоматически скорректирован.' : ''}
          </p>
        </div>
      ) : null}
      {notes ? <p className="ms-muted ms-grounding">{notes}</p> : null}
      {facts.ai_source ? <p className="ms-muted">AI: {facts.ai_source}</p> : null}
    </aside>
  )
}

function OrderCompositionBits({
  composition,
  lineItems,
  productSnippet,
}: {
  composition?: string | null
  lineItems?: string[]
  productSnippet?: string | null
}) {
  const lines = (lineItems || []).map(x => String(x || '').trim()).filter(Boolean)
  const comp = String(composition || '').trim()
  const snippet = String(productSnippet || '').trim()
  if (comp || lines.length) {
    return (
      <div className="ms-order-composition">
        <span className="ms-muted">Состав: </span>
        {lines.length ? lines.join('; ') : comp}
      </div>
    )
  }
  if (snippet) {
    return <div>{snippet}</div>
  }

  return null
}

function orderPaymentLabel(status?: string | null, state?: string | null): string {
  const s = (status || '').trim().toLowerCase()
  const map: Record<string, string> = {
    paid: 'оплачен',
    unpaid: 'не оплачен',
    partial: 'частично',
    cancelled: 'отменён',
    failed: 'не состоялся'
  }
  const pay = map[s] || ''
  const st = (state || '').trim()
  if (pay && st && st.toLowerCase() !== pay) {return `${st} · ${pay}`}
  return st || pay || ''
}

function ClientCardModal({
  clientId,
  onClose,
  call
}: {
  clientId: string | null
  onClose: () => void
  call: Rest
}) {
  const [detail, setDetail] = useState<ClientDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [syncLoading, setSyncLoading] = useState(false)
  const aiProvider = MS_AI_DEFAULT_PROVIDER
  const aiModel = MS_AI_DEFAULT_MODEL
  const [error, setError] = useState('')
  const [ordersOpen, setOrdersOpen] = useState(true)
  const [note, setNote] = useState('')
  const [tgCheck, setTgCheck] = useState<{
    busy?: boolean
    exists?: boolean
    checked?: boolean
    chat_id?: string
    tg_nick?: string | null
    via?: string
    detail?: string
  } | null>(null)

  useEffect(() => {
    setTgCheck(null)
  }, [clientId])

  const runTgCheck = useCallback(async () => {
    if (!clientId) {return}
    setTgCheck({ busy: true })
    try {
      const data = await call<{
        exists?: boolean
        checked?: boolean
        chat_id?: string
        tg_nick?: string | null
        via?: string
        detail?: string
      }>(`/clients/${encodeURIComponent(clientId)}/telegram-check`, {
        method: 'POST',
        timeoutMs: 60_000
      })
      setTgCheck({ ...data, busy: false })
    } catch (err) {
      setTgCheck({
        busy: false,
        checked: false,
        detail: err instanceof Error ? err.message : String(err)
      })
    }
  }, [call, clientId])

  useEffect(() => {
    readMsAiChoice() // migrate stale localStorage → DeepSeek default
  }, [])

  useEffect(() => {
    if (!clientId) {return}
    let cancelled = false
    setLoading(true)
    setError('')
    setDetail(null)
    setOrdersOpen(true)
    setNote('')
    void call<ClientDetail>(`/clients/${encodeURIComponent(clientId)}`)
      .then(payload => {
        if (cancelled) {return}
        setDetail(payload)
        // Auto-renew DeepSeek when card has only heuristic / no LLM cache.
        const src = String(payload?.ai?.source || '')
        if (src !== 'llm') {
          void (async () => {
            setAiLoading(true)
            try {
              try {
                await call(`/clients/${encodeURIComponent(clientId)}/conversation/sync`, {
                  method: 'POST'
                })
              } catch {
                /* best-effort */
              }
              const aiPayload = await call<{ ai?: ClientDetail['ai'] }>(
                `/clients/${encodeURIComponent(clientId)}/ai`,
                {
                  method: 'POST',
                  body: {
                    provider: MS_AI_DEFAULT_PROVIDER,
                    model: MS_AI_DEFAULT_MODEL
                  }
                }
              )
              if (!cancelled) {
                setDetail(prev =>
                  prev ? { ...prev, ai: aiPayload.ai || prev.ai } : prev
                )
              }
            } catch (err) {
              if (!cancelled) {
                setError(err instanceof Error ? err.message : String(err))
              }
            } finally {
              if (!cancelled) {setAiLoading(false)}
            }
          })()
        }
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setLoading(false)}
      })

    return () => {
      cancelled = true
    }
  }, [call, clientId])

  // Keep «TG conversation» live while the card is open — inbound replies
  // appear without pressing Sync (same 30s cadence as ClientDialogModal).
  useEffect(() => {
    if (!clientId) {return}
    const timer = setInterval(() => {
      void call<{ conversation?: ClientConversation }>(
        `/clients/${encodeURIComponent(clientId)}/conversation/sync?refresh_ai=false`,
        { method: 'POST', timeoutMs: 90_000 }
      )
        .then(data => {
          if (data.conversation) {
            setDetail(prev =>
              prev ? { ...prev, conversation: data.conversation } : prev
            )
          }
        })
        .catch(() => undefined)
    }, 30_000)

    return () => clearInterval(timer)
  }, [call, clientId])

  if (!clientId) {return null}

  const client = detail?.client || {}
  const stats = detail?.stats || {}
  const orders = detail?.orders || []
  const ai = detail?.ai || {}
  const msg = detail?.messaging || {}
  const conversation = detail?.conversation
  const buckets = client.tag_buckets || {}
  const name = client.name || 'Клиент'
  const shownOrders = ordersOpen ? orders : orders.slice(0, 5)

  const refreshAi = async () => {
    setAiLoading(true)
    setError('')

    try {
      // Pull TG history first so recommendation can use the thread.
      try {
        await call(`/clients/${encodeURIComponent(clientId)}/conversation/sync`, {
          method: 'POST'
        })
      } catch {
        /* sync is best-effort — AI still runs on local/export thread */
      }
      const payload = await call<{ ai?: ClientDetail['ai'] }>(
        `/clients/${encodeURIComponent(clientId)}/ai`,
        {
          method: 'POST',
          body: {
            provider: aiProvider || undefined,
            model: aiModel || undefined
          }
        }
      )

      setDetail(prev => (prev ? { ...prev, ai: payload.ai || prev.ai } : prev))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAiLoading(false)
    }
  }

  const syncTelegramHistory = async () => {
    setSyncLoading(true)
    setError('')

    try {
      const data = await call<{
        conversation?: ClientConversation & {
          sync?: {
            imported?: number
            inbound_imported?: number
            matched_sessions?: number
            ok?: boolean
            reason?: string
            telegram_user?: { reason?: string; error?: string }
            gateway?: { reason?: string }
          }
        }
        ai?: ClientDetail['ai']
        ai_refreshed?: boolean
        ai_reason?: string
      }>(`/clients/${encodeURIComponent(clientId)}/conversation/sync`, {
        method: 'POST',
        body: {
          refresh_ai: true,
          provider: aiProvider || undefined,
          model: aiModel || undefined
        }
      })

      if (data.conversation || data.ai) {
        setDetail(prev =>
          prev
            ? {
                ...prev,
                conversation: data.conversation || prev.conversation,
                ai: data.ai || prev.ai
              }
            : prev
        )
        const sync = data.conversation?.sync
        const noPeer =
          sync?.reason === 'no_tg_nick_or_phone' ||
          sync?.telegram_user?.reason === 'no_tg_nick_or_phone'
        if (noPeer && !(sync?.imported || sync?.inbound_imported)) {
          setError('Нет ТГ ника / телефона — sync невозможен')
        } else if (sync && sync.ok === false && sync.reason && !data.ai_refreshed) {
          setError(`Sync: ${sync.reason}`)
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncLoading(false)
    }
  }

  const sendAndRecord = async (channel: 'whatsapp' | 'telegram') => {
    const text = note.trim()

    if (!text) {
      setError('Введите текст в поле ниже — он уйдёт в Telegram / историю.')

      return
    }

    setError('')

    try {
      const data = await call<{
        conversation?: ClientConversation
        deep_link?: string
        delivery?: { ok?: boolean; detail?: string; error?: string }
      }>(`/clients/${encodeURIComponent(clientId)}/conversation`, {
        method: 'POST',
        body: {
          text,
          direction: 'outbound',
          channel,
          source: 'client_card_send',
          open_deep_link: true
        }
      })

      if (data.conversation) {
        setDetail(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
      }

      if (channel === 'telegram' && data.delivery?.ok) {
        setNote('')

        return
      }

      if (channel === 'telegram' && data.delivery && data.delivery.ok === false && !data.delivery.error?.includes('skipped')) {
        const detail = data.delivery.detail || data.delivery.error || 'не отправлено'
        setError(`Telegram Bot: ${detail}. Откроется deep-link, если есть.`)
      }

      if (data.deep_link) {window.open(data.deep_link, '_blank', 'noopener')}
      setNote('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div
      className="ms-modal-backdrop"
      onClick={e => {
        if (e.target === e.currentTarget) {onClose()}
      }}
    >
      <div aria-modal="true" className="ms-modal ms-client-card" role="dialog">
        <div className="ms-card-head">
          <h3>Заказы · {name}</h3>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        {error ? (
          <MsErrorModal message={error} onClose={() => setError('')} />
        ) : null}
        {loading ? (
          <p className="ms-muted">Загрузка карточки…</p>
        ) : (
          <div className="ms-card-body">
            <section className="ms-card-section">
              <div className="ms-card-name">{name}</div>
              <div className="ms-muted">
                {(client.role || '—') +
                  (client.sex ? ` · ${client.sex}` : '') +
                  (client.state ? ` · ${client.state}` : '')}
              </div>
              <TagPills
                className="ms-tag-row ms-tag-channel"
                items={[...(buckets.marketplace || []), ...(client.channels || [])]}
              />
              <TagPills items={[...(buckets.loyalty || []), ...(buckets.other || [])]} />
              <TagPills className="ms-tag-row ms-tag-event" items={buckets.events || []} />
            </section>
            <section className="ms-card-section">
              <h4>Контакты</h4>
              <div className="ms-kv-grid">
                <span className="ms-muted">Телефон</span>
                <span>{client.phone || '—'}</span>
                <span className="ms-muted">Email</span>
                <span>{client.email || '—'}</span>
                <span className="ms-muted">Telegram</span>
                <span>{client.tg_nick || '—'}</span>
                <span className="ms-muted">Тип</span>
                <span>{client.company_type || '—'}</span>
                <span className="ms-muted">Осн. канал</span>
                <span>{client.primary_channel || msg.primary_channel || '—'}</span>
                <span className="ms-muted">Есть в TG</span>
                <span>
                  {tgCheck?.busy ? (
                    'проверяем…'
                  ) : tgCheck?.checked ? (
                    tgCheck.exists ? (
                      `да${tgCheck.tg_nick ? ` · @${String(tgCheck.tg_nick).replace(/^@/, '')}` : ''}${tgCheck.via ? ` (${tgCheck.via})` : ''}`
                    ) : (
                      `нет — ${tgCheck.detail || 'не найден'}`
                    )
                  ) : tgCheck?.detail ? (
                    tgCheck.detail
                  ) : (
                    <button
                      className="ms-link-btn"
                      onClick={() => void runTgCheck()}
                      title="Резолв @ника / телефона через личный Telegram (MTProto), fallback — Business bot"
                      type="button"
                    >
                      Проверить
                    </button>
                  )}
                </span>
              </div>
            </section>
            <section className="ms-card-section">
              <div className="ms-card-head">
                <h4>Переписка</h4>
                <button
                  className="ms-btn"
                  disabled={syncLoading}
                  onClick={() => void syncTelegramHistory()}
                  title="Подтянуть входящие/исходящие из gateway + личного Telegram; обновить рекомендации AI"
                  type="button"
                >
                  {syncLoading ? 'Sync + AI…' : 'Sync Telegram'}
                </button>
              </div>
              <ConversationThread conversation={conversation} title="TG conversation" />
            </section>
            <section className="ms-card-section">
              <h4>Статистика</h4>
              <div className="ms-stats-grid">
                <div>
                  <div className="ms-stat-val">{money(stats.avg_check)}</div>
                  <div className="ms-muted">Средний чек</div>
                </div>
                <div>
                  <div className="ms-stat-val">{String(stats.order_count || 0)}</div>
                  <div className="ms-muted">
                    Заказов
                    {stats.paid_order_count != null || stats.cancelled_order_count != null
                      ? ` · оплач. ${stats.paid_order_count ?? 0}` +
                        (stats.cancelled_order_count
                          ? ` · отм. ${stats.cancelled_order_count}`
                          : '')
                      : ''}
                  </div>
                </div>
                <div>
                  <div className="ms-stat-val">{stats.vip ? 'да' : 'нет'}</div>
                  <div className="ms-muted">ВИП</div>
                </div>
                <div>
                  <div className="ms-stat-val">
                    {stats.loyalty_points != null ? String(stats.loyalty_points) : '—'}
                  </div>
                  <div className="ms-muted">Лояльность</div>
                </div>
              </div>
              {stats.last_order ? (
                <div className="ms-last-order">
                  <strong>Последний заказ</strong>
                  <div className="ms-muted">
                    {(stats.last_order.date || '').slice(0, 16).replace('T', ' ')} ·{' '}
                    {money(stats.last_order.sum)}
                    {stats.last_order.channel ? ` · ${stats.last_order.channel}` : ''}
                    {orderPaymentLabel(stats.last_order.payment_status, stats.last_order.state)
                      ? ` · ${orderPaymentLabel(stats.last_order.payment_status, stats.last_order.state)}`
                      : ''}
                  </div>
                  {stats.last_order.product_snippet ||
                  stats.last_order.composition ||
                  (stats.last_order.line_items || []).length ? (
                    <OrderCompositionBits
                      composition={stats.last_order.composition}
                      lineItems={stats.last_order.line_items}
                      productSnippet={stats.last_order.product_snippet}
                    />
                  ) : null}
                </div>
              ) : null}
            </section>
            <section className="ms-card-section">
              <button className="ms-section-toggle" onClick={() => setOrdersOpen(v => !v)} type="button">
                Все заказы ({orders.length}) {ordersOpen ? '▾' : '▸'}
              </button>
              <div className="ms-orders-list">
                {shownOrders.length ? (
                  shownOrders.map((o, idx) => {
                    const statusLabel = orderPaymentLabel(o.payment_status, o.state)
                    const cancelled =
                      (o.payment_status || '').toLowerCase() === 'cancelled' ||
                      /отмен/i.test(o.state || '')

                    return (
                    <div
                      className={`ms-order-row${cancelled ? ' is-cancelled' : ''}`}
                      key={`${o.id || ''}-${idx}`}
                    >
                      <strong>{o.name || o.id || 'Заказ'}</strong>
                      {statusLabel ? (
                        <span className={`ms-order-status${cancelled ? ' is-cancelled' : ''}`}>
                          {statusLabel}
                        </span>
                      ) : null}
                      <div className="ms-muted">
                        {(o.date || '').slice(0, 16).replace('T', ' ')} · {money(o.sum)}
                        {o.channel ? ` · ${o.channel}` : ''}
                      </div>
                      {o.product_snippet || o.composition || (o.line_items || []).length ? (
                        <OrderCompositionBits
                          composition={o.composition}
                          lineItems={o.line_items}
                          productSnippet={o.product_snippet}
                        />
                      ) : null}
                    </div>
                    )
                  })
                ) : (
                  <p className="ms-muted">Заказов в кэше нет.</p>
                )}
              </div>
            </section>
            <section className="ms-card-section">
              <div className="ms-card-head">
                <h4>Саммари AI · DeepSeek</h4>
                <div className="ms-chips">
                  <button className="ms-btn" disabled={aiLoading} onClick={() => void refreshAi()} type="button">
                    {aiLoading ? 'Генерация…' : 'Обновить AI'}
                  </button>
                </div>
              </div>
              <p className="ms-muted">
                Модель по умолчанию: DeepSeek ({MS_AI_DEFAULT_MODEL})
              </p>
              {ai.data_thin ? <p className="ms-muted">Данных мало — выводы осторожные.</p> : null}
              {/deepseek/i.test(String(ai.model || aiModel || '')) || ai.source === 'llm' ? (
                <>
                  <p className="ms-ai-label">История и профиль</p>
                  <p>{ai.history_profile || '—'}</p>
                  <p className="ms-ai-label">Повод и intent покупки</p>
                  <p>{ai.occasion_intent || '—'}</p>
                  <h4>Рекомендация AI</h4>
                  <p>{ai.recommendation || '—'}</p>
                </>
              ) : (
                <p className="ms-muted">
                  Нажмите «Обновить AI» — саммари только через DeepSeek.
                </p>
              )}
              <p className="ms-muted">
                Источник: {ai.source || '—'}
                {ai.model || aiModel ? ` · ${ai.model || aiModel}` : ''}
              </p>
            </section>
            <section className="ms-card-section">
              <h4>Быстрые действия</h4>
              <div className="ms-quick-actions">
                <button
                  className="ms-btn"
                  onClick={() =>
                    setNote(
                      `Напоминание: связаться с ${name} (~5 дней до повода). Чек ≈ ${money(stats.avg_check)}`
                    )
                  }
                  type="button"
                >
                  Напоминание
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={!note.trim()}
                  onClick={() => void sendAndRecord('whatsapp')}
                  type="button"
                >
                  WhatsApp → история
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={!note.trim()}
                  onClick={() => void sendAndRecord('telegram')}
                  type="button"
                >
                  Telegram → история
                </button>
                <button
                  className="ms-btn"
                  onClick={() => {
                    setOrdersOpen(true)
                    setNote(`События: ${(buckets.events || []).join(', ') || 'нет тегов событий'}`)
                  }}
                  type="button"
                >
                  События
                </button>
                <button
                  className="ms-btn ms-btn-primary"
                  onClick={() => {
                    const sales =
                      (client.sales_type || '').toLowerCase().includes('маркет')
                        ? 'marketplace'
                        : (client.sales_type || '').toLowerCase().includes('прям')
                          ? 'direct'
                          : 'all'

                    writeDraftPrefill({
                      clientId,
                      channel: channelFromMessaging(msg.primary_channel || client.primary_channel),
                      salesFilter: sales
                    })
                    onClose()
                    host.navigate('/campaigns')
                  }}
                  type="button"
                >
                  Черновик рассылки
                </button>
              </div>
              <label className="ms-send-note">
                Текст для отправки / записи в историю
                <textarea
                  onChange={e => setNote(e.target.value)}
                  placeholder="Напишите исходящее — кнопка WhatsApp/Telegram запишет его в TG conversation и откроет чат…"
                  rows={3}
                  value={note}
                />
              </label>
              <p className="ms-muted">{msg.hint || ''}</p>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}

const CLIENTS_PAGE_SIZE = 100
const CLIENTS_LOCAL_CACHE_PREFIX = 'hermes.moysklad.clients.v4:'
const CLIENTS_LOCAL_CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
const CLIENTS_REVALIDATE_POLL_MS = 4000
const CLIENTS_REVALIDATE_POLL_MAX_MS = 90_000
/** Catalog filter can exceed default 15s desktop REST timeout → Chromium abort modal. */
const CLIENTS_FETCH_TIMEOUT_MS = 90_000
/** First audience paint — match backend PAGE_SNAPSHOT_ROWS (fast snapshot path). */
const AUDIENCE_PAGE_SIZE = 100
/**
 * Scroll/load-more batches. Backend grows a Redis/file page window so appends
 * hit snapshot when possible; otherwise partial catalog flushes while MoySklad
 * still downloads (~10k: «Подгружаем… 100 / 9507» must not block on full rebuild).
 */
const AUDIENCE_APPEND_PAGE_SIZE = 250
/** Cap localStorage audience cache growth after appends. */
const AUDIENCE_LOCAL_CACHE_ROWS = 500
/**
 * Auto infinite-scroll stops here — rest only via «Ещё клиенты».
 * Loading all ~9k chips is pointless for outreach (pick one client) and
 * looks like a hung «Подгружаем… 500 / 8952» worker job.
 */
const AUDIENCE_AUTO_SCROLL_CAP = 400

interface ClientsLocalCachePayload {
  saved_at: number
  sales_filter: string
  q: string
  group: string
  group_source: string
  clients: ClientRow[]
  counts: Counts | null
  matched_total: number
  has_more: boolean
  next_offset: number
  group_options_ms: GroupChipOption[]
  group_options_ai: GroupChipOption[]
  synced_at_label: string
  from_cache: boolean
}

function clientsLocalCacheKey(parts: {
  salesFilter: string
  q: string
  group: string
  groupSource: string
}): string {
  return (
    CLIENTS_LOCAL_CACHE_PREFIX +
    [parts.salesFilter, parts.groupSource, parts.group, parts.q].join('|')
  )
}

function readClientsLocalCache(key: string): ClientsLocalCachePayload | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) {return null}
    const parsed = JSON.parse(raw) as ClientsLocalCachePayload
    if (!parsed || !Array.isArray(parsed.clients) || !parsed.clients.length) {
      return null
    }
    if (
      typeof parsed.saved_at !== 'number' ||
      Date.now() - parsed.saved_at > CLIENTS_LOCAL_CACHE_MAX_AGE_MS
    ) {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writeClientsLocalCache(key: string, payload: ClientsLocalCachePayload): void {
  try {
    localStorage.setItem(key, JSON.stringify(payload))
  } catch {
    // Quota / private mode — ignore; network path still works.
  }
}

/** Instant paint: exact key, else filter unfiltered local snapshot by group+q. */
function seedClientsLocalPayload(parts: {
  salesFilter: string
  q: string
  group: string
  groupSource: string
}): ClientsLocalCachePayload | null {
  return pickLocalClientsSeed({
    q: parts.q,
    group: parts.group,
    groupSource: parts.groupSource,
    readExact: () =>
      readClientsLocalCache(
        clientsLocalCacheKey({
          salesFilter: parts.salesFilter,
          q: parts.q,
          group: parts.group,
          groupSource: parts.groupSource
        })
      ),
    readBase: () =>
      readClientsLocalCache(
        clientsLocalCacheKey({
          salesFilter: parts.salesFilter,
          q: '',
          group: parts.group,
          groupSource: parts.groupSource
        })
      ),
    readUnfilteredBases: () => [
      readClientsLocalCache(
        clientsLocalCacheKey({
          salesFilter: parts.salesFilter,
          q: '',
          group: '',
          groupSource: 'any'
        })
      ),
      readClientsLocalCache(
        clientsLocalCacheKey({
          salesFilter: 'all',
          q: '',
          group: '',
          groupSource: 'any'
        })
      )
    ],
    filterRows: (seed, q, group, groupSource) => {
      const clients = filterClientRowsByAudience(seed.clients, {
        q,
        group,
        groupSource,
        salesFilter: parts.salesFilter
      })
      if (!clients.length) {
        return null
      }
      return {
        ...seed,
        q,
        group,
        group_source: groupSource,
        sales_filter: parts.salesFilter,
        clients,
        matched_total: clients.length,
        has_more: Boolean(seed.has_more),
        next_offset: clients.length,
        from_cache: true
      }
    }
  })
}

function findGroupChipCount(
  group: string,
  groupSource: string,
  ms: GroupChipOption[],
  ai: GroupChipOption[]
): number | null {
  const name = String(group || '').trim()
  if (!name) {
    return null
  }
  const src = String(groupSource || 'any').toLowerCase()
  const pool = src === 'ai' ? ai : src === 'ms' ? ms : [...ms, ...ai]
  const hit = pool.find(opt => opt.name === name)
  return hit && typeof hit.count === 'number' ? hit.count : null
}

/** A repaint must not blank «TG conversation»: keep the previous non-empty
 *  preview when the incoming copy of the same row arrives without one
 *  (stale server snapshot / local cache racing the enriched page). */
function preserveConversationPreviews(prev: ClientRow[], next: ClientRow[]): ClientRow[] {
  if (!prev.length) {return next}

  const byId = new Map(prev.map(r => [String(r.id || ''), r]))

  return next.map(row => {
    const old = byId.get(String(row.id || ''))

    if (!old) {return row}
    const incoming = row.tg_conversation_preview || row.tg_conversation || ''
    const previous = old.tg_conversation_preview || old.tg_conversation || ''

    if (incoming || !previous) {return row}

    return {
      ...row,
      tg_conversation_preview: old.tg_conversation_preview,
      tg_conversation: row.tg_conversation || old.tg_conversation
    }
  })
}

function mergeClientPages(prev: ClientRow[], incoming: ClientRow[]): ClientRow[] {
  const seen = new Set<string>()
  const out: ClientRow[] = []

  for (const row of [...prev, ...incoming]) {
    const id = String(row.id || '').trim()

    if (id) {
      if (seen.has(id)) {continue}
      seen.add(id)
    }

    out.push(row)
  }

  return out
}

function ClientsPage() {
  const call = useMsRest()
  const [salesFilter, setSalesFilter] = useState('all')
  const [q, setQ] = useState('')
  const [qDebounced, setQDebounced] = useState('')
  const [group, setGroup] = useState('')
  const [groupSource, setGroupSource] = useState<'any' | 'ms' | 'ai'>('any')
  const [stage, setStage] = useState<StageKey>('all')
  const [entityType, setEntityType] = useState<'all' | 'individual' | 'legal'>('all')
  const [loyaltyOnly, setLoyaltyOnly] = useState(false)
  const [stageCounts, setStageCounts] = useState<StageCounts | null>(null)
  const [stageTagStatus, setStageTagStatus] = useState('')
  const [stageTagBusy, setStageTagBusy] = useState(false)
  const [stageTagArmed, setStageTagArmed] = useState(false)
  const initialLocal = (() => {
    if (typeof localStorage === 'undefined') {return null}
    return readClientsLocalCache(
      clientsLocalCacheKey({
        salesFilter: 'all',
        q: '',
        group: '',
        groupSource: 'any'
      })
    )
  })()
  const [loading, setLoading] = useState(!initialLocal)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [clients, setClients] = useState<ClientRow[]>(() => initialLocal?.clients || [])
  const [counts, setCounts] = useState<Counts | null>(() => initialLocal?.counts || null)
  const [matched, setMatched] = useState(() => initialLocal?.matched_total || 0)
  const [hasMore, setHasMore] = useState(() => Boolean(initialLocal?.has_more))
  const [nextOffset, setNextOffset] = useState(() => initialLocal?.next_offset || 0)
  const [groupOptionsMs, setGroupOptionsMs] = useState<GroupChipOption[]>(
    () => initialLocal?.group_options_ms || []
  )
  const [groupOptionsAi, setGroupOptionsAi] = useState<GroupChipOption[]>(
    () => mergeAiGroupOptions(initialLocal?.group_options_ai || [])
  )
  const [integrityNote, setIntegrityNote] = useState('')
  const [auditOpen, setAuditOpen] = useState(false)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditError, setAuditError] = useState('')
  const [audit, setAudit] = useState<AuditReport | null>(null)
  const [tgImportBusy, setTgImportBusy] = useState(false)
  const [tgImportNote, setTgImportNote] = useState('')
  const [tgDialogSyncBusy, setTgDialogSyncBusy] = useState(false)
  const [tgPhoneVerifyBusy, setTgPhoneVerifyBusy] = useState(false)

  /** Bulk-sync personal TG dialogs → «TG conversation»; poll until done. */
  const runTgDialogSync = async () => {
    setTgDialogSyncBusy(true)
    setTgImportNote('Синхронизация личных TG-диалогов запущена…')
    try {
      await call('/clients/conversations/telegram-sync?limit=150', {
        method: 'POST'
      })
      for (let i = 0; i < 120; i += 1) {
        await new Promise(r => setTimeout(r, 5000))
        const data = await call<{
          state?: {
            running?: boolean
            error?: string | null
            stats?: {
              matched?: number
              synced?: number
              imported?: number
              inbound_imported?: number
              skipped_fresh?: number
              pending_left?: number
            } | null
          }
        }>('/clients/conversations/telegram-sync')
        const state = data.state
        if (state?.running) {
          continue
        }
        if (state?.error) {
          setTgImportNote(`TG диалоги: ${state.error}`)
        } else {
          const s = state?.stats || {}
          setTgImportNote(
            `TG диалоги: совпало ${s.matched ?? 0} · синхронизировано ${s.synced ?? 0}` +
              ` · сообщений ${s.imported ?? 0}` +
              ((s.inbound_imported ?? 0) > 0 ? ` (входящих ${s.inbound_imported})` : '') +
              ((s.pending_left ?? 0) > 0 ? ` · в очереди ещё ${s.pending_left}` : '') +
              ((s.skipped_fresh ?? 0) > 0 ? ` · свежих пропущено ${s.skipped_fresh}` : '')
          )
          await load({ refresh: false })
        }
        break
      }
    } catch (err) {
      setTgImportNote(
        `TG диалоги: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setTgDialogSyncBusy(false)
    }
  }

  /** Match колонка Телефон → Telegram contacts, then live ImportContacts. */
  const runTgPhoneVerify = async () => {
    setTgPhoneVerifyBusy(true)
    setTgImportNote('Проверка Telegram по телефонам запущена…')
    try {
      await call('/clients/telegram-verify?live=true&limit=400&delay_ms=400', {
        method: 'POST'
      })
      for (let i = 0; i < 180; i += 1) {
        await new Promise(r => setTimeout(r, 4000))
        const data = await call<{
          state?: {
            running?: boolean
            error?: string | null
            stats?: {
              cache?: { scanned?: number; matched?: number; contacts_with_phone?: number }
              live?: { active?: number; inactive?: number; skipped?: number }
              live_queued?: number
            } | null
          }
        }>('/clients/telegram-verify')
        const state = data.state
        if (state?.running) {
          continue
        }
        if (state?.error) {
          setTgImportNote(`TG телефоны: ${state.error}`)
        } else {
          const cache = state?.stats?.cache || {}
          const live = state?.stats?.live || {}
          setTgImportNote(
            `TG по телефону: в кэше контактов ${cache.contacts_with_phone ?? 0}` +
              ` · совпало ${cache.matched ?? 0}` +
              ` · live OK ${live.active ?? 0} / нет ${live.inactive ?? 0}` +
              ((live.skipped ?? 0) > 0 ? ` · skip ${live.skipped}` : '')
          )
          await load({ refresh: false })
        }
        break
      }
    } catch (err) {
      setTgImportNote(
        `TG телефоны: ${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      setTgPhoneVerifyBusy(false)
    }
  }
  const [syncedLabel, setSyncedLabel] = useState(() => initialLocal?.synced_at_label || '')
  const [fromCache, setFromCache] = useState(() => Boolean(initialLocal?.from_cache ?? initialLocal))
  const [staleHint, setStaleHint] = useState(false)
  const [cardClientId, setCardClientId] = useState<string | null>(null)
  /** Click on «TG conversation» cell → in-app dialog (history + reply). */
  const [tgDialog, setTgDialog] = useState<{ id: string; name?: string } | null>(null)
  const [recalcOpen, setRecalcOpen] = useState(false)
  const [recalcLoading, setRecalcLoading] = useState(false)
  const [recalcGroups, setRecalcGroups] = useState('')
  const [recalcSource, setRecalcSource] = useState('')

  const [recalcPreview, setRecalcPreview] = useState<{ changed?: number; total?: number } | null>(
    null
  )

  const [recalcError, setRecalcError] = useState('')
  const [aiFillStatus, setAiFillStatus] = useState('')
  const [columnSort, setColumnSort] = useState<ColumnSortSpec | null>(null)
  const [columnFilters, setColumnFilters] = useState<Partial<Record<ClientColKey, ColumnFilterSpec>>>(
    {}
  )
  const [openFilterKey, setOpenFilterKey] = useState<ClientColKey | null>(null)
  const loadGen = useRef(0)
  const loadingMoreRef = useRef(false)
  const lazyAiTriedRef = useRef(new Set<string>())
  const lazyAiInFlightRef = useRef(false)
  const clientsRef = useRef<ClientRow[]>([])
  clientsRef.current = clients

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q.trim()), 280)
    return () => clearTimeout(t)
  }, [q])

  const displayClients = useMemo(
    () => applyClientColumnFilters(clients, columnFilters, columnSort),
    [clients, columnFilters, columnSort]
  )

  const columnUniques = useMemo(() => {
    const map: Partial<Record<ClientColKey, string[]>> = {}
    for (const col of CLIENT_COLUMNS) {
      map[col.key] = uniqueColumnValues(clients, col)
    }
    return map
  }, [clients])

  const columnFilterActive = useMemo(
    () =>
      CLIENT_COLUMNS.some(col => {
        const f = columnFilters[col.key]
        return Boolean(f && (f.query.trim() || f.selected != null))
      }),
    [columnFilters]
  )

  /** Auto-fill empty CRM fields for every currently shown row (batched). */
  const drainLazyAiFill = useCallback(() => {
    if (lazyAiInFlightRef.current) {
      return
    }

    const pending = clientsRef.current
      .filter(rowNeedsLazyAiFill)
      .map(r => String(r.id || '').trim())
      .filter(id => id && !lazyAiTriedRef.current.has(id))

    if (!pending.length) {
      return
    }

    const ids = pending.slice(0, LAZY_AI_BATCH)

    for (const id of ids) {
      lazyAiTriedRef.current.add(id)
    }

    lazyAiInFlightRef.current = true
    const shown = clientsRef.current.length
    setAiFillStatus(`AI: заполняю ${ids.length} из ${shown} показанных…`)

    void call<{
      updated?: number
      cached?: number
      filled_field_count?: number
      source?: string
      ai_fill_cache_backend?: string
      cache_backend?: string
      results?: Array<{
        id?: string
        filled?: Record<string, unknown>
        fields?: Record<string, unknown>
        ai_fields?: string[]
        source?: string
      }>
    }>('/clients/ai-fill', {
      method: 'POST',
      body: {
        ids,
        limit: ids.length,
        use_llm: true,
        force: false
      },
      timeoutMs: 120_000
    })
      .then(data => {
        setClients(prev => applyAiFillResults(prev, data.results || []))
        const backend = data.ai_fill_cache_backend || data.cache_backend || ''
        const left = Math.max(0, pending.length - ids.length)
        setAiFillStatus(
          `✓ AI: +${data.updated || 0}` +
            (data.cached ? ` · кэш ${data.cached}` : '') +
            (data.filled_field_count ? ` · полей ${data.filled_field_count}` : '') +
            (backend ? ` · ${backend}` : '') +
            (left ? ` · ещё ${left}…` : ` · показано ${shown}`)
        )
      })
      .catch(err => {
        // Keep ids in tried to avoid tight retry loops; filter/refresh clears tried.
        setAiFillStatus(`AI ошибка: ${err instanceof Error ? err.message : String(err)}`)
      })
      .finally(() => {
        lazyAiInFlightRef.current = false
        // Next batch for remaining shown rows (e.g. 50→100 after scroll).
        queueMicrotask(() => drainLazyAiFill())
      })
  }, [call])

  const load = useCallback(
    async (opts?: { refresh?: boolean; append?: boolean; offset?: number }) => {
      const append = Boolean(opts?.append)
      const offset = append ? (opts?.offset ?? nextOffset) : 0
      const gen = append ? loadGen.current : ++loadGen.current
      const cacheKey = clientsLocalCacheKey({ salesFilter, q: qDebounced, group, groupSource })

      if (append) {
        if (loadingMoreRef.current || !hasMore) {return}
        loadingMoreRef.current = true
        setLoadingMore(true)
      } else {
        // CDN-style: paint local snapshot immediately, then revalidate.
        // On q miss, filter empty-q / all-tab cache so «Павел» is instant.
        if (!opts?.refresh) {
          const local = seedClientsLocalPayload({
            salesFilter,
            q: qDebounced,
            group,
            groupSource
          })
          if (local) {
            setClients(local.clients)
            setCounts(local.counts)
            setMatched(local.matched_total || 0)
            setNextOffset(local.next_offset || local.clients.length)
            setHasMore(Boolean(local.has_more))
            setGroupOptionsMs(local.group_options_ms || [])
            setGroupOptionsAi(mergeAiGroupOptions(local.group_options_ai || []))
            setFromCache(true)
            // Local paint is a snapshot — do not sticky-spin «обновляем…» until server says so.
            setStaleHint(false)
            setSyncedLabel(local.synced_at_label || '')
            setLoading(false)
          } else {
            setClients([])
            setCounts(null)
            setMatched(0)
            setHasMore(false)
            setNextOffset(0)
            setLoading(true)
            setStaleHint(false)
          }
        } else {
          setLoading(true)
          setStaleHint(false)
        }
        setError('')
        lazyAiTriedRef.current.clear()
        setAiFillStatus('')
      }

      try {
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          q: qDebounced,
          group,
          group_source: groupSource,
          stage,
          entity_type: entityType,
          limit: String(CLIENTS_PAGE_SIZE),
          offset: String(offset)
        })

        if (loyaltyOnly) {params.set('loyalty_only', 'true')}

        if (opts?.refresh) {params.set('refresh', 'true')}

        const data = await call<{
          clients?: ClientRow[]
          counts?: Counts
          matched_total?: number
          has_more?: boolean
          next_offset?: number
          returned?: number
          group_options?: GroupChipOption[]
          group_options_by_source?: { ms?: GroupChipOption[]; ai?: GroupChipOption[] }
          stage_counts?: StageCounts
          cached?: boolean
          stale?: boolean
          revalidating?: boolean
          snapshot?: boolean
          synced_at_label?: string
          synced_at?: number
        }>(`/clients?${params}`, { timeoutMs: CLIENTS_FETCH_TIMEOUT_MS })

        if (gen !== loadGen.current) {return}
        const page = data.clients || []
        setClients(prev =>
          append ? mergeClientPages(prev, page) : preserveConversationPreviews(prev, page)
        )
        setCounts(data.counts || null)
        setMatched(data.matched_total || 0)
        if (data.stage_counts) {setStageCounts(data.stage_counts)}

        const computedNext =
          data.next_offset != null ? data.next_offset : offset + page.length

        setNextOffset(computedNext)
        setHasMore(
          data.has_more != null
            ? Boolean(data.has_more)
            : computedNext < (data.matched_total || 0)
        )

        if (!append) {
          const { ms: msOpts, ai: aiOpts } = resolveGroupOptionsBySource(data)
          setGroupOptionsMs(msOpts)
          setGroupOptionsAi(aiOpts)
          setFromCache(Boolean(data.cached || data.snapshot))
          // «обновляем…» only while server is actually rebuilding — not for every stale/snapshot paint.
          setStaleHint(Boolean(data.revalidating))
          setSyncedLabel(data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''))
          if (data.counts) {
            const t = data.counts.total || 0
            const d = data.counts.direct || 0
            const m = data.counts.marketplace || 0
            setIntegrityNote(
              t === d + m
                ? `Вкладки: ${d}+${m}=${t} (partition OK)`
                : `⚠ вкладки ${d}+${m}≠${t}`
            )
          }
          writeClientsLocalCache(cacheKey, {
            saved_at: Date.now(),
            sales_filter: salesFilter,
            q: qDebounced,
            group,
            group_source: groupSource,
            clients: page,
            counts: data.counts || null,
            matched_total: data.matched_total || 0,
            has_more:
              data.has_more != null
                ? Boolean(data.has_more)
                : computedNext < (data.matched_total || 0),
            next_offset: computedNext,
            group_options_ms: msOpts,
            group_options_ai: aiOpts,
            synced_at_label:
              data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''),
            from_cache: Boolean(data.cached)
          })
          // Keep empty-q base warm so the next search filters instantly.
          if (!qDebounced.trim()) {
            writeClientsLocalCache(
              clientsLocalCacheKey({
                salesFilter,
                q: '',
                group,
                groupSource
              }),
              {
                saved_at: Date.now(),
                sales_filter: salesFilter,
                q: '',
                group,
                group_source: groupSource,
                clients: page,
                counts: data.counts || null,
                matched_total: data.matched_total || 0,
                has_more:
                  data.has_more != null
                    ? Boolean(data.has_more)
                    : computedNext < (data.matched_total || 0),
                next_offset: computedNext,
                group_options_ms: msOpts,
                group_options_ai: aiOpts,
                synced_at_label:
                  data.synced_at_label || (data.synced_at ? String(data.synced_at) : ''),
                from_cache: Boolean(data.cached)
              }
            )
          }
        }
      } catch (err) {
        if (gen !== loadGen.current) {return}
        // Superseded search / desktop REST abort must not block filtering.
        if (isBenignRequestAbort(err)) {
          return
        }
        setError(err instanceof Error ? err.message : String(err))

        // Keep painted local/stale rows on soft refresh failure.
        if (!append && !clientsRef.current.length) {setClients([])}
      } finally {
        if (append) {
          loadingMoreRef.current = false
          setLoadingMore(false)
        } else if (gen === loadGen.current) {
          setLoading(false)
        }
      }
    },
    [call, entityType, group, groupSource, hasMore, loyaltyOnly, nextOffset, qDebounced, salesFilter, stage]
  )

  /** Two steps on purpose: dry-run first, write only on a second, armed click. */
  const markFailedStage = useCallback(async () => {
    setStageTagBusy(true)
    setError('')
    try {
      const write = stageTagArmed
      const data = await call<{
        ok?: boolean
        total?: number
        changed?: number
        tag?: string
        push?: { pushed?: number; errors?: { id?: string; error?: string }[] }
      }>('/clients/stage/failed-tag', {
        method: 'POST',
        body: { sales_filter: salesFilter, q: qDebounced, dry_run: !write },
        timeoutMs: 600_000
      })

      if (!write) {
        const n = data.changed || 0
        setStageTagArmed(n > 0)
        setStageTagStatus(
          n > 0
            ? `Проставим тег «${data.tag || 'не состоялся'}» ${n} клиентам (из ${data.total || 0}). Нажмите ещё раз — запишу в МойСклад.`
            : 'Тег уже стоит у всех в этой выборке — писать нечего.'
        )
      } else {
        const pushed = data.push?.pushed || 0
        const failed = data.push?.errors?.length || 0
        setStageTagArmed(false)
        setStageTagStatus(
          `Записано в МойСклад: ${pushed}${failed ? ` · ошибок ${failed}` : ''}`
        )
        await load({ refresh: true })
      }
    } catch (err) {
      setStageTagArmed(false)
      setStageTagStatus('')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStageTagBusy(false)
    }
  }, [call, load, qDebounced, salesFilter, stageTagArmed])

  // Re-arm from scratch whenever the audience changes.
  useEffect(() => {
    setStageTagArmed(false)
    setStageTagStatus('')
  }, [salesFilter, qDebounced, stage])

  useEffect(() => {
    void load()
  }, [salesFilter, group, groupSource, qDebounced, stage, entityType, loyaltyOnly]) // eslint-disable-line react-hooks/exhaustive-deps -- reset list on filter change

  // Drop Excel column filters when the main audience filter changes.
  useEffect(() => {
    setColumnFilters({})
    setColumnSort(null)
    setOpenFilterKey(null)
  }, [salesFilter, group, groupSource, qDebounced, stage, entityType, loyaltyOnly])

  // While server rebuilds in background, poll until fresh (clears sticky «обновляем…»).
  useEffect(() => {
    if (!staleHint || loading) {
      return
    }

    const started = Date.now()
    const timer = window.setInterval(() => {
      if (Date.now() - started > CLIENTS_REVALIDATE_POLL_MAX_MS) {
        window.clearInterval(timer)
        setStaleHint(false)
        return
      }
      void load()
    }, CLIENTS_REVALIDATE_POLL_MS)

    return () => window.clearInterval(timer)
  }, [staleHint, loading, load])

  // Lazy AI: whenever shown set grows/changes, fill empty cells for those rows.
  useEffect(() => {
    if (!clients.length || loading) {
      return
    }

    drainLazyAiFill()
  }, [clients, drainLazyAiFill, loading])

  const onTableScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget

      if (el.scrollHeight - el.scrollTop - el.clientHeight > 160) {return}

      if (!hasMore || loading || loadingMoreRef.current) {return}
      void load({ append: true, offset: nextOffset })
    },
    [hasMore, load, loading, nextOffset]
  )

  const cacheHint = syncedLabel
    ? `${
        staleHint
          ? 'снимок (обновляем…)'
          : fromCache
            ? 'снимок'
            : 'свежая выгрузка'
      } · синхр. ${syncedLabel}`
    : fromCache
      ? staleHint
        ? 'снимок (обновляем…)'
        : 'снимок'
      : loading && clients.length
        ? 'обновляем…'
        : ''

  return (
    <div className="ms-page" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>Клиенты</h1>
          <p className="ms-muted">МойСклад · Маркетплейс / Прямые</p>
          {cacheHint ? <p className="ms-muted ms-sync-meta">{cacheHint}</p> : null}
          {tgImportNote ? <p className="ms-muted ms-sync-meta">{tgImportNote}</p> : null}
        </div>
        <div className="ms-actions">
          <button className="ms-btn" disabled={loading} onClick={() => void load()} type="button">
            Обновить
          </button>
          <button
            className="ms-btn ms-btn-primary"
            disabled={loading}
            onClick={() => void load({ refresh: true })}
            title="Принудительно скачать данные из МойСклад и сбросить кэш Саммари AI · DeepSeek"
            type="button"
          >
            Синхронизация
          </button>
          <button
            className="ms-btn"
            disabled={loading}
            onClick={() => {
              setAiFillStatus('Сбрасываю кэш Саммари AI · DeepSeek…')
              void call<{
                ok?: boolean
                cleared?: unknown
                regenerated_count?: number
              }>('/clients/ai/refresh-all', {
                method: 'POST',
                body: { regenerate: false },
                timeoutMs: 60_000
              })
                .then(data => {
                  setAiFillStatus(
                    data.ok
                      ? '✓ Кэш Саммари AI сброшен — откройте карточку для нового DeepSeek'
                      : 'Сброс AI не подтверждён'
                  )
                })
                .catch(err =>
                  setAiFillStatus(
                    `AI refresh: ${err instanceof Error ? err.message : String(err)}`
                  )
                )
            }}
            title="Сбросить все кэшированные Саммари AI · DeepSeek (без полной синхронизации МойСклад)"
            type="button"
          >
            Обновить все AI
          </button>
          <button
            className="ms-btn"
            disabled={loading || recalcLoading}
            onClick={() => {
              setRecalcOpen(true)
              setRecalcError('')
              setRecalcPreview(null)
              setRecalcLoading(true)
              void call<{ groups?: string[]; source?: string }>('/groups/recalculate/propose', {
                method: 'POST',
                body: { sales_filter: salesFilter, group, q }
              })
                .then(data => {
                  setRecalcGroups((data.groups || []).join('\n'))
                  setRecalcSource(data.source || '')
                })
                .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                .finally(() => setRecalcLoading(false))
            }}
            title="LLM предложит новые имена групп; подтверждение переназначит теги"
            type="button"
          >
            Пересчитать группы
          </button>
          <button
            className="ms-btn"
            disabled={auditLoading}
            onClick={() => {
              setAuditOpen(true)
              setAuditError('')
              setAuditLoading(true)
              void call<{ audit?: AuditReport }>('/clients/integrity', { timeoutMs: 600_000 })
                .then(data => setAudit(data.audit || null))
                .catch(err => setAuditError(err instanceof Error ? err.message : String(err)))
                .finally(() => setAuditLoading(false))
            }}
            title="Дубли, недостижимые клиенты, битые телефоны и даты, расхождения по деньгам"
            type="button"
          >
            {auditLoading ? 'Проверяю…' : 'Проверить таблицу'}
          </button>
          <button
            className="ms-btn"
            disabled={loading || tgImportBusy}
            onClick={() => {
              setTgImportBusy(true)
              setTgImportNote('')
              void call<{
                ok?: boolean
                matched?: number
                imported_messages?: number
                chats_total?: number
                stamped_rows?: number
                error?: string
                path?: string
              }>('/clients/telegram-export/import?force=true', {
                method: 'POST',
                timeoutMs: 600_000
              })
                .then(data => {
                  if (data.error) {
                    setTgImportNote(`TG импорт: ${data.error}`)
                    return
                  }
                  setTgImportNote(
                    `TG → клиенты: чатов ${data.chats_total ?? 0} · ` +
                      `привязано ${data.matched ?? 0} · сообщений ${data.imported_messages ?? 0}` +
                      (data.path ? ` · ${data.path}` : '')
                  )
                  return load({ refresh: false })
                })
                .catch(err =>
                  setTgImportNote(
                    `TG импорт: ${err instanceof Error ? err.message : String(err)}`
                  )
                )
                .finally(() => setTgImportBusy(false))
            }}
            title="Сопоставить data/telegram_export.json с Наименование → колонка TG conversation (контекст для AI)"
            type="button"
          >
            {tgImportBusy ? 'Импорт TG…' : 'Импорт Telegram'}
          </button>
          <button
            className="ms-btn"
            disabled={loading || tgDialogSyncBusy}
            onClick={() => void runTgDialogSync()}
            title="Подтянуть переписки из вашего личного Telegram (подключён в Рассылках) в колонку TG conversation"
            type="button"
          >
            {tgDialogSyncBusy ? 'Синхр. TG…' : 'Синхр. TG-диалоги'}
          </button>
          <button
            className="ms-btn"
            disabled={loading || tgPhoneVerifyBusy}
            onClick={() => void runTgPhoneVerify()}
            title="Проверить колонку Телефон: есть ли у номера аккаунт Telegram. Пишет «TG активен» и фильтр Telegram в Рассылках."
            type="button"
          >
            {tgPhoneVerifyBusy ? 'Проверяю TG…' : 'TG по телефонам'}
          </button>
          <button className="ms-btn" onClick={() => host.navigate('/campaigns')} type="button">
            Рассылка
          </button>
          <button
            className="ms-btn"
            onClick={() => {
              toggleAiPlayground()
            }}
            title="AI тест: монитор качества Саммари / Повод / Рекомендация"
            type="button"
          >
            AI тест
          </button>
        </div>
      </div>
      <FilterTabs
        counts={counts}
        disabled={salesFilterTabsDisabled({ loading, hasCounts: Boolean(counts) })}
        onChange={setSalesFilter}
        salesFilter={salesFilter}
      />
      <div className="ms-stage-bar">
        <div className="ms-chips">
          {STAGE_CHIPS.map(chip => {
            const n = stageCounts?.[chip.id]

            return (
              <button
                className={`ms-chip${stage === chip.id ? ' is-active' : ''}`}
                key={chip.id}
                onClick={() => setStage(chip.id)}
                title={chip.title}
                type="button"
              >
                {chip.label}
                {n != null ? <span>{n}</span> : null}
              </button>
            )
          })}
        </div>
        {stage === 'failed' ? (
          <button
            className="ms-btn"
            disabled={stageTagBusy || loading}
            onClick={() => void markFailedStage()}
            title="Сначала покажет, кому проставится тег «не состоялся»; запись — вторым нажатием"
            type="button"
          >
            {stageTagBusy ? 'Считаю…' : stageTagArmed ? 'Записать теги' : 'Пометить в МойСклад'}
          </button>
        ) : null}
        {stageTagStatus ? <span className="ms-muted">{stageTagStatus}</span> : null}
        <select
          aria-label="Тип контрагента"
          className="ms-select"
          onChange={e =>
            setEntityType(e.target.value as 'all' | 'individual' | 'legal')
          }
          title="Физические / юридические лица (юрлица + ИП)"
          value={entityType}
        >
          <option value="all">Все лица</option>
          <option value="individual">Физ. лица</option>
          <option value="legal">Юр. лица + ИП</option>
        </select>
        <button
          className={`ms-chip${loyaltyOnly ? ' is-active' : ''}`}
          onClick={() => setLoyaltyOnly(v => !v)}
          title="Только клиенты с начисленными баллами лояльности"
          type="button"
        >
          Есть баллы
        </button>
      </div>
      <div className="ms-search">
        <input
          onChange={e => setQ(e.target.value)}
          placeholder="Поиск по имени / телефону…"
          type="search"
          value={q}
        />
      </div>
      <GroupCloudSection
        activeGroup={group}
        activeSource={groupSource}
        emptyHint="Нет тегов МойСклад в текущей выборке"
        items={groupOptionsMs}
        limit={120}
        onToggle={(name, source) => {
          if (group === name && groupSource === source) {
            setGroup('')
            setGroupSource('any')
          } else {
            setGroup(name)
            setGroupSource(source)
          }
        }}
        sourceKey="ms"
        title="Группы: Мой склад"
      />
      <GroupCloudSection
        activeGroup={group}
        activeSource={groupSource}
        emptyHint="ИИ-группы появятся после эвристик/AI fill (новый, премиум…)"
        items={groupOptionsAi}
        limit={80}
        onToggle={(name, source) => {
          if (group === name && groupSource === source) {
            setGroup('')
            setGroupSource('any')
          } else {
            setGroup(name)
            setGroupSource(source)
          }
        }}
        sourceKey="ai"
        title="Группы: ИИ"
      />
      {error ? <MsErrorModal message={error} onClose={() => setError('')} /> : null}
      <p className="ms-muted">
        Найдено: {matched}
        {clients.length ? ` · загружено ${clients.length}` : ''}
        {columnFilterActive ? ` · после фильтров ${displayClients.length}` : ''}
        {integrityNote ? ` · ${integrityNote}` : ''}
        {columnFilterActive || columnSort ? (
          <>
            {' · '}
            <button
              className="ms-link-btn"
              onClick={() => {
                setColumnFilters({})
                setColumnSort(null)
                setOpenFilterKey(null)
              }}
              type="button"
            >
              сбросить сортировку/фильтры колонок
            </button>
          </>
        ) : null}
      </p>
      {recalcOpen ? (
        <div className="ms-modal-backdrop" onClick={() => setRecalcOpen(false)}>
          <div
            className="ms-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
          >
            <div className="ms-card-head">
              <h2 className="ms-section-title">Пересчитать группы</h2>
              <button className="ms-btn" onClick={() => setRecalcOpen(false)} type="button">
                Закрыть
              </button>
            </div>
            <p className="ms-muted">
              Отредактируйте названия (по одному на строку), затем подтвердите. Источник:{' '}
              {recalcSource || '…'}
            </p>
            {recalcError ? <div className="ms-error">{recalcError}</div> : null}
            <textarea
              disabled={recalcLoading}
              onChange={e => setRecalcGroups(e.target.value)}
              rows={12}
              style={{ width: '100%' }}
              value={recalcGroups}
            />
            {recalcPreview ? (
              <p className="ms-muted">
                Превью: изменится {recalcPreview.changed ?? 0} из {recalcPreview.total ?? 0}
              </p>
            ) : null}
            <div className="ms-compose-actions">
              <button
                className="ms-btn"
                disabled={recalcLoading || !recalcGroups.trim()}
                onClick={() => {
                  setRecalcLoading(true)
                  setRecalcError('')

                  const groups = recalcGroups
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean)

                  void call<{ changed?: number; total?: number }>('/groups/recalculate/apply', {
                    method: 'POST',
                    body: {
                      groups,
                      sales_filter: salesFilter,
                      group,
                      q,
                      dry_run: true,
                      push: false
                    }
                  })
                    .then(data => setRecalcPreview({ changed: data.changed, total: data.total }))
                    .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                    .finally(() => setRecalcLoading(false))
                }}
                type="button"
              >
                Превью
              </button>
              <button
                className="ms-btn ms-btn-primary"
                disabled={recalcLoading || !recalcGroups.trim()}
                onClick={() => {
                  setRecalcLoading(true)
                  setRecalcError('')

                  const groups = recalcGroups
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean)

                  void call('/groups/recalculate/apply', {
                    method: 'POST',
                    body: {
                      groups,
                      sales_filter: salesFilter,
                      group,
                      q,
                      dry_run: false,
                      push: true
                    }
                  })
                    .then(() => {
                      setRecalcOpen(false)
                      void load({ refresh: true })
                    })
                    .catch(err => setRecalcError(err instanceof Error ? err.message : String(err)))
                    .finally(() => setRecalcLoading(false))
                }}
                type="button"
              >
                Записать в МойСклад
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {auditOpen ? (
        <div className="ms-modal-backdrop" onClick={() => setAuditOpen(false)}>
          <div className="ms-modal ms-audit-modal" onClick={e => e.stopPropagation()} role="dialog">
            <div className="ms-card-head">
              <h2 className="ms-section-title">Проверка таблицы</h2>
              <button className="ms-btn" onClick={() => setAuditOpen(false)} type="button">
                Закрыть
              </button>
            </div>
            {auditLoading ? <p className="ms-muted">Считаю по всему каталогу…</p> : null}
            {auditError ? <div className="ms-error">{auditError}</div> : null}
            {!auditLoading && audit ? (
              <>
                <p className="ms-muted">
                  Строк проверено: {audit.rows_total ?? 0} · проблемных записей:{' '}
                  {audit.issues_total ?? 0} · критичных: {audit.errors_total ?? 0}
                  {audit.checked_at ? ` · ${audit.checked_at.replace('T', ' ')}` : ''}
                </p>
                {audit.clean ? (
                  <p className="ms-muted">Проблем не найдено.</p>
                ) : (
                  <div className="ms-audit-list">
                    {(audit.issues || []).map(issue => (
                      <div className={`ms-audit-issue is-${issue.severity}`} key={issue.code}>
                        <div className="ms-audit-issue-head">
                          <strong>{issue.label}</strong>
                          <span className="ms-audit-count">{issue.count}</span>
                        </div>
                        {issue.hint ? <p className="ms-muted">{issue.hint}</p> : null}
                        <ul className="ms-audit-sample">
                          {(issue.sample || []).map(row => (
                            <li key={`${issue.code}-${row.id}`}>
                              <button
                                className="ms-link-btn"
                                onClick={() => {
                                  setAuditOpen(false)
                                  if (row.id) {setCardClientId(row.id)}
                                }}
                                type="button"
                              >
                                {row.name || row.id}
                              </button>
                              {row.detail ? <span className="ms-muted"> — {row.detail}</span> : null}
                            </li>
                          ))}
                        </ul>
                        {issue.count > (issue.sample?.length || 0) ? (
                          <p className="ms-muted">
                            …и ещё {issue.count - (issue.sample?.length || 0)}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : null}
          </div>
        </div>
      ) : null}
      {aiFillStatus ? <p className="ms-action-status">{aiFillStatus}</p> : null}
      {loading && !clients.length ? (
        <p className="ms-muted">Загрузка клиентов…</p>
      ) : (
        <div className="ms-table-wrap" onScroll={onTableScroll}>
          {loading && clients.length ? (
            <p className="ms-muted ms-sync-meta">Обновляем список в фоне…</p>
          ) : null}
          <table className="ms-table">
            <thead>
              <tr>
                {CLIENT_COLUMNS.map(col => (
                  <ClientsColumnHeader
                    col={col}
                    filter={columnFilters[col.key] || EMPTY_FILTER}
                    key={col.key}
                    onClearFilter={() =>
                      setColumnFilters(prev => {
                        const next = { ...prev }
                        delete next[col.key]
                        return next
                      })
                    }
                    onFilterChange={next =>
                      setColumnFilters(prev => ({ ...prev, [col.key]: next }))
                    }
                    onSort={dir =>
                      setColumnSort(dir ? { key: col.key, dir } : null)
                    }
                    onToggleOpen={() =>
                      setOpenFilterKey(prev => (prev === col.key ? null : col.key))
                    }
                    open={openFilterKey === col.key}
                    sort={columnSort}
                    uniqueValues={columnUniques[col.key] || []}
                  />
                ))}
              </tr>
            </thead>
            <tbody>
              {displayClients.map(row => (
                <tr key={row.id || row.name}>
                  {CLIENT_COLUMNS.map(col => {
                    const value = col.render(row)
                    const aiKey = AI_COLUMN_KEYS[col.key]
                    const isAi = Boolean(
                      aiKey && (row.ai_fields || []).includes(aiKey) && value
                    )

                    if (col.key === 'name') {
                      return (
                        <td key={col.key}>
                          <button
                            className="ms-link-btn"
                            onClick={() => row.id && setCardClientId(row.id)}
                            title="Открыть карточку клиента"
                            type="button"
                          >
                            {value || '—'}
                          </button>
                        </td>
                      )
                    }

                    if (col.key === 'tg_active') {
                      const title =
                        row.tg_active === true
                          ? `Есть Telegram${row.tg_active_nick ? ` · ${row.tg_active_nick}` : ''}`
                          : row.tg_active === false
                            ? 'Не находится по номеру — возможно, поиск скрыт настройками приватности. Проверьте по нику или вручную.'
                            : 'Ещё не проверен — проверка идёт автоматически в фоне'
                      const cls =
                        row.tg_active === true
                          ? 'ms-tg-active-ok'
                          : row.tg_active === false
                            ? 'ms-tg-active-bad'
                            : row.tg_nick
                              ? 'ms-tg-active-unknown'
                              : ''
                      return (
                        <td className={cls || undefined} key={col.key} title={title}>
                          {value || '—'}
                        </td>
                      )
                    }

                    if (
                      col.key === 'tg_conversation' &&
                      row.id &&
                      (value || row.tg_nick)
                    ) {
                      return (
                        <td key={col.key}>
                          <button
                            className="ms-link-btn ms-tg-cell-btn"
                            onClick={() =>
                              setTgDialog({
                                id: row.id!,
                                name: row.name || row.tg_nick || ''
                              })
                            }
                            title="Открыть диалог: история + ответ прямо здесь"
                            type="button"
                          >
                            {value || 'открыть диалог'}
                          </button>
                        </td>
                      )
                    }

                    return (
                      <td className={isAi ? 'ms-ai-added' : undefined} key={col.key}>
                        <AiCell ai={isAi} value={value} />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {loadingMore ? <p className="ms-muted ms-load-more">Подгружаем ещё…</p> : null}
          {!hasMore && clients.length > 0 ? (
            <p className="ms-muted ms-load-more">Все {matched} клиентов загружены</p>
          ) : null}
        </div>
      )}
      <ClientCardModal call={call} clientId={cardClientId} onClose={() => setCardClientId(null)} />
      {tgDialog ? (
        <ClientDialogModal
          clientId={tgDialog.id}
          clientName={tgDialog.name}
          onClose={() => setTgDialog(null)}
        />
      ) : null}
    </div>
  )
}

function CampaignsPage() {
  const call = useMsRest()
  const callStream = useMsRestStream()
  const [salesFilter, setSalesFilter] = useState('all')
  const [title, setTitle] = useState('Рассылка по фильтрам')
  const [channel, setChannel] = useState('telegram')
  /** Delivery channel for compose/send — not an audience filter until user toggles chips. */
  const [channelKind, setChannelKind] = useState('')
  const [group, setGroup] = useState('')
  const [groupSource, setGroupSource] = useState<'any' | 'ms' | 'ai'>('any')
  const [requirePhone, setRequirePhone] = useState(false)
  const [requireTelegram, setRequireTelegram] = useState(false)
  const [vipOnly, setVipOnly] = useState(false)
  const [loyaltyOnly, setLoyaltyOnly] = useState(false)
  const [birthdaySoon, setBirthdaySoon] = useState(false)
  const [daysBeforeEvent, setDaysBeforeEvent] = useState(0)
  const [eventDateFrom, setEventDateFrom] = useState<string | null>(null)
  const [eventDateTo, setEventDateTo] = useState<string | null>(null)
  const [segments, setSegments] = useState<SavedSegment[]>([])
  const [segmentsLoading, setSegmentsLoading] = useState(false)
  const [segmentName, setSegmentName] = useState('')
  const [segmentSaving, setSegmentSaving] = useState(false)
  const [segmentStatus, setSegmentStatus] = useState('')
  const [activeSegmentId, setActiveSegmentId] = useState('')
  const [filterDrawer, setFilterDrawer] = useState<'calendar' | 'groups-ms' | 'groups-ai' | null>(
    null
  )
  const [saveFilterOpen, setSaveFilterOpen] = useState(false)
  const [personalize] = useState(false)
  const [, setBatchProgress] = useState('')
  const [offer, setOffer] = useState('')
  const [offerTick, setOfferTick] = useState(0)
  const [actionStatus, setActionStatus] = useState('')
  const offerRef = useRef('')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [audience, setAudience] = useState(0)
  const [audiencePreview, setAudiencePreview] = useState<ClientRow[]>([])
  const [audienceQ, setAudienceQ] = useState('')
  const [audienceQDebounced, setAudienceQDebounced] = useState('')
  const [audienceHasMore, setAudienceHasMore] = useState(false)
  const [audienceNextOffset, setAudienceNextOffset] = useState(0)
  const [audienceLoadingMore, setAudienceLoadingMore] = useState(false)
  /** Server served a partial/stale catalog (rebuild in flight) — auto re-poll. */
  const [audienceStaleHint, setAudienceStaleHint] = useState(false)
  const [groupOptionsMs, setGroupOptionsMs] = useState<GroupChipOption[]>([])
  const [groupOptionsAi, setGroupOptionsAi] = useState<GroupChipOption[]>([])
  const [selectedClientId, setSelectedClientId] = useState<string | null>(null)
  const [selectedClientName, setSelectedClientName] = useState('')
  const [facts, setFacts] = useState<ClientFacts | null>(null)
  const [groundingNotes, setGroundingNotes] = useState('')
  const [genSource, setGenSource] = useState('')
  const [sellerName, setSellerName] = useState('')
  const [sellerFacts, setSellerFacts] = useState('')
  const [sellerLoaded, setSellerLoaded] = useState(false)
  const [bizConnectionId, setBizConnectionId] = useState('')
  const [bizBotUsername, setBizBotUsername] = useState('')
  const [telegramAccount, setTelegramAccount] = useState<{
    configured?: boolean
    bot_username?: string | null
    business_connection_configured?: boolean
    business_connection_id?: string | null
    account?: {
      ok?: boolean
      username?: string | null
      first_name?: string | null
      can_reply?: boolean
      can_read_messages?: boolean
      is_enabled?: boolean
      error?: string
      detail?: string
    } | null
  } | null>(null)
  const [bizSaving, setBizSaving] = useState(false)
  const [tgUser, setTgUser] = useState<{
    available?: boolean
    api_configured?: boolean
    api_source?: string
    api_id_masked?: string
    api_hash_masked?: string
    session_saved?: boolean
    authorized?: boolean
    phone?: string | null
    user?: { id?: number; username?: string | null; name?: string | null } | null
    contacts_cached?: number
    detail?: string
    error?: string
    gateway_configured?: boolean
    send_mode?: string
  } | null>(null)
  const [tgOpen, setTgOpen] = useState(false)
  const [tgStep, setTgStep] = useState<'phone' | 'code' | 'password'>('phone')
  const [tgBusy, setTgBusy] = useState(false)
  const [tgProgress, setTgProgress] = useState<{ title: string; detail: string } | null>(null)
  const [tgPhone, setTgPhone] = useState('')
  const [tgCode, setTgCode] = useState('')
  const [tgPassword, setTgPassword] = useState('')
  const [tgSession, setTgSession] = useState('')
  const [error, setError] = useState('')

  const runTgBusy = async (title: string, detail: string, work: () => Promise<void>) => {
    setTgBusy(true)
    setTgProgress({ title, detail })
    setError('')
    try {
      await work()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setTgBusy(false)
      setTgProgress(null)
    }
  }

  const [outreachContacts, setOutreachContacts] = useState<OutreachContact[]>([])
  const [contactPickerId, setContactPickerId] = useState('')
  const [contactsError, setContactsError] = useState('')
  const [contactsLoading, setContactsLoading] = useState(false)
  const [addContactOpen, setAddContactOpen] = useState(false)
  const [addContactName, setAddContactName] = useState('')
  const [addContactNick, setAddContactNick] = useState('')
  const [addContactChatId, setAddContactChatId] = useState('')
  const [addContactQuery, setAddContactQuery] = useState('')
  const [addContactSaving, setAddContactSaving] = useState(false)
  const [addContactResolving, setAddContactResolving] = useState(false)
  const [selectedClientIds, setSelectedClientIds] = useState<string[]>([])
  const [draftsOpen, setDraftsOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [rewriting, setRewriting] = useState(false)
  const [suggestingBouquet, setSuggestingBouquet] = useState(false)
  const [paraphrasing, setParaphrasing] = useState(false)
  const [checkingSanity, setCheckingSanity] = useState(false)
  const [sanity, setSanity] = useState<SanityResult | null>(null)
  const [prefillReady, setPrefillReady] = useState(false)
  const audienceLoadMoreRef = useRef(false)
  /** Bumps on each audience filter change — stale /clients responses ignored. */
  const audienceLoadGen = useRef(0)
  const audiencePreviewRef = useRef<ClientRow[]>([])
  audiencePreviewRef.current = audiencePreview
  /** Keep pagination out of loadAudience deps — else every append recreates the
   * callback, the filter useEffect re-runs, and contacts reset to page 1 while
   * «Подгружаем клиентов…» loops. */
  const audienceNextOffsetRef = useRef(0)
  const audienceHasMoreRef = useRef(false)
  audienceNextOffsetRef.current = audienceNextOffset
  audienceHasMoreRef.current = audienceHasMore
  const groupOptionsMsRef = useRef<GroupChipOption[]>([])
  const groupOptionsAiRef = useRef<GroupChipOption[]>([])
  groupOptionsMsRef.current = groupOptionsMs
  groupOptionsAiRef.current = groupOptionsAi
  const sellerSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** Bumps on each client switch / generate — stale stream events are ignored. */
  const outreachGenRef = useRef(0)
  /** Separate from outreachGen — draft-cache load must not cancel facts fetch. */
  const factsGenRef = useRef(0)
  const outreachAbortRef = useRef<AbortController | null>(null)
  const selectedClientNameRef = useRef('')
  const selectedClientIdRef = useRef<string | null>(null)
  const channelRef = useRef(channel)
  const titleRef = useRef(title)
  const factsRef = useRef<ClientFacts | null>(null)
  const groundingNotesRef = useRef('')
  const genSourceRef = useRef('')
  const sanityRef = useRef<SanityResult | null>(null)

  // Keep latest values for async/stream callbacks without lagging a useEffect tick.
  offerRef.current = offer
  selectedClientNameRef.current = selectedClientName
  selectedClientIdRef.current = selectedClientId
  channelRef.current = channel
  titleRef.current = title
  factsRef.current = facts
  groundingNotesRef.current = groundingNotes
  genSourceRef.current = genSource
  sanityRef.current = sanity

  const applyOfferText = useCallback((next: string, status: string) => {
    const text = (next || '').trim() ? next : ''
    setOffer(text)
    offerRef.current = text
    setOfferTick(t => t + 1)
    setActionStatus(status)
  }, [])

  const restoreServerDraft = useCallback(
    (draft: {
      message?: string
      grounding_notes?: string
      source?: string
      status?: string
      client_name?: string
      title?: string
      facts?: ClientFacts | null
      sanity?: SanityResult | null
    }) => {
      const name = (draft.client_name || selectedClientNameRef.current || '').trim()

      if (name) {
        setSelectedClientName(name)
        selectedClientNameRef.current = name
        setTitle(draft.title || `Черновик · ${name}`)
      } else if (draft.title) {
        setTitle(draft.title)
      }

      // Keep seeded /clients facts when draft cache has message but no facts blob.
      // Never let a stale draft conversation (often «· 1» outbound only) clobber
      // a fresher live pull from loadClientFacts.
      if (draft.facts) {
        setFacts(prev => {
          const next = { ...draft.facts! }
          const prevCount = Number(prev?.conversation?.message_count || 0)
          const nextCount = Number(next.conversation?.message_count || 0)
          if (prev?.conversation && prevCount >= nextCount) {
            next.conversation = prev.conversation
          }
          if (
            prev?.recommendation &&
            (!next.recommendation || prevCount > nextCount)
          ) {
            // Keep recommendation only when live pull is thinner — never keep
            // a stale history_profile after facts fingerprint moved on.
            next.recommendation = prev.recommendation
            if (!next.history_profile) {
              next.history_profile = prev.history_profile
            }
            if (!next.occasion_intent) {
              next.occasion_intent = prev.occasion_intent
            }
            next.ai_source = prev.ai_source || next.ai_source
          }
          return next
        })
      }
      setSanity(draft.sanity || null)
      setGroundingNotes(draft.grounding_notes || '')
      setGenSource(draft.source || 'redis-cache')
      setError('')
      applyOfferText(draft.message || '', draft.status || AI_GENERATED_STATUS)
    },
    [applyOfferText]
  )

  /** Sync title + seed Facts immediately (no auto LLM). */
  const applyClientSelectionUi = useCallback(
    (clientId: string, clientName: string, seedFacts?: ClientFacts | null) => {
      const name = (clientName || '').trim()
      setSelectedClientId(clientId)
      setSelectedClientName(name)
      selectedClientNameRef.current = name
      setTitle(name ? `Черновик · ${name}` : 'Черновик · клиент')
      setFacts(seedFacts || null)
      setSanity(null)
      setGroundingNotes('')
      setGenSource('')
      setError('')
      applyOfferText('', 'Загружаем кэш…')
    },
    [applyOfferText]
  )

  useEffect(() => {
    const prefill = readDraftPrefill()

    if (prefill) {
      setSelectedClientId(prefill.clientId)

      if (prefill.channel) {
        // Compose is Telegram-only until WA ships again.
        setChannel(prefill.channel.startsWith('telegram') ? prefill.channel : 'telegram')
      }

      if (prefill.salesFilter) {setSalesFilter(prefill.salesFilter)}
      setTitle('Черновик · клиент')
    }

    setPrefillReady(true)
  }, [])

  useEffect(() => {
    void call<{
      seller_name?: string
      seller_facts?: string
      telegram_business_connection_id?: string
      telegram_account?: typeof telegramAccount
      telegram?: { bot_username?: string | null }
    }>('/campaigns/seller-settings')
      .then(data => {
        setSellerName(data.seller_name || '')
        setSellerFacts(data.seller_facts || '')
        const biz =
          data.telegram_business_connection_id ||
          data.telegram_account?.business_connection_id ||
          ''
        setBizConnectionId(biz)
        const bot =
          data.telegram_account?.bot_username ||
          data.telegram?.bot_username ||
          ''
        setBizBotUsername(bot || '')
        if (data.telegram_account) {
          setTelegramAccount(data.telegram_account)
        }
      })
      .catch(() => undefined)
      .finally(() => {
        setSellerLoaded(true)
        void call<{
          configured?: boolean
          bot_username?: string | null
          business_connection_id?: string | null
          account?: typeof telegramAccount extends { account?: infer A } ? A : never
        }>('/campaigns/telegram-account')
          .then(snap => {
            setTelegramAccount(snap)
            if (snap.business_connection_id) {
              setBizConnectionId(snap.business_connection_id)
            }
            if (snap.bot_username) {
              setBizBotUsername(snap.bot_username)
            }
          })
          .catch(() => undefined)
      })
  }, [call])

  const loadOutreachContacts = useCallback(async () => {
    setContactsLoading(true)
    try {
      const data = await call<{
        contacts?: OutreachContact[]
      }>('/campaigns/telegram-contacts?limit=300', { timeoutMs: 45_000 })
      setOutreachContacts(data.contacts || [])
      setContactsError('')
    } catch (err) {
      // Keep the previous list, but SHOW the failure — a silently empty
      // picker after "✓ контакты синхронизированы" is undebuggable from UI.
      const raw = err instanceof Error ? err.message : String(err)
      console.warn('[moysklad] telegram-contacts load failed:', err)
      setContactsError(
        /abort/i.test(raw)
          ? 'сервер не ответил за 45 сек (таймаут) — смотрите серверный лог telegram-contacts'
          : raw
      )
    } finally {
      setContactsLoading(false)
    }
  }, [call])

  useEffect(() => {
    void loadOutreachContacts()
  }, [loadOutreachContacts])

  const refreshTgUser = useCallback(
    async (probe = true) => {
      try {
        const data = await call<typeof tgUser>(
          `/campaigns/telegram-user?probe=${probe ? 'true' : 'false'}`
        )
        setTgUser(data)
        if (data?.phone && !tgPhone) {
          setTgPhone(data.phone)
        }
        if (data?.authorized) {
          setTgStep('phone')
        }
        return data
      } catch {
        return null
      }
    },
    [call, tgPhone]
  )

  useEffect(() => {
    void refreshTgUser(false).then(data => {
      // After logout / cold start: show phone form so the user can reconnect
      // without hunting for a collapsed panel.
      if (data && data.authorized === false) {
        setTgOpen(true)
        setTgStep('phone')
      }
    })
    // Probe once on mount; the panel's buttons re-probe on demand.
  }, [])

  // Contact sync runs server-side in the background; the UI only polls
  // progress — no blocking modal, the tab can be closed at any point.
  const tgPollSync = useCallback(() => {
    let tries = 0
    const tick = async () => {
      try {
        const st = await call<{
          running?: boolean
          phase?: string
          scanned?: number
          total?: number
          from_address_book?: number
          from_dialogs?: number
          error?: string
        }>('/campaigns/telegram-user/contacts/sync')
        if (st?.running) {
          const phase =
            st.phase === 'address_book'
              ? 'адресная книга'
              : st.phase === 'dialogs'
                ? `чаты, просмотрено ${st.scanned ?? 0}`
                : 'запуск'
          setActionStatus(`Синхронизация в фоне: контактов ${st.total ?? 0} · ${phase}…`)
          if (++tries < 300) {setTimeout(() => void tick(), 2000)}
          return
        }
        if (st?.phase === 'error' && st.error) {
          setError(`Синхронизация контактов: ${st.error}`)
        } else if (st) {
          setActionStatus(
            `✓ Контакты из Telegram: ${st.total ?? 0}` +
              ` (адресная книга ${st.from_address_book ?? 0}, чаты ${st.from_dialogs ?? 0})`
          )
        }
        await refreshTgUser(false)
        await loadOutreachContacts()
      } catch {
        if (++tries < 300) {setTimeout(() => void tick(), 3000)}
      }
    }
    void tick()
  }, [call, loadOutreachContacts, refreshTgUser])

  const tgStartContactsSync = useCallback(async () => {
    await call('/campaigns/telegram-user/contacts/refresh', { method: 'POST' }).catch(() => null)
    tgPollSync()
  }, [call, tgPollSync])

  const tgInstallRuntime = async () => {
    await runTgBusy('Установка Telethon', 'Ставим MTProto-движок в venv…', async () => {
      const data = await call<typeof tgUser & { version?: string }>(
        '/campaigns/telegram-user/install',
        { method: 'POST' }
      )
      setTgUser(data)
      setActionStatus(`✓ telethon установлен${data?.version ? ` ${data.version}` : ''}`)
    })
  }

  const tgLogin = async (opts?: { forceSms?: boolean }) => {
    if (!tgPhone.trim()) {
      setError('Укажите номер телефона в формате +79991234567')
      return
    }
    const forceSms = Boolean(opts?.forceSms)
    await runTgBusy(
      forceSms ? 'Telethon: SMS' : 'Telethon: вход',
      forceSms
        ? `Повторно запрашиваем код SMS на ${tgPhone.trim()}…`
        : `Отправляем код на ${tgPhone.trim()}…`,
      async () => {
        const body: { phone: string; force_sms?: boolean } = {
          phone: tgPhone.trim()
        }
        if (forceSms) {
          body.force_sms = true
        }
        const data = await call<{
          authorized?: boolean
          code_sent?: boolean
          phone?: string
          code_delivery?: string
          code_delivery_hint?: string
          gateway_configured?: boolean
        }>('/campaigns/telegram-user/login', { method: 'POST', body, timeoutMs: 55_000 })
        if (data.phone) {
          setTgPhone(data.phone)
        }
        if (data.authorized) {
          setActionStatus('✓ Личный Telegram уже подключён')
          await refreshTgUser()
          await tgStartContactsSync()
        } else {
          setTgStep('code')
          const hint =
            data.code_delivery_hint ||
            (data.code_delivery === 'telegram_app'
              ? 'Код в приложении Telegram (чат «Login code»), не SMS.'
              : data.code_delivery === 'sms'
                ? 'Код отправлен SMS на этот номер.'
                : 'Проверьте Telegram и SMS на этом номере.')
          setActionStatus(
            data.phone
              ? `Код для ${data.phone}: ${hint}`
              : `Код отправлен. ${hint}`
          )
        }
      }
    )
  }

  const tgSubmitCode = async () => {
    await runTgBusy('Telethon: код', 'Проверяем код из Telegram…', async () => {
      const data = await call<{ authorized?: boolean; password_required?: boolean }>(
        '/campaigns/telegram-user/code',
        { method: 'POST', body: { code: tgCode.trim() }, timeoutMs: 35_000 }
      )
      setTgCode('')
      if (data.password_required) {
        setTgStep('password')
        setActionStatus('Нужен облачный пароль (2FA)')
        return
      }
      setTgStep('phone')
      setActionStatus('✓ Личный Telegram подключён — контакты тянутся в фоне')
      await refreshTgUser()
      await tgStartContactsSync()
    })
  }

  const tgSubmitPassword = async () => {
    await runTgBusy('Telethon: 2FA', 'Проверяем облачный пароль…', async () => {
      await call('/campaigns/telegram-user/password', {
        method: 'POST',
        body: { password: tgPassword },
        timeoutMs: 35_000
      })
      setTgPassword('')
      setTgStep('phone')
      setActionStatus('✓ Личный Telegram подключён — контакты тянутся в фоне')
      await refreshTgUser()
      await tgStartContactsSync()
    })
  }

  const tgSaveSession = async () => {
    if (!tgSession.trim()) {
      setError('Вставьте StringSession (логин Telethon с машины, где открывается Telegram)')
      return
    }
    await runTgBusy('Telethon: сессия', 'Сохраняем StringSession на сервер…', async () => {
      await call('/campaigns/telegram-user/session', {
        method: 'POST',
        body: { session: tgSession.trim(), phone: tgPhone.trim() },
        timeoutMs: 35_000
      })
      setTgSession('')
      setActionStatus('✓ Сессия Telegram сохранена — контакты тянутся в фоне')
      await refreshTgUser()
      await tgStartContactsSync()
    })
  }

  const tgSyncContacts = async () => {
    setError('')
    try {
      const st = await call<{ started?: boolean }>(
        '/campaigns/telegram-user/contacts/refresh',
        { method: 'POST' }
      )
      setActionStatus(
        st?.started
          ? 'Синхронизация запущена в фоне — контакты подтягиваются…'
          : 'Синхронизация уже идёт в фоне…'
      )
      tgPollSync()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const tgLogout = async () => {
    await runTgBusy('Выход', 'Отключаем личный Telegram…', async () => {
      await call('/campaigns/telegram-user/logout', { method: 'POST' })
      setTgStep('phone')
      setTgPhone('')
      setTgCode('')
      setTgPassword('')
      setTgSession('')
      setTgOpen(true)
      setActionStatus('Личный Telegram отключён — введите номер для нового входа')
      await refreshTgUser(false)
      await loadOutreachContacts()
    })
  }

  const persistSellerSettings = useCallback(
    (name: string, factsText: string, bizId?: string | null) => {
      if (sellerSaveTimer.current) {clearTimeout(sellerSaveTimer.current)}
      sellerSaveTimer.current = setTimeout(() => {
        const body: Record<string, string> = {
          seller_name: name,
          seller_facts: factsText
        }

        if (bizId !== undefined && bizId !== null) {
          body.telegram_business_connection_id = bizId
        }

        void call<{ telegram_account?: typeof telegramAccount }>(
          '/campaigns/seller-settings',
          {
            method: 'PUT',
            body
          }
        )
          .then(data => {
            if (data.telegram_account) {
              setTelegramAccount(data.telegram_account)
            }
          })
          .catch(() => undefined)
      }, 450)
    },
    [call]
  )

  const refreshTelegramAccount = useCallback(async () => {
    try {
      const data = await call<{
        configured?: boolean
        bot_username?: string | null
        business_connection_id?: string | null
        account?: typeof telegramAccount extends { account?: infer A } ? A : never
      }>('/campaigns/telegram-account')
      setTelegramAccount(data)
      if (data.business_connection_id) {
        setBizConnectionId(data.business_connection_id)
      }
      if (data.bot_username) {
        setBizBotUsername(data.bot_username)
      }
    } catch {
      /* ignore probe errors — UI shows last known */
    }
  }, [call])

  const saveBusinessConnection = async () => {
    setBizSaving(true)
    setError('')

    try {
      const data = await call<{
        telegram_business_connection_id?: string
        telegram_account?: typeof telegramAccount
      }>('/campaigns/seller-settings', {
        method: 'PUT',
        body: {
          seller_name: sellerName,
          seller_facts: sellerFacts,
          telegram_business_connection_id: bizConnectionId.trim()
        }
      })
      setBizConnectionId(data.telegram_business_connection_id || bizConnectionId.trim())
      if (data.telegram_account) {
        setTelegramAccount(data.telegram_account)
      }
      setActionStatus(
        data.telegram_account?.account?.ok
          ? `✓ Business: @${data.telegram_account.account.username || 'аккаунт'} подключён`
          : 'Connection ID сохранён — проверьте права Reply в Telegram Business'
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBizSaving(false)
    }
  }

  useEffect(() => {
    const t = setTimeout(() => setAudienceQDebounced(audienceQ.trim()), 280)

    return () => clearTimeout(t)
  }, [audienceQ])

  const audienceFilterParams = useCallback(
    (opts?: { limit?: number; offset?: number; q?: string }) => {
      const params = new URLSearchParams({
        sales_filter: salesFilter,
        group,
        group_source: groupSource,
        q: opts?.q ?? audienceQDebounced,
        limit: String(opts?.limit ?? 40),
        offset: String(opts?.offset ?? 0)
      })

      if (channelKind) {params.set('channel_kind', channelKind)}

      if (requirePhone) {params.set('require_phone', 'true')}

      if (requireTelegram) {params.set('require_telegram', 'true')}

      if (vipOnly) {params.set('vip_only', 'true')}

      if (loyaltyOnly) {params.set('loyalty_only', 'true')}

      if (birthdaySoon) {params.set('birthday_soon', 'true')}

      if (daysBeforeEvent > 0) {
        params.set('days_before_event', String(daysBeforeEvent))
      }

      if (eventDateFrom) {
        params.set('event_date_from', eventDateFrom)
      }

      if (eventDateTo) {
        params.set('event_date_to', eventDateTo)
      }

      return params
    },
    [
      audienceQDebounced,
      birthdaySoon,
      channelKind,
      daysBeforeEvent,
      eventDateFrom,
      eventDateTo,
      group,
      groupSource,
      loyaltyOnly,
      requirePhone,
      requireTelegram,
      salesFilter,
      vipOnly
    ]
  )

  const loadSegments = useCallback(() => {
    setSegmentsLoading(true)
    void call<{ segments?: SavedSegment[] }>('/segments')
      .then(data => setSegments(data.segments || []))
      .catch(err => setSegmentStatus(err instanceof Error ? err.message : String(err)))
      .finally(() => setSegmentsLoading(false))
  }, [call])

  useEffect(() => {
    loadSegments()
  }, [loadSegments])

  const saveCurrentSegment = useCallback(async () => {
    const name = segmentName.trim()

    if (!name) {
      setSegmentStatus('Дайте списку имя.')

      return false
    }

    setSegmentSaving(true)
    setSegmentStatus('')
    try {
      const data = await call<{ segment?: SavedSegment }>('/segments', {
        method: 'POST',
        body: {
          id: activeSegmentId,
          name,
          sales_filter: salesFilter,
          group,
          q: audienceQDebounced,
          group_source: groupSource,
          channel_kind: channelKind,
          require_phone: requirePhone,
          require_telegram: requireTelegram,
          vip_only: vipOnly,
          birthday_soon: birthdaySoon,
          days_before_event: daysBeforeEvent,
          event_date_from: eventDateFrom || '',
          event_date_to: eventDateTo || ''
        }
      })
      if (data.segment) {
        setActiveSegmentId(data.segment.id)
        setSegmentStatus(
          `✓ Список «${data.segment.name}» сохранён (${data.segment.matched_total ?? 0} клиентов)`
        )
        loadSegments()
        return true
      }
      loadSegments()
      return false
    } catch (err) {
      setSegmentStatus(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setSegmentSaving(false)
    }
  }, [
    activeSegmentId, audienceQDebounced, birthdaySoon, call, channelKind, daysBeforeEvent,
    eventDateFrom, eventDateTo,
    group, groupSource, loadSegments, requirePhone, requireTelegram, salesFilter,
    segmentName, vipOnly
  ])

  const applySegment = useCallback((segment: SavedSegment) => {
    const f = segment.filters || {}

    setActiveSegmentId(segment.id)
    setSegmentName(segment.name)
    setSalesFilter(f.sales_filter || 'all')
    setGroup(f.group || '')
    setGroupSource((f.group_source as 'any' | 'ms' | 'ai') || 'any')
    setAudienceQ(f.q || '')
    setChannelKind(String(f.channel_kind || '').trim())
    setRequireTelegram(Boolean(f.require_telegram))
    setRequirePhone(Boolean(f.require_phone))
    setVipOnly(Boolean(f.vip_only))
    setBirthdaySoon(Boolean(f.birthday_soon))
    setDaysBeforeEvent(f.days_before_event || 0)
    setEventDateFrom(f.event_date_from || null)
    setEventDateTo(f.event_date_to || null)
    setSegmentStatus(`Загружен список «${segment.name}» — фильтры выше применены.`)
  }, [])

  const removeSegment = useCallback(
    async (segment: SavedSegment) => {
      try {
        await call(`/segments/${encodeURIComponent(segment.id)}`, { method: 'DELETE' })
        if (activeSegmentId === segment.id) {
          setActiveSegmentId('')
          setSegmentName('')
        }
        loadSegments()
      } catch (err) {
        setSegmentStatus(err instanceof Error ? err.message : String(err))
      }
    },
    [activeSegmentId, call, loadSegments]
  )

  const audienceCalendarFilterActive = Boolean(
    daysBeforeEvent > 0 || eventDateFrom || eventDateTo
  )
  const audienceExtrasFilterActive = Boolean(
    channelKind ||
      requirePhone ||
      requireTelegram ||
      vipOnly ||
      loyaltyOnly ||
      birthdaySoon ||
      audienceCalendarFilterActive
  )
  const audienceClientFastFilterActive = Boolean(
    (salesFilter && salesFilter !== 'all') ||
      group ||
      audienceQDebounced.trim() ||
      channelKind ||
      requirePhone ||
      requireTelegram ||
      vipOnly ||
      loyaltyOnly ||
      birthdaySoon
  )

  const loadAudience = useCallback(
    async (opts?: { append?: boolean; quiet?: boolean }) => {
      const append = Boolean(opts?.append)
      // quiet = re-poll while the server catalog rebuilds: keep painted rows,
      // no wipe/seed — only the fetch + repaint.
      const quiet = Boolean(opts?.quiet) && !append
      const offset = append ? audienceNextOffsetRef.current : 0
      const gen = append ? audienceLoadGen.current : ++audienceLoadGen.current

      if (append) {
        if (audienceLoadMoreRef.current || !audienceHasMoreRef.current) {return}
        audienceLoadMoreRef.current = true
        setAudienceLoadingMore(true)
      } else if (quiet) {
        setLoading(true)
        setError('')
      } else if (audienceCalendarFilterActive) {
        // Calendar / days-before need server dates — don't paint stale chips.
        setAudiencePreview([])
        setAudience(0)
        audienceNextOffsetRef.current = 0
        audienceHasMoreRef.current = false
        setAudienceNextOffset(0)
        setAudienceHasMore(false)
        setLoading(true)
        setError('')
      } else {
        // Instant: filter local cache / painted chips while API revalidates.
        const local = seedClientsLocalPayload({
          salesFilter,
          q: audienceQDebounced,
          group,
          groupSource
        })
        const chipCount = findGroupChipCount(
          group,
          groupSource,
          groupOptionsMsRef.current,
          groupOptionsAiRef.current
        )
        const fastOpts = {
          q: audienceQDebounced,
          group,
          groupSource,
          salesFilter,
          channelKind,
          requirePhone,
          requireTelegram,
          vipOnly,
          birthdaySoon
        }
        if (local?.clients?.length) {
          const clients = audienceExtrasFilterActive
            ? filterClientRowsByAudience(local.clients, fastOpts)
            : local.clients
          setAudiencePreview(clients)
          setAudience(
            chipCount != null ? chipCount : local.matched_total || clients.length
          )
          if (local.counts) {
            setCounts(local.counts)
          }
          const nextOff = clients.length
          const more =
            Boolean(local.has_more) ||
            (chipCount != null && chipCount > clients.length) ||
            (local.matched_total || 0) > clients.length
          audienceNextOffsetRef.current = nextOff
          audienceHasMoreRef.current = more
          setAudienceNextOffset(nextOff)
          setAudienceHasMore(more)
          setLoading(true)
        } else {
          const painted = audiencePreviewRef.current
          if (audienceClientFastFilterActive && painted.length) {
            const filtered = filterClientRowsByAudience(painted, fastOpts)
            setAudiencePreview(filtered)
            setAudience(chipCount != null ? chipCount : filtered.length)
            const nextOff = filtered.length
            const more = chipCount != null ? chipCount > filtered.length : false
            audienceNextOffsetRef.current = nextOff
            audienceHasMoreRef.current = more
            setAudienceNextOffset(nextOff)
            setAudienceHasMore(more)
          } else if (audienceClientFastFilterActive) {
            // Filter active but nothing paintable — clear stale «все».
            setAudiencePreview([])
            setAudience(chipCount != null ? chipCount : 0)
            audienceNextOffsetRef.current = 0
            audienceHasMoreRef.current = Boolean(chipCount && chipCount > 0)
            setAudienceNextOffset(0)
            setAudienceHasMore(Boolean(chipCount && chipCount > 0))
          }
          setLoading(true)
        }
        setError('')
      }

      try {
        // Quiet re-polls refetch at least as many rows as already painted so
        // the list never shrinks mid-rebuild (backend caps limit at 500).
        const pageLimit = append
          ? AUDIENCE_APPEND_PAGE_SIZE
          : quiet
            ? Math.min(
                500,
                Math.max(AUDIENCE_PAGE_SIZE, audiencePreviewRef.current.length)
              )
            : AUDIENCE_PAGE_SIZE
        const fetchPage = () =>
          call<{
            counts?: Counts
            matched_total?: number
            clients?: ClientRow[]
            group_options?: GroupChipOption[]
            group_options_by_source?: { ms?: GroupChipOption[]; ai?: GroupChipOption[] }
            has_more?: boolean
            next_offset?: number
            revalidating?: boolean
          }>(`/clients?${audienceFilterParams({ offset, limit: pageLimit })}`, {
            timeoutMs: CLIENTS_FETCH_TIMEOUT_MS
          })

        let page: Awaited<ReturnType<typeof fetchPage>> | null = null
        // First (replace) loads must survive a post-deploy catalog rebuild:
        // the server 503s «rebuilding» for ~1–2 min after restart, and with a
        // single attempt the UI died at «0 · обновляем…» until a manual click.
        const maxAttempts = append ? 10 : 30
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          try {
            page = await fetchPage()
            if (
              !append ||
              (page.clients || []).length > 0 ||
              attempt >= maxAttempts - 1
            ) {
              break
            }
          } catch (err) {
            if (!isCatalogWarmingError(err) || attempt >= maxAttempts - 1) {
              throw err
            }
          }
          await new Promise(r =>
            setTimeout(r, audienceRetryDelayMs(attempt, append))
          )
          if (gen !== audienceLoadGen.current) {
            return
          }
        }
        if (!page) {
          throw new Error('catalog rebuilding; retry shortly')
        }

        if (gen !== audienceLoadGen.current) {
          return
        }

        const rows = page.clients || []
        setAudience(page.matched_total || 0)
        setCounts(page.counts || null)

        // First page always replaces — never merge stale unfiltered chips back in
        // (sales / group / extras looked broken because prior list was longer).
        const painted: ClientRow[] = append
          ? mergeClientPages(audiencePreviewRef.current, rows)
          : rows
        if (
          append &&
          !rows.length &&
          painted.length >= (page.matched_total || 0)
        ) {
          audienceHasMoreRef.current = false
          setAudienceHasMore(false)
        }
        setAudiencePreview(painted)

        if (gen !== audienceLoadGen.current) {
          return
        }

        const next =
          page.next_offset != null ? page.next_offset : offset + rows.length
        const more =
          page.has_more != null
            ? Boolean(page.has_more)
            : next < (page.matched_total || 0)
        audienceNextOffsetRef.current = next
        audienceHasMoreRef.current = more
        setAudienceNextOffset(next)
        setAudienceHasMore(more)

        if (!append) {
          // Rebuild in flight server-side → matched_total is partial (e.g. 15
          // of 152 on calendar filters). Poll effect below refetches quietly
          // until the catalog is complete — no dead «Нет клиентов…».
          setAudienceStaleHint(Boolean(page.revalidating))
        }

        const { ms, ai } = append
          ? {
              ms: groupOptionsMsRef.current,
              ai: groupOptionsAiRef.current
            }
          : resolveGroupOptionsBySource(page)
        if (!append) {
          setGroupOptionsMs(ms)
          setGroupOptionsAi(ai)
        }
        writeClientsLocalCache(
          clientsLocalCacheKey({
            salesFilter,
            q: audienceQDebounced,
            group,
            groupSource
          }),
          {
            saved_at: Date.now(),
            sales_filter: salesFilter,
            q: audienceQDebounced,
            group,
            group_source: groupSource,
            clients: painted.slice(0, AUDIENCE_LOCAL_CACHE_ROWS),
            counts: page.counts || null,
            matched_total: page.matched_total || 0,
            has_more: more,
            next_offset: next,
            group_options_ms: ms,
            group_options_ai: ai,
            synced_at_label: '',
            from_cache: false
          }
        )
      } catch (err) {
        if (gen !== audienceLoadGen.current) {
          return
        }
        if (isBenignRequestAbort(err)) {
          return
        }
        setError(
          isCatalogWarmingError(err)
            ? 'Каталог клиентов пересобирается на сервере (обычно 1–2 минуты после обновления). Нажмите любой фильтр чуть позже — список вернётся.'
            : err instanceof Error
              ? err.message
              : String(err)
        )

        if (!append && !audiencePreviewRef.current.length) {
          setAudiencePreview([])
        }
      } finally {
        if (append) {
          audienceLoadMoreRef.current = false
          setAudienceLoadingMore(false)
        } else if (gen === audienceLoadGen.current) {
          setLoading(false)
        }
      }
    },
    [
      audienceCalendarFilterActive,
      audienceClientFastFilterActive,
      audienceExtrasFilterActive,
      audienceFilterParams,
      audienceQDebounced,
      birthdaySoon,
      call,
      channelKind,
      group,
      groupSource,
      loyaltyOnly,
      requirePhone,
      requireTelegram,
      salesFilter,
      vipOnly
    ]
  )

  const refresh = useCallback(async () => {
    setError('')

    try {
      const list = await call<{ campaigns?: Campaign[] }>('/campaigns')
      setCampaigns(list.campaigns || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }

    await loadAudience()
  }, [call, loadAudience])

  useEffect(() => {
    void loadAudience()
    // loadAudience identity tracks filter deps — include it so group chip clicks always refetch.
  }, [loadAudience])

  // While the server catalog rebuilds (post-deploy restart), /clients answers
  // 200 with a partial catalog + revalidating=true. Same pattern as the
  // Clients page: poll quietly until fresh so filters recover on their own.
  useEffect(() => {
    if (!audienceStaleHint || loading) {
      return
    }

    const started = Date.now()
    const timer = window.setInterval(() => {
      if (Date.now() - started > CLIENTS_REVALIDATE_POLL_MAX_MS) {
        window.clearInterval(timer)
        setAudienceStaleHint(false)
        return
      }
      void loadAudience({ quiet: true })
    }, CLIENTS_REVALIDATE_POLL_MS)

    return () => window.clearInterval(timer)
  }, [audienceStaleHint, loading, loadAudience])

  useEffect(() => {
    void call<{ campaigns?: Campaign[] }>('/campaigns')
      .then(list => setCampaigns(list.campaigns || []))
      .catch(() => undefined)
  }, [call])

  const loadCachedDraft = useCallback(
    async (clientId: string, nextChannel = channel) => {
      outreachAbortRef.current?.abort()
      outreachGenRef.current += 1
      const gen = outreachGenRef.current
      const isCurrent = () => gen === outreachGenRef.current

      setGenerating(false)
      setError('')
      setActionStatus('Загружаем кэш…')

      try {
        const q = new URLSearchParams({
          client_id: clientId,
          channel: nextChannel || 'telegram'
        })
        const data = await call<{
          hit?: boolean
          draft?: {
            message?: string
            grounding_notes?: string
            source?: string
            status?: string
            client_name?: string
            title?: string
            facts?: ClientFacts | null
            sanity?: SanityResult | null
          } | null
          cache_backend?: string
        }>(`/campaigns/draft-cache?${q}`)

        if (!isCurrent()) {
          return
        }

        if (data.hit && data.draft?.message?.trim()) {
          restoreServerDraft({
            ...data.draft,
            client_name: selectedClientNameRef.current || data.draft.client_name,
            title:
              selectedClientNameRef.current
                ? `Черновик · ${selectedClientNameRef.current}`
                : data.draft.title,
            source: data.draft.source || data.cache_backend || 'redis-cache'
          })

          return
        }

        applyOfferText(
          '',
          'Нет кэша — нажмите «Сгенерировать AI» / «Букет из истории» / другое.'
        )
        setGenSource('')
      } catch (err) {
        if (!isCurrent()) {
          return
        }

        setError(err instanceof Error ? err.message : String(err))
        applyOfferText('', 'Кэш недоступен — можно сгенерировать кнопкой.')
      }
    },
    [applyOfferText, call, channel, restoreServerDraft]
  )

  /** Open Facts panel from /clients/{id} on select — independent of AI generate.
   *  Syncs personal TG first so replies after a mass Рассылка land in history.
   */
  const loadClientFacts = useCallback(
    async (clientId: string) => {
      const gen = ++factsGenRef.current
      try {
        // Force live pull (bypass 90s throttle) so inbound replies after send appear.
        try {
          const synced = await call<{
            conversation?: ClientConversation
            ai?: ClientDetail['ai']
          }>(`/clients/${encodeURIComponent(clientId)}/conversation/sync`, {
            method: 'POST',
            timeoutMs: 90_000,
            body: {
              refresh_ai: true,
              provider: MS_AI_DEFAULT_PROVIDER,
              model: MS_AI_DEFAULT_MODEL
            }
          })
          if (gen !== factsGenRef.current) {
            return
          }
          if (synced.conversation || synced.ai) {
            setFacts(prev => {
              if (!prev) {
                return {
                  client_id: clientId,
                  conversation: synced.conversation,
                  recommendation: synced.ai?.recommendation,
                  history_profile: synced.ai?.history_profile,
                  occasion_intent: synced.ai?.occasion_intent,
                  ai_source: synced.ai?.source,
                  ai_model: synced.ai?.model || MS_AI_DEFAULT_MODEL,
                  ai_provider: synced.ai?.provider || MS_AI_DEFAULT_PROVIDER
                }
              }
              return {
                ...prev,
                conversation: synced.conversation || prev.conversation,
                recommendation:
                  synced.ai?.recommendation || prev.recommendation,
                history_profile:
                  synced.ai?.history_profile || prev.history_profile,
                occasion_intent:
                  synced.ai?.occasion_intent || prev.occasion_intent,
                ai_source: synced.ai?.source || prev.ai_source,
                ai_model: synced.ai?.model || prev.ai_model || MS_AI_DEFAULT_MODEL,
                ai_provider:
                  synced.ai?.provider || prev.ai_provider || MS_AI_DEFAULT_PROVIDER
              }
            })
          }
        } catch {
          /* sync best-effort — detail fetch still paints orders/facts */
        }

        const detail = await call<ClientDetail>(
          `/clients/${encodeURIComponent(clientId)}`
        )
        if (gen !== factsGenRef.current) {
          return
        }
        setFacts(prev => {
          const next = factsFromDetail(detail)
          const prevCount = Number(prev?.conversation?.message_count || 0)
          const nextCount = Number(next.conversation?.message_count || 0)
          if (prev?.conversation && prevCount > nextCount) {
            next.conversation = prev.conversation
          }
          return next
        })
      } catch {
        // Keep optimistic seed from the chip row; do not wipe on soft fail.
        if (gen !== factsGenRef.current) {
          return
        }
      }
    },
    [call]
  )

  /** Force LLM generate (Сгенерировать AI). Result is saved to Redis on the server. */
  const loadOutreach = useCallback(
    async (clientId: string, nextChannel = channel) => {
      outreachAbortRef.current?.abort()
      const ac = new AbortController()
      outreachAbortRef.current = ac
      const gen = ++outreachGenRef.current
      const isCurrent = () => gen === outreachGenRef.current && !ac.signal.aborted

      setGenerating(true)
      setError('')

      const knownName = selectedClientNameRef.current.trim()

      if (knownName) {
        setTitle(`Черновик · ${knownName}`)
      }

      applyOfferText('', 'Генерируем креативный текст…')
      setSanity(null)
      setGroundingNotes('')
      setGenSource('')

      try {
        let streamed = ''

        await callStream('/campaigns/generate/stream', {
          method: 'POST',
          timeoutMs: OUTREACH_AI_TIMEOUT_MS,
          signal: ac.signal,
          body: {
            client_id: clientId,
            channel: nextChannel,
            refresh_ai: true,
            seller_name: sellerName,
            seller_facts: sellerFacts
          },
          onEvent: raw => {
            if (!isCurrent()) {
              return
            }

            if (!raw || typeof raw !== 'object') {
              return
            }

            const ev = raw as Record<string, unknown>
            const type = String(ev.type || '')

            if (type === 'status' && typeof ev.text === 'string') {
              setActionStatus(ev.text)
            } else if (type === 'delta' && typeof ev.text === 'string') {
              streamed += ev.text
              applyOfferText(streamed, 'Генерируем… (поток)')
            } else if (type === 'replace' && typeof ev.text === 'string') {
              streamed = ev.text
              applyOfferText(streamed, 'Текст обновлён')
            } else if (type === 'error') {
              const err = String(ev.error || 'stream error')
              setError(
                /403|security policy|access denied/i.test(err)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${err}`
                  : `LLM: ${err}`
              )
            } else if (type === 'done') {
              const nextFacts =
                ev.facts && typeof ev.facts === 'object' ? (ev.facts as ClientFacts) : null
              const nextSanity =
                ev.sanity && typeof ev.sanity === 'object'
                  ? (ev.sanity as SanityResult)
                  : null
              const nextNotes =
                typeof ev.grounding_notes === 'string' ? ev.grounding_notes : ''
              const nextSource = typeof ev.source === 'string' ? ev.source : ''

              if (nextFacts) {
                setFacts(nextFacts)
              }

              if (nextNotes) {
                setGroundingNotes(nextNotes)
              }

              if (nextSource) {
                setGenSource(nextSource)
              }

              if (nextSanity) {
                setSanity(nextSanity)
              }

              const msg = pickOutreachMessage(ev) || streamed
              const status = nextSanity?.auto_revised
                ? 'AI сгенерировал текст (sanity поправил формулировку).'
                : AI_GENERATED_STATUS

              if (msg) {
                streamed = msg
                applyOfferText(msg, status)
              } else {
                setError('Сервер не вернул текст сообщения. Попробуйте ещё раз.')
                setActionStatus('')
              }

              if (typeof ev.error === 'string' && ev.error) {
                setError(
                  /403|security policy|access denied/i.test(ev.error)
                    ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                    : `LLM: ${ev.error}`
                )
              }

              const remoteName =
                typeof ev.client_name === 'string' ? ev.client_name.trim() : ''
              const localName = selectedClientNameRef.current.trim()

              if (remoteName && !localName) {
                setSelectedClientName(remoteName)
                selectedClientNameRef.current = remoteName
                setTitle(`Черновик · ${remoteName}`)
              } else if (localName) {
                setTitle(`Черновик · ${localName}`)
              } else if (remoteName) {
                setTitle(`Черновик · ${remoteName}`)
              }

              if (ev.cached) {
                setGenSource(prev => prev || 'redis-cache')
              }
            }
          }
        })
      } catch (err) {
        if (!isCurrent()) {
          return
        }

        if (err instanceof Error && err.name === 'AbortError') {
          return
        }

        setError(err instanceof Error ? err.message : String(err))
        setActionStatus('')
      } finally {
        if (isCurrent()) {
          setGenerating(false)
        }
      }
    },
    [applyOfferText, callStream, channel, sellerFacts, sellerName]
  )

  useEffect(() => {
    if (!prefillReady || !selectedClientId) {
      return
    }

    // Default: load durable Redis/file cache — never auto-LLM on select/channel.
    // Facts panel loads in parallel so chip click shows client data immediately.
    void loadCachedDraft(selectedClientId, channel)
    void loadClientFacts(selectedClientId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillReady, selectedClientId, channel])

  // Poll the selected client's TG thread so inbound replies land in the Facts
  // «TG conversation» block without re-clicking the chip (30s, no AI regen).
  useEffect(() => {
    if (!prefillReady || !selectedClientId) {
      return
    }
    const cid = selectedClientId
    const timer = setInterval(() => {
      void call<{ conversation?: ClientConversation }>(
        `/clients/${encodeURIComponent(cid)}/conversation/sync?refresh_ai=false`,
        { method: 'POST', timeoutMs: 90_000 }
      )
        .then(data => {
          if (selectedClientIdRef.current !== cid || !data.conversation) {
            return
          }
          setFacts(prev =>
            prev ? { ...prev, conversation: data.conversation } : prev
          )
        })
        .catch(() => undefined)
    }, 30_000)

    return () => clearInterval(timer)
  }, [call, prefillReady, selectedClientId])

  // Persist manual edits / button results to Redis/file so reopen skips LLM.
  useEffect(() => {
    const cid = selectedClientId
    const msg = offer.trim()

    if (!cid || !msg || generating || rewriting || paraphrasing || suggestingBouquet) {
      return
    }

    if (msg.length < 8) {
      return
    }

    const t = setTimeout(() => {
      void call('/campaigns/draft-cache', {
        method: 'PUT',
        body: {
          client_id: cid,
          channel,
          message: msg,
          grounding_notes: groundingNotes,
          source: genSource || 'manual',
          status: actionStatus || AI_GENERATED_STATUS,
          client_name: selectedClientNameRef.current || '',
          title: title || '',
          facts: facts || {},
          sanity: sanity || null
        }
      }).catch(() => undefined)
    }, 700)

    return () => clearTimeout(t)
  }, [
    actionStatus,
    call,
    channel,
    facts,
    genSource,
    generating,
    groundingNotes,
    offer,
    paraphrasing,
    rewriting,
    sanity,
    selectedClientId,
    suggestingBouquet,
    title
  ])

  const selectAudienceClient = (row: ClientRow) => {
    const plan = planAudienceChipClick({
      pickMode: 'single',
      rowId: row.id || '',
      rowName: row.name || '',
      rowPhone: row.phone || '',
      rowTgNick: row.tg_nick || '',
      selectedIds: selectedClientIds
    })

    if (!plan.ok) {
      setError(
        'У клиента нет id МойСклад — нажмите «Обновить» на Клиентах и выберите снова'
      )
      return
    }

    setChannel('telegram')
    setContactPickerId(plan.focusId)
    setSelectedClientIds(plan.selectedIds)
    outreachAbortRef.current?.abort()
    outreachGenRef.current += 1
    factsGenRef.current += 1
    // Always focus compose + seed Facts — multi mode used to no-op / leave empty.
    applyClientSelectionUi(
      plan.focusId,
      plan.focusName,
      seedFactsFromAudienceRow(row) as ClientFacts
    )
  }

  const selectContactFromPicker = (contactId: string) => {
    setContactPickerId(contactId)
    if (!contactId) {
      return
    }

    const contact = outreachContacts.find(c => c.id === contactId)
    const name = contact?.name || contact?.label || contactId
    const plan = planAudienceChipClick({
      pickMode: 'single',
      rowId: contactId,
      rowName: name,
      rowPhone: contact?.phone || '',
      rowTgNick: contact?.tg_nick || '',
      selectedIds: selectedClientIds
    })

    if (!plan.ok) {
      return
    }

    setChannel('telegram')
    setSelectedClientIds(plan.selectedIds)
    outreachAbortRef.current?.abort()
    outreachGenRef.current += 1
    factsGenRef.current += 1
    applyClientSelectionUi(
      plan.focusId,
      plan.focusName,
      seedFactsFromAudienceRow({
        id: contactId,
        name,
        phone: contact?.phone,
        tg_nick: contact?.tg_nick
      }) as ClientFacts
    )
  }

  const resolveOutreachContactQuery = async () => {
    const q = addContactQuery.trim() || addContactNick.trim() || addContactChatId.trim()
    if (!q) {
      setError('Укажите @ник, t.me/… или numeric chat id — расшифруем через Bot API')
      return
    }
    setAddContactResolving(true)
    setError('')
    try {
      const data = await call<{
        tg_nick?: string
        tg_chat_id?: string
        name?: string
        resolved_via?: string
      }>('/campaigns/telegram-contacts/resolve', {
        method: 'POST',
        body: {
          query: addContactQuery.trim(),
          tg_nick: addContactNick.trim(),
          tg_chat_id: addContactChatId.trim()
        }
      })
      if (data.tg_nick) {
        setAddContactNick(data.tg_nick)
      }
      if (data.tg_chat_id) {
        setAddContactChatId(String(data.tg_chat_id))
      }
      if (data.name && !addContactName.trim()) {
        setAddContactName(data.name)
      }
      setActionStatus(
        `✓ Расшифровано (${data.resolved_via || 'api'}): ` +
          `${data.tg_nick ? `@${data.tg_nick}` : ''} ${data.tg_chat_id || ''}`.trim()
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAddContactResolving(false)
    }
  }

  const addCustomOutreachContact = async () => {
    const nick = addContactNick.trim().replace(/^@/, '')
    const chatId = addContactChatId.trim()
    const query = addContactQuery.trim()
    if (!nick && !chatId && !query) {
      setError('Укажите @ник, t.me/… или numeric chat id — расшифруем через Bot API')
      return
    }

    setAddContactSaving(true)
    setError('')
    try {
      const data = await call<{
        contact?: OutreachContact & { resolved_via?: string }
      }>('/campaigns/telegram-contacts', {
        method: 'POST',
        body: {
          name: addContactName.trim(),
          tg_nick: nick,
          tg_chat_id: chatId,
          query,
          resolve: true
        }
      })
      const added = data.contact
      if (added?.id) {
        // Pin the new row at the top of «Добавленные» immediately — don't wait
        // for the full refresh or hunt among 300 Telegram peers.
        setOutreachContacts(prev => {
          const row = { ...added, source: added.source || 'custom' }
          const rest = prev.filter(c => c.id !== row.id)
          return [row, ...rest]
        })
        selectContactFromPicker(added.id)
      }
      await loadOutreachContacts()
      if (added?.id) {
        selectContactFromPicker(added.id)
      }
      setAddContactName('')
      setAddContactNick('')
      setAddContactChatId('')
      setAddContactQuery('')
      setAddContactOpen(false)
      setActionStatus(
        `✓ В «Добавленные»: ` +
          (data.contact?.label || nick || chatId || query)
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setAddContactSaving(false)
    }
  }

  const [lastSentClientIds, setLastSentClientIds] = useState<string[]>([])
  const [replies, setReplies] = useState<
    Array<{
      client_id?: string
      client_name?: string
      tg_nick?: string
      preview?: string
      message_count?: number
      updated_at?: string
    }>
  >([])
  const [repliesBusy, setRepliesBusy] = useState(false)
  const [repliesMeta, setRepliesMeta] = useState('')

  // ── Массовая рассылка: background job + per-recipient status ──
  const [massText, setMassText] = useState('')
  const [massIds, setMassIds] = useState<string[]>([])
  const [massPicking, setMassPicking] = useState(false)
  const [massStarting, setMassStarting] = useState(false)
  const [massCancelling, setMassCancelling] = useState(false)
  const [massJob, setMassJob] = useState<MassJobSummary | null>(null)
  const [massRows, setMassRows] = useState<MassRecipientRow[]>([])
  const [massModalOpen, setMassModalOpen] = useState(false)
  const [massConfirmOpen, setMassConfirmOpen] = useState(false)
  const [massRowFilter, setMassRowFilter] = useState<'all' | 'failed' | 'ok'>('all')
  const [historyJobs, setHistoryJobs] = useState<MassJobSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyOpenJobId, setHistoryOpenJobId] = useState<string | null>(null)
  const [historyRows, setHistoryRows] = useState<MassRecipientRow[]>([])
  const [historyRowsLoading, setHistoryRowsLoading] = useState(false)

  const [historyError, setHistoryError] = useState('')

  const [chatTurns, setChatTurns] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatModel, setChatModel] = useState<'deepseek' | 'grok' | 'gpt'>('deepseek')
  const [skillPromptText, setSkillPromptText] = useState('')
  const [skillNote, setSkillNote] = useState('')

  // Chat resets per client — the fact anchor changes with the selection.
  useEffect(() => {
    setChatTurns([])
    setChatInput('')
    setSkillPromptText('')
  }, [selectedClientId])

  const sendChatTurn = useCallback(async () => {
    const ask = chatInput.trim()
    if (!ask || chatBusy || !selectedClientId) {return}
    const model = CHAT_MODEL_IDS[chatModel]
    const turns = [...chatTurns, { role: 'user' as const, content: ask }]
    setChatTurns(turns)
    setChatInput('')
    setChatBusy(true)
    try {
      const data = await call<{ reply?: string; message?: string }>('/campaigns/chat', {
        method: 'POST',
        timeoutMs: 90_000,
        body: {
          client_id: selectedClientId,
          channel,
          draft: offerRef.current || offer,
          messages: turns,
          provider: 'openrouter',
          model,
          seller_name: sellerName,
          seller_facts: sellerFacts
        }
      })
      const reply = (data.reply || '').trim() || 'Готово.'
      setChatTurns(prev => [...prev, { role: 'assistant', content: reply }])
      const msg = (data.message || '').trim()
      if (msg) {
        setOffer(msg)
        offerRef.current = msg
        setGenSource('chat')
      }
    } catch (err) {
      setChatTurns(prev => [
        ...prev,
        {
          role: 'assistant',
          content: `Ошибка: ${err instanceof Error ? err.message : String(err)}`
        }
      ])
    } finally {
      setChatBusy(false)
    }
  }, [call, channel, chatBusy, chatInput, chatModel, chatTurns, offer, selectedClientId, sellerFacts, sellerName])

  const saveSkill = useCallback(
    async (text: string) => {
      try {
        await call('/campaigns/skills', {
          method: 'POST',
          body: { text, notes: '' }
        })
        setSkillNote('✓ Сохранено в навык — следующие генерации учтут этот стиль')
      } catch (err) {
        setSkillNote(err instanceof Error ? err.message : String(err))
      } finally {
        setSkillPromptText('')
      }
    },
    [call]
  )

  const [sentFeed, setSentFeed] = useState<
    {
      client_id?: string
      client_name?: string
      tg_nick?: string
      text?: string
      ts?: string
      status?: string
      source?: string
    }[]
  >([])
  const [sentFeedOpen, setSentFeedOpen] = useState(false)

  const loadSentFeed = useCallback(async () => {
    try {
      const data = await call<{ messages?: typeof sentFeed }>(
        '/campaigns/sent-history?limit=300'
      )
      setSentFeed(data.messages || [])
    } catch {
      /* best-effort */
    }
  }, [call])

  const loadSendHistory = useCallback(async () => {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const data = await call<{ jobs?: MassJobSummary[] }>(
        '/campaigns/mass-send/history?limit=20'
      )
      setHistoryJobs(data.jobs || [])
    } catch (err) {
      setHistoryJobs([])
      setHistoryError(err instanceof Error ? err.message : String(err))
    } finally {
      setHistoryLoading(false)
    }
  }, [call])

  useEffect(() => {
    void loadSendHistory()
    void loadSentFeed()
  }, [loadSendHistory, loadSentFeed])

  // When a live blast finishes, refresh section 3 so the job stays visible.
  const massJobStatus = massJob?.status
  useEffect(() => {
    if (!massJob?.id) {
      return
    }
    if (isMassJobActive(massJobStatus)) {
      return
    }
    void loadSendHistory()
  }, [massJob?.id, massJobStatus, loadSendHistory])

  const toggleHistoryJob = useCallback(
    async (jobId: string) => {
      if (historyOpenJobId === jobId) {
        setHistoryOpenJobId(null)
        setHistoryRows([])
        return
      }
      setHistoryOpenJobId(jobId)
      setHistoryRows([])
      setHistoryRowsLoading(true)
      try {
        const data = await call<{
          job?: MassJobSummary & { recipients?: MassRecipientRow[] }
        }>(`/campaigns/mass-send/${encodeURIComponent(jobId)}?limit=2000`)
        setHistoryRows(data.job?.recipients || [])
      } catch {
        setHistoryRows([])
      } finally {
        setHistoryRowsLoading(false)
      }
    },
    [call, historyOpenJobId]
  )
  const [dialogClient, setDialogClient] = useState<{ id: string; name?: string } | null>(
    null
  )
  const massJobRef = useRef<MassJobSummary | null>(null)
  massJobRef.current = massJob
  const massRowsRef = useRef<MassRecipientRow[]>([])
  massRowsRef.current = massRows
  /** Prevents overlapping polls when a slice request outlives the interval. */
  const massPollBusyRef = useRef(false)

  const pollMassJob = useCallback(async () => {
    const jobId = massJobRef.current?.id
    if (!jobId || massPollBusyRef.current) {
      return
    }
    massPollBusyRef.current = true
    try {
      // Terminal rows never change — poll from the first live index. When the
      // job is finished, keep paging until the local log holds every row.
      for (let hop = 0; hop < 20; hop += 1) {
        const offset = terminalPrefixLength(massRowsRef.current)
        const data = await call<{
          job?: MassJobSummary & {
            recipients?: MassRecipientRow[]
            results_offset?: number
          }
        }>(
          `/campaigns/mass-send/${encodeURIComponent(jobId)}?offset=${offset}&limit=500`
        )
        const next = data.job
        if (!next?.id || next.id !== massJobRef.current?.id) {
          return
        }
        const { recipients, results_offset, ...summary } = next
        setMassJob(summary)
        massJobRef.current = summary
        const merged = overlayMassRows(
          massRowsRef.current,
          recipients || [],
          results_offset ?? offset
        )
        massRowsRef.current = merged
        setMassRows(merged)
        const done = !isMassJobActive(summary.status)
        const complete = terminalPrefixLength(merged) >= (summary.total || 0)
        if (!done || complete || !(recipients || []).length) {
          return
        }
      }
    } catch {
      // Stale snapshot is fine — next tick retries.
    } finally {
      massPollBusyRef.current = false
    }
  }, [call])

  useEffect(() => {
    if (!massJob?.id || !isMassJobActive(massJob.status)) {
      return
    }
    void pollMassJob()
    const timer = setInterval(() => {
      void pollMassJob()
    }, 2000)
    return () => clearInterval(timer)
    // Re-arm only on job switch / lifecycle edge — not every counter tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [massJob?.id, isMassJobActive(massJob?.status), pollMassJob])

  // Reattach after a UI reload: a blast may still be running server-side.
  useEffect(() => {
    void call<{ job?: MassJobSummary | null }>('/campaigns/mass-send/latest')
      .then(data => {
        const job = data.job
        if (job?.id && isMassJobActive(job.status)) {
          setMassJob(job)
          setMassModalOpen(true)
        }
      })
      .catch(() => undefined)
  }, [call])

  const massSelectAudience = async () => {
    setMassPicking(true)
    setError('')
    try {
      const params = audienceFilterParams({
        limit: MASS_AUDIENCE_SELECT_CAP,
        offset: 0
      })
      const data = await call<{ ids?: string[]; matched_total?: number }>(
        `/clients/ids?${params}`,
        { timeoutMs: 180_000 }
      )
      const ids = data.ids || []
      setMassIds(ids)
      setActionStatus(
        ids.length
          ? `✓ В массовую рассылку взято ${ids.length}` +
              ((data.matched_total || 0) > ids.length
                ? ` из ${data.matched_total} (потолок ${MASS_AUDIENCE_SELECT_CAP})`
                : ' — вся текущая аудитория')
          : 'Фильтр пуст — сузьте аудиторию выше.'
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setMassPicking(false)
    }
  }

  const massStart = async () => {
    const text = massText.trim()
    if (!text || !massIds.length || massStarting) {
      return
    }
    setMassConfirmOpen(false)
    setMassStarting(true)
    setError('')
    try {
      const data = await call<{ job?: MassJobSummary }>('/campaigns/mass-send', {
        method: 'POST',
        body: {
          message: text,
          client_ids: massIds,
          channel,
          deliver: true,
          stop_on_error: false
        }
      })
      if (data.job?.id) {
        setMassRows([])
        massRowsRef.current = []
        setMassRowFilter('all')
        setMassJob(data.job)
        setMassModalOpen(true)
        // «Собрать ответы» works off this cohort after the blast.
        setLastSentClientIds(ids => mergeUniqueIds(ids, massIds))
        setActionStatus(`Рассылка запущена: ${data.job.total || massIds.length} получателей`)
        void loadSendHistory()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setMassStarting(false)
    }
  }

  const massCancel = async () => {
    const jobId = massJobRef.current?.id
    if (!jobId || massCancelling) {
      return
    }
    setMassCancelling(true)
    try {
      await call(`/campaigns/mass-send/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST'
      })
      await pollMassJob()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setMassCancelling(false)
    }
  }

  const collectReplies = async () => {
    const cohort =
      lastSentClientIds.length > 0
        ? lastSentClientIds
        : selectedClientId
          ? [selectedClientId]
          : []
    if (!cohort.length) {
      setError(
        'Нет когорты для сбора — сначала отправьте сообщение клиенту или выберите его в списке.'
      )
      return
    }
    setRepliesBusy(true)
    setError('')
    setRepliesMeta(`Синхронизирую ответы (${cohort.length})…`)
    try {
      const data = await call<{
        awaiting?: number
        synced?: number
        inbound_imported?: number
        replies?: Array<{
          client_id?: string
          client_name?: string
          tg_nick?: string
          preview?: string
          message_count?: number
          updated_at?: string
        }>
      }>('/campaigns/replies/collect', {
        method: 'POST',
        timeoutMs: 300_000,
        body: {
          client_ids: cohort.slice(0, 200),
          sync: true,
          limit: 200
        }
      })
      setReplies(data.replies || [])
      setRepliesMeta(
        `Ждут ответа: ${data.awaiting ?? 0}` +
          (data.inbound_imported
            ? ` · новых входящих: ${data.inbound_imported}`
            : '') +
          (data.synced ? ` · sync ${data.synced}` : '')
      )
      setActionStatus(
        (data.awaiting || 0) > 0
          ? `✓ Собраны ответы: ${data.awaiting} клиентов ждут ответа`
          : '✓ Sync готов — новых входящих в когорте пока нет'
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setRepliesMeta('')
    } finally {
      setRepliesBusy(false)
    }
  }

  const markSentToConversation = async () => {
    const draft = offerRef.current.trim()
    const clientId = (selectedClientId || '').trim()

    if (!clientId) {
      setError('Выберите клиента в списке аудитории — исходящее уйдёт в Telegram / историю.')

      return
    }

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setCheckingSanity(true)
    setError('')
    setActionStatus(
      channel.startsWith('telegram')
        ? 'Отправка через Telegram…'
        : 'Пишем исходящее в историю…'
    )

    try {
      const data = await call<{
        conversation?: ClientConversation
        facts?: ClientFacts
        deep_link?: string
        delivery?: { ok?: boolean; detail?: string; error?: string; skipped?: boolean }
      }>('/campaigns/mark-sent', {
        method: 'POST',
        body: {
          message: draft,
          channel,
          client_id: clientId,
          open_deep_link: true,
          deliver: true
        }
      })

      setLastSentClientIds(ids => mergeUniqueIds(ids, [clientId]))

      if (data.facts) {
        setFacts(data.facts)
      } else if (data.conversation) {
        setFacts(prev => (prev ? { ...prev, conversation: data.conversation } : prev))
      }

      if (data.delivery?.ok) {
        applyOfferText(draft, '✓ Отправлено. Можно выбрать следующего клиента.')
        setActionStatus('✓ Ушло. Выберите другого клиента или соберите ответы.')
      } else if (channel.startsWith('telegram') && data.delivery && !data.delivery.skipped) {
        const detail = data.delivery.detail || data.delivery.error || 'ошибка'
        applyOfferText(draft, `⚠ В историю записано; Bot API: ${detail}`)
        setError(`Telegram: ${detail}`)
        setActionStatus('')
      } else {
        applyOfferText(draft, '✓ Исходящее добавлено в историю (лейбл исходящее).')
        setActionStatus('✓ В истории. Можно выбрать следующего клиента.')
      }

      if (data.deep_link) {
        window.open(data.deep_link, '_blank', 'noopener')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
      setBatchProgress('')
    } finally {
      setCheckingSanity(false)
    }
  }

  const regenerateAi = async () => {
    if (!selectedClientId) {
      setError('Сначала выберите клиента из аудитории или карточки.')

      return
    }

    await loadOutreach(selectedClientId, channel)
  }

  const humanizeDraft = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setRewriting(true)
    setError('')
    // Wipe draft while rewriting — seller sees the new sales pass replace it.
    applyOfferText('', 'Переписываем продающе и по-человечески…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/rewrite/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Переписываем… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed || draft
            streamed = msg
            applyOfferText(
              msg,
              msg.trim() === draft
                ? 'Переписали тон (текст почти тот же — правки лёгкие).'
                : 'Текст обновлён: продающе и по-человечески.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      // Restore draft if rewrite failed after we cleared the field.
      applyOfferText(draft, '')
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setRewriting(false)
    }
  }

  const suggestHistoricalBouquet = async () => {
    if (!selectedClientId) {
      setError('Сначала выберите клиента — нужна история заказов.')

      return
    }

    setSuggestingBouquet(true)
    setError('')
    applyOfferText('', 'Подбираем конкретный букет из истории…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/suggest-bouquet/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          client_id: selectedClientId,
          channel,
          refresh_ai: false,
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Букет из истории… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed
            streamed = msg
            applyOfferText(
              msg || '',
              msg
                ? 'Предложен конкретный букет из истории заказов.'
                : 'Не удалось предложить букет — проверьте историю.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setSuggestingBouquet(false)
    }
  }

  const paraphraseDraft = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setParaphrasing(true)
    setError('')
    applyOfferText('', 'Полная парафраза — другой текст, тот же смысл…')
    setSanity(null)

    try {
      let streamed = ''

      await callStream('/campaigns/paraphrase/stream', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts
        },
        onEvent: raw => {
          if (!raw || typeof raw !== 'object') {
            return
          }

          const ev = raw as Record<string, unknown>
          const type = String(ev.type || '')

          if (type === 'status' && typeof ev.text === 'string') {
            setActionStatus(ev.text)
          } else if (type === 'delta' && typeof ev.text === 'string') {
            streamed += ev.text
            applyOfferText(streamed, 'Парафраза… (поток)')
          } else if (type === 'replace' && typeof ev.text === 'string') {
            streamed = ev.text
            applyOfferText(streamed, 'Текст обновлён')
          } else if (type === 'error') {
            setError(String(ev.error || 'stream error'))
          } else if (type === 'done') {
            const msg = pickOutreachMessage(ev) || streamed || draft
            streamed = msg
            const same = msg.trim() === draft
            applyOfferText(
              msg,
              same
                ? 'Парафраза почти совпала с исходником — попробуйте ещё раз.'
                : 'Полная парафраза: формулировки сменены, факты те же.'
            )

            if (typeof ev.grounding_notes === 'string' && ev.grounding_notes) {
              setGroundingNotes(ev.grounding_notes)
            }

            if (typeof ev.source === 'string' && ev.source) {
              setGenSource(ev.source)
            }

            if (ev.facts && typeof ev.facts === 'object') {
              setFacts(ev.facts as ClientFacts)
            }

            if (ev.sanity && typeof ev.sanity === 'object') {
              setSanity(ev.sanity as SanityResult)
            }

            if (typeof ev.error === 'string' && ev.error) {
              setError(
                /403|security policy|access denied/i.test(ev.error)
                  ? `LLM недоступен (OpenRouter 403). Проверьте ключ. ${ev.error}`
                  : `LLM: ${ev.error}`
              )
            }
          }
        }
      })
    } catch (err) {
      applyOfferText(draft, '')
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setParaphrasing(false)
    }
  }

  const runSanityCheck = async () => {
    const draft = offerRef.current.trim()

    if (!draft) {
      setError('Сначала введите или сгенерируйте текст сообщения.')

      return
    }

    setCheckingSanity(true)
    setError('')
    setActionStatus('Проверяем смысл…')

    try {
      const data = await call<{
        message?: string
        sanity?: SanityResult
        facts?: ClientFacts
      }>('/campaigns/sanity', {
        method: 'POST',
        timeoutMs: OUTREACH_AI_TIMEOUT_MS,
        body: {
          message: draft,
          channel,
          client_id: selectedClientId || '',
          seller_name: sellerName,
          seller_facts: sellerFacts,
          apply_revision: true
        }
      })

      const msg = pickOutreachMessage(data) || draft
      const ok = data.sanity?.ok !== false
      const revised = Boolean(data.sanity?.auto_revised || (msg.trim() && msg.trim() !== draft))

      applyOfferText(
        msg,
        revised
          ? 'Смысл: текст скорректирован (см. замечания справа).'
          : ok
            ? 'Смысл в порядке — текст оставлен как есть.'
            : `Смысл: ${(data.sanity?.issues || []).join('; ') || 'есть замечания'}.`
      )

      if (data.sanity) {
        setSanity(data.sanity)
      }

      if (data.facts && Object.keys(data.facts).length) {
        setFacts(data.facts)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionStatus('')
    } finally {
      setCheckingSanity(false)
    }
  }

  const createDraft = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setBatchProgress('')

    try {
      // Mass personalize paused — individual send / single campaign create only.
      await call('/campaigns', {
        method: 'POST',
        body: {
          title,
          channel,
          mode: 'manual',
          offer,
          sales_filter: salesFilter,
          group,
          channel_kind: channelKind,
          require_phone: requirePhone,
          require_telegram: requireTelegram,
          vip_only: vipOnly,
          birthday_soon: birthdaySoon,
          group_source: groupSource,
          days_before_event: daysBeforeEvent,
          event_date_from: eventDateFrom || '',
          event_date_to: eventDateTo || '',
          personalize,
          client_id: selectedClientId || '',
          generate_ai: false,
          seller_name: sellerName,
          seller_facts: sellerFacts
        }
      })

      if (!selectedClientId) {
        setOffer('')
      }

      await refresh()
      setDraftsOpen(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const canSendOne =
    Boolean(selectedClientId) &&
    Boolean(offer.trim()) &&
    !checkingSanity &&
    !generating &&
    !rewriting &&
    !suggestingBouquet &&
    !paraphrasing

  return (
    <div className="ms-page" data-selectable-text="true">
      <div className="ms-page-header">
        <div>
          <h1>Рассылки</h1>
          <p className="ms-muted">
            Фильтр → список → клик клиента → текст → отправить. Массовая позже.
          </p>
        </div>
        <div className="ms-page-header-actions">
          <button
            className="ms-btn"
            onClick={() => setDraftsOpen(true)}
            type="button"
          >
            Черновики{campaigns.length ? ` · ${campaigns.length}` : ''}
          </button>
          <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
            ← Клиенты
          </button>
        </div>
      </div>

      <section aria-label="Индивидуальная отправка" className="ms-mass-rail ms-individual-rail">
        <p className="ms-mass-hint">
          {selectedClientId
            ? `Клиент: ${selectedClientName || facts?.name || selectedClientId}. Правьте текст ниже и жмите «Отправить».`
            : loading && audiencePreview.length === 0
              ? 'Загружаем список клиентов…'
              : audiencePreview.length > 0
                ? `В фильтре ${audience} чел. — нажмите клиента в списке ниже.`
                : audience > 0
                  ? `В фильтре ${audience} чел. Список ещё грузится — подождите секунду.`
                  : 'Сузьте фильтры — пока matched = 0.'}
        </p>
        {!selectedClientId && audiencePreview.length > 0 ? (
          <div className="ms-chips ms-rail-audience-chips">
            {audiencePreview.slice(0, 12).map(row => {
              const nick = (row.tg_nick || '').replace(/^@/, '')
              return (
                <button
                  className="ms-chip"
                  key={`rail-${row.id || row.name}`}
                  onClick={() => selectAudienceClient(row)}
                  title={nick ? `@${nick}` : row.phone || row.id}
                  type="button"
                >
                  {row.name || row.phone || row.id}
                  {nick ? <span>@{nick}</span> : null}
                </button>
              )
            })}
            {audience > Math.min(12, audiencePreview.length) ? (
              <span className="ms-muted">
                +{audience - Math.min(12, audiencePreview.length)} в полном списке ↓
              </span>
            ) : null}
          </div>
        ) : null}
        <div className="ms-mass-rail-actions">
          {selectedClientId && offer.trim() ? (
            <button
              className="ms-btn ms-btn-primary"
              disabled={!canSendOne}
              onClick={() => void markSentToConversation()}
              type="button"
            >
              {checkingSanity ? 'Отправляю…' : 'Отправить в Telegram'}
            </button>
          ) : null}
          {selectedClientId || lastSentClientIds.length > 0 ? (
            <button
              className="ms-btn"
              disabled={repliesBusy}
              onClick={() => void collectReplies()}
              type="button"
            >
              {repliesBusy ? 'Собираю…' : 'Собрать ответы'}
            </button>
          ) : null}
          {lastSentClientIds.length ? (
            <span className="ms-mass-count">
              Отправлено в этой сессии: <strong>{lastSentClientIds.length}</strong>
            </span>
          ) : null}
        </div>
        {actionStatus ? <p className="ms-muted ms-mass-status">{actionStatus}</p> : null}
        {(repliesMeta || replies.length > 0) && (
          <div className="ms-replies-inbox">
            <p className="ms-ai-label">Ответы клиентов</p>
            {repliesMeta ? <p className="ms-muted">{repliesMeta}</p> : null}
            {replies.length ? (
              <ul className="ms-replies-list">
                {replies.map(row => (
                  <li key={row.client_id || row.preview}>
                    <button
                      className="ms-link-btn"
                      onClick={() => {
                        if (!row.client_id) {
                          return
                        }
                        setDialogClient({
                          id: row.client_id,
                          name: row.client_name || row.tg_nick || ''
                        })
                      }}
                      title="Открыть диалог и ответить прямо здесь"
                      type="button"
                    >
                      {row.client_name || row.tg_nick || row.client_id}
                    </button>
                    {row.preview ? (
                      <span className="ms-muted"> — {row.preview}</span>
                    ) : null}
                    {row.client_id ? (
                      <button
                        className="ms-link-btn ms-replies-to-draft"
                        onClick={() => {
                          setSelectedClientId(row.client_id!)
                          setSelectedClientName(row.client_name || '')
                          setContactPickerId(row.client_id!)
                          void loadOutreach(row.client_id!, channel)
                        }}
                        title="Выбрать клиента в блоке 1:1 (AI-черновик)"
                        type="button"
                      >
                        в черновик
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        )}
      </section>

      <section className="ms-audience-builder">
        <h2 className="ms-section-title">1. Аудитория</h2>
        <p className="ms-muted">
          Найдено (после дедупа): <strong>{audience}</strong>
          {loading ? ' · обновляем…' : audienceStaleHint ? ' · каталог пересобирается, список дополняется…' : ''}
          {selectedClientId ? (
            <>
              {' '}
              · выбран <strong>{selectedClientName || facts?.name || selectedClientId}</strong>
              <button
                className="ms-link-btn"
                onClick={() => {
                  outreachAbortRef.current?.abort()
                  outreachGenRef.current += 1
                  factsGenRef.current += 1
                  setSelectedClientId(null)
                  setSelectedClientName('')
                  setSelectedClientIds([])
                  selectedClientNameRef.current = ''
                  setFacts(null)
                  setGroundingNotes('')
                  setGenSource('')
                  setSanity(null)
                  setActionStatus('')
                  applyOfferText('', '')
                  setTitle('Рассылка по фильтрам')
                  setGenerating(false)
                }}
                style={{ marginLeft: '0.5rem' }}
                type="button"
              >
                сбросить клиента
              </button>
            </>
          ) : (
            <> · кликните клиента в списке</>
          )}
        </p>

        <div aria-label="Фильтры аудитории" className="ms-filter-window">
          <div className="ms-filter-window-head">
            <strong>Фильтры</strong>
            <div className="ms-filter-head-actions">
              <label className="ms-filter-presets">
                <span className="ms-sr-only">Сохранённые фильтры</span>
                <select
                  aria-label="Сохранённые фильтры"
                  className="ms-select"
                  disabled={segmentsLoading}
                  onChange={e => {
                    const id = e.target.value
                    if (!id) {
                      setActiveSegmentId('')
                      setSegmentName('')
                      return
                    }
                    const seg = segments.find(s => s.id === id)
                    if (seg) {
                      applySegment(seg)
                    }
                  }}
                  value={activeSegmentId}
                >
                  <option value="">
                    {segmentsLoading
                      ? 'Загрузка…'
                      : segments.length
                        ? 'Сохранённые фильтры…'
                        : 'Нет сохранённых'}
                  </option>
                  {segments.map(seg => (
                    <option key={seg.id} value={seg.id}>
                      {seg.name}
                      {seg.matched_total != null ? ` · ${seg.matched_total}` : ''}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="ms-btn"
                onClick={() => setSaveFilterOpen(v => !v)}
                title="Сохранить текущие фильтры"
                type="button"
              >
                Сохранить фильтр
              </button>
              <button
                className="ms-btn"
                onClick={() => {
                  setSalesFilter('all')
                  setChannelKind('')
                  setChannel('telegram')
                  setVipOnly(false)
                  setRequirePhone(false)
                  setRequireTelegram(false)
                  setBirthdaySoon(false)
                  setDaysBeforeEvent(0)
                  setEventDateFrom(null)
                  setEventDateTo(null)
                  setGroup('')
                  setGroupSource('any')
                  setAudienceQ('')
                  setActiveSegmentId('')
                  setSegmentName('')
                  setSegmentStatus('')
                  setFilterDrawer(null)
                  setSaveFilterOpen(false)
                }}
                title="Сбросить все фильтры аудитории"
                type="button"
              >
                Сбросить фильтры
              </button>
            </div>
          </div>

          {saveFilterOpen ? (
            <div className="ms-save-filter-bar">
              <input
                autoFocus
                className="ms-input"
                onChange={e => setSegmentName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && segmentName.trim()) {
                    e.preventDefault()
                    void saveCurrentSegment().then(ok => {
                      if (ok) {
                        setSaveFilterOpen(false)
                      }
                    })
                  }
                  if (e.key === 'Escape') {
                    setSaveFilterOpen(false)
                  }
                }}
                placeholder="Имя фильтра, напр. «Не состоялся · Прямые»"
                value={segmentName}
              />
              <button
                className="ms-btn"
                disabled={segmentSaving || !segmentName.trim()}
                onClick={() => {
                  void saveCurrentSegment().then(ok => {
                    if (ok) {
                      setSaveFilterOpen(false)
                    }
                  })
                }}
                type="button"
              >
                {segmentSaving
                  ? 'Сохраняю…'
                  : activeSegmentId
                    ? 'Обновить'
                    : 'Сохранить'}
              </button>
              {activeSegmentId ? (
                <button
                  className="ms-link-btn"
                  onClick={() => {
                    const active = segments.find(s => s.id === activeSegmentId)
                    if (active) {
                      void removeSegment(active)
                    }
                  }}
                  type="button"
                >
                  Удалить
                </button>
              ) : null}
              {segmentStatus ? <span className="ms-muted">{segmentStatus}</span> : null}
            </div>
          ) : segmentStatus ? (
            <p className="ms-muted ms-filter-status">{segmentStatus}</p>
          ) : null}

          <div className="ms-filter-workspace">
            <div className="ms-filter-rail">
              <div className="ms-filter-block">
                <span className="ms-filter-label">Канал продаж</span>
                <FilterTabs
                  counts={counts}
                  disabled={salesFilterTabsDisabled({ loading, hasCounts: Boolean(counts) })}
                  onChange={setSalesFilter}
                  salesFilter={salesFilter}
                />
              </div>

              <div className="ms-filter-block">
                <span className="ms-filter-label">Дополнительно</span>
                <div className="ms-chips">
                  <button
                    className={`ms-chip${vipOnly ? ' is-active' : ''}`}
                    onClick={() => setVipOnly(v => !v)}
                    type="button"
                  >
                    VIP
                  </button>
                  <button
                    className={`ms-chip${loyaltyOnly ? ' is-active' : ''}`}
                    onClick={() => setLoyaltyOnly(v => !v)}
                    title="Только клиенты с начисленными баллами"
                    type="button"
                  >
                    Баллы
                  </button>
                  <button
                    className={`ms-chip${requirePhone ? ' is-active' : ''}`}
                    onClick={() => setRequirePhone(v => !v)}
                    type="button"
                  >
                    Телефон
                  </button>
                  <button
                    className={`ms-chip${requireTelegram ? ' is-active is-tg-filter' : ''}`}
                    onClick={() => setRequireTelegram(v => !v)}
                    title="Только клиенты с проверенным активным Telegram (колонка «TG активен» = ✓)"
                    type="button"
                  >
                    Telegram
                  </button>
                  <button
                    className={`ms-chip${birthdaySoon ? ' is-active' : ''}`}
                    onClick={() => setBirthdaySoon(v => !v)}
                    type="button"
                  >
                    ДР
                  </button>
                </div>
              </div>

              <div className="ms-filter-block">
                <span className="ms-filter-label">Ещё</span>
                <div className="ms-filter-rail-actions">
                  <button
                    className={`ms-filter-slide-btn${
                      filterDrawer === 'calendar' || eventDateFrom || eventDateTo || daysBeforeEvent > 0
                        ? ' is-active'
                        : ''
                    }`}
                    onClick={() =>
                      setFilterDrawer(d => (d === 'calendar' ? null : 'calendar'))
                    }
                    type="button"
                  >
                    <span>Даты события</span>
                    <span className="ms-muted">
                      {formatRuRange(eventDateFrom, eventDateTo) ||
                        (daysBeforeEvent > 0 ? `за ${daysBeforeEvent} дн.` : 'календарь')}
                    </span>
                  </button>
                  <button
                    className={`ms-filter-slide-btn${
                      filterDrawer === 'groups-ms' || (group && groupSource === 'ms')
                        ? ' is-active'
                        : ''
                    }`}
                    onClick={() =>
                      setFilterDrawer(d => (d === 'groups-ms' ? null : 'groups-ms'))
                    }
                    type="button"
                  >
                    <span>Группы МойСклад</span>
                    <span className="ms-muted">
                      {group && groupSource === 'ms'
                        ? group
                        : `${groupOptionsMs.length || 0}`}
                    </span>
                  </button>
                  <button
                    className={`ms-filter-slide-btn${
                      filterDrawer === 'groups-ai' || (group && groupSource === 'ai')
                        ? ' is-active'
                        : ''
                    }`}
                    onClick={() =>
                      setFilterDrawer(d => (d === 'groups-ai' ? null : 'groups-ai'))
                    }
                    type="button"
                  >
                    <span>Группы ИИ</span>
                    <span className="ms-muted">
                      {group && groupSource === 'ai'
                        ? group
                        : `${groupOptionsAi.length || 0}`}
                    </span>
                  </button>
                </div>
              </div>
            </div>

            {filterDrawer ? (
              <div
                aria-label={
                  filterDrawer === 'calendar'
                    ? 'Календарь событий'
                    : filterDrawer === 'groups-ms'
                      ? 'Группы МойСклад'
                      : 'Группы ИИ'
                }
                className="ms-filter-slide"
                role="dialog"
              >
                <div className="ms-filter-slide-head">
                  <strong>
                    {filterDrawer === 'calendar'
                      ? 'Даты события / заказа'
                      : filterDrawer === 'groups-ms'
                        ? 'Группы: МойСклад'
                        : 'Группы: ИИ'}
                  </strong>
                  <button
                    aria-label="Закрыть"
                    className="ms-link-btn"
                    onClick={() => setFilterDrawer(null)}
                    type="button"
                  >
                    Закрыть
                  </button>
                </div>
                <div className="ms-filter-slide-body">
                  {filterDrawer === 'calendar' ? (
                    <EventCalendarPicker
                      dateFrom={eventDateFrom}
                      dateTo={eventDateTo}
                      leadDays={daysBeforeEvent}
                      onLeadDaysChange={n => {
                        setDaysBeforeEvent(n)
                        if (n > 0) {
                          setBirthdaySoon(false)
                        }
                      }}
                      onRangeChange={(from, to) => {
                        setEventDateFrom(from)
                        setEventDateTo(to)
                        if (from || to) {
                          setBirthdaySoon(false)
                        }
                      }}
                    />
                  ) : null}
                  {filterDrawer === 'groups-ms' ? (
                    <GroupCloudSection
                      activeGroup={group}
                      activeSource={groupSource}
                      emptyHint="Нет тегов МойСклад в текущей выборке"
                      items={groupOptionsMs}
                      limit={120}
                      onToggle={(name, source) => {
                        if (group === name && groupSource === source) {
                          setGroup('')
                          setGroupSource('any')
                        } else {
                          setGroup(name)
                          setGroupSource(source)
                        }
                      }}
                      sourceKey="ms"
                      title="Группы: МойСклад"
                    />
                  ) : null}
                  {filterDrawer === 'groups-ai' ? (
                    <GroupCloudSection
                      activeGroup={group}
                      activeSource={groupSource}
                      emptyHint="ИИ-группы появятся после эвристик/AI fill"
                      items={groupOptionsAi}
                      limit={100}
                      onToggle={(name, source) => {
                        if (group === name && groupSource === source) {
                          setGroup('')
                          setGroupSource('any')
                        } else {
                          setGroup(name)
                          setGroupSource(source)
                        }
                      }}
                      sourceKey="ai"
                      title="Группы: ИИ"
                    />
                  ) : null}
                </div>
              </div>
            ) : null}

            <aside
              aria-label="Аудитория"
              className={`ms-audience-col${requireTelegram ? ' is-tg-filter' : ''}`}
            >
              <div className="ms-audience-pick ms-audience-pick-side">
                <div className="ms-audience-pick-head">
                  <p className="ms-muted">
                    Клиенты · <strong>{audience}</strong>
                    {loading
                      ? ' · обновляем…'
                      : audienceStaleHint
                        ? ' · каталог пересобирается…'
                        : ''}
                    {selectedClientId
                      ? ` · ${selectedClientName || selectedClientId}`
                      : ' · кликните клиента'}
                  </p>
                </div>
                <div className="ms-search">
                  <input
                    onChange={e => setAudienceQ(e.target.value)}
                    placeholder="Поиск: имя / телефон / @tg…"
                    type="search"
                    value={audienceQ}
                  />
                </div>
                {audiencePreview.length ? (
                  <div
                    className="ms-audience-list"
                    onScroll={event => {
                      const el = event.currentTarget

                      if (el.scrollHeight - el.scrollTop - el.clientHeight > 120) {
                        return
                      }

                      if (!audienceHasMoreRef.current || audienceLoadMoreRef.current) {
                        return
                      }

                      if (audiencePreviewRef.current.length >= AUDIENCE_AUTO_SCROLL_CAP) {
                        return
                      }

                      void loadAudience({ append: true })
                    }}
                  >
                    <div className="ms-chips">
                      {audiencePreview.map(row => {
                        const active = selectedClientId === row.id
                        const nick = (row.tg_nick || '').replace(/^@/, '')
                        const tgOk = row.tg_active === true
                        const tgBad = row.tg_active === false

                        return (
                          <button
                            className={`ms-chip${active ? ' is-active' : ''}${
                              tgOk ? ' is-tg-verified' : tgBad ? ' is-tg-dead' : ''
                            }`}
                            key={row.id || row.name}
                            onClick={() => selectAudienceClient(row)}
                            title={
                              tgOk
                                ? `@${nick || row.tg_active_nick || ''} · есть Telegram`
                                : tgBad
                                  ? `${nick ? `@${nick} · ` : ''}не находится по номеру (возможно, скрыт приватностью) — не значит, что Telegram нет`
                                  : nick
                                    ? `@${nick} · ещё не проверен`
                                    : row.phone || row.id
                            }
                            type="button"
                          >
                            {row.name || row.phone || row.id}
                            {nick ? <span>@{nick}</span> : null}
                            {tgOk ? <span className="ms-chip-tg-dot">✓</span> : null}
                            {row.order_count != null ? <span>{row.order_count}</span> : null}
                          </button>
                        )
                      })}
                    </div>
                    {audienceLoadingMore ? (
                      <p className="ms-muted ms-load-more">
                        Подгружаем… {audiencePreview.length}
                        {audience ? ` / ${audience}` : ''}
                      </p>
                    ) : null}
                    {audienceHasMore ? (
                      <button
                        className="ms-btn"
                        disabled={audienceLoadingMore}
                        onClick={() => void loadAudience({ append: true })}
                        type="button"
                      >
                        {audiencePreview.length >= AUDIENCE_AUTO_SCROLL_CAP
                          ? `Ещё (${audiencePreview.length} / ${audience})`
                          : 'Ещё клиенты'}
                      </button>
                    ) : audiencePreview.length ? (
                      <p className="ms-muted ms-load-more">
                        Показано {audiencePreview.length} из {audience}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="ms-muted">
                    {loading
                      ? 'Загрузка аудитории…'
                      : audienceStaleHint
                        ? 'Каталог пересобирается на сервере — список появится сам, не переключайте фильтры…'
                        : eventDateFrom || eventDateTo
                          ? daysBeforeEvent > 0
                            ? `Нет клиентов: событие в выбранных датах, связаться за ${daysBeforeEvent} дн. до.`
                            : 'Нет клиентов с событием / заказом в выбранных датах.'
                          : daysBeforeEvent > 0
                            ? `Нет клиентов в окне ${daysBeforeEvent} дн. до события.`
                            : 'Нет клиентов под текущие фильтры / поиск.'}
                  </p>
                )}
              </div>
            </aside>
          </div>
        </div>

      </section>

      <h2 className="ms-section-title">2. Текст и отправка</h2>

      <details className="ms-hidden-panel">
        <summary>Массовая рассылка (скрыто — открыть при необходимости)</summary>
      <section aria-label="Массовая рассылка" className="ms-mass-panel">
        <div className="ms-mass-panel-head">
          <strong>Массовая рассылка</strong>
          <span className="ms-muted">
            одно сообщение всей отфильтрованной аудитории · отправка идёт на
            сервере и не блокирует интерфейс
          </span>
          <span className="ms-muted">
            {tgUser?.authorized
              ? 'Канал: личный Telegram (по умолчанию), Business bot — запасной'
              : 'Канал: Business bot — подключите личный Telegram ниже, чтобы слать от своего имени'}
          </span>
        </div>
        <div className="ms-compose-actions">
          <button
            className="ms-btn"
            disabled={massPicking || isMassJobActive(massJob?.status)}
            onClick={() => void massSelectAudience()}
            title="Забрать все id из текущего фильтра аудитории (шаг 1)"
            type="button"
          >
            {massPicking
              ? 'Собираем аудиторию…'
              : `Взять аудиторию из фильтра${audience ? ` (${audience})` : ''}`}
          </button>
          {massIds.length ? (
            <span className="ms-mass-count">
              Получателей: <strong>{massIds.length}</strong>
            </span>
          ) : null}
          {massIds.length ? (
            <button
              className="ms-link-btn"
              onClick={() => setMassIds([])}
              type="button"
            >
              сбросить
            </button>
          ) : null}
        </div>
        <label>
          Текст для всех получателей
          <textarea
            onChange={e => setMassText(e.target.value)}
            placeholder="Одно сообщение для всей выборки… (возьмите текст из черновика или напишите здесь)"
            rows={5}
            value={massText}
          />
        </label>
        <div className="ms-compose-actions">
          {offer.trim() ? (
            <button
              className="ms-btn"
              onClick={() => setMassText(offer)}
              title="Скопировать текст из блока 1:1 ниже"
              type="button"
            >
              Взять текст из черновика
            </button>
          ) : null}
          <button
            className="ms-btn ms-btn-primary"
            disabled={
              !massText.trim() ||
              !massIds.length ||
              massStarting ||
              isMassJobActive(massJob?.status)
            }
            onClick={() => {
              if (needsMassSendConfirm(massIds.length)) {
                setMassConfirmOpen(true)
              } else {
                void massStart()
              }
            }}
            type="button"
          >
            {massStarting
              ? 'Запускаем…'
              : `Отправить всем${massIds.length ? ` (${massIds.length})` : ''}`}
          </button>
          {massJob ? (
            <button
              className="ms-btn"
              onClick={() => {
                setMassModalOpen(true)
                void pollMassJob()
              }}
              type="button"
            >
              Статус рассылки
              {isMassJobActive(massJob.status)
                ? ` · ${massJob.attempted || 0}/${massJob.total || 0}`
                : ''}
            </button>
          ) : null}
        </div>
        {isMassJobActive(massJob?.status) ? (
          <p className="ms-muted">
            Идёт рассылка: {massJob?.attempted || 0}/{massJob?.total || 0} · ✓{' '}
            {massJob?.sent_ok || 0}
            {massJob?.sent_failed ? ` · ✕ ${massJob.sent_failed}` : ''} — окно
            статуса можно закрывать, отправка продолжится.
          </p>
        ) : null}
      </section>
      </details>

      <div className="ms-compose-split">
        <form className="ms-campaign-form" onSubmit={event => void createDraft(event)}>
          <details className="ms-tg-account">
            <summary className="ms-tg-account-head">
              <strong>Настройки аккаунта (скрыто)</strong>
              <span className="ms-muted">
                {tgUser?.authorized
                  ? `✓ ${
                      tgUser.user?.username
                        ? `@${tgUser.user.username}`
                        : tgUser.user?.name || tgUser.phone || 'аккаунт'
                    }`
                  : 'нужен для отправки и сбора ответов'}
              </span>
            </summary>
            <div className="ms-tg-account-body">
              <button
                className="ms-link-btn"
                disabled={tgBusy}
                onClick={() => void refreshTgUser()}
                type="button"
              >
                Проверить
              </button>
              <div className="ms-muted ms-tg-account-status">
              {tgUser && tgUser.available === false ? (
                <>
                  ⚠ Нет MTProto-движка (telethon) — вход невозможен.{' '}
                  <button
                    className="ms-link-btn"
                    disabled={tgBusy}
                    onClick={() => void tgInstallRuntime()}
                    type="button"
                  >
                    {tgBusy ? 'Ставим…' : 'Установить telethon'}
                  </button>{' '}
                  ·{' '}
                </>
              ) : null}
              {tgUser?.authorized ? (
                <>
                  ✓ Подключён{' '}
                  <strong>
                    {tgUser.user?.username
                      ? `@${tgUser.user.username}`
                      : tgUser.user?.name || tgUser.phone || 'аккаунт'}
                  </strong>{' '}
                  · контактов: {tgUser.contacts_cached ?? 0} · рассылка уходит
                  от вашего имени
                </>
              ) : tgUser?.session_saved ? (
                'Сессия сохранена, но не авторизована — войдите заново'
              ) : (
                'Не подключён — Bot API не видит ваш список контактов и не пишет первым'
              )}
              {tgUser?.detail ? ` · ${tgUser.detail}` : ''}
              </div>
              <div className="ms-compose-actions">
              <button
                className="ms-btn"
                onClick={() => {
                  setTgOpen(open => {
                    const next = !open
                    if (next && !tgUser?.authorized) {
                      setTgStep('phone')
                    }
                    return next
                  })
                }}
                type="button"
              >
                {tgOpen ? 'Скрыть' : tgUser?.authorized ? 'Настройки входа' : 'Подключить аккаунт'}
              </button>
              {tgUser?.authorized ? (
                <>
                  <button
                    className="ms-link-btn"
                    disabled={tgBusy}
                    onClick={() => void tgSyncContacts()}
                    type="button"
                  >
                    {tgBusy ? 'Синхронизируем…' : 'Синхронизировать контакты'}
                  </button>
                  <button
                    className="ms-link-btn"
                    disabled={tgBusy}
                    onClick={() => void tgLogout()}
                    type="button"
                  >
                    Выйти
                  </button>
                </>
              ) : null}
              </div>
            {tgOpen ? (
              <div className="ms-add-contact">
                {tgStep === 'phone' ? (
                  <>
                    <label>
                      Телефон аккаунта
                      <input
                        onChange={e => setTgPhone(e.target.value)}
                        placeholder="+79991234567"
                        value={tgPhone}
                      />
                    </label>
                    <div className="ms-compose-actions">
                      <button
                        className="ms-btn ms-btn-primary"
                        disabled={tgBusy}
                        onClick={() => void tgLogin()}
                        type="button"
                      >
                        {tgBusy ? 'Отправляем код…' : 'Получить код'}
                      </button>
                    </div>
                    <p className="ms-muted">
                      Код обычно приходит в приложение Telegram (не SMS). Если
                      Selectel не достучится до Telegram — нужен
                      TELEGRAM_USER_GATEWAY_URL (Railway egress) или StringSession
                      ниже.
                      {tgUser?.gateway_configured === false ? (
                        <>
                          {' '}
                          <strong>Сейчас gateway не настроен на сервере</strong> —
                          запрос кода идёт с RU IP и часто не доходит.
                        </>
                      ) : null}
                    </p>
                    <label>
                      StringSession (обход блокировки)
                      <input
                        onChange={e => setTgSession(e.target.value)}
                        placeholder="1BVtsOHwBu…"
                        type="password"
                        value={tgSession}
                      />
                    </label>
                    <div className="ms-compose-actions">
                      <button
                        className="ms-btn"
                        disabled={tgBusy || !tgSession.trim()}
                        onClick={() => void tgSaveSession()}
                        type="button"
                      >
                        Сохранить сессию
                      </button>
                    </div>
                  </>
                ) : null}
                {tgStep === 'code' ? (
                  <>
                    <label>
                      Код из Telegram
                      <input
                        onChange={e => setTgCode(e.target.value)}
                        placeholder="12345"
                        value={tgCode}
                      />
                    </label>
                    <div className="ms-compose-actions">
                      <button
                        className="ms-btn ms-btn-primary"
                        disabled={tgBusy}
                        onClick={() => void tgSubmitCode()}
                        type="button"
                      >
                        {tgBusy ? 'Проверяем…' : 'Войти'}
                      </button>
                      <button
                        className="ms-link-btn"
                        disabled={tgBusy}
                        onClick={() => void tgLogin({ forceSms: true })}
                        type="button"
                      >
                        Нет кода? Отправить SMS
                      </button>
                    </div>
                  </>
                ) : null}
                {tgStep === 'password' ? (
                  <>
                    <label>
                      Облачный пароль (2FA)
                      <input
                        onChange={e => setTgPassword(e.target.value)}
                        type="password"
                        value={tgPassword}
                      />
                    </label>
                    <button
                      className="ms-btn ms-btn-primary"
                      disabled={tgBusy}
                      onClick={() => void tgSubmitPassword()}
                      type="button"
                    >
                      {tgBusy ? 'Проверяем…' : 'Подтвердить'}
                    </button>
                  </>
                ) : null}
                <p className="ms-muted">
                  Как в обычном Telegram: телефон → код → облачный пароль (если
                  включён). Сессия хранится на сервере; код и пароль никуда не
                  сохраняются. После входа контакты появятся в «Кому
                  отправить», рассылка уйдёт с вашего аккаунта.
                </p>
              </div>
            ) : null}
            </div>
          </details>

          {/* Telegram Business bot block hidden: личный MTProto-аккаунт
              покрывает login + send. Bot/connection id живут в env / Офис. */}

          <div className="ms-contact-picker">
            <label>
              Кому отправить
              <select
                onChange={e => selectContactFromPicker(e.target.value)}
                value={contactPickerId || selectedClientId || ''}
              >
                <option value="">— выберите контакт —</option>
                {outreachContacts.some(c => c.source === 'custom') ? (
                  <optgroup label="Добавленные">
                    {outreachContacts
                      .filter(c => c.source === 'custom')
                      .map((c, i) => (
                        <option key={`custom-${c.id || i}`} value={c.id}>
                          {c.label || c.name || c.tg_nick || c.id}
                        </option>
                      ))}
                  </optgroup>
                ) : null}
                {outreachContacts.some(c => c.source !== 'custom') ? (
                  <optgroup label="Контакты">
                    {outreachContacts
                      .filter(c => c.source !== 'custom')
                      .map((c, i) => (
                        <option key={`all-${c.id || i}`} value={c.id}>
                          {c.label || c.name || c.tg_nick || c.id}
                          {c.source === 'telegram' ? ' · мои контакты' : ''}
                        </option>
                      ))}
                  </optgroup>
                ) : null}
              </select>
            </label>
            <label>
              Аудитория из фильтра{audience ? ` · ${audience}` : ''}
              <select
                onChange={e => {
                  const row = audiencePreview.find(
                    r => (r.id || '') === e.target.value
                  )
                  if (row) {
                    selectAudienceClient(row)
                  }
                }}
                value={
                  selectedClientId &&
                  audiencePreview.some(r => r.id === selectedClientId)
                    ? selectedClientId
                    : ''
                }
              >
                <option value="">
                  {audiencePreview.length
                    ? '— выберите из отфильтрованных —'
                    : audience > 0
                      ? 'Список аудитории ещё грузится…'
                      : 'Фильтр пуст — настройте аудиторию выше'}
                </option>
                {audiencePreview
                  .filter(r => r.id)
                  .map(row => {
                    const nick = (row.tg_nick || '').replace(/^@/, '')
                    return (
                      <option key={`aud-${row.id}`} value={row.id}>
                        {row.name || row.phone || row.id}
                        {nick ? ` · @${nick}` : ''}
                        {row.order_count != null ? ` · ${row.order_count}` : ''}
                      </option>
                    )
                  })}
              </select>
              {audience > audiencePreview.length ? (
                <span className="ms-muted ms-picker-hint">
                  Загружено {audiencePreview.length} из {audience} — остальные
                  подгружаются прокруткой списка аудитории выше.
                </span>
              ) : null}
            </label>
            {contactsError ? (
              <p className="ms-error">
                Список контактов не загрузился: {contactsError} — нажмите
                «Обновить список».
              </p>
            ) : (
              <p className="ms-muted">
                {contactsLoading
                  ? 'Загружаем список контактов…'
                  : outreachContacts.length
                    ? `В списке ${outreachContacts.length}` +
                      (outreachContacts.some(c => c.source === 'custom')
                        ? ` · добавленных ${outreachContacts.filter(c => c.source === 'custom').length}`
                        : '')
                    : 'Список пуст — подключите личный Telegram и синхронизируйте контакты, затем «Обновить список»'}
              </p>
            )}
            <div className="ms-compose-actions">
              <button
                className="ms-btn"
                onClick={() => setAddContactOpen(open => !open)}
                type="button"
              >
                {addContactOpen ? 'Скрыть форму' : 'Добавить свой контакт'}
              </button>
              <button
                className="ms-link-btn"
                onClick={() => void loadOutreachContacts()}
                type="button"
              >
                Обновить список
              </button>
            </div>
            {addContactOpen ? (
              <div className="ms-add-contact">
                <label>
                  @ник / t.me / chat id
                  <input
                    onChange={e => setAddContactQuery(e.target.value)}
                    placeholder="@papa2139 или https://t.me/papa2139 или 415321451"
                    value={addContactQuery}
                  />
                </label>
                <div className="ms-compose-actions">
                  <button
                    className="ms-btn"
                    disabled={addContactResolving || addContactSaving}
                    onClick={() => void resolveOutreachContactQuery()}
                    type="button"
                  >
                    {addContactResolving ? 'Расшифровываем…' : 'Расшифровать (API)'}
                  </button>
                </div>
                <label>
                  Имя
                  <input
                    onChange={e => setAddContactName(e.target.value)}
                    placeholder="Ася"
                    value={addContactName}
                  />
                </label>
                {/* @ник / chat id после Bot API resolve — скрыты: личные
                    контакты Telethon покрывают выбор; cold resolve живёт в state. */}
                <button
                  className="ms-btn ms-btn-primary"
                  disabled={addContactSaving || addContactResolving}
                  onClick={() => void addCustomOutreachContact()}
                  type="button"
                >
                  {addContactSaving ? 'Добавляем…' : 'Добавить и выбрать'}
                </button>
              </div>
            ) : null}
          </div>

          <details className="ms-seller-settings">
            <summary className="ms-muted">Подпись продавца</summary>
            <label>
              Кто пишет (имя / магазин)
              <input
                disabled={!sellerLoaded}
                onChange={e => {
                  const v = e.target.value
                  setSellerName(v)
                  persistSellerSettings(v, sellerFacts)
                }}
                placeholder="Анна · Iris"
                value={sellerName}
              />
            </label>
            <label>
              Что можно упомянуть
              <textarea
                disabled={!sellerLoaded}
                onChange={e => {
                  const v = e.target.value
                  setSellerFacts(v)
                  persistSellerSettings(sellerName, v)
                }}
                placeholder="Тон, адрес, специализация…"
                rows={2}
                value={sellerFacts}
              />
            </label>
          </details>
          <label>
            Текст сообщения
            <textarea
              key={`offer-${offerTick}`}
              onChange={e => {
                const v = e.target.value
                setOffer(v)
                offerRef.current = v
              }}
              placeholder={
                selectedClientId
                  ? 'Нажмите «Сгенерировать AI» или введите текст…'
                  : 'Сначала выберите клиента в списке аудитории'
              }
              rows={8}
              value={offer}
            />
          </label>
          {selectedClientId ? (
            <div className="ms-chat-panel">
              <div className="ms-chat-head">
                <span className="ms-ai-label">Чат доработки текста</span>
                <select
                  className="ms-select"
                  onChange={e => setChatModel(e.target.value as 'deepseek' | 'grok' | 'gpt')}
                  title="Какой моделью улучшать текст"
                  value={chatModel}
                >
                  <option value="deepseek">DeepSeek</option>
                  <option value="grok">Grok</option>
                  <option value="gpt">GPT</option>
                </select>
              </div>
              {chatTurns.length ? (
                <div className="ms-chat-log">
                  {chatTurns.map((t, idx) => (
                    <div className={`ms-chat-msg is-${t.role}`} key={idx}>
                      {t.content}
                    </div>
                  ))}
                  {chatBusy ? <div className="ms-chat-msg is-assistant">…</div> : null}
                </div>
              ) : null}
              <div className="ms-chat-input">
                <input
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      void sendChatTurn()
                    }
                  }}
                  placeholder="Например: короче и теплее, добавь про баллы…"
                  type="text"
                  value={chatInput}
                />
                <button
                  className="ms-btn"
                  disabled={chatBusy || !chatInput.trim()}
                  onClick={() => void sendChatTurn()}
                  type="button"
                >
                  {chatBusy ? '…' : '➤'}
                </button>
              </div>
            </div>
          ) : null}
          {skillPromptText ? (
            <div className="ms-skill-prompt">
              <span>Сохранить отправленное сообщение в навык генерации?</span>
              <button
                className="ms-btn ms-btn-primary"
                onClick={() => void saveSkill(skillPromptText)}
                type="button"
              >
                Да, в навык
              </button>
              <button
                className="ms-btn"
                onClick={() => setSkillPromptText('')}
                type="button"
              >
                Нет
              </button>
            </div>
          ) : null}
          {skillNote ? <p className="ms-muted">{skillNote}</p> : null}
          {actionStatus ? <p className="ms-action-status">{actionStatus}</p> : null}
          <div className="ms-compose-actions">
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={
                  generating || rewriting || checkingSanity || suggestingBouquet || paraphrasing
                }
                onClick={() => void regenerateAi()}
                type="button"
              >
                {generating ? 'Генерация…' : 'Сгенерировать AI'}
              </button>
            ) : null}
            <details className="ms-more-actions">
              <summary>Другие действия</summary>
              <div className="ms-compose-actions">
            {selectedClientId ? (
              <button
                className="ms-btn"
                disabled={
                  generating || rewriting || checkingSanity || suggestingBouquet || paraphrasing
                }
                onClick={() => void suggestHistoricalBouquet()}
                title="Назвать конкретный букет из прошлых заказов клиента"
                type="button"
              >
                {suggestingBouquet ? 'Подбираем букет…' : 'Букет из истории'}
              </button>
            ) : null}
            <button
              className="ms-btn"
              disabled={
                rewriting ||
                generating ||
                checkingSanity ||
                suggestingBouquet ||
                paraphrasing ||
                !offer.trim()
              }
              onClick={() => void humanizeDraft()}
              type="button"
            >
              {rewriting ? 'Переписываем…' : 'Продающе и по-человечески'}
            </button>
            <button
              className="ms-btn"
              disabled={
                paraphrasing ||
                generating ||
                rewriting ||
                checkingSanity ||
                suggestingBouquet ||
                !offer.trim()
              }
              onClick={() => void paraphraseDraft()}
              title="Полная парафраза: другой текст, не generate и не sales-rewrite"
              type="button"
            >
              {paraphrasing ? 'Парафраза…' : 'Полная парафраза'}
            </button>
            <button
              className="ms-btn"
              disabled={
                checkingSanity ||
                generating ||
                rewriting ||
                suggestingBouquet ||
                paraphrasing ||
                !offer.trim()
              }
              onClick={() => void runSanityCheck()}
              type="button"
            >
              {checkingSanity ? 'Проверяем…' : 'Проверить смысл'}
            </button>
              </div>
            </details>
            {selectedClientId && offer.trim() ? (
              <button
                className="ms-btn ms-btn-primary"
                disabled={!canSendOne}
                onClick={() => void markSentToConversation()}
                type="button"
              >
                {checkingSanity ? 'Отправляю…' : 'Отправить в Telegram'}
              </button>
            ) : null}
            <button
              className="ms-btn"
              disabled={
                saving ||
                loading ||
                generating ||
                rewriting ||
                checkingSanity ||
                suggestingBouquet ||
                paraphrasing ||
                !selectedClientId ||
                !offer.trim()
              }
              type="submit"
            >
              Сохранить 1:1 черновик
            </button>
          </div>
          {genSource ? (
            <p className="ms-muted">
              Источник текста: {genSource === 'redis-cache' || genSource === 'redis+file' || genSource === 'file'
                ? `кэш (${genSource})`
                : genSource}
            </p>
          ) : null}
        </form>
        <FactsPanel facts={facts} notes={groundingNotes} sanity={sanity} />
      </div>
      <h2 className="ms-section-title">3. История отправок</h2>
      <section aria-label="История отправок" className="ms-mass-panel ms-history-panel">
        <div className="ms-card-head">
          <p className="ms-muted">
            Последние рассылки и одинаковые исходящие из Фактов / TG conversation
            (импорт Telegram и прошлые отправки): кому, что, статус.
          </p>
          <button
            className="ms-link-btn"
            disabled={historyLoading}
            onClick={() => void loadSendHistory()}
            type="button"
          >
            {historyLoading ? 'Обновляем…' : 'Обновить'}
          </button>
        </div>
        {historyError ? <p className="ms-error">{historyError}</p> : null}
        {!historyJobs.length ? (
          <p className="ms-muted">
            {historyLoading
              ? 'Загружаем…'
              : 'Пока нет рассылок и групповых исходящих (≥2 клиента с одним текстом).'}
          </p>
        ) : (
          historyJobs.map(job => (
            <div className="ms-history-job" key={job.id}>
              <button
                className="ms-history-job-head"
                onClick={() => {
                  if (job.id) {void toggleHistoryJob(job.id)}
                }}
                type="button"
              >
                <span className="ms-muted">
                  {(job.created_at || '').slice(0, 16).replace('T', ' ')}
                </span>
                <span className="ms-history-job-msg">{job.message_preview || '—'}</span>
                <span className="ms-muted">
                  {massJobStatusLabel(job.status)}
                  {job.history_kind === 'conversation_blast' ? ' · из переписки' : ''} ·{' '}
                  {job.total ?? 0} получ. · ✓{job.sent_ok ?? 0} · ✕{job.sent_failed ?? 0}
                </span>
              </button>
              {historyOpenJobId === job.id ? (
                <div className="ms-mass-log">
                  {historyRowsLoading ? (
                    <p className="ms-muted">Загружаем получателей…</p>
                  ) : (
                    historyRows.map((row, idx) => (
                      <div
                        className={`ms-mass-log-row is-${row.status || 'pending'}`}
                        key={`${row.client_id || idx}`}
                      >
                        <span>{massRecipientDisplay(row)}</span>
                        <span className="ms-muted">
                          {massRowStatusLabel(row.status)}
                          {row.detail || row.error
                            ? ` · ${row.detail || row.error}`
                            : ''}
                          {row.ts ? ` · ${String(row.ts).slice(11, 16)}` : ''}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          ))
        )}
        <details
          className="ms-history-feed"
          onToggle={e => {
            const open = (e.target as HTMLDetailsElement).open
            setSentFeedOpen(open)
            if (open) {void loadSentFeed()}
          }}
          open={sentFeedOpen}
        >
          <summary>
            Все отправленные сообщения{sentFeed.length ? ` · ${sentFeed.length}` : ''} —
            включая одиночные с карточки/диалога
          </summary>
          <div className="ms-mass-log">
            {!sentFeed.length ? (
              <p className="ms-muted">Исходящих пока нет.</p>
            ) : (
              sentFeed.map((m, idx) => (
                <div
                  className={`ms-mass-log-row is-${m.status === 'delivered' ? 'ok' : 'sending'}`}
                  key={`${m.client_id || ''}-${m.ts || idx}`}
                >
                  <span>
                    {m.client_name || (m.tg_nick ? `@${m.tg_nick}` : m.client_id || '—')}
                    {m.text ? ` — ${m.text.slice(0, 120)}${m.text.length > 120 ? '…' : ''}` : ''}
                  </span>
                  <span className="ms-muted">
                    {m.status === 'delivered' ? '✓ доставлено' : '✎ записано'}
                    {m.ts ? ` · ${String(m.ts).slice(0, 16).replace('T', ' ')}` : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        </details>
      </section>
      {error ? <MsErrorModal message={error} onClose={() => setError('')} /> : null}
      {massConfirmOpen ? (
        <MassConfirmModal
          count={massIds.length}
          onClose={() => setMassConfirmOpen(false)}
          onConfirm={() => void massStart()}
          starting={massStarting}
        />
      ) : null}
      {massModalOpen && massJob ? (
        <MassSendStatusModal
          cancelling={massCancelling}
          job={massJob}
          onCancel={() => void massCancel()}
          onClose={() => setMassModalOpen(false)}
          onCollectReplies={() => void collectReplies()}
          onOpenDialog={row => {
            if (row.client_id) {
              setDialogClient({ id: row.client_id, name: row.client_name })
            }
          }}
          onRowFilter={setMassRowFilter}
          repliesBusy={repliesBusy}
          rowFilter={massRowFilter}
          rows={massRows}
        />
      ) : null}
      {dialogClient ? (
        <ClientDialogModal
          clientId={dialogClient.id}
          clientName={dialogClient.name}
          onClose={() => setDialogClient(null)}
        />
      ) : null}
      {draftsOpen ? (
        <div
          aria-hidden={!draftsOpen}
          className="ms-drawer-backdrop"
          onClick={() => setDraftsOpen(false)}
        >
          <aside
            aria-label="Черновики рассылок"
            className="ms-drawer ms-drafts-drawer"
            onClick={e => e.stopPropagation()}
            role="dialog"
          >
            <div className="ms-drawer-head">
              <h2>Черновики</h2>
              <button
                aria-label="Закрыть черновики"
                className="ms-btn"
                onClick={() => setDraftsOpen(false)}
                type="button"
              >
                Закрыть
              </button>
            </div>
            {!campaigns.length ? (
              <p className="ms-muted">{loading ? 'Загрузка…' : 'Пока нет рассылок.'}</p>
            ) : (
              <ul className="ms-campaign-list">
                {campaigns.map(c => (
                  <li className="ms-campaign-card" key={c.id}>
                    <div className="ms-campaign-card-head">
                      <strong>{c.title}</strong>
                      <button
                        className="ms-btn"
                        onClick={() =>
                          void call(`/campaigns/${encodeURIComponent(c.id)}`, {
                            method: 'DELETE'
                          }).then(refresh)
                        }
                        type="button"
                      >
                        Удалить
                      </button>
                    </div>
                    <div className="ms-muted">
                      {c.channel} · {c.mode} · аудитория {c.audience_count || 0}
                      {c.client_name ? ` · ${c.client_name}` : ''} · {c.status || 'draft'}
                      {c.ai_source ? ` · AI ${c.ai_source}` : ''}
                      {c.personalize_pending ? ' · персонализация в очереди' : ''}
                    </div>
                    {c.offer ? <p className="ms-campaign-offer">{c.offer}</p> : null}
                    {c.recommendation ? (
                      <p className="ms-muted">Контекст: {c.recommendation}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </div>
      ) : null}
      {tgProgress ? (
        <TgProgressModal detail={tgProgress.detail} title={tgProgress.title} />
      ) : null}
    </div>
  )
}

type PlaygroundOutputKey =
  | 'history_profile'
  | 'occasion_intent'
  | 'recommendation'
  | 'fact_blocks'
  | 'system_prompt'
  | 'heuristic'
  | 'llm'
  | 'full'

const PLAYGROUND_DEBUG_TABS: Array<{ id: PlaygroundOutputKey; label: string }> = [
  { id: 'fact_blocks', label: 'Факты' },
  { id: 'heuristic', label: 'Heuristic' },
  { id: 'llm', label: 'LLM raw' },
  { id: 'system_prompt', label: 'System prompt' },
  { id: 'full', label: 'Все этапы' }
]

const PLAYGROUND_QUALITY_FIELDS: Array<{
  id: 'history_profile' | 'occasion_intent' | 'recommendation'
  label: string
  hint: string
}> = [
  {
    id: 'history_profile',
    label: 'Саммари',
    hint: 'История и профиль — есть ли конкретика по заказам?'
  },
  {
    id: 'occasion_intent',
    label: 'Повод / intent',
    hint: 'Повод, сезонность, окно касания — не выдумка?'
  },
  {
    id: 'recommendation',
    label: 'Рекомендация',
    hint: 'Что предложить продавцу — действие + якорь на фактах'
  }
]

interface GoldenClientSummary {
  id?: string
  name?: string
  order_count?: number
  avg_check?: number
  channels?: string[]
}

interface PlaygroundPanels {
  input_text?: string
  outputs?: Partial<Record<PlaygroundOutputKey, string>>
}

interface PlaygroundTrace {
  ok?: boolean
  client_id?: string
  client_name?: string
  source?: string
  data_thin?: boolean
  stages?: {
    active?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    }
    heuristic?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    }
    llm?: {
      history_profile?: string
      occasion_intent?: string
      recommendation?: string
      source?: string
    } | null
  }
  panels?: PlaygroundPanels
}

function playgroundStats(text: string): { chars: number; words: number; lines: number } {
  const trimmed = text.trim()
  if (!trimmed) {
    return { chars: 0, words: 0, lines: 0 }
  }

  return {
    chars: trimmed.length,
    words: trimmed.split(/\s+/).filter(Boolean).length,
    lines: trimmed.split(/\n/).length
  }
}

const $aiPlaygroundOpen = atom(false)

function openAiPlayground() {
  $aiPlaygroundOpen.set(true)
}

function closeAiPlayground() {
  $aiPlaygroundOpen.set(false)
}

function toggleAiPlayground() {
  $aiPlaygroundOpen.set(!$aiPlaygroundOpen.get())
}

/** Bottom-right FAB + slide-out — same pattern as desktop keybinds panel.
 *  Mounted via titleBar.center (always on); portals to body so chrome stays empty. */
function AiPlaygroundChrome() {
  const open = useValue($aiPlaygroundOpen)

  useEffect(() => {
    if (!open) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      const target = event.target as HTMLElement | null
      if (target?.closest?.('textarea, input, select, [contenteditable="true"]')) {
        return
      }

      closeAiPlayground()
      event.preventDefault()
      event.stopPropagation()
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [open])

  return createPortal(
    <div
      className={cn(
        'pointer-events-none fixed z-[200] flex flex-col items-end gap-2',
        'right-[calc(0.75rem+var(--corner-chrome-width,10.5rem)+0.5rem)] bottom-3',
        '[-webkit-app-region:no-drag]'
      )}
      data-slot="ms-ai-playground-panel"
    >
      {open ? (
        <div
          aria-label="AI тест · клиенты"
          className="pointer-events-auto flex w-[min(40rem,calc(100vw-5rem))] max-h-[min(78vh,42rem)] flex-col overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--ui-stroke-secondary)_80%,transparent)] bg-[var(--ui-chat-bubble-background,#1a1a1e)] shadow-lg"
          role="dialog"
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[color-mix(in_srgb,var(--ui-stroke-secondary)_60%,transparent)] px-3 py-2">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold">AI тест · клиенты</h2>
              <p className="ms-muted truncate text-[0.7rem]">
                Саммари / Повод / Рекомендация · golden dataset
              </p>
            </div>
            <Button
              aria-label="Закрыть"
              onClick={() => {
                haptic('tap')
                closeAiPlayground()
              }}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Codicon name="close" size="0.875rem" />
            </Button>
          </div>
          <div className="ms-playground-float-body min-h-0 flex-1 overflow-y-auto">
            <AiPlaygroundPage embedded />
          </div>
        </div>
      ) : null}

      <Tip label="AI тест · клиенты">
        <Button
          aria-expanded={open}
          aria-label="AI тест · клиенты"
          className={cn(
            'pointer-events-auto size-9 rounded-full border border-[color-mix(in_srgb,var(--ui-stroke-secondary)_80%,transparent)] bg-[var(--ui-chat-bubble-background,#1a1a1e)] shadow-lg',
            open && 'bg-[var(--chrome-action-hover,rgba(255,255,255,0.08))]'
          )}
          onClick={() => {
            haptic(open ? 'tap' : 'open')
            toggleAiPlayground()
          }}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Codicon name="beaker" />
        </Button>
      </Tip>
    </div>,
    document.body
  )
}

function AiPlaygroundPage({ embedded = false }: { embedded?: boolean } = {}) {
  const call = useMsRest()
  const [clients, setClients] = useState<GoldenClientSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [inputText, setInputText] = useState('')
  const [outputs, setOutputs] = useState<Partial<Record<PlaygroundOutputKey, string>>>({})
  const [historyText, setHistoryText] = useState('')
  const [occasionText, setOccasionText] = useState('')
  const [recoText, setRecoText] = useState('')
  const [aiSource, setAiSource] = useState('')
  const [debugKey, setDebugKey] = useState<PlaygroundOutputKey>('fact_blocks')
  const [meta, setMeta] = useState('')
  const [error, setError] = useState('')
  const [loadingList, setLoadingList] = useState(false)
  const [running, setRunning] = useState(false)
  const [inputOpen, setInputOpen] = useState(true)
  const [compareOpen, setCompareOpen] = useState(false)

  const qualityMap = {
    history_profile: { value: historyText, set: setHistoryText },
    occasion_intent: { value: occasionText, set: setOccasionText },
    recommendation: { value: recoText, set: setRecoText }
  } as const

  const applyTrace = useCallback((trace: PlaygroundTrace) => {
    const panels = trace.panels || {}
    if (typeof panels.input_text === 'string') {
      setInputText(panels.input_text)
    }
    const nextOutputs = panels.outputs || {}
    setOutputs(nextOutputs)
    setHistoryText(String(nextOutputs.history_profile || trace.stages?.active?.history_profile || ''))
    setOccasionText(String(nextOutputs.occasion_intent || trace.stages?.active?.occasion_intent || ''))
    setRecoText(String(nextOutputs.recommendation || trace.stages?.active?.recommendation || ''))
    const src = trace.stages?.active?.source || '—'
    setAiSource(src)
    setMeta(
      [
        trace.client_name || 'клиент',
        `id=${trace.client_id || '—'}`,
        `source=${trace.source || '—'}`,
        `ai=${src}`,
        trace.data_thin ? 'данных мало' : null
      ]
        .filter(Boolean)
        .join(' · ')
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoadingList(true)
    setError('')
    void call<{ clients?: GoldenClientSummary[]; count?: number; generated_at?: string }>(
      '/eval/golden-clients'
    )
      .then(data => {
        if (cancelled) {return}
        const rows = data.clients || []
        setClients(rows)
        if (rows[0]?.id) {
          setSelectedId(String(rows[0].id))
        }
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setLoadingList(false)}
      })

    return () => {
      cancelled = true
    }
  }, [call])

  useEffect(() => {
    if (!selectedId) {return}
    let cancelled = false
    setRunning(true)
    setError('')
    void call<PlaygroundTrace & { client?: unknown }>(
      `/eval/golden-clients/${encodeURIComponent(selectedId)}`
    )
      .then(data => {
        if (!cancelled) {applyTrace(data)}
      })
      .catch(err => {
        if (!cancelled) {setError(err instanceof Error ? err.message : String(err))}
      })
      .finally(() => {
        if (!cancelled) {setRunning(false)}
      })

    return () => {
      cancelled = true
    }
  }, [applyTrace, call, selectedId])

  const runPlayground = async (runLlm: boolean) => {
    setRunning(true)
    setError('')

    try {
      const trace = await call<PlaygroundTrace>('/eval/playground/run', {
        method: 'POST',
        body: {
          client_id: selectedId,
          input_json: inputText,
          run_llm: runLlm
        },
        timeoutMs: runLlm ? OUTREACH_AI_TIMEOUT_MS : 30_000
      })
      applyTrace(trace)
      if (runLlm) {
        setCompareOpen(true)
        setDebugKey('llm')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  const debugText = outputs[debugKey] || ''
  const heuristicPretty = outputs.heuristic || ''
  const llmPretty = outputs.llm || ''

  return (
    <div
      className={cn('ms-playground', !embedded && 'ms-page')}
      data-selectable-text="true"
    >
      {!embedded ? (
        <div className="ms-page-header">
          <div>
            <h1>AI тест · клиенты</h1>
            <p className="ms-muted">
              Контроль качества: правьте входной JSON → Смотрите Саммари / Повод / Рекомендацию
            </p>
            {meta ? <p className="ms-muted ms-sync-meta">{meta}</p> : null}
          </div>
          <div className="ms-actions">
            <button className="ms-btn" onClick={() => host.navigate('/clients')} type="button">
              ← Клиенты
            </button>
            <button
              className="ms-btn"
              disabled={running || !inputText.trim()}
              onClick={() => void runPlayground(false)}
              title="Heuristic по текущему JSON (без LLM)"
              type="button"
            >
              {running ? 'Считаю…' : 'Пересчитать'}
            </button>
            <button
              className="ms-btn ms-btn-primary"
              disabled={running || !inputText.trim()}
              onClick={() => void runPlayground(true)}
              title="Вызов auxiliary LLM — сравните с heuristic"
              type="button"
            >
              Запустить LLM
            </button>
          </div>
        </div>
      ) : (
        <div className="ms-playground-float-actions">
          {meta ? <p className="ms-muted ms-sync-meta">{meta}</p> : null}
          <div className="ms-actions">
            <button
              className="ms-btn"
              disabled={running || !inputText.trim()}
              onClick={() => void runPlayground(false)}
              title="Heuristic по текущему JSON (без LLM)"
              type="button"
            >
              {running ? 'Считаю…' : 'Пересчитать'}
            </button>
            <button
              className="ms-btn ms-btn-primary"
              disabled={running || !inputText.trim()}
              onClick={() => void runPlayground(true)}
              title="Вызов auxiliary LLM — сравните с heuristic"
              type="button"
            >
              Запустить LLM
            </button>
          </div>
        </div>
      )}

      <div className="ms-playground-toolbar">
        <label className="ms-playground-pick">
          Клиент из golden dataset
          <select
            disabled={loadingList || running}
            onChange={e => setSelectedId(e.target.value)}
            value={selectedId}
          >
            {clients.length === 0 ? <option value="">Нет клиентов</option> : null}
            {clients.map(c => (
              <option key={c.id || c.name} value={c.id || ''}>
                {(c.name || '—') +
                  ` · заказов ${c.order_count ?? 0}` +
                  (c.avg_check ? ` · ≈ ${Math.round(c.avg_check)} ₽` : '')}
              </option>
            ))}
          </select>
        </label>
        <div className="ms-playground-badges">
          <span className={`ms-playground-badge${aiSource === 'llm' ? ' is-llm' : ' is-heur'}`}>
            {aiSource === 'llm' ? 'LLM' : 'Heuristic'}
          </span>
          {running ? <span className="ms-playground-badge is-run">идёт генерация…</span> : null}
        </div>
      </div>

      {error ? <MsErrorModal message={error} onClose={() => setError('')} /> : null}

      <section aria-label="Качество генерации" className="ms-playground-quality">
        <div className="ms-playground-quality-head">
          <h2 className="ms-section-title">Выход AI — монитор качества</h2>
          <p className="ms-muted">
            Три поля ниже — результат. Читайте как продавец: конкретика, без выдумок, ясный next step.
            Текст можно править вручную для заметок (на генерацию влияет только входной JSON).
          </p>
        </div>
        <div className="ms-playground-quality-grid">
          {PLAYGROUND_QUALITY_FIELDS.map(field => {
            const box = qualityMap[field.id]
            const stats = playgroundStats(box.value)
            const empty = stats.chars === 0

            return (
              <label className={`ms-playground-qcard${empty ? ' is-empty' : ''}`} key={field.id}>
                <span className="ms-playground-qcard-top">
                  <span className="ms-ai-label">{field.label}</span>
                  <span className="ms-playground-qmeta">
                    {stats.words} сл. · {stats.chars} зн.
                    {empty ? ' · пусто' : ''}
                  </span>
                </span>
                <span className="ms-muted ms-playground-qhint">{field.hint}</span>
                <textarea
                  className="ms-playground-prose"
                  onChange={e => box.set(e.target.value)}
                  placeholder={running ? 'Генерация…' : 'Нет текста — пересчитайте или запустите LLM'}
                  spellCheck
                  value={box.value}
                />
              </label>
            )
          })}
        </div>
      </section>

      <section aria-label="Управление входом" className="ms-playground-control">
        <button
          className="ms-playground-fold"
          onClick={() => setInputOpen(v => !v)}
          type="button"
        >
          {inputOpen ? '▾' : '▸'} Входные факты (JSON) — правьте → Пересчитать / LLM
        </button>
        {inputOpen ? (
          <label className="ms-playground-pane ms-playground-input">
            <span className="ms-muted">
              Единственный рычаг генерации. Меняйте client/orders/risks и жмите кнопки сверху.
            </span>
            <textarea
              onChange={e => setInputText(e.target.value)}
              placeholder="JSON клиента + заказов / risks…"
              spellCheck={false}
              value={inputText}
            />
          </label>
        ) : null}
      </section>

      <section aria-label="Сравнение и отладка" className="ms-playground-debug">
        <button
          className="ms-playground-fold"
          onClick={() => setCompareOpen(v => !v)}
          type="button"
        >
          {compareOpen ? '▾' : '▸'} Сравнение Heuristic ↔ LLM · отладка
        </button>
        {compareOpen ? (
          <div className="ms-playground-debug-body">
            <div className="ms-playground-compare">
              <label className="ms-playground-pane">
                <span className="ms-ai-label">Heuristic JSON</span>
                <textarea
                  className="ms-playground-mono"
                  readOnly
                  spellCheck={false}
                  value={heuristicPretty || '— сначала Пересчитать —'}
                />
              </label>
              <label className="ms-playground-pane">
                <span className="ms-ai-label">LLM JSON</span>
                <textarea
                  className="ms-playground-mono"
                  readOnly
                  spellCheck={false}
                  value={llmPretty || '— Запустите LLM —'}
                />
              </label>
            </div>
            <div className="ms-filter-tabs" role="tablist">
              {PLAYGROUND_DEBUG_TABS.map(tab => (
                <button
                  className={`ms-filter-tab${debugKey === tab.id ? ' is-active' : ''}`}
                  key={tab.id}
                  onClick={() => setDebugKey(tab.id)}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <label className="ms-playground-pane">
              <span className="ms-ai-label">
                Debug · {PLAYGROUND_DEBUG_TABS.find(t => t.id === debugKey)?.label || debugKey}
              </span>
              <textarea
                className="ms-playground-mono"
                readOnly
                spellCheck={false}
                value={debugText || (running ? 'Загрузка…' : '—')}
              />
            </label>
          </div>
        ) : null}
      </section>
    </div>
  )
}

function AiPlaygroundLegacyRoute() {
  useEffect(() => {
    openAiPlayground()
    host.navigate('/clients')
  }, [])

  return <p className="ms-muted">Открываю AI тест…</p>
}

const plugin: HermesPlugin = {
  id: 'moysklad',
  name: 'МойСклад CRM',
  // Iris default-on. Settings ▸ Plugins can disable. Live data needs
  // MOYSKLAD_API_TOKEN in .env.
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    restStream = ctx.restStream
    ctx.onDispose(() => {
      rest = null
      restStream = null
    })

    ctx.registerMany([
      {
        id: 'clients-page',
        area: ROUTES_AREA,
        data: { path: '/clients' } satisfies RouteContribution,
        render: () => <ClientsPage />
      },
      {
        id: 'clients-playground-page',
        area: ROUTES_AREA,
        data: { path: '/clients/playground' } satisfies RouteContribution,
        render: () => <AiPlaygroundLegacyRoute />
      },
      {
        id: 'clients-playground-chrome',
        area: TITLEBAR_AREAS.center,
        render: () => <AiPlaygroundChrome />
      },
      {
        id: 'campaigns-page',
        area: ROUTES_AREA,
        data: { path: '/campaigns' } satisfies RouteContribution,
        render: () => <CampaignsPage />
      },
      {
        id: 'clients-nav',
        area: SIDEBAR_NAV_AREA,
        order: 40,
        data: {
          codicon: 'organization',
          label: 'Клиенты',
          path: '/clients'
        } satisfies SidebarNavContribution
      },
      {
        id: 'campaigns-nav',
        area: SIDEBAR_NAV_AREA,
        order: 42,
        data: {
          codicon: 'mail',
          label: 'Рассылки',
          path: '/campaigns'
        } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
