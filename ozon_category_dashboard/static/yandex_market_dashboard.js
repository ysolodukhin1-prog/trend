(function (global) {
  "use strict";

  const TITLES = {
    yandexOverview: "Яндекс Маркет · Обзор",
    yandexFunnel: "Яндекс Маркет · Воронка",
    yandexFinance: "Яндекс Маркет · Финансы",
    yandexPromotion: "Яндекс Маркет · Продвижение",
    yandexInventory: "Яндекс Маркет · Остатки",
  };
  const COLORS = ["#ff4747", "#121817", "#079b79", "#e6a52d"];
  const uiByScope = new Map();

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function sumMetric(rows, key) {
    const values = (rows || []).map((row) => finite(row?.[key])).filter((value) => value !== null);
    return values.length ? values.reduce((total, value) => total + value, 0) : null;
  }

  function ratio(numerator, denominator) {
    const top = finite(numerator);
    const bottom = finite(denominator);
    return top === null || bottom === null || bottom === 0 ? null : (100 * top / bottom);
  }

  function formatValue(value, kind = "number") {
    const number = finite(value);
    if (number === null) return "—";
    const digits = kind === "money" || kind === "percent" ? 2 : 0;
    const text = new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(number);
    if (kind === "money") return `${text} ₽`;
    if (kind === "percent") return `${text}%`;
    return text;
  }

  function formatDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return value ? String(value) : "—";
    return new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "2-digit" });
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ru-RU");
  }

  function groupRows(rows, dimensions, metrics) {
    const grouped = new Map();
    (rows || []).forEach((row) => {
      const key = dimensions.map((dimension) => String(row?.[dimension] ?? "")).join("\u0001");
      if (!grouped.has(key)) {
        const base = {};
        dimensions.forEach((dimension) => { base[dimension] = row?.[dimension] ?? ""; });
        metrics.forEach((metric) => { base[metric] = null; base[`__${metric}`] = 0; });
        grouped.set(key, base);
      }
      const target = grouped.get(key);
      metrics.forEach((metric) => {
        const value = finite(row?.[metric]);
        if (value === null) return;
        target[metric] = (target[metric] ?? 0) + value;
        target[`__${metric}`] += 1;
      });
    });
    return [...grouped.values()].map((row) => {
      metrics.forEach((metric) => { delete row[`__${metric}`]; });
      return row;
    });
  }

  function storeMap(payload) {
    return Object.fromEntries((payload.stores || []).map((store) => [String(store.campaign_id), store.store_name || store.campaign_id]));
  }

  function storeLabel(payload, campaignId) {
    const id = String(campaignId || "");
    return storeMap(payload)[id] ? `${storeMap(payload)[id]} · ${id}` : (id || "Все магазины");
  }

  function latestStockRows(rows) {
    const dates = (rows || []).map((row) => String(row.snapshot_date || "")).filter(Boolean).sort();
    const latest = dates.at(-1) || "";
    return { date: latest, rows: (rows || []).filter((row) => String(row.snapshot_date || "") === latest) };
  }

  function card(label, value, note = "", primary = false) {
    return `<article class="yandex-kpi${primary ? " is-primary" : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</article>`;
  }

  function empty(message) {
    return `<div class="yandex-empty">${escapeHtml(message)}</div>`;
  }

  function table(title, subtitle, columns, rows) {
    const body = rows.length
      ? rows.map((row) => `<tr>${columns.map((column) => `<td class="${column.numeric ? "is-number" : ""}">${column.render ? column.render(row) : escapeHtml(row[column.key] ?? "—")}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${columns.length}">${empty("За выбранный период строк нет")}</td></tr>`;
    return `<section class="yandex-panel yandex-table-panel"><header><div><h3>${escapeHtml(title)}</h3><span>${escapeHtml(subtitle || "")}</span></div></header><div class="yandex-table-scroll"><table><thead><tr>${columns.map((column) => `<th class="${column.numeric ? "is-number" : ""}">${escapeHtml(column.label)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div></section>`;
  }

  function lineChart(title, subtitle, rows, dateKey, series) {
    const grouped = groupRows(rows, [dateKey], series.map((item) => item.key)).sort((left, right) => String(left[dateKey]).localeCompare(String(right[dateKey])));
    const allValues = grouped.flatMap((row) => series.map((item) => finite(row[item.key]))).filter((value) => value !== null);
    if (!grouped.length || !allValues.length) return `<section class="yandex-panel"><header><div><h3>${escapeHtml(title)}</h3><span>${escapeHtml(subtitle)}</span></div></header>${empty("Нет данных для графика")}</section>`;
    const width = 1080;
    const height = 260;
    const left = 66;
    const right = 18;
    const top = 20;
    const bottom = 38;
    const min = Math.min(0, ...allValues);
    const max = Math.max(0, ...allValues);
    const span = max - min || 1;
    const x = (index) => left + (grouped.length === 1 ? 0 : index * (width - left - right) / (grouped.length - 1));
    const y = (value) => top + (max - value) * (height - top - bottom) / span;
    const zeroY = y(0);
    const paths = series.map((item, seriesIndex) => {
      const points = grouped.map((row, index) => {
        const value = finite(row[item.key]);
        return value === null ? null : { x: x(index), y: y(value), value, date: row[dateKey] };
      }).filter(Boolean);
      if (!points.length) return "";
      return `<polyline points="${points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ")}" fill="none" stroke="${item.color || COLORS[seriesIndex]}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>${points.map((point) => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="3" fill="${item.color || COLORS[seriesIndex]}"><title>${escapeHtml(`${formatDate(point.date)} · ${item.label}: ${formatValue(point.value, item.kind)}`)}</title></circle>`).join("")}`;
    }).join("");
    const ticks = [0, .5, 1].map((step) => {
      const value = max - span * step;
      const yy = y(value);
      return `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="yandex-grid"/><text x="${left - 8}" y="${yy + 4}" text-anchor="end" class="yandex-scale">${escapeHtml(formatValue(value, series[0].kind))}</text>`;
    }).join("");
    const labels = grouped.filter((_, index) => index === 0 || index === grouped.length - 1 || index % Math.max(1, Math.ceil(grouped.length / 6)) === 0).map((row) => {
      const index = grouped.indexOf(row);
      return `<text x="${x(index)}" y="${height - 12}" text-anchor="middle" class="yandex-scale">${escapeHtml(formatDate(row[dateKey]))}</text>`;
    }).join("");
    const legend = series.map((item, index) => `<span><i style="background:${item.color || COLORS[index]}"></i>${escapeHtml(item.label)}</span>`).join("");
    return `<section class="yandex-panel"><header><div><h3>${escapeHtml(title)}</h3><span>${escapeHtml(subtitle)}</span></div><div class="yandex-legend">${legend}</div></header><svg class="yandex-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">${ticks}<line x1="${left}" y1="${zeroY}" x2="${width - right}" y2="${zeroY}" class="yandex-axis"/>${paths}${labels}</svg></section>`;
  }

  function horizontalBars(title, subtitle, rows, labelKey, valueKey, kind = "money") {
    const values = rows.map((row) => finite(row[valueKey])).filter((value) => value !== null);
    if (!values.length) return `<section class="yandex-panel"><header><div><h3>${escapeHtml(title)}</h3><span>${escapeHtml(subtitle)}</span></div></header>${empty("Нет данных для рейтинга")}</section>`;
    const selected = [...rows].filter((row) => finite(row[valueKey]) !== null).sort((a, b) => finite(b[valueKey]) - finite(a[valueKey])).slice(0, 12);
    const max = Math.max(...selected.map((row) => Math.abs(finite(row[valueKey]))), 1);
    return `<section class="yandex-panel"><header><div><h3>${escapeHtml(title)}</h3><span>${escapeHtml(subtitle)}</span></div></header><div class="yandex-bars">${selected.map((row) => `<div class="yandex-bar-row"><span title="${escapeHtml(row[labelKey])}">${escapeHtml(row[labelKey])}</span><div><i style="width:${Math.max(1, 100 * Math.abs(finite(row[valueKey])) / max).toFixed(1)}%"></i></div><strong>${escapeHtml(formatValue(row[valueKey], kind))}</strong></div>`).join("")}</div></section>`;
  }

  function coverageBlock(payload) {
    const coverage = payload.coverage || [];
    const completed = coverage.filter((row) => row.state === "completed").length;
    const actualFrom = coverage.map((row) => row.actual_date_from).filter(Boolean).sort()[0] || payload.date_from;
    const actualTo = coverage.map((row) => row.actual_date_to).filter(Boolean).sort().at(-1) || payload.date_to;
    const finishedAt = coverage.map((row) => row.finished_at).filter(Boolean).sort().at(-1);
    const status = payload.status || "unavailable";
    const statusLabel = status === "available" ? "Данные доступны" : status === "partial" ? "Частичное покрытие" : "Данные недоступны";
    return {
      status,
      label: statusLabel,
      line: `${completed}/${coverage.length || 0} источников · ${formatDate(actualFrom)} — ${formatDate(actualTo)}`,
      freshness: formatDateTime(finishedAt),
    };
  }

  function renderOverview(payload) {
    const orders = payload.orders_daily || [];
    const returns = payload.returns_daily || [];
    const stocks = latestStockRows(payload.stock_snapshots || []);
    const kpis = [
      card("Заказы", formatValue(sumMetric(orders, "orders")), "созданы за период", true),
      card("Оплата покупателей", formatValue(sumMetric(orders, "buyer_payment"), "money"), "без субсидий и кешбэка"),
      card("Доставлено", formatValue(sumMetric(orders, "delivered_orders")), "текущий статус заказов"),
      card("Отменено", formatValue(sumMetric(orders, "cancelled_orders")), "текущий статус заказов"),
      card("Возвраты", formatValue(sumMetric(returns, "returns")), "строки возвратов"),
      card("Доступный остаток", formatValue(sumMetric(stocks.rows, "available_for_order")), stocks.date ? `снимок ${formatDate(stocks.date)}` : "нет снимка"),
    ].join("");
    const sku = (payload.top_sku_by_buyer_payment || []).map((row) => ({ ...row, store_label: storeLabel(payload, row.campaign_id) }));
    return `<div class="yandex-kpis">${kpis}</div><div class="yandex-grid-2">${lineChart("Оплата покупателей", "Динамика по дате создания заказа", orders, "order_date", [{ key: "buyer_payment", label: "Оплата", kind: "money" }])}${lineChart("Заказы и доставки", "Количество заказов по дням", orders, "order_date", [{ key: "orders", label: "Заказы" }, { key: "delivered_orders", label: "Доставлено" }])}</div>${horizontalBars("Топ SKU по оплате", "Первые 12 из 100 SKU", sku, "offer_id", "buyer_payment")}${table("Детализация SKU", "Оплата покупателей и текущий статус доставки", [
      { label: "Магазин", key: "store_label" },
      { label: "SKU продавца", key: "offer_id" },
      { label: "Заказано, шт", numeric: true, render: (row) => escapeHtml(formatValue(row.ordered_units)) },
      { label: "Доставлено, шт", numeric: true, render: (row) => escapeHtml(formatValue(row.delivered_units_current_status)) },
      { label: "Оплата", numeric: true, render: (row) => escapeHtml(formatValue(row.buyer_payment, "money")) },
    ], sku)}`;
  }

  function renderFunnel(payload) {
    const rows = payload.funnel_daily || [];
    const totals = {
      shows: sumMetric(rows, "shows"), clicks: sumMetric(rows, "clicks"), to_cart: sumMetric(rows, "to_cart"),
      ordered_units: sumMetric(rows, "ordered_units"), delivered_units: sumMetric(rows, "delivered_units"), returned_units: sumMetric(rows, "returned_units"),
    };
    const stages = [
      ["Показы", totals.shows, null], ["Клики", totals.clicks, ratio(totals.clicks, totals.shows)],
      ["В корзину", totals.to_cart, ratio(totals.to_cart, totals.clicks)], ["Заказано", totals.ordered_units, ratio(totals.ordered_units, totals.clicks)],
      ["Доставлено", totals.delivered_units, ratio(totals.delivered_units, totals.ordered_units)],
    ];
    const max = Math.max(...stages.map((stage) => finite(stage[1]) || 0), 1);
    const funnel = `<section class="yandex-panel"><header><div><h3>Воронка за период</h3><span>Конверсии пересчитаны из сумм; нулевой знаменатель остаётся недоступным</span></div></header><div class="yandex-funnel">${stages.map(([label, value, conversion], index) => `<div><span>${escapeHtml(label)}</span><div><i style="width:${Math.max(2, 100 * (finite(value) || 0) / max).toFixed(1)}%;background:${COLORS[index % COLORS.length]}"></i></div><strong>${escapeHtml(formatValue(value))}</strong><small>${index ? escapeHtml(formatValue(conversion, "percent")) : "база"}</small></div>`).join("")}</div></section>`;
    const daily = groupRows(rows, ["metric_date"], ["shows", "clicks", "to_cart", "ordered_units", "delivered_units", "returned_units"]).map((row) => ({
      ...row,
      ctr_pct: ratio(row.clicks, row.shows),
      click_to_cart_pct: ratio(row.to_cart, row.clicks),
      click_to_order_pct: ratio(row.ordered_units, row.clicks),
    }));
    return `<div class="yandex-kpis">${card("Показы", formatValue(totals.shows), "отчётная воронка", true)}${card("Клики", formatValue(totals.clicks), formatValue(ratio(totals.clicks, totals.shows), "percent"))}${card("В корзину", formatValue(totals.to_cart), formatValue(ratio(totals.to_cart, totals.clicks), "percent"))}${card("Заказано", formatValue(totals.ordered_units), formatValue(ratio(totals.ordered_units, totals.clicks), "percent"))}${card("Доставлено", formatValue(totals.delivered_units), formatValue(ratio(totals.delivered_units, totals.ordered_units), "percent"))}${card("Возвращено", formatValue(totals.returned_units), "по данным воронки")}</div><div class="yandex-grid-2">${funnel}${lineChart("Заказано и доставлено", "Единицы товара по дням", daily, "metric_date", [{ key: "ordered_units", label: "Заказано" }, { key: "delivered_units", label: "Доставлено" }])}</div>${table("Воронка по дням", "Сумма по выбранным магазинам", [
      { label: "Дата", render: (row) => escapeHtml(formatDate(row.metric_date)) },
      { label: "Показы", numeric: true, render: (row) => escapeHtml(formatValue(row.shows)) },
      { label: "Клики", numeric: true, render: (row) => escapeHtml(formatValue(row.clicks)) },
      { label: "CTR", numeric: true, render: (row) => escapeHtml(formatValue(row.ctr_pct, "percent")) },
      { label: "В корзину", numeric: true, render: (row) => escapeHtml(formatValue(row.to_cart)) },
      { label: "Клик → корзина", numeric: true, render: (row) => escapeHtml(formatValue(row.click_to_cart_pct, "percent")) },
      { label: "Заказано", numeric: true, render: (row) => escapeHtml(formatValue(row.ordered_units)) },
      { label: "Клик → заказ", numeric: true, render: (row) => escapeHtml(formatValue(row.click_to_order_pct, "percent")) },
      { label: "Доставлено", numeric: true, render: (row) => escapeHtml(formatValue(row.delivered_units)) },
    ], daily)}`;
  }

  function renderFinance(payload) {
    const orders = payload.orders_daily || [];
    const services = payload.services_daily || [];
    const transactions = payload.transactions_daily || [];
    const realization = payload.realization_periods || [];
    const serviceRows = groupRows(services, ["service_type"], ["service_amount", "source_rows", "priced_rows"]).sort((a, b) => Math.abs(finite(b.service_amount) || 0) - Math.abs(finite(a.service_amount) || 0));
    const transactionRows = groupRows(transactions, ["transaction_type", "payment_status"], ["transaction_amount", "source_rows", "priced_rows"]).sort((a, b) => Math.abs(finite(b.transaction_amount) || 0) - Math.abs(finite(a.transaction_amount) || 0));
    return `<div class="yandex-kpis">${card("Оплата покупателей", formatValue(sumMetric(orders, "buyer_payment"), "money"), "не выручка по начислению", true)}${card("Субсидии", formatValue(sumMetric(orders, "subsidy"), "money"), "отдельный компонент")}${card("Кешбэк Плюса", formatValue(sumMetric(orders, "cashback"), "money"), "отдельный компонент")}${card("Сервисы Маркета", formatValue(sumMetric(services, "service_amount"), "money"), "начисленные услуги")}${card("Нетто транзакций", formatValue(sumMetric(transactions, "transaction_amount"), "money"), "по всем статусам")}${card("Прибыль", "—", "нет полной себестоимости и налогов")}</div><div class="yandex-notice is-warning"><strong>Финансовая граница.</strong> Транзакции и начисления показаны раздельно. Они не считаются выручкой или прибылью; строки с неполной стоимостью не заменяются нулём.</div><div class="yandex-grid-2">${table("Сервисы Маркета", "Начисления по типам услуг", [
      { label: "Тип услуги", key: "service_type" },
      { label: "Строк", numeric: true, render: (row) => escapeHtml(formatValue(row.source_rows)) },
      { label: "Со стоимостью", numeric: true, render: (row) => escapeHtml(formatValue(row.priced_rows)) },
      { label: "Сумма", numeric: true, render: (row) => escapeHtml(formatValue(row.service_amount, "money")) },
    ], serviceRows)}${table("Расчётные транзакции", "Суммы по типу и статусу платежа", [
      { label: "Тип", key: "transaction_type" },
      { label: "Статус", key: "payment_status" },
      { label: "Строк", numeric: true, render: (row) => escapeHtml(formatValue(row.source_rows)) },
      { label: "Сумма", numeric: true, render: (row) => escapeHtml(formatValue(row.transaction_amount, "money")) },
    ], transactionRows)}</div>${table("Реализация", "Периодические данные по событиям", [
      { label: "Магазин", render: (row) => escapeHtml(storeLabel(payload, row.campaign_id)) },
      { label: "Период", render: (row) => escapeHtml(`${formatDate(row.date_from)} — ${formatDate(row.date_to)}`) },
      { label: "Событие", key: "event_type" },
      { label: "Единиц", numeric: true, render: (row) => escapeHtml(formatValue(row.units)) },
      { label: "Сумма", numeric: true, render: (row) => escapeHtml(formatValue(row.amount, "money")) },
      { label: "Покрытие цены", numeric: true, render: (row) => escapeHtml(`${formatValue(row.priced_rows)} / ${formatValue(row.source_rows)}`) },
    ], realization)}`;
  }

  function renderPromotion(payload) {
    const rows = payload.marketing_daily || [];
    const sources = groupRows(rows, ["source_key"], ["shows", "clicks", "to_cart", "attributed_units", "attributed_amount", "estimated_cost", "actual_cost", "deducted_bonuses", "source_rows", "rows_with_actual_cost"]).map((row) => ({ ...row, ctr_pct: ratio(row.clicks, row.shows), drr_pct: ratio(row.actual_cost, row.attributed_amount) }));
    const boost = payload.boost_sales_periods || [];
    return `<div class="yandex-kpis">${card("Показы", formatValue(sumMetric(rows, "shows")), "все рекламные источники", true)}${card("Клики", formatValue(sumMetric(rows, "clicks")), formatValue(ratio(sumMetric(rows, "clicks"), sumMetric(rows, "shows")), "percent"))}${card("Атрибутировано", formatValue(sumMetric(rows, "attributed_amount"), "money"), "по отчётам продвижения")}${card("Фактический расход", formatValue(sumMetric(rows, "actual_cost"), "money"), "только строки с фактом")}${card("ДРР", formatValue(ratio(sumMetric(rows, "actual_cost"), sumMetric(rows, "attributed_amount")), "percent"), "факт / атрибутированная сумма")}${card("Бонусы", formatValue(sumMetric(rows, "deducted_bonuses"), "money"), "списано бонусами")}</div><div class="yandex-notice"><strong>Уровень данных — бизнес.</strong> Яндекс не распределяет эти расходы однозначно по магазинам, поэтому фильтр магазина здесь отключён.</div>${lineChart("Расход и атрибутированная сумма", "Дневная динамика в рублях", rows, "metric_date", [{ key: "actual_cost", label: "Фактический расход", kind: "money" }, { key: "attributed_amount", label: "Атрибутированная сумма", kind: "money" }])}${table("Каналы продвижения", "Метрики агрегированы внутри каждого источника", [
      { label: "Источник", key: "source_key" },
      { label: "Показы", numeric: true, render: (row) => escapeHtml(formatValue(row.shows)) },
      { label: "Клики", numeric: true, render: (row) => escapeHtml(formatValue(row.clicks)) },
      { label: "CTR", numeric: true, render: (row) => escapeHtml(formatValue(row.ctr_pct, "percent")) },
      { label: "Атрибутировано", numeric: true, render: (row) => escapeHtml(formatValue(row.attributed_amount, "money")) },
      { label: "Расход", numeric: true, render: (row) => escapeHtml(formatValue(row.actual_cost, "money")) },
      { label: "ДРР", numeric: true, render: (row) => escapeHtml(formatValue(row.drr_pct, "percent")) },
    ], sources)}${table("Буст продаж", "Периодическая атрибуция и начисление", [
      { label: "Период", render: (row) => escapeHtml(`${formatDate(row.date_from)} — ${formatDate(row.date_to)}`) },
      { label: "Атрибутировано, шт", numeric: true, render: (row) => escapeHtml(formatValue(row.attributed_units)) },
      { label: "Доставленная сумма", numeric: true, render: (row) => escapeHtml(formatValue(row.attributed_delivered_amount, "money")) },
      { label: "Начислено", numeric: true, render: (row) => escapeHtml(formatValue(row.billed_amount, "money")) },
    ], boost)}`;
  }

  function renderInventory(payload) {
    const latest = latestStockRows(payload.stock_snapshots || []);
    const rows = latest.rows.map((row) => ({ ...row, store_label: storeLabel(payload, row.campaign_id) }));
    const totalAvailable = sumMetric(rows, "available_for_order");
    const totalReserved = sumMetric(rows, "reserved");
    return `<div class="yandex-kpis">${card("Доступно к заказу", formatValue(totalAvailable), latest.date ? `снимок ${formatDate(latest.date)}` : "нет снимка", true)}${card("В резерве", formatValue(totalReserved), "на дату снимка")}${card("SKU × склад", formatValue(sumMetric(rows, "sku_warehouse_rows")), "строки снимка")}${card("Строк с остатком", formatValue(sumMetric(rows, "rows_with_available_stock")), "значение остатка доступно")}${card("Складов", formatValue(new Set(rows.map((row) => `${row.campaign_id}\u0001${row.warehouse}`)).size), "в выбранном срезе")}${card("Исторический ряд", "—", "API предоставил снимок, не ряд")}</div><div class="yandex-notice is-warning"><strong>Снимок остатков.</strong> Панель показывает последнее доступное состояние на конец выбранного периода. Отсутствие исторического снимка не считается нулевым остатком.</div>${horizontalBars("Доступный остаток по складам", latest.date ? `Снимок ${formatDate(latest.date)}` : "Снимок отсутствует", rows.map((row) => ({ ...row, warehouse_label: `${row.store_label} · ${row.warehouse || "Склад"}` })), "warehouse_label", "available_for_order", "number")}${table("Остатки по складам", latest.date ? `Последний снимок: ${formatDate(latest.date)}` : "Нет снимка", [
      { label: "Магазин", key: "store_label" },
      { label: "Склад", key: "warehouse" },
      { label: "SKU × склад", numeric: true, render: (row) => escapeHtml(formatValue(row.sku_warehouse_rows)) },
      { label: "Доступно", numeric: true, render: (row) => escapeHtml(formatValue(row.available_for_order)) },
      { label: "Резерв", numeric: true, render: (row) => escapeHtml(formatValue(row.reserved)) },
    ], rows)}`;
  }

  function reportBody(payload, dashboard) {
    if (payload.status === "unavailable") return empty(payload.reason || "Аналитический слой Яндекса ещё не установлен");
    if (dashboard === "yandexFunnel") return renderFunnel(payload);
    if (dashboard === "yandexFinance") return renderFinance(payload);
    if (dashboard === "yandexPromotion") return renderPromotion(payload);
    if (dashboard === "yandexInventory") return renderInventory(payload);
    return renderOverview(payload);
  }

  function render(root, payload, options = {}) {
    if (!root) return;
    const dashboard = options.dashboard || "yandexOverview";
    const scopeKey = `${payload.client || "client"}:${dashboard}`;
    if (!uiByScope.has(scopeKey)) uiByScope.set(scopeKey, { query: "" });
    const coverage = coverageBlock(payload);
    const promotion = dashboard === "yandexPromotion";
    const stores = (payload.stores || []).filter((store) => store.is_accessible !== false && store.import_enabled !== false);
    const selectedStore = promotion ? "" : String(payload.store || "");
    const storeOptions = `<option value="">Все магазины</option>${stores.map((store) => `<option value="${escapeHtml(store.campaign_id)}" ${String(store.campaign_id) === selectedStore ? "selected" : ""}>${escapeHtml(`${store.store_name || store.campaign_id} · ${store.campaign_id}`)}</option>`).join("")}`;
    root.innerHTML = `<header class="yandex-report-head"><div><span class="yandex-eyebrow">ЯНДЕКС МАРКЕТ · API</span><h2>${escapeHtml((TITLES[dashboard] || TITLES.yandexOverview).replace("Яндекс Маркет · ", ""))}</h2><p>${escapeHtml(options.clientLabel || payload.client || "")} · ${stores.length} магазина · данные только для чтения</p></div><div class="yandex-coverage is-${escapeHtml(coverage.status)}"><strong>${escapeHtml(coverage.label)}</strong><span>${escapeHtml(coverage.line)}</span></div></header><section class="yandex-controls"><label><span>Магазин</span><select data-yandex-store ${promotion ? "disabled" : ""}>${storeOptions}</select></label><div><span>Период отчёта</span><strong>${escapeHtml(`${formatDate(payload.date_from)} — ${formatDate(payload.date_to)}`)}</strong></div><div><span>Свежесть источников</span><strong>${escapeHtml(coverage.freshness)}</strong></div></section>${payload.aggregate_cache_current === false ? `<div class="yandex-notice is-warning">Агрегат воронки обновляется; цифры рассчитаны из основной витрины.</div>` : ""}${reportBody(payload, dashboard)}<footer class="yandex-footnote"><strong>Источник:</strong> Yandex Market Partner API и типизированные витрины PULSE. Статус качества относится ко всему клиентскому набору. Недоступные значения показаны как «—».</footer>`;
    root.querySelector("[data-yandex-store]")?.addEventListener("change", (event) => options.onStoreChange?.(event.target.value));
  }

  global.YandexMarketDashboard = { render, titles: TITLES };
  global.YandexMarketDashboardTest = { finite, sumMetric, ratio, groupRows, latestStockRows, coverageBlock };
})(typeof window !== "undefined" ? window : globalThis);
