(function () {
  "use strict";
  // MoySklad Clients dashboard plugin — Iris CRM /clients-style UI
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const hooks = SDK.hooks;
  const h = React.createElement;
  const useState = hooks.useState;
  const useEffect = hooks.useEffect;
  const useCallback = hooks.useCallback;
  const useMemo = hooks.useMemo;
  const useRef =
    hooks.useRef ||
    function (v) {
      const bag = { current: v };
      return bag;
    };

  const API = "/api/plugins/moysklad";
  const PAGE_SIZE = 50;
  var STAGE_CHIPS = [
    { id: "failed", label: "Не состоялся" },
    { id: "customer", label: "Покупатель" },
    { id: "no_orders", label: "Нет заказов" },
  ];


  function pickOutreachMessage(data) {
    if (!data || typeof data !== "object") return "";
    var nested = data.result && typeof data.result === "object" ? data.result : null;
    var sanity = data.sanity && typeof data.sanity === "object" ? data.sanity : null;
    var candidates = [
      data.message,
      data.text,
      data.offer,
      data.draft,
      sanity && sanity.revised_text,
      nested && nested.message,
      nested && nested.text,
    ];
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i];
      if (typeof c === "string" && c.trim()) return c;
    }
    return "";
  }

  function api(path, options) {
    return SDK.fetchJSON(API + path, options);
  }

  function money(n) {
    const v = Number(n) || 0;
    try {
      return new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      }).format(v);
    } catch (_) {
      return Math.round(v) + " ₽";
    }
  }

  function FilterTabs({ salesFilter, counts, onChange, disabled }) {
    const tabs = [
      { id: "all", label: "Все", count: counts && counts.total },
      { id: "marketplace", label: "Маркетплейс", count: counts && counts.marketplace },
      { id: "direct", label: "Прямые", count: counts && counts.direct },
    ];
    return h(
      "div",
      { className: "ms-filter-tabs", role: "tablist" },
      tabs.map(function (tab) {
        return h(
          "button",
          {
            key: tab.id,
            type: "button",
            role: "tab",
            className: "ms-filter-tab" + (salesFilter === tab.id ? " is-active" : ""),
            disabled: disabled,
            onClick: function () {
              onChange(tab.id);
            },
          },
          tab.label,
          tab.count != null
            ? h("span", { className: "ms-tab-count" }, String(tab.count))
            : null,
        );
      }),
    );
  }

  function GroupCloud({ options, groupsTotal, selected, onSelect, onClear }) {
    if (!options || !options.length) {
      return h(
        "section",
        { className: "ms-group-cloud" },
        h(
          "div",
          { className: "ms-group-cloud-head" },
          h("span", { className: "ms-group-cloud-title" }, "Группы (МойСклад)"),
          h("span", { className: "ms-muted" }, "Нет групп в текущем фильтре"),
        ),
      );
    }
    return h(
      "section",
      { className: "ms-group-cloud", "aria-label": "Фильтр по группам" },
      h(
        "div",
        { className: "ms-group-cloud-head" },
        h("span", { className: "ms-group-cloud-title" }, "Группы"),
        h(
          "span",
          { className: "ms-legend", "aria-hidden": "true" },
          h("span", { className: "leg-ms" }, "● МС"),
          h("span", { className: "leg-ai" }, "● AI"),
        ),
        h(
          "span",
          { className: "ms-group-cloud-hint" },
          "МойСклад + AI · " + options.length,
        ),
        selected
          ? h(
              "button",
              { type: "button", className: "ms-group-clear", onClick: onClear },
              "Сбросить",
            )
          : null,
      ),
      h(
        "div",
        { className: "ms-group-chips" },
        h(
          "button",
          {
            type: "button",
            className: "ms-group-chip" + (!selected ? " is-active" : ""),
            style: { "--chip-hue": 210 },
            onClick: onClear,
          },
          "Все",
          h("span", { className: "ms-group-chip-count" }, String(groupsTotal || 0)),
        ),
        options.map(function (item) {
          const active = selected && selected.toLowerCase() === String(item.name).toLowerCase();
          const src = String(item.source || "ms");
          const srcClass =
            src === "ai" ? " is-ai" : src === "both" ? " is-both" : " is-ms";
          const srcLabel = src === "ai" ? "AI" : src === "both" ? "МС+AI" : "МС";
          const titleBits = [item.count + " клиентов", "источник: " + srcLabel];
          if (item.ms_count) titleBits.push("МС " + item.ms_count);
          if (item.ai_count) titleBits.push("AI " + item.ai_count);
          return h(
            "button",
            {
              key: item.name + ":" + src,
              type: "button",
              className: "ms-group-chip" + srcClass + (active ? " is-active" : ""),
              style: { "--chip-hue": item.hue || (src === "ai" ? 280 : 200) },
              title: titleBits.join(" · "),
              onClick: function () {
                onSelect(item.name);
              },
            },
            h("span", { className: "ms-chip-src ms-chip-src-" + src }, srcLabel),
            item.name,
            h("span", { className: "ms-group-chip-count" }, String(item.count)),
          );
        }),
      ),
    );
  }

  function cell(value) {
    if (value == null || value === "") {
      return h("span", { className: "ms-muted" }, "—");
    }
    return String(value);
  }

  function aiCell(value, isAi) {
    if (value == null || value === "") {
      return h("span", { className: "ms-muted" }, "—");
    }
    if (!isAi) return String(value);
    return h(
      "span",
      { className: "ms-ai-cell", title: "Заполнено AI" },
      h("span", { className: "ms-ai-dot", "aria-hidden": "true" }),
      h("span", { className: "ms-ai-value" }, String(value)),
    );
  }

  var AI_COLUMN_KEYS = {
    state: "state",
    groups: "groups",
    role: "role",
    sex: "sex",
    tg_nick: "tg_nick",
    company_type: "company_type",
  };

  function formatDate(value) {
    if (!value) return "";
    var s = String(value);
    // MoySklad moments look like 2024-05-01 12:30:00.000 — show date (+ time if present)
    if (s.length >= 10) return s.slice(0, 16).replace("T", " ");
    return s;
  }

  var CLIENT_COLUMNS = [
    { key: "name", label: "Наименование" },
    { key: "phone", label: "Телефон" },
    { key: "state", label: "Статус" },
    { key: "sales_type", label: "Тип канала продаж" },
    { key: "channel", label: "Канал продаж", from: function (c) {
      if (c.channels && c.channels.length) return c.channels.join(", ");
      return c.channel || "";
    } },
    { key: "avg_check", label: "Средний чек", from: function (c) { return money(c.avg_check); } },
    { key: "last_order_at", label: "Дата последнего заказа", from: function (c) {
      return formatDate(c.last_order_at);
    } },
    { key: "order_count", label: "Всего заказов", from: function (c) {
      return String(c.order_count || 0);
    } },
    { key: "bonus_points", label: "Баллы начисленные" },
    { key: "groups", label: "Группы", from: function (c) {
      var ms = String(c.ms_groups || "").trim();
      var ai = (c.ai_groups || []).filter(Boolean);
      if (ms && ai.length) return "МС: " + ms + " · AI: " + ai.join(", ");
      if (ai.length) return "AI: " + ai.join(", ");
      if (c.groups) return c.groups;
      return (c.tags || []).join(", ");
    } },
    { key: "role", label: "Заказчик или получатель" },
    { key: "actual_address", label: "Фактический адрес" },
    { key: "actual_address_comment", label: "Фактический адрес (Комментарий)" },
    { key: "company_type", label: "Тип контрагента" },
    { key: "sex", label: "Пол" },
    { key: "email", label: "E-mail" },
    { key: "tg_nick", label: "ТГ ник" },
    { key: "tg_conversation", label: "TG conversation" },
  ];

  function TagPills({ items, className }) {
    if (!items || !items.length) return null;
    return h(
      "div",
      { className: className || "ms-tag-row" },
      items.map(function (t) {
        return h("span", { key: t, className: "ms-tag-pill" }, t);
      }),
    );
  }

  function ClientsTable({ clients, onOpenClient }) {
    if (!clients || !clients.length) {
      return h("p", { className: "ms-muted" }, "Клиенты не найдены.");
    }
    return h(
      "div",
      { className: "ms-table-wrap" },
      h(
        "table",
        { className: "ms-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            CLIENT_COLUMNS.map(function (col) {
              return h("th", { key: col.key }, col.label);
            }),
          ),
        ),
        h(
          "tbody",
          null,
          clients.map(function (c) {
            return h(
              "tr",
              { key: c.id },
              CLIENT_COLUMNS.map(function (col) {
                var raw = col.from ? col.from(c) : c[col.key];
                if (col.key === "name") {
                  return h(
                    "td",
                    { key: col.key },
                    h(
                      "button",
                      {
                        type: "button",
                        className: "ms-link-btn",
                        title: "Открыть карточку клиента",
                        onClick: function () {
                          if (onOpenClient) onOpenClient(c);
                        },
                      },
                      String(raw || "—"),
                    ),
                  );
                }
                var aiKey = AI_COLUMN_KEYS[col.key];
                var isAi = Boolean(
                  aiKey && (c.ai_fields || []).indexOf(aiKey) >= 0 && raw,
                );
                return h(
                  "td",
                  { key: col.key, className: isAi ? "ms-ai-added" : undefined },
                  aiCell(raw, isAi),
                );
              }),
            );
          }),
        ),
      ),
    );
  }

  function OrdersList({ orders, limit }) {
    var list = orders || [];
    var shown = limit != null ? list.slice(0, limit) : list;
    if (!shown.length) {
      return h("p", { className: "ms-muted" }, "Заказов в кэше нет.");
    }
    return h(
      "div",
      { className: "ms-orders-list" },
      shown.map(function (o, idx) {
        return h(
          "div",
          { key: (o.id || "") + "-" + idx, className: "ms-order-row" },
          h(
            "div",
            { className: "ms-order-main" },
            h("strong", null, o.name || o.id || "Заказ"),
            h(
              "span",
              { className: "ms-muted" },
              formatDate(o.date) + " · " + money(o.sum) + (o.channel ? " · " + o.channel : ""),
            ),
          ),
          o.product_snippet
            ? h("div", { className: "ms-order-snippet" }, o.product_snippet)
            : null,
        );
      }),
    );
  }

  function ClientCardModal({ open, clientId, onClose }) {
    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [aiLoading, setAiLoading] = useState(false);
    const [ordersOpen, setOrdersOpen] = useState(true);
    const [note, setNote] = useState("");
    const [tgCheck, setTgCheck] = useState(null);

    useEffect(
      function () {
        setTgCheck(null);
      },
      [clientId],
    );

    function runTgCheck() {
      if (!clientId) return;
      setTgCheck({ busy: true });
      api("/clients/" + encodeURIComponent(clientId) + "/telegram-check", {
        method: "POST",
      })
        .then(function (data) {
          setTgCheck(Object.assign({}, data, { busy: false }));
        })
        .catch(function (err) {
          setTgCheck({
            busy: false,
            checked: false,
            detail: String((err && err.message) || err),
          });
        });
    }

    useEffect(
      function () {
        if (!open || !clientId) return;
        setLoading(true);
        setError(null);
        setDetail(null);
        setOrdersOpen(false);
        setNote("");
        api("/clients/" + encodeURIComponent(clientId))
          .then(function (payload) {
            setDetail(payload);
            var src = payload && payload.ai && payload.ai.source;
            if (src !== "llm") {
              setAiLoading(true);
              api("/clients/" + encodeURIComponent(clientId) + "/ai", { method: "POST" })
                .then(function (aiPayload) {
                  setDetail(function (prev) {
                    if (!prev) return prev;
                    return Object.assign({}, prev, { ai: aiPayload.ai || aiPayload });
                  });
                })
                .catch(function (err) {
                  setError(String((err && err.message) || err));
                })
                .finally(function () {
                  setAiLoading(false);
                });
            }
          })
          .catch(function (err) {
            setError(String((err && err.message) || err));
          })
          .finally(function () {
            setLoading(false);
          });
      },
      [open, clientId],
    );

    // Keep «TG conversation» live while the card is open — inbound replies
    // appear without pressing Sync (30s cadence, no AI regen).
    useEffect(
      function () {
        if (!open || !clientId) return;
        var timer = setInterval(function () {
          api(
            "/clients/" + encodeURIComponent(clientId) + "/conversation/sync?refresh_ai=false",
            { method: "POST" },
          )
            .then(function (data) {
              if (data && data.conversation) {
                setDetail(function (prev) {
                  if (!prev) return prev;
                  return Object.assign({}, prev, { conversation: data.conversation });
                });
              }
            })
            .catch(function () {});
        }, 30000);
        return function () {
          clearInterval(timer);
        };
      },
      [open, clientId],
    );

    const refreshAi = useCallback(
      function () {
        if (!clientId) return;
        setAiLoading(true);
        setError(null);
        api("/clients/" + encodeURIComponent(clientId) + "/ai", { method: "POST" })
          .then(function (payload) {
            setDetail(function (prev) {
              if (!prev) return prev;
              return Object.assign({}, prev, { ai: payload.ai || payload });
            });
          })
          .catch(function (err) {
            setError(String((err && err.message) || err));
          })
          .finally(function () {
            setAiLoading(false);
          });
      },
      [clientId],
    );

    if (!open) return null;

    var client = (detail && detail.client) || {};
    var stats = (detail && detail.stats) || {};
    var orders = (detail && detail.orders) || [];
    var ai = (detail && detail.ai) || {};
    var msg = (detail && detail.messaging) || {};
    var conversation = (detail && detail.conversation) || null;
    var buckets = client.tag_buckets || {};
    var name = client.name || "Клиент";

    function openUrl(url) {
      if (!url) return;
      try {
        window.open(url, "_blank", "noopener,noreferrer");
      } catch (_) {}
    }

    function sendAndRecord(channel) {
      var text = String(note || "").trim();
      if (!text) {
        setError("Введите текст — он уйдёт в Telegram / историю.");
        return;
      }
      setError(null);
      api("/clients/" + encodeURIComponent(clientId) + "/conversation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text,
          direction: "outbound",
          channel: channel,
          source: "client_card_send",
          open_deep_link: true,
        }),
      })
        .then(function (data) {
          if (data.conversation) {
            setDetail(function (prev) {
              if (!prev) return prev;
              return Object.assign({}, prev, { conversation: data.conversation });
            });
          }
          if (channel === "telegram" && data.delivery && data.delivery.ok) {
            setNote("");
            return;
          }
          if (
            channel === "telegram" &&
            data.delivery &&
            data.delivery.ok === false &&
            !(String(data.delivery.error || "").indexOf("skipped") >= 0)
          ) {
            setError(
              "Telegram Bot: " +
                (data.delivery.detail || data.delivery.error || "не отправлено") +
                ". Откроется deep-link, если есть."
            );
          }
          if (data.deep_link) openUrl(data.deep_link);
          setNote("");
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        });
    }

    return h(
      "div",
      {
        className: "ms-modal-backdrop",
        onClick: function (e) {
          if (e.target === e.currentTarget) onClose();
        },
      },
      h(
        "div",
        {
          className: "ms-modal ms-client-card",
          role: "dialog",
          "aria-modal": "true",
          "aria-label": "Карточка клиента",
        },
        h(
          "div",
          { className: "ms-card-head" },
          h("h3", null, "Заказы · " + name),
          h(
            "button",
            { type: "button", className: "ms-btn", onClick: onClose },
            "Закрыть",
          ),
        ),
        error ? h("div", { className: "ms-error" }, error) : null,
        loading
          ? h("p", { className: "ms-muted" }, "Загрузка карточки…")
          : h(
              "div",
              { className: "ms-card-body" },
              h(
                "section",
                { className: "ms-card-section ms-card-hero" },
                h("div", { className: "ms-card-name" }, name),
                h(
                  "div",
                  { className: "ms-muted" },
                  (client.role || "—") +
                    (client.sex ? " · " + client.sex : "") +
                    (client.state ? " · " + client.state : ""),
                ),
                h(TagPills, {
                  items: [].concat(
                    buckets.marketplace || [],
                    client.channels || [],
                  ),
                  className: "ms-tag-row ms-tag-channel",
                }),
                h(TagPills, {
                  items: [].concat(buckets.loyalty || [], buckets.other || []),
                }),
                h(TagPills, {
                  items: buckets.events || [],
                  className: "ms-tag-row ms-tag-event",
                }),
              ),
              h(
                "section",
                { className: "ms-card-section" },
                h("h4", null, "Контакты"),
                h(
                  "div",
                  { className: "ms-kv-grid" },
                  h("span", { className: "ms-muted" }, "Телефон"),
                  h("span", null, client.phone || "—"),
                  h("span", { className: "ms-muted" }, "Email"),
                  h("span", null, client.email || "—"),
                  h("span", { className: "ms-muted" }, "Telegram"),
                  h("span", null, client.tg_nick || "—"),
                  h("span", { className: "ms-muted" }, "Тип"),
                  h("span", null, client.company_type || "—"),
                  h("span", { className: "ms-muted" }, "Статус"),
                  h("span", null, client.state || "—"),
                  h("span", { className: "ms-muted" }, "Осн. канал"),
                  h(
                    "span",
                    null,
                    client.primary_channel || msg.primary_channel || "—",
                  ),
                  h("span", { className: "ms-muted" }, "Есть в TG"),
                  h(
                    "span",
                    null,
                    tgCheck && tgCheck.busy
                      ? "проверяем…"
                      : tgCheck && tgCheck.checked
                        ? tgCheck.exists
                          ? "да" +
                            (tgCheck.tg_nick
                              ? " · @" + String(tgCheck.tg_nick).replace(/^@/, "")
                              : "") +
                            (tgCheck.via ? " (" + tgCheck.via + ")" : "")
                          : "не подтверждён — " +
                            (tgCheck.detail || "номер может быть скрыт")
                        : tgCheck && tgCheck.detail
                          ? tgCheck.detail
                          : h(
                              "button",
                              {
                                className: "ms-link-btn",
                                type: "button",
                                title:
                                  "Резолв @ника / телефона через личный Telegram (MTProto), fallback — Business bot",
                                onClick: runTgCheck,
                              },
                              "Проверить",
                            ),
                  ),
                ),
              ),
              h(
                "section",
                { className: "ms-card-section" },
                h(ConversationThread, {
                  conversation: conversation,
                  title: "TG conversation",
                }),
              ),
              h(
                "section",
                { className: "ms-card-section" },
                h("h4", null, "Статистика"),
                h(
                  "div",
                  { className: "ms-stats-grid" },
                  h("div", null, h("div", { className: "ms-stat-val" }, money(stats.avg_check)), h("div", { className: "ms-muted" }, "Средний чек")),
                  h("div", null, h("div", { className: "ms-stat-val" }, String(stats.order_count || 0)), h("div", { className: "ms-muted" }, "Заказов")),
                  h("div", null, h("div", { className: "ms-stat-val" }, stats.vip ? "да" : "нет"), h("div", { className: "ms-muted" }, "ВИП")),
                  h(
                    "div",
                    null,
                    h(
                      "div",
                      { className: "ms-stat-val" },
                      stats.loyalty_points != null ? String(stats.loyalty_points) : "—",
                    ),
                    h("div", { className: "ms-muted" }, "Лояльность"),
                  ),
                ),
                stats.last_order
                  ? h(
                      "div",
                      { className: "ms-last-order" },
                      h("strong", null, "Последний заказ"),
                      h(
                        "div",
                        { className: "ms-muted" },
                        formatDate(stats.last_order.date) +
                          " · " +
                          money(stats.last_order.sum) +
                          (stats.last_order.channel ? " · " + stats.last_order.channel : ""),
                      ),
                      stats.last_order.product_snippet
                        ? h("div", null, stats.last_order.product_snippet)
                        : null,
                    )
                  : null,
              ),
              h(
                "section",
                { className: "ms-card-section" },
                h(
                  "button",
                  {
                    type: "button",
                    className: "ms-section-toggle",
                    onClick: function () {
                      setOrdersOpen(!ordersOpen);
                    },
                  },
                  "Все заказы (" + orders.length + ") " + (ordersOpen ? "▾" : "▸"),
                ),
                h(OrdersList, {
                  orders: orders,
                  limit: ordersOpen ? null : 5,
                }),
              ),
              h(
                "section",
                { className: "ms-card-section ms-ai-block" },
                h(
                  "div",
                  { className: "ms-card-head" },
                  h("h4", null, "Саммари AI"),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      disabled: aiLoading,
                      onClick: refreshAi,
                    },
                    aiLoading ? "Генерация…" : "Обновить AI",
                  ),
                ),
                ai.data_thin
                  ? h("p", { className: "ms-muted" }, "Данных мало — выводы осторожные.")
                  : null,
                h("p", { className: "ms-ai-label" }, "История и профиль"),
                h("p", null, ai.history_profile || "—"),
                h("p", { className: "ms-ai-label" }, "Повод и intent покупки"),
                h("p", null, ai.occasion_intent || "—"),
                h("h4", null, "Рекомендация AI"),
                h("p", null, ai.recommendation || "—"),
                h(
                  "p",
                  { className: "ms-muted" },
                  "Источник: " + (ai.source || "heuristic"),
                ),
              ),
              h(
                "section",
                { className: "ms-card-section" },
                h("h4", null, "Быстрые действия"),
                h(
                  "div",
                  { className: "ms-quick-actions" },
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      onClick: function () {
                        setNote(
                          "Напоминание: связаться с " +
                            name +
                            " (~5 дней до повода). Чек ≈ " +
                            money(stats.avg_check),
                        );
                      },
                    },
                    "Напоминание",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn ms-btn-primary",
                      disabled: !String(note || "").trim(),
                      onClick: function () {
                        sendAndRecord("whatsapp");
                      },
                    },
                    "WhatsApp → история",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn ms-btn-primary",
                      disabled: !String(note || "").trim(),
                      onClick: function () {
                        sendAndRecord("telegram");
                      },
                    },
                    "Telegram → история",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      onClick: function () {
                        setOrdersOpen(true);
                        setNote(
                          "События: " +
                            ((buckets.events || []).join(", ") || "нет тегов событий"),
                        );
                      },
                    },
                    "События",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn ms-btn-primary",
                      onClick: function () {
                        var sales = "all";
                        var st = String(client.sales_type || "").toLowerCase();
                        if (st.indexOf("маркет") >= 0) sales = "marketplace";
                        else if (st.indexOf("прям") >= 0) sales = "direct";
                        var ch = "telegram";
                        var primary = String(
                          msg.primary_channel || client.primary_channel || "",
                        ).toLowerCase();
                        if (primary.indexOf("whatsapp") >= 0) ch = "whatsapp";
                        try {
                          sessionStorage.setItem(
                            "moysklad.draftPrefill",
                            JSON.stringify({
                              clientId: clientId,
                              channel: ch,
                              salesFilter: sales,
                            }),
                          );
                        } catch (_) {}
                        try {
                          var url = new URL(window.location.href);
                          url.searchParams.set("view", "campaigns");
                          url.searchParams.set("client_id", clientId);
                          window.location.assign(url.pathname + url.search);
                        } catch (_) {
                          onClose();
                        }
                      },
                    },
                    "Черновик рассылки",
                  ),
                ),
                note ? h("p", { className: "ms-note" }, note) : null,
                h("p", { className: "ms-muted" }, msg.hint || ""),
              ),
            ),
      ),
    );
  }

  function AssignModal({ open, loading, data, error, onClose, onPush, onRecalcPreview, onRecalcApply, onGroupsEdit }) {
    if (!open) return null;
    const list = (data && data.assignments) || [];
    const isRecalc = !!(data && data.recalculate);
    const groupsText = isRecalc
      ? ((data.groupsText != null
          ? data.groupsText
          : (data.groups || []).join("\n")))
      : "";
    return h(
      "div",
      {
        className: "ms-modal-backdrop",
        onClick: function (e) {
          if (e.target === e.currentTarget) onClose();
        },
      },
      h(
        "div",
        { className: "ms-modal", role: "dialog", "aria-modal": "true" },
        h("h3", null, isRecalc ? "Пересчитать группы" : "Предложенные группы"),
        error ? h("div", { className: "ms-error" }, error) : null,
        loading
          ? h("p", { className: "ms-muted" }, isRecalc ? "Считаем taxonomy…" : "Считаем эвристики…")
          : isRecalc
            ? h(
                "p",
                { className: "ms-muted" },
                "Источник: " +
                  ((data && data.source) || "—") +
                  (data && data.changed != null
                    ? " · изменится " + data.changed + " из " + (data.total || 0)
                    : ""),
              )
            : h(
                "p",
                { className: "ms-muted" },
                "Изменится: " +
                  ((data && data.changed) || 0) +
                  " из " +
                  ((data && data.total) || 0),
              ),
        isRecalc
          ? h("textarea", {
              rows: 12,
              style: { width: "100%" },
              value: groupsText,
              disabled: loading,
              onChange: function (e) {
                if (onGroupsEdit) onGroupsEdit(e.target.value);
              },
            })
          : h(
              "div",
              { className: "ms-modal-list" },
              list.slice(0, 80).map(function (item) {
                return h(
                  "div",
                  { key: item.id, className: "ms-modal-item" },
                  h("strong", null, item.name || item.id),
                  h(
                    "div",
                    { className: "ms-muted" },
                    "добавить: " + ((item.added || []).join(", ") || "—"),
                  ),
                );
              }),
              list.length > 80
                ? h("div", { className: "ms-muted" }, "…и ещё " + (list.length - 80))
                : null,
            ),
        h(
          "div",
          { className: "ms-modal-actions" },
          h("button", { type: "button", className: "ms-btn", onClick: onClose }, "Закрыть"),
          isRecalc
            ? h(
                "button",
                {
                  type: "button",
                  className: "ms-btn",
                  disabled: loading || !groupsText.trim(),
                  onClick: onRecalcPreview,
                },
                "Превью",
              )
            : null,
          h(
            "button",
            {
              type: "button",
              className: "ms-btn ms-btn-primary",
              disabled: loading || (isRecalc ? !groupsText.trim() : !list.length),
              onClick: isRecalc ? onRecalcApply : onPush,
            },
            "Записать в МойСклад",
          ),
        ),
      ),
    );
  }

  function ClientsPage() {
    const [salesFilter, setSalesFilter] = useState("all");
    const [entityType, setEntityType] = useState("all");
    const [loyaltyOnly, setLoyaltyOnly] = useState(false);
    const [group, setGroup] = useState("");
    const [qInput, setQInput] = useState("");
    const [q, setQ] = useState("");
    const [clients, setClients] = useState([]);
    const [counts, setCounts] = useState(null);
    const [matchedTotal, setMatchedTotal] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const [nextOffset, setNextOffset] = useState(0);
    const [groupOptions, setGroupOptions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState(null);
    const [modalOpen, setModalOpen] = useState(false);
    const [assignLoading, setAssignLoading] = useState(false);
    const [assignData, setAssignData] = useState(null);
    const [assignError, setAssignError] = useState(null);
    const [cardClientId, setCardClientId] = useState(null);
    const [syncedLabel, setSyncedLabel] = useState("");
    const [fromCache, setFromCache] = useState(false);
    const [aiFillLoading, setAiFillLoading] = useState(false);
    const [aiFillStatus, setAiFillStatus] = useState("");
    const [tgImportBusy, setTgImportBusy] = useState(false);
    const [tgImportNote, setTgImportNote] = useState("");
    const loadGen = useRef(0);
    const loadingMoreRef = useRef(false);

    const mergePages = useCallback(function (prev, incoming) {
      const seen = {};
      const out = [];
      (prev || []).concat(incoming || []).forEach(function (row) {
        const id = String((row && row.id) || "").trim();
        if (id) {
          if (seen[id]) return;
          seen[id] = true;
        }
        out.push(row);
      });
      return out;
    }, []);

    const load = useCallback(
      function (opts) {
        const append = !!(opts && opts.append);
        const refresh = !!(opts && opts.refresh);
        const offset = append
          ? opts && opts.offset != null
            ? opts.offset
            : nextOffset
          : 0;
        const gen = append ? loadGen.current : ++loadGen.current;
        if (append) {
          if (loadingMoreRef.current || !hasMore) return;
          loadingMoreRef.current = true;
          setLoadingMore(true);
        } else {
          setLoading(true);
          setError(null);
        }
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          group: group || "",
          q: q || "",
          entity_type: entityType,
          limit: String(PAGE_SIZE),
          offset: String(offset),
        });
        if (loyaltyOnly) params.set("loyalty_only", "true");
        if (refresh) params.set("refresh", "true");
        api("/clients?" + params.toString())
          .then(function (payload) {
            if (gen !== loadGen.current) return;
            const page = (payload && payload.clients) || [];
            setClients(function (prev) {
              return append ? mergePages(prev, page) : page;
            });
            setCounts((payload && payload.counts) || null);
            const total = (payload && payload.matched_total) || 0;
            setMatchedTotal(total);
            const computedNext =
              payload && payload.next_offset != null
                ? payload.next_offset
                : offset + page.length;
            setNextOffset(computedNext);
            setHasMore(
              payload && payload.has_more != null
                ? !!payload.has_more
                : computedNext < total,
            );
            if (!append) {
              setGroupOptions((payload && payload.group_options) || []);
              setFromCache(!!(payload && payload.cached));
              setSyncedLabel(
                (payload && (payload.synced_at_label || payload.synced_at)) || "",
              );
            }
          })
          .catch(function (err) {
            if (gen !== loadGen.current) return;
            setError(String((err && err.message) || err));
            if (!append) setClients([]);
          })
          .finally(function () {
            if (append) {
              loadingMoreRef.current = false;
              setLoadingMore(false);
            } else if (gen === loadGen.current) {
              setLoading(false);
            }
          });
      },
      [salesFilter, entityType, loyaltyOnly, group, q, nextOffset, hasMore, mergePages],
    );

    useEffect(
      function () {
        load({ offset: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [salesFilter, entityType, loyaltyOnly, group, q],
    );

    useEffect(
      function () {
        const t = setTimeout(function () {
          setQ(qInput.trim());
        }, 300);
        return function () {
          clearTimeout(t);
        };
      },
      [qInput],
    );

    const openAssign = useCallback(
      function () {
        setModalOpen(true);
        setAssignLoading(true);
        setAssignError(null);
        setAssignData(null);
        api("/groups/assign", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sales_filter: salesFilter,
            group: group || "",
            q: q || "",
            dry_run: true,
          }),
        })
          .then(function (payload) {
            setAssignData(payload);
          })
          .catch(function (err) {
            setAssignError(String((err && err.message) || err));
          })
          .finally(function () {
            setAssignLoading(false);
          });
      },
      [salesFilter, group, q],
    );

    const pushAssign = useCallback(
      function () {
        if (!assignData || !(assignData.assignments || []).length) return;
        setAssignLoading(true);
        setAssignError(null);
        api("/groups/push", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            assignments: assignData.assignments,
            only_changed: true,
          }),
        })
          .then(function (payload) {
            if (!payload.ok && payload.errors && payload.errors.length) {
              setAssignError(
                "Ошибки: " +
                  payload.errors
                    .slice(0, 3)
                    .map(function (e) {
                      return e.error;
                    })
                    .join("; "),
              );
            } else {
              setModalOpen(false);
              load({ offset: 0, refresh: true });
            }
          })
          .catch(function (err) {
            setAssignError(String((err && err.message) || err));
          })
          .finally(function () {
            setAssignLoading(false);
          });
      },
      [assignData, load],
    );

    function onScroll(e) {
      const el = e.currentTarget;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 160) return;
      if (!hasMore || loading || loadingMoreRef.current) return;
      load({ append: true, offset: nextOffset });
    }

    var cacheHint = syncedLabel
      ? (fromCache ? "из кэша" : "свежая выгрузка") + " · синхр. " + syncedLabel
      : fromCache
        ? "из кэша"
        : "";

    return h(
      "div",
      { className: "ms-clients", "data-selectable-text": "true" },
      h(
        "div",
        { className: "ms-clients-header" },
        h(
          "div",
          null,
          h("h1", { className: "ms-clients-title" }, "Клиенты"),
          cacheHint
            ? h("p", { className: "ms-muted ms-sync-meta" }, cacheHint)
            : null,
        ),
        h(
          "div",
          { className: "ms-clients-actions" },
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: loading,
              onClick: function () {
                load({ offset: 0 });
              },
            },
            "Обновить",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn ms-btn-primary",
              disabled: loading,
              title: "Принудительно скачать данные из МойСклад и обновить кэш",
              onClick: function () {
                load({ offset: 0, refresh: true });
              },
            },
            "Синхронизация",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: loading || tgImportBusy,
              title:
                "Сопоставить telegram_export.json с клиентами → колонка TG conversation",
              onClick: function () {
                setTgImportBusy(true);
                setTgImportNote("Импорт Telegram…");
                setError(null);
                api("/clients/telegram-export/import?force=true", {
                  method: "POST",
                })
                  .then(function (data) {
                    setTgImportNote(
                      "TG → клиенты: чатов " +
                        (data.chats_total || 0) +
                        " · привязано " +
                        (data.matched || 0) +
                        " · сообщений " +
                        (data.imported_messages || 0) +
                        (data.error ? " · " + data.error : ""),
                    );
                    return load({ offset: 0 });
                  })
                  .catch(function (err) {
                    setTgImportNote(
                      "TG импорт: " + ((err && err.message) || String(err)),
                    );
                  })
                  .finally(function () {
                    setTgImportBusy(false);
                  });
              },
            },
            tgImportBusy ? "Импорт TG…" : "Импорт Telegram",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: loading,
              onClick: openAssign,
            },
            "Предложить группы",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: loading,
              title: "LLM предложит новые имена групп",
              onClick: function () {
                setModalOpen(true);
                setAssignLoading(true);
                setAssignError(null);
                setAssignData(null);
                api("/groups/recalculate/propose", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    sales_filter: salesFilter,
                    group: group || "",
                    q: q || "",
                  }),
                })
                  .then(function (payload) {
                    setAssignData({
                      recalculate: true,
                      groups: payload.groups || [],
                      groupsText: (payload.groups || []).join("\n"),
                      source: payload.source,
                      assignments: [],
                      changed: 0,
                      total: 0,
                    });
                  })
                  .catch(function (err) {
                    setAssignError(String((err && err.message) || err));
                  })
                  .finally(function () {
                    setAssignLoading(false);
                  });
              },
            },
            "Пересчитать группы",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: loading || aiFillLoading,
              title: "Заполнить пустые Группы / Статус / Пол / роль / ТГ ник через AI",
              onClick: function () {
                setAiFillLoading(true);
                setAiFillStatus("AI заполняет пустые поля…");
                setError(null);
                api("/clients/ai-fill", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    sales_filter: salesFilter,
                    group: group || "",
                    q: q || "",
                    limit: 40,
                    use_llm: true,
                  }),
                })
                  .then(function (payload) {
                    setAiFillStatus(
                      "✓ AI: обновлено " +
                        (payload.updated || 0) +
                        " клиентов · полей " +
                        (payload.filled_field_count || 0) +
                        (payload.source ? " · " + payload.source : ""),
                    );
                    return load({ offset: 0 });
                  })
                  .catch(function (err) {
                    setError(String((err && err.message) || err));
                    setAiFillStatus("");
                  })
                  .finally(function () {
                    setAiFillLoading(false);
                  });
              },
            },
            aiFillLoading ? "AI заполняет…" : "Заполнить AI",
          ),
        ),
      ),
      h(FilterTabs, {
        salesFilter: salesFilter,
        counts: counts,
        disabled: loading,
        onChange: function (id) {
          setGroup("");
          setSalesFilter(id);
        },
      }),
      h(
        "div",
        { className: "ms-search" },
        h("input", {
          type: "search",
          placeholder: "Поиск по имени / телефону…",
          value: qInput,
          onChange: function (e) {
            setQInput(e.target.value);
          },
        }),
        h(
          "button",
          {
            type: "button",
            className: "ms-chip" + (entityType === "individual" ? " is-active" : ""),
            title: "Только физические лица",
            onClick: function () {
              setEntityType(function (v) {
                return v === "individual" ? "all" : "individual";
              });
            },
          },
          "Физ. лица",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-chip" + (entityType === "legal" ? " is-active" : ""),
            title: "Только юридические лица",
            onClick: function () {
              setEntityType(function (v) {
                return v === "legal" ? "all" : "legal";
              });
            },
          },
          "Юр. лица",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-chip" + (entityType === "entrepreneur" ? " is-active" : ""),
            title: "Только индивидуальные предприниматели",
            onClick: function () {
              setEntityType(function (v) {
                return v === "entrepreneur" ? "all" : "entrepreneur";
              });
            },
          },
          "ИП",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-chip" + (loyaltyOnly ? " is-active" : ""),
            title: "Только клиенты с начисленными баллами",
            onClick: function () {
              setLoyaltyOnly(function (v) {
                return !v;
              });
            },
          },
          "Есть баллы",
        ),
      ),
      h(GroupCloud, {
        options: groupOptions || [],
        groupsTotal: matchedTotal || 0,
        selected: group,
        onSelect: function (name) {
          setGroup(name);
        },
        onClear: function () {
          setGroup("");
        },
      }),
      error ? h("div", { className: "ms-error" }, error) : null,
      aiFillStatus
        ? h("p", { className: "ms-action-status" }, aiFillStatus)
        : null,
      tgImportNote
        ? h("p", { className: "ms-action-status" }, tgImportNote)
        : null,
      h(
        "p",
        { className: "ms-muted" },
        "Найдено: " +
          matchedTotal +
          (clients.length ? " · показано " + clients.length : ""),
      ),
      loading && !clients.length
        ? h("p", { className: "ms-muted" }, "Загрузка клиентов из МойСклад…")
        : h(
            "div",
            { className: "ms-table-wrap", onScroll: onScroll },
            h(ClientsTable, {
              clients: clients || [],
              onOpenClient: function (c) {
                if (c && c.id) setCardClientId(c.id);
              },
            }),
            loadingMore
              ? h("p", { className: "ms-muted ms-load-more" }, "Подгружаем ещё…")
              : null,
            !hasMore && clients.length
              ? h(
                  "p",
                  { className: "ms-muted ms-load-more" },
                  "Все " + matchedTotal + " клиентов загружены",
                )
              : null,
          ),
      h(AssignModal, {
        open: modalOpen,
        loading: assignLoading,
        data: assignData,
        error: assignError,
        onClose: function () {
          setModalOpen(false);
        },
        onPush: pushAssign,
        onGroupsEdit: function (text) {
          setAssignData(function (prev) {
            return Object.assign({}, prev || {}, {
              recalculate: true,
              groupsText: text,
              groups: text
                .split("\n")
                .map(function (s) {
                  return s.trim();
                })
                .filter(Boolean),
            });
          });
        },
        onRecalcPreview: function () {
          if (!assignData) return;
          setAssignLoading(true);
          setAssignError(null);
          const groups = (assignData.groupsText || (assignData.groups || []).join("\n"))
            .split("\n")
            .map(function (s) {
              return s.trim();
            })
            .filter(Boolean);
          api("/groups/recalculate/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              groups: groups,
              sales_filter: salesFilter,
              group: group || "",
              q: q || "",
              dry_run: true,
              push: false,
            }),
          })
            .then(function (payload) {
              setAssignData(
                Object.assign({}, assignData, {
                  recalculate: true,
                  groups: groups,
                  groupsText: groups.join("\n"),
                  changed: payload.changed,
                  total: payload.total,
                  assignments: payload.assignments || [],
                  source: assignData.source,
                }),
              );
            })
            .catch(function (err) {
              setAssignError(String((err && err.message) || err));
            })
            .finally(function () {
              setAssignLoading(false);
            });
        },
        onRecalcApply: function () {
          if (!assignData) return;
          setAssignLoading(true);
          setAssignError(null);
          const groups = (assignData.groupsText || (assignData.groups || []).join("\n"))
            .split("\n")
            .map(function (s) {
              return s.trim();
            })
            .filter(Boolean);
          api("/groups/recalculate/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              groups: groups,
              sales_filter: salesFilter,
              group: group || "",
              q: q || "",
              dry_run: false,
              push: true,
            }),
          })
            .then(function () {
              setModalOpen(false);
              load({ offset: 0, refresh: true });
            })
            .catch(function (err) {
              setAssignError(String((err && err.message) || err));
            })
            .finally(function () {
              setAssignLoading(false);
            });
        },
      }),
      h(ClientCardModal, {
        open: !!cardClientId,
        clientId: cardClientId,
        onClose: function () {
          setCardClientId(null);
        },
      }),
    );
  }

  function moneyFmt(n) {
    var v = Number(n) || 0;
    try {
      return new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      }).format(v);
    } catch (_) {
      return String(Math.round(v)) + " ₽";
    }
  }

  function FactBlockView({ block }) {
    if (!block) return null;
    var riskClass = block.do_not_upsell ? " ms-fact-block-risk" : "";
    var lines = block.lines || [];
    var kvChildren = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i] || {};
      kvChildren.push(h("span", { className: "ms-muted", key: "l" + i }, line.label || "—"));
      kvChildren.push(h("span", { key: "v" + i }, line.value || "—"));
    }
    return h(
      "div",
      { className: "ms-fact-block" + riskClass },
      h("p", { className: "ms-ai-label" }, block.title || "Факты"),
      block.empty || !lines.length
        ? h("p", { className: "ms-muted" }, block.note || "Нет данных")
        : h("div", { className: "ms-kv-grid ms-fact-block-grid" }, kvChildren),
    );
  }

  function ConversationThread({ conversation, compact, title }) {
    var messages = (conversation && conversation.messages) || [];
    if (!messages.length) {
      return h(
        "div",
        { className: "ms-conversation" },
        h("p", { className: "ms-ai-label" }, title || "TG conversation"),
        h(
          "p",
          { className: "ms-muted" },
          "Нет истории. Нажмите «Импорт Telegram» на Клиентах (нужен telegram_export.json на сервере) — подтянутся старые личные чаты.",
        ),
      );
    }
    var shown = messages.slice().reverse(); // full history, newest first
    return h(
      "div",
      { className: "ms-conversation" },
      h(
        "p",
        { className: "ms-ai-label" },
        (title || "TG conversation") +
          (conversation.message_count != null ? " · " + conversation.message_count : ""),
      ),
      h(
        "div",
        { className: "ms-conversation-list" + (compact ? " is-compact" : "") },
        shown.map(function (m, idx) {
          return h(
            "div",
            {
              key: m.id || String(m.ts || "") + "-" + idx,
              className: "ms-conversation-msg is-" + (m.direction || "outbound"),
            },
            h(
              "div",
              { className: "ms-muted" },
              (m.label || m.direction || "сообщение") +
                (m.ts ? " · " + String(m.ts).slice(0, 16).replace("T", " ") : ""),
            ),
            h("div", null, m.text),
          );
        }),
      ),
    );
  }

  function FactsPanel({ facts, notes, sanity }) {
    if (!facts) {
      return h(
        "aside",
        { className: "ms-facts-panel" },
        h("h3", null, "Факты клиента"),
        h(
          "p",
          { className: "ms-muted" },
          "Выберите клиента из аудитории или из карточки — здесь заказы, чек и теги для сверки с AI.",
        ),
      );
    }
    var last = facts.last_order;
    var historyProse = String(facts.history_profile || "").trim();
    var occasionProse = String(facts.occasion_intent || "").trim();
    var recommendation = String(facts.recommendation || "").trim();
    var hasAiSummary = !!(historyProse || occasionProse || recommendation);
    var historyBlock =
      facts.block_history_profile ||
      (facts.fact_blocks && facts.fact_blocks.history_profile) ||
      null;
    var occasionBlock =
      facts.block_occasion_intent ||
      (facts.fact_blocks && facts.fact_blocks.occasion_intent) ||
      null;
    var risksBlock =
      facts.block_risks || (facts.fact_blocks && facts.fact_blocks.risks) || null;
    var aiSummaryChildren = [h("p", { className: "ms-ai-label", key: "ai-h" }, "Саммари AI")];
    if (historyProse) {
      aiSummaryChildren.push(
        h("p", { className: "ms-ai-label", key: "hp-l" }, "История и профиль клиента"),
        h("p", { className: "ms-facts-rec", key: "hp-v" }, historyProse),
      );
    }
    if (occasionProse) {
      aiSummaryChildren.push(
        h("p", { className: "ms-ai-label", key: "oi-l" }, "Повод и intent покупки"),
        h("p", { className: "ms-facts-rec", key: "oi-v" }, occasionProse),
      );
    }
    if (recommendation) {
      aiSummaryChildren.push(
        h("p", { className: "ms-ai-label", key: "rec-l" }, "Рекомендация AI"),
        h("p", { className: "ms-facts-rec", key: "rec-v" }, recommendation),
      );
    }
    return h(
      "aside",
      { className: "ms-facts-panel" },
      h("h3", null, "Факты · " + (facts.name || "клиент")),
      facts.data_thin
        ? h("p", { className: "ms-muted" }, "Данных мало — текст должен быть осторожным.")
        : null,
      h(
        "div",
        { className: "ms-kv-grid" },
        h("span", { className: "ms-muted" }, "Заказов"),
        h("span", null, String(facts.order_count || 0)),
        h("span", { className: "ms-muted" }, "Средний чек"),
        h("span", null, moneyFmt(facts.avg_check)),
        h("span", { className: "ms-muted" }, "Каналы"),
        h(
          "span",
          null,
          (facts.channels || []).join(", ") || facts.primary_channel || "—",
        ),
        h("span", { className: "ms-muted" }, "VIP"),
        h("span", null, facts.vip ? "да" : "нет"),
        h("span", { className: "ms-muted" }, "Телефон"),
        h("span", null, facts.phone || "—"),
        h("span", { className: "ms-muted" }, "Telegram"),
        h("span", null, facts.tg_nick || "—"),
      ),
      hasAiSummary
        ? h("div", { className: "ms-fact-block ms-facts-ai-summary" }, aiSummaryChildren)
        : null,
      !historyProse ? h(FactBlockView, { block: historyBlock }) : null,
      !occasionProse ? h(FactBlockView, { block: occasionBlock }) : null,
      h(FactBlockView, { block: risksBlock }),
      h(ConversationThread, {
        conversation: facts.conversation,
        compact: true,
        title: "TG conversation",
      }),
      last
        ? h(
            "div",
            { className: "ms-last-order" },
            h("strong", null, "Последний заказ"),
            h(
              "div",
              { className: "ms-muted" },
              String(last.date || "").slice(0, 16).replace("T", " ") +
                " · " +
                moneyFmt(last.sum) +
                (last.channel ? " · " + last.channel : ""),
            ),
            last.product_snippet ? h("div", null, last.product_snippet) : null,
          )
        : null,
      h(TagPills, { items: facts.event_tags || [], className: "ms-tag-row ms-tag-event" }),
      sanity
        ? h(
            "div",
            { className: "ms-sanity" + (sanity.ok ? " is-ok" : " is-bad") },
            h("p", { className: "ms-ai-label" }, "Проверка смысла"),
            h(
              "p",
              { className: "ms-muted" },
              sanity.ok
                ? "Ок — явных конфликтов с долгом/рисками нет."
                : ((sanity.issues || []).join(" ") || "Есть замечания к тексту.") +
                    (sanity.auto_revised ? " Текст автоматически скорректирован." : ""),
            ),
          )
        : null,
      notes ? h("p", { className: "ms-muted ms-grounding" }, notes) : null,
      facts.ai_source ? h("p", { className: "ms-muted" }, "AI: " + facts.ai_source) : null,
    );
  }

  function CardsSideList({ onAdd, onRemove, addedNames }) {
    const [cards, setCards] = useState(null);
    const [selected, setSelected] = useState(null);
    const [error, setError] = useState("");

    useEffect(function () {
      api("/cards/marketplaces?limit=100")
        .then(function (payload) {
          setCards(payload.combined || []);
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return h(
      "aside",
      { className: "ms-cards-side" },
      h(
        "div",
        { className: "ms-cards-side-head" },
        h("strong", null, "Карточки"),
        h(
          "p",
          { className: "ms-muted" },
          "Обе площадки одним списком" + (cards ? " · " + cards.length : "") + " — листайте вниз",
        ),
      ),
      h(
        "div",
        { className: "ms-cards-side-list" },
        error ? h("p", { className: "ms-error" }, error) : null,
        !cards && !error ? h("p", { className: "ms-muted" }, "Загружаем…") : null,
        (cards || []).map(function (card, idx) {
          return h(CombinedCardTile, {
            key: String(card.name || idx),
            card: card,
            onSelect: setSelected,
            onAdd: onAdd,
            onRemove: onRemove,
            added: addedNames ? addedNames.indexOf(card.name || "") !== -1 : false,
          });
        }),
      ),
      selected
        ? h(CombinedDrawer, {
            card: selected,
            onClose: function () {
              setSelected(null);
            },
            onAdd: onAdd,
            onRemove: onRemove,
            added: addedNames ? addedNames.indexOf(selected.name || "") !== -1 : false,
          })
        : null,
    );
  }

  function CampaignsPage() {
    const [chatTurns, setChatTurns] = useState([]);
    const [chatInput, setChatInput] = useState("");
    const [chatBusy, setChatBusy] = useState(false);
    const [skillPromptText, setSkillPromptText] = useState("");
    // Verified live via OpenRouter on this key (probe 17.08.2026):
    // grok-* / gpt-5* return empty — key has no access.
    var CHAT_REFINE_MODELS = {
      deepseek: { id: "deepseek/deepseek-chat", label: "DeepSeek" },
      gpt: { id: "openai/gpt-4o", label: "GPT" },
      claude: { id: "anthropic/claude-sonnet-4.5", label: "Claude" },
      gemini: { id: "google/gemini-2.5-flash", label: "Gemini" },
    };
    const [chatModel, setChatModel] = useState("deepseek");

    useEffect(
      function () {
        setChatTurns([]);
        setChatInput("");
        setSkillPromptText("");
      },
      [selectedClientId],
    );

    function sendChatTurn(override) {
      override = override || {};
      var ask = String(override.ask != null ? override.ask : chatInput || "").trim();
      if (!ask || chatBusy || !selectedClientId) return;
      var modelKey = override.model || chatModel;
      var turns = chatTurns.concat([{ role: "user", content: ask }]);
      setChatTurns(turns);
      setChatInput("");
      setChatBusy(true);
      api("/campaigns/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: selectedClientId,
          channel: channel,
          draft: offerRef.current || offer || "",
          messages: turns,
          provider: "openrouter",
          model: (CHAT_REFINE_MODELS[modelKey] || CHAT_REFINE_MODELS.deepseek).id,
          seller_name: sellerName,
          seller_facts: sellerFacts,
        }),
      })
        .then(function (data) {
          var reply = String((data && data.reply) || "").trim() || "Готово.";
          setChatTurns(function (prev) {
            return prev.concat([{ role: "assistant", content: reply }]);
          });
          var msg = String((data && data.message) || "").trim();
          if (msg) {
            setOffer(msg);
            offerRef.current = msg;
          }
        })
        .catch(function (err) {
          setChatTurns(function (prev) {
            return prev.concat([
              { role: "assistant", content: "Ошибка: " + String((err && err.message) || err) },
            ]);
          });
        })
        .finally(function () {
          setChatBusy(false);
        });
    }

    function saveSkill(text) {
      api("/campaigns/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, notes: "" }),
      })
        .then(function () {
          setActionStatus("✓ Сохранено в навык — следующие генерации учтут стиль");
        })
        .catch(function () {})
        .finally(function () {
          setSkillPromptText("");
        });
    }

    const [sentFeed, setSentFeed] = useState([]);
    const [sentFeedLoading, setSentFeedLoading] = useState(false);
    const [historyJobs, setHistoryJobs] = useState([]);

    function newestFirst(rows, key) {
      return (rows || []).slice().sort(function (a, b) {
        var ta = Date.parse(String((a && a[key]) || "")) || 0;
        var tb = Date.parse(String((b && b[key]) || "")) || 0;
        return tb - ta;
      });
    }

    function loadSendHistory() {
      setSentFeedLoading(true);
      Promise.all([
        api("/campaigns/mass-send/history?limit=20").catch(function () {
          return {};
        }),
        api("/campaigns/sent-history?limit=300").catch(function () {
          return {};
        }),
      ])
        .then(function (out) {
          setHistoryJobs(newestFirst((out[0] && out[0].jobs) || [], "created_at"));
          setSentFeed(newestFirst((out[1] && out[1].messages) || [], "ts"));
        })
        .finally(function () {
          setSentFeedLoading(false);
        });
    }

    useEffect(function () {
      loadSendHistory();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    const [campaigns, setCampaigns] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [title, setTitle] = useState("Рассылка по фильтрам");
    const [sendImage, setSendImage] = useState(null); // {name, dataUrl?, url?}
    const [sendCards, setSendCards] = useState([]); // [{name, block, image}]
    const [insertMenuOpen, setInsertMenuOpen] = useState(false);
    const [pickerCards, setPickerCards] = useState(null); // combined list for the photo picker
    const [pickerOpen, setPickerOpen] = useState(false);
    const [pickerCard, setPickerCard] = useState(null); // card chosen inside the picker

    function openCardPicker() {
      setInsertMenuOpen(false);
      setPickerCard(null);
      setPickerOpen(true);
      if (!pickerCards) {
        api("/cards/marketplaces?limit=100")
          .then(function (payload) {
            setPickerCards(payload.combined || []);
          })
          .catch(function (err) {
            setError(String((err && err.message) || err));
            setPickerOpen(false);
          });
      }
    }

    function cardPhotos(card) {
      var out = [];
      var listings = (card && card.listings) || {};
      Object.keys(listings).forEach(function (mp) {
        ((listings[mp] && listings[mp].images) || []).forEach(function (src) {
          if (src && out.indexOf(src) === -1) out.push(src);
        });
      });
      if (!out.length && card && card.image) out.push(card.image);
      return out;
    }

    function pickPhoto(card, src) {
      setSendImage({ name: (card && card.name) || "card.jpg", url: src });
      setPickerOpen(false);
      setPickerCard(null);
    }

    function addCardToMessage(card) {
      var entry = cardMessageBlock(card);
      setSendCards(function (prev) {
        for (var i = 0; i < prev.length; i++) if (prev[i].name === entry.name) return prev;
        return prev.concat([entry]);
      });
      var current = String(offerRef.current || "");
      var nextText = current.trim() ? current.replace(/\s+$/, "") + "\n\n" + entry.block : entry.block;
      setOffer(nextText);
      offerRef.current = nextText;
      if (entry.image) {
        // Photo MUST travel with the card — the freshly added card wins.
        setSendImage({ name: entry.name, url: entry.image });
      }
    }

    function removeCardFromMessage(name) {
      setSendCards(function (prev) {
        var entry = null;
        var rest = [];
        prev.forEach(function (item) {
          if (item.name === name) entry = item;
          else rest.push(item);
        });
        if (entry) {
          var current = String(offerRef.current || "");
          var nextText = current.split("\n\n" + entry.block).join("").split(entry.block).join("").trim();
          setOffer(nextText);
          offerRef.current = nextText;
          setSendImage(function (image) {
            if (image && image.url && image.url === entry.image) {
              for (var i = 0; i < rest.length; i++) {
                if (rest[i].image) return { name: rest[i].name, url: rest[i].image };
              }
              return null;
            }
            return image;
          });
        }
        return rest;
      });
    }
    const [channel, setChannel] = useState("telegram");
    const [channelKind, setChannelKind] = useState("");
    const [group, setGroup] = useState("");
    const [requirePhone, setRequirePhone] = useState(false);
    const [requireTelegram, setRequireTelegram] = useState(false);
    const [vipOnly, setVipOnly] = useState(false);
    const [loyaltyOnly, setLoyaltyOnly] = useState(false);
    const [stage, setStage] = useState("all");
    const [entityType, setEntityType] = useState("all");
    const [stageCounts, setStageCounts] = useState(null);
    const [birthdaySoon, setBirthdaySoon] = useState(false);
    const [personalize, setPersonalize] = useState(false);
    const [mode, setMode] = useState("manual");
    const [offer, setOffer] = useState("");
    const [actionStatus, setActionStatus] = useState("");
    const offerRef = useRef("");
    useEffect(function () { offerRef.current = offer; }, [offer]);
    const [salesFilter, setSalesFilter] = useState("direct");
    const [saving, setSaving] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [rewriting, setRewriting] = useState(false);
    const [checkingSanity, setCheckingSanity] = useState(false);
    const [sanity, setSanity] = useState(null);
    const [counts, setCounts] = useState(null);
    const [audience, setAudience] = useState(0);
    const [audiencePreview, setAudiencePreview] = useState([]);
    const [audienceQ, setAudienceQ] = useState("");
    const [audienceQDebounced, setAudienceQDebounced] = useState("");
    const [audienceHasMore, setAudienceHasMore] = useState(false);
    const [audienceNextOffset, setAudienceNextOffset] = useState(0);
    const [audienceLoadingMore, setAudienceLoadingMore] = useState(false);
    const audienceLoadMoreRef = useRef(false);
    const sellerSaveTimer = useRef(null);
    const [groupOptions, setGroupOptions] = useState([]);
    const [selectedClientId, setSelectedClientId] = useState(null);
    const [facts, setFacts] = useState(null);
    const [groundingNotes, setGroundingNotes] = useState("");
    const [genSource, setGenSource] = useState("");
    const [sellerName, setSellerName] = useState("");
    const [sellerFacts, setSellerFacts] = useState("");
    const [sellerLoaded, setSellerLoaded] = useState(false);
    const [contactsOpen, setContactsOpen] = useState(true);
    const [prefillReady, setPrefillReady] = useState(false);
    const [tgUser, setTgUser] = useState(null);
    const [tgOpen, setTgOpen] = useState(false);
    const [tgStep, setTgStep] = useState("phone");
    const [tgBusy, setTgBusy] = useState(false);
    const [tgProgress, setTgProgress] = useState(null);
    const [tgPhone, setTgPhone] = useState("");
    const [tgCode, setTgCode] = useState("");
    const [tgPassword, setTgPassword] = useState("");
    const [tgSession, setTgSession] = useState("");

    function runTgBusy(title, detail, work, timeoutMs) {
      setTgBusy(true);
      setTgProgress({ title: title, detail: detail });
      setError("");
      var waitMs = typeof timeoutMs === "number" && timeoutMs > 0 ? timeoutMs : 60000;
      var timedOut = false;
      var timer = setTimeout(function () {
        timedOut = true;
        setError(
          "Telegram не ответил за " +
            Math.round(waitMs / 1000) +
            "с. С Selectel нужен TELEGRAM_USER_GATEWAY_URL (Railway) или StringSession.",
        );
        setTgBusy(false);
        setTgProgress(null);
      }, waitMs);
      return Promise.resolve()
        .then(work)
        .catch(function (err) {
          if (!timedOut) setError((err && err.message) || String(err));
        })
        .finally(function () {
          clearTimeout(timer);
          if (!timedOut) {
            setTgBusy(false);
            setTgProgress(null);
          }
        });
    }

    function refreshTgUser(probe) {
      return api("/campaigns/telegram-user?probe=" + (probe ? "true" : "false"))
        .then(function (data) {
          setTgUser(data || null);
          if (data && data.phone && !tgPhone) setTgPhone(data.phone);
          // Only snap back to the phone form after a successful auth —
          // never wipe an in-progress code/password step on a failed probe.
          if (data && data.authorized) setTgStep("phone");
          return data;
        })
        .catch(function () {
          return null;
        });
    }

    useEffect(function () {
      refreshTgUser(false);
    }, []);

    // Contact sync runs server-side in the background; the UI only polls
    // progress — no blocking modal, tab can be closed at any point.
    function tgPollSync() {
      var tries = 0;
      function tick() {
        api("/campaigns/telegram-user/contacts/sync")
          .then(function (st) {
            if (st && st.running) {
              var phase =
                st.phase === "address_book"
                  ? "адресная книга"
                  : st.phase === "dialogs"
                    ? "чаты, просмотрено " + (st.scanned || 0)
                    : "запуск";
              setActionStatus(
                "Синхронизация в фоне: контактов " +
                  (st.total || 0) +
                  " · " +
                  phase +
                  "…",
              );
              if (++tries < 300) setTimeout(tick, 2000);
              return;
            }
            if (st && st.phase === "error" && st.error) {
              setError("Синхронизация контактов: " + st.error);
            } else if (st) {
              setActionStatus(
                "✓ Контакты из Telegram: " +
                  (st.total || 0) +
                  " (адресная книга " +
                  (st.from_address_book || 0) +
                  ", чаты " +
                  (st.from_dialogs || 0) +
                  ")",
              );
            }
            refreshTgUser(false);
          })
          .catch(function () {
            if (++tries < 300) setTimeout(tick, 3000);
          });
      }
      tick();
    }

    function tgSyncAfterAuth() {
      return api("/campaigns/telegram-user/contacts/refresh", { method: "POST" })
        .catch(function () {
          return null;
        })
        .then(function () {
          tgPollSync();
          return refreshTgUser(true);
        });
    }

    function tgLogin() {
      if (!String(tgPhone || "").trim()) {
        setError("Укажите номер телефона в формате +79991234567");
        return;
      }
      runTgBusy(
        "Telethon: вход",
        "Отправляем код на " + String(tgPhone).trim() + "…",
        function () {
          var body = { phone: String(tgPhone || "").trim() };
          return api("/campaigns/telegram-user/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }).then(function (data) {
            if (data && data.authorized) {
              setActionStatus("✓ Личный Telegram уже подключён");
              return tgSyncAfterAuth();
            }
            setTgCode("");
            setTgPassword("");
            setTgStep("code");
            setActionStatus("Код отправлен в Telegram — введите его ниже");
          });
        },
        90000,
      );
    }

    function tgSubmitCode() {
      runTgBusy(
        "Telethon: код",
        "Проверяем код из Telegram…",
        function () {
          return api("/campaigns/telegram-user/code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: String(tgCode || "").trim() }),
          }).then(function (data) {
            setTgCode("");
            if (data && data.password_required) {
              setTgStep("password");
              setActionStatus("Нужен облачный пароль (2FA)");
              return;
            }
            setTgStep("phone");
            setActionStatus("✓ Личный Telegram подключён — контакты синхронизированы");
            return tgSyncAfterAuth();
          });
        },
        90000,
      );
    }

    function tgSubmitPassword() {
      runTgBusy(
        "Telethon: 2FA",
        "Проверяем облачный пароль…",
        function () {
          return api("/campaigns/telegram-user/password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: String(tgPassword || "") }),
          }).then(function () {
            setTgPassword("");
            setTgStep("phone");
            setActionStatus("✓ Личный Telegram подключён — контакты синхронизированы");
            return tgSyncAfterAuth();
          });
        },
        90000,
      );
    }

    function tgRestartLogin(opts) {
      // Soft restart of the login wizard (no logout of an already-authorized
      // session). Used when stuck on the code/2FA step or to re-enter the flow.
      var clearPhone = !!(opts && opts.clearPhone);
      setError("");
      setTgCode("");
      setTgPassword("");
      setTgSession("");
      if (clearPhone) setTgPhone("");
      setTgStep("phone");
      setTgOpen(true);
      setActionStatus(
        clearPhone
          ? "Введите новый номер и нажмите «Получить код»"
          : "Можно сменить номер или снова нажать «Получить код»",
      );
    }

    function tgBeginLogin() {
      // Explicit «Войти» entry: open the phone form even if a stale code step
      // was left open, or after a dead session_saved probe.
      if (tgUser && tgUser.authorized) {
        // Switch account: logout first, then show phone form.
        runTgBusy("Смена аккаунта", "Выходим из текущего Telegram…", function () {
          return api("/campaigns/telegram-user/logout", { method: "POST" }).then(
            function () {
              setTgCode("");
              setTgPassword("");
              setTgSession("");
              setTgStep("phone");
              setTgOpen(true);
              setActionStatus("Введите номер и получите код для нового входа");
              return refreshTgUser(false);
            },
          );
        });
        return;
      }
      tgRestartLogin({ clearPhone: false });
    }

    function tgLogout() {
      runTgBusy("Выход", "Отключаем личный Telegram…", function () {
        return api("/campaigns/telegram-user/logout", { method: "POST" }).then(
          function () {
            setTgCode("");
            setTgPassword("");
            setTgSession("");
            setTgStep("phone");
            setTgOpen(true);
            setActionStatus("Личный Telegram отключён — можно войти другим номером");
            return refreshTgUser(false);
          },
        );
      });
    }

    function tgSyncContacts() {
      setError("");
      api("/campaigns/telegram-user/contacts/refresh", { method: "POST" })
        .then(function (st) {
          setActionStatus(
            st && st.started
              ? "Синхронизация запущена в фоне — контакты подтягиваются…"
              : "Синхронизация уже идёт в фоне…",
          );
          tgPollSync();
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
        });
    }

    function tgInstallRuntime() {
      runTgBusy("Установка Telethon", "Ставим MTProto-движок в venv…", function () {
        return api("/campaigns/telegram-user/install", { method: "POST" }).then(
          function (data) {
            setTgUser(data || null);
            setActionStatus(
              "✓ telethon установлен" +
                (data && data.version ? " " + data.version : ""),
            );
          },
        );
      });
    }

    function tgSaveSession() {
      if (!String(tgSession || "").trim()) {
        setError("Вставьте StringSession");
        return;
      }
      runTgBusy("Telethon: сессия", "Сохраняем StringSession…", function () {
        return api("/campaigns/telegram-user/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session: String(tgSession || "").trim(),
            phone: String(tgPhone || "").trim(),
          }),
        }).then(function () {
          setTgSession("");
          setActionStatus("✓ Сессия Telegram сохранена");
          return tgSyncAfterAuth();
        });
      });
    }

    useEffect(function () {
      var cid = null;
      var ch = null;
      var sf = null;
      try {
        var sp = new URLSearchParams(window.location.search);
        cid = sp.get("client_id");
        var raw = sessionStorage.getItem("moysklad.draftPrefill");
        if (raw) {
          sessionStorage.removeItem("moysklad.draftPrefill");
          var parsed = JSON.parse(raw);
          if (parsed && parsed.clientId) {
            cid = parsed.clientId;
            ch = parsed.channel || ch;
            sf = parsed.salesFilter || sf;
          }
        }
      } catch (_) {}
      if (cid) {
        setSelectedClientId(cid);
        setMode("auto");
        setTitle("Черновик · клиент");
        if (ch) setChannel(ch);
        if (sf) setSalesFilter(sf);
      }
      setPrefillReady(true);
    }, []);

    useEffect(function () {
      api("/campaigns/seller-settings")
        .then(function (data) {
          setSellerName((data && data.seller_name) || "");
          setSellerFacts((data && data.seller_facts) || "");
        })
        .catch(function () {})
        .finally(function () {
          setSellerLoaded(true);
        });
    }, []);

    function persistSellerSettings(name, factsText) {
      if (sellerSaveTimer.current) clearTimeout(sellerSaveTimer.current);
      sellerSaveTimer.current = setTimeout(function () {
        api("/campaigns/seller-settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            seller_name: name,
            seller_facts: factsText,
          }),
        }).catch(function () {});
      }, 450);
    }

    useEffect(
      function () {
        const t = setTimeout(function () {
          setAudienceQDebounced((audienceQ || "").trim());
        }, 280);
        return function () {
          clearTimeout(t);
        };
      },
      [audienceQ],
    );

    const audienceQs = useCallback(
      function (opts) {
        opts = opts || {};
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          group: group || "",
          q: opts.q != null ? opts.q : audienceQDebounced,
          limit: String(opts.limit != null ? opts.limit : 40),
          offset: String(opts.offset != null ? opts.offset : 0),
          stage: stage,
          entity_type: entityType,
        });
        if (channelKind) params.set("channel_kind", channelKind);
        if (requirePhone) params.set("require_phone", "true");
        if (requireTelegram) params.set("require_telegram", "true");
        if (vipOnly) params.set("vip_only", "true");
        if (loyaltyOnly) params.set("loyalty_only", "true");
        if (birthdaySoon) params.set("birthday_soon", "true");
        return params.toString();
      },
      [
        salesFilter,
        group,
        channelKind,
        requirePhone,
        requireTelegram,
        vipOnly,
        loyaltyOnly,
        birthdaySoon,
        stage,
        entityType,
        audienceQDebounced,
      ],
    );

    const loadAudience = useCallback(
      function (opts) {
        const append = !!(opts && opts.append);
        const offset = append ? audienceNextOffset : 0;
        if (append) {
          if (audienceLoadMoreRef.current || !audienceHasMore) return;
          audienceLoadMoreRef.current = true;
          setAudienceLoadingMore(true);
        } else {
          setLoading(true);
          setError("");
        }
        api("/clients?" + audienceQs({ offset: offset, limit: 40 }))
          .then(function (page) {
            const rows = (page && page.clients) || [];
            setAudiencePreview(function (prev) {
              if (!append) return rows;
              const seen = {};
              const out = [];
              (prev || []).concat(rows).forEach(function (row) {
                const id = String((row && row.id) || "").trim();
                if (id) {
                  if (seen[id]) return;
                  seen[id] = true;
                }
                out.push(row);
              });
              return out;
            });
            setAudience((page && page.matched_total) || 0);
            setCounts((page && page.counts) || null);
            if (page && page.stage_counts) setStageCounts(page.stage_counts);
            if (!append) setGroupOptions((page && page.group_options) || []);
            const next =
              page && page.next_offset != null
                ? page.next_offset
                : offset + rows.length;
            setAudienceNextOffset(next);
            setAudienceHasMore(
              page && page.has_more != null
                ? !!page.has_more
                : next < ((page && page.matched_total) || 0),
            );
          })
          .catch(function (err) {
            setError((err && err.message) || String(err));
            if (!append) setAudiencePreview([]);
          })
          .finally(function () {
            if (append) {
              audienceLoadMoreRef.current = false;
              setAudienceLoadingMore(false);
            } else {
              setLoading(false);
            }
          });
      },
      [audienceQs, audienceNextOffset, audienceHasMore],
    );

    const refresh = useCallback(
      function () {
        api("/campaigns")
          .then(function (data) {
            setCampaigns((data && data.campaigns) || []);
          })
          .catch(function () {});
        loadAudience();
      },
      [loadAudience],
    );

    useEffect(
      function () {
        loadAudience();
      },
      [
        salesFilter,
        group,
        channelKind,
        requirePhone,
        requireTelegram,
        vipOnly,
        birthdaySoon,
        stage,
        entityType,
        loyaltyOnly,
        audienceQDebounced,
      ],
    );

    useEffect(
      function () {
        api("/campaigns")
          .then(function (data) {
            setCampaigns((data && data.campaigns) || []);
          })
          .catch(function () {});
      },
      [],
    );

    const loadOutreach = useCallback(
      function (clientId, nextChannel) {
        setGenerating(true);
        setError("");
        setActionStatus("Генерируем текст…");
        api("/campaigns/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            client_id: clientId,
            channel: nextChannel || channel,
            refresh_ai: true,
            seller_name: sellerName,
            seller_facts: sellerFacts,
          }),
        })
          .then(function (data) {
            setFacts(data.facts || null);
            setGroundingNotes(data.grounding_notes || "");
            setGenSource(data.source || "");
            setSanity(data.sanity || null);
            var msg = pickOutreachMessage(data);
            if (msg) {
              setOffer(msg);
              setActionStatus(
                data.sanity && data.sanity.auto_revised
                  ? "AI сгенерировал текст (sanity поправил формулировку)."
                  : "AI сгенерировал текст — можно править вручную."
              );
            } else {
              setError("Сервер не вернул текст сообщения. Попробуйте ещё раз.");
              setActionStatus("");
            }
            if (data.client_name) setTitle("Черновик · " + data.client_name);
          })
          .catch(function (err) {
            setError((err && err.message) || String(err));
          })
          .finally(function () {
            setGenerating(false);
          });
      },
      [channel, sellerName, sellerFacts],
    );

    function humanizeDraft() {
      var draft = String(offerRef.current || offer || "").trim();
      if (!draft) {
        setError("Сначала введите или сгенерируйте текст сообщения.");
        return;
      }
      setRewriting(true);
      setError("");
      setActionStatus("Переписываем продающе и по-человечески…");
      api("/campaigns/rewrite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: draft,
          channel: channel,
          client_id: selectedClientId || "",
          seller_name: sellerName,
          seller_facts: sellerFacts,
        }),
      })
        .then(function (data) {
          var draft = String(offerRef.current || "").trim();
          var msg = pickOutreachMessage(data) || draft;
          setOffer(msg);
          setActionStatus(
            msg.trim() === draft
              ? "Переписали тон (текст почти тот же — правки лёгкие)."
              : "Текст обновлён: продающе и по-человечески."
          );
          if (data.grounding_notes) setGroundingNotes(data.grounding_notes);
          if (data.source) setGenSource(data.source);
          if (data.facts) setFacts(data.facts);
          if (data.sanity) setSanity(data.sanity);
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
          setActionStatus("");
        })
        .finally(function () {
          setRewriting(false);
        });
    }

    function runSanityCheck() {
      var draft = String(offerRef.current || offer || "").trim();
      if (!draft) {
        setError("Сначала введите или сгенерируйте текст сообщения.");
        return;
      }
      setCheckingSanity(true);
      setError("");
      setActionStatus("Проверяем смысл…");
      api("/campaigns/sanity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: draft,
          channel: channel,
          client_id: selectedClientId || "",
          seller_name: sellerName,
          seller_facts: sellerFacts,
          apply_revision: true,
        }),
      })
        .then(function (data) {
          var draft = String(offerRef.current || "").trim();
          var msg = pickOutreachMessage(data) || draft;
          var revised = !!(data.sanity && (data.sanity.auto_revised || (msg.trim() && msg.trim() !== draft)));
          setOffer(msg);
          setActionStatus(
            revised
              ? "Смысл: текст скорректирован (см. замечания)."
              : data.sanity && data.sanity.ok === false
                ? "Смысл: " + ((data.sanity.issues || []).join("; ") || "есть замечания") + "."
                : "Смысл в порядке — текст оставлен как есть."
          );
          if (data.sanity) setSanity(data.sanity);
          if (data.facts && Object.keys(data.facts).length) setFacts(data.facts);
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
          setActionStatus("");
        })
        .finally(function () {
          setCheckingSanity(false);
        });
    }

    function markSentToConversation() {
      if (!selectedClientId) {
        setError("Выберите клиента — исходящее уйдёт в Telegram / историю.");
        return;
      }
      var draft = String(offerRef.current || offer || "").trim();
      if (!draft) {
        setError("Сначала введите или сгенерируйте текст сообщения.");
        return;
      }
      setCheckingSanity(true);
      setError("");
      setActionStatus(
        String(channel || "").indexOf("telegram") === 0
          ? "Отправка через Telegram Business bot…"
          : "Пишем исходящее в историю…"
      );
      api("/campaigns/mark-sent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: draft,
          channel: channel,
          client_id: selectedClientId,
          open_deep_link: true,
          deliver: true,
          image_base64: (sendImage && sendImage.dataUrl) || "",
          image_url: (sendImage && sendImage.url) || "",
          image_name: (sendImage && sendImage.name) || "photo.jpg",
        }),
      })
        .then(function (data) {
          var draft = String(offerRef.current || "").trim();
          if (data.facts) setFacts(data.facts);
          else if (data.conversation) {
            setFacts(function (prev) {
              return prev ? Object.assign({}, prev, { conversation: data.conversation }) : prev;
            });
          }
          setOffer(draft);
          if (data.delivery && data.delivery.ok) {
            setActionStatus("✓ Отправлено в Telegram (Business bot) + история.");
            setSkillPromptText(draft);
          } else if (
            String(channel || "").indexOf("telegram") === 0 &&
            data.delivery &&
            !data.delivery.skipped
          ) {
            var detail = data.delivery.detail || data.delivery.error || "ошибка";
            setActionStatus("⚠ В историю записано; Bot API: " + detail);
            setError("Telegram: " + detail);
          } else {
            setActionStatus("⚠ НЕ доставлено в Telegram — записано только в историю (клиент недоступен или доставка не настроена).");
          }
          if (data.deep_link) window.open(data.deep_link, "_blank", "noopener");
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
          setActionStatus("");
        })
        .finally(function () {
          setCheckingSanity(false);
        });
    }

    useEffect(
      function () {
        if (!prefillReady || !selectedClientId) return;
        loadOutreach(selectedClientId, channel);
      },
      [prefillReady, selectedClientId],
    );

    // Poll the selected client's TG thread so inbound replies land in the
    // Facts «TG conversation» block without re-clicking (30s, no AI regen).
    useEffect(
      function () {
        if (!prefillReady || !selectedClientId) return;
        var cid = selectedClientId;
        var timer = setInterval(function () {
          api(
            "/clients/" + encodeURIComponent(cid) + "/conversation/sync?refresh_ai=false",
            { method: "POST" },
          )
            .then(function (data) {
              if (!data || !data.conversation) return;
              setFacts(function (prev) {
                return prev ? Object.assign({}, prev, { conversation: data.conversation }) : prev;
              });
            })
            .catch(function () {});
        }, 30000);
        return function () {
          clearInterval(timer);
        };
      },
      [prefillReady, selectedClientId],
    );

    function syncDeliveryChannel(kind) {
      setChannelKind(kind);
      if (kind === "telegram") {
        setChannel("telegram");
        setRequireTelegram(true);
        setRequirePhone(false);
      } else if (kind === "whatsapp") {
        setChannel("whatsapp");
        setRequirePhone(true);
        setRequireTelegram(false);
      } else {
        setRequirePhone(false);
        setRequireTelegram(false);
      }
    }

    function createDraft(e) {
      e.preventDefault();
      setSaving(true);
      setError("");
      api("/campaigns", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title,
          channel: channel,
          mode: mode,
          offer: offer,
          sales_filter: salesFilter,
          group: group,
          channel_kind: channelKind,
          require_phone: requirePhone,
          require_telegram: requireTelegram,
          vip_only: vipOnly,
          birthday_soon: birthdaySoon,
          personalize: personalize,
          client_id: selectedClientId || "",
          generate_ai: mode === "auto" && !String(offer || "").trim(),
          seller_name: sellerName,
          seller_facts: sellerFacts,
        }),
      })
        .then(function () {
          if (!selectedClientId) setOffer("");
          refresh();
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
        })
        .finally(function () {
          setSaving(false);
        });
    }

    function remove(id) {
      api("/campaigns/" + encodeURIComponent(id), { method: "DELETE" })
        .then(refresh)
        .catch(function (err) {
          setError((err && err.message) || String(err));
        });
    }

    return h(
      "div",
      { className: "ms-clients ms-campaigns" },
      h(
        "div",
        { className: "ms-clients-header" },
        h(
          "div",
          null,
          h("h1", { className: "ms-clients-title" }, "Рассылки"),
          h(
            "p",
            { className: "ms-muted" },
            "Массовые черновики · аудитория = дедуп-кэш Клиентов",
          ),
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            disabled: loading,
            onClick: refresh,
          },
          "Обновить",
        ),
      ),
      h(FilterTabs, {
        salesFilter: salesFilter,
        counts: counts,
        disabled: loading,
        onChange: setSalesFilter,
      }),
      h(
        "div",
        { className: "ms-filter-block" },
        h("span", { className: "ms-filter-label" }, "Тип клиента"),
        h(
          "div",
          { className: "ms-chips" },
          STAGE_CHIPS.map(function (chip) {
            var n = stageCounts && stageCounts[chip.id];
            return h(
              "button",
              {
                key: chip.id,
                type: "button",
                className: "ms-chip" + (stage === chip.id ? " is-active" : ""),
                onClick: function () {
                  setStage(stage === chip.id ? "all" : chip.id);
                },
              },
              chip.label,
              n != null ? h("span", null, String(n)) : null,
            );
          }),
          h(
            "button",
            {
              type: "button",
              className: "ms-chip" + (entityType === "individual" ? " is-active" : ""),
              onClick: function () {
                setEntityType(entityType === "individual" ? "all" : "individual");
              },
            },
            "Физ. лица",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-chip" + (entityType === "legal" ? " is-active" : ""),
              onClick: function () {
                setEntityType(entityType === "legal" ? "all" : "legal");
              },
            },
            "Юр. лица",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-chip" + (loyaltyOnly ? " is-active" : ""),
              onClick: function () {
                setLoyaltyOnly(!loyaltyOnly);
              },
            },
            "Есть баллы",
          ),
        ),
      ),
      h(
        "section",
        { className: "ms-audience-builder" },
        h("h2", { className: "ms-section-title" }, "Аудитория массовой рассылки"),
        h(
          "p",
          { className: "ms-muted" },
          "Найдено (после дедупа): ",
          h("strong", null, String(audience)),
          loading ? " · обновляем…" : "",
          selectedClientId
            ? [
                " · выбран ",
                h(
                  "strong",
                  { key: "n" },
                  (facts && facts.name) || selectedClientId,
                ),
                h(
                  "button",
                  {
                    key: "x",
                    type: "button",
                    className: "ms-link-btn",
                    style: { marginLeft: "0.5rem" },
                    onClick: function () {
                      setSelectedClientId(null);
                      setFacts(null);
                      setGroundingNotes("");
                      setTitle("Рассылка по фильтрам");
                    },
                  },
                  "сбросить клиента",
                ),
              ]
            : null,
        ),
        h(
          "div",
          { className: "ms-filter-block" },
          h("span", { className: "ms-filter-label" }, "Канал доставки"),
          h(
            "div",
            { className: "ms-filter-tabs", role: "group" },
            [
              { id: "", label: "Любой" },
              { id: "telegram", label: "Только Telegram" },
              { id: "whatsapp", label: "Только WhatsApp" },
            ].map(function (opt) {
              return h(
                "button",
                {
                  key: opt.id || "any",
                  type: "button",
                  className:
                    "ms-filter-tab" +
                    (channelKind === opt.id ? " is-active" : ""),
                  onClick: function () {
                    syncDeliveryChannel(opt.id);
                  },
                },
                opt.label,
              );
            }),
          ),
        ),
        h(
          "div",
          { className: "ms-filter-block" },
          h("span", { className: "ms-filter-label" }, "Дополнительно"),
          h(
            "div",
            { className: "ms-chips" },
            h(
              "button",
              {
                type: "button",
                className: "ms-chip" + (vipOnly ? " is-active" : ""),
                onClick: function () {
                  setVipOnly(!vipOnly);
                },
              },
              "VIP",
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-chip" + (requirePhone ? " is-active" : ""),
                onClick: function () {
                  setRequirePhone(!requirePhone);
                },
              },
              "Есть телефон",
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-chip" + (requireTelegram ? " is-active" : ""),
                onClick: function () {
                  setRequireTelegram(!requireTelegram);
                },
              },
              "Есть Telegram",
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-chip" + (birthdaySoon ? " is-active" : ""),
                onClick: function () {
                  setBirthdaySoon(!birthdaySoon);
                },
              },
              "ДР / события",
            ),
          ),
        ),
        groupOptions.length
          ? h(
              "div",
              { className: "ms-filter-block" },
              h("span", { className: "ms-filter-label" }, "Тег / повод"),
              h(
                "div",
                { className: "ms-chips" },
                groupOptions.slice(0, 20).map(function (opt) {
                  return h(
                    "button",
                    {
                      key: opt.name,
                      type: "button",
                      className:
                        "ms-chip" + (group === opt.name ? " is-active" : ""),
                      onClick: function () {
                        setGroup(group === opt.name ? "" : opt.name);
                      },
                    },
                    opt.name,
                    h("span", null, String(opt.count)),
                  );
                }),
              ),
            )
          : null,
        h(
          "div",
          { className: "ms-audience-pick" },
          h(
            "div",
            { className: "ms-audience-pick-head" },
            h(
              "p",
              { className: "ms-muted" },
              "Клиенты аудитории (поиск / подгрузка — доступны все " +
                audience +
                "):",
            ),
            audiencePreview.length
              ? h(
                  "button",
                  {
                    type: "button",
                    className: "ms-link-btn",
                    onClick: function () {
                      setContactsOpen(!contactsOpen);
                    },
                  },
                  contactsOpen ? "Скрыть контакты" : "Показать контакты",
                )
              : null,
          ),
          contactsOpen
            ? h(
                React.Fragment,
                null,
                h(
                  "div",
                  { className: "ms-search" },
                  h("input", {
                    type: "search",
                    placeholder: "Найти клиента в аудитории…",
                    value: audienceQ,
                    onChange: function (e) {
                      setAudienceQ(e.target.value);
                    },
                  }),
                ),
                audiencePreview.length
                  ? h(
                      "div",
                      {
                        className: "ms-audience-list",
                        onScroll: function (e) {
                          const el = e.currentTarget;
                          if (
                            el.scrollHeight - el.scrollTop - el.clientHeight >
                            120
                          )
                            return;
                          if (!audienceHasMore || audienceLoadMoreRef.current)
                            return;
                          loadAudience({ append: true });
                        },
                      },
                      h(
                        "div",
                        { className: "ms-chips" },
                        audiencePreview.map(function (row) {
                          return h(
                            "button",
                            {
                              key: row.id || row.name,
                              type: "button",
                              className:
                                "ms-chip" +
                                (selectedClientId === row.id
                                  ? " is-active"
                                  : ""),
                              onClick: function () {
                                if (!row.id) return;
                                setSelectedClientId(row.id);
                                setMode("auto");
                                if (row.phone && !row.tg_nick)
                                  setChannel("whatsapp");
                                else setChannel("telegram");
                              },
                            },
                            row.name || row.phone || row.id,
                            row.order_count != null
                              ? h("span", null, String(row.order_count))
                              : null,
                          );
                        }),
                      ),
                      audienceLoadingMore
                        ? h(
                            "p",
                            { className: "ms-muted ms-load-more" },
                            "Подгружаем клиентов…",
                          )
                        : null,
                      audienceHasMore
                        ? h(
                            "button",
                            {
                              type: "button",
                              className: "ms-btn",
                              disabled: audienceLoadingMore,
                              onClick: function () {
                                loadAudience({ append: true });
                              },
                            },
                            "Ещё клиенты",
                          )
                        : h(
                            "p",
                            { className: "ms-muted ms-load-more" },
                            "Показано " +
                              audiencePreview.length +
                              " из " +
                              audience,
                          ),
                    )
                  : h(
                      "p",
                      { className: "ms-muted" },
                      loading
                        ? "Загрузка аудитории…"
                        : "Нет клиентов под текущие фильтры / поиск.",
                    ),
              )
            : h(
                "p",
                { className: "ms-muted" },
                "Контакты скрыты" +
                  (audiencePreview.length
                    ? " · загружено " +
                      audiencePreview.length +
                      " из " +
                      audience
                    : "") +
                  ".",
              ),
        ),
      ),
      h(
        "div",
        { className: "ms-filter-tabs", role: "tablist" },
        h(
          "button",
          {
            type: "button",
            className: "ms-filter-tab" + (mode === "manual" ? " is-active" : ""),
            onClick: function () {
              setMode("manual");
            },
          },
          "Ручная",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-filter-tab" + (mode === "auto" ? " is-active" : ""),
            onClick: function () {
              setMode("auto");
            },
          },
          "Авто (AI)",
        ),
      ),
      h(
        "div",
        { className: "ms-compose-split" },
        h(
          "form",
          { className: "ms-campaign-form", onSubmit: createDraft },
          h(
            "div",
            { className: "ms-tg-account" },
            h(
              "div",
              { className: "ms-tg-account-head" },
              h("strong", null, "Личный Telegram (мои контакты)"),
              h(
                "button",
                {
                  type: "button",
                  className: "ms-link-btn",
                  disabled: tgBusy,
                  onClick: function () {
                    refreshTgUser(true);
                  },
                },
                "Проверить",
              ),
            ),
            h(
              "p",
              { className: "ms-muted ms-tg-account-status" },
              tgUser && tgUser.available === false
                ? h(
                    "span",
                    null,
                    "⚠ Нет MTProto-движка (telethon). ",
                    h(
                      "button",
                      {
                        type: "button",
                        className: "ms-link-btn",
                        disabled: tgBusy,
                        onClick: tgInstallRuntime,
                      },
                      tgBusy ? "Ставим…" : "Установить telethon",
                    ),
                  )
                : null,
              tgUser && tgUser.authorized
                ? "✓ Подключён " +
                    ((tgUser.user && (tgUser.user.username
                      ? "@" + tgUser.user.username
                      : tgUser.user.name)) ||
                      tgUser.phone ||
                      "аккаунт") +
                    " · контактов: " +
                    (tgUser.contacts_cached || 0)
                : tgUser && tgUser.session_saved
                  ? "Сессия сохранена, но не авторизована — войдите заново"
                  : "Не подключён — Bot API не видит ваш список контактов и не пишет первым",
            ),
            h(
              "div",
              { className: "ms-compose-actions" },
              h(
                "button",
                {
                  type: "button",
                  className: "ms-btn",
                  onClick: function () {
                    setTgOpen(!tgOpen);
                  },
                },
                tgOpen
                  ? "Скрыть"
                  : tgUser && tgUser.authorized
                    ? "Настройки входа"
                    : "Подключить аккаунт",
              ),
              !(tgUser && tgUser.authorized)
                ? h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn ms-btn-primary",
                      disabled: tgBusy,
                      onClick: tgBeginLogin,
                    },
                    "Войти",
                  )
                : null,
              tgUser && tgUser.authorized
                ? h(
                    "button",
                    {
                      type: "button",
                      className: "ms-link-btn",
                      disabled: tgBusy,
                      onClick: tgSyncContacts,
                    },
                    tgBusy ? "Синхронизируем…" : "Синхронизировать контакты",
                  )
                : null,
              tgUser && tgUser.authorized
                ? h(
                    "button",
                    {
                      type: "button",
                      className: "ms-link-btn",
                      disabled: tgBusy,
                      onClick: tgBeginLogin,
                    },
                    "Сменить аккаунт",
                  )
                : null,
              tgUser && tgUser.authorized
                ? h(
                    "button",
                    {
                      type: "button",
                      className: "ms-link-btn",
                      disabled: tgBusy,
                      onClick: tgLogout,
                    },
                    "Выйти",
                  )
                : null,
            ),
            tgOpen
              ? h(
                  "div",
                  { className: "ms-add-contact" },
                  tgStep === "phone"
                    ? h(
                        "div",
                        { className: "ms-add-contact" },
                        h(
                          "label",
                          null,
                          "Телефон аккаунта",
                          h("input", {
                            value: tgPhone,
                            placeholder: "+79991234567",
                            onChange: function (e) {
                              setTgPhone(e.target.value);
                            },
                          }),
                        ),
                        h(
                          "div",
                          { className: "ms-compose-actions" },
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-btn ms-btn-primary",
                              disabled: tgBusy,
                              onClick: tgLogin,
                            },
                            tgBusy ? "Отправляем код…" : "Получить код",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-link-btn",
                              disabled: tgBusy,
                              onClick: function () {
                                tgRestartLogin({ clearPhone: true });
                              },
                            },
                            "Сменить номер",
                          ),
                        ),
                        h(
                          "p",
                          { className: "ms-muted" },
                          "Если код не приходит: Selectel IP часто не достучится до Telegram. Нужен TELEGRAM_USER_GATEWAY_URL (Railway), TELEGRAM_PROXY или StringSession ниже.",
                        ),
                        h(
                          "label",
                          null,
                          "StringSession (обход блокировки)",
                          h("input", {
                            type: "password",
                            value: tgSession,
                            placeholder: "1BVtsOHwBu…",
                            onChange: function (e) {
                              setTgSession(e.target.value);
                            },
                          }),
                        ),
                        h(
                          "button",
                          {
                            type: "button",
                            className: "ms-btn",
                            disabled: tgBusy || !String(tgSession || "").trim(),
                            onClick: tgSaveSession,
                          },
                          "Сохранить сессию",
                        ),
                      )
                    : null,
                  tgStep === "code"
                    ? h(
                        "div",
                        { className: "ms-add-contact" },
                        h(
                          "p",
                          { className: "ms-muted" },
                          "Код для " +
                            (String(tgPhone || "").trim() || "текущего номера") +
                            ". Не тот номер — «Сменить номер».",
                        ),
                        h(
                          "label",
                          null,
                          "Код из Telegram",
                          h("input", {
                            value: tgCode,
                            placeholder: "12345",
                            onChange: function (e) {
                              setTgCode(e.target.value);
                            },
                          }),
                        ),
                        h(
                          "div",
                          { className: "ms-compose-actions" },
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-btn ms-btn-primary",
                              disabled: tgBusy,
                              onClick: tgSubmitCode,
                            },
                            tgBusy ? "Проверяем…" : "Войти",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-btn",
                              disabled: tgBusy || !String(tgPhone || "").trim(),
                              onClick: tgLogin,
                            },
                            "Отправить код ещё раз",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-link-btn",
                              disabled: tgBusy,
                              onClick: function () {
                                tgRestartLogin({ clearPhone: false });
                              },
                            },
                            "Начать вход заново",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-link-btn",
                              disabled: tgBusy,
                              onClick: function () {
                                tgRestartLogin({ clearPhone: true });
                              },
                            },
                            "Сменить номер",
                          ),
                        ),
                      )
                    : null,
                  tgStep === "password"
                    ? h(
                        "div",
                        { className: "ms-add-contact" },
                        h(
                          "label",
                          null,
                          "Облачный пароль (2FA)",
                          h("input", {
                            type: "password",
                            value: tgPassword,
                            onChange: function (e) {
                              setTgPassword(e.target.value);
                            },
                          }),
                        ),
                        h(
                          "div",
                          { className: "ms-compose-actions" },
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-btn ms-btn-primary",
                              disabled: tgBusy,
                              onClick: tgSubmitPassword,
                            },
                            tgBusy ? "Проверяем…" : "Подтвердить",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-link-btn",
                              disabled: tgBusy,
                              onClick: function () {
                                tgRestartLogin({ clearPhone: false });
                              },
                            },
                            "Начать вход заново",
                          ),
                          h(
                            "button",
                            {
                              type: "button",
                              className: "ms-link-btn",
                              disabled: tgBusy,
                              onClick: function () {
                                tgRestartLogin({ clearPhone: true });
                              },
                            },
                            "Сменить номер",
                          ),
                        ),
                      )
                    : null,
                  h(
                    "p",
                    { className: "ms-muted" },
                    "Как в обычном Telegram: телефон → код → облачный пароль (если включён). «Начать вход заново» / «Сменить номер» сбрасывают шаг. Сессия на сервере; код и пароль не сохраняются.",
                  ),
                )
              : null,
          ),
          h(
            "label",
            null,
            "Название",
            h("input", {
              value: title,
              required: true,
              onChange: function (e) {
                setTitle(e.target.value);
              },
            }),
          ),
          h(
            "label",
            null,
            "Канал отправки",
            h(
              "select",
              {
                value: channel,
                onChange: function (e) {
                  setChannel(e.target.value);
                },
              },
              h("option", { value: "telegram" }, "Telegram (личные)"),
              h("option", { value: "telegram_channel" }, "Telegram-канал"),
              h("option", { value: "whatsapp" }, "WhatsApp"),
            ),
          ),
          h(
            "label",
            null,
            "Имя продавца / подпись",
            h("input", {
              value: sellerName,
              disabled: !sellerLoaded,
              placeholder: "Напр. «Анна из Iris» или название магазина",
              onChange: function (e) {
                var v = e.target.value;
                setSellerName(v);
                persistSellerSettings(v, sellerFacts);
              },
            }),
          ),
          h(
            "label",
            null,
            "Факты о продавце / магазине",
            h("textarea", {
              rows: 3,
              value: sellerFacts,
              disabled: !sellerLoaded,
              placeholder:
                "Адрес, специализация, тон, что можно упомянуть…",
              onChange: function (e) {
                var v = e.target.value;
                setSellerFacts(v);
                persistSellerSettings(sellerName, v);
              },
            }),
          ),
          sendCards.length
            ? h(
                "div",
                { className: "ms-send-cards" },
                h("span", { className: "ms-muted" }, "Карточки в сообщении:"),
                sendCards.map(function (entry) {
                  return h(
                    "span",
                    { key: entry.name, className: "ms-send-card-chip" },
                    String(entry.name).slice(0, 40),
                    h(
                      "button",
                      {
                        type: "button",
                        className: "ms-link-btn",
                        title: "Убрать карточку из текста",
                        onClick: function () {
                          removeCardFromMessage(entry.name);
                        },
                      },
                      "✕",
                    ),
                  );
                }),
              )
            : null,
          h(
            "div",
            { className: "ms-msg-field" },
            h("span", { className: "ms-muted" }, "Текст сообщения"),
            h(
              "div",
              { className: "ms-msg-row" },
              h(
                "label",
                {
                  className: "ms-msg-plus",
                  title: sendImage ? "Заменить картинку" : "Вставить картинку в сообщение",
                },
                "+",
                h("input", {
                  type: "file",
                  accept: "image/*",
                  style: { display: "none" },
                  onChange: function (ev) {
                    var file = ev.target.files && ev.target.files[0];
                    ev.target.value = "";
                    if (!file) return;
                    if (file.size > 9 * 1024 * 1024) {
                      setError("Картинка больше 9 МБ — Telegram не примет.");
                      return;
                    }
                    var reader = new FileReader();
                    reader.onload = function () {
                      setSendImage({ name: file.name || "photo.jpg", dataUrl: String(reader.result || "") });
                    };
                    reader.readAsDataURL(file);
                  },
                }),
              ),
              h("textarea", {
              rows: 8,
              value: offer,
              placeholder: selectedClientId
                ? "Сгенерируйте AI или введите текст… Ctrl+V вставляет картинку, карточку можно перетащить из списка."
                : "Общий текст для фильтрованной аудитории… Ctrl+V вставляет картинку, карточку можно перетащить из списка.",
              onChange: function (e) {
                var v = e.target.value;
                setOffer(v);
                offerRef.current = v;
              },
              onDragOver: function (e) {
                var types = (e.dataTransfer && e.dataTransfer.types) || [];
                for (var i = 0; i < types.length; i++) {
                  if (types[i] === "application/x-ms-card") {
                    e.preventDefault();
                    return;
                  }
                }
              },
              onDrop: function (e) {
                var raw = e.dataTransfer && e.dataTransfer.getData("application/x-ms-card");
                if (!raw) return;
                e.preventDefault();
                try {
                  var card = JSON.parse(raw);
                  var addition = [card.name, card.url].filter(Boolean).join("\n");
                  if (addition) {
                    var current = String(offerRef.current || offer || "");
                    var nextText = current.trim() ? current.replace(/\s+$/, "") + "\n\n" + addition : addition;
                    setOffer(nextText);
                    offerRef.current = nextText;
                  }
                  if (card.image) {
                    setSendImage({ name: card.name || "card.jpg", url: card.image });
                  }
                } catch (_) {}
              },
              onPaste: function (e) {
                var items = (e.clipboardData && e.clipboardData.items) || [];
                var file = null;
                for (var i = 0; i < items.length; i++) {
                  if (items[i].type && items[i].type.indexOf("image/") === 0) {
                    file = items[i].getAsFile();
                    break;
                  }
                }
                if (!file) return;
                e.preventDefault();
                if (file.size > 9 * 1024 * 1024) {
                  setError("Картинка больше 9 МБ — Telegram не примет.");
                  return;
                }
                var reader = new FileReader();
                reader.onload = function () {
                  setSendImage({ name: file.name || "clipboard.png", dataUrl: String(reader.result || "") });
                };
                reader.readAsDataURL(file);
              },
            }),
            ),
          ),
          h(
            "div",
            { className: "ms-image-attach" },
            sendImage
              ? h(
                  "div",
                  { className: "ms-image-attach-preview" },
                  h("img", { src: sendImage.dataUrl || sendImage.url, alt: sendImage.name || "" }),
                  h(
                    "span",
                    { className: "ms-muted" },
                    (sendImage.name || "картинка") + " — уйдёт фотографией в Telegram",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-link-btn",
                      onClick: function () {
                        setSendImage(null);
                      },
                    },
                    "убрать",
                  ),
                )
              : h(
                  "span",
                  { className: "ms-muted" },
                  "«+» слева от текста вставляет картинку — она уйдёт фотографией при «Отправить в Telegram».",
                ),
          ),
          selectedClientId
            ? h(
                "div",
                { className: "ms-chat-panel" },
                h(
                  "div",
                  { className: "ms-chat-head" },
                  h("span", { className: "ms-ai-label" }, "Чат доработки текста"),
                  h(
                    "div",
                    { className: "ms-chips" },
                    Object.keys(CHAT_REFINE_MODELS).map(function (mk) {
                      var meta = CHAT_REFINE_MODELS[mk];
                      return h(
                        "button",
                        {
                          key: mk,
                          type: "button",
                          disabled: chatBusy,
                          className: "ms-chip" + (chatModel === mk ? " is-active" : ""),
                          title: "Переписать текст моделью " + meta.label,
                          onClick: function () {
                            setChatModel(mk);
                            if (String(offerRef.current || offer || "").trim()) {
                              sendChatTurn({
                                ask: "Перепиши этот текст той же сутью, но свежими словами.",
                                model: mk,
                              });
                            }
                          },
                        },
                        meta.label,
                      );
                    }),
                  ),
                ),
                chatTurns.length
                  ? h(
                      "div",
                      { className: "ms-chat-log" },
                      chatTurns.map(function (t, idx) {
                        return h(
                          "div",
                          { key: idx, className: "ms-chat-msg is-" + t.role },
                          t.content,
                        );
                      }),
                      chatBusy
                        ? h("div", { className: "ms-chat-msg is-assistant" }, "…")
                        : null,
                    )
                  : null,
                h(
                  "div",
                  { className: "ms-chat-input" },
                  h("input", {
                    type: "text",
                    value: chatInput,
                    placeholder: "Например: короче и теплее, добавь про баллы…",
                    onChange: function (e) {
                      setChatInput(e.target.value);
                    },
                    onKeyDown: function (e) {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        sendChatTurn();
                      }
                    },
                  }),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      disabled: chatBusy || !String(chatInput || "").trim(),
                      onClick: sendChatTurn,
                    },
                    chatBusy ? "…" : "➤",
                  ),
                ),
              )
            : null,
          skillPromptText
            ? h(
                "div",
                { className: "ms-skill-prompt" },
                h("span", null, "Сохранить отправленное сообщение в навык?"),
                h(
                  "button",
                  {
                    type: "button",
                    className: "ms-btn ms-btn-primary",
                    onClick: function () {
                      saveSkill(skillPromptText);
                    },
                  },
                  "Да, в навык",
                ),
                h(
                  "button",
                  {
                    type: "button",
                    className: "ms-btn",
                    onClick: function () {
                      setSkillPromptText("");
                    },
                  },
                  "Нет",
                ),
              )
            : null,
          actionStatus
            ? h("p", { className: "ms-action-status" }, actionStatus)
            : null,
          h(
            "label",
            { className: "ms-check" },
            h("input", {
              type: "checkbox",
              checked: personalize,
              disabled: !!selectedClientId,
              onChange: function (e) {
                setPersonalize(!!e.target.checked);
              },
            }),
            "Персонализировать по клиентам (очередь — позже)",
          ),
          h(
            "div",
            { className: "ms-compose-actions" },
            selectedClientId
              ? h(
                  "button",
                  {
                    type: "button",
                    className: "ms-btn",
                    disabled: generating || rewriting || checkingSanity,
                    onClick: function () {
                      loadOutreach(selectedClientId, channel);
                    },
                  },
                  generating ? "Генерация…" : "Сгенерировать AI",
                )
              : null,
            h(
              "span",
              { className: "ms-insert-wrap" },
              h(
                "button",
                {
                  type: "button",
                  className: "ms-btn",
                  onClick: function () {
                    setInsertMenuOpen(!insertMenuOpen);
                  },
                },
                "Вставить ▾",
              ),
              insertMenuOpen
                ? h(
                    "div",
                    { className: "ms-insert-menu" },
                    h(
                      "button",
                      { type: "button", className: "ms-insert-item", onClick: openCardPicker },
                      "Вставить из карточки…",
                    ),
                    h(
                      "label",
                      { className: "ms-insert-item" },
                      "Вставить другое…",
                      h("input", {
                        type: "file",
                        accept: "image/*",
                        style: { display: "none" },
                        onChange: function (ev) {
                          setInsertMenuOpen(false);
                          var file = ev.target.files && ev.target.files[0];
                          ev.target.value = "";
                          if (!file) return;
                          if (file.size > 9 * 1024 * 1024) {
                            setError("Картинка больше 9 МБ — Telegram не примет.");
                            return;
                          }
                          var reader = new FileReader();
                          reader.onload = function () {
                            setSendImage({ name: file.name || "photo.jpg", dataUrl: String(reader.result || "") });
                          };
                          reader.readAsDataURL(file);
                        },
                      }),
                    ),
                  )
                : null,
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-btn",
                disabled:
                  rewriting ||
                  generating ||
                  checkingSanity ||
                  !String(offer || "").trim(),
                onClick: humanizeDraft,
              },
              rewriting ? "Переписываем…" : "Продающе и по-человечески",
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-btn",
                disabled:
                  checkingSanity ||
                  generating ||
                  rewriting ||
                  !String(offer || "").trim(),
                onClick: runSanityCheck,
              },
              checkingSanity ? "Проверяем…" : "Проверить смысл",
            ),
            selectedClientId
              ? h(
                  "button",
                  {
                    type: "button",
                    className: "ms-btn",
                    disabled:
                      checkingSanity ||
                      generating ||
                      rewriting ||
                      !String(offer || "").trim(),
                    onClick: markSentToConversation,
                  },
                  "Отправить в Telegram",
                )
              : null,
            h(
              "button",
              {
                type: "submit",
                className: "ms-btn ms-btn-primary",
                disabled:
                  saving ||
                  loading ||
                  generating ||
                  rewriting ||
                  checkingSanity ||
                  audience < 1,
              },
              selectedClientId
                ? "Создать 1:1 черновик"
                : "Массовый черновик (" + audience + ")",
            ),
          ),
          genSource
            ? h("p", { className: "ms-muted" }, "Источник текста: " + genSource)
            : null,
        ),
        h(FactsPanel, { facts: facts, notes: groundingNotes, sanity: sanity }),
        h(CardsSideList, {
          onAdd: addCardToMessage,
          onRemove: removeCardFromMessage,
          addedNames: sendCards.map(function (entry) {
            return entry.name;
          }),
        }),
        pickerOpen
          ? h(
              React.Fragment,
              null,
              h("div", {
                className: "ms-drawer-overlay",
                onClick: function () {
                  setPickerOpen(false);
                  setPickerCard(null);
                },
              }),
              h(
                "aside",
                { className: "ms-drawer ms-photo-picker" },
                h(
                  "div",
                  { className: "ms-drawer-head" },
                  pickerCard
                    ? h(
                        "button",
                        {
                          type: "button",
                          className: "ms-btn",
                          onClick: function () {
                            setPickerCard(null);
                          },
                        },
                        "← Карточки",
                      )
                    : h("strong", null, "Выберите карточку"),
                  pickerCard ? h("strong", { className: "ms-photo-picker-title" }, String(pickerCard.name || "").slice(0, 48)) : null,
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      onClick: function () {
                        setPickerOpen(false);
                        setPickerCard(null);
                      },
                    },
                    "Закрыть",
                  ),
                ),
                h(
                  "div",
                  { className: "ms-photo-picker-body" },
                  !pickerCards ? h("p", { className: "ms-muted" }, "Загружаем карточки…") : null,
                  pickerCards && !pickerCard
                    ? h(
                        "div",
                        { className: "ms-photo-picker-grid" },
                        pickerCards.map(function (card, idx) {
                          return h(
                            "button",
                            {
                              key: String(card.name || idx),
                              type: "button",
                              className: "ms-photo-pick-card",
                              onClick: function () {
                                setPickerCard(card);
                              },
                            },
                            card.image
                              ? h("img", { src: card.image, alt: card.name || "", loading: "lazy" })
                              : h("span", { className: "ms-muted" }, "нет фото"),
                            h("span", { className: "ms-photo-pick-name" }, String(card.name || "—").slice(0, 60)),
                          );
                        }),
                      )
                    : null,
                  pickerCard
                    ? h(
                        "div",
                        { className: "ms-photo-picker-grid" },
                        cardPhotos(pickerCard).map(function (src, idx) {
                          return h(
                            "button",
                            {
                              key: idx,
                              type: "button",
                              className: "ms-photo-pick-card",
                              title: "Вставить это фото в сообщение",
                              onClick: function () {
                                pickPhoto(pickerCard, src);
                              },
                            },
                            h("img", { src: src, alt: "", loading: "lazy" }),
                          );
                        }),
                      )
                    : null,
                  pickerCard && !cardPhotos(pickerCard).length
                    ? h("p", { className: "ms-muted" }, "У карточки нет фото.")
                    : null,
                ),
              ),
            )
          : null,
      ),
      error ? h("div", { className: "ms-error" }, error) : null,
      h("h2", { className: "ms-section-title" }, "История отправок"),
      h(
        "section",
        { className: "ms-mass-panel ms-history-panel" },
        h(
          "div",
          { className: "ms-card-head" },
          h(
            "p",
            { className: "ms-muted" },
            "Кому, что и с каким статусом ушло (массовые + одиночные).",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-link-btn",
              disabled: sentFeedLoading,
              onClick: loadSendHistory,
            },
            sentFeedLoading ? "Обновляем…" : "Обновить",
          ),
        ),
        h(
          "div",
          { className: "ms-mass-log" },
          !(sentFeed || []).length
            ? h("p", { className: "ms-muted" }, "Исходящих пока нет.")
            : sentFeed.map(function (m, idx) {
                return h(
                  "div",
                  {
                    key: String(m.client_id || "") + "-" + String(m.ts || idx),
                    className:
                      "ms-mass-log-row is-" + (m.status === "delivered" ? "ok" : "sending"),
                  },
                  h(
                    "span",
                    null,
                    (m.client_name || (m.tg_nick ? "@" + m.tg_nick : m.client_id) || "—") +
                      (m.text
                        ? " — " + String(m.text).slice(0, 120) + (String(m.text).length > 120 ? "…" : "")
                        : ""),
                  ),
                  h(
                    "span",
                    { className: "ms-muted" },
                    (m.status === "delivered" ? "✓ доставлено" : "✎ записано (не доставлено)") +
                      (m.ts ? " · " + String(m.ts).slice(0, 16).replace("T", " ") : ""),
                  ),
                );
              }),
        ),
        (historyJobs || []).map(function (job) {
          return h(
            "div",
            { key: job.id, className: "ms-history-job" },
            h(
              "div",
              { className: "ms-history-job-head" },
              h(
                "span",
                { className: "ms-muted" },
                String(job.created_at || "").slice(0, 16).replace("T", " "),
              ),
              h("span", { className: "ms-history-job-msg" }, job.message_preview || "—"),
              h(
                "span",
                { className: "ms-muted" },
                String(job.status || "") +
                  " · " +
                  String(job.total || 0) +
                  " получ. · ✓" +
                  String(job.sent_ok || 0) +
                  " · ✕" +
                  String(job.sent_failed || 0),
              ),
            ),
          );
        }),
      ),
      h("h2", { className: "ms-section-title" }, "Черновики"),
      !campaigns.length
        ? h(
            "p",
            { className: "ms-muted" },
            loading ? "Загрузка…" : "Пока нет рассылок.",
          )
        : h(
            "ul",
            { className: "ms-campaign-list" },
            campaigns.map(function (c) {
              return h(
                "li",
                { key: c.id, className: "ms-campaign-card" },
                h(
                  "div",
                  { className: "ms-campaign-card-head" },
                  h("strong", null, c.title),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn",
                      onClick: function () {
                        remove(c.id);
                      },
                    },
                    "Удалить",
                  ),
                ),
                h(
                  "div",
                  { className: "ms-muted" },
                  c.channel +
                    " · " +
                    c.mode +
                    " · аудитория " +
                    (c.audience_count || 0) +
                    (c.client_name ? " · " + c.client_name : "") +
                    " · " +
                    (c.status || "draft") +
                    (c.ai_source ? " · AI " + c.ai_source : "") +
                    (c.personalize_pending
                      ? " · персонализация в очереди"
                      : ""),
                ),
                c.offer
                  ? h("p", { className: "ms-campaign-offer" }, c.offer)
                  : null,
              );
            }),
          ),
      tgProgress
        ? h(
            "div",
            {
              className: "ms-modal-backdrop ms-tg-progress-backdrop",
              role: "status",
            },
            h(
              "div",
              {
                className: "ms-modal ms-tg-progress",
                onClick: function (e) {
                  e.stopPropagation();
                },
              },
              h("div", {
                className: "ms-tg-progress-spinner",
                "aria-hidden": "true",
              }),
              h("h3", null, tgProgress.title),
              h("p", { className: "ms-muted" }, tgProgress.detail),
              h(
                "p",
                { className: "ms-muted" },
                "Не закрывайте вкладку — ждём ответ Telegram / Telethon.",
              ),
            ),
          )
        : null,
    );
  }

  var DASH_METRICS = ["turnover", "revenue", "margin", "orders", "avg_check"];
  var DASH_METRIC_RU = {
    turnover: "Оборот",
    revenue: "Выручка",
    margin: "Маржа",
    orders: "Заказы",
    avg_check: "Ср чек",
    commission: "Комиссия",
    new_clients: "Новые клиенты",
    second_purchase: "Вторая покупка",
    third_purchase: "Третья покупка",
    regular_clients: "Постоянные клиенты",
    platform_commission: "Комиссия площадки",
  };
  var FLOWWOW_ROWS = [
    "turnover",
    "orders",
    "avg_check",
    "commission",
    "revenue",
    "new_clients",
    "second_purchase",
    "third_purchase",
    "regular_clients",
    "platform_commission",
  ];

  function dashMoney(n) {
    if (n == null || isNaN(Number(n))) return "—";
    return Number(n).toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  }
  function dashPct(n) {
    if (n == null || isNaN(Number(n))) return "";
    var pct = Number(n) * 100;
    var sign = pct > 0 ? "+" : "";
    return sign + pct.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + "%";
  }
  function dashPctEl(n) {
    var text = dashPct(n);
    if (!text) return null;
    var cls = n > 0.0005 ? "is-up" : n < -0.0005 ? "is-down" : "is-flat";
    return h("span", { className: "ms-dash-pct " + cls }, text);
  }
  function dashMetricVal(metric, n) {
    if (metric === "platform_commission") return n == null ? "—" : dashPct(n);
    if (
      metric === "orders" ||
      metric === "new_clients" ||
      metric === "second_purchase" ||
      metric === "third_purchase" ||
      metric === "regular_clients"
    ) {
      return n == null ? "—" : String(Math.round(Number(n)));
    }
    return dashMoney(n);
  }

  function DashTableTools(query, setQuery, sortLabel, onToggle, placeholder) {
    return h(
      "div",
      { className: "ms-dash-table-tools" },
      h("input", {
        type: "search",
        className: "ms-dash-filter",
        value: query,
        placeholder: placeholder,
        onChange: function (e) {
          setQuery(e.target.value);
        },
      }),
      h(
        "button",
        { type: "button", className: "ms-link-btn", onClick: onToggle },
        sortLabel,
      ),
    );
  }

  function dashFilterChannels(channels, q) {
    var needle = String(q || "").trim().toLowerCase();
    if (!needle) return channels || [];
    return (channels || []).filter(function (ch) {
      return (
        String(ch.label || "").toLowerCase().indexOf(needle) >= 0 ||
        String(ch.key || "").toLowerCase().indexOf(needle) >= 0
      );
    });
  }

  function dashSortChannels(channels, dir, last) {
    var sign = dir === "asc" ? 1 : -1;
    return (channels || []).slice().sort(function (a, b) {
      var av = Number((a.turnover || [])[last] || 0);
      var bv = Number((b.turnover || [])[last] || 0);
      return (av - bv) * sign;
    });
  }

  function DashMatrix(props) {
    var matrix = props.matrix;
    var title = props.title;
    const [query, setQuery] = useState("");
    const [sortDir, setSortDir] = useState("desc");
    var periods = (matrix && matrix.periods) || [];
    var last = Math.max(0, periods.length - 1);
    var channels = dashSortChannels(
      dashFilterChannels((matrix && matrix.channels) || [], query),
      sortDir,
      last,
    );
    var totals = matrix && matrix.totals;
    if (!periods.length) return h("p", { className: "ms-muted" }, "Нет оплаченных заказов за период.");
    var body = [];
    channels.forEach(function (ch) {
      DASH_METRICS.forEach(function (metric, mi) {
        var cells = [
          mi === 0
            ? h(
                "th",
                { className: "ms-dash-sticky", rowSpan: DASH_METRICS.length, key: "ch" },
                ch.label,
                h(
                  "div",
                  { className: "ms-muted" },
                  "комиссия " + ((Number(ch.commission_rate || 0) * 100).toFixed(1)) + "%",
                ),
              )
            : null,
          h("td", { className: "ms-dash-sticky-2 ms-dash-metric", key: "m" }, DASH_METRIC_RU[metric]),
        ];
        periods.forEach(function (_p, i) {
          var series = ch[metric] || [];
          cells.push(
            h(
              "td",
              { className: "ms-dash-num", key: ch.key + metric + i },
              h("div", null, dashMetricVal(metric, series[i])),
              dashPctEl(ch.growth && ch.growth[metric] ? ch.growth[metric][i] : null),
            ),
          );
        });
        body.push(
          h(
            "tr",
            { key: ch.key + metric, className: mi === 0 ? "ms-dash-channel-start" : undefined },
            cells.filter(Boolean),
          ),
        );
      });
    });
    if (totals) {
      DASH_METRICS.forEach(function (metric, mi) {
        var cells = [
          mi === 0
            ? h("th", { className: "ms-dash-sticky", rowSpan: DASH_METRICS.length, key: "t" }, "Итого")
            : null,
          h("td", { className: "ms-dash-sticky-2 ms-dash-metric", key: "m" }, DASH_METRIC_RU[metric]),
        ];
        periods.forEach(function (_p, i) {
          cells.push(
            h(
              "td",
              { className: "ms-dash-num", key: "tot" + metric + i },
              h("div", null, dashMetricVal(metric, (totals[metric] || [])[i])),
              dashPctEl(totals.growth && totals.growth[metric] ? totals.growth[metric][i] : null),
            ),
          );
        });
        body.push(
          h(
            "tr",
            { key: "total-" + metric, className: mi === 0 ? "ms-dash-total-start" : "ms-dash-total" },
            cells.filter(Boolean),
          ),
        );
      });
    }
    return h(
      "div",
      null,
      DashTableTools(
        query,
        setQuery,
        "Сорт: оборот " + (sortDir === "desc" ? "↓" : "↑"),
        function () {
          setSortDir(sortDir === "desc" ? "asc" : "desc");
        },
        "Фильтр канала…",
      ),
      h(
      "div",
      { className: "ms-table-wrap ms-dash-table-wrap" },
      h(
        "table",
        { className: "ms-table ms-dash-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { className: "ms-dash-sticky" }, title),
            h("th", { className: "ms-dash-sticky-2" }, "Показатель"),
            periods.map(function (p) {
              return h("th", { key: p.id }, p.label);
            }),
          ),
        ),
        h("tbody", null, body),
      ),
      ),
    );
  }

  function DashDays(props) {
    var analytics = props.analytics || {};
    const [query, setQuery] = useState("");
    const [sortDir, setSortDir] = useState("desc");
    var keys = (analytics.by_day && analytics.by_day.channels) || [];
    var labels = analytics.channel_labels || {};
    var needle = String(query || "").trim().toLowerCase();
    var visKeys = needle
      ? keys.filter(function (k) {
          return (
            String(labels[k] || "").toLowerCase().indexOf(needle) >= 0 ||
            String(k).toLowerCase().indexOf(needle) >= 0
          );
        })
      : keys;
    if (!visKeys.length) visKeys = keys;
    var rows = ((analytics.by_day && analytics.by_day.rows) || []).filter(function (r) {
      if (r.kind !== "month") {
        var active = visKeys.some(function (k) {
          return Number((r.channels && r.channels[k] && r.channels[k].orders) || 0) > 0;
        });
        if (!active) return false;
      }
      if (!needle) return true;
      if (String(r.label || "").toLowerCase().indexOf(needle) >= 0 || String(r.id).indexOf(needle) >= 0) return true;
      return visKeys.length !== keys.length;
    });
    rows = rows.slice().sort(function (a, b) {
      return String(a.id).localeCompare(String(b.id)) * (sortDir === "asc" ? 1 : -1);
    });
    keys = visKeys;
    if (!rows.length)
      return h(
        "div",
        null,
        DashTableTools(
          query,
          setQuery,
          "Сорт: дата " + (sortDir === "desc" ? "↓" : "↑"),
          function () {
            setSortDir(sortDir === "desc" ? "asc" : "desc");
          },
          "Фильтр даты или канала…",
        ),
        h("p", { className: "ms-muted" }, "Нет оплаченных заказов за выбранные дни."),
      );
    return h(
      "div",
      null,
      DashTableTools(
        query,
        setQuery,
        "Сорт: дата " + (sortDir === "desc" ? "↓" : "↑"),
        function () {
          setSortDir(sortDir === "desc" ? "asc" : "desc");
        },
        "Фильтр даты или канала…",
      ),
      h(
      "div",
      { className: "ms-table-wrap ms-dash-table-wrap" },
      h(
        "table",
        { className: "ms-table ms-dash-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { className: "ms-dash-sticky", rowSpan: 2 }, "Дата"),
            keys.map(function (k) {
              return h("th", { key: k, colSpan: 2 }, labels[k] || k);
            }),
          ),
          h(
            "tr",
            null,
            keys.flatMap(function (k) {
              return [h("th", { key: k + "o" }, "Заказы"), h("th", { key: k + "t" }, "Оборот")];
            }),
          ),
        ),
        h(
          "tbody",
          null,
          rows.map(function (r) {
            return h(
              "tr",
              { key: r.id, className: r.kind === "month" ? "ms-dash-month-row" : undefined },
              h("th", { className: "ms-dash-sticky" }, r.label),
              keys.flatMap(function (k) {
                var cell = (r.channels && r.channels[k]) || {};
                return [
                  h("td", { className: "ms-dash-num", key: r.id + k + "o" }, cell.orders || "—"),
                  h("td", { className: "ms-dash-num", key: r.id + k + "t" }, dashMoney(cell.turnover)),
                ];
              }),
            );
          }),
        ),
      ),
      ),
    );
  }

  function DashFlowwow(props) {
    var analytics = props.analytics || {};
    var fw = analytics.flowwow || {};
    var periods = fw.periods || [];
    var metrics = fw.metrics || {};
    var years = Object.keys(fw.year_totals || {}).sort();
    var growth = metrics.growth || {};
    if (!periods.length) return h("p", { className: "ms-muted" }, "Нет заказов FlowWow.");
    return h(
      "div",
      { className: "ms-table-wrap ms-dash-table-wrap" },
      h(
        "table",
        { className: "ms-table ms-dash-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { className: "ms-dash-sticky" }, "Показатель"),
            periods.map(function (p) {
              return h("th", { key: p.id }, p.label);
            }),
            years.map(function (y) {
              return h("th", { key: "y" + y }, "Итого " + y);
            }),
          ),
        ),
        h(
          "tbody",
          null,
          FLOWWOW_ROWS.map(function (metric) {
            var series = metrics[metric] || [];
            return h(
              "tr",
              { key: metric },
              h("th", { className: "ms-dash-sticky ms-dash-metric" }, DASH_METRIC_RU[metric]),
              periods.map(function (_p, i) {
                return h(
                  "td",
                  { className: "ms-dash-num", key: metric + i },
                  h("div", null, dashMetricVal(metric, series[i])),
                  dashPctEl(growth[metric] ? growth[metric][i] : null),
                );
              }),
              years.map(function (y) {
                var block = (fw.year_totals && fw.year_totals[y]) || {};
                return h(
                  "td",
                  { className: "ms-dash-num", key: metric + y },
                  dashMetricVal(metric, block[metric]),
                );
              }),
            );
          }),
        ),
      ),
    );
  }

  var CH_COLORS = {
    yandex_market: "#e8b86d",
    flavy: "#e39ac4",
    yandex_eda: "#e89b6c",
    ozon: "#8fb0e4",
    flowwow: "#8fd0b8",
    floday: "#b4c98a",
    skyloft: "#c4b4ea",
    direct: "#d8b4fe",
    other: "#94a3b8",
  };
  function chColor(k) {
    return CH_COLORS[k] || "#94a3b8";
  }
  var _msPluginSrc = (document.currentScript && document.currentScript.src) || "";
  function pluginVendorUrl(file) {
    var el = document.currentScript || document.querySelector('script[data-hermes-plugin="moysklad"]');
    var src = (el && el.src) || _msPluginSrc || "";
    if (src) {
      return src.replace(/\/[^/]+(\?.*)?$/, "/vendor/" + file);
    }
    return "/dashboard-plugins/moysklad/dist/vendor/" + file;
  }
  var ECHARTS_SRC = pluginVendorUrl("echarts.min.js");
  var PLOTLY_SRC = pluginVendorUrl("plotly.min.js");

  function loadChartLib(src, globalName) {
    return new Promise(function (resolve, reject) {
      if (window[globalName]) return resolve(window[globalName]);
      var s = document.querySelector('script[data-ms-lib="' + globalName + '"]');
      if (s) {
        if (s.getAttribute("data-ms-loaded") === "1") return resolve(window[globalName]);
        s.addEventListener("load", function () { resolve(window[globalName]); });
        s.addEventListener("error", reject);
        return;
      }
      s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.setAttribute("data-ms-lib", globalName);
      s.onload = function () {
        s.setAttribute("data-ms-loaded", "1");
        resolve(window[globalName]);
      };
      s.onerror = function () {
        reject(new Error("failed to load " + src));
      };
      document.head.appendChild(s);
    });
  }

  function dashSeries(ch, metric) {
    return (ch[metric] || []).map(function (v) { return Number(v) || 0; });
  }

  function MsEChart(props) {
    var elRef = useRef(null);
    const [err, setErr] = useState("");
    var optKey = JSON.stringify(props.option || {});
    useEffect(
      function () {
        var chart;
        var dead = false;
        var ro;
        setErr("");
        loadChartLib(ECHARTS_SRC, "echarts")
          .then(function (echarts) {
            if (dead || !elRef.current || !echarts) {
              if (!echarts && !dead) setErr("ECharts не загрузился");
              return;
            }
            chart = echarts.init(elRef.current);
            chart.setOption(props.option || {}, true);
            requestAnimationFrame(function () { if (chart) chart.resize(); });
            ro = new ResizeObserver(function () { if (chart) chart.resize(); });
            ro.observe(elRef.current);
          })
          .catch(function () {
            if (!dead) setErr("Нет vendor/echarts.min.js — обновите страницу");
          });
        return function () {
          dead = true;
          if (ro) ro.disconnect();
          if (chart) chart.dispose();
        };
      },
      [optKey],
    );
    if (err) return h("p", { className: "ms-error" }, err);
    return h("div", { className: "ms-dash-echart", ref: elRef });
  }

  function MsPlotlyHeat(props) {
    var elRef = useRef(null);
    var key = JSON.stringify({ x: props.x, y: props.y, z: props.z });
    useEffect(
      function () {
        var el = elRef.current;
        var dead = false;
        loadChartLib(PLOTLY_SRC, "Plotly").then(function (Plotly) {
          if (dead || !el || !Plotly) return;
          Plotly.react(
            el,
            [
              {
                type: "heatmap",
                x: props.x,
                y: props.y,
                z: props.z,
                colorscale: [
                  [0, "#2f1236"],
                  [0.45, "#7c3a8c"],
                  [1, "#e8b86d"],
                ],
                hoverongaps: false,
              },
            ],
            {
              margin: { l: 110, r: 40, t: 24, b: 56 },
              paper_bgcolor: "rgba(0,0,0,0)",
              plot_bgcolor: "#2f1236",
              font: { color: "#f0daf5" },
            },
            { responsive: true, displaylogo: false },
          );
        }).catch(function () {
          return loadChartLib(ECHARTS_SRC, "echarts").then(function (echarts) {
            if (dead || !el || !echarts) return;
            var y = props.y || [];
            var x = props.x || [];
            var z = props.z || [];
            var data = [];
            var max = 1;
            for (var yi = 0; yi < y.length; yi++) {
              for (var xi = 0; xi < x.length; xi++) {
                var v = (z[yi] && z[yi][xi]) || 0;
                data.push([xi, yi, v]);
                if (v > max) max = v;
              }
            }
            var chart = echarts.init(el);
            chart.setOption({
              tooltip: { position: "top" },
              grid: { left: 110, right: 24, top: 8, bottom: 48 },
              xAxis: { type: "category", data: x },
              yAxis: { type: "category", data: y },
              visualMap: { min: 0, max: max, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#2f1236", "#7c3a8c", "#e8b86d"] } },
              series: [{ type: "heatmap", data: data }],
            }, true);
          });
        });
        return function () {
          dead = true;
          if (el && window.Plotly) window.Plotly.purge(el);
        };
      },
      [key],
    );
    return h("div", { className: "ms-dash-plotly", ref: elRef });
  }

  function DashCharts(props) {
    var analytics = props.analytics || {};
    const [metric, setMetric] = useState("turnover");
    const [scope, setScope] = useState("month");
    const [hidden, setHidden] = useState({});
    const [active, setActive] = useState(null);
    var insights = analytics.insights || [];
    var matrix = scope === "week" ? analytics.by_week : analytics.by_month;
    var periods = (matrix && matrix.periods) || [];
    var allCh = (matrix && matrix.channels) || [];
    var channels = allCh.filter(function (ch) { return !hidden[ch.key]; });
    if (!periods.length) {
      return h("p", { className: "ms-muted" }, "Мало данных для графиков.");
    }
    var cats = periods.map(function (p) { return p.label; });
    var selected = {};
    allCh.forEach(function (ch) { selected[ch.label] = !hidden[ch.key]; });
    var lineSeries = channels.map(function (ch) {
      return {
        name: ch.label,
        type: "line",
        smooth: true,
        data: dashSeries(ch, metric),
        itemStyle: { color: chColor(ch.key) },
      };
    });
    var stackSeries = channels.map(function (ch) {
      return {
        name: ch.label,
        type: "bar",
        stack: "mix",
        data: dashSeries(ch, metric),
        itemStyle: { color: chColor(ch.key) },
      };
    });
    var last = periods.length - 1;
    var pieData = channels
      .map(function (ch) {
        return { name: ch.label, value: dashSeries(ch, metric)[last] || 0, itemStyle: { color: chColor(ch.key) } };
      })
      .filter(function (d) { return d.value > 0; });
    var heatZ = channels.map(function (ch) { return dashSeries(ch, metric); });
    var heatY = channels.map(function (ch) { return ch.label; });
    var baseOpt = {
      backgroundColor: "transparent",
      textStyle: { color: "#f4ede4" },
      tooltip: { trigger: "axis", backgroundColor: "#3a1840", borderColor: "#7c3a8c" },
      legend: { type: "scroll", textStyle: { color: "#f0daf5" }, selected: selected },
      grid: { left: 56, right: 16, top: 40, bottom: 56 },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
      xAxis: { type: "category", data: cats, axisLabel: { color: "#f0daf5" } },
      yAxis: { type: "value", axisLabel: { color: "#f0daf5" }, splitLine: { lineStyle: { color: "#592466" } } },
    };
    function applyInsight(row) {
      setActive(row.id);
      if (row.metric) setMetric(row.metric);
      if (row.scope) setScope(row.scope);
      if (row.channel) {
        var next = {};
        allCh.forEach(function (ch) { if (ch.key !== row.channel) next[ch.key] = true; });
        setHidden(next);
      } else setHidden({});
    }
    function toggle(key) {
      setActive(null);
      setHidden(function (prev) {
        var next = Object.assign({}, prev);
        if (next[key]) delete next[key];
        else next[key] = true;
        return next;
      });
    }
    return h(
      "div",
      { className: "ms-dash-board" },
      insights.length
        ? h(
            "div",
            { className: "ms-dash-takes" },
            insights.map(function (row) {
              return h(
                "button",
                {
                  type: "button",
                  key: row.id,
                  className: "ms-dash-take is-" + (row.tone || "info") + (active === row.id ? " is-active" : ""),
                  onClick: function () { applyInsight(row); },
                },
                h("strong", null, row.title),
                h("span", null, row.body),
              );
            }),
          )
        : h("p", { className: "ms-muted" }, "Hot take появятся при двух периодах с заказами."),
      h(
        "div",
        { className: "ms-dash-chart-toolbar" },
        h(
          "div",
          { className: "ms-filter-tabs" },
          [
            ["turnover", "Оборот"],
            ["revenue", "Выручка"],
            ["orders", "Заказы"],
            ["avg_check", "Ср чек"],
          ].map(function (opt) {
            return h(
              "button",
              {
                type: "button",
                key: opt[0],
                className: "ms-filter-tab" + (metric === opt[0] ? " is-active" : ""),
                onClick: function () { setMetric(opt[0]); },
              },
              opt[1],
            );
          }),
        ),
        h(
          "div",
          { className: "ms-filter-tabs" },
          h("button", { type: "button", className: "ms-filter-tab" + (scope === "month" ? " is-active" : ""), onClick: function () { setScope("month"); } }, "Месяцы"),
          h("button", { type: "button", className: "ms-filter-tab" + (scope === "week" ? " is-active" : ""), onClick: function () { setScope("week"); } }, "Недели"),
        ),
        h("button", { type: "button", className: "ms-link-btn", onClick: function () { setHidden({}); setActive(null); } }, "Все каналы"),
      ),
      h(
        "div",
        { className: "ms-dash-legend" },
        allCh.map(function (ch) {
          return h(
            "button",
            { type: "button", key: ch.key, className: "ms-dash-leg" + (hidden[ch.key] ? " is-off" : ""), onClick: function () { toggle(ch.key); } },
            h("i", { style: { background: chColor(ch.key) } }),
            ch.label,
          );
        }),
      ),
      h(
        "div",
        { className: "ms-dash-chart-grid" },
        h("section", null, h("h3", null, "Динамика · ECharts"), h("div", { className: "ms-dash-chart" }, h(MsEChart, { option: Object.assign({}, baseOpt, { series: lineSeries }) }))),
        h("section", null, h("h3", null, "Состав · ECharts"), h("div", { className: "ms-dash-chart" }, h(MsEChart, { option: Object.assign({}, baseOpt, { series: stackSeries }) }))),
      ),
      h(
        "div",
        { className: "ms-dash-chart-grid" },
        h("section", null, h("h3", null, "Доли · ECharts"), h("div", { className: "ms-dash-chart" }, h(MsEChart, { option: { backgroundColor: "transparent", tooltip: { trigger: "item" }, series: [{ type: "pie", radius: ["38%", "68%"], data: pieData }] } }))),
        h("section", null, h("h3", null, "Тепловая карта · Plotly"), h("div", { className: "ms-dash-chart" }, h(MsPlotlyHeat, { x: cats, y: heatY, z: heatZ }))),
      ),
    );
  }

  function DashboardPage() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [tab, setTab] = useState("charts");
    const [reportChatOpen, setReportChatOpen] = useState(false);

    function load() {
      setLoading(true);
      setError("");
      api("/dashboard")
        .then(function (payload) {
          setData(payload);
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        })
        .finally(function () {
          setLoading(false);
        });
    }

    useEffect(function () {
      load();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    var c = (data && data.clients) || {};
    var sends = (data && data.sends) || {};
    var job = data && data.last_mass_job;
    var analytics = (data && data.analytics) || {};
    var kpi = analytics.kpi || {};
    var tiles = [
      ["Клиентов всего", c.total],
      ["Физ. лица", c.individual],
      ["Юр. лица", c.legal],
      ["ИП", c.entrepreneur],
      ["С телефоном", c.with_phone],
      ["С баллами", c.with_loyalty],
      ["VIP", c.vip],
      ["✓ есть TG", c.tg_active],
      ["TG не найден", c.tg_not_found],
      ["TG не проверен", c.tg_unchecked],
      ["Отправок за 24ч", sends.last_24h],
      ["Отправок за 7д", sends.last_7d],
      ["✓ доставлено (7д)", sends.delivered_7d],
      ["✎ не доставлено (7д)", sends.recorded_7d],
    ];
    var tabs = [
      ["charts", "Графики"],
      ["month", "Месяц"],
      ["week", "Неделя"],
      ["day", "По дням"],
      ["flowwow", "Флау"],
      ["overview", "База"],
    ];

    var overview = [
      h(
        "div",
        { className: "ms-stats-grid ms-dashboard-grid", key: "tiles" },
        tiles.map(function (t) {
          return h(
            "div",
            { key: t[0] },
            h("div", { className: "ms-stat-val" }, String(t[1] != null ? t[1] : "—")),
            h("div", { className: "ms-muted" }, t[0]),
          );
        }),
      ),
      h(
        "section",
        { className: "ms-mass-panel", key: "job" },
        h("strong", null, "Последняя массовая рассылка"),
        job
          ? h(
              "p",
              { className: "ms-muted" },
              String(job.created_at || "").slice(0, 16).replace("T", " ") +
                " · " +
                String(job.status || "—") +
                " · " +
                String(job.total || 0) +
                " получ. · ✓" +
                String(job.sent_ok || 0) +
                " · ✕" +
                String(job.sent_failed || 0),
            )
          : h("p", { className: "ms-muted" }, "Рассылок пока не было."),
      ),
      h(
        "section",
        { className: "ms-mass-panel", key: "sends" },
        h("strong", null, "Последние отправки"),
        (sends.recent || []).length
          ? h(
              "div",
              { className: "ms-mass-log" },
              sends.recent.map(function (m, idx) {
                return h(
                  "div",
                  {
                    key: String(m.ts || idx),
                    className:
                      "ms-mass-log-row is-" + (m.status === "delivered" ? "ok" : "sending"),
                  },
                  h("span", null, (m.client_name || "—") + (m.text ? " — " + m.text : "")),
                  h(
                    "span",
                    { className: "ms-muted" },
                    (m.status === "delivered" ? "✓" : "✎") +
                      (m.ts ? " · " + String(m.ts).slice(0, 16).replace("T", " ") : ""),
                  ),
                );
              }),
            )
          : h("p", { className: "ms-muted" }, "Исходящих пока нет."),
      ),
    ];

    return h(
      "div",
      { className: "ms-page ms-dashboard-page" },
      h(
        "div",
        { className: "ms-card-head" },
        h("h1", { className: "ms-clients-title" }, "Дашборд"),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            onClick: function () {
              setReportChatOpen(true);
            },
          },
          "Чат-аналитик",
        ),
        h(
          "button",
          { type: "button", className: "ms-btn", disabled: loading, onClick: load },
          loading ? "Обновляем…" : "Обновить",
        ),
      ),
      h(
        "p",
        { className: "ms-muted" },
        "Формулы Excel «По дням / НЕДЕЛЯ / МЕСЯЦ / Флау» по заказам МойСклад без отменённых.",
      ),
      reportChatOpen
        ? h(MsChatDrawer, {
            endpoint: "/dashboard/chat",
            seedFollowups: [
              "Построй отчёт за последний месяц по всем каналам",
              "Какой канал просел сильнее всего к прошлому месяцу?",
              "Сойдись с кабинетом Яндекса — где расхождение?",
            ],
            title: "Чат-аналитик отчёта",
            hint: "Считает только из данных МоегоСклада. Если цифр не хватает — скажет каких; пришлите их сообщением, и он пересчитает.",
            example: "Построй такой же отчёт по такой же форме за июль и август",
            onClose: function () {
              setReportChatOpen(false);
            },
          })
        : null,
      h(
        "section",
        { className: "ms-card-section" },
        h(
          "details",
          null,
          h("summary", { className: "ms-dash-method-summary" }, "Как посчитано (метод и границы периодов)"),
          h(
            "ul",
            { className: "ms-muted ms-dash-method" },
            h("li", null, "Дата заказа = момент создания в МоемСкладе (moment), не дата доставки."),
            h("li", null, "Месяц — календарный по этой дате; неделя — понедельник–воскресенье."),
            h("li", null, "Считаются все заказы кроме отменённых; неоплаченные входят (маркетплейсы не пишут оплату в заказ)."),
            h("li", null, "Суммы — цены заказов МоегоСклада; для Яндекса это цены ДО его скидок, фактические продажи — в сверке."),
            h("li", null, "Каналы: все из поля «Канал продаж» (включая Яндекс Еду и Ozon) — если Excel их не учитывает, будет расхождение."),
            h("li", null, "Выручка = оборот × (1 − комиссия площадки); «оборот» и «выручка» — разные строки."),
          ),
        ),
      ),
      data && data.yandex_reconciliation && data.yandex_reconciliation.length
        ? h(
            "section",
            { className: "ms-card-section" },
            h("h2", null, "Сверка с кабинетом Яндекс Маркета"),
            h(
              "p",
              { className: "ms-muted" },
              "МойСклад пишет цены до скидок Яндекса; «Оборот к Excel» = месяц с Яндексом по ценам кабинета — эта колонка должна сходиться с отчётом.",
            ),
            h(
              "div",
              { className: "ms-table-wrap" },
              h(
                "table",
                null,
                h(
                  "thead",
                  null,
                  h(
                    "tr",
                    null,
                    h("th", null, "Месяц"),
                    h("th", null, "Яндекс МС / заказы"),
                    h("th", null, "Яндекс кабинет / заказы"),
                    h("th", null, "К выплате"),
                    h("th", null, "Δ Яндекс"),
                    h("th", null, "Оборот МС (все каналы)"),
                    h("th", null, "Оборот к Excel"),
                  ),
                ),
                h(
                  "tbody",
                  null,
                  data.yandex_reconciliation.map(function (row) {
                    return h(
                      "tr",
                      { key: row.month },
                      h("td", null, row.month),
                      h(
                        "td",
                        null,
                        Math.round(row.ms_turnover || 0).toLocaleString("ru-RU") +
                          " ₽ / " +
                          (row.ms_orders != null ? row.ms_orders : "—"),
                      ),
                      h(
                        "td",
                        null,
                        Math.round(row.cabinet_buyer_total || 0).toLocaleString("ru-RU") +
                          " ₽ / " +
                          (row.cabinet_orders != null ? row.cabinet_orders : "—"),
                      ),
                      h(
                        "td",
                        null,
                        Math.round(row.cabinet_payout_total || 0).toLocaleString("ru-RU") + " ₽",
                      ),
                      h(
                        "td",
                        null,
                        row.delta != null
                          ? Math.round(row.delta).toLocaleString("ru-RU") +
                              " ₽ (" +
                              Math.round((row.delta_pct || 0) * 100) +
                              "%)"
                          : "—",
                      ),
                      h("td", null, Math.round(row.ms_month_total || 0).toLocaleString("ru-RU") + " ₽"),
                      h(
                        "td",
                        null,
                        h("strong", null, Math.round(row.adjusted_month_total || 0).toLocaleString("ru-RU") + " ₽"),
                      ),
                    );
                  }),
                ),
              ),
            ),
          )
        : null,
      error ? h("p", { className: "ms-error" }, error) : null,
      data && data.cache_backend
        ? h(
            "p",
            { className: "ms-muted ms-dash-cache" },
            "Кэш " +
              data.cache_backend +
              (data.synced_at_label ? " · каталог " + data.synced_at_label : "") +
              (data.analytics_cached ? " · аналитика из кэша" : " · аналитика пересчитана") +
              (data.stale ? " · устарел, фоновое обновление" : "") +
              ". API МойСклад не дергаем, пока жив кэш.",
          )
        : null,
      kpi && kpi.turnover != null
        ? h(
            "div",
            { className: "ms-stats-grid ms-dashboard-grid" },
            [
              [dashMoney(kpi.turnover), "Оборот · " + (kpi.period || "")],
              [dashMoney(kpi.revenue), "Выручка (после комиссии)"],
              [String(kpi.orders != null ? kpi.orders : "—"), "Заказы"],
              [kpi.avg_check != null ? dashMoney(kpi.avg_check) : "—", "Средний чек"],
              [dashMoney(kpi.margin), "Маржа"],
              [dashPct(kpi.mom_turnover) || "—", "Прирост оборота к прошлому месяцу"],
            ].map(function (t) {
              return h(
                "div",
                { key: t[1] },
                h("div", { className: "ms-stat-val" }, t[0]),
                h("div", { className: "ms-muted" }, t[1]),
              );
            }),
          )
        : null,
      h(
        "div",
        { className: "ms-filter-tabs", role: "tablist" },
        tabs.map(function (t) {
          return h(
            "button",
            {
              type: "button",
              key: t[0],
              role: "tab",
              className: "ms-filter-tab" + (tab === t[0] ? " is-active" : ""),
              onClick: function () {
                setTab(t[0]);
              },
            },
            t[1],
          );
        }),
      ),
      tab === "charts" ? h(DashCharts, { analytics: analytics || {} }) : null,
      tab === "overview" ? overview : null,
      tab === "day" ? h(DashDays, { analytics: analytics }) : null,
      tab === "week" ? h(DashMatrix, { matrix: analytics.by_week, title: "Канал" }) : null,
      tab === "month" ? h(DashMatrix, { matrix: analytics.by_month, title: "Канал" }) : null,
      tab === "flowwow" ? h(DashFlowwow, { analytics: analytics }) : null,
    );
  }

  var MP_LABELS = { flowwow: "Flowwow", yandex_market: "Яндекс Маркет" };

  function cardStatusRu(product) {
    return product.is_archived ? "в архиве" : product.is_active ? "активна" : "скрыта";
  }

  function cardPriceRu(product) {
    if (!product.price) return "—";
    var base = money(product.price);
    var discount = Number(product.discount || 0);
    return discount > 0 ? base + " · скидка " + discount + "%" : base;
  }

  function cardMessageBlock(card) {
    var listings = card.listings || {};
    var vals = Object.keys(listings).map(function (mp) {
      return listings[mp] || {};
    });
    var withText = null;
    for (var i = 0; i < vals.length; i++) {
      if (vals[i].description || vals[i].description_preview) {
        withText = vals[i];
        break;
      }
    }
    if (!withText) withText = vals[0] || {};
    var price = "";
    var url = "";
    vals.forEach(function (v) {
      if (!price && v.price) price = v.price;
      if (!url && v.url) url = v.url;
    });
    var lines = ["«" + (card.name || "—") + "»"];
    if (price) lines.push("Цена: " + Math.round(Number(price)).toLocaleString("ru-RU") + " ₽");
    var desc = String(withText.description || withText.description_preview || "").trim();
    if (desc) lines.push(desc);
    if (url) lines.push(url);
    return { block: lines.join("\n"), image: card.image || "", name: card.name || "—" };
  }

  function cardDragPayload(card) {
    var listings = card.listings || {};
    var url = "";
    Object.keys(listings).forEach(function (mp) {
      if (!url && listings[mp] && listings[mp].url) url = listings[mp].url;
    });
    return JSON.stringify({ kind: "ms-card", name: card.name || "", image: card.image || "", url: url });
  }

  function CombinedCardTile({ card, onSelect, onAdd, onRemove, added }) {
    var listings = card.listings || {};
    return h(
      "div",
      {
        className: "ms-mp-card is-clickable",
        draggable: true,
        onDragStart: function (ev) {
          ev.dataTransfer.setData("application/x-ms-card", cardDragPayload(card));
          var dragUrl = "";
          Object.keys(listings).forEach(function (mp) {
            if (!dragUrl && listings[mp] && listings[mp].url) dragUrl = listings[mp].url;
          });
          ev.dataTransfer.setData("text/plain", dragUrl || card.name || "");
          ev.dataTransfer.effectAllowed = "copy";
        },
        onClick: function () {
          onSelect(card);
        },
      },
      onAdd
        ? h(
            "button",
            {
              type: "button",
              className: "ms-mp-plus" + (added ? " is-added" : ""),
              title: added ? "Убрать из сообщения" : "В сообщение (текст + фото)",
              onClick: function (ev) {
                ev.stopPropagation();
                if (added && onRemove) onRemove(card.name || "");
                else if (!added) onAdd(card);
              },
            },
            added ? "✓" : "+",
          )
        : null,
      card.image
        ? h("img", { className: "ms-mp-card-img", src: card.image, alt: card.name || "", loading: "lazy" })
        : h("div", { className: "ms-mp-card-img is-empty" }, "нет фото"),
      h(
        "div",
        { className: "ms-mp-card-body" },
        h(
          "div",
          { className: "ms-mp-badges" },
          (card.marketplaces || []).map(function (mp) {
            return h("span", { key: mp, className: "ms-mp-badge" }, MP_LABELS[mp] || mp);
          }),
        ),
        h("strong", null, card.name || "—"),
        Object.keys(listings).map(function (mp) {
          var p = listings[mp];
          return h(
            "span",
            { key: mp, className: "ms-muted" },
            (MP_LABELS[mp] || mp) +
              ": " +
              cardPriceRu(p) +
              " · " +
              cardStatusRu(p) +
              (p.content_rating != null ? " · " + p.content_rating + "/100" : ""),
          );
        }),
      ),
    );
  }

  function CombinedDrawer({ card, onClose, onAdd, onRemove, added }) {
    var listings = card.listings || {};
    var keys = Object.keys(listings);
    var first = keys.length ? listings[keys[0]] : null;
    return h(
      React.Fragment,
      null,
      h("div", { className: "ms-drawer-overlay", onClick: onClose }),
      h(
        "aside",
        { className: "ms-drawer" },
        h(
          "div",
          { className: "ms-drawer-head" },
          h(
            "strong",
            null,
            (card.marketplaces || [])
              .map(function (mp) {
                return MP_LABELS[mp] || mp;
              })
              .join(" + "),
          ),
          onAdd
            ? h(
                "button",
                {
                  type: "button",
                  className: "ms-btn ms-btn-primary",
                  onClick: function () {
                    if (added && onRemove) onRemove(card.name || "");
                    else if (!added) onAdd(card);
                  },
                },
                added ? "✓ Убрать из сообщения" : "+ В сообщение",
              )
            : null,
          h("button", { type: "button", className: "ms-btn", onClick: onClose }, "Закрыть"),
        ),
        card.image ? h("img", { className: "ms-drawer-img", src: card.image, alt: card.name || "" }) : null,
        h(
          "div",
          { className: "ms-drawer-body" },
          h("h3", null, card.name || "—"),
          keys.map(function (mp) {
            var p = listings[mp];
            return h(
              "div",
              { key: mp, className: "ms-mp-listing" },
              h("strong", null, MP_LABELS[mp] || mp),
              h("span", null, cardPriceRu(p)),
              h(
                "span",
                { className: "ms-muted" },
                cardStatusRu(p) +
                  (p.images_count ? " · фото: " + p.images_count : "") +
                  (p.content_rating != null ? " · контент: " + p.content_rating + "/100" : "") +
                  (p.offer_id ? " · " + p.offer_id : ""),
              ),
              p.url
                ? h("p", null, h("a", { href: p.url, target: "_blank", rel: "noreferrer" }, "Открыть на площадке ↗"))
                : null,
            );
          }),
          first && (first.description || first.description_preview)
            ? h("p", { className: "ms-drawer-desc" }, first.description || first.description_preview)
            : null,
        ),
      ),
    );
  }

  function MsChatDrawer({ onClose, endpoint, title, hint, example, seedFollowups }) {
    const [turns, setTurns] = useState([]);
    const [draft, setDraft] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [followups, setFollowups] = useState(seedFollowups || [example]);

    function send(text) {
      var content = String(text != null ? text : draft).trim();
      if (!content || busy) return;
      var next = turns.concat([{ role: "user", content: content }]);
      setTurns(next);
      setDraft("");
      setFollowups([]);
      setBusy(true);
      setError("");
      api(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next }),
      })
        .then(function (out) {
          setTurns(next.concat([{ role: "assistant", content: (out && out.reply) || "(пустой ответ)" }]));
          setFollowups(((out && out.followups) || []).filter(Boolean).slice(0, 3));
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        })
        .finally(function () {
          setBusy(false);
        });
    }

    return h(
      React.Fragment,
      null,
      h("div", { className: "ms-drawer-overlay", onClick: onClose }),
      h(
        "aside",
        { className: "ms-drawer ms-chat-drawer" },
        h(
          "div",
          { className: "ms-drawer-head" },
          h("strong", null, title),
          h("button", { type: "button", className: "ms-btn", onClick: onClose }, "Закрыть"),
        ),
        h("p", { className: "ms-muted ms-drawer-hint" }, hint),
        h(
          "div",
          { className: "ms-chat-drawer-log" },
          turns.length === 0 && !followups.length
            ? h("p", { className: "ms-muted" }, "Например: «" + example + "»")
            : null,
          turns.length === 0 && followups.length
            ? h("p", { className: "ms-muted" }, "С чего начать — выберите вопрос или напишите свой:")
            : null,
          turns.map(function (turn, idx) {
            return h("div", { key: idx, className: "ms-chat-bubble is-" + turn.role }, turn.content);
          }),
          busy ? h("p", { className: "ms-muted" }, "Считает…") : null,
          error ? h("p", { className: "ms-error" }, error) : null,
          !busy && followups.length
            ? h(
                "div",
                { className: "ms-chat-followups" },
                followups.map(function (question) {
                  return h(
                    "button",
                    {
                      key: question,
                      type: "button",
                      className: "ms-chat-followup",
                      onClick: function () {
                        send(question);
                      },
                    },
                    question,
                  );
                }),
              )
            : null,
        ),
        h(
          "div",
          { className: "ms-chat-drawer-input" },
          h("textarea", {
            rows: 2,
            value: draft,
            placeholder: "Вопрос…",
            onChange: function (ev) {
              setDraft(ev.target.value);
            },
            onKeyDown: function (ev) {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                send();
              }
            },
          }),
          h(
            "button",
            { type: "button", className: "ms-btn", disabled: busy || !draft.trim(), onClick: send },
            "Отправить",
          ),
        ),
      ),
    );
  }

  var REC_BLOCKS = [
    ["low_rating", "Низкий контент-рейтинг (Яндекс)"],
    ["few_photos", "Мало фото (< 3)"],
    ["add_to_yandex", "Добавить на Яндекс Маркет (есть только на Flowwow)"],
    ["add_to_flowwow", "Добавить на Flowwow (есть только на Яндексе)"],
    ["duplicates", "Дубли артикулов"],
    ["price_gaps", "Разные цены на площадках"],
    ["hidden_candidates", "Скрыты, но контент готов"],
  ];

  function recLine(row) {
    var bits = [];
    if (row.marketplace) bits.push(MP_LABELS[row.marketplace] || row.marketplace);
    if (row.rating != null) bits.push("рейтинг " + row.rating + "/100");
    if (row.images != null) bits.push("фото: " + row.images);
    if (row.price != null) bits.push(Math.round(row.price).toLocaleString("ru-RU") + " ₽");
    if (row.prices)
      bits.push(
        Object.keys(row.prices)
          .map(function (mp) {
            return (MP_LABELS[mp] || mp) + ": " + Math.round(row.prices[mp]).toLocaleString("ru-RU") + " ₽";
          })
          .join(" / "),
      );
    return bits.join(" · ");
  }

  function RecommendationsDrawer({ onClose }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");

    useEffect(function () {
      api("/cards/recommendations")
        .then(setData)
        .catch(function (err) {
          setError(String((err && err.message) || err));
        });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return h(
      React.Fragment,
      null,
      h("div", { className: "ms-drawer-overlay", onClick: onClose }),
      h(
        "aside",
        { className: "ms-drawer ms-chat-drawer" },
        h(
          "div",
          { className: "ms-drawer-head" },
          h("strong", null, "Рекомендации по данным"),
          h("button", { type: "button", className: "ms-btn", onClick: onClose }, "Закрыть"),
        ),
        h(
          "p",
          { className: "ms-muted ms-drawer-hint" },
          "Посчитано из карточек обеих площадок без ИИ — рейтинг, фото, цены, дубли.",
        ),
        h(
          "div",
          { className: "ms-chat-drawer-log" },
          error ? h("p", { className: "ms-error" }, error) : null,
          !data && !error ? h("p", { className: "ms-muted" }, "Считаем…") : null,
          data
            ? REC_BLOCKS.map(function (block) {
                var rows = data[block[0]] || [];
                if (!rows.length) return null;
                return h(
                  "section",
                  { key: block[0] },
                  h("strong", null, block[1] + " (" + rows.length + ")"),
                  h(
                    "ul",
                    { className: "ms-rec-list" },
                    rows.map(function (row, idx) {
                      var line = recLine(row);
                      return h(
                        "li",
                        { key: idx },
                        row.name || (row.names || []).join(" / ") || row.article || "—",
                        line ? h("span", { className: "ms-muted" }, " — " + line) : null,
                        row.action ? h("span", { className: "ms-muted" }, " → " + row.action) : null,
                      );
                    }),
                  ),
                );
              })
            : null,
        ),
      ),
    );
  }

  var CARD_TABS = [
    ["list", "Список"],
    ["create", "Создание"],
    ["seo", "СЕО"],
    ["placement", "Куда добавить"],
    ["orders", "Заказы"],
    ["analytics", "Аналитика"],
  ];

  function RecBlockList({ blocks, data }) {
    if (!data) return h("p", { className: "ms-muted" }, "Считаем…");
    var nonEmpty = blocks.filter(function (b) {
      return (data[b[0]] || []).length;
    });
    if (!nonEmpty.length) return h("p", { className: "ms-muted" }, "Замечаний нет — всё чисто.");
    return h(
      React.Fragment,
      null,
      nonEmpty.map(function (block) {
        var rows = data[block[0]] || [];
        var meta = (data.meta || {})[block[0]];
        return h(
          "section",
          { key: block[0] },
          h("strong", null, block[1] + " (" + rows.length + ")"),
          meta
            ? h(
                "p",
                { className: "ms-muted ms-rec-meta" },
                "Правило: " + (meta.rule || "—") + " · Источник: " + (meta.source || "—"),
              )
            : null,
          h(
            "ul",
            { className: "ms-rec-list" },
            rows.map(function (row, idx) {
              var line = recLine(row);
              return h(
                "li",
                { key: idx },
                row.name || (row.names || []).join(" / ") || row.article || "—",
                line ? h("span", { className: "ms-muted" }, " — " + line) : null,
                row.action ? h("span", { className: "ms-muted" }, " → " + row.action) : null,
              );
            }),
          ),
        );
      }),
    );
  }

  function CardsCreateTab() {
    const [query, setQuery] = useState("");
    const [rows, setRows] = useState([]);
    const [picked, setPicked] = useState(null);
    const [draft, setDraft] = useState(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");

    function search() {
      if (!query.trim()) return;
      setBusy("search");
      setError("");
      api("/cards/ms-search?query=" + encodeURIComponent(query.trim()))
        .then(function (out) {
          setRows(out.rows || []);
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        })
        .finally(function () {
          setBusy("");
        });
    }

    function generate(row) {
      setPicked(row);
      setDraft(null);
      setBusy("draft");
      setError("");
      api("/cards/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: row.name, price: row.price }),
      })
        .then(setDraft)
        .catch(function (err) {
          setError(String((err && err.message) || err));
        })
        .finally(function () {
          setBusy("");
        });
    }

    return h(
      "div",
      { className: "ms-cards-subtab" },
      h(
        "p",
        { className: "ms-muted" },
        "Шаг 1: найдите букет в каталоге МоегоСклада → шаг 2: система сгенерирует описания под каждую площадку. Публикация — следующий этап.",
      ),
      h(
        "div",
        { className: "ms-cards-search" },
        h("input", {
          value: query,
          placeholder: "Название букета в МоемСкладе…",
          onChange: function (ev) {
            setQuery(ev.target.value);
          },
          onKeyDown: function (ev) {
            if (ev.key === "Enter") search();
          },
        }),
        h(
          "button",
          { type: "button", className: "ms-btn", disabled: busy === "search" || !query.trim(), onClick: search },
          busy === "search" ? "Ищем…" : "Найти в МС",
        ),
      ),
      error ? h("p", { className: "ms-error" }, error) : null,
      rows.length
        ? h(
            "ul",
            { className: "ms-rec-list" },
            rows.map(function (row) {
              return h(
                "li",
                { key: row.id },
                row.name + " ",
                h(
                  "span",
                  { className: "ms-muted" },
                  (row.type === "bundle" ? "комплект" : row.type) +
                    " · " +
                    (row.price ? Math.round(row.price).toLocaleString("ru-RU") + " ₽" : "без цены") +
                    " ",
                ),
                h(
                  "button",
                  {
                    type: "button",
                    className: "ms-link-btn",
                    disabled: busy === "draft",
                    onClick: function () {
                      generate(row);
                    },
                  },
                  "сгенерировать описание",
                ),
              );
            }),
          )
        : null,
      busy === "draft"
        ? h("p", { className: "ms-muted" }, "Генерируем описания для «" + ((picked && picked.name) || "") + "»…")
        : null,
      draft && draft.drafts
        ? Object.keys(draft.drafts).map(function (mp) {
            return h(
              "section",
              { key: mp },
              h("strong", null, MP_LABELS[mp] || mp),
              h("p", { className: "ms-drawer-desc" }, draft.drafts[mp]),
            );
          })
        : null,
    );
  }

  function CardsOrdersTab({ limit, status }) {
    const [orders, setOrders] = useState(null);
    const [error, setError] = useState("");

    useEffect(
      function () {
        setOrders(null);
        api(
          "/cards/orders?limit=" +
            (limit || 25) +
            (status ? "&status=" + encodeURIComponent(status) : ""),
        )
          .then(function (out) {
            setOrders(out.orders || []);
          })
          .catch(function (err) {
            setError(String((err && err.message) || err));
          });
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [limit, status],
    );

    return h(
      "div",
      { className: "ms-cards-subtab" },
      h(
        "p",
        { className: "ms-muted" },
        "Живые заказы из кабинета Яндекс Маркета — источник: API /campaigns/…/orders, без кэша (в МойСклад заносятся родной интеграцией; Flowwow заказов по API не отдаёт). Лимит и статус меняются в «Параметрах».",
      ),
      error ? h("p", { className: "ms-error" }, error) : null,
      !orders && !error ? h("p", { className: "ms-muted" }, "Загружаем…") : null,
      orders
        ? h(
            "div",
            { className: "ms-table-wrap" },
            h(
              "table",
              null,
              h(
                "thead",
                null,
                h(
                  "tr",
                  null,
                  h("th", null, "Создан"),
                  h("th", null, "Статус"),
                  h("th", null, "Сумма"),
                  h("th", null, "Точка"),
                  h("th", null, "Состав"),
                ),
              ),
              h(
                "tbody",
                null,
                orders.map(function (order) {
                  return h(
                    "tr",
                    { key: order.id },
                    h("td", null, order.created || "—"),
                    h("td", null, (order.status || "") + (order.substatus ? " · " + order.substatus : "")),
                    h(
                      "td",
                      null,
                      order.buyer_total != null
                        ? Math.round(order.buyer_total).toLocaleString("ru-RU") + " ₽"
                        : "—",
                    ),
                    h("td", null, order.campaign || "—"),
                    h("td", null, (order.items || []).join("; ")),
                  );
                }),
              ),
            ),
          )
        : null,
    );
  }

  function CardsAnalyticsTab({ months }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");

    useEffect(
      function () {
        setData(null);
        api("/cards/analytics?months=" + (months || 4))
          .then(setData)
          .catch(function (err) {
            setError(String((err && err.message) || err));
          });
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [months],
    );

    var months = Object.keys((data && data.channel_dynamics) || {});
    var channelSet = {};
    months.forEach(function (m) {
      Object.keys(data.channel_dynamics[m] || {}).forEach(function (ch) {
        channelSet[ch] = true;
      });
    });
    var channels = Object.keys(channelSet);
    var recon = (data && data.yandex_reconciliation) || [];

    return h(
      "div",
      { className: "ms-cards-subtab" },
      error ? h("p", { className: "ms-error" }, error) : null,
      !data && !error ? h("p", { className: "ms-muted" }, "Считаем…") : null,
      months.length
        ? h(
            "section",
            null,
            h("strong", null, "Динамика каналов из МоегоСклада (оборот ₽ / заказы) · " + (months || 4) + " мес."),
            data && data.sources && data.sources.channel_dynamics
              ? h("p", { className: "ms-muted ms-rec-meta" }, "Источник: " + data.sources.channel_dynamics)
              : null,
            h(
              "div",
              { className: "ms-table-wrap" },
              h(
                "table",
                null,
                h(
                  "thead",
                  null,
                  h(
                    "tr",
                    null,
                    [h("th", { key: "ch" }, "Канал")].concat(
                      months.map(function (m) {
                        return h("th", { key: m }, m);
                      }),
                    ),
                  ),
                ),
                h(
                  "tbody",
                  null,
                  channels.map(function (ch) {
                    return h(
                      "tr",
                      { key: ch },
                      [h("td", { key: "n" }, MP_LABELS[ch] || ch)].concat(
                        months.map(function (m) {
                          var cell = (data.channel_dynamics[m] || {})[ch];
                          return h(
                            "td",
                            { key: m },
                            cell
                              ? Math.round(cell.turnover || 0).toLocaleString("ru-RU") +
                                  " / " +
                                  (cell.orders || 0)
                              : "—",
                          );
                        }),
                      ),
                    );
                  }),
                ),
              ),
            ),
          )
        : null,
      recon.length
        ? h(
            "section",
            null,
            h("strong", null, "Сверка с кабинетом Яндекс Маркета"),
            h(
              "p",
              { className: "ms-muted" },
              "МойСклад пишет цены до скидок — в кабинете фактические продажи." +
                (data && data.sources && data.sources.yandex_reconciliation
                  ? " Источник: " + data.sources.yandex_reconciliation
                  : ""),
            ),
            h(
              "div",
              { className: "ms-table-wrap" },
              h(
                "table",
                null,
                h(
                  "thead",
                  null,
                  h(
                    "tr",
                    null,
                    h("th", null, "Месяц"),
                    h("th", null, "МС"),
                    h("th", null, "Кабинет (покупатели)"),
                    h("th", null, "К выплате"),
                    h("th", null, "Δ"),
                  ),
                ),
                h(
                  "tbody",
                  null,
                  recon.map(function (row) {
                    return h(
                      "tr",
                      { key: row.month },
                      h("td", null, row.month),
                      h(
                        "td",
                        null,
                        Math.round(row.ms_turnover || 0).toLocaleString("ru-RU") +
                          " ₽ / " +
                          (row.ms_orders != null ? row.ms_orders : "—"),
                      ),
                      h(
                        "td",
                        null,
                        Math.round(row.cabinet_buyer_total || 0).toLocaleString("ru-RU") +
                          " ₽ / " +
                          (row.cabinet_orders != null ? row.cabinet_orders : "—"),
                      ),
                      h(
                        "td",
                        null,
                        Math.round(row.cabinet_payout_total || 0).toLocaleString("ru-RU") + " ₽",
                      ),
                      h(
                        "td",
                        null,
                        row.delta_pct != null ? Math.round(row.delta_pct * 100) + "%" : "—",
                      ),
                    );
                  }),
                ),
              ),
            ),
          )
        : null,
    );
  }

  var DEFAULT_REC_PARAMS = {
    ratingThreshold: 85,
    minPhotos: 3,
    priceGapPct: 10,
    cap: 25,
    months: 4,
    ordersLimit: 25,
    ordersStatus: "",
  };

  function recQuery(p) {
    return (
      "rating_threshold=" +
      p.ratingThreshold +
      "&min_photos=" +
      p.minPhotos +
      "&price_gap_min=" +
      (p.priceGapPct / 100).toFixed(2) +
      "&cap=" +
      p.cap
    );
  }

  function ParamsField({ label, value, min, max, onChange }) {
    return h(
      "label",
      { className: "ms-muted ms-params-row" },
      label,
      h("input", {
        type: "number",
        min: min,
        max: max,
        value: value,
        onChange: function (ev) {
          var parsed = Number(ev.target.value);
          if (isFinite(parsed)) onChange(Math.max(min, Math.min(max, parsed)));
        },
      }),
    );
  }

  function CardsParamsDrawer({ params, onApply, onClose }) {
    const [draft, setDraft] = useState(params);

    function set(key, value) {
      setDraft(function (prev) {
        var next = Object.assign({}, prev);
        next[key] = value;
        return next;
      });
    }

    return h(
      React.Fragment,
      null,
      h("div", { className: "ms-drawer-overlay", onClick: onClose }),
      h(
        "aside",
        { className: "ms-drawer" },
        h(
          "div",
          { className: "ms-drawer-head" },
          h("strong", null, "Параметры модели"),
          h("button", { type: "button", className: "ms-btn", onClick: onClose }, "Закрыть"),
        ),
        h(
          "p",
          { className: "ms-muted ms-drawer-hint" },
          "Пороги, по которым считаются рекомендации. Меняете — блоки пересчитываются из тех же данных.",
        ),
        h(
          "div",
          { className: "ms-params-body" },
          h(ParamsField, {
            label: "Порог контент-рейтинга (Яндекс)",
            value: draft.ratingThreshold,
            min: 1,
            max: 100,
            onChange: function (v) {
              set("ratingThreshold", v);
            },
          }),
          h(ParamsField, {
            label: "Минимум фото",
            value: draft.minPhotos,
            min: 1,
            max: 20,
            onChange: function (v) {
              set("minPhotos", v);
            },
          }),
          h(ParamsField, {
            label: "Порог разницы цен, %",
            value: draft.priceGapPct,
            min: 0,
            max: 100,
            onChange: function (v) {
              set("priceGapPct", v);
            },
          }),
          h(ParamsField, {
            label: "Строк в блоке (cap)",
            value: draft.cap,
            min: 1,
            max: 200,
            onChange: function (v) {
              set("cap", v);
            },
          }),
          h(ParamsField, {
            label: "Месяцев динамики (Аналитика)",
            value: draft.months,
            min: 2,
            max: 14,
            onChange: function (v) {
              set("months", v);
            },
          }),
          h(ParamsField, {
            label: "Лимит заказов (Заказы)",
            value: draft.ordersLimit,
            min: 1,
            max: 100,
            onChange: function (v) {
              set("ordersLimit", v);
            },
          }),
          h(
            "label",
            { className: "ms-muted ms-params-row" },
            "Статус заказов",
            h(
              "select",
              {
                value: draft.ordersStatus,
                onChange: function (ev) {
                  set("ordersStatus", ev.target.value);
                },
              },
              h("option", { value: "" }, "Все"),
              h("option", { value: "PROCESSING" }, "PROCESSING"),
              h("option", { value: "DELIVERY" }, "DELIVERY"),
              h("option", { value: "DELIVERED" }, "DELIVERED"),
              h("option", { value: "CANCELLED" }, "CANCELLED"),
            ),
          ),
          h(
            "div",
            { className: "ms-params-actions" },
            h(
              "button",
              {
                type: "button",
                className: "ms-btn ms-btn-primary",
                onClick: function () {
                  onApply(draft);
                  onClose();
                },
              },
              "Применить",
            ),
            h(
              "button",
              {
                type: "button",
                className: "ms-btn",
                onClick: function () {
                  setDraft(DEFAULT_REC_PARAMS);
                },
              },
              "Сбросить",
            ),
          ),
        ),
      ),
    );
  }

  function CardsPage() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [selected, setSelected] = useState(null);
    const [chatOpen, setChatOpen] = useState(false);
    const [recsOpen, setRecsOpen] = useState(false);
    const [subTab, setSubTab] = useState("list");
    const [recData, setRecData] = useState(null);
    const [recParams, setRecParams] = useState(DEFAULT_REC_PARAMS);
    const [paramsOpen, setParamsOpen] = useState(false);
    const [mpFilter, setMpFilter] = useState("all");
    const [statusFilter, setStatusFilter] = useState("all");

    useEffect(
      function () {
        if (subTab === "seo" || subTab === "placement") {
          setRecData(null);
          api("/cards/recommendations?" + recQuery(recParams))
            .then(setRecData)
            .catch(function () {
              setRecData({});
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [subTab, recParams],
    );

    function load(force) {
      setLoading(true);
      setError("");
      api("/cards/marketplaces?limit=100" + (force ? "&force=true" : ""))
        .then(function (payload) {
          setData(payload);
        })
        .catch(function (err) {
          setError(String((err && err.message) || err));
        })
        .finally(function () {
          setLoading(false);
        });
    }

    useEffect(function () {
      load(false);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    var combined = (data && data.combined) || [];
    var filtered = combined.filter(function (card) {
      var mps = card.marketplaces || [];
      if (mpFilter === "both" && mps.length < 2) return false;
      if (mpFilter !== "all" && mpFilter !== "both" && mps.indexOf(mpFilter) === -1) return false;
      return statusFilter === "all" || (card.statuses || []).indexOf(statusFilter) !== -1;
    });

    var summary = [];
    var fw = (data && data.flowwow) || null;
    var ya = (data && data.yandex) || null;
    if (fw && fw.configured && !fw.error)
      summary.push("Flowwow «" + ((fw.shop && fw.shop.name) || "—") + "»: " + (fw.total != null ? fw.total : 0));
    if (ya && ya.configured && !ya.error)
      summary.push("Яндекс «" + ((ya.business && ya.business.name) || "—") + "»: " + (ya.total != null ? ya.total : 0));

    return h(
      "div",
      { className: "ms-page ms-cards-page" },
      h(
        "div",
        { className: "ms-card-head" },
        h("h1", { className: "ms-clients-title" }, "Карточки"),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            onClick: function () {
              setParamsOpen(true);
            },
          },
          "Параметры",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            onClick: function () {
              setRecsOpen(true);
            },
          },
          "Рекомендации",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            onClick: function () {
              setChatOpen(true);
            },
          },
          "Чат по карточкам",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-btn",
            disabled: loading,
            onClick: function () {
              load(true);
            },
          },
          loading ? "Обновляем…" : "Обновить",
        ),
      ),
      h(
        "p",
        { className: "ms-muted" },
        "Все карточки обеих площадок одним списком; одинаковая карточка на двух маркетплейсах помечена обоими. " +
          summary.join(" · "),
      ),
      error ? h("p", { className: "ms-error" }, error) : null,
      h(
        "div",
        { className: "ms-filter-tabs", role: "tablist" },
        CARD_TABS.map(function (tab) {
          return h(
            "button",
            {
              key: tab[0],
              type: "button",
              role: "tab",
              className: "ms-filter-tab" + (subTab === tab[0] ? " is-active" : ""),
              onClick: function () {
                setSubTab(tab[0]);
              },
            },
            tab[1],
          );
        }),
      ),
      subTab === "list"
        ? h(
            React.Fragment,
            null,
            h(
              "div",
              { className: "ms-mp-filters" },
              h(
                "label",
                { className: "ms-muted" },
                "Маркетплейс ",
                h(
                  "select",
                  {
                    value: mpFilter,
                    onChange: function (ev) {
                      setMpFilter(ev.target.value);
                    },
                  },
                  h("option", { value: "all" }, "Все"),
                  h("option", { value: "flowwow" }, "Flowwow"),
                  h("option", { value: "yandex_market" }, "Яндекс Маркет"),
                  h("option", { value: "both" }, "На обоих"),
                ),
              ),
              h(
                "label",
                { className: "ms-muted" },
                "Статус ",
                h(
                  "select",
                  {
                    value: statusFilter,
                    onChange: function (ev) {
                      setStatusFilter(ev.target.value);
                    },
                  },
                  h("option", { value: "all" }, "Все"),
                  h("option", { value: "active" }, "Активна"),
                  h("option", { value: "hidden" }, "Скрыта"),
                  h("option", { value: "archived" }, "В архиве"),
                ),
              ),
              h("span", { className: "ms-muted" }, filtered.length + " из " + combined.length),
            ),
            loading && !data
              ? h("p", { className: "ms-muted" }, "Загружаем…")
              : filtered.length
                ? h(
                    "div",
                    { className: "ms-mp-grid" },
                    filtered.map(function (card, idx) {
                      return h(CombinedCardTile, {
                        key: String(card.name || idx),
                        card: card,
                        onSelect: setSelected,
                      });
                    }),
                  )
                : h("p", { className: "ms-muted" }, "Карточек по выбранным фильтрам нет."),
          )
        : null,
      subTab === "create" ? h(CardsCreateTab) : null,
      subTab === "seo"
        ? h(
            "div",
            { className: "ms-cards-subtab" },
            h(
              "p",
              { className: "ms-muted" },
              "Контент-рейтинг Яндекса и полнота контента — что поднять, чтобы получать больше показов.",
            ),
            h(RecBlockList, {
              data: recData,
              blocks: [
                ["low_rating", "Низкий контент-рейтинг (Яндекс)"],
                ["few_photos", "Мало фото (< 3)"],
              ],
            }),
          )
        : null,
      subTab === "placement"
        ? h(
            "div",
            { className: "ms-cards-subtab" },
            h(
              "p",
              { className: "ms-muted" },
              "Что добавить на вторую площадку и что привести в порядок — посчитано из данных обеих площадок.",
            ),
            h(RecBlockList, {
              data: recData,
              blocks: [
                ["add_to_yandex", "Добавить на Яндекс Маркет (есть только на Flowwow)"],
                ["add_to_flowwow", "Добавить на Flowwow (есть только на Яндексе)"],
                ["hidden_candidates", "Скрыты, но контент готов"],
                ["duplicates", "Дубли артикулов"],
                ["price_gaps", "Разные цены на площадках"],
              ],
            }),
          )
        : null,
      subTab === "orders"
        ? h(CardsOrdersTab, { limit: recParams.ordersLimit, status: recParams.ordersStatus })
        : null,
      subTab === "analytics" ? h(CardsAnalyticsTab, { months: recParams.months }) : null,
      paramsOpen
        ? h(CardsParamsDrawer, {
            params: recParams,
            onApply: setRecParams,
            onClose: function () {
              setParamsOpen(false);
            },
          })
        : null,
      selected
        ? h(CombinedDrawer, {
            card: selected,
            onClose: function () {
              setSelected(null);
            },
          })
        : null,
      recsOpen
        ? h(RecommendationsDrawer, {
            onClose: function () {
              setRecsOpen(false);
            },
          })
        : null,
      chatOpen
        ? h(MsChatDrawer, {
            endpoint: "/cards/chat",
            seedFollowups: [
              "Какие карточки стоит добавить на вторую площадку?",
              "Что исправить в карточках с низким рейтингом?",
              "Где у нас дубли и разные цены на площадках?",
            ],
            title: "Чат по карточкам",
            hint: "Консультант по размещению и продвижению: смотрит статусы, фото и контент-рейтинг карточек и говорит, что исправить или добавить — отдельно для каждой площадки.",
            example: "Какие карточки стоит добавить на вторую площадку и что исправить в слабых?",
            onClose: function () {
              setChatOpen(false);
            },
          })
        : null,
    );
  }


  function MoySkladApp() {
    const [view, setView] = useState(function () {
      try {
        const sp = new URLSearchParams(window.location.search);
        const v = sp.get("view");
        return v === "campaigns" || v === "dashboard" || v === "cards" ? v : "clients";
      } catch (_) {
        return "clients";
      }
    });

    function go(next) {
      setView(next);
      try {
        const url = new URL(window.location.href);
        if (next && next !== "clients") url.searchParams.set("view", next);
        else url.searchParams.delete("view");
        window.history.replaceState({}, "", url.pathname + url.search);
      } catch (_) {}
    }

    return h(
      "div",
      { className: "ms-shell", "data-selectable-text": "true" },
      h(
        "nav",
        { className: "ms-topnav", "aria-label": "МойСклад" },
        h(
          "button",
          {
            type: "button",
            className: "ms-topnav-link" + (view === "clients" ? " is-active" : ""),
            onClick: function () {
              go("clients");
            },
          },
          "Клиенты",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-topnav-link" + (view === "campaigns" ? " is-active" : ""),
            onClick: function () {
              go("campaigns");
            },
          },
          "Рассылки",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-topnav-link" + (view === "dashboard" ? " is-active" : ""),
            onClick: function () {
              go("dashboard");
            },
          },
          "Дашборд",
        ),
        h(
          "button",
          {
            type: "button",
            className: "ms-topnav-link" + (view === "cards" ? " is-active" : ""),
            onClick: function () {
              go("cards");
            },
          },
          "Карточки",
        ),
        h(
          "a",
          { className: "ms-topnav-link ms-topnav-ext", href: "/plugins" },
          "Plugins",
        ),
      ),
      view === "campaigns"
        ? h(CampaignsPage)
        : view === "dashboard"
          ? h(DashboardPage)
          : view === "cards"
            ? h(CardsPage)
            : h(ClientsPage),
    );
  }

  window.__HERMES_PLUGINS__.register("moysklad", MoySkladApp);
})();
