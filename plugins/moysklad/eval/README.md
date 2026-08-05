# MoySklad golden clients (eval)

Fixture for human comparison of **AI summary + sales recommendation** vs DeepSeek
(and other models). Goal: enough factual signal (orders, channels, seasonality,
tags) that hallucinations are obvious.

## Files

| File | Purpose |
|------|---------|
| `golden_clients_v1.json` | ~20 rich counterparties + order history |
| `../scripts/build_golden_clients.py` | Rebuild fixture from live MoySklad API |

## Rebuild

Requires `MOYSKLAD_API_TOKEN` (env or `~/.hermes/.env`). Never commit tokens.

```bash
# from repo root
python plugins/moysklad/scripts/build_golden_clients.py --limit 20 --max-orders 8000
```

Selection: counterparties with ≥3 orders; **excludes** marketplace aggregates
and placeholders (Яндекс Маркет/Еда, Озон, FlowWow buckets, «без номера»,
«не использовать», …). Ranked by personal signal (contacts/tags/attrs), then
order count (capped), distinct channels, seasonal months (Feb/Mar/Sep/Dec/Jan),
then optional line-item snippets for top orders.

## Human scoring (Саша / stakeholder)

For each client in the fixture:

1. Feed the **facts JSON** (client + orders only — no invented fields) into the
   model under test (Hermes guarded prompt vs DeepSeek baseline).
2. Score independently:
   - **Summary fidelity** (0–2): invents phone/VIP/orders? cites real dates/sums?
   - **Recommendation usefulness** (0–2): contact window + check size grounded
     in history? flower-shop appropriate?
   - **Hallucination flag**: yes/no + short note (e.g. “invented Telegram”).

Prompt contract lives in `plugins/moysklad/client_card.py` (`_AI_SYSTEM` +
`generate_ai_for_detail`). Dashboard: open client card → «Обновить AI».

## PII

Fixture may include phones/emails for private Iris shop eval fidelity. Do **not**
commit API tokens. Before making the repo public, re-export with redaction or
hash phones.
