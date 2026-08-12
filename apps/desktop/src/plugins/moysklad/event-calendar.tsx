import { useMemo, useState } from 'react'

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const LEAD_OPTIONS = [0, 3, 5, 7, 14] as const

export function toIsoDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function parseIsoDate(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) {
    return null
  }
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

function addMonths(year: number, month: number, delta: number): { year: number; month: number } {
  const d = new Date(year, month + delta, 1)
  return { year: d.getFullYear(), month: d.getMonth() }
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate()
}

function mondayOffset(year: number, month: number): number {
  const dow = new Date(year, month, 1).getDay()
  return dow === 0 ? 6 : dow - 1
}

export function formatRuRange(from: string | null, to: string | null): string {
  if (!from && !to) {
    return ''
  }
  const fmt = (iso: string) => {
    const d = parseIsoDate(iso)
    if (!d) {
      return iso
    }
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
  }
  const a = from || to || ''
  const b = to || from || ''
  if (a === b) {
    return fmt(a)
  }
  return `${fmt(a)} — ${fmt(b)}`
}

export interface EventCalendarPickerProps {
  dateFrom: string | null
  dateTo: string | null
  leadDays: number
  onRangeChange: (from: string | null, to: string | null) => void
  onLeadDaysChange: (days: number) => void
}

export function EventCalendarPicker({
  dateFrom,
  dateTo,
  leadDays,
  onRangeChange,
  onLeadDaysChange
}: EventCalendarPickerProps) {
  const today = useMemo(() => toIsoDate(new Date()), [])
  const initial = parseIsoDate(dateFrom || dateTo || today) || new Date()
  const [viewYear, setViewYear] = useState(initial.getFullYear())
  const [viewMonth, setViewMonth] = useState(initial.getMonth())
  const [anchor, setAnchor] = useState<string | null>(null)

  const monthLabel = useMemo(
    () =>
      new Date(viewYear, viewMonth, 1).toLocaleDateString('ru-RU', {
        month: 'long',
        year: 'numeric'
      }),
    [viewMonth, viewYear]
  )

  const cells = useMemo(() => {
    const offset = mondayOffset(viewYear, viewMonth)
    const dim = daysInMonth(viewYear, viewMonth)
    const out: Array<{ iso: string; day: number; inMonth: boolean }> = []
    const prev = addMonths(viewYear, viewMonth, -1)
    const prevDim = daysInMonth(prev.year, prev.month)
    for (let i = offset - 1; i >= 0; i -= 1) {
      const day = prevDim - i
      const d = new Date(prev.year, prev.month, day)
      out.push({ iso: toIsoDate(d), day, inMonth: false })
    }
    for (let day = 1; day <= dim; day += 1) {
      const d = new Date(viewYear, viewMonth, day)
      out.push({ iso: toIsoDate(d), day, inMonth: true })
    }
    const next = addMonths(viewYear, viewMonth, 1)
    let day = 1
    while (out.length % 7 !== 0) {
      const d = new Date(next.year, next.month, day)
      out.push({ iso: toIsoDate(d), day, inMonth: false })
      day += 1
    }
    return out
  }, [viewMonth, viewYear])

  const rangeStart = dateFrom || dateTo
  const rangeEnd = dateTo || dateFrom

  const isInRange = (iso: string) => {
    if (!rangeStart || !rangeEnd) {
      return false
    }
    return iso >= rangeStart && iso <= rangeEnd
  }

  const isRangeEdge = (iso: string) => iso === rangeStart || iso === rangeEnd

  const onDayClick = (iso: string) => {
    if (!anchor || (rangeStart && rangeEnd && rangeStart !== rangeEnd)) {
      setAnchor(iso)
      onRangeChange(iso, iso)
      return
    }
    if (anchor === iso) {
      return
    }
    const from = anchor < iso ? anchor : iso
    const to = anchor < iso ? iso : anchor
    setAnchor(null)
    onRangeChange(from, to)
  }

  const clear = () => {
    setAnchor(null)
    onRangeChange(null, null)
  }

  const summary = formatRuRange(dateFrom, dateTo)

  return (
    <div className="ms-event-calendar">
      <div className="ms-event-calendar-head">
        <button
          className="ms-btn ms-btn-ghost ms-cal-nav"
          onClick={() => {
            const n = addMonths(viewYear, viewMonth, -1)
            setViewYear(n.year)
            setViewMonth(n.month)
          }}
          type="button"
        >
          ‹
        </button>
        <span className="ms-event-calendar-title">{monthLabel}</span>
        <button
          className="ms-btn ms-btn-ghost ms-cal-nav"
          onClick={() => {
            const n = addMonths(viewYear, viewMonth, 1)
            setViewYear(n.year)
            setViewMonth(n.month)
          }}
          type="button"
        >
          ›
        </button>
      </div>
      <div className="ms-event-calendar-weekdays">
        {WEEKDAYS.map(w => (
          <span className="ms-event-calendar-weekday" key={w}>
            {w}
          </span>
        ))}
      </div>
      <div className="ms-event-calendar-grid">
        {cells.map(cell => {
          const selected = isInRange(cell.iso)
          const edge = isRangeEdge(cell.iso)
          const isToday = cell.iso === today
          const classes = [
            'ms-event-calendar-day',
            !cell.inMonth ? 'is-outside' : '',
            selected ? 'is-in-range' : '',
            edge ? 'is-edge' : '',
            isToday ? 'is-today' : ''
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <button
              className={classes}
              key={`${cell.iso}-${cell.inMonth ? 'in' : 'out'}`}
              onClick={() => onDayClick(cell.iso)}
              type="button"
            >
              {cell.day}
            </button>
          )
        })}
      </div>
      <div className="ms-event-calendar-foot">
        <span className="ms-muted">
          {summary
            ? leadDays > 0
              ? `Событие: ${summary} · связаться за ${leadDays} дн. до`
              : `Событие: ${summary}`
            : 'Выберите день или диапазон дат события'}
        </span>
        {summary ? (
          <button className="ms-link-btn" onClick={clear} type="button">
            Сбросить
          </button>
        ) : null}
      </div>
      <div className="ms-event-calendar-lead">
        <span className="ms-filter-label">Связаться за N дней до события</span>
        <div className="ms-chips">
          {LEAD_OPTIONS.map(n => (
            <button
              className={`ms-chip${leadDays === n ? ' is-active' : ''}`}
              key={n}
              onClick={() => {
                onLeadDaysChange(n)
              }}
              type="button"
            >
              {n === 0 ? 'Выкл' : `${n} дн`}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
