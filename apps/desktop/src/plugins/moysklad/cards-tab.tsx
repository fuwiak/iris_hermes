/**
 * «Карточки товаров» — UI 1:1 with design/mocks/kartochki-tovarov.html.
 *
 * GET /cards/marketplaces → combined rows + Flowwow/Yandex sections.
 * List view: topbar search, marketplace cards, status tabs, product table,
 * ИИ-помощник + быстрые действия. Drawers: detail, chat, recommendations,
 * params. Sub-views: create / seo / placement / orders / analytics.
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
  yandex: 'Яндекс Маркет',
  yandex_market: 'Яндекс Маркет'
}

const REC_BLOCK_LABELS: Record<string, string> = {
  low_rating: 'Низкий контент-рейтинг',
  few_photos: 'Мало фото',
  add_to_yandex: 'Добавить на Яндекс Маркет',
  add_to_flowwow: 'Добавить на Flowwow',
  duplicates: 'Дубли артикулов',
  price_gaps: 'Разные цены на площадках',
  hidden_candidates: 'Скрыта, контент готов'
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
  background: 'rgba(61,42,92,.28)',
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
  background: 'var(--hermes-bg, #ffffff)',
  borderLeft: '1px solid var(--hermes-border, #e8eaf0)',
  boxShadow: '-8px 0 24px rgba(61,42,92,.12)',
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
  const entry = cardMessageBlock(card)
  const listings = card.listings || {}
  const url = Object.values(listings)
    .map(product => product.url || '')
    .find(Boolean)

  return JSON.stringify({
    kind: 'ms-card',
    name: entry.name,
    image: entry.image,
    block: entry.block,
    url: url || ''
  })
}

const PLUS_STYLE: React.CSSProperties = {
  position: 'absolute',
  left: 6,
  top: 6,
  zIndex: 2,
  width: 30,
  height: 30,
  borderRadius: '50%',
  border: '1px solid var(--hermes-border, #e8eaf0)',
  background: 'rgba(255,255,255,.95)',
  color: '#1e2033',
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

type CardRecItem = {
  block?: string
  name?: string
  action?: string
  docs?: string
  docs_source?: string
  fact?: {
    marketplace?: string
    rating?: number | null
    images?: number | null
    price?: number | null
    prices?: Record<string, number>
    gap_pct?: number
    article?: string
  }
}

type CardRecPayload = {
  ok?: boolean
  name?: string
  found?: boolean
  recommendations?: CardRecItem[]
}

function CombinedDrawer({
  added,
  card,
  onAdd,
  onClose,
  onRemove,
  rest
}: {
  added?: boolean
  card: CombinedCard
  onAdd?: (card: CombinedCard) => void
  onClose: () => void
  onRemove?: (name: string) => void
  rest?: CardsRest
}) {
  const listings = Object.entries(card.listings || {})
  const first = listings[0]?.[1]
  const [recs, setRecs] = useState<CardRecItem[] | null>(null)
  const [recsError, setRecsError] = useState('')

  useEffect(() => {
    if (!rest || !card.name) {
      setRecs(null)
      setRecsError('')
      return
    }
    let cancelled = false
    setRecs(null)
    setRecsError('')
    const q = encodeURIComponent(card.name)
    rest<CardRecPayload>(`/cards/recommendations?name=${q}`, { timeoutMs: 120_000 })
      .then(payload => {
        if (!cancelled) {
          setRecs(payload.recommendations || [])
        }
      })
      .catch(err => {
        if (!cancelled) {
          setRecsError(err instanceof Error ? err.message : String(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [card.name, rest])

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
          {rest ? (
            <section
              style={{
                borderTop: '1px solid var(--hermes-border, rgba(128,128,128,.25))',
                paddingTop: 10,
                display: 'flex',
                flexDirection: 'column',
                gap: 8
              }}
            >
              <strong>Рекомендации по карточке</strong>
              <p className="ms-muted" style={{ margin: 0, fontSize: 12 }}>
                Данные площадок + справка по размещению и продвижению
              </p>
              {recsError ? <p className="ms-error">{recsError}</p> : null}
              {!recs && !recsError ? <p className="ms-muted">Считаем…</p> : null}
              {recs && !recs.length ? (
                <p className="ms-muted">Замечаний нет — карточка в порядке при текущих правилах.</p>
              ) : null}
              {recs?.map((item, idx) => (
                <div
                  key={`${item.block || 'rec'}-${idx}`}
                  style={{
                    border: '1px solid var(--hermes-border, rgba(128,128,128,.3))',
                    borderRadius: 8,
                    padding: '8px 10px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4
                  }}
                >
                  <strong>
                    {REC_BLOCK_LABELS[item.block || ''] || item.block || 'Действие'}
                  </strong>
                  {item.action ? <span>{item.action}</span> : null}
                  {item.docs_source ? (
                    <span className="ms-muted" style={{ fontSize: 12 }}>
                      Docs: {item.docs_source}
                    </span>
                  ) : null}
                  {item.docs ? (
                    <span className="ms-muted" style={{ fontSize: 12, lineHeight: 1.4 }}>
                      {item.docs}
                    </span>
                  ) : null}
                </div>
              ))}
            </section>
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
  meta?: Record<
    string,
    { rule?: string; source?: string; docs?: string; docs_source?: string; docs_action?: string }
  >
  knowledge?: {
    entry_count?: number
    marketplaces?: string[]
    blocks?: Record<string, { id?: string; title?: string; source_label?: string }[]>
  }
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
          Действия опираются на справку площадок (размещение / продвижение).
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
                const meta = data.meta?.[key as string]

                return (
                  <section key={key}>
                    <strong>
                      {label} ({rows.length})
                    </strong>
                    {meta ? (
                      <p className="ms-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
                        Правило: {meta.rule} · Источник: {meta.source}
                        {meta.docs_source ? ` · Docs: ${meta.docs_source}` : ''}
                      </p>
                    ) : null}
                    {meta?.docs_action ? (
                      <p className="ms-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
                        По справке площадки: {meta.docs_action}
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
  ['seo', 'СЕО · продвижение'],
  ['placement', 'Куда добавить'],
  ['create', 'Создание'],
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
                {meta.docs_source ? ` · Docs: ${meta.docs_source}` : ''}
              </p>
            ) : null}
            {meta?.docs_action ? (
              <p className="ms-muted" style={{ margin: '2px 0 0', fontSize: 12 }}>
                По справке площадки: {meta.docs_action}
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

function SeoPromotionPanel({
  data,
  onAskAi,
  onOpenParams,
  params
}: {
  data: RecPayload | null
  onAskAi: () => void
  onOpenParams: () => void
  params: RecParams
}) {
  const lowCount = (data?.low_rating || []).length
  const fewCount = (data?.few_photos || []).length
  const total = data?.cards_total ?? 0

  const tips: string[] = []
  const pushMetaTips = (key: string) => {
    const meta = data?.meta?.[key]
    if (!meta) {
      return
    }
    if (meta.docs_action) {
      tips.push(meta.docs_action)
    } else if (meta.docs) {
      tips.push(meta.docs)
    } else if (meta.rule) {
      tips.push(meta.rule)
    }
  }
  pushMetaTips('low_rating')
  pushMetaTips('few_photos')
  const yandexBoost = data?.knowledge?.blocks?.add_to_yandex
  if (yandexBoost?.length) {
    const block = yandexBoost[0]
    tips.push(
      block.title
        ? `${block.title}${block.source_label ? ` · ${block.source_label}` : ''}`
        : 'Добавление на Яндекс Маркет расширяет охват и показы'
    )
  }

  return (
    <div className="ms-kc-seo">
      <div className="ms-kc-seo-head">
        <h2>СЕО · продвижение карточек</h2>
        <div className="ms-kc-seo-actions">
          <button className="ms-kc-btn-ghost" onClick={onOpenParams} type="button">
            Параметры
          </button>
          <button className="ms-kc-btn-ai" onClick={onAskAi} type="button">
            Спросить ИИ про продвижение
          </button>
        </div>
      </div>
      <div className="ms-kc-seo-stats">
        <div className="ms-kc-seo-stat">
          <div className="ms-kc-seo-stat-val">{data ? lowCount : '—'}</div>
          Низкий контент-рейтинг
        </div>
        <div className="ms-kc-seo-stat">
          <div className="ms-kc-seo-stat-val">{data ? fewCount : '—'}</div>
          Мало фото (&lt; {params.minPhotos})
        </div>
        <div className="ms-kc-seo-stat">
          <div className="ms-kc-seo-stat-val">{data ? total : '—'}</div>
          Карточек в каталоге
        </div>
      </div>
      {tips.length ? (
        <div className="ms-kc-seo-tips">
          {tips.map((tip, idx) => (
            <p key={idx}>{tip}</p>
          ))}
        </div>
      ) : null}
      <RecBlockList
        blocks={[
          ['low_rating', 'Низкий контент-рейтинг (Яндекс)'],
          ['few_photos', `Мало фото (< ${params.minPhotos})`]
        ]}
        data={data}
      />
    </div>
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

const CARDS_RAIL_OPEN_KEY = 'moysklad.cardsRail.open'

/** The rail starts CLOSED: the compose area is the work surface, the card feed
 *  is an occasional lookup. Collapsed also means no /cards/marketplaces call
 *  (that request runs up to 120s), so opening Рассылки costs nothing. */
function storedCardsRailOpen(): boolean {
  try {
    return window.localStorage.getItem(CARDS_RAIL_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

function persistCardsRailOpen(open: boolean) {
  try {
    window.localStorage.setItem(CARDS_RAIL_OPEN_KEY, open ? '1' : '0')
  } catch {
    /* private mode / storage off — the rail just forgets its state */
  }
}

/** Narrow scrollable card feed for the Рассылки compose area: the combined
 * list of both marketplaces, first card on top, scroll down for the rest.
 * Collapsed by default — a thin «Карточки» button holds its place. */
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
  const [open, setOpen] = useState(storedCardsRailOpen)
  const [cards, setCards] = useState<CombinedCard[] | null>(null)
  const [selected, setSelected] = useState<CombinedCard | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) {
      return
    }

    rest<CardsPayload>('/cards/marketplaces?limit=100', { timeoutMs: 120_000 })
      .then(payload => setCards(payload.combined || []))
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
  }, [open, rest])

  const toggle = (next: boolean) => {
    setOpen(next)
    persistCardsRailOpen(next)

    if (!next) {
      setSelected(null)
    }
  }

  if (!open) {
    return (
      <aside style={{ flexShrink: 0, position: 'sticky', top: 8 }}>
        <button
          className="ms-link-btn"
          onClick={() => toggle(true)}
          title="Показать карточки маркетплейсов"
          type="button"
        >
          Карточки ▸
        </button>
      </aside>
    )
  }

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
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
          <strong>Карточки</strong>
          <button className="ms-link-btn" onClick={() => toggle(false)} title="Скрыть" type="button">
            ✕
          </button>
        </div>
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
          rest={rest}
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
  onPick: (card: CombinedCard, photoUrl: string) => void
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
                      onPick(card, src)
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

type StatusBucket = 'all' | 'draft' | 'published' | 'needs_update' | 'errors'

type RowVisual = {
  bucket: Exclude<StatusBucket, 'all'>
  pill: 'ok' | 'warn' | 'draft' | 'err'
  label: string
  note: string
}

const PAGE_SIZE = 6

const STATUS_TABS: { id: StatusBucket; label: string }[] = [
  { id: 'all', label: 'Все товары' },
  { id: 'draft', label: 'Черновики' },
  { id: 'published', label: 'Опубликованные' },
  { id: 'needs_update', label: 'Требуют обновления' },
  { id: 'errors', label: 'Ошибки' }
]

const TABLE_MPS: { key: string; short: string; label: string; tone: 'fw' | 'fl' | 'ya' }[] = [
  { key: 'flowwow', short: 'F', label: 'Флаувау', tone: 'fw' },
  { key: 'flowery', short: 'Fl', label: 'Flowery', tone: 'fl' },
  { key: 'yandex_market', short: 'Я', label: 'Яндекс', tone: 'ya' }
]

function listingBadge(product: MarketplaceProduct | undefined): 'ok' | 'warn' | 'err' | null {
  if (!product) {
    return null
  }
  if (product.is_archived) {
    return 'err'
  }
  if (product.is_active) {
    if (product.content_rating != null && product.content_rating < 60) {
      return 'warn'
    }
    return 'ok'
  }
  return 'warn'
}

function classifyCard(card: CombinedCard): RowVisual {
  const listings = Object.values(card.listings || {})
  const mps = card.marketplaces || []
  if (!mps.length || !listings.length) {
    return { bucket: 'draft', pill: 'draft', label: 'Черновик', note: '• Не опубликован' }
  }

  const active = listings.filter(p => p.is_active)
  const hidden = listings.filter(p => !p.is_active && !p.is_archived)
  const archived = listings.filter(p => p.is_archived)
  const low = listings.filter(p => p.content_rating != null && p.content_rating < 60)

  if (!active.length && archived.length && !hidden.length) {
    const n = archived.length
    return {
      bucket: 'errors',
      pill: 'err',
      label: 'Ошибка публикации',
      note: n === 1 ? '• 1 площадка' : `• ${n} площадки`
    }
  }

  if (!active.length) {
    return { bucket: 'draft', pill: 'draft', label: 'Черновик', note: '• Не опубликован' }
  }

  if (low.length || hidden.length) {
    const n = low.length + hidden.length
    return {
      bucket: 'needs_update',
      pill: 'warn',
      label: 'Требует обновления',
      note: n === 1 ? '• 1 площадка' : `• ${n} площадки`
    }
  }

  const allKnown = TABLE_MPS.filter(m => m.key !== 'flowery').every(m => mps.includes(m.key))
  return {
    bucket: 'published',
    pill: 'ok',
    label: 'Опубликован',
    note: allKnown ? '• Все площадки' : active.length === 1 ? '• 1 площадка' : `• ${active.length} площадки`
  }
}

function cardProductId(card: CombinedCard): string {
  const listings = Object.values(card.listings || {})
  const id = listings.map(p => p.product_id || p.offer_id).find(Boolean)
  return id != null ? String(id) : '—'
}

function pageWindow(current: number, total: number): (number | 'ellipsis')[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  const pages: (number | 'ellipsis')[] = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  if (start > 2) {
    pages.push('ellipsis')
  }
  for (let p = start; p <= end; p++) {
    pages.push(p)
  }
  if (end < total - 1) {
    pages.push('ellipsis')
  }
  pages.push(total)
  return pages
}

function exportCardsJson(cards: CombinedCard[]) {
  const blob = new Blob([JSON.stringify(cards, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'iris-cards-export.json'
  a.click()
  URL.revokeObjectURL(url)
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
  const [mpFilter, setMpFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [statusTab, setStatusTab] = useState<StatusBucket>('all')
  const [query, setQuery] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [page, setPage] = useState(1)

  useEffect(() => {
    if (subTab === 'seo' || subTab === 'placement') {
      setRecData(null)
      rest<RecPayload>(`/cards/recommendations?${recQuery(params)}`, { timeoutMs: 120_000 })
        .then(setRecData)
        .catch(() => setRecData({}))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subTab, params])

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

  const searched = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return combined
    }
    return combined.filter(card => (card.name || '').toLowerCase().includes(q))
  }, [combined, query])

  const counts = useMemo(() => {
    const next: Record<StatusBucket, number> = {
      all: searched.length,
      draft: 0,
      published: 0,
      needs_update: 0,
      errors: 0
    }
    for (const card of searched) {
      next[classifyCard(card).bucket] += 1
    }
    return next
  }, [searched])

  const filtered = useMemo(
    () =>
      searched.filter(card => {
        const mps = card.marketplaces || []
        if (mpFilter === 'both' && mps.length < 2) {
          return false
        }
        if (mpFilter !== 'all' && mpFilter !== 'both' && !mps.includes(mpFilter)) {
          return false
        }
        if (statusFilter !== 'all' && !(card.statuses || []).includes(statusFilter)) {
          return false
        }
        if (statusTab !== 'all' && classifyCard(card).bucket !== statusTab) {
          return false
        }
        return true
      }),
    [searched, mpFilter, statusFilter, statusTab]
  )

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE) || 1)

  useEffect(() => {
    setPage(1)
  }, [query, statusTab, mpFilter, statusFilter])

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages)
    }
  }, [page, totalPages])

  const pageItems = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return filtered.slice(start, start + PAGE_SIZE)
  }, [filtered, page])

  const shopName =
    data?.flowwow?.shop?.name || data?.yandex?.business?.name || 'Цветочная студия'
  const problems = [data?.flowwow, data?.yandex]
    .map(section => section?.error || (!section?.configured ? section?.note : ''))
    .filter(Boolean) as string[]

  const flowwowOk = Boolean(data?.flowwow?.configured && !data.flowwow.error)
  const yandexOk = Boolean(data?.yandex?.configured && !data.yandex.error)
  const flowwowTotal = data?.flowwow?.total ?? data?.flowwow?.products?.length ?? 0
  const yandexTotal = data?.yandex?.total ?? data?.yandex?.products?.length ?? 0

  const openCreate = () => setSubTab('create')

  return (
    <div className="ms-page ms-cards-page">
      <header className="ms-kc-topbar">
        <label className="ms-kc-search">
          <svg fill="none" height="18" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            onChange={ev => setQuery(ev.target.value)}
            placeholder="Поиск по товарам..."
            type="search"
            value={query}
          />
          <span className="ms-kc-kbd">⌘ K</span>
        </label>
        <div className="ms-kc-topbar-actions">
          <button
            aria-label="Обновить"
            className="ms-kc-icon-btn"
            disabled={loading}
            onClick={() => void load(true)}
            type="button"
          >
            <svg fill="none" height="18" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
              <path d="M15 18a3 3 0 0 1-6 0" />
              <path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5 1.5 5H4.5S6 14 6 10z" />
            </svg>
            {problems.length ? <span className="ms-kc-dot" /> : null}
          </button>
          <button
            aria-label="Чат по карточкам"
            className="ms-kc-icon-btn"
            onClick={() => setChatOpen(true)}
            type="button"
          >
            <svg fill="none" height="18" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
              <path d="M4 6h16v11H8l-4 3V6z" />
            </svg>
          </button>
          <button className="ms-kc-biz" onClick={() => setParamsOpen(true)} type="button">
            <div aria-hidden="true" className="ms-kc-biz-logo">
              <svg fill="none" height="14" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" width="14">
                <path d="M12 3c2 4 2 6 0 9-2-3-2-5 0-9z" />
                <path d="M12 12v9" />
              </svg>
            </div>
            <div className="ms-kc-biz-meta">
              <div className="ms-kc-biz-name">{shopName}</div>
              <div className="ms-kc-biz-sub">Основной магазин</div>
            </div>
            <svg fill="none" height="16" stroke="#858BA3" strokeWidth="2" viewBox="0 0 24 24" width="16">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        </div>
      </header>

      <div className="ms-kc-page-head">
        <div>
          <h1>Карточки товаров</h1>
          <p>
            Создавайте и управляйте карточками в одном окне. Публикуйте на маркетплейсах в пару кликов.
          </p>
        </div>
        <button className="ms-kc-btn-primary" onClick={openCreate} type="button">
          + Создать карточку
        </button>
      </div>

      {error ? <p className="ms-kc-error">{error}</p> : null}
      {problems.map((text, idx) => (
        <p className="ms-kc-problems" key={idx}>
          {text}
        </p>
      ))}

      <section aria-label="Интеграции маркетплейсов" className="ms-kc-mp-row">
        <article className="ms-kc-mp-card">
          <div className="ms-kc-mp-logo fw">F</div>
          <div className="ms-kc-mp-body">
            <div className="ms-kc-mp-name">Флаувау</div>
            <div className={`ms-kc-mp-status${flowwowOk ? '' : data?.flowwow?.error ? ' is-err' : ' is-off'}`}>
              {flowwowOk ? 'Подключен' : data?.flowwow?.error ? 'Ошибка' : 'Не подключен'}
            </div>
            <div className="ms-kc-mp-count">{flowwowOk ? `${flowwowTotal} товаров` : 'Нет данных'}</div>
          </div>
          <button className="ms-kc-btn-ghost" onClick={() => setParamsOpen(true)} type="button">
            Настроить
          </button>
        </article>
        <article className="ms-kc-mp-card">
          <div className="ms-kc-mp-logo fl">Fl</div>
          <div className="ms-kc-mp-body">
            <div className="ms-kc-mp-name">Flowery</div>
            <div className="ms-kc-mp-status is-off">Не подключен</div>
            <div className="ms-kc-mp-count">Скоро</div>
          </div>
          <button className="ms-kc-btn-ghost" onClick={() => setParamsOpen(true)} type="button">
            Настроить
          </button>
        </article>
        <article className="ms-kc-mp-card">
          <div className="ms-kc-mp-logo ya">Я</div>
          <div className="ms-kc-mp-body">
            <div className="ms-kc-mp-name">Яндекс Цветы</div>
            <div className={`ms-kc-mp-status${yandexOk ? '' : data?.yandex?.error ? ' is-err' : ' is-off'}`}>
              {yandexOk ? 'Подключен' : data?.yandex?.error ? 'Ошибка' : 'Не подключен'}
            </div>
            <div className="ms-kc-mp-count">{yandexOk ? `${yandexTotal} товаров` : 'Нет данных'}</div>
          </div>
          <button className="ms-kc-btn-ghost" onClick={() => setParamsOpen(true)} type="button">
            Настроить
          </button>
        </article>
        <button className="ms-kc-mp-card ms-kc-mp-add" onClick={() => setParamsOpen(true)} type="button">
          <span style={{ fontSize: 18, lineHeight: 1 }}>+</span>
          Подключить маркетплейс
        </button>
      </section>

      <div className="ms-kc-content">
        <section className="ms-kc-panel ms-kc-table-panel">
          <div className="ms-kc-tabs-row">
            <div className="ms-kc-tabs" role="tablist">
              {STATUS_TABS.map(tab => (
                <button
                  aria-selected={statusTab === tab.id}
                  className={`ms-kc-tab${statusTab === tab.id ? ' is-active' : ''}`}
                  key={tab.id}
                  onClick={() => {
                    setStatusTab(tab.id)
                    setSubTab('list')
                    setPage(1)
                  }}
                  role="tab"
                  type="button"
                >
                  {tab.label} <span>{counts[tab.id]}</span>
                </button>
              ))}
            </div>
            <button
              className={`ms-kc-filters-btn${filtersOpen ? ' is-open' : ''}`}
              onClick={() => setFiltersOpen(open => !open)}
              type="button"
            >
              <svg fill="none" height="15" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="15">
                <path d="M4 5h16l-6 7v5l-4 2v-7L4 5z" />
              </svg>
              Фильтры
            </button>
          </div>

          <div aria-label="Разделы карточек" className="ms-kc-feature-tabs" role="tablist">
            {CARD_TABS.map(([id, label]) => (
              <button
                aria-selected={subTab === id}
                className={`ms-kc-feature-tab${subTab === id ? ' is-active' : ''}`}
                key={id}
                onClick={() => {
                  if (id === 'list') {
                    setPage(1)
                  }
                  setSubTab(id)
                }}
                role="tab"
                type="button"
              >
                {label}
              </button>
            ))}
          </div>

          {filtersOpen ? (
            <div className="ms-kc-filters-bar">
              <label>
                Маркетплейс
                <select onChange={ev => setMpFilter(ev.target.value)} value={mpFilter}>
                  <option value="all">Все</option>
                  <option value="flowwow">Flowwow / Флаувау</option>
                  <option value="yandex_market">Яндекс Маркет</option>
                  <option value="both">На обоих</option>
                </select>
              </label>
              <label>
                Статус API
                <select onChange={ev => setStatusFilter(ev.target.value)} value={statusFilter}>
                  <option value="all">Все</option>
                  <option value="active">Активна</option>
                  <option value="hidden">Скрыта</option>
                  <option value="archived">В архиве</option>
                </select>
              </label>
            </div>
          ) : null}

          {subTab === 'list' ? (
            <>
              {loading && !data ? (
                <p className="ms-kc-empty">Загружаем…</p>
              ) : pageItems.length ? (
                <table className="ms-kc-table">
                  <thead>
                    <tr>
                      <th style={{ width: '42%' }}>Товар</th>
                      <th style={{ width: '24%' }}>Статус</th>
                      <th style={{ width: '28%' }}>Маркетплейсы</th>
                      <th style={{ width: '6%' }} />
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((card, idx) => {
                      const visual = classifyCard(card)
                      const listings = card.listings || {}
                      return (
                        <tr key={`${card.name || idx}`} onClick={() => setSelected(card)}>
                          <td>
                            <div className="ms-kc-product">
                              {card.image ? (
                                <img alt="" className="ms-kc-thumb" loading="lazy" src={card.image} />
                              ) : (
                                <div className="ms-kc-thumb ms-kc-thumb-empty">нет фото</div>
                              )}
                              <div>
                                <div className="ms-kc-product-name">{card.name || '—'}</div>
                                <div className="ms-kc-product-id">ID: {cardProductId(card)}</div>
                              </div>
                            </div>
                          </td>
                          <td>
                            <div className="ms-kc-status-wrap">
                              <span className={`ms-kc-pill ${visual.pill}`}>{visual.label}</span>
                              <span className="ms-kc-status-note">{visual.note}</span>
                            </div>
                          </td>
                          <td>
                            <div className="ms-kc-mp-icons">
                              {TABLE_MPS.map(mp => {
                                const product = listings[mp.key]
                                const badge = mp.key === 'flowery' ? null : listingBadge(product)
                                return (
                                  <div className="ms-kc-mp-icon" key={mp.key}>
                                    <div className={`ms-kc-mp-circle ${badge ? mp.tone : 'idle'}`}>
                                      {badge ? mp.short : '–'}
                                      {badge ? (
                                        <span className={`ms-kc-mp-badge ${badge}`}>
                                          {badge === 'ok' ? '✓' : '!'}
                                        </span>
                                      ) : null}
                                    </div>
                                    <span className="ms-kc-mp-label">{mp.label}</span>
                                  </div>
                                )
                              })}
                            </div>
                          </td>
                          <td>
                            <button
                              aria-label="Меню"
                              className="ms-kc-row-menu"
                              onClick={ev => {
                                ev.stopPropagation()
                                setSelected(card)
                              }}
                              type="button"
                            >
                              ⋯
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <p className="ms-kc-empty">Карточек по выбранным фильтрам нет.</p>
              )}

              <div className="ms-kc-pagination">
                <div className="ms-kc-page-info">
                  {filtered.length
                    ? `Показано ${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, filtered.length)} из ${filtered.length}`
                    : 'Показано 0 из 0'}
                </div>
                <div className="ms-kc-pages">
                  <button
                    aria-label="Назад"
                    className="ms-kc-page"
                    disabled={page <= 1}
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    type="button"
                  >
                    ‹
                  </button>
                  {pageWindow(page, totalPages).map((item, idx) =>
                    item === 'ellipsis' ? (
                      <span className="ms-kc-page" key={`e-${idx}`} style={{ cursor: 'default' }}>
                        …
                      </span>
                    ) : (
                      <button
                        className={`ms-kc-page${page === item ? ' is-active' : ''}`}
                        key={item}
                        onClick={() => setPage(item)}
                        type="button"
                      >
                        {item}
                      </button>
                    )
                  )}
                  <button
                    aria-label="Вперёд"
                    className="ms-kc-page"
                    disabled={page >= totalPages}
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    type="button"
                  >
                    ›
                  </button>
                </div>
              </div>
            </>
          ) : subTab === 'seo' ? (
            <SeoPromotionPanel
              data={recData}
              onAskAi={() => setChatOpen(true)}
              onOpenParams={() => setParamsOpen(true)}
              params={params}
            />
          ) : (
            <div className="ms-kc-subview">
              {subTab === 'create' ? <CreateTab rest={rest} /> : null}
              {subTab === 'placement' ? (
                <div>
                  <p className="ms-muted">
                    Что добавить на вторую площадку и что привести в порядок — посчитано из данных обеих
                    площадок.
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
              {subTab === 'orders' ? (
                <OrdersTab limit={params.ordersLimit} rest={rest} status={params.ordersStatus} />
              ) : null}
              {subTab === 'analytics' ? <AnalyticsTab months={params.months} rest={rest} /> : null}
            </div>
          )}
        </section>

        <aside className="ms-kc-right-col">
          <section className="ms-kc-panel ms-kc-side-card">
            <h3>
              <svg fill="none" height="18" stroke="#7137F5" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
                <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z" />
              </svg>
              ИИ-помощник
            </h3>
            <p className="ms-kc-sub">Я помогу улучшить карточки и продажи</p>
            <div className="ms-kc-ai-list">
              <button className="ms-kc-ai-item" onClick={() => setSubTab('seo')} type="button">
                <div className="ms-kc-ai-icon g">✓</div>
                <div>
                  <div className="ms-kc-ai-title">СЕО · продвижение</div>
                  <div className="ms-kc-ai-desc">
                    {counts.needs_update ? `${counts.needs_update} товара можно улучшить` : 'Всё в порядке'}
                  </div>
                </div>
              </button>
              <button className="ms-kc-ai-item" onClick={() => setSubTab('seo')} type="button">
                <div className="ms-kc-ai-icon g">✓</div>
                <div>
                  <div className="ms-kc-ai-title">Улучшить описания и фото</div>
                  <div className="ms-kc-ai-desc">Контент-рейтинг и фото</div>
                </div>
              </button>
              <button className="ms-kc-ai-item" onClick={() => setChatOpen(true)} type="button">
                <div className="ms-kc-ai-icon b">#</div>
                <div>
                  <div className="ms-kc-ai-title">Подобрать хештеги</div>
                  <div className="ms-kc-ai-desc">Спросить ИИ-помощника</div>
                </div>
              </button>
              <button className="ms-kc-ai-item" onClick={() => setRecsOpen(true)} type="button">
                <div className="ms-kc-ai-icon o">↗</div>
                <div>
                  <div className="ms-kc-ai-title">Анализ конкурентов</div>
                  <div className="ms-kc-ai-desc">Посмотреть рекомендации</div>
                </div>
              </button>
            </div>
            <button className="ms-kc-btn-ai" onClick={() => setChatOpen(true)} type="button">
              Открыть ИИ-помощника
            </button>
          </section>

          <section className="ms-kc-panel ms-kc-side-card">
            <h3>Быстрые действия</h3>
            <div className="ms-kc-qa-list">
              <button className="ms-kc-qa-item" onClick={() => setSubTab('seo')} type="button">
                <div className="ms-kc-qa-icon">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z" />
                  </svg>
                </div>
                <div>
                  <div className="ms-kc-qa-title">СЕО · продвижение</div>
                  <div className="ms-kc-qa-desc">Рейтинг, фото и советы площадок</div>
                </div>
              </button>
              <button className="ms-kc-qa-item" onClick={() => setParamsOpen(true)} type="button">
                <div className="ms-kc-qa-icon">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <path d="M4 7h16M4 12h10M4 17h7" />
                  </svg>
                </div>
                <div>
                  <div className="ms-kc-qa-title">Массовое редактирование</div>
                  <div className="ms-kc-qa-desc">Параметры и пороги рекомендаций</div>
                </div>
              </button>
              <button className="ms-kc-qa-item" onClick={() => setSubTab('placement')} type="button">
                <div className="ms-kc-qa-icon">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <rect height="12" rx="2" width="12" x="8" y="8" />
                    <path d="M4 16V6a2 2 0 0 1 2-2h10" />
                  </svg>
                </div>
                <div>
                  <div className="ms-kc-qa-title">Копировать на другие площадки</div>
                  <div className="ms-kc-qa-desc">Дублировать карточки</div>
                </div>
              </button>
              <button className="ms-kc-qa-item" onClick={openCreate} type="button">
                <div className="ms-kc-qa-icon">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <path d="M12 3v12" />
                    <path d="m7 10 5 5 5-5" />
                    <path d="M5 19h14" />
                  </svg>
                </div>
                <div>
                  <div className="ms-kc-qa-title">Импорт товаров</div>
                  <div className="ms-kc-qa-desc">Создать из МойСклад</div>
                </div>
              </button>
              <button className="ms-kc-qa-item" onClick={() => exportCardsJson(filtered)} type="button">
                <div className="ms-kc-qa-icon">
                  <svg fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16">
                    <path d="M12 21V9" />
                    <path d="m7 14 5-5 5 5" />
                    <path d="M5 5h14" />
                  </svg>
                </div>
                <div>
                  <div className="ms-kc-qa-title">Экспорт товаров</div>
                  <div className="ms-kc-qa-desc">Скачать каталог JSON</div>
                </div>
              </button>
            </div>
          </section>
        </aside>
      </div>

      {paramsOpen ? <ParamsDrawer onApply={setParams} onClose={() => setParamsOpen(false)} params={params} /> : null}
      {selected ? <CombinedDrawer card={selected} onClose={() => setSelected(null)} rest={rest} /> : null}
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
