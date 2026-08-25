/**
 * «Карточки» tab — one list of ALL marketplace cards with filters.
 *
 * GET /cards/marketplaces returns per-marketplace sections plus `combined`:
 * one row per card, matched across marketplaces by normalized name — a card
 * living on both Flowwow and Яндекс Маркет carries both badges. Filters:
 * marketplace (incl. «на обоих») and card status. Click opens a slide-out
 * drawer with per-marketplace details; «Чат по карточкам» is a placement /
 * promotion advisor (POST /cards/chat). The report analyst chat lives on the
 * Дашборд page (ReportChatDrawer, POST /dashboard/chat).
 * Styles reuse ms-* classes + inline styles so this tab avoids moysklad.css.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

type CardsRest = <T>(
  path: string,
  opts?: { method?: string; body?: unknown; timeoutMs?: number }
) => Promise<T>

type MarketplaceProduct = {
  product_id?: number
  offer_id?: string
  name?: string
  description_preview?: string
  description?: string
  price?: string
  discount?: string
  currency?: string
  is_active?: boolean
  is_archived?: boolean
  url?: string
  image?: string
  images_count?: number
  card_status?: string
  content_rating?: number | null
}

type SectionInfo = {
  configured?: boolean
  note?: string
  error?: string
  shop?: { shop_id?: number; name?: string; address?: string }
  business?: { id?: number; name?: string }
  products?: MarketplaceProduct[]
  total?: number | null
}

export type CombinedCard = {
  name?: string
  image?: string
  marketplaces?: string[]
  statuses?: string[]
  listings?: Record<string, MarketplaceProduct>
}

type CardsPayload = {
  ok?: boolean
  flowwow?: SectionInfo
  yandex?: SectionInfo
  combined?: CombinedCard[]
  generated_at?: string
}

const MP_LABELS: Record<string, string> = {
  flowwow: 'Flowwow',
  yandex_market: 'Яндекс Маркет'
}

const GRID_STYLE: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
  gap: 12,
  marginTop: 12
}

const CARD_STYLE: React.CSSProperties = {
  border: '1px solid var(--hermes-border, rgba(128,128,128,.25))',
  borderRadius: 8,
  overflow: 'hidden',
  display: 'flex',
  flexDirection: 'column',
  minHeight: 240,
  cursor: 'pointer'
}

const THUMB_STYLE: React.CSSProperties = {
  width: '100%',
  height: 140,
  objectFit: 'cover',
  display: 'block',
  background: 'rgba(128,128,128,.12)'
}

const BADGE_STYLE: React.CSSProperties = {
  fontSize: 11,
  padding: '1px 7px',
  borderRadius: 9,
  border: '1px solid var(--hermes-border, rgba(128,128,128,.35))'
}

const OVERLAY_STYLE: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,.45)',
  zIndex: 40
}

const DRAWER_STYLE: React.CSSProperties = {
  position: 'fixed',
  top: 0,
  right: 0,
  bottom: 0,
  width: 'min(460px, 92vw)',
  zIndex: 41,
  display: 'flex',
  flexDirection: 'column',
  background: 'var(--hermes-bg, #241028)',
  borderLeft: '1px solid var(--hermes-border, rgba(128,128,128,.35))',
  boxShadow: '-8px 0 24px rgba(0,0,0,.35)',
  overflowY: 'auto'
}

function priceLabel(p: MarketplaceProduct): string {
  if (!p.price) {
    return '—'
  }

  const base = `${Number(p.price).toLocaleString('ru-RU')} ${p.currency || 'RUB'}`
  const discount = Number(p.discount || 0)
  return discount > 0 ? `${base} · скидка ${discount}%` : base
}

function statusLabel(p: MarketplaceProduct): string {
  return p.is_archived ? 'в архиве' : p.is_active ? 'активна' : 'скрыта'
}

/** The whole listing as message text: name, price, description, URL. */
export function cardMessageBlock(card: CombinedCard): { block: string; image: string; name: string } {
  const listings = Object.values(card.listings || {})
  const withText = listings.find(p => p.description || p.description_preview) || listings[0]
  const price = listings.map(p => p.price).find(Boolean)
  const url = listings.map(p => p.url || '').find(Boolean) || ''
  // No URL line — the photo travels as the actual photo attachment.
  void url
  const lines = [
    `«${card.name || '—'}»`,
    price ? `Цена: ${Math.round(Number(price)).toLocaleString('ru-RU')} ₽` : '',
    (withText?.description || withText?.description_preview || '').trim()
  ].filter(Boolean)
  return { block: lines.join('\n'), image: card.image || '', name: card.name || '—' }
}

export function cardDragPayload(card: CombinedCard): string {
  const listings = card.listings || {}
  const url = Object.values(listings)
    .map(product => product.url || '')
    .find(Boolean)
  return JSON.stringify({ kind: 'ms-card', name: card.name || '', image: card.image || '', url: url || '' })
}

const PLUS_STYLE: React.CSSProperties = {
  position: 'absolute',
  left: 6,
  top: 6,
  zIndex: 2,
  width: 30,
  height: 30,
  borderRadius: '50%',
  border: '1px solid var(--hermes-border, rgba(139,58,160,.9))',
  background: 'rgba(36,16,40,.92)',
  color: 'inherit',
  fontSize: 18,
  lineHeight: 1,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center'
}

function CombinedCardTile({
  added,
  card,
  onAdd,
  onRemove,
  onSelect
}: {
  added?: boolean
  card: CombinedCard
  onAdd?: (card: CombinedCard) => void
  onRemove?: (name: string) => void
  onSelect: (card: CombinedCard) => void
}) {
  const listings = card.listings || {}
  return (
    <div
      draggable
      onClick={() => onSelect(card)}
      onDragStart={ev => {
        ev.dataTransfer.setData('application/x-ms-card', cardDragPayload(card))
        ev.dataTransfer.setData('text/plain', Object.values(listings).map(p => p.url || '').find(Boolean) || card.name || '')
        ev.dataTransfer.effectAllowed = 'copy'
      }}
      role="button"
      style={{ ...CARD_STYLE, position: 'relative' }}
    >
      {onAdd ? (
        <button
          onClick={ev => {
            ev.stopPropagation()

            if (added && onRemove) {
              onRemove(card.name || '')
            } else if (!added) {
              onAdd(card)
            }
          }}
          style={PLUS_STYLE}
          title={added ? 'Убрать из сообщения' : 'В сообщение (текст + фото)'}
          type="button"
        >
          {added ? '✓' : '+'}
        </button>
      ) : null}
      {card.image ? (
        <img alt={card.name || ''} loading="lazy" src={card.image} style={THUMB_STYLE} />
      ) : (
        <div style={{ ...THUMB_STYLE, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span className="ms-muted">нет фото</span>
        </div>
      )}
      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 5, flex: 1 }}>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {(card.marketplaces || []).map(mp => (
            <span key={mp} style={BADGE_STYLE}>
              {MP_LABELS[mp] || mp}
            </span>
          ))}
        </div>
        <strong style={{ fontSize: 13, lineHeight: 1.3 }}>{card.name || '—'}</strong>
        {Object.entries(listings).map(([mp, product]) => (
          <span className="ms-muted" key={mp} style={{ fontSize: 12 }}>
            {MP_LABELS[mp] || mp}: {priceLabel(product)} · {statusLabel(product)}
            {product.content_rating != null ? ` · ${product.content_rating}/100` : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

function CombinedDrawer({
  added,
  card,
  onAdd,
  onClose,
  onRemove
}: {
  added?: boolean
  card: CombinedCard
  onAdd?: (card: CombinedCard) => void
  onClose: () => void
  onRemove?: (name: string) => void
}) {
  const listings = Object.entries(card.listings || {})
  const first = listings[0]?.[1]
  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={DRAWER_STYLE}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>{(card.marketplaces || []).map(mp => MP_LABELS[mp] || mp).join(' + ')}</strong>
          {onAdd ? (
            <button
              className="ms-btn ms-btn-primary"
              onClick={() => {
                if (added && onRemove) {
                  onRemove(card.name || '')
                } else if (!added) {
                  onAdd(card)
                }
              }}
              type="button"
            >
              {added ? '✓ Убрать из сообщения' : '+ В сообщение'}
            </button>
          ) : null}
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        {card.image ? (
          <img alt={card.name || ''} src={card.image} style={{ ...THUMB_STYLE, height: 260 }} />
        ) : null}
        <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <h3 style={{ margin: 0 }}>{card.name || '—'}</h3>
          {listings.map(([mp, product]) => (
            <div
              key={mp}
              style={{
                border: '1px solid var(--hermes-border, rgba(128,128,128,.3))',
                borderRadius: 8,
                padding: '8px 10px',
                display: 'flex',
                flexDirection: 'column',
                gap: 4
              }}
            >
              <strong>{MP_LABELS[mp] || mp}</strong>
              <span>{priceLabel(product)}</span>
              <span className="ms-muted" style={{ fontSize: 12 }}>
                {statusLabel(product)}
                {product.images_count ? ` · фото: ${product.images_count}` : ''}
                {product.content_rating != null ? ` · контент: ${product.content_rating}/100` : ''}
                {product.offer_id ? ` · ${product.offer_id}` : ''}
              </span>
              {product.url ? (
                <a href={product.url} rel="noreferrer" target="_blank">
                  Открыть на площадке ↗
                </a>
              ) : null}
            </div>
          ))}
          {first?.description || first?.description_preview ? (
            <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.45, margin: 0 }}>
              {first.description || first.description_preview}
            </p>
          ) : null}
        </div>
      </aside>
    </>
  )
}

type RecRow = {
  name?: string
  names?: string[]
  marketplace?: string
  article?: string
  rating?: number | null
  images?: number | null
  price?: number | null
  prices?: Record<string, number>
  gap_pct?: number
  action?: string
}

type RecPayload = {
  ok?: boolean
  cards_total?: number
  generated_at?: string
  meta?: Record<string, { rule?: string; source?: string }>
  low_rating?: RecRow[]
  few_photos?: RecRow[]
  add_to_yandex?: RecRow[]
  add_to_flowwow?: RecRow[]
  duplicates?: RecRow[]
  price_gaps?: RecRow[]
  hidden_candidates?: RecRow[]
}

type RecParams = {
  ratingThreshold: number
  minPhotos: number
  priceGapPct: number
  cap: number
  months: number
  ordersLimit: number
  ordersStatus: string
}

const DEFAULT_PARAMS: RecParams = {
  ratingThreshold: 85,
  minPhotos: 3,
  priceGapPct: 10,
  cap: 25,
  months: 4,
  ordersLimit: 25,
  ordersStatus: ''
}

function recQuery(params: RecParams): string {
  return (
    `rating_threshold=${params.ratingThreshold}&min_photos=${params.minPhotos}` +
    `&price_gap_min=${(params.priceGapPct / 100).toFixed(2)}&cap=${params.cap}`
  )
}

const REC_BLOCKS: [keyof RecPayload, string][] = [
  ['low_rating', 'Низкий контент-рейтинг (Яндекс)'],
  ['few_photos', 'Мало фото (< 3)'],
  ['add_to_yandex', 'Добавить на Яндекс Маркет (есть только на Flowwow)'],
  ['add_to_flowwow', 'Добавить на Flowwow (есть только на Яндексе)'],
  ['duplicates', 'Дубли артикулов'],
  ['price_gaps', 'Разные цены на площадках'],
  ['hidden_candidates', 'Скрыты, но контент готов']
]

function recLine(row: RecRow): string {
  const bits: string[] = []
  if (row.marketplace) {
    bits.push(MP_LABELS[row.marketplace] || row.marketplace)
  }

  if (row.rating != null) {
    bits.push(`рейтинг ${row.rating}/100`)
  }

  if (row.images != null) {
    bits.push(`фото: ${row.images}`)
  }

  if (row.price != null) {
    bits.push(`${Math.round(row.price).toLocaleString('ru-RU')} ₽`)
  }

  if (row.prices) {
    bits.push(
      Object.entries(row.prices)
        .map(([mp, value]) => `${MP_LABELS[mp] || mp}: ${Math.round(value).toLocaleString('ru-RU')} ₽`)
        .join(' / ')
    )
  }

  return bits.join(' · ')
}

function RecommendationsDrawer({ onClose, rest }: { onClose: () => void; rest: CardsRest }) {
  const [data, setData] = useState<RecPayload | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    rest<RecPayload>('/cards/recommendations', { timeoutMs: 120_000 })
      .then(setData)
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [rest])

  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={{ ...DRAWER_STYLE, width: 'min(560px, 94vw)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>Рекомендации по данным</strong>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <p className="ms-muted" style={{ padding: '0 14px', margin: 0 }}>
          Посчитано из карточек обеих площадок без ИИ — рейтинг, фото, цены, дубли.
        </p>
        <div style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {error ? <p className="ms-error">{error}</p> : null}
          {!data && !error ? <p className="ms-muted">Считаем…</p> : null}
          {data
            ? REC_BLOCKS.map(([key, label]) => {
                const rows = (data[key] as RecRow[] | undefined) || []
                if (!rows.length) {
                  return null
                }

                return (
                  <section key={key}>
                    <strong>
                      {label} ({rows.length})
                    </strong>
                    <ul style={{ margin: '6px 0 0', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {rows.map((row, idx) => (
                        <li key={idx} style={{ lineHeight: 1.35 }}>
                          {row.name || (row.names || []).join(' / ') || row.article}
                          {recLine(row) ? <span className="ms-muted"> — {recLine(row)}</span> : null}
                          {row.action ? <span className="ms-muted"> → {row.action}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </section>
                )
              })
            : null}
        </div>
      </aside>
    </>
  )
}

type MsAssortmentRow = { id?: string; type?: string; name?: string; price?: number; archived?: boolean }

type CardDraft = { ok?: boolean; name?: string; price?: number | null; drafts?: Record<string, string> }

type YandexOrderRow = {
  id?: number
  campaign?: string
  status?: string
  substatus?: string
  created?: string
  buyer_total?: number
  items?: string[]
}

type CardsAnalyticsPayload = {
  sources?: Record<string, string>
  channel_dynamics?: Record<string, Record<string, { turnover?: number; orders?: number }>>
  yandex_reconciliation?: {
    month?: string
    ms_turnover?: number
    ms_orders?: number
    cabinet_buyer_total?: number
    cabinet_payout_total?: number
    cabinet_orders?: number
    delta_pct?: number
  }[]
}

const CARD_TABS: [string, string][] = [
  ['list', 'Список'],
  ['create', 'Создание'],
  ['seo', 'СЕО'],
  ['placement', 'Куда добавить'],
  ['orders', 'Заказы'],
  ['analytics', 'Аналитика']
]

function RecBlockList({ blocks, data }: { blocks: [keyof RecPayload, string][]; data: RecPayload | null }) {
  if (!data) {
    return <p className="ms-muted">Считаем…</p>
  }

  const nonEmpty = blocks.filter(([key]) => ((data[key] as RecRow[] | undefined) || []).length)
  if (!nonEmpty.length) {
    return <p className="ms-muted">Замечаний нет — всё чисто (при текущих параметрах).</p>
  }

  return (
    <>
      {nonEmpty.map(([key, label]) => {
        const rows = (data[key] as RecRow[] | undefined) || []
        const meta = data.meta?.[key as string]
        return (
          <section key={key} style={{ marginTop: 10 }}>
            <strong>
              {label} ({rows.length})
            </strong>
            {meta ? (
              <p className="ms-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
                Правило: {meta.rule} · Источник: {meta.source}
              </p>
            ) : null}
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {rows.map((row, idx) => (
                <li key={idx} style={{ lineHeight: 1.35 }}>
                  {row.name || (row.names || []).join(' / ') || row.article}
                  {recLine(row) ? <span className="ms-muted"> — {recLine(row)}</span> : null}
                  {row.action ? <span className="ms-muted"> → {row.action}</span> : null}
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </>
  )
}

function NumberField({
  label,
  max,
  min,
  onChange,
  value
}: {
  label: string
  max: number
  min: number
  onChange: (value: number) => void
  value: number
}) {
  return (
    <label className="ms-muted" style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
      {label}
      <input
        max={max}
        min={min}
        onChange={ev => {
          const parsed = Number(ev.target.value)

          if (Number.isFinite(parsed)) {
            onChange(Math.max(min, Math.min(max, parsed)))
          }
        }}
        style={{ width: 90, font: 'inherit', padding: 4 }}
        type="number"
        value={value}
      />
    </label>
  )
}

function ParamsDrawer({
  onApply,
  onClose,
  params
}: {
  onApply: (params: RecParams) => void
  onClose: () => void
  params: RecParams
}) {
  const [draft, setDraft] = useState<RecParams>(params)

  const set = (patch: Partial<RecParams>) => setDraft(prev => ({ ...prev, ...patch }))
  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={{ ...DRAWER_STYLE, width: 'min(380px, 92vw)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>Параметры модели</strong>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <p className="ms-muted" style={{ padding: '0 14px', margin: 0 }}>
          Пороги, по которым считаются рекомендации. Меняете — блоки пересчитываются из тех же данных.
        </p>
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <NumberField
            label="Порог контент-рейтинга (Яндекс)"
            max={100}
            min={1}
            onChange={v => set({ ratingThreshold: v })}
            value={draft.ratingThreshold}
          />
          <NumberField label="Минимум фото" max={20} min={1} onChange={v => set({ minPhotos: v })} value={draft.minPhotos} />
          <NumberField
            label="Порог разницы цен, %"
            max={100}
            min={0}
            onChange={v => set({ priceGapPct: v })}
            value={draft.priceGapPct}
          />
          <NumberField label="Строк в блоке (cap)" max={200} min={1} onChange={v => set({ cap: v })} value={draft.cap} />
          <NumberField label="Месяцев динамики (Аналитика)" max={14} min={2} onChange={v => set({ months: v })} value={draft.months} />
          <NumberField
            label="Лимит заказов (Заказы)"
            max={100}
            min={1}
            onChange={v => set({ ordersLimit: v })}
            value={draft.ordersLimit}
          />
          <label className="ms-muted" style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
            Статус заказов
            <select
              onChange={ev => set({ ordersStatus: ev.target.value })}
              style={{ font: 'inherit', padding: 4 }}
              value={draft.ordersStatus}
            >
              <option value="">Все</option>
              <option value="PROCESSING">PROCESSING</option>
              <option value="DELIVERY">DELIVERY</option>
              <option value="DELIVERED">DELIVERED</option>
              <option value="CANCELLED">CANCELLED</option>
            </select>
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="ms-btn ms-btn-primary"
              onClick={() => {
                onApply(draft)
                onClose()
              }}
              type="button"
            >
              Применить
            </button>
            <button className="ms-btn" onClick={() => setDraft(DEFAULT_PARAMS)} type="button">
              Сбросить
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}

function CreateTab({ rest }: { rest: CardsRest }) {
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<MsAssortmentRow[]>([])
  const [picked, setPicked] = useState<MsAssortmentRow | null>(null)
  const [draft, setDraft] = useState<CardDraft | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const search = useCallback(async () => {
    if (!query.trim()) {
      return
    }

    setBusy('search')
    setError('')
    try {
      const out = await rest<{ rows?: MsAssortmentRow[] }>(
        `/cards/ms-search?query=${encodeURIComponent(query.trim())}`,
        { timeoutMs: 60_000 }
      )
      setRows(out.rows || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy('')
    }
  }, [query, rest])

  const generate = useCallback(
    async (row: MsAssortmentRow) => {
      setPicked(row)
      setDraft(null)
      setBusy('draft')
      setError('')
      try {
        const out = await rest<CardDraft>('/cards/draft', {
          method: 'POST',
          body: { name: row.name, price: row.price },
          timeoutMs: 120_000
        })
        setDraft(out)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy('')
      }
    },
    [rest]
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
      <p className="ms-muted">
        Шаг 1: найдите букет в каталоге МоегоСклада → шаг 2: система сгенерирует описания под каждую
        площадку. Публикация на площадки — следующий этап.
      </p>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          onChange={ev => setQuery(ev.target.value)}
          onKeyDown={ev => {
            if (ev.key === 'Enter') {
              void search()
            }
          }}
          placeholder="Название букета в МоемСкладе…"
          style={{ flex: 1, font: 'inherit', padding: 8 }}
          value={query}
        />
        <button className="ms-btn" disabled={busy === 'search' || !query.trim()} onClick={() => void search()} type="button">
          {busy === 'search' ? 'Ищем…' : 'Найти в МС'}
        </button>
      </div>
      {error ? <p className="ms-error">{error}</p> : null}
      {rows.length ? (
        <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {rows.map(row => (
            <li key={row.id}>
              {row.name}{' '}
              <span className="ms-muted">
                {row.type === 'bundle' ? 'комплект' : row.type} ·{' '}
                {row.price ? `${Math.round(row.price).toLocaleString('ru-RU')} ₽` : 'без цены'}
              </span>{' '}
              <button className="ms-link-btn" disabled={busy === 'draft'} onClick={() => void generate(row)} type="button">
                сгенерировать описание
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {busy === 'draft' ? <p className="ms-muted">Генерируем описания для «{picked?.name}»…</p> : null}
      {draft?.drafts
        ? Object.entries(draft.drafts).map(([mp, text]) => (
            <section key={mp}>
              <strong>{MP_LABELS[mp] || mp}</strong>
              <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.45, marginTop: 4 }}>{text}</p>
            </section>
          ))
        : null}
    </div>
  )
}

function OrdersTab({ limit, rest, status }: { limit: number; rest: CardsRest; status: string }) {
  const [orders, setOrders] = useState<YandexOrderRow[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setOrders(null)
    rest<{ orders?: YandexOrderRow[]; configured?: boolean }>(
      `/cards/orders?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ''}`,
      { timeoutMs: 120_000 }
    )
      .then(out => setOrders(out.orders || []))
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [rest, limit, status])

  return (
    <div style={{ marginTop: 12 }}>
      <p className="ms-muted">
        Живые заказы из кабинета Яндекс Маркета — источник: API /campaigns/…/orders, без кэша (заказы
        заносятся в МойСклад родной интеграцией; Flowwow заказов по API не отдаёт). Лимит {limit}
        {status ? `, статус ${status}` : ''} — меняется в «Параметрах».
      </p>
      {error ? <p className="ms-error">{error}</p> : null}
      {!orders && !error ? <p className="ms-muted">Загружаем…</p> : null}
      {orders ? (
        <div className="ms-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Создан</th>
                <th>Статус</th>
                <th>Сумма</th>
                <th>Точка</th>
                <th>Состав</th>
              </tr>
            </thead>
            <tbody>
              {orders.map(order => (
                <tr key={order.id}>
                  <td>{order.created}</td>
                  <td>
                    {order.status}
                    {order.substatus ? ` · ${order.substatus}` : ''}
                  </td>
                  <td>{order.buyer_total != null ? `${Math.round(order.buyer_total).toLocaleString('ru-RU')} ₽` : '—'}</td>
                  <td>{order.campaign}</td>
                  <td>{(order.items || []).join('; ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

function AnalyticsTab({ months, rest }: { months: number; rest: CardsRest }) {
  const [data, setData] = useState<CardsAnalyticsPayload | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setData(null)
    rest<CardsAnalyticsPayload>(`/cards/analytics?months=${months}`, { timeoutMs: 180_000 })
      .then(setData)
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [rest, months])

  const monthIds = Object.keys(data?.channel_dynamics || {})
  const channels = Array.from(
    new Set(monthIds.flatMap(month => Object.keys(data?.channel_dynamics?.[month] || {})))
  )
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {error ? <p className="ms-error">{error}</p> : null}
      {!data && !error ? <p className="ms-muted">Считаем…</p> : null}
      {monthIds.length ? (
        <section>
          <strong>Динамика каналов из МоегоСклада (оборот ₽ / заказы) · {months} мес.</strong>
          {data?.sources?.channel_dynamics ? (
            <p className="ms-muted" style={{ margin: '2px 0 4px', fontSize: 12 }}>
              Источник: {data.sources.channel_dynamics}
            </p>
          ) : null}
          <div className="ms-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Канал</th>
                  {monthIds.map(month => (
                    <th key={month}>{month}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {channels.map(channel => (
                  <tr key={channel}>
                    <td>{MP_LABELS[channel] || channel}</td>
                    {monthIds.map(month => {
                      const cell = data?.channel_dynamics?.[month]?.[channel]
                      return (
                        <td key={month}>
                          {cell
                            ? `${Math.round(cell.turnover || 0).toLocaleString('ru-RU')} / ${cell.orders ?? 0}`
                            : '—'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {data?.yandex_reconciliation?.length ? (
        <section>
          <strong>Сверка с кабинетом Яндекс Маркета</strong>
          <p className="ms-muted">
            МойСклад пишет цены до скидок — в кабинете фактические продажи.
            {data?.sources?.yandex_reconciliation ? ` Источник: ${data.sources.yandex_reconciliation}` : ''}
          </p>
          <div className="ms-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Месяц</th>
                  <th>МС</th>
                  <th>Кабинет (покупатели)</th>
                  <th>К выплате</th>
                  <th>Δ</th>
                </tr>
              </thead>
              <tbody>
                {data.yandex_reconciliation.map(row => (
                  <tr key={row.month}>
                    <td>{row.month}</td>
                    <td>
                      {Math.round(row.ms_turnover || 0).toLocaleString('ru-RU')} ₽ / {row.ms_orders ?? '—'}
                    </td>
                    <td>
                      {Math.round(row.cabinet_buyer_total || 0).toLocaleString('ru-RU')} ₽ / {row.cabinet_orders ?? '—'}
                    </td>
                    <td>{Math.round(row.cabinet_payout_total || 0).toLocaleString('ru-RU')} ₽</td>
                    <td>{row.delta_pct != null ? `${Math.round(row.delta_pct * 100)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  )
}

type ChatTurn = { role: 'user' | 'assistant'; content: string }

export function ChatDrawer({
  endpoint,
  example,
  hint,
  onClose,
  rest,
  seedFollowups,
  title
}: {
  endpoint: string
  example: string
  hint: string
  onClose: () => void
  rest: CardsRest
  seedFollowups?: string[]
  title: string
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [followups, setFollowups] = useState<string[]>(seedFollowups || [example])

  const send = useCallback(
    async (text?: string) => {
      const content = (text ?? draft).trim()
      if (!content || busy) {
        return
      }

      const next: ChatTurn[] = [...turns, { role: 'user', content }]
      setTurns(next)
      setDraft('')
      setFollowups([])
      setBusy(true)
      setError('')
      try {
        const out = await rest<{ reply?: string; followups?: string[] }>(endpoint, {
          method: 'POST',
          body: { messages: next },
          timeoutMs: 120_000
        })
        setTurns([...next, { role: 'assistant', content: out.reply || '(пустой ответ)' }])
        setFollowups((out.followups || []).filter(Boolean).slice(0, 3))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [busy, draft, endpoint, rest, turns]
  )

  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={{ ...DRAWER_STYLE, width: 'min(520px, 94vw)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>{title}</strong>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <p className="ms-muted" style={{ padding: '0 14px', margin: 0 }}>
          {hint}
        </p>
        <div style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {turns.length === 0 && !followups.length ? <p className="ms-muted">Например: «{example}»</p> : null}
          {turns.length === 0 && followups.length ? (
            <p className="ms-muted">С чего начать — выберите вопрос или напишите свой:</p>
          ) : null}
          {turns.map((turn, idx) => (
            <div
              key={idx}
              style={{
                alignSelf: turn.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '92%',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.45,
                borderRadius: 8,
                padding: '8px 10px',
                border: '1px solid var(--hermes-border, rgba(128,128,128,.3))',
                background: turn.role === 'user' ? 'rgba(128,128,255,.12)' : 'rgba(128,128,128,.10)'
              }}
            >
              {turn.content}
            </div>
          ))}
          {busy ? <p className="ms-muted">Считает…</p> : null}
          {error ? <p className="ms-error">{error}</p> : null}
          {!busy && followups.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {followups.map(question => (
                <button
                  key={question}
                  onClick={() => void send(question)}
                  style={{
                    font: 'inherit',
                    fontSize: 12,
                    padding: '5px 10px',
                    borderRadius: 12,
                    border: '1px dashed var(--hermes-border, rgba(128,128,128,.45))',
                    background: 'transparent',
                    color: 'inherit',
                    cursor: 'pointer',
                    textAlign: 'left'
                  }}
                  type="button"
                >
                  {question}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div style={{ display: 'flex', gap: 8, padding: 14 }}>
          <textarea
            onChange={ev => setDraft(ev.target.value)}
            onKeyDown={ev => {
              if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault()
                void send()
              }
            }}
            placeholder="Вопрос…"
            rows={2}
            style={{ flex: 1, resize: 'vertical', font: 'inherit', padding: 8 }}
            value={draft}
          />
          <button className="ms-btn" disabled={busy || !draft.trim()} onClick={() => void send()} type="button">
            Отправить
          </button>
        </div>
      </aside>
    </>
  )
}

/** Narrow scrollable card feed for the Рассылки compose area: the combined
 * list of both marketplaces, first card on top, scroll down for the rest. */
export function CardsSidePanel({
  addedNames,
  onAddCard,
  onRemoveCard,
  rest
}: {
  addedNames?: string[]
  onAddCard?: (card: CombinedCard) => void
  onRemoveCard?: (name: string) => void
  rest: CardsRest
}) {
  const [cards, setCards] = useState<CombinedCard[] | null>(null)
  const [selected, setSelected] = useState<CombinedCard | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    rest<CardsPayload>('/cards/marketplaces?limit=100', { timeoutMs: 120_000 })
      .then(payload => setCards(payload.combined || []))
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [rest])

  return (
    <aside
      style={{
        width: 260,
        flexShrink: 0,
        position: 'sticky',
        top: 8,
        maxHeight: 'calc(100vh - 120px)',
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid var(--hermes-border, rgba(128,128,128,.3))',
        borderRadius: 8,
        overflow: 'hidden'
      }}
    >
      <div style={{ padding: '8px 10px' }}>
        <strong>Карточки</strong>
        <p className="ms-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
          Обе площадки одним списком{cards ? ` · ${cards.length}` : ''} — листайте вниз
        </p>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 10px 10px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {error ? <p className="ms-error">{error}</p> : null}
        {!cards && !error ? <p className="ms-muted">Загружаем…</p> : null}
        {(cards || []).map((card, idx) => (
          <CombinedCardTile
            added={addedNames?.includes(card.name || '')}
            card={card}
            key={`${card.name || idx}`}
            onAdd={onAddCard}
            onRemove={onRemoveCard}
            onSelect={setSelected}
          />
        ))}
      </div>
      {selected ? (
        <CombinedDrawer
          added={addedNames?.includes(selected.name || '')}
          card={selected}
          onAdd={onAddCard}
          onClose={() => setSelected(null)}
          onRemove={onRemoveCard}
        />
      ) : null}
    </aside>
  )
}

/** Two-step picker: choose a card, then one of its photos. */
export function CardPhotoPicker({
  onClose,
  onPick,
  rest
}: {
  onClose: () => void
  onPick: (name: string, url: string) => void
  rest: CardsRest
}) {
  const [cards, setCards] = useState<CombinedCard[] | null>(null)
  const [card, setCard] = useState<CombinedCard | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    rest<CardsPayload>('/cards/marketplaces?limit=100', { timeoutMs: 120_000 })
      .then(payload => setCards(payload.combined || []))
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [rest])

  const photos = useMemo(() => {
    const out: string[] = []
    Object.values(card?.listings || {}).forEach(product => {
      ;((product as MarketplaceProduct & { images?: string[] }).images || []).forEach(src => {
        if (src && !out.includes(src)) {
          out.push(src)
        }
      })
    })

    if (!out.length && card?.image) {
      out.push(card.image)
    }

    return out
  }, [card])

  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={{ ...DRAWER_STYLE, width: 'min(640px, 96vw)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          {card ? (
            <button className="ms-btn" onClick={() => setCard(null)} type="button">
              ← Карточки
            </button>
          ) : (
            <strong>Выберите карточку</strong>
          )}
          {card ? <strong style={{ flex: 1, textAlign: 'center', fontSize: 13 }}>{(card.name || '').slice(0, 48)}</strong> : <span style={{ flex: 1 }} />}
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
          {error ? <p className="ms-error">{error}</p> : null}
          {!cards && !error ? <p className="ms-muted">Загружаем карточки…</p> : null}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
            {!card
              ? (cards || []).map((item, idx) => (
                  <button
                    key={`${item.name || idx}`}
                    onClick={() => setCard(item)}
                    style={{ ...CARD_STYLE, minHeight: 0, padding: 4, background: 'transparent', color: 'inherit', font: 'inherit' }}
                    type="button"
                  >
                    {item.image ? (
                      <img alt="" loading="lazy" src={item.image} style={{ ...THUMB_STYLE, height: 110, borderRadius: 6 }} />
                    ) : (
                      <span className="ms-muted">нет фото</span>
                    )}
                    <span style={{ fontSize: 11, lineHeight: 1.25, textAlign: 'left' }}>{(item.name || '—').slice(0, 60)}</span>
                  </button>
                ))
              : photos.map((src, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      onPick(card.name || 'card.jpg', src)
                      onClose()
                    }}
                    style={{ ...CARD_STYLE, minHeight: 0, padding: 4, background: 'transparent' }}
                    title="Вставить это фото в сообщение"
                    type="button"
                  >
                    <img alt="" loading="lazy" src={src} style={{ ...THUMB_STYLE, height: 110, borderRadius: 6 }} />
                  </button>
                ))}
          </div>
          {card && !photos.length ? <p className="ms-muted">У карточки нет фото.</p> : null}
        </div>
      </aside>
    </>
  )
}

export function ReportChatDrawer({ onClose, rest }: { onClose: () => void; rest: CardsRest }) {
  return (
    <ChatDrawer
      endpoint="/dashboard/chat"
      example="Построй такой же отчёт по такой же форме за июль и август"
      hint="Считает только из данных МоегоСклада. Если цифр не хватает — скажет каких; пришлите их сообщением, и он пересчитает."
      onClose={onClose}
      rest={rest}
      seedFollowups={[
        'Построй отчёт за последний месяц по всем каналам',
        'Какой канал просел сильнее всего к прошлому месяцу?',
        'Сойдись с кабинетом Яндекса — где расхождение?'
      ]}
      title="Чат-аналитик отчёта"
    />
  )
}

export function CardsPage({ rest }: { rest: CardsRest }) {
  const [data, setData] = useState<CardsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<CombinedCard | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [recsOpen, setRecsOpen] = useState(false)
  const [subTab, setSubTab] = useState('list')
  const [recData, setRecData] = useState<RecPayload | null>(null)
  const [params, setParams] = useState<RecParams>(DEFAULT_PARAMS)
  const [paramsOpen, setParamsOpen] = useState(false)

  useEffect(() => {
    if (subTab === 'seo' || subTab === 'placement') {
      setRecData(null)
      rest<RecPayload>(`/cards/recommendations?${recQuery(params)}`, { timeoutMs: 120_000 })
        .then(setRecData)
        .catch(() => setRecData({}))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subTab, params])
  const [mpFilter, setMpFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')

  const load = useCallback(
    async (force: boolean) => {
      setLoading(true)
      setError('')
      try {
        const payload = await rest<CardsPayload>(
          `/cards/marketplaces?limit=100${force ? '&force=true' : ''}`,
          { timeoutMs: 120_000 }
        )
        setData(payload)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    },
    [rest]
  )

  useEffect(() => {
    void load(false)
  }, [load])

  const combined = useMemo(() => data?.combined || [], [data])
  const filtered = useMemo(
    () =>
      combined.filter(card => {
        const mps = card.marketplaces || []
        if (mpFilter === 'both' && mps.length < 2) {
          return false
        }

        if (mpFilter !== 'all' && mpFilter !== 'both' && !mps.includes(mpFilter)) {
          return false
        }

        return statusFilter === 'all' || (card.statuses || []).includes(statusFilter)
      }),
    [combined, mpFilter, statusFilter]
  )

  const summaryBits: string[] = []
  if (data?.flowwow?.configured && !data.flowwow.error) {
    summaryBits.push(`Flowwow «${data.flowwow.shop?.name || '—'}»: ${data.flowwow.total ?? 0}`)
  }
  if (data?.yandex?.configured && !data.yandex.error) {
    summaryBits.push(`Яндекс «${data.yandex.business?.name || '—'}»: ${data.yandex.total ?? 0}`)
  }
  const problems = [data?.flowwow, data?.yandex]
    .map(section => section?.error || (!section?.configured ? section?.note : ''))
    .filter(Boolean)

  const selectStyle: React.CSSProperties = { font: 'inherit', padding: '4px 8px' }
  return (
    <div className="ms-page ms-cards-page">
      <div className="ms-page-head">
        <h1>Карточки</h1>
        <button className="ms-btn" onClick={() => setParamsOpen(true)} type="button">
          Параметры
        </button>
        <button className="ms-btn" onClick={() => setRecsOpen(true)} type="button">
          Рекомендации
        </button>
        <button className="ms-btn" onClick={() => setChatOpen(true)} type="button">
          Чат по карточкам
        </button>
        <button className="ms-btn" disabled={loading} onClick={() => void load(true)} type="button">
          {loading ? 'Обновляем…' : 'Обновить'}
        </button>
      </div>
      <p className="ms-muted">
        Все карточки обеих площадок одним списком; одинаковая карточка на двух маркетплейсах помечена
        обоими. {summaryBits.join(' · ')}
      </p>
      {problems.map((text, idx) => (
        <p className="ms-muted" key={idx}>
          {text}
        </p>
      ))}
      {error ? <p className="ms-error">{error}</p> : null}
      <div className="ms-filter-tabs" role="tablist">
        {CARD_TABS.map(([id, label]) => (
          <button
            className={`ms-filter-tab${subTab === id ? ' is-active' : ''}`}
            key={id}
            onClick={() => setSubTab(id)}
            role="tab"
            type="button"
          >
            {label}
          </button>
        ))}
      </div>
      {subTab === 'list' ? (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
            <label className="ms-muted">
              Маркетплейс{' '}
              <select onChange={ev => setMpFilter(ev.target.value)} style={selectStyle} value={mpFilter}>
                <option value="all">Все</option>
                <option value="flowwow">Flowwow</option>
                <option value="yandex_market">Яндекс Маркет</option>
                <option value="both">На обоих</option>
              </select>
            </label>
            <label className="ms-muted">
              Статус{' '}
              <select onChange={ev => setStatusFilter(ev.target.value)} style={selectStyle} value={statusFilter}>
                <option value="all">Все</option>
                <option value="active">Активна</option>
                <option value="hidden">Скрыта</option>
                <option value="archived">В архиве</option>
              </select>
            </label>
            <span className="ms-muted">
              {filtered.length} из {combined.length}
            </span>
          </div>
          {loading && !data ? (
            <p className="ms-muted">Загружаем…</p>
          ) : filtered.length ? (
            <div style={GRID_STYLE}>
              {filtered.map((card, idx) => (
                <CombinedCardTile card={card} key={`${card.name || idx}`} onSelect={setSelected} />
              ))}
            </div>
          ) : (
            <p className="ms-muted">Карточек по выбранным фильтрам нет.</p>
          )}
        </>
      ) : null}
      {subTab === 'create' ? <CreateTab rest={rest} /> : null}
      {subTab === 'seo' ? (
        <div style={{ marginTop: 12 }}>
          <p className="ms-muted">
            Контент-рейтинг Яндекса и полнота контента — что поднять, чтобы получать больше показов.
          </p>
          <RecBlockList
            blocks={[
              ['low_rating', 'Низкий контент-рейтинг (Яндекс)'],
              ['few_photos', 'Мало фото (< 3)']
            ]}
            data={recData}
          />
        </div>
      ) : null}
      {subTab === 'placement' ? (
        <div style={{ marginTop: 12 }}>
          <p className="ms-muted">
            Что добавить на вторую площадку и что привести в порядок — посчитано из данных обеих площадок.
          </p>
          <RecBlockList
            blocks={[
              ['add_to_yandex', 'Добавить на Яндекс Маркет (есть только на Flowwow)'],
              ['add_to_flowwow', 'Добавить на Flowwow (есть только на Яндексе)'],
              ['hidden_candidates', 'Скрыты, но контент готов'],
              ['duplicates', 'Дубли артикулов'],
              ['price_gaps', 'Разные цены на площадках']
            ]}
            data={recData}
          />
        </div>
      ) : null}
      {subTab === 'orders' ? <OrdersTab limit={params.ordersLimit} rest={rest} status={params.ordersStatus} /> : null}
      {subTab === 'analytics' ? <AnalyticsTab months={params.months} rest={rest} /> : null}
      {paramsOpen ? <ParamsDrawer onApply={setParams} onClose={() => setParamsOpen(false)} params={params} /> : null}
      {selected ? <CombinedDrawer card={selected} onClose={() => setSelected(null)} /> : null}
      {recsOpen ? <RecommendationsDrawer onClose={() => setRecsOpen(false)} rest={rest} /> : null}
      {chatOpen ? (
        <ChatDrawer
          endpoint="/cards/chat"
          example="Какие карточки стоит добавить на вторую площадку и что исправить в слабых?"
          hint="Консультант по размещению и продвижению: смотрит статусы, фото и контент-рейтинг ваших карточек и говорит, что исправить или добавить — отдельно для каждой площадки."
          onClose={() => setChatOpen(false)}
          rest={rest}
          seedFollowups={[
            'Какие карточки стоит добавить на вторую площадку?',
            'Что исправить в карточках с низким рейтингом?',
            'Где у нас дубли и разные цены на площадках?'
          ]}
          title="Чат по карточкам"
        />
      ) : null}
    </div>
  )
}
