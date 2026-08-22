/**
 * «Карточки» tab — marketplace product cards in one place.
 *
 * Reads GET /cards/marketplaces (moysklad plugin API): Flowwow is live,
 * Yandex Market shows a "needs token" placeholder until the Api-Key is
 * configured. First step of the card-autopublish flow (call 21.08.2026).
 * Styles reuse existing ms-* classes + local inline grid so this tab does
 * not touch moysklad.css.
 */

import { useCallback, useEffect, useState } from 'react'

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

type FlowwowSection = {
  configured?: boolean
  note?: string
  error?: string
  shop?: { shop_id?: number; name?: string; address?: string }
  products?: MarketplaceProduct[]
  total?: number
}

type YandexSection = {
  configured?: boolean
  note?: string
  error?: string
  business?: { id?: number; name?: string }
  products?: MarketplaceProduct[]
  total?: number | null
}

type CardsPayload = {
  ok?: boolean
  flowwow?: FlowwowSection
  yandex?: YandexSection
  generated_at?: string
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
  minHeight: 240
}

const THUMB_STYLE: React.CSSProperties = {
  width: '100%',
  height: 140,
  objectFit: 'cover',
  display: 'block',
  background: 'rgba(128,128,128,.12)'
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
  width: 'min(440px, 92vw)',
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

function ProductCard({
  product,
  onSelect
}: {
  product: MarketplaceProduct
  onSelect?: (product: MarketplaceProduct) => void
}) {
  return (
    <div
      onClick={() => onSelect?.(product)}
      role={onSelect ? 'button' : undefined}
      style={{ ...CARD_STYLE, cursor: onSelect ? 'pointer' : undefined }}
    >
      {product.image ? (
        <img alt={product.name || ''} loading="lazy" src={product.image} style={THUMB_STYLE} />
      ) : (
        <div style={{ ...THUMB_STYLE, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span className="ms-muted">нет фото</span>
        </div>
      )}
      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
        <strong style={{ fontSize: 13, lineHeight: 1.3 }}>
          {product.url ? (
            <a href={product.url} onClick={ev => ev.stopPropagation()} rel="noreferrer" target="_blank">
              {product.name || '—'}
            </a>
          ) : (
            product.name || '—'
          )}
        </strong>
        <span>{priceLabel(product)}</span>
        <span className="ms-muted" style={{ fontSize: 12 }}>
          {product.is_archived ? 'в архиве' : product.is_active ? 'активна' : 'скрыта'}
          {product.images_count ? ` · фото: ${product.images_count}` : ''}
          {product.content_rating != null ? ` · контент: ${product.content_rating}/100` : ''}
        </span>
      </div>
    </div>
  )
}

function ProductDrawer({
  item,
  onClose
}: {
  item: { product: MarketplaceProduct; marketplace: string }
  onClose: () => void
}) {
  const { product, marketplace } = item
  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={DRAWER_STYLE}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>{marketplace}</strong>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        {product.image ? (
          <img alt={product.name || ''} src={product.image} style={{ ...THUMB_STYLE, height: 260 }} />
        ) : null}
        <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h3 style={{ margin: 0 }}>{product.name || '—'}</h3>
          <span>{priceLabel(product)}</span>
          <span className="ms-muted">
            {product.is_archived ? 'в архиве' : product.is_active ? 'активна' : 'скрыта'}
            {product.images_count ? ` · фото: ${product.images_count}` : ''}
            {product.content_rating != null ? ` · контент: ${product.content_rating}/100` : ''}
            {product.card_status ? ` · ${product.card_status}` : ''}
          </span>
          {product.offer_id ? <span className="ms-muted">offerId: {product.offer_id}</span> : null}
          {product.url ? (
            <a href={product.url} rel="noreferrer" target="_blank">
              Открыть на площадке ↗
            </a>
          ) : null}
          {product.description || product.description_preview ? (
            <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.45, margin: 0 }}>
              {product.description || product.description_preview}
            </p>
          ) : (
            <p className="ms-muted">Описания нет.</p>
          )}
        </div>
      </aside>
    </>
  )
}

type ChatTurn = { role: 'user' | 'assistant'; content: string }

function ChatDrawer({ onClose, rest }: { onClose: () => void; rest: CardsRest }) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = useCallback(async () => {
    const content = draft.trim()
    if (!content || busy) {
      return
    }

    const next: ChatTurn[] = [...turns, { role: 'user', content }]
    setTurns(next)
    setDraft('')
    setBusy(true)
    setError('')
    try {
      const out = await rest<{ reply?: string }>('/cards/chat', {
        method: 'POST',
        body: { messages: next },
        timeoutMs: 120_000
      })
      setTurns([...next, { role: 'assistant', content: out.reply || '(пустой ответ)' }])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [busy, draft, rest, turns])

  return (
    <>
      <div onClick={onClose} style={OVERLAY_STYLE} />
      <aside style={{ ...DRAWER_STYLE, width: 'min(520px, 94vw)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <strong style={{ flex: 1 }}>Чат-аналитик отчёта</strong>
          <button className="ms-btn" onClick={onClose} type="button">
            Закрыть
          </button>
        </div>
        <p className="ms-muted" style={{ padding: '0 14px', margin: 0 }}>
          Считает только из данных МоегоСклада. Попросите построить отчёт за
          нужные месяцы — если цифр не хватает, он скажет каких; пришлите их
          сообщением, и он пересчитает.
        </p>
        <div style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {turns.length === 0 ? (
            <p className="ms-muted">
              Например: «Построй такой же отчёт по такой же форме за июль и август».
            </p>
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
            placeholder="Вопрос по отчёту…"
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

function FlowwowBlock({
  section,
  onSelect
}: {
  section?: FlowwowSection
  onSelect?: (product: MarketplaceProduct) => void
}) {
  if (!section) {
    return null
  }

  if (!section.configured) {
    return <p className="ms-muted">{section.note || 'Flowwow не настроен.'}</p>
  }

  if (section.error) {
    return <p className="ms-error">Flowwow: {section.error}</p>
  }

  const products = section.products || []
  return (
    <>
      <p className="ms-muted">
        Магазин «{section.shop?.name || '—'}» ({section.shop?.address || '—'}) · карточек всего:{' '}
        {section.total ?? products.length}
      </p>
      {products.length ? (
        <div style={GRID_STYLE}>
          {products.map(product => (
            <ProductCard key={String(product.product_id ?? product.name)} onSelect={onSelect} product={product} />
          ))}
        </div>
      ) : (
        <p className="ms-muted">Карточек нет.</p>
      )}
    </>
  )
}

function YandexBlock({
  section,
  onSelect
}: {
  section?: YandexSection
  onSelect?: (product: MarketplaceProduct) => void
}) {
  if (!section) {
    return null
  }

  if (!section.configured) {
    return <p className="ms-muted">{section.note || 'Нет доступа — нужен API-токен.'}</p>
  }

  if (section.error) {
    return <p className="ms-error">Яндекс Маркет: {section.error}</p>
  }

  const products = section.products || []
  return (
    <>
      <p className="ms-muted">
        Бизнес «{section.business?.name || '—'}» · показано карточек: {products.length}
        {' · контент-рейтинг из кабинета (0–100)'}
      </p>
      {products.length ? (
        <div style={GRID_STYLE}>
          {products.map(product => (
            <ProductCard key={String(product.offer_id ?? product.name)} onSelect={onSelect} product={product} />
          ))}
        </div>
      ) : (
        <p className="ms-muted">Карточек нет.</p>
      )}
    </>
  )
}

export function CardsPage({ rest }: { rest: CardsRest }) {
  const [data, setData] = useState<CardsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<{ product: MarketplaceProduct; marketplace: string } | null>(null)
  const [chatOpen, setChatOpen] = useState(false)

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

  return (
    <div className="ms-page ms-cards-page">
      <div className="ms-page-head">
        <h1>Карточки</h1>
        <button className="ms-btn" onClick={() => setChatOpen(true)} type="button">
          Чат-аналитик
        </button>
        <button className="ms-btn" disabled={loading} onClick={() => void load(true)} type="button">
          {loading ? 'Обновляем…' : 'Обновить'}
        </button>
      </div>
      <p className="ms-muted">
        Карточки товаров на маркетплейсах. Дальше здесь появится создание карточки из букета
        МоегоСклада и автопубликация на площадки.
      </p>
      {error ? <p className="ms-error">{error}</p> : null}
      <section className="ms-card-section">
        <h2>Flowwow</h2>
        {loading && !data ? (
          <p className="ms-muted">Загружаем…</p>
        ) : (
          <FlowwowBlock
            onSelect={product => setSelected({ product, marketplace: 'Flowwow' })}
            section={data?.flowwow}
          />
        )}
      </section>
      <section className="ms-card-section">
        <h2>Яндекс Маркет</h2>
        {loading && !data ? (
          <p className="ms-muted">Загружаем…</p>
        ) : (
          <YandexBlock
            onSelect={product => setSelected({ product, marketplace: 'Яндекс Маркет' })}
            section={data?.yandex}
          />
        )}
      </section>
      {selected ? <ProductDrawer item={selected} onClose={() => setSelected(null)} /> : null}
      {chatOpen ? <ChatDrawer onClose={() => setChatOpen(false)} rest={rest} /> : null}
    </div>
  )
}
