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
          limit: String(PAGE_SIZE),
          offset: String(offset),
        });
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
      [salesFilter, group, q, nextOffset, hasMore, mergePages],
    );

    useEffect(
      function () {
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

  function FactsPanel({ facts, notes }) {
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
      facts.recommendation
        ? h(
            "div",
            null,
            h("p", { className: "ms-ai-label" }, "Рекомендация (контекст)"),
            h("p", { className: "ms-facts-rec" }, facts.recommendation),
          )
        : null,
      notes ? h("p", { className: "ms-muted ms-grounding" }, notes) : null,
    );
  }

  function CampaignsPage() {
    const [campaigns, setCampaigns] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [title, setTitle] = useState("Рассылка по фильтрам");
    const [channel, setChannel] = useState("telegram");
    const [channelKind, setChannelKind] = useState("");
    const [group, setGroup] = useState("");
    const [requirePhone, setRequirePhone] = useState(false);
    const [requireTelegram, setRequireTelegram] = useState(false);
    const [vipOnly, setVipOnly] = useState(false);
    const [birthdaySoon, setBirthdaySoon] = useState(false);
    const [personalize, setPersonalize] = useState(false);
    const [mode, setMode] = useState("manual");
    const [offer, setOffer] = useState("");
    const [salesFilter, setSalesFilter] = useState("direct");
    const [saving, setSaving] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [rewriting, setRewriting] = useState(false);
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
        });
        if (channelKind) params.set("channel_kind", channelKind);
        if (requirePhone) params.set("require_phone", "true");
        if (requireTelegram) params.set("require_telegram", "true");
        if (vipOnly) params.set("vip_only", "true");
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
        birthdaySoon,
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
            if (data.message) setOffer(data.message);
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
      if (!String(offer || "").trim()) {
        setError("Сначала введите или сгенерируйте текст сообщения.");
        return;
      }
      setRewriting(true);
      setError("");
      api("/campaigns/rewrite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: offer,
          channel: channel,
          client_id: selectedClientId || "",
          seller_name: sellerName,
          seller_facts: sellerFacts,
        }),
      })
        .then(function (data) {
          if (data.message) setOffer(data.message);
          if (data.grounding_notes) setGroundingNotes(data.grounding_notes);
          if (data.source) setGenSource(data.source);
          if (data.facts) setFacts(data.facts);
        })
        .catch(function (err) {
          setError((err && err.message) || String(err));
        })
        .finally(function () {
          setRewriting(false);
        });
    }

    useEffect(
      function () {
        if (!prefillReady || !selectedClientId) return;
        loadOutreach(selectedClientId, channel);
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
          h(
            "label",
            null,
            "Текст сообщения",
            h("textarea", {
              rows: 8,
              value: offer,
              placeholder: selectedClientId
                ? "Сгенерируйте AI или введите текст…"
                : "Общий текст для фильтрованной аудитории…",
              onChange: function (e) {
                setOffer(e.target.value);
              },
            }),
          ),
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
                    disabled: generating || rewriting,
                    onClick: function () {
                      loadOutreach(selectedClientId, channel);
                    },
                  },
                  generating ? "Генерация…" : "Сгенерировать AI",
                )
              : null,
            h(
              "button",
              {
                type: "button",
                className: "ms-btn",
                disabled: rewriting || generating || !String(offer || "").trim(),
                onClick: humanizeDraft,
              },
              rewriting ? "Переписываем…" : "Продающе и по-человечески",
            ),
            h(
              "button",
              {
                type: "submit",
                className: "ms-btn ms-btn-primary",
                disabled:
                  saving || loading || generating || rewriting || audience < 1,
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
        h(FactsPanel, { facts: facts, notes: groundingNotes }),
      ),
      error ? h("div", { className: "ms-error" }, error) : null,
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
