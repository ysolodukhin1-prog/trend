// Standalone commercial radar extension for the existing KOKOC BI shell.
(function registerHealthCheck() {
function createHealthCheck(options) {
  "use strict";

  const DASHBOARD_KEY = "commercialRadar";
  const DASHBOARD_TITLE = "Health Check";
  const HOST_ID = "commercialRadarDashboard";
  const NAV_ID = "navCommercialRadar";
  const COMPACT_SIDEBAR_CLASS = "pfm-compact-sidebar-active";
  const ROOT_ATTR = "data-planfact-funnel-matrix";
  const STATUS_LABELS = {
    blocked: "Данные заблокированы",
    critical: "Критично",
    signal: "Сигнал",
    watch: "Наблюдать",
    ok: "В норме",
    limited: "Ограничение",
    immature: "Незрело",
    no_data: "Нет данных",
    no_plan: "Нет плана",
    not_applicable: "Не применимо",
  };
  const STATUS_ORDER = [
    "blocked",
    "critical",
    "signal",
    "watch",
    "limited",
    "immature",
    "no_data",
    "no_plan",
    "ok",
    "not_applicable",
  ];
  const numberFormat = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 1,
  });
  const integerFormat = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 0,
  });
  const compactFormat = new Intl.NumberFormat("ru-RU", {
    notation: "compact",
    maximumFractionDigits: 1,
  });

  let requestVersion = 0;
  let activePayload = null;
  let collapsedSections = new Set();
  let inFlightRequest = null;
  let inFlightRequestKey = "";
  let guideReturnFocus = null;
  let trendReturnFocus = null;
  let radarScope = { category: "", product: "", article: "" };
  let radarFilterOptions = { category_names: [] };
  let radarFilterOptionsKey = "";
  let hostProductSearchTimer = null;
  let disposed = false;
  let detailReturnFocus = null;

  function requestJson(url) {
    return options ? options.getJson(url) : getJson(url);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function clientKey() {
    if (options) return options.client;
    if (typeof currentClient === "function") {
      const activeClient = currentClient();
      if (activeClient) return activeClient;
    }
    const urlClient = new URL(window.location.href).searchParams.get("client");
    return state?.client || urlClient || "gloria_jeans";
  }

  function marketplaceKey() {
    if (options) return options.marketplace;
    return String(
      document.getElementById("marketplace")?.value ||
        (typeof currentMarketplace === "function" ? currentMarketplace() : "") ||
        state?.marketplace ||
        "",
    )
      .trim()
      .toLowerCase();
  }

  function marketplaceLabel(value = marketplaceKey()) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "wb") return "WB";
    if (key === "ozon") return "Ozon";
    return key ? key.toUpperCase() : "Площадка не выбрана";
  }

  function payloadMarketplace(payload) {
    return String(
      payload?.marketplace || payload?.meta?.marketplace || marketplaceKey(),
    )
      .trim()
      .toLowerCase();
  }

  function selectedMonth() {
    if (options) return options.month || "";
    const from = document.getElementById("date_from")?.value || "";
    const to = document.getElementById("date_to")?.value || "";
    // Health Check is a monthly report. For a range crossing a month boundary,
    // use the closing month instead of silently falling back to the first day.
    const raw = to || from;
    return /^\d{4}-\d{2}/.test(raw) ? raw.slice(0, 7) : "";
  }

  function normalizeRadarMonthRange() {
    const month = selectedMonth();
    if (!/^\d{4}-\d{2}$/.test(month)) return;
    const [year, monthNumber] = month.split("-").map(Number);
    const lastDay = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
    const dateFrom = `${month}-01`;
    const dateTo = `${month}-${String(lastDay).padStart(2, "0")}`;
    const fromInput = document.getElementById("date_from");
    const toInput = document.getElementById("date_to");
    if (fromInput) fromInput.value = dateFrom;
    if (toInput) toInput.value = dateTo;
    if (typeof updateDateRangeToggle === "function") updateDateRangeToggle();
  }

  function scopeFromUrl() {
    const params = new URLSearchParams(window.location.search || "");
    return {
      category: params.get("radar_category") || "",
      product: params.get("radar_product") || "",
      article: params.get("radar_article") || "",
    };
  }

  function persistScope(nextScope) {
    radarScope = {
      category: String(nextScope?.category || "").trim(),
      product: String(nextScope?.product || "").trim(),
      article: String(nextScope?.article || "").trim(),
    };
    const params = new URLSearchParams(window.location.search || "");
    ["category", "product", "article"].forEach((key) => {
      const urlKey = `radar_${key}`;
      if (radarScope[key]) params.set(urlKey, radarScope[key]);
      else params.delete(urlKey);
    });
    history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }

  function scopeFromHostFilters() {
    const categories =
      typeof selectedValues === "function" ? selectedValues("category") : [];
    return {
      category: String(categories[0] || "").trim(),
      product: String(document.getElementById("product")?.value || "").trim(),
      article: String(document.getElementById("article")?.value || "").trim(),
    };
  }

  function syncHostScopeControls(scope = radarScope) {
    if (typeof setSelectedValues === "function") {
      setSelectedValues("category", scope.category ? [scope.category] : []);
    }
    if (typeof setProductFilter === "function") {
      setProductFilter(scope.product || "");
    }
    const article = document.getElementById("article");
    if (article) article.value = scope.article || "";
  }

  function activeDateRange() {
    return {
      dateFrom: document.getElementById("date_from")?.value || "",
      dateTo: document.getElementById("date_to")?.value || "",
    };
  }

  async function loadRadarFilterOptions(marketplace, category = "") {
    const range = activeDateRange();
    const key = [
      clientKey(),
      marketplace,
      range.dateFrom,
      range.dateTo,
      category,
    ].join("|");
    if (radarFilterOptionsKey === key) return radarFilterOptions;
    const params = new URLSearchParams({
      client: clientKey(),
      dashboard: "funnel",
      marketplace,
    });
    if (range.dateFrom) params.set("date_from", range.dateFrom);
    if (range.dateTo) params.set("date_to", range.dateTo);
    if (category) params.append("categories", category);
    const payload = await getJson(`/api/filters?${params.toString()}`);
    radarFilterOptions = payload && typeof payload === "object" ? payload : { category_names: [] };
    radarFilterOptionsKey = key;
    return radarFilterOptions;
  }

  async function loadHostProductOptions(query = "", category = "", selectedProduct = "") {
    const marketplace = marketplaceKey();
    const range = activeDateRange();
    const params = new URLSearchParams({
      client: clientKey(),
      dashboard: "funnel",
      marketplace,
    });
    const normalizedQuery = String(query || "").trim();
    if (normalizedQuery) params.set("q", normalizedQuery);
    if (range.dateFrom) params.set("date_from", range.dateFrom);
    if (range.dateTo) params.set("date_to", range.dateTo);
    if (category) params.append("categories", category);
    try {
      const payload = await getJson(`/api/funnel-products?${params.toString()}`);
      if (typeof fillProductSelect === "function") {
        fillProductSelect(payload?.product_names || []);
      }
      if (typeof setProductFilter === "function") {
        setProductFilter(selectedProduct || "");
      }
      return payload;
    } catch {
      if (typeof fillProductSelect === "function") fillProductSelect([]);
      return { product_names: [] };
    }
  }

  function isCommercialRadarScreen() {
    return state?.dashboard === DASHBOARD_KEY;
  }

  function isSupportedClient() {
    if (typeof isAvitoOnlyClient === "function" && isAvitoOnlyClient()) return false;
    // The matrix runtime is client-agnostic and validates the selected client's
    // marketplace/source contract itself. Keep the report visible for every
    // configured client and render an explicit DQ/source message on failure.
    return true;
  }

  function ensureClientCapability() {
    (state?.clients || []).forEach((client) => {
      if (!Array.isArray(client.reports)) client.reports = [];
      const marketplaces = new Set(client.marketplaces || []);
      if (marketplaces.size === 1 && marketplaces.has("avito")) {
        client.reports = client.reports.filter((report) => report !== DASHBOARD_KEY);
        return;
      }
      if (!client.reports.includes(DASHBOARD_KEY)) {
        client.reports.push(DASHBOARD_KEY);
      }
    });
  }

  function syncDashboardUrl(dashboard = state?.dashboard) {
    if (!dashboard || !window?.history?.replaceState) return;
    const url = new URL(window.location.href);
    url.searchParams.set("client", clientKey());
    url.searchParams.set("dashboard", dashboard);
    const marketplace = marketplaceKey();
    if (marketplace) url.searchParams.set("marketplace", marketplace);
    else url.searchParams.delete("marketplace");
    const range = activeDateRange();
    if (range.dateFrom) url.searchParams.set("date_from", range.dateFrom);
    else url.searchParams.delete("date_from");
    if (range.dateTo) url.searchParams.set("date_to", range.dateTo);
    else url.searchParams.delete("date_to");
    window.history.replaceState({}, "", url);
  }

  function ensureHost() {
    let host = document.getElementById(HOST_ID);
    if (host) return host;
    host = document.createElement("section");
    host.id = HOST_ID;
    host.className = "commercial-radar-dashboard hidden";
    host.setAttribute("aria-label", DASHBOARD_TITLE);
    const finance = document.getElementById("financeDashboard");
    const filters = document.querySelector(".filters");
    if (finance?.parentNode) finance.parentNode.insertBefore(host, finance);
    else filters?.insertAdjacentElement("afterend", host);
    return host;
  }

  function ensureRoot() {
    const host = ensureHost();
    if (!host) return null;
    let root = host.querySelector(`[${ROOT_ATTR}]`);
    if (!root) {
      root = document.createElement("section");
      root.setAttribute(ROOT_ATTR, "");
      root.className = "pfm-shell";
      host.prepend(root);
    }
    return root;
  }

  function valueTitle(value, unit, ratioColumn) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "Нет подтверждённого значения";
    }
    const numeric = Number(value);
    if (ratioColumn || unit === "percent") {
      return `${numberFormat.format(numeric * 100)}%`;
    }
    if (unit === "rub") return `${integerFormat.format(numeric)} ₽`;
    if (unit === "days") return `${numberFormat.format(numeric)} дн.`;
    if (unit === "score_5") return `${numberFormat.format(numeric)} / 5`;
    return integerFormat.format(numeric);
  }

  function formatValue(value, unit, options = {}) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return '<span class="pfm-empty" title="Нет подтверждённого значения">—</span>';
    }
    const numeric = Number(value);
    const exact = valueTitle(numeric, unit, Boolean(options.ratio));
    let visible;
    if (options.ratio || unit === "percent") {
      visible = `${numberFormat.format(numeric * 100)}%`;
    } else if (unit === "rub") {
      visible = `${compactFormat.format(numeric)} ₽`;
    } else if (unit === "days") {
      visible = `${numberFormat.format(numeric)} дн.`;
    } else if (unit === "score_5") {
      visible = numberFormat.format(numeric);
    } else {
      visible = compactFormat.format(numeric);
    }
    return `<span title="${escapeHtml(exact)}">${escapeHtml(visible)}</span>`;
  }

  function statusLabel(code) {
    return STATUS_LABELS[code] || code || "Без статуса";
  }

  function planLabel(plan) {
    const type = plan?.type || "unavailable";
    const labels = {
      exact_client: "План клиента",
      category_benchmark: "Отраслевой референс",
      history_median: "Медиана 3 мес.",
      guardrail: "Guardrail",
      unavailable: "Нет плана",
    };
    return labels[type] || type;
  }

  function trendNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function normalizedTrend(points, size = 28) {
    const source = Array.isArray(points) ? points : [];
    return Array.from({ length: size }, (_, index) => {
      const point = source[index] || {};
      return {
        index,
        date: String(point.date || ""),
        value: trendNumber(point.value),
      };
    });
  }

  function dateLabel(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return "—";
    const [year, month, day] = String(value).split("-");
    return `${day}.${month}.${year}`;
  }

  function shortDateLabel(value) {
    const full = dateLabel(value);
    return full === "—" ? full : full.slice(0, 5);
  }

  function axisValueLabel(value, unit) {
    if (!Number.isFinite(Number(value))) return "—";
    const numeric = Number(value);
    if (unit === "percent") return `${numberFormat.format(numeric * 100)}%`;
    if (unit === "rub") return `${compactFormat.format(numeric)} ₽`;
    if (unit === "days") return `${numberFormat.format(numeric)} дн.`;
    if (unit === "score_5") return numberFormat.format(numeric);
    return compactFormat.format(numeric);
  }

  function trendRangeLabel(points) {
    const dates = points.map((point) => point.date).filter(Boolean);
    if (!dates.length) return "период не определён";
    return `${dateLabel(dates[0])} — ${dateLabel(dates[dates.length - 1])}`;
  }

  function referenceSourceLabel(reference) {
    const source = String(reference?.source || "");
    const labels = {
      "current_plan.sales_plan_rub": "Клиентский план продаж",
      "current_plan.ad_spend_plan_rub": "Клиентский рекламный бюджет",
      "current_plan.tacos_plan_pct": "Клиентский план TACoS",
      "median(previous_3_month_facts)": "Медиана трёх предыдущих полных месяцев",
      "report_config.default_plan": "Порог из реестра метрик",
    };
    return labels[source] || reference?.label || "Норматив не задан";
  }

  function normalizedWeekly(points) {
    return (Array.isArray(points) ? points : []).map((point, index) => ({
      index,
      weekStart: String(point?.week_start || ""),
      weekEnd: String(point?.week_end || ""),
      value: trendNumber(point?.value),
      days: Number(point?.days) || 0,
      partial: Boolean(point?.is_partial),
    }));
  }

  function normalizedDailyForecast(points, startIndex = 0) {
    return (Array.isArray(points) ? points : []).map((point, index) => ({
      index: startIndex + index,
      date: String(point?.date || ""),
      value: trendNumber(point?.value),
    }));
  }

  function normalizedWeeklyForecast(points, startIndex = 0) {
    return (Array.isArray(points) ? points : []).map((point, index) => ({
      index: startIndex + index,
      weekStart: String(point?.week_start || ""),
      weekEnd: String(point?.week_end || ""),
      value: trendNumber(point?.value),
      days: Number(point?.days) || 7,
      partial: Boolean(point?.is_partial),
    }));
  }

  function sparkline(points, statusCode, metricId, metricName, unit = "") {
    const safePoints = Array.isArray(points) ? points.slice(0, 28) : [];
    const valid = safePoints
      .map((point, index) => ({
        index,
        value: trendNumber(point?.value),
        date: point?.date,
      }))
      .filter((point) => point.value !== null);
    if (!valid.length) {
      return '<span class="pfm-empty pfm-no-trend">нет ряда</span>';
    }
    const values = valid.map((point) => point.value);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const span = maximum - minimum || 1;
    const width = 112;
    const height = 30;
    const x = (index) =>
      safePoints.length <= 1 ? width / 2 : (index / (safePoints.length - 1)) * width;
    const y = (value) => height - 3 - ((value - minimum) / span) * (height - 6);
    const segments = [];
    let current = [];
    safePoints.forEach((point, index) => {
      const value = trendNumber(point?.value);
      if (value === null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push(`${current.length ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`);
    });
    if (current.length) segments.push(current);
    const label = `Динамика за ${safePoints.length} дней: минимум ${valueTitle(
      minimum,
      unit,
      false,
    )}, максимум ${valueTitle(maximum, unit, false)}`;
    return `
      <button type="button" class="pfm-spark-button"
              data-pfm-trend="${escapeHtml(metricId)}"
              aria-haspopup="dialog"
              aria-label="Открыть полный график «${escapeHtml(metricName)}» за два периода по 28 дней"
              title="${escapeHtml(label)} · нажмите, чтобы раскрыть">
        <svg class="pfm-spark pfm-spark--${escapeHtml(statusCode)}"
             viewBox="0 0 ${width} ${height}" aria-hidden="true">
          ${segments
            .map(
              (segment) =>
                `<path d="${segment.join(" ")}" vector-effect="non-scaling-stroke"></path>`,
            )
            .join("")}
        </svg>
      </button>`;
  }

  function trendSegments(points, x, y) {
    const segments = [];
    let current = [];
    points.forEach((point, index) => {
      if (point.value === null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      const pointIndex = Number.isFinite(Number(point.index)) ? Number(point.index) : index;
      current.push(
        `${current.length ? "L" : "M"}${x(pointIndex).toFixed(1)},${y(point.value).toFixed(1)}`,
      );
    });
    if (current.length) segments.push(current);
    return segments;
  }

  function trendChartSvg(row, current, previous) {
    const forecast = normalizedDailyForecast(row?.forecast?.daily_7d, current.length);
    const forecastConnector = forecast.length
      ? [...current].reverse().filter((point) => point.value !== null).slice(0, 1).reverse().concat(forecast)
      : [];
    const previousForecast = normalizedDailyForecast(
      row?.forecast?.previous_period?.daily_7d,
      previous.length,
    );
    const previousForecastConnector = previousForecast.length
      ? [...previous].reverse().filter((point) => point.value !== null).slice(0, 1).reverse().concat(previousForecast)
      : [];
    const referenceValue = trendNumber(row?.chart_reference?.daily_value);
    const validValues = [...current, ...previous, ...forecast, ...previousForecast]
      .map((point) => point.value)
      .filter((value) => value !== null);
    if (referenceValue !== null) validValues.push(referenceValue);
    if (!validValues.length) {
      return '<div class="pfm-trend-empty">Нет подтверждённых дневных значений для графика.</div>';
    }
    const minimum = Math.min(...validValues);
    const maximum = Math.max(...validValues);
    const rawSpan = maximum - minimum;
    const flatPadding =
      row.unit === "percent" ? 0.01 : row.unit === "score_5" ? 0.1 : 1;
    const padding = rawSpan
      ? rawSpan * 0.08
      : Math.max(Math.abs(maximum) * 0.08, flatPadding);
    const domainMin = minimum - padding;
    const domainMax = maximum + padding;
    const domainSpan = domainMax - domainMin || 1;
    const width = 980;
    const height = 350;
    const left = 86;
    const right = 22;
    const top = 20;
    const bottom = 46;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const pointCount = Math.max(
      current.length + forecast.length,
      previous.length + previousForecast.length,
      1,
    );
    const x = (index) =>
      left + (pointCount <= 1 ? plotWidth / 2 : (index / (pointCount - 1)) * plotWidth);
    const y = (value) => top + ((domainMax - value) / domainSpan) * plotHeight;
    const gapBandWidth = Math.max(10, plotWidth / Math.max(pointCount - 1, 1) * 0.72);
    const currentGaps = current.filter((point) => point.value === null);
    const gapBandsHtml = currentGaps
      .map((point) => `
        <rect class="pfm-trend-gap-band" data-series="gap"
              x="${(x(point.index) - gapBandWidth / 2).toFixed(1)}"
              y="${top}" width="${gapBandWidth.toFixed(1)}" height="${plotHeight}">
          <title>${escapeHtml(`Нет подтверждённых данных: ${dateLabel(point.date)}`)}</title>
        </rect>`)
      .join("");
    const yTicks = Array.from({ length: 5 }, (_, index) =>
      domainMax - (domainSpan * index) / 4,
    );
    const xTickIndexes = [0, 6, 13, 20, 27, pointCount - 1].filter(
      (index) => index < pointCount,
    );
    const combinedDates = [...current, ...forecast];
    const renderPaths = (points, series) =>
      trendSegments(points, x, y)
        .map(
          (segment) =>
            `<path class="pfm-trend-line pfm-trend-line--${series}"
                   data-series="${series}" d="${segment.join(" ")}"></path>`,
        )
        .join("");
    const renderPoints = (points, series) =>
      points
        .filter((point) => point.value !== null)
        .map(
          (point) => `
            <circle class="pfm-trend-point pfm-trend-point--${series}"
                    cx="${x(point.index).toFixed(1)}"
                    cy="${y(point.value).toFixed(1)}" r="3">
              <title>${escapeHtml(
                `${series === "current" ? "Последние 28 дней" : series === "forecast" ? "Прогноз текущего периода" : series === "previous-forecast" ? "Прогноз предыдущего периода" : "Предыдущие 28 дней"}: ${dateLabel(point.date)} — ${valueTitle(point.value, row.unit, false)}`,
              )}</title>
            </circle>`,
        )
        .join("");
    return `
      <div class="pfm-trend-chart-scroll">
        <svg class="pfm-trend-chart" viewBox="0 0 ${width} ${height}"
             data-pfm-chart="28d"
             role="img" aria-labelledby="pfmTrendDialogTitle pfmTrendChartDescription">
          <desc id="pfmTrendChartDescription">Линии сопоставляют последние и предыдущие 28 календарных дней; каждое окно продолжено собственным прогнозом на следующие 7 дней, рассчитанным только по доступной на его cutoff истории.</desc>
          ${yTicks
            .map((tick) => {
              const tickY = y(tick).toFixed(1);
              return `<line class="pfm-trend-grid-line" x1="${left}" x2="${width - right}" y1="${tickY}" y2="${tickY}"></line>
                      <text class="pfm-trend-axis-label" x="${left - 12}" y="${tickY}" text-anchor="end" dominant-baseline="middle">${escapeHtml(axisValueLabel(tick, row.unit))}</text>`;
            })
            .join("")}
          <line class="pfm-trend-axis" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}"></line>
          <line class="pfm-trend-axis" x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}"></line>
          ${gapBandsHtml}
          ${referenceValue === null ? "" : `
            <line class="pfm-trend-norm" data-series="norm"
                  x1="${left}" x2="${width - right}"
                  y1="${y(referenceValue).toFixed(1)}" y2="${y(referenceValue).toFixed(1)}">
              <title>${escapeHtml(`${row.chart_reference.label}: ${valueTitle(referenceValue, row.unit, false)} за день`)}</title>
            </line>
            <text class="pfm-trend-norm-label" x="${width - right - 4}" y="${(y(referenceValue) - 7).toFixed(1)}" text-anchor="end">${escapeHtml(axisValueLabel(referenceValue, row.unit))}</text>`}
          ${xTickIndexes
            .map(
              (index) => `
                <text class="pfm-trend-axis-label" x="${x(index).toFixed(1)}" y="${height - 17}" text-anchor="middle">
                  ${escapeHtml(shortDateLabel(combinedDates[index]?.date))}
                </text>`,
            )
            .join("")}
          ${renderPaths(previous, "previous")}
          ${renderPaths(current, "current")}
          ${renderPaths(previousForecastConnector, "previous-forecast")}
          ${renderPaths(forecastConnector, "forecast")}
          ${renderPoints(previous, "previous")}
          ${renderPoints(current, "current")}
          ${renderPoints(previousForecast, "previous-forecast")}
          ${renderPoints(forecast, "forecast")}
        </svg>
      </div>`;
  }

  function weeklyTrendChartSvg(row) {
    const points = normalizedWeekly(row.weekly_ytd);
    const forecast = normalizedWeeklyForecast(row?.forecast?.weekly_4w, points.length);
    const forecastConnector = forecast.length
      ? [...points].reverse().filter((point) => point.value !== null).slice(0, 1).reverse().concat(forecast)
      : [];
    const referenceValue = trendNumber(row?.chart_reference?.weekly_value);
    const values = [...points, ...forecast].map((point) => point.value).filter((value) => value !== null);
    if (referenceValue !== null) values.push(referenceValue);
    if (!values.length) {
      return '<div class="pfm-trend-empty">Нет подтверждённых недельных значений с начала года.</div>';
    }
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const rawSpan = maximum - minimum;
    const flatPadding = row.unit === "percent" ? 0.01 : row.unit === "score_5" ? 0.1 : 1;
    const padding = rawSpan ? rawSpan * 0.08 : Math.max(Math.abs(maximum) * 0.08, flatPadding);
    const domainMin = minimum - padding;
    const domainMax = maximum + padding;
    const domainSpan = domainMax - domainMin || 1;
    const width = 980;
    const height = 330;
    const left = 86;
    const right = 22;
    const top = 20;
    const bottom = 46;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const pointCount = Math.max(points.length + forecast.length, 1);
    const x = (index) => left + (pointCount <= 1 ? plotWidth / 2 : (index / (pointCount - 1)) * plotWidth);
    const y = (value) => top + ((domainMax - value) / domainSpan) * plotHeight;
    const yTicks = Array.from({ length: 5 }, (_, index) => domainMax - (domainSpan * index) / 4);
    const tickStep = Math.max(1, Math.ceil(pointCount / 6));
    const xTickIndexes = [...new Set([0, ...Array.from({ length: 6 }, (_, index) => index * tickStep), pointCount - 1])]
      .filter((index) => index >= 0 && index < pointCount);
    const combinedWeeks = [...points, ...forecast];
    return `
      <div class="pfm-trend-chart-scroll">
        <svg class="pfm-trend-chart" viewBox="0 0 ${width} ${height}"
             data-pfm-chart="weekly-ytd" role="img"
             aria-label="${escapeHtml(`Недельная динамика метрики ${row.name} с начала года`)}">
          ${yTicks.map((tick) => {
            const tickY = y(tick).toFixed(1);
            return `<line class="pfm-trend-grid-line" x1="${left}" x2="${width - right}" y1="${tickY}" y2="${tickY}"></line>
                    <text class="pfm-trend-axis-label" x="${left - 12}" y="${tickY}" text-anchor="end" dominant-baseline="middle">${escapeHtml(axisValueLabel(tick, row.unit))}</text>`;
          }).join("")}
          <line class="pfm-trend-axis" x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}"></line>
          <line class="pfm-trend-axis" x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}"></line>
          ${xTickIndexes.map((index) => `<text class="pfm-trend-axis-label" x="${x(index).toFixed(1)}" y="${height - 17}" text-anchor="middle">${escapeHtml(shortDateLabel(combinedWeeks[index]?.weekStart))}</text>`).join("")}
          ${referenceValue === null ? "" : `
            <line class="pfm-trend-norm" data-series="weekly-norm"
                  x1="${left}" x2="${width - right}"
                  y1="${y(referenceValue).toFixed(1)}" y2="${y(referenceValue).toFixed(1)}">
              <title>${escapeHtml(`${row.chart_reference.label}: ${valueTitle(referenceValue, row.unit, false)} за 7 дней`)}</title>
            </line>
            <text class="pfm-trend-norm-label" x="${width - right - 4}" y="${(y(referenceValue) - 7).toFixed(1)}" text-anchor="end">${escapeHtml(axisValueLabel(referenceValue, row.unit))}</text>`}
          ${trendSegments(points, x, y).map((segment) => `<path class="pfm-trend-line pfm-trend-line--weekly" data-series="weekly" d="${segment.join(" ")}"></path>`).join("")}
          ${trendSegments(forecastConnector, x, y).map((segment) => `<path class="pfm-trend-line pfm-trend-line--weekly-forecast" data-series="weekly-forecast" d="${segment.join(" ")}"></path>`).join("")}
          ${points.filter((point) => point.value !== null).map((point) => `
            <circle class="pfm-trend-point pfm-trend-point--weekly${point.partial ? " pfm-trend-point--partial" : ""}"
                    cx="${x(point.index).toFixed(1)}" cy="${y(point.value).toFixed(1)}" r="3">
              <title>${escapeHtml(`${dateLabel(point.weekStart)} — ${dateLabel(point.weekEnd)}${point.partial ? " · неполная неделя" : ""}: ${valueTitle(point.value, row.unit, false)}`)}</title>
            </circle>`).join("")}
          ${forecast.filter((point) => point.value !== null).map((point) => `
            <circle class="pfm-trend-point pfm-trend-point--weekly-forecast"
                    cx="${x(point.index).toFixed(1)}" cy="${y(point.value).toFixed(1)}" r="3">
              <title>${escapeHtml(`Прогноз: ${dateLabel(point.weekStart)} — ${dateLabel(point.weekEnd)}: ${valueTitle(point.value, row.unit, false)}`)}</title>
            </circle>`).join("")}
        </svg>
      </div>`;
  }

  function trendInsightHtml(row) {
    const insight = row?.chart_insight || {};
    const reference = row?.chart_reference || {};
    const hasReference = trendNumber(reference.daily_value) !== null;
    const deviationRatio = trendNumber(insight.deviation_ratio);
    const changeRatio = trendNumber(insight.change_ratio);
    return `
      <section class="pfm-insight-grid" aria-label="Заключение по метрике" data-pfm-trend-insight>
        <article class="pfm-insight-card">
          <span>Норматив на день</span>
          <strong>${hasReference ? formatValue(reference.daily_value, row.unit) : "Не задан"}</strong>
          <small>${escapeHtml(hasReference ? referenceSourceLabel(reference) : "Подтверждённого плана или guardrail нет")}</small>
        </article>
        <article class="pfm-insight-card">
          <span>Последний факт</span>
          <strong>${formatValue(insight.latest_value, row.unit)}</strong>
          <small>${escapeHtml(dateLabel(insight.latest_date))}</small>
        </article>
        <article class="pfm-insight-card">
          <span>Отклонение от норматива</span>
          <strong>${hasReference ? formatValue(insight.deviation, row.unit) : "—"}</strong>
          <small>${deviationRatio === null ? "Нельзя рассчитать" : `${escapeHtml(numberFormat.format(deviationRatio * 100))}%`}</small>
        </article>
        <article class="pfm-insight-card pfm-insight-card--${escapeHtml(insight.state_code || "no_data")}">
          <span>Заключение · статус MTD</span>
          <strong>${escapeHtml(insight.state_label || "Недостаточно данных")}</strong>
          <small>${escapeHtml(insight.dynamics_label || "Динамика не определена")}${changeRatio === null ? "" : ` · ${escapeHtml(numberFormat.format(changeRatio * 100))}% за 7 к 7 дням`}</small>
        </article>
      </section>`;
  }

  function forecastQualityHtml(row) {
    const forecast = row?.forecast || {};
    if (!forecast.available) {
      return `<p class="pfm-forecast-note pfm-forecast-note--unavailable"><b>Прогноз недоступен.</b> ${escapeHtml(forecast.reason || "Недостаточно подтверждённых дневных данных.")}</p>`;
    }
    const confidenceLabels = { high: "Высокое", medium: "Среднее", low: "Низкое" };
    const confidence = confidenceLabels[forecast.confidence] || "Не определено";
    return `
      <p class="pfm-forecast-note pfm-forecast-note--${escapeHtml(forecast.confidence || "low")}" data-pfm-forecast-quality>
        <b>Прогноз: 7 дней и 4 недели.</b>
        История: ${escapeHtml(String(forecast.history_months_used || 0))} мес., ${escapeHtml(String(forecast.history_days_used || 0))} дней · доверие: <b>${escapeHtml(confidence)}</b>.
        ${forecast.warning ? `<span>${escapeHtml(forecast.warning)}</span>` : ""}
      </p>`;
  }

  function metricGapHtml(row) {
    const gaps = row?.data_gaps || {};
    const current = gaps.current || {};
    const previous = gaps.previous || {};
    const currentDates = Array.isArray(current.missing_dates) ? current.missing_dates : [];
    const previousDates = Array.isArray(previous.missing_dates) ? previous.missing_dates : [];
    if (!gaps.has_gap) {
      return `<section class="pfm-gap-card pfm-gap-card--ok" data-pfm-data-gaps>
        <span class="pfm-gap-icon" aria-hidden="true">✓</span>
        <div><b>Разрывов в сравниваемых окнах нет</b><small>${escapeHtml(gaps.impact || "Дневной ряд полный.")}</small></div>
      </section>`;
    }
    const fields = Array.isArray(gaps.unusable_fields) ? gaps.unusable_fields : [];
    const dates = currentDates.map(dateLabel).join(", ") || "в текущем окне нет";
    return `<section class="pfm-gap-card pfm-gap-card--warning" data-pfm-data-gaps
                    data-gap-reason="${escapeHtml(gaps.reason_code || "metric_value_missing")}">
      <span class="pfm-gap-icon" aria-hidden="true">!</span>
      <div>
        <b>${escapeHtml(gaps.title || "Есть разрыв данных")}</b>
        <p>${escapeHtml(gaps.explanation || "Пропуски не заменены нулями.")}</p>
        <dl class="pfm-gap-facts">
          <div><dt>Нет дат</dt><dd>${escapeHtml(dates)}</dd></div>
          <div><dt>Покрытие</dt><dd>${escapeHtml(String(current.available_days ?? "—"))}/${escapeHtml(String(current.expected_days ?? "—"))} дней · ${current.coverage_pct == null ? "—" : `${escapeHtml(numberFormat.format(current.coverage_pct))}%`}</dd></div>
          <div><dt>Предыдущее окно</dt><dd>${previousDates.length ? escapeHtml(previousDates.map(dateLabel).join(", ")) : "полное"}</dd></div>
        </dl>
        ${fields.length ? `<p><b>Не хватает надёжных значений:</b> ${fields.map((item) => escapeHtml(item.label || item.field)).join(", ")}.</p>` : ""}
        <small>${escapeHtml(gaps.impact || "Надёжность сравнения снижена.")}</small>
      </div>
    </section>`;
  }

  function codexMetricConclusionHtml(row) {
    const insight = row?.chart_insight || {};
    const gaps = row?.data_gaps || {};
    const forecast = row?.forecast || {};
    let text = "Данных недостаточно для содержательного вывода.";
    let kind = "limited";
    if ((gaps.current?.missing_dates || []).length) {
      text = `Сначала восстановить пропущенные даты. Сейчас подтверждена проблема источника, а не бизнес-причина изменения метрики. ${gaps.impact || ""}`.trim();
      kind = "blocked";
    } else if (insight.latest_value !== null && insight.latest_value !== undefined) {
      const confidence = { high: "высокую", medium: "среднюю", low: "низкую" }[forecast.confidence] || "неопределённую";
      text = `${insight.conclusion || `${insight.state_label || "Статус не определён"}. ${insight.dynamics_label || ""}`.trim()} Прогноз имеет ${confidence} надёжность; причинная ветка требует отдельной проверки гипотез.`;
      kind = insight.state_code || "limited";
    }
    return `<section class="pfm-codex-conclusion pfm-codex-conclusion--${escapeHtml(kind)}" data-pfm-codex-conclusion>
      <div class="pfm-codex-conclusion-title"><span aria-hidden="true">✦</span><b>Вывод по данным</b><small>по проверяемым данным</small></div>
      <p>${escapeHtml(text)}</p>
    </section>`;
  }

  function hypothesisMetricLabel(metricId) {
    const row = (activePayload?.rows || []).find((item) => item.id === metricId);
    return row?.guide?.short_label || row?.name || "Проверяемая метрика";
  }

  function hypothesisMetricList(items, emptyText) {
    const source = Array.isArray(items) ? items : [];
    if (!source.length) return `<p class="pfm-reader-empty">${escapeHtml(emptyText)}</p>`;
    return `<ul class="pfm-reader-metric-list">${source.map((item) => {
      const canComparePlan = !["no_data", "limited", "immature"].includes(item.status);
      const achievement = item.achievement_pct == null || !canComparePlan ? "" : ` · ${numberFormat.format(item.achievement_pct)}% плана`;
      return `<li class="pfm-reader-metric pfm-reader-metric--${escapeHtml(item.status || "no_data")}">
        <b>${escapeHtml(item.label || hypothesisMetricLabel(item.metric_id))}</b>
        <span>${escapeHtml(item.status_label || statusLabel(item.status))}${escapeHtml(achievement)}</span>
      </li>`;
    }).join("")}</ul>`;
  }

  function hypothesisReaderSummaryHtml(result) {
    const summary = result?.reader_summary || {};
    const primary = summary.primary_bottleneck;
    return `<section class="pfm-reader-diagnosis" data-pfm-reader-diagnosis>
      <article class="pfm-reader-card pfm-reader-card--wide">
        <span>Что происходит</span>
        <strong>${escapeHtml(summary.what_happened || result?.conclusion?.text || "Вывод не сформирован.")}</strong>
      </article>
      <article class="pfm-reader-card pfm-reader-card--problem">
        <span>Где найден первый провал</span>
        ${primary ? `<strong>${escapeHtml(primary.label || "Проверяемая метрика")}</strong><small>${escapeHtml(primary.status_label || statusLabel(primary.status))}${primary.achievement_pct == null ? "" : ` · ${escapeHtml(numberFormat.format(primary.achievement_pct))}% плана`}</small>` : "<strong>Провал пока не локализован</strong>"}
      </article>
      <article class="pfm-reader-card pfm-reader-card--normal">
        <span>Что уже проверено и в норме</span>
        ${hypothesisMetricList(summary.normal_metrics, "Подтверждённых нормальных звеньев пока нет.")}
      </article>
      <article class="pfm-reader-card pfm-reader-card--missing">
        <span>Чего не хватает для подтверждения</span>
        ${hypothesisMetricList(summary.missing_metrics, "Явных пробелов в доступных проверках нет.")}
      </article>
      <article class="pfm-reader-card pfm-reader-card--wide pfm-reader-card--hypothesis">
        <span>Рабочая гипотеза</span>
        <strong>${escapeHtml(summary.leading_hypothesis || "Причина пока не подтверждена.")}</strong>
        <small>${escapeHtml(summary.limitation || "")}</small>
      </article>
      <article class="pfm-reader-card pfm-reader-card--wide pfm-reader-card--action">
        <span>Что проверить первым</span>
        <strong>${escapeHtml(summary.next_step || "Уточнить доказательную базу и повторить анализ.")}</strong>
      </article>
    </section>`;
  }

  function hypothesisCauseRows(node, depth = 0, parentTitle = "", rows = []) {
    if (!node || depth > 5) return rows;
    const hypotheses = Array.isArray(node.hypotheses) ? node.hypotheses : [];
    hypotheses.forEach((item) => {
      const next = item?.next || null;
      rows.push({
        item,
        depth,
        parentTitle: parentTitle || node.title || "Проверяемая метрика",
        terminal: next?.terminal || null,
      });
      if (next && Array.isArray(next.hypotheses) && next.hypotheses.length) {
        hypothesisCauseRows(next, depth + 1, item.title || node.title || "Гипотеза", rows);
      }
    });
    return rows;
  }

  function hypothesisCauseFactsHtml(item) {
    const evidence = Array.isArray(item?.evidence) ? item.evidence : [];
    if (!evidence.length) {
      return `<span class="pfm-cause-empty">Нет подключённых фактов</span>`;
    }
    const visible = evidence.slice(0, 3);
    return `<div class="pfm-cause-facts">${visible.map((fact) => {
      const comparison = fact?.comparison || {};
      const label = fact?.label || hypothesisMetricLabel(fact?.metric_id);
      if (!comparison.available) {
        return `<span class="pfm-cause-fact pfm-cause-fact--missing"><b>${escapeHtml(label)}</b><small>нет сопоставимых данных</small></span>`;
      }
      const change = comparison.change_pct == null
        ? "—"
        : formatValue(comparison.change_pct / 100, "", { ratio: true });
      return `<span class="pfm-cause-fact">
        <b>${escapeHtml(label)}</b>
        <small>${formatValue(comparison.previous, fact?.unit)} → ${formatValue(comparison.current, fact?.unit)} · ${change}</small>
      </span>`;
    }).join("")}${evidence.length > visible.length ? `<small class="pfm-cause-more">+${escapeHtml(String(evidence.length - visible.length))} проверки</small>` : ""}</div>`;
  }

  function hypothesisCauseRowHtml(row) {
    const item = row.item || {};
    const status = item.status || "evidence_gap";
    const levelLabel = row.depth === 0
      ? "Корневая гипотеза"
      : `Влияет на «${row.parentTitle || "гипотезу выше"}»`;
    const terminalConclusion = row.terminal?.conclusion || "";
    const conclusion = terminalConclusion || item.verdict || "Вывод пока не сформирован.";
    return `<tr class="pfm-cause-row pfm-cause-row--${row.depth === 0 ? "root" : "driver"} pfm-cause-row--${escapeHtml(status)}${item.selected ? " is-selected" : ""}">
      <th scope="row">
        <div class="pfm-cause-hypothesis" style="--cause-depth:${escapeHtml(String(row.depth))}">
          <span>${escapeHtml(levelLabel)}</span>
          <strong>${escapeHtml(item.title || "Проверить гипотезу")}</strong>
          ${item.selected ? "<small>Основная доказательная ветка</small>" : ""}
        </div>
      </th>
      <td>${hypothesisCauseFactsHtml(item)}</td>
      <td><span class="pfm-cause-status pfm-cause-status--${escapeHtml(status)}">${escapeHtml(item.status_label || "Нужны данные")}</span></td>
      <td><div class="pfm-cause-conclusion"><b>${escapeHtml(conclusion)}</b>${item.action ? `<small><span>Следующий шаг:</span> ${escapeHtml(item.action)}</small>` : ""}</div></td>
    </tr>`;
  }

  function hypothesisCauseSummaryHtml(result) {
    const summary = result?.reader_summary || {};
    const tree = result?.evidence_tree || null;
    const rows = hypothesisCauseRows(tree);
    if (!rows.length) return hypothesisReaderSummaryHtml(result);
    return `<section class="pfm-cause-summary" data-pfm-cause-summary>
      <header>
        <div><span class="pfm-eyebrow">СВОДКА ПРИЧИН</span><h5>Корневые и влияющие гипотезы</h5></div>
        <small>Строки с отступом объясняют, что влияет на гипотезу уровнем выше.</small>
      </header>
      <div class="pfm-cause-context">
        <b>${escapeHtml(summary.what_happened || result?.conclusion?.text || "Изменение метрики анализируется.")}</b>
        <small>${escapeHtml(summary.leading_hypothesis || "Причина пока не подтверждена.")}</small>
      </div>
      <div class="pfm-cause-summary-wrap">
        <table class="pfm-cause-summary-table">
          <colgroup><col class="pfm-cause-col-hypothesis"><col class="pfm-cause-col-facts"><col class="pfm-cause-col-status"><col class="pfm-cause-col-conclusion"></colgroup>
          <thead><tr><th>Гипотеза и зависимость</th><th>Факты: было → стало</th><th>Статус</th><th>Вывод и следующий шаг</th></tr></thead>
          <tbody>${rows.map(hypothesisCauseRowHtml).join("")}</tbody>
        </table>
      </div>
    </section>`;
  }

  function hypothesisEvidenceMetricHtml(item) {
    const comparison = item?.comparison || {};
    const relation = item?.relation_to_target || {};
    const comparable = Boolean(comparison.available);
    return `<article class="pfm-evidence-metric pfm-evidence-metric--${escapeHtml(item?.status || "no_data")}">
      <header><b>${escapeHtml(item?.label || hypothesisMetricLabel(item?.metric_id))}</b><span>${escapeHtml(item?.status_label || statusLabel(item?.status || "no_data"))}</span></header>
      ${comparable ? `<dl>
        <div><dt>Текущий период</dt><dd>${formatValue(comparison.current, item?.unit)}</dd></div>
        <div><dt>Предыдущий</dt><dd>${formatValue(comparison.previous, item?.unit)}</dd></div>
        <div><dt>Изменение</dt><dd>${formatValue(comparison.change_pct == null ? null : comparison.change_pct / 100, "", { ratio: true })}</dd></div>
      </dl><small>${escapeHtml(relation.label || "сопоставимость не рассчитана")} · ${escapeHtml(String(comparison.aligned_points || 0))} сопоставимых дней</small>` : `<p>Нет двух сопоставимых окон. Нулевые значения не подставляются.</p>`}
    </article>`;
  }

  function oosEvidenceHtml(diagnostic) {
    if (!diagnostic || !diagnostic.version) return "";
    const affected = Array.isArray(diagnostic.affected_skus) ? diagnostic.affected_skus.slice(0, 5) : [];
    const current = diagnostic.current_period || {};
    const previous = diagnostic.previous_period || {};
    const rule = diagnostic.rule || {};
    const statusLabelMap = { supported: "Поддержана фактами", refuted: "Не поддержана", evidence_gap: "Нужны данные" };
    const stockRule = Number(rule.stock_zero_or_cover_below_days ?? 3);
    const dropRule = Math.abs(Number(rule.strong_sales_drop_pct ?? -30));
    return `<section class="pfm-oos-evidence pfm-oos-evidence--${escapeHtml(diagnostic.status || "evidence_gap")}" data-pfm-oos-evidence>
      <header>
        <span><b>Проверка потери продаж из-за OOS</b><small>${escapeHtml(diagnostic.reason || "Проверка не завершена.")}</small></span>
        <em>${escapeHtml(statusLabelMap[diagnostic.status] || "Нужны данные")}</em>
      </header>
      <dl class="pfm-oos-summary">
        <div><dt>Текущий период</dt><dd>${escapeHtml(current.from || "—")} — ${escapeHtml(current.to || "—")}</dd><small>${escapeHtml(String(current.observed_days ?? 0))} дней с данными</small></div>
        <div><dt>Предыдущий период</dt><dd>${escapeHtml(previous.from || "—")} — ${escapeHtml(previous.to || "—")}</dd><small>${escapeHtml(String(previous.observed_days ?? 0))} дней с данными</small></div>
        <div><dt>Последний остаток</dt><dd>${escapeHtml(diagnostic.stock_snapshot_date || "нет данных")}</dd><small>${escapeHtml(diagnostic.stock_source_label || "источник не определён")} · лаг ${escapeHtml(String(diagnostic.stock_lag_days ?? "—"))} дн.</small></div>
        <div><dt>Затронуто SKU</dt><dd>${escapeHtml(String(diagnostic.affected_sku_count ?? 0))}</dd><small>проверено ${escapeHtml(String(diagnostic.evaluated_sku_count ?? 0))}</small></div>
        <div><dt>Оценка потерь</dt><dd>${formatValue(diagnostic.estimated_lost_units, "units")}</dd><small>${formatValue(diagnostic.estimated_lost_revenue_rub, "rub")}</small></div>
      </dl>
      <p class="pfm-oos-rule"><b>Правило:</b> продажи SKU были в предыдущем окне; сейчас отсутствуют или снизились минимум на ${escapeHtml(String(dropRule))}%; последний остаток равен 0 либо покрывает меньше ${escapeHtml(String(stockRule))} дней по средней скорости предыдущих 28 дней.</p>
      ${diagnostic.partial ? `<p class="pfm-oos-warning">По ${escapeHtml(String(diagnostic.missing_stock_sku_count || 0))} SKU остаток отсутствует в источнике. Эти пропуски не считаются нулём и не включаются в подтверждённую потерю.</p>` : ""}
      ${affected.length ? `<div class="pfm-oos-table-wrap"><table class="pfm-oos-table">
        <thead><tr><th>Товар / SKU</th><th>Продажи: было</th><th>Стало</th><th>Изменение</th><th>Остаток</th><th>Покрытие</th><th>Оценка потери</th></tr></thead>
        <tbody>${affected.map((row) => `<tr>
          <th><b>${escapeHtml(row.product_name || row.seller_article || row.sku || "SKU")}</b><small>${escapeHtml(row.seller_article || row.sku || "")}</small></th>
          <td>${formatValue(row.previous_units, "units")}</td>
          <td>${formatValue(row.current_units, "units")}</td>
          <td>${formatValue(row.sales_change_pct == null ? null : row.sales_change_pct / 100, "", { ratio: true })}</td>
          <td>${formatValue(row.stock_available_qty, "units")}</td>
          <td>${row.stock_cover_days == null ? "—" : `${escapeHtml(String(row.stock_cover_days))} дн.`}</td>
          <td><b>${formatValue(row.estimated_lost_units, "units")}</b><small>${formatValue(row.estimated_lost_revenue_rub, "rub")}</small></td>
        </tr>`).join("")}</tbody>
      </table></div>` : `<p class="pfm-reader-empty">SKU, одновременно прошедшие оба условия, не найдены либо данных для проверки недостаточно.</p>`}
      <small class="pfm-oos-limit">Оценка равна положительному разрыву с предыдущим сопоставимым периодом. Это локализует потери, но не доказывает, что весь разрыв вызван только остатком.</small>
    </section>`;
  }


  function evidenceHypothesisHtml(item, depth = 0) {
    if (!item || depth > 5) return "";
    const evidence = Array.isArray(item.evidence) ? item.evidence : [];
    const open = item.selected ? " open" : "";
    return `<details class="pfm-evidence-hypothesis pfm-evidence-hypothesis--${escapeHtml(item.status || "evidence_gap")}"${open}>
      <summary>
        <span class="pfm-evidence-path" aria-hidden="true">${escapeHtml(String(depth + 1))}</span>
        <span><b>${escapeHtml(item.title || "Проверить гипотезу")}</b><small>${escapeHtml(item.verdict || "")}</small></span>
        <em>${escapeHtml(item.status_label || "Нужны данные")}</em>
      </summary>
      <div class="pfm-evidence-hypothesis-body">
        ${oosEvidenceHtml(item.oos_evidence)}
        ${evidence.length ? `<div class="pfm-evidence-facts">${evidence.map(hypothesisEvidenceMetricHtml).join("")}</div>` : `<p class="pfm-reader-empty">Для этой ветки измеримые факты ещё не подключены.</p>`}
        ${item.action ? `<p class="pfm-evidence-action"><b>Следующая проверка:</b> ${escapeHtml(item.action)}</p>` : ""}
        <p class="pfm-evidence-causal-limit">${escapeHtml(item.causal_limit || "")}</p>
        ${item.next ? evidenceTreeHtml(item.next, depth + 1) : ""}
      </div>
    </details>`;
  }

  function evidenceTreeHtml(node, depth = 0) {
    if (!node || depth > 5) return "";
    const metric = node.metric || {};
    const hypotheses = Array.isArray(node.hypotheses) ? node.hypotheses : [];
    return `<section class="pfm-evidence-tree" data-depth="${depth}" data-pfm-evidence-tree>
      <header class="pfm-evidence-tree-header">
        <span class="pfm-eyebrow">ДОКАЗАТЕЛЬНОЕ ДЕРЕВО</span>
        <h5>${escapeHtml(node.title || "Проверка причин")}</h5>
        <p>Сначала локализуем драйвер на сопоставимом окне, затем раскрываем причины следующего уровня. Синхронность не выдаётся за причинность.</p>
      </header>
      ${metric?.metric_id ? `<div class="pfm-evidence-root">${hypothesisEvidenceMetricHtml(metric)}</div>` : ""}
      ${hypotheses.length ? `<div class="pfm-evidence-branches">${hypotheses.map((item) => evidenceHypothesisHtml(item, depth)).join("")}</div>` : ""}
      ${node.terminal ? `<article class="pfm-evidence-terminal pfm-evidence-terminal--${escapeHtml(node.terminal.status || "evidence_gap")}"><b>${escapeHtml(node.terminal.conclusion || "Проверка остановлена на границе доказательств.")}</b><small>${escapeHtml(node.terminal.causal_limit || "")}</small></article>` : ""}
    </section>`;
  }

  function hypothesisTreeHtml(node, depth = 0) {
    if (!node || depth > 4) return "";
    const checks = Array.isArray(node.checks) ? node.checks : [];
    const branches = Array.isArray(node.branches) ? node.branches : [];
    return `<section class="pfm-hypothesis-node" data-depth="${depth}">
      <header><b>${escapeHtml(node.title || "Проверка")}</b></header>
      ${checks.length ? `<div class="pfm-hypothesis-checks">${checks.map((check) => `<span class="pfm-check-chip pfm-check-chip--${escapeHtml(check.status || "no_data")}">${escapeHtml(hypothesisMetricLabel(check.metric_id))} · ${escapeHtml(statusLabel(check.status || "no_data"))}</span>`).join("")}</div>` : ""}
      ${node.conclusion ? `<p>${escapeHtml(node.conclusion)}</p>` : ""}
      ${branches.map((branch) => `<section class="pfm-hypothesis-branch pfm-hypothesis-branch--selected">
        <p><b>Следующая проверка:</b> ${escapeHtml(branch.next?.title || "уточнить доказательства")}</p>
        ${branch.action ? `<p><b>Действие:</b> ${escapeHtml(branch.action)}</p>` : ""}
        ${hypothesisTreeHtml(branch.next, depth + 1)}
      </section>`).join("")}
    </section>`;
  }

  function hypothesisResultHtml(result) {
    const scenarios = Array.isArray(result?.all_scenarios) ? result.all_scenarios : [];
    const engine = result?.engine || {};
    return `<section class="pfm-hypothesis-result-card" data-pfm-hypothesis-analysis>
      <header>
        <div><span class="pfm-eyebrow">РЕЗУЛЬТАТ ПРОВЕРКИ</span><h4>${escapeHtml(result?.scenario?.label || "Сценарий не определён")}</h4></div>
        <span class="pfm-engine-badge pfm-engine-badge--${escapeHtml(engine.model_status || "not_configured")}">${engine.model_status === "completed" ? `Заключение модели · ${escapeHtml(engine.model || "")}` : "Проверка по правилам"}</span>
      </header>
      ${hypothesisCauseSummaryHtml(result)}
      <details class="pfm-analysis-details pfm-analysis-details--evidence">
        <summary>Подробные доказательства</summary>
        ${evidenceTreeHtml(result?.evidence_tree)}
      </details>
      <details class="pfm-analysis-details">
        <summary>Как определён сценарий</summary>
        <div class="pfm-scenario-grid">${scenarios.map((scenario) => `<article class="pfm-scenario-card${scenario.active ? " is-active" : ""}"><b>${escapeHtml(scenario.label)}</b>${scenario.active ? "<span>Текущий сценарий</span>" : ""}</article>`).join("")}</div>
      </details>
      <details class="pfm-analysis-details pfm-analysis-details--technical">
        <summary>Техническая логика для аналитика</summary>
        ${engine.model_message ? `<p class="pfm-model-note">${escapeHtml(engine.model_message)}</p>` : ""}
        ${hypothesisTreeHtml(result?.relevant_path || result?.diagnostic_tree)}
      </details>
    </section>`;
  }

  function hypothesisLaunchHtml(row) {
    return `<section class="pfm-hypothesis-launch" data-pfm-hypothesis-launch>
      <div><b>Найти причину изменения</b><small>Пять сценариев: резкое падение/скачок, трендовый рост/снижение и плато. Сначала проверяется качество данных.</small></div>
      <button type="button" class="pfm-hypothesis-button" data-pfm-hypothesis-run="${escapeHtml(row.id)}" aria-label="Запустить анализ гипотез" title="Запустить анализ гипотез">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm6 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM9 10v2a3 3 0 0 0 3 3h0m3-1V9a3 3 0 0 0-3-3h0"/></svg>
        <span>Анализ гипотез</span>
      </button>
      <div class="pfm-hypothesis-result" data-pfm-hypothesis-result hidden></div>
    </section>`;
  }

  function trendTableRows(row, current, previous) {
    return current
      .map((currentPoint, index) => {
        const previousPoint = previous[index] || { date: "", value: null };
        const comparable =
          currentPoint.value !== null && previousPoint.value !== null;
        const delta = comparable
          ? currentPoint.value - previousPoint.value
          : null;
        const deltaRatio =
          comparable && previousPoint.value !== 0
            ? currentPoint.value / previousPoint.value - 1
            : null;
        return `
          <tr>
            <td class="pfm-trend-day">${index + 1}</td>
            <td>${escapeHtml(dateLabel(currentPoint.date))}</td>
            <td class="pfm-number">${formatValue(currentPoint.value, row.unit)}</td>
            <td>${escapeHtml(dateLabel(previousPoint.date))}</td>
            <td class="pfm-number">${formatValue(previousPoint.value, row.unit)}</td>
            <td class="pfm-number">${formatValue(delta, row.unit)}</td>
            <td class="pfm-number">${formatValue(deltaRatio, "", { ratio: true })}</td>
          </tr>`;
      })
      .join("");
  }

  function trendDialogHtml(row) {
    const trendDays = Number(activePayload?.trend_days) || 28;
    const current = normalizedTrend(row.trend_28d, trendDays);
    const previous = normalizedTrend(row.trend_previous_28d, trendDays);
    const forecastDaily = normalizedDailyForecast(row?.forecast?.daily_7d);
    const previousForecastDaily = normalizedDailyForecast(
      row?.forecast?.previous_period?.daily_7d,
    );
    return `
      <header class="pfm-trend-dialog-header">
        <span class="pfm-eyebrow">${escapeHtml(row.section || "")}</span>
        <h3 id="pfmTrendDialogTitle">${escapeHtml(row.name || row.id)}</h3>
        <p>Статус месяца, дневное отклонение от норматива и динамика на двух временных масштабах.</p>
      </header>
      ${trendInsightHtml(row)}
      ${metricGapHtml(row)}
      ${forecastQualityHtml(row)}
      ${codexMetricConclusionHtml(row)}
      ${hypothesisLaunchHtml(row)}
      <h4>Последние и предыдущие ${trendDays} дней</h4>
      <div class="pfm-trend-legend" aria-label="Периоды сравнения">
        <span><i class="pfm-trend-key pfm-trend-key--current"></i><b>Последние ${trendDays} дней</b> · ${escapeHtml(trendRangeLabel(current))}</span>
        <span><i class="pfm-trend-key pfm-trend-key--previous"></i><b>Предыдущие ${trendDays} дней</b> · ${escapeHtml(trendRangeLabel(previous))}</span>
        ${row?.forecast?.previous_period?.available ? `<span><i class="pfm-trend-key pfm-trend-key--previous-forecast"></i><b>Прогноз предыдущего периода</b> · ${escapeHtml(trendRangeLabel(previousForecastDaily))}</span>` : ""}
        ${row?.forecast?.available ? `<span><i class="pfm-trend-key pfm-trend-key--forecast"></i><b>Прогноз 7 дней</b> · ${escapeHtml(trendRangeLabel(forecastDaily))}</span>` : ""}
        ${row?.chart_reference?.available ? `<span><i class="pfm-trend-key pfm-trend-key--norm"></i><b>${escapeHtml(row.chart_reference.label)}</b> · ${formatValue(row.chart_reference.daily_value, row.unit)} в день</span>` : `<span><i class="pfm-trend-key pfm-trend-key--unavailable"></i><b>Норматив не задан</b></span>`}
      </div>
      ${trendChartSvg(row, current, previous)}
      <h4>По неделям с начала года</h4>
      <div class="pfm-trend-legend" aria-label="Недельный ряд">
        <span><i class="pfm-trend-key pfm-trend-key--current"></i><b>Факт по календарным неделям</b></span>
        ${row?.forecast?.available ? `<span><i class="pfm-trend-key pfm-trend-key--forecast"></i><b>Прогноз на 4 недели</b></span>` : ""}
        ${row?.chart_reference?.available ? `<span><i class="pfm-trend-key pfm-trend-key--norm"></i><b>Текущий ориентир на 7 дней</b> · ${formatValue(row.chart_reference.weekly_value, row.unit)}</span>` : ""}
      </div>
      ${weeklyTrendChartSvg(row)}
      <p class="pfm-dialog-note">Первая и текущая недели могут быть неполными. Для потоковых метрик недельный норматив = текущий месячный план / календарные дни месяца × 7; для конверсий и ставок используется тот же уровень.</p>
      <h4>Данные по дням</h4>
      <div class="pfm-trend-table-wrap">
        <table class="pfm-trend-table" data-pfm-trend-table>
          <thead>
            <tr>
              <th>День периода</th>
              <th>Дата: последние ${trendDays}</th>
              <th>Значение</th>
              <th>Дата: предыдущие ${trendDays}</th>
              <th>Значение</th>
              <th>Δ</th>
              <th>Изменение, %</th>
            </tr>
          </thead>
          <tbody>${trendTableRows(row, current, previous)}</tbody>
        </table>
      </div>
      <p class="pfm-dialog-note">Пропуски показаны как «—» и не заменяются нулями. Δ сравнивает одинаковый порядковый день двух периодов.</p>`;
  }

  function previousMonthCells(row) {
    const months = Array.isArray(row?.values?.previous_months)
      ? row.values.previous_months
      : [];
    return months
      .map(
        (item) =>
          `<td class="pfm-number">${formatValue(item?.value, row.unit)}</td>`,
      )
      .join("");
  }

  function metricRow(row, treeMeta = {}) {
    const values = row.values || {};
    const status = row.status || {};
    const code = status.code || "no_data";
    const guide = row?.guide && typeof row.guide === "object" ? row.guide : {};
    const shortLabel = firstText(guide.short_label, row.name, row.id, "Метрика");
    const shortDescription = firstText(
      guide.short_description,
      guide.display_name,
      row.name,
    );
    const accessibleName =
      shortDescription && shortDescription !== shortLabel
        ? `${shortLabel}: ${shortDescription}`
        : shortLabel;
    const groupKey = `group:${treeMeta.groupId || "other"}`;
    const branchKey = treeMeta.branchId ? `branch:${treeMeta.branchId}` : "";
    const collapsed =
      collapsedSections.has(groupKey) ||
      (branchKey && collapsedSections.has(branchKey));
    return `
      <tr class="pfm-row pfm-row--${escapeHtml(code)}"
          data-section="${escapeHtml(treeMeta.filterKey || treeMeta.branchId || treeMeta.groupId || "other")}"
          data-group="${escapeHtml(treeMeta.groupId || "other")}"
          data-branch="${escapeHtml(treeMeta.branchId || "")}"
          data-status="${escapeHtml(code)}"
          data-search="${escapeHtml(`${shortLabel} ${shortDescription} ${row.name} ${row.id} ${treeMeta.groupName || ""} ${treeMeta.branchName || ""}`.toLowerCase())}"
          ${collapsed ? "hidden" : ""}>
        <th scope="row" class="pfm-metric" data-depth="${treeMeta.branchId ? "2" : "1"}">
          <button class="pfm-metric-button" type="button"
                  data-pfm-details="${escapeHtml(row.id)}"
                  aria-label="${escapeHtml(accessibleName)}">
            <span class="pfm-metric-short">${escapeHtml(shortLabel)}</span>
            <span class="pfm-metric-description">${escapeHtml(shortDescription)}</span>
          </button>
        </th>
        ${previousMonthCells(row)}
        <td class="pfm-number">${formatValue(values.plan_month, row.unit)}</td>
        <td class="pfm-number">${formatValue(values.plan_mtd, row.unit)}</td>
        <td class="pfm-number pfm-fact">${formatValue(values.fact_mtd, row.unit)}</td>
        <td class="pfm-number">${formatValue(values.plan_fact_ratio, "", {
          ratio: true,
        })}</td>
        <td class="pfm-number">${formatValue(values.run_rate, row.unit)}</td>
        <td class="pfm-number">${formatValue(values.run_rate_ratio, "", {
          ratio: true,
        })}</td>
        <td class="pfm-trend">${sparkline(row.trend_28d, code, row.id, row.name, row.unit)}</td>
        <td class="pfm-status">
          <span class="pfm-status-badge pfm-status-badge--${escapeHtml(code)}"
                title="${escapeHtml(status.explanation || "")}">
            ${escapeHtml(statusLabel(code))}
          </span>
        </td>
      </tr>`;
  }

  function metricRows(payload) {
    return Array.isArray(payload?.rows)
      ? payload.rows
      : Array.isArray(payload?.metric_rows)
        ? payload.metric_rows
        : [];
  }

  function hasActualFact(row) {
    const fact = row?.values?.fact_mtd;
    if (fact === null || fact === undefined || fact === "") return false;
    return Number.isFinite(Number(fact));
  }

  function healthBlock(payload, id) {
    return (payload?.health_score?.blocks || []).find(
      (item) => String(item?.id || "") === String(id || ""),
    ) || null;
  }

  function healthBandClass(item) {
    const code = String(item?.band?.code || "unavailable").toLowerCase();
    return ["healthy", "watch", "signal", "critical"].includes(code)
      ? code
      : "unavailable";
  }

  function healthBlockBadge(item) {
    if (!item) return "";
    const numeric = item.score === null || item.score === undefined
      ? Number.NaN
      : Number(item.score);
    const score = Number.isFinite(numeric) ? numberFormat.format(numeric) : "—";
    const evaluated = Number(item.evaluated_metric_count || 0);
    const total = Number(item.contract_metric_count || 0);
    const label = item?.band?.label || "Нет оценки";
    const title = Number.isFinite(numeric)
      ? `Health Score: ${score} из 100 · ${label} · оценено ${evaluated} из ${total} метрик`
      : `Health Score не рассчитан · ${label} · оценено ${evaluated} из ${total} метрик`;
    return `
      <span class="pfm-health-block pfm-health--${healthBandClass(item)}"
            data-pfm-health-block="${escapeHtml(item.id)}"
            title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
        <span>Health</span><strong>${score}</strong><em>${evaluated}/${total}</em>
      </span>`;
  }

  function healthTotalHtml(payload) {
    const health = payload?.health_score || {};
    const total = health.total || {};
    const numeric = total.score === null || total.score === undefined
      ? Number.NaN
      : Number(total.score);
    const score = Number.isFinite(numeric) ? numberFormat.format(numeric) : "—";
    const evaluated = Number(total.evaluated_metric_count || 0);
    const contract = Number(total.contract_metric_count || 0);
    const label = total?.band?.label || "Нет оценки";
    const reliability = health?.reliability?.status || "limited";
    const reason = health?.reliability?.reason || "";
    const scoreValue = Number.isFinite(numeric)
      ? Math.max(0, Math.min(100, numeric))
      : 0;
    const scoreAngle = (scoreValue * 3.6).toFixed(1);
    const coverage = contract > 0
      ? Math.max(0, Math.min(100, (evaluated / contract) * 100))
      : 0;
    const title = `Health Score ${score} из 100 · ${label} · оценено ${evaluated} из ${contract} метрик. ${reason}`;
    return `
      <section class="pfm-health-total pfm-health--${healthBandClass(total)}"
               data-pfm-health-total data-reliability="${escapeHtml(reliability)}"
               style="--pfm-health-angle: ${scoreAngle}deg; --pfm-health-coverage: ${coverage.toFixed(1)}%;"
               title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
        <div class="pfm-health-ring" aria-hidden="true">
          <span><strong data-pfm-health-value>${score}</strong><small>/100</small></span>
        </div>
        <div class="pfm-health-total-copy">
          <span>Общий Health Score</span>
          <small><i aria-hidden="true"></i>${escapeHtml(label)}</small>
          <div class="pfm-health-coverage">
            <em>Оценено ${evaluated} из ${contract}</em>
            <b>${Math.round(coverage)}%</b>
            <span aria-hidden="true"><i></i></span>
          </div>
        </div>
      </section>`;
  }

  function treeRows(payload, sourceRows = metricRows(payload)) {
    const byId = new Map(sourceRows.map((row) => [String(row.id), row]));
    const configured = Array.isArray(payload?.tree) ? payload.tree : [];
    if (!configured.length) {
      return sectionRows(payload, sourceRows).map((section, index) => ({
        id: `legacy_${index}`,
        name: section.name,
        rows: section.rows,
        children: [],
        health: null,
      }));
    }
    return configured.map((group) => ({
      id: String(group.id),
      name: String(group.name),
      rows: (group.metric_ids || []).map((id) => byId.get(String(id))).filter(Boolean),
      health: healthBlock(payload, group.id),
      children: (group.children || []).map((branch) => ({
        id: String(branch.id),
        name: String(branch.name),
        rows: (branch.metric_ids || []).map((id) => byId.get(String(id))).filter(Boolean),
        health: healthBlock(payload, branch.id),
      })),
    }));
  }

  function sectionRows(payload, sourceRows = metricRows(payload)) {
    const sections = [];
    sourceRows.forEach((row) => {
      const name = row.section || row.section_id || "Без раздела";
      let section = sections.find((item) => item.name === name);
      if (!section) {
        section = { name, rows: [] };
        sections.push(section);
      }
      section.rows.push({ ...row, section: name });
    });
    return sections;
  }

  function treeMetricRows(tree) {
    return tree.flatMap((group) => [
      ...group.rows,
      ...group.children.flatMap((branch) => branch.rows),
    ]);
  }

  function treeFilterOptions(tree) {
    return tree.flatMap((group) => [
      { value: group.id, label: group.name },
      ...group.children.map((branch) => ({
        value: branch.id,
        label: `↳ ${branch.name}`,
      })),
    ]);
  }

  function headerCells(payload) {
    const first = (payload?.rows || [])[0];
    const previous = first?.values?.previous_months || [];
    const previousHeaders = previous
      .map((item) => {
        const month = String(item?.month || "").slice(0, 7);
        return `<th scope="col">Факт<br><small>${escapeHtml(month)}</small></th>`;
      })
      .join("");
    const month = String(payload?.month || "").slice(0, 7);
    return `
      <th scope="col" class="pfm-metric">Метрика</th>
      ${previousHeaders}
      <th scope="col">План<br><small>${escapeHtml(month)}</small></th>
      <th scope="col">План<br><small>MTD</small></th>
      <th scope="col">Факт<br><small>MTD</small></th>
      <th scope="col">План / факт</th>
      <th scope="col">Run Rate</th>
      <th scope="col">Run Rate / план</th>
      <th scope="col">28 дней</th>
      <th scope="col" class="pfm-status">Статус</th>`;
  }

  function tableColgroup(payload) {
    const first = (payload?.rows || [])[0];
    const previousCount = Array.isArray(first?.values?.previous_months)
      ? first.values.previous_months.length
      : 3;
    return `
      <colgroup>
        <col class="pfm-col-metric">
        ${Array.from({ length: previousCount }, () => '<col class="pfm-col-history">').join("")}
        <col class="pfm-col-plan">
        <col class="pfm-col-plan-mtd">
        <col class="pfm-col-fact">
        <col class="pfm-col-ratio">
        <col class="pfm-col-run-rate">
        <col class="pfm-col-run-rate-ratio">
        <col class="pfm-col-trend">
        <col class="pfm-col-status">
      </colgroup>`;
  }

  function statusOptions(rows) {
    const present = new Set(rows.map((row) => row?.status?.code).filter(Boolean));
    return STATUS_ORDER.filter((code) => present.has(code))
      .map(
        (code) =>
          `<option value="${escapeHtml(code)}">${escapeHtml(statusLabel(code))}</option>`,
      )
      .join("");
  }

  function coverageText(payload) {
    const coverage =
      payload?.coverage || payload?.summary?.coverage || payload?.meta?.coverage;
    if (!coverage || typeof coverage !== "object") return "";
    const count = (value) => {
      if (value === null || value === undefined || value === "") return null;
      const numeric = Number(value);
      return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : null;
    };
    const fact = count(coverage.fact_metric_count);
    const partial = count(
      coverage.partial_fact_metric_count ??
        coverage.partial_metric_count ??
        coverage.dq_partial_metric_count ??
        coverage.dq_blocked_metric_count,
    );
    const unsupported = count(coverage.unsupported_metric_count);
    const parts = [];
    if (fact !== null) parts.push(`${integerFormat.format(fact)} с фактом`);
    if (partial !== null && partial > 0) {
      parts.push(`${integerFormat.format(partial)} частично`);
    }
    if (unsupported !== null && unsupported > 0) {
      parts.push(`${integerFormat.format(unsupported)} без источника`);
    }
    return parts.join(" · ");
  }

  function availabilityLabel(status) {
    const labels = {
      available: "Доступно",
      dq_partial: "Данные неполные",
      dq_blocked: "Данные неполные",
      limited: "Ограниченная доступность",
      unavailable: "Источник не подключён",
      no_current_value: "Факт пока не загружен",
    };
    return labels[status] || "Статус источника";
  }

  function firstText(...values) {
    for (const value of values) {
      if (Array.isArray(value)) {
        const text = value
          .filter((item) => item !== null && item !== undefined && item !== "")
          .map((item) => String(item).trim())
          .filter(Boolean)
          .join(", ");
        if (text) return text;
        continue;
      }
      if (value !== null && value !== undefined) {
        const text = String(value).trim();
        if (text) return text;
      }
    }
    return "";
  }

  function sourceList(value) {
    if (Array.isArray(value)) return firstText(value);
    if (value && typeof value === "object") return Object.keys(value).join(", ");
    return firstText(value);
  }

  function guideCollections(payload) {
    const guide = payload?.guide;
    return [
      payload?.metric_guides,
      payload?.metric_guide,
      payload?.metric_catalog,
      Array.isArray(guide) ? guide : null,
      guide && typeof guide === "object" ? guide.metrics : null,
      guide && typeof guide === "object" ? guide.metric_guides : null,
      guide && typeof guide === "object" ? guide.rows : null,
      guide && typeof guide === "object" ? guide : null,
      payload?.meta?.metric_guides,
    ].filter(Boolean);
  }

  function guideEntryForRow(row, payload = activePayload) {
    const identifiers = [row?.id, row?.metric_id, row?.registry_id]
      .filter(Boolean)
      .map(String);
    let external = {};
    for (const collection of guideCollections(payload)) {
      let candidate = null;
      if (Array.isArray(collection)) {
        candidate = collection.find((item) =>
          identifiers.includes(String(item?.id || item?.metric_id || item?.registry_id || "")),
        );
      } else if (collection && typeof collection === "object") {
        candidate = identifiers
          .map((identifier) => collection[identifier])
          .find((item) => item && typeof item === "object");
      }
      if (candidate && typeof candidate === "object") {
        external = { ...external, ...candidate };
      }
    }
    const inline =
      (row?.metric_guide && typeof row.metric_guide === "object"
        ? row.metric_guide
        : {}) || {};
    const guide = row?.guide && typeof row.guide === "object" ? row.guide : {};
    return { ...external, ...inline, ...guide };
  }

  function directionInterpretation(row) {
    const direction = String(row?.direction || "").toLowerCase();
    if (["higher_is_better", "increase", "up"].includes(direction)) {
      return "Рост обычно является положительным сигналом; снижение проверяйте относительно плана и сопоставимого периода.";
    }
    if (["lower_is_better", "decrease", "down"].includes(direction)) {
      return "Снижение обычно является положительным сигналом; рост требует проверки драйверов и качества данных.";
    }
    return "Сопоставляйте с планом, динамикой за 28 дней и связанными метриками воронки; одиночное отклонение не считается причиной.";
  }

  function sourceRequirement(row, guide) {
    const explicit = firstText(
      guide.source_needed,
      guide.required_source,
      guide.required_sources,
      guide.needed_source,
      guide.source_requirement,
      guide.data_source_needed,
      guide?.source?.needed,
    );
    if (explicit) return explicit;
    const source = row?.source || {};
    const objects = sourceList(
      source.objects || source.object || source.tables || source.datasets,
    );
    const fields = sourceList(
      source.fields || source.required_fields || source.field_mapping,
    );
    const parts = [];
    if (objects) parts.push(`источник: ${objects}`);
    if (fields) parts.push(`поля: ${fields}`);
    if (parts.length) return `Для расчёта нужны ${parts.join("; ")}.`;
    const reason = firstText(row?.availability?.reason, guide.availability_reason);
    if (reason) {
      return `Нужен валидированный источник данных. Текущее ограничение: ${reason}`;
    }
    return "Нужен источник, содержащий исходные данные для расчёта этой метрики.";
  }

  function normalizedGuide(row, payload = activePayload) {
    const guide = guideEntryForRow(row, payload);
    const contract = row?.contract || {};
    const source = row?.source || {};
    const status = row?.status || {};
    const availability = row?.availability || {};
    const plainName = firstText(
      guide.display_name,
      guide.plain_name,
      guide.plainName,
      guide.name_ru,
      guide.name,
      row?.plain_name,
      row?.name,
      row?.id,
      "Метрика",
    );
    const businessMeaning = firstText(
      guide.business_meaning,
      guide.meaning,
      guide.business_description,
      guide.description,
      row?.business_meaning,
      contract.business_meaning,
      contract.note,
      `Показывает текущее значение метрики «${plainName}» в управленческой воронке.`,
    );
    const formula = firstText(
      guide.calculation_logic,
      guide.formula,
      guide.calculation,
      row?.formula,
      contract.formula,
      source.formula,
      "Формула пока не описана в контракте метрики.",
    );
    const interpretation = firstText(
      guide.interpretation,
      guide.how_to_read,
      guide.reading,
      guide.business_interpretation,
      row?.interpretation,
      contract.interpretation,
      status.explanation ? `Текущий сигнал: ${status.explanation}` : "",
      directionInterpretation(row),
    );
    const availabilityStatus = firstText(availability.status, "unknown");
    const availabilityReason = firstText(
      availability.reason,
      guide.availability_reason,
      "Дополнительная причина не указана.",
    );
    return {
      plainName,
      businessMeaning,
      formula,
      interpretation,
      availabilityStatus,
      availabilityReason,
      sourceNeeded: sourceRequirement(row, guide),
    };
  }

  function availabilityClass(status) {
    const token = String(status || "unknown").toLowerCase();
    return /^[a-z0-9_-]+$/.test(token) ? token : "unknown";
  }

  function metricGuideCard(row, payload) {
    const guide = normalizedGuide(row, payload);
    const section = row.section || row.section_id || "Без раздела";
    const searchText = [
      guide.plainName,
      row.id,
      row.metric_id,
      section,
      guide.businessMeaning,
      guide.formula,
      guide.interpretation,
      guide.availabilityReason,
      guide.sourceNeeded,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return `
      <article class="pfm-guide-item" data-pfm-guide-item
               data-metric-id="${escapeHtml(row.id || row.metric_id || "")}"
               data-search="${escapeHtml(searchText)}">
        <div class="pfm-guide-card-heading">
          <div>
            <span class="pfm-guide-section">${escapeHtml(section)}</span>
            <h4>${escapeHtml(guide.plainName)}</h4>
          </div>
          <span class="pfm-guide-availability pfm-guide-availability--${escapeHtml(availabilityClass(guide.availabilityStatus))}">
            ${escapeHtml(availabilityLabel(guide.availabilityStatus))}
          </span>
        </div>
        <dl class="pfm-guide-grid">
          <div data-pfm-guide-field="meaning">
            <dt>Бизнес-смысл</dt>
            <dd>${escapeHtml(guide.businessMeaning)}</dd>
          </div>
          <div data-pfm-guide-field="formula">
            <dt>Формула</dt>
            <dd><code>${escapeHtml(guide.formula)}</code></dd>
          </div>
          <div data-pfm-guide-field="interpretation">
            <dt>Как интерпретировать</dt>
            <dd>${escapeHtml(guide.interpretation)}</dd>
          </div>
          <div data-pfm-guide-field="availability">
            <dt>Доступность сейчас</dt>
            <dd><strong>${escapeHtml(availabilityLabel(guide.availabilityStatus))}.</strong> ${escapeHtml(guide.availabilityReason)}</dd>
          </div>
          <div data-pfm-guide-field="source">
            <dt>Какой источник нужен</dt>
            <dd>${escapeHtml(guide.sourceNeeded)}</dd>
          </div>
        </dl>
      </article>`;
  }

  function guideDrawerHtml(payload, rows, hiddenCount) {
    return `
      <dialog class="pfm-guide-drawer" data-pfm-guide-drawer
              id="pfmMetricGuideDrawer" aria-labelledby="pfmMetricGuideTitle">
        <div class="pfm-guide-panel">
          <header class="pfm-guide-header">
            <div>
              <span class="pfm-eyebrow">Полный реестр</span>
              <h3 id="pfmMetricGuideTitle">Справочник метрик</h3>
              <p>${rows.length} метрик · ${hiddenCount} без факта скрыто из основной таблицы</p>
            </div>
            <button type="button" class="pfm-guide-close" data-pfm-guide-close
                    aria-label="Закрыть справочник метрик">×</button>
          </header>
          <div class="pfm-guide-tools">
            <label>
              <span>Поиск по справочнику</span>
              <input type="search" data-pfm-guide-search
                     placeholder="Название, смысл, формула или источник">
            </label>
            <p data-pfm-guide-count aria-live="polite">Показано: ${rows.length} из ${rows.length}</p>
          </div>
          <div class="pfm-guide-list" data-pfm-guide-list>
            ${rows.map((row) => metricGuideCard(row, payload)).join("")}
            <p class="pfm-guide-empty" data-pfm-guide-empty hidden>Ничего не найдено. Измените запрос.</p>
          </div>
        </div>
      </dialog>`;
  }

  function qualityText(payload) {
    const status = payload?.data_quality?.status || payload?.summary?.source_status || "unknown";
    const freshness = payload?.data_quality?.analysis_freshness_days;
    const coverage = payload?.data_quality?.coverage_14_pct;
    const parts = [
      `${marketplaceLabel(payloadMarketplace(payload))} · DQ: ${statusLabel(status)}`,
    ];
    if (freshness !== null && freshness !== undefined) {
      parts.push(`лаг ${numberFormat.format(Number(freshness))} дн.`);
    }
    if (coverage !== null && coverage !== undefined) {
      parts.push(`покрытие ${numberFormat.format(Number(coverage))}%`);
    }
    return parts.join(" · ");
  }

  function iconSvg(name) {
    const paths = {
      quality: '<path d="M12 3l7 3v5c0 4.6-2.9 8-7 10-4.1-2-7-5.4-7-10V6l7-3z"/><path d="m9 12 2 2 4-5"/>',
      fact: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
      hidden: '<path d="M3 3l18 18M10.6 10.7a2 2 0 0 0 2.7 2.7M9.9 4.2A10.8 10.8 0 0 1 12 4c5.5 0 9 5 9 5a16 16 0 0 1-2.1 2.6M6.6 6.6C4.3 8 3 10 3 10s3.5 5 9 5c1 0 2-.2 2.8-.5"/>',
      guide: '<path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H11v17H7.5A3.5 3.5 0 0 0 4 22V5.5zM20 5.5A3.5 3.5 0 0 0 16.5 2H13v17h3.5A3.5 3.5 0 0 1 20 22V5.5z"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.fact}</svg>`;
  }

  function treeBodyHtml(tree, columnCount) {
    return tree.map((group) => {
      const groupKey = `group:${group.id}`;
      const groupCollapsed = collapsedSections.has(groupKey);
      const groupCount = group.rows.length + group.children.reduce((sum, branch) => sum + branch.rows.length, 0);
      const directRows = group.rows.map((row) => metricRow(row, {
        groupId: group.id,
        groupName: group.name,
        filterKey: group.id,
      })).join("");
      const branches = group.children.map((branch) => {
        const branchKey = `branch:${branch.id}`;
        const branchCollapsed = collapsedSections.has(branchKey) || groupCollapsed;
        return `
          <tr class="pfm-branch-row" data-pfm-branch-header="${escapeHtml(branch.id)}"
              data-group="${escapeHtml(group.id)}" ${groupCollapsed ? "hidden" : ""}>
            <th colspan="${columnCount}">
              <button type="button" data-pfm-collapse="${escapeHtml(branchKey)}"
                      aria-expanded="${branchCollapsed ? "false" : "true"}">
                <span class="pfm-tree-title">
                  <span>${branchCollapsed ? "▸" : "▾"}</span>
                  ${escapeHtml(branch.name)} <small>${branch.rows.length}</small>
                </span>
                ${healthBlockBadge(branch.health)}
              </button>
            </th>
          </tr>
          ${branch.rows.map((row) => metricRow(row, {
            groupId: group.id,
            groupName: group.name,
            branchId: branch.id,
            branchName: branch.name,
            filterKey: branch.id,
          })).join("")}`;
      }).join("");
      return `
        <tbody data-pfm-tree-group="${escapeHtml(group.id)}">
          <tr class="pfm-section-row" data-pfm-group-header="${escapeHtml(group.id)}">
            <th colspan="${columnCount}">
              <button type="button" data-pfm-collapse="${escapeHtml(groupKey)}"
                      aria-expanded="${groupCollapsed ? "false" : "true"}">
                <span class="pfm-tree-title">
                  <span>${groupCollapsed ? "▸" : "▾"}</span>
                  ${escapeHtml(group.name)} <small>${groupCount}</small>
                </span>
                ${healthBlockBadge(group.health)}
              </button>
            </th>
          </tr>
          ${directRows}${branches}
        </tbody>`;
    }).join("");
  }

  function renderPayload(root, payload) {
    activePayload = payload;
    const registryRows = metricRows(payload);
    const factRows = registryRows.filter(hasActualFact);
    const hiddenCount = registryRows.length - factRows.length;
    const tree = treeRows(payload, factRows);
    const rows = treeMetricRows(tree);
    const filterOptions = treeFilterOptions(tree);
    const client = payload.client_label || payload.client || clientKey();
    const analysisDate = payload.analysis_date || payload?.meta?.analysis_date || "—";
    const platform = marketplaceLabel(payloadMarketplace(payload));
    const coverage = coverageText(payload);
    root.innerHTML = `
      <div class="pfm-heading">
        <div>
          <span class="pfm-eyebrow">Сводный управленческий отчёт</span>
          <h2>${DASHBOARD_TITLE}</h2>
          <p>
            ${escapeHtml(client)} · ${escapeHtml(platform)}
            · факт по ${escapeHtml(analysisDate)}
          </p>
        </div>
        <div class="pfm-heading-side">
          ${healthTotalHtml(payload)}
          <div class="pfm-heading-icons" aria-label="Состояние отчёта">
          <span class="pfm-icon-chip pfm-icon-chip--quality" title="${escapeHtml(qualityText(payload))}" aria-label="${escapeHtml(qualityText(payload))}">
            ${iconSvg("quality")}<b>DQ</b>
          </span>
          <span class="pfm-icon-chip" data-pfm-coverage title="${escapeHtml(coverage || `${rows.length} метрик с фактом`)}" aria-label="${escapeHtml(coverage || `${rows.length} метрик с фактом`)}">
            ${iconSvg("fact")}<b>${rows.length}</b>
          </span>
          ${hiddenCount > 0 ? `<span class="pfm-icon-chip pfm-icon-chip--hidden" data-pfm-hidden-count title="${hiddenCount} метрик без факта доступны в справочнике" aria-label="${hiddenCount} метрик без факта доступны в справочнике">${iconSvg("hidden")}<b>${hiddenCount}</b></span>` : ""}
          <button type="button" class="pfm-guide-open" data-pfm-guide-open
                  title="Справочник всех метрик" aria-label="Справочник всех ${registryRows.length} метрик"
                  aria-haspopup="dialog" aria-controls="pfmMetricGuideDrawer">
            ${iconSvg("guide")}<b>${registryRows.length}</b>
          </button>
          </div>
        </div>
      </div>
      <div class="pfm-notice">
        Сначала показан результат, затем источники заказов и выручка; ниже —
        сворачиваемые сценарии проверки органики и SEO, рекламы, визуальной
        воронки, контента, цены, запасов и качества данных.
      </div>
      <div class="pfm-controls" aria-label="Фильтры матрицы">
        <label>
          <span>Поиск</span>
          <input type="search" data-pfm-search placeholder="Название метрики">
        </label>
        <label>
          <span>Раздел</span>
          <select data-pfm-section>
            <option value="">Все разделы</option>
            ${filterOptions
              .map(
                (item) =>
                  `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`,
              )
              .join("")}
          </select>
        </label>
        <label>
          <span>Статус</span>
          <select data-pfm-status>
            <option value="">Все статусы</option>
            ${statusOptions(rows)}
          </select>
        </label>
        <label class="pfm-checkbox">
          <input type="checkbox" data-pfm-problems>
          <span>Только отклонения</span>
        </label>
        <button type="button" class="pfm-reset" data-pfm-reset>Сбросить</button>
      </div>
      <div class="pfm-table-wrap" tabindex="0"
           aria-label="Health Check по метрикам с фактическими данными; на рабочем столе таблица умещается по ширине">
        <table class="pfm-table">
          ${tableColgroup(payload)}
          <thead><tr>${headerCells(payload)}</tr></thead>
          ${treeBodyHtml(tree, Math.max(12, 9 + (rows[0]?.values?.previous_months?.length || 3)))}
        </table>
      </div>
      <p class="pfm-filter-result" aria-live="polite"></p>
      <dialog class="pfm-dialog" data-pfm-dialog>
        <button type="button" class="pfm-dialog-close" data-pfm-dialog-close
                aria-label="Закрыть">×</button>
        <div data-pfm-dialog-body></div>
      </dialog>
      <dialog class="pfm-trend-dialog" data-pfm-trend-dialog
              aria-labelledby="pfmTrendDialogTitle">
        <button type="button" class="pfm-dialog-close" data-pfm-trend-close
                aria-label="Закрыть полный график">×</button>
        <div data-pfm-trend-body></div>
      </dialog>
      ${guideDrawerHtml(payload, registryRows, hiddenCount)}`;
    bindInteractions(root);
    applyFilters(root);
    applyGuideFilter(root);
  }

  function applyFilters(root) {
    const search = String(root.querySelector("[data-pfm-search]")?.value || "")
      .trim()
      .toLowerCase();
    const section = root.querySelector("[data-pfm-section]")?.value || "";
    const status = root.querySelector("[data-pfm-status]")?.value || "";
    const problems = Boolean(root.querySelector("[data-pfm-problems]")?.checked);
    const problemCodes = new Set(["blocked", "critical", "signal", "watch"]);
    let visible = 0;
    root.querySelectorAll(".pfm-row").forEach((row) => {
      const groupCollapsed = collapsedSections.has(`group:${row.dataset.group}`);
      const branchCollapsed = row.dataset.branch
        ? collapsedSections.has(`branch:${row.dataset.branch}`)
        : false;
      const sectionMatches =
        !section ||
        row.dataset.group === section ||
        row.dataset.branch === section;
      const matches =
        (!search || row.dataset.search.includes(search)) &&
        sectionMatches &&
        (!status || row.dataset.status === status) &&
        (!problems || problemCodes.has(row.dataset.status)) &&
        !groupCollapsed &&
        !branchCollapsed;
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    root.querySelectorAll("[data-pfm-tree-group]").forEach((body) => {
      const groupId = body.dataset.pfmTreeGroup;
      const branchIds = [...body.querySelectorAll("[data-pfm-branch-header]")]
        .map((header) => header.dataset.pfmBranchHeader);
      const groupSelected = !section || section === groupId || branchIds.includes(section);
      const groupHeader = body.querySelector("[data-pfm-group-header]");
      if (groupHeader) groupHeader.hidden = !groupSelected;
      const groupCollapsed = collapsedSections.has(`group:${groupId}`);
      body.querySelectorAll("[data-pfm-branch-header]").forEach((header) => {
        const branchId = header.dataset.pfmBranchHeader;
        const branchSelected = !section || section === groupId || section === branchId;
        const anyVisible = Boolean(
          body.querySelector(`.pfm-row[data-branch="${CSS.escape(branchId)}"]:not([hidden])`),
        );
        const branchCollapsed = collapsedSections.has(`branch:${branchId}`);
        header.hidden =
          groupCollapsed ||
          !branchSelected ||
          (!anyVisible && !branchCollapsed && Boolean(search || status || problems));
      });
    });
    const result = root.querySelector(".pfm-filter-result");
    if (result) result.textContent = `Показано метрик: ${visible}`;
  }

  function applyGuideFilter(root) {
    const search = String(
      root.querySelector("[data-pfm-guide-search]")?.value || "",
    )
      .trim()
      .toLowerCase();
    const items = [...root.querySelectorAll("[data-pfm-guide-item]")];
    let visible = 0;
    items.forEach((item) => {
      const matches = !search || String(item.dataset.search || "").includes(search);
      item.hidden = !matches;
      if (matches) visible += 1;
    });
    const result = root.querySelector("[data-pfm-guide-count]");
    if (result) result.textContent = `Показано: ${visible} из ${items.length}`;
    const empty = root.querySelector("[data-pfm-guide-empty]");
    if (empty) empty.hidden = visible > 0;
  }

  function closeDialog(dialog) {
    if (typeof dialog?.close === "function") dialog.close();
    else dialog?.removeAttribute("open");
  }

  function openMetricGuide(root, trigger) {
    const dialog = root.querySelector("[data-pfm-guide-drawer]");
    if (!dialog) return;
    guideReturnFocus = trigger || document.activeElement;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    requestAnimationFrame(() => {
      root.querySelector("[data-pfm-guide-search]")?.focus();
    });
  }

  function detailHtml(row) {
    const status = row.status || {};
    const plan = row.plan || {};
    const source = row.source || {};
    const contract = row.contract || {};
    const availability = row.availability || {};
    const availabilityStatus = String(availability.status || "unknown");
    const availabilityReason = String(availability.reason || "");
    const thresholds = Array.isArray(contract.thresholds) ? contract.thresholds : [];
    return `
      <span class="pfm-eyebrow">${escapeHtml(row.section || "")}</span>
      <h3>${escapeHtml(row.name || row.id)}</h3>
      <p class="pfm-dialog-lead">${escapeHtml(status.explanation || "Без интерпретации.")}</p>
      <dl class="pfm-detail-grid">
        <div><dt>Статус</dt><dd>${escapeHtml(statusLabel(status.code))}</dd></div>
        <div data-pfm-availability
             data-availability-status="${escapeHtml(availabilityStatus)}">
          <dt>Доступность данных</dt>
          <dd>
            <strong>${escapeHtml(availabilityLabel(availabilityStatus))}</strong>
            · <code>${escapeHtml(availabilityStatus)}</code>
            ${availabilityReason ? `<br><small>${escapeHtml(availabilityReason)}</small>` : ""}
          </dd>
        </div>
        <div><dt>План</dt><dd>${escapeHtml(planLabel(plan))}${plan.provisional ? " · временный" : ""}</dd></div>
        <div><dt>Формула</dt><dd><code>${escapeHtml(contract.formula || "—")}</code></dd></div>
        <div><dt>Отсечка</dt><dd>${escapeHtml(source.cutoff || activePayload?.analysis_date || "—")}</dd></div>
        <div><dt>Поля</dt><dd>${escapeHtml((source.fields || []).join(", ") || "—")}</dd></div>
        <div><dt>Агрегация</dt><dd>${escapeHtml(row.aggregation || "—")}</dd></div>
        <div><dt>Зрелость</dt><dd>${escapeHtml(contract.maturity || "—")}</dd></div>
        <div><dt>Происхождение плана</dt><dd>${escapeHtml(plan.provenance || "—")}</dd></div>
      </dl>
      ${
        thresholds.length
          ? `<h4>Сигнальные пороги</h4>
             <ul>${thresholds
               .map(
                 (item) =>
                   `<li><b>${escapeHtml(statusLabel(item.status))}:</b> ${escapeHtml(
                     item.rationale || `${item.comparison} ${item.operator} ${item.value}`,
                   )}</li>`,
               )
               .join("")}</ul>`
          : ""
      }
      ${
        contract.note
          ? `<p class="pfm-dialog-note">${escapeHtml(contract.note)}</p>`
          : ""
      }`;
  }

  function openDetails(root, metricId) {
    const rows = activePayload?.rows || activePayload?.metric_rows || [];
    const row = rows.find((item) => String(item.id || item.metric_id) === metricId);
    if (!row) return;
    const dialog = root.querySelector("[data-pfm-dialog]");
    const body = root.querySelector("[data-pfm-dialog-body]");
    if (!dialog || !body) return;
    detailReturnFocus = document.activeElement;
    dialog.setAttribute("aria-label", row.name || metricId);
    body.innerHTML = detailHtml(row);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function openTrend(root, metricId, trigger) {
    const rows = activePayload?.rows || activePayload?.metric_rows || [];
    const row = rows.find((item) => String(item.id || item.metric_id) === metricId);
    if (!row) return;
    const dialog = root.querySelector("[data-pfm-trend-dialog]");
    const body = root.querySelector("[data-pfm-trend-body]");
    if (!dialog || !body) return;
    trendReturnFocus = trigger || document.activeElement;
    body.innerHTML = trendDialogHtml(row);
    body.querySelector("[data-pfm-hypothesis-run]")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      runHypothesisAnalysis(root, String(button.dataset.pfmHypothesisRun || row.id), button);
    });
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    requestAnimationFrame(() => {
      root.querySelector("[data-pfm-trend-close]")?.focus();
    });
  }

  async function runHypothesisAnalysis(root, metricId, button) {
    const resultHost = root.querySelector("[data-pfm-hypothesis-result]");
    if (!resultHost || !button) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    const originalContent = Array.from(button.childNodes);
    button.textContent = "Анализирую…";
    resultHost.hidden = false;
    resultHost.innerHTML = '<div class="pfm-hypothesis-loading"><span class="pfm-loader" aria-hidden="true"></span><b>Проверяю DQ, сценарий и дерево причин</b></div>';
    const params = new URLSearchParams({
      client: clientKey(),
      marketplace: marketplaceKey(),
      metric: metricId,
    });
    const month = selectedMonth();
    if (month) params.set("month", month);
    Object.entries(radarScope || {}).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    try {
      const result = await requestJson(`/api/health-check-hypothesis-analysis?${params.toString()}`);
      if (disposed || !resultHost.isConnected || !button.isConnected) return;
      if (!result || result.ok === false) throw new Error(result?.message || "Анализ недоступен");
      resultHost.innerHTML = hypothesisResultHtml(result);
      resultHost.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) {
      if (disposed || !resultHost.isConnected) return;
      resultHost.innerHTML = '<div class="pfm-hypothesis-error" role="alert"><b>Анализ не выполнен</b><span>Не удалось получить диагностику. Повторите анализ; если ошибка сохраняется, проверьте доступность источников.</span></div>';
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.replaceChildren(...originalContent);
    }
  }

  function bindInteractions(root) {
    ["[data-pfm-search]", "[data-pfm-section]", "[data-pfm-status]", "[data-pfm-problems]"].forEach(
      (selector) => {
        root.querySelector(selector)?.addEventListener("input", () => applyFilters(root));
        root.querySelector(selector)?.addEventListener("change", () => applyFilters(root));
      },
    );
    root.querySelector("[data-pfm-reset]")?.addEventListener("click", () => {
      const search = root.querySelector("[data-pfm-search]");
      const section = root.querySelector("[data-pfm-section]");
      const status = root.querySelector("[data-pfm-status]");
      const problems = root.querySelector("[data-pfm-problems]");
      if (search) search.value = "";
      if (section) section.value = "";
      if (status) status.value = "";
      if (problems) problems.checked = false;
      applyFilters(root);
    });
    root.querySelectorAll("[data-pfm-collapse]").forEach((button) => {
      button.addEventListener("click", () => {
        const filters = ["search", "section", "status", "problems"].map((key) => {
          const input = root.querySelector(`[data-pfm-${key}]`);
          return [key, key === "problems" ? input?.checked : input?.value];
        });
        const section = button.dataset.pfmCollapse;
        if (collapsedSections.has(section)) collapsedSections.delete(section);
        else collapsedSections.add(section);
        renderPayload(root, activePayload);
        filters.forEach(([key, value]) => {
          const input = root.querySelector(`[data-pfm-${key}]`);
          if (input) { if (key === "problems") input.checked = value; else input.value = value; }
        });
        applyFilters(root);
        [...root.querySelectorAll("[data-pfm-collapse]")].find((item) => item.dataset.pfmCollapse === section)?.focus();
      });
    });
    root.querySelectorAll("[data-pfm-details]").forEach((button) => {
      button.addEventListener("click", () =>
        openDetails(root, button.dataset.pfmDetails),
      );
    });
    root.querySelectorAll("[data-pfm-trend]").forEach((button) => {
      button.addEventListener("click", () =>
        openTrend(root, button.dataset.pfmTrend, button),
      );
    });
    const trendDialog = root.querySelector("[data-pfm-trend-dialog]");
    root.querySelector("[data-pfm-trend-close]")?.addEventListener("click", () => {
      closeDialog(trendDialog);
    });
    trendDialog?.addEventListener("click", (event) => {
      if (event.target === trendDialog) closeDialog(trendDialog);
    });
    trendDialog?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialog(trendDialog);
      }
    });
    trendDialog?.addEventListener("close", () => {
      if (trendReturnFocus && typeof trendReturnFocus.focus === "function") {
        trendReturnFocus.focus();
      }
      trendReturnFocus = null;
    });
    const dialog = root.querySelector("[data-pfm-dialog]");
    dialog?.addEventListener("close", () => {
      detailReturnFocus?.focus();
      detailReturnFocus = null;
    });
    root.querySelector("[data-pfm-dialog-close]")?.addEventListener("click", () => {
      closeDialog(dialog);
    });
    dialog?.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });

    const guideDialog = root.querySelector("[data-pfm-guide-drawer]");
    const guideOpen = root.querySelector("[data-pfm-guide-open]");
    guideOpen?.addEventListener("click", () => openMetricGuide(root, guideOpen));
    root
      .querySelector("[data-pfm-guide-search]")
      ?.addEventListener("input", () => applyGuideFilter(root));
    root.querySelector("[data-pfm-guide-close]")?.addEventListener("click", () => {
      closeDialog(guideDialog);
    });
    guideDialog?.addEventListener("click", (event) => {
      if (event.target === guideDialog) closeDialog(guideDialog);
    });
    guideDialog?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialog(guideDialog);
      }
    });
    guideDialog?.addEventListener("close", () => {
      if (guideReturnFocus && typeof guideReturnFocus.focus === "function") {
        guideReturnFocus.focus();
      }
      guideReturnFocus = null;
    });
  }

  function renderLoading(root) {
    root.innerHTML = `
      <div class="pfm-loading">
        <span class="pfm-loader" aria-hidden="true"></span>
        <div><b>Собираю радар</b><small>Единая отсечка, 60 метрик, 28-дневные ряды</small></div>
      </div>`;
  }

  function renderUnavailable(root, message) {
    root.innerHTML = `
      <div class="pfm-unavailable">
        <b>Health Check недоступен</b>
        <span>${escapeHtml(message)}</span>
      </div>`;
  }

  async function loadMatrix() {
    if (!isCommercialRadarScreen()) return null;
    const root = ensureRoot();
    if (!root) return null;
    const marketplace = marketplaceKey();
    radarScope = scopeFromUrl();
    if (!marketplace) {
      requestVersion += 1;
      renderUnavailable(root, "Выберите маркетплейс для построения радара.");
      return null;
    }
    const month = selectedMonth();
    const requestKey = [
      clientKey(),
      marketplace,
      month,
      radarScope.category,
      radarScope.product,
      radarScope.article,
    ].join("|");
    if (inFlightRequest && inFlightRequestKey === requestKey) {
      return inFlightRequest;
    }

    const version = ++requestVersion;
    renderLoading(root);
    const params = new URLSearchParams({
      client: clientKey(),
      marketplace,
    });
    if (month) params.set("month", month);
    Object.entries(radarScope).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });

    const request = (async () => {
      try {
        loadRadarFilterOptions(marketplace).catch(
          () => radarFilterOptions,
        );
        const payload = await getJson(`/api/planfact-funnel-matrix?${params.toString()}`);
        if (version !== requestVersion || !isCommercialRadarScreen()) return null;
        if (!payload || payload.ok === false) {
          throw new Error(payload?.message || "Некорректный ответ BI");
        }
        renderPayload(root, payload);
        return payload;
      } catch (error) {
        if (version !== requestVersion || !isCommercialRadarScreen()) return null;
        renderUnavailable(
          root,
          error?.message ||
            `Не удалось получить данные радара ${marketplaceLabel(marketplace)}.`,
        );
        return null;
      } finally {
        if (
          version === requestVersion &&
          inFlightRequestKey === requestKey
        ) {
          inFlightRequest = null;
          inFlightRequestKey = "";
        }
      }
    })();
    inFlightRequest = request;
    inFlightRequestKey = requestKey;
    return request;
  }

  function ensureNavigation() {
    let button = document.getElementById(NAV_ID);
    if (button) {
      decorateSidebarNavigation();
      return button;
    }
    const planFactButton = document.getElementById("navPlanFact");
    if (!planFactButton?.parentNode) return null;
    button = document.createElement("button");
    button.id = NAV_ID;
    button.type = "button";
    button.className = "nav-item";
    button.textContent = DASHBOARD_TITLE;
    planFactButton.parentNode.insertBefore(button, planFactButton);
    button.addEventListener("click", () => {
      state.dashboard = DASHBOARD_KEY;
      state.drillCategory = "";
      state.page = 1;
      state.sortCol = "report_date";
      state.sortDir = "asc";
      if (typeof resetColumnFilters === "function") resetColumnFilters();
      syncDashboardUrl(DASHBOARD_KEY);
      if (typeof closeMobileNav === "function") closeMobileNav();
      Promise.resolve(loadData()).catch((error) => {
        const status = document.getElementById("status");
        if (status) status.textContent = `Ошибка: ${error.message}`;
      });
    });
    decorateSidebarNavigation();
    return button;
  }

  function sidebarIconSvg(id) {
    const paths = {
      navClientReview: '<circle cx="12" cy="8" r="3"/><path d="M5 20c.7-4 3-6 7-6s6.3 2 7 6"/>',
      navReactUi: '<rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="13" width="8" height="7" rx="1.5"/><path d="M13 7h6l-2-2M11 17H5l2 2"/>',
      navCommercialRadar: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
      navPlanFact: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
      navAdv: '<path d="m4 13 12-6v10L4 13zM16 10h3a3 3 0 0 1 0 6h-3M6 14l2 6h4l-2-5"/>',
      navMediaAdv: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8" cy="10" r="2"/><path d="m4 17 5-4 3 3 3-3 5 4"/>',
      navFunnel: '<path d="M3 5h18l-7 8v5l-4 2v-7L3 5z"/>',
      navWeeklyDynamics: '<path d="M3 18 8 12l4 3 7-9M15 6h4v4"/>',
      navSeoMonitoring: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 10h6M10 7v6"/>',
      navWbSearchQueries: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 8h6M7 11h4"/>',
      navWbEntrance: '<path d="M4 5h8v14H4M12 12h9M17 8l4 4-4 4"/>',
      navAbc: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
      navProduct: '<path d="m4 8 8-4 8 4v9l-8 4-8-4V8zM4 8l8 4 8-4M12 12v9"/>',
      navSku: '<path d="M3 12 12 3h7v7l-9 9-7-7z"/><circle cx="15.5" cy="6.5" r="1"/>',
      navAdmin: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1A8 8 0 0 0 15 6.2L14.7 4h-4L10.4 6.2a8 8 0 0 0-1.5.9l-2.4-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1a8 8 0 0 0 1.5.9l.3 2.2h4l.3-2.2a8 8 0 0 0 1.5-.9l2.4 1 2-3.4-2-1.5a7 7 0 0 0 .1-1z"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[id] || '<circle cx="12" cy="12" r="8"/><path d="M9 12h6"/>'}</svg>`;
  }

  function decorateSidebarNavigation() {
    document.querySelectorAll(".sidebar .nav-item").forEach((item) => {
      if (item.dataset.compactLabel) return;
      const label = String(item.textContent || "").trim();
      if (!label) return;
      item.dataset.compactLabel = label;
      item.title = label;
      item.setAttribute("aria-label", label);
      item.replaceChildren();
      const icon = document.createElement("span");
      icon.className = "pfm-nav-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = sidebarIconSvg(item.id);
      const text = document.createElement("span");
      text.className = "pfm-nav-label";
      text.textContent = label;
      item.append(icon, text);
    });
    const uiModeLink = document.getElementById("navReactUi");
    if (uiModeLink && !uiModeLink.dataset.compactLabel) {
      const accessibleLabel = "Перейти в React-интерфейс";
      uiModeLink.dataset.compactLabel = accessibleLabel;
      uiModeLink.dataset.pfmUiSwitch = "react";
      uiModeLink.classList.add("pfm-ui-switch-link");
      uiModeLink.title = accessibleLabel;
      uiModeLink.setAttribute("aria-label", accessibleLabel);
      uiModeLink.replaceChildren();
      const icon = document.createElement("span");
      icon.className = "pfm-ui-switch-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "R";
      const text = document.createElement("span");
      text.className = "pfm-ui-switch-label";
      text.textContent = "React";
      uiModeLink.append(icon, text);
    }
  }

  function setCommercialRadarVisible(visible) {
    const host = ensureHost();
    host?.classList.toggle("hidden", !visible);
    document.body.classList.toggle("commercial-radar-active", visible);
    decorateSidebarNavigation();
    if (!visible) {
      requestVersion += 1;
      inFlightRequest = null;
      inFlightRequestKey = "";
      host?.querySelector(`[${ROOT_ATTR}]`)?.remove();
      activePayload = null;
      return;
    }
    document.getElementById("financeDashboard")?.classList.add("hidden");
    document.querySelector(".filters")?.classList.remove("hidden");
    document.querySelector(".kpis")?.classList.add("hidden");
    document.querySelector(".visuals")?.classList.add("hidden");
    document.querySelector(".table-wrap")?.classList.add("hidden");
    document.querySelector(".pagination")?.classList.add("hidden");
    document.getElementById("exportExcel")?.classList.add("hidden");
    document.getElementById("backToAbc")?.classList.add("hidden");
    if (typeof setChartExportButtonsVisible === "function") {
      setChartExportButtonsVisible(false);
    }
  }

  async function renderCommercialRadarDashboard() {
    ensureClientCapability();
    setCommercialRadarVisible(true);
    const title = document.getElementById("dashboardTitle");
    const status = document.getElementById("status");
    const platform = marketplaceLabel();
    if (title) title.textContent = DASHBOARD_TITLE;
    if (status) {
      status.textContent = `${clientLabel()} · ${platform}: загрузка радара...`;
    }
    if (typeof updateNavigation === "function") updateNavigation();
    if (typeof updateFilterVisibility === "function") updateFilterVisibility();
    const payload = await loadMatrix();
    if (!isCommercialRadarScreen()) return;
    const analysisDate = payload?.analysis_date || payload?.meta?.analysis_date;
    const payloadPlatform = marketplaceLabel(payloadMarketplace(payload));
    const registryRows = metricRows(payload);
    const factCount = registryRows.filter(hasActualFact).length;
    if (status) {
      status.textContent = payload
        ? `${clientLabel()} · ${payloadPlatform}: ${factCount} с фактом · ${registryRows.length} в справочнике · факт по ${analysisDate || "—"} · ${qualityText(payload)}`
        : `${clientLabel()} · ${platform}: радар недоступен`;
    }
    if (typeof markFiltersDirty === "function") markFiltersDirty(false);
    if (typeof persistDashboardState === "function") persistDashboardState();
  }

  // Shared report instances must return before any legacy shell registrations.
  if (options) {
    radarScope = options.scope || { category: "", product: "", article: "" };
    return {
      render(root, payload) { if (!disposed) renderPayload(root, payload); },
      destroy(root) {
        disposed = true;
        root.querySelectorAll("dialog[open]").forEach(closeDialog);
        root.replaceChildren();
        activePayload = null;
      },
    };
  }

  if (Array.isArray(dateRangeDashboards) && !dateRangeDashboards.includes(DASHBOARD_KEY)) {
    dateRangeDashboards.push(DASHBOARD_KEY);
  }
  [productContextDashboards, productNameDashboards, articleDashboards].forEach(
    (dashboards) => {
      if (Array.isArray(dashboards) && !dashboards.includes(DASHBOARD_KEY)) {
        dashboards.push(DASHBOARD_KEY);
      }
    },
  );

  function bindHostScopeFilters() {
    const apply = document.getElementById("applyFilters");
    const reset = document.getElementById("resetFilters");
    const marketplace = document.getElementById("marketplace");
    const categoryMenu = document.getElementById("categoryMenu");
    const productToggle = document.getElementById("productToggle");
    const productSearch = document.getElementById("productSearch");
    if (apply && !apply.dataset.pfmScopeBound) {
      apply.dataset.pfmScopeBound = "true";
      apply.addEventListener(
        "click",
        () => {
          if (isCommercialRadarScreen()) persistScope(scopeFromHostFilters());
        },
        true,
      );
    }
    if (reset && !reset.dataset.pfmScopeBound) {
      reset.dataset.pfmScopeBound = "true";
      reset.addEventListener(
        "click",
        () => {
          if (isCommercialRadarScreen()) persistScope({ category: "", product: "", article: "" });
        },
        true,
      );
    }
    if (marketplace && !marketplace.dataset.pfmScopeBound) {
      marketplace.dataset.pfmScopeBound = "true";
      marketplace.addEventListener(
        "change",
        () => {
          if (!isCommercialRadarScreen()) return;
          radarFilterOptionsKey = "";
          persistScope({ category: "", product: "", article: "" });
        },
        true,
      );
    }
    if (categoryMenu && !categoryMenu.dataset.pfmScopeBound) {
      categoryMenu.dataset.pfmScopeBound = "true";
      categoryMenu.addEventListener("change", (event) => {
        if (!isCommercialRadarScreen()) return;
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        if (target.dataset.all !== "true" && target.checked && target.value) {
          setSelectedValues("category", [target.value]);
        }
        if (typeof setProductFilter === "function") setProductFilter("");
      });
    }
    if (productToggle && !productToggle.dataset.pfmScopeBound) {
      productToggle.dataset.pfmScopeBound = "true";
      productToggle.addEventListener("click", () => {
        if (!isCommercialRadarScreen()) return;
        const scope = scopeFromHostFilters();
        loadHostProductOptions(productSearch?.value || "", scope.category, scope.product);
      });
    }
    if (productSearch && !productSearch.dataset.pfmScopeBound) {
      productSearch.dataset.pfmScopeBound = "true";
      productSearch.addEventListener("input", () => {
        if (!isCommercialRadarScreen()) return;
        window.clearTimeout(hostProductSearchTimer);
        hostProductSearchTimer = window.setTimeout(() => {
          const scope = scopeFromHostFilters();
          loadHostProductOptions(productSearch.value, scope.category, scope.product);
        }, 250);
      });
    }
  }

  const previousRestoreDashboardState = restoreDashboardState;
  restoreDashboardState = function restoreDashboardStateWithCommercialRadar() {
    const result = previousRestoreDashboardState();
    const params = new URLSearchParams(window.location.search || "");
    const requested = params.get("dashboard");
    const requestedMarketplace = params.get("marketplace");
    let storedDashboard = "";
    if (!requested) {
      try {
        storedDashboard = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}").dashboard || "";
      } catch {
        storedDashboard = "";
      }
    }
    const desired = requested || storedDashboard;
    if (desired === DASHBOARD_KEY) {
      state.dashboard = DASHBOARD_KEY;
      if (requestedMarketplace) {
        if (typeof setMarketplaceValue === "function") {
          setMarketplaceValue(requestedMarketplace);
        } else if (document.getElementById("marketplace")) {
          document.getElementById("marketplace").value = requestedMarketplace;
        }
      }
    }
    return result;
  };

  const previousUpdateNavigation = updateNavigation;
  updateNavigation = function updateNavigationWithCommercialRadar() {
    ensureClientCapability();
    document.body.classList.add(COMPACT_SIDEBAR_CLASS);
    const result = previousUpdateNavigation();
    const button = ensureNavigation();
    if (button) {
      button.classList.toggle("hidden", !isSupportedClient());
      button.classList.toggle("active", isCommercialRadarScreen());
    }
    return result;
  };

  const previousLoadFilters = loadFilters;
  loadFilters = async function loadFiltersWithCommercialRadar(options = {}) {
    if (!isCommercialRadarScreen()) return previousLoadFilters(options);
    ensureClientCapability();
    if (!isSupportedClient()) {
      state.dashboard = firstSupportedDashboard();
      return previousLoadFilters(options);
    }
    normalizeRadarMonthRange();
    const draftScope = state?.filtersDirty ? scopeFromHostFilters() : scopeFromUrl();
    const filters = await loadRadarFilterOptions(marketplaceKey(), draftScope.category)
      .catch(() => ({ category_names: [] }));
    if (typeof fillCategorySelect === "function") {
      fillCategorySelect(filters?.category_names || []);
    }
    await loadHostProductOptions("", draftScope.category, draftScope.product);
    syncHostScopeControls(draftScope);
    if (typeof updateDateRangeToggle === "function") updateDateRangeToggle();
    if (typeof updateFilterVisibility === "function") updateFilterVisibility();
    return undefined;
  };

  const previousLoadData = loadData;
  loadData = async function loadDataWithCommercialRadar() {
    ensureClientCapability();
    if (isCommercialRadarScreen() && isSupportedClient()) {
      normalizeRadarMonthRange();
      syncDashboardUrl(DASHBOARD_KEY);
      return renderCommercialRadarDashboard();
    }
    if (isCommercialRadarScreen()) state.dashboard = firstSupportedDashboard();
    setCommercialRadarVisible(false);
    syncDashboardUrl(state.dashboard);
    return previousLoadData();
  };

  ensureClientCapability();
  document.body.classList.add(COMPACT_SIDEBAR_CLASS);
  ensureHost();
  ensureNavigation();
  bindHostScopeFilters();
}
window.PulseHealthCheck = { create: createHealthCheck };
if (typeof state !== "undefined") createHealthCheck();
})();


// Re-render a direct Health Check URL after the base shell has finished its initial boot.
if (typeof state !== 'undefined' && state.dashboard === 'commercialRadar') {
  setTimeout(() => Promise.resolve(loadData()).catch((error) => {
    const status = document.getElementById('status');
    if (status) status.textContent = 'Ошибка: ' + error.message;
  }), 0);
}
