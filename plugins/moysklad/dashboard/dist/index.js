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

  function ClientsTable({ clients }) {
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
            h("th", null, "Клиент"),
            h("th", null, "Группы"),
            h("th", null, "Каналы"),
            h("th", null, "Заказы"),
            h("th", null, "Средний чек"),
            h("th", null, "Статус"),
          ),
        ),
        h(
          "tbody",
          null,
          clients.map(function (c) {
            return h(
              "tr",
              { key: c.id },
              h(
                "td",
                null,
                h("div", null, c.name || "—"),
                c.phone ? h("div", { className: "ms-muted" }, c.phone) : null,
              ),
              h(
                "td",
                null,
                (c.tags || []).length
                  ? (c.tags || []).map(function (t) {
                      return h("span", { key: t, className: "ms-tag-pill" }, t);
                    })
                  : h("span", { className: "ms-muted" }, "—"),
              ),
              h(
                "td",
                null,
                (c.channels || []).length
                  ? (c.channels || []).join(", ")
                  : h("span", { className: "ms-muted" }, "—"),
              ),
              h("td", null, String(c.order_count || 0)),
              h("td", null, money(c.avg_check)),
              h("td", null, c.state || h("span", { className: "ms-muted" }, "—")),
            );
          }),
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

    return h(
      "div",
      { className: "ms-clients" },
      h(
        "div",
        { className: "ms-clients-header" },
        h("h1", { className: "ms-clients-title" }, "Клиенты"),
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
                load({ offset: offset, refresh: true });
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
        : h(ClientsTable, { clients: (data && data.clients) || [] }),
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
    );
  }

  window.__HERMES_PLUGINS__.register("moysklad", ClientsPage);
})();
