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

  const API = "/api/plugins/moysklad";
  const PAGE_SIZE = 50;

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
        h("span", { className: "ms-group-cloud-title" }, "Группы (МойСклад)"),
        h(
          "span",
          { className: "ms-legend", "aria-hidden": "true" },
          h("span", { className: "leg-ms" }, "МС"),
          h("span", { className: "leg-ai" }, "AI в таблице"),
        ),
        h(
          "span",
          { className: "ms-group-cloud-hint" },
          "ТЗ + события по месяцам · " + options.length,
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
          return h(
            "button",
            {
              key: item.name,
              type: "button",
              className: "ms-group-chip" + (active ? " is-active" : ""),
              style: { "--chip-hue": item.hue || 200 },
              title: item.count + " клиентов",
              onClick: function () {
                onSelect(item.name);
              },
            },
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
      return c.channel || ((c.channels || []).length ? c.channels.join(", ") : "");
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
                return h("td", { key: col.key }, cell(raw));
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
    const [ordersOpen, setOrdersOpen] = useState(false);
    const [note, setNote] = useState("");

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
    var buckets = client.tag_buckets || {};
    var name = client.name || "Клиент";

    function openUrl(url) {
      if (!url) return;
      try {
        window.open(url, "_blank", "noopener,noreferrer");
      } catch (_) {}
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
                  h("span", null, client.tg_nick || client.tg_conversation || "—"),
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
                ),
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
                      disabled: !msg.whatsapp_url,
                      title: msg.whatsapp_url || "Телефон не указан",
                      onClick: function () {
                        openUrl(msg.whatsapp_url);
                      },
                    },
                    "WhatsApp",
                  ),
                  h(
                    "button",
                    {
                      type: "button",
                      className: "ms-btn ms-btn-primary",
                      disabled: !msg.telegram_url,
                      title: msg.telegram_url || "Telegram не указан",
                      onClick: function () {
                        openUrl(msg.telegram_url);
                      },
                    },
                    "Telegram",
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
                ),
                note ? h("p", { className: "ms-note" }, note) : null,
                h("p", { className: "ms-muted" }, msg.hint || ""),
              ),
            ),
      ),
    );
  }

  function AssignModal({ open, loading, data, error, onClose, onPush }) {
    if (!open) return null;
    const list = (data && data.assignments) || [];
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
        h("h3", null, "Предложенные группы"),
        error ? h("div", { className: "ms-error" }, error) : null,
        loading
          ? h("p", { className: "ms-muted" }, "Считаем эвристики…")
          : h(
              "p",
              { className: "ms-muted" },
              "Изменится: " +
                ((data && data.changed) || 0) +
                " из " +
                ((data && data.total) || 0),
            ),
        h(
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
          h(
            "button",
            {
              type: "button",
              className: "ms-btn ms-btn-primary",
              disabled: loading || !list.length,
              onClick: onPush,
            },
            "Записать в МойСклад",
          ),
        ),
      ),
    );
  }

  function ClientsPage() {
    const [salesFilter, setSalesFilter] = useState("all");
    const [group, setGroup] = useState("");
    const [qInput, setQInput] = useState("");
    const [q, setQ] = useState("");
    const [offset, setOffset] = useState(0);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [modalOpen, setModalOpen] = useState(false);
    const [assignLoading, setAssignLoading] = useState(false);
    const [assignData, setAssignData] = useState(null);
    const [assignError, setAssignError] = useState(null);
    const [cardClientId, setCardClientId] = useState(null);

    const load = useCallback(
      function (opts) {
        const refresh = !!(opts && opts.refresh);
        const nextOffset = opts && opts.offset != null ? opts.offset : offset;
        setLoading(true);
        setError(null);
        const params = new URLSearchParams({
          sales_filter: salesFilter,
          group: group || "",
          q: q || "",
          limit: String(PAGE_SIZE),
          offset: String(nextOffset),
        });
        if (refresh) params.set("refresh", "true");
        api("/clients?" + params.toString())
          .then(function (payload) {
            setData(payload);
            setOffset(nextOffset);
          })
          .catch(function (err) {
            setError(String((err && err.message) || err));
            setData(null);
          })
          .finally(function () {
            setLoading(false);
          });
      },
      [salesFilter, group, q, offset],
    );

    useEffect(
      function () {
        setOffset(0);
        load({ offset: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
      },
      [salesFilter, group, q],
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

    const counts = (data && data.counts) || {};
    const matchedTotal = (data && data.matched_total) || 0;
    const canPrev = offset > 0;
    const canNext = offset + PAGE_SIZE < matchedTotal;

    const openAssign = useCallback(function () {
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
    }, [salesFilter, group, q]);

    const pushAssign = useCallback(function () {
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
    }, [assignData, load]);

    const pageLabel = useMemo(
      function () {
        if (!matchedTotal) return "0";
        const from = offset + 1;
        const to = Math.min(offset + PAGE_SIZE, matchedTotal);
        return from + "–" + to + " из " + matchedTotal;
      },
      [offset, matchedTotal],
    );

    var syncedLabel =
      (data && (data.synced_at_label || data.synced_at)) || "";
    var cacheHint = data
      ? (data.cached ? "из кэша" : "свежая выгрузка") +
        (syncedLabel ? " · синхр. " + syncedLabel : "")
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
                load({ offset: offset });
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
              disabled: loading,
              onClick: openAssign,
            },
            "Предложить группы",
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
      ),
      h(GroupCloud, {
        options: (data && data.group_options) || [],
        groupsTotal: (data && data.groups_total) || 0,
        selected: group,
        onSelect: function (name) {
          setGroup(name);
        },
        onClear: function () {
          setGroup("");
        },
      }),
      error ? h("div", { className: "ms-error" }, error) : null,
      loading && !data
        ? h("p", { className: "ms-muted" }, "Загрузка клиентов из МойСклад…")
        : h(ClientsTable, {
            clients: (data && data.clients) || [],
            onOpenClient: function (c) {
              if (c && c.id) setCardClientId(c.id);
            },
          }),
      h(
        "div",
        { className: "ms-pager" },
        h("span", { className: "ms-muted" }, pageLabel),
        h(
          "div",
          { className: "ms-clients-actions" },
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: !canPrev || loading,
              onClick: function () {
                load({ offset: Math.max(0, offset - PAGE_SIZE) });
              },
            },
            "Назад",
          ),
          h(
            "button",
            {
              type: "button",
              className: "ms-btn",
              disabled: !canNext || loading,
              onClick: function () {
                load({ offset: offset + PAGE_SIZE });
              },
            },
            "Вперёд",
          ),
        ),
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

  function CampaignsPage() {
    const [campaigns, setCampaigns] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [title, setTitle] = useState("Рассылка по фильтрам");
    const [channel, setChannel] = useState("telegram");
    const [mode, setMode] = useState("manual");
    const [offer, setOffer] = useState("");
    const [salesFilter, setSalesFilter] = useState("direct");
    const [saving, setSaving] = useState(false);
    const [counts, setCounts] = useState(null);
    const [audience, setAudience] = useState(0);

    const refresh = useCallback(function () {
      setLoading(true);
      setError("");
      Promise.all([
        api("/campaigns"),
        api("/clients?sales_filter=" + encodeURIComponent(salesFilter) + "&limit=1"),
      ])
        .then(function (pair) {
          setCampaigns((pair[0] && pair[0].campaigns) || []);
          setCounts((pair[1] && pair[1].counts) || null);
          setAudience((pair[1] && pair[1].matched_total) || 0);
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
        })
        .finally(function () {
          setLoading(false);
        });
    }, [salesFilter]);

    useEffect(function () {
      refresh();
    }, [refresh]);

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
        }),
      })
        .then(function () {
          setOffer("");
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
        h("div", null,
          h("h1", { className: "ms-clients-title" }, "Рассылки"),
          h(
            "p",
            { className: "ms-muted" },
            "Telegram / WhatsApp черновики по аудитории МойСклад (как на kinetic-ai /campaigns).",
          ),
        ),
        h(
          "button",
          { type: "button", className: "ms-btn", disabled: loading, onClick: refresh },
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
        "p",
        { className: "ms-muted" },
        "Аудитория: ",
        h("strong", null, String(audience)),
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
        "form",
        { className: "ms-campaign-form", onSubmit: createDraft },
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
          "Канал",
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
        mode === "manual"
          ? h(
              "label",
              null,
              "Текст сообщения",
              h("textarea", {
                rows: 4,
                value: offer,
                placeholder: "Текст рассылки…",
                onChange: function (e) {
                  setOffer(e.target.value);
                },
              }),
            )
          : h(
              "p",
              { className: "ms-muted" },
              "Текст для каждого клиента подставится из шаблона AI при создании черновика.",
            ),
        h(
          "button",
          {
            type: "submit",
            className: "ms-btn ms-btn-primary",
            disabled: saving || loading,
          },
          mode === "auto" ? "Создать авто-черновик" : "Создать черновик",
        ),
      ),
      error ? h("div", { className: "ms-error" }, error) : null,
      h("h2", { className: "ms-section-title" }, "Черновики"),
      loading && !campaigns.length
        ? h("p", { className: "ms-muted" }, "Загрузка…")
        : !campaigns.length
          ? h("p", { className: "ms-muted" }, "Пока нет рассылок.")
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
                      String(c.audience_count || 0) +
                      " · " +
                      (c.status || "draft"),
                  ),
                  c.offer
                    ? h("p", { className: "ms-campaign-offer" }, c.offer)
                    : null,
                );
              }),
            ),
    );
  }

  function MoySkladApp() {
    const [view, setView] = useState(function () {
      try {
        const sp = new URLSearchParams(window.location.search);
        return sp.get("view") === "campaigns" ? "campaigns" : "clients";
      } catch (_) {
        return "clients";
      }
    });

    function go(next) {
      setView(next);
      try {
        const url = new URL(window.location.href);
        if (next === "campaigns") url.searchParams.set("view", "campaigns");
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
          "a",
          { className: "ms-topnav-link ms-topnav-ext", href: "/plugins" },
          "Plugins",
        ),
      ),
      view === "campaigns" ? h(CampaignsPage) : h(ClientsPage),
    );
  }

  window.__HERMES_PLUGINS__.register("moysklad", MoySkladApp);
})();
