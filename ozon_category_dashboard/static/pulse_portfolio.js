(() => {
  "use strict";

  const state = { payload: null, config: { clients: [] }, horizonDays: 28, editingKey: null };
  const MAX_SUPPORT_METRICS = 8;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const finite = (value) => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);

  function formatValue(value, format = "currency") {
    const number = finite(value);
    if (number === null) return "—";
    if (format === "percent") return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(number)}%`;
    if (format === "integer") return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(number);
    if (format === "number") return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(number);
    if (format === "rating") return new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(number);
    if (format === "days") return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(number)} дн.`;
    const compact = new Intl.NumberFormat("ru-RU", {
      notation: Math.abs(number) >= 1000000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(number) >= 1000000 ? 1 : 0,
    }).format(number);
    return `${compact} ₽`;
  }

  function formatPercent(value) {
    const number = finite(value);
    return number === null ? "—" : `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(number)}%`;
  }

  function periodLabel(period) {
    if (!period) return "Plan/Fact не подключён";
    const value = new Date(`${period}T00:00:00`);
    return Number.isNaN(value.getTime()) ? period : value.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  }

  function initials(label) {
    const words = String(label || "").trim().split(/\s+/).filter(Boolean);
    return (words.slice(0, 2).map((word) => word[0]).join("") || "—").toUpperCase();
  }

  function metricDefinitions() {
    return state.payload?.metric_groups?.flatMap((group) => group.metrics || []) || [];
  }

  function metricDefinition(metricId) {
    return metricDefinitions().find((metric) => metric.id === metricId);
  }

  function clientByKey(clientKey) {
    return state.payload?.clients?.find((client) => client.key === clientKey);
  }

  function windowSeries(series) {
    const rows = (series || []).filter((point) => point?.date && (finite(point.fact) !== null || finite(point.plan) !== null));
    return rows.slice(-Math.max(1, state.horizonDays));
  }

  function metricAvailable(metric) {
    return Boolean(metric) && (finite(metric.fact) !== null || finite(metric.plan) !== null || windowSeries(metric.series).length);
  }

  function pathFor(rows, key, min, range, width, height, margin) {
    const segments = [];
    rows.forEach((point, index) => {
      const value = finite(point[key]);
      if (value === null) return;
      const x = margin.left + index / Math.max(rows.length - 1, 1) * (width - margin.left - margin.right);
      const y = margin.top + (1 - (value - min) / range) * (height - margin.top - margin.bottom);
      segments.push(`${segments.length ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`);
    });
    return segments.join(" ");
  }

  function chartGeometry(series, width, height, margin) {
    const rows = windowSeries(series);
    const values = rows.flatMap((point) => [finite(point.fact), finite(point.plan)]).filter((value) => value !== null);
    if (!rows.length || !values.length) return null;
    const min = Math.min(0, ...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    return {
      rows,
      factPath: pathFor(rows, "fact", min, range, width, height, margin),
      planPath: pathFor(rows, "plan", min, range, width, height, margin),
      min,
      max,
      range,
    };
  }

  function sparkline(metric, tone) {
    const width = 180, height = 42, margin = { left: 3, right: 3, top: 4, bottom: 4 };
    const geometry = chartGeometry(metric?.series, width, height, margin);
    if (!geometry) return `<span class="spark-empty">Нет динамики</span>`;
    return `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><line class="spark-base" x1="3" y1="38" x2="177" y2="38"/><path class="spark-plan" d="${geometry.planPath}"/><path class="spark-fact ${escapeHtml(tone)}" d="${geometry.factPath}"/></svg>`;
  }

  function mainChart(metric, tone, clientLabel, metricLabel) {
    const width = 720, height = 194, margin = { left: 12, right: 12, top: 14, bottom: 24 };
    const geometry = chartGeometry(metric?.series, width, height, margin);
    if (!geometry) return `<div class="main-chart-empty">Нет динамики за выбранный период</div>`;
    const labels = [0, Math.floor((geometry.rows.length - 1) / 2), geometry.rows.length - 1]
      .filter((index, position, rows) => rows.indexOf(index) === position)
      .map((index) => {
        const x = margin.left + index / Math.max(geometry.rows.length - 1, 1) * (width - margin.left - margin.right);
        const raw = geometry.rows[index]?.date;
        const label = raw ? new Date(`${raw}T00:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" }) : "";
        return `<text x="${x}" y="188" text-anchor="middle">${escapeHtml(label)}</text>`;
      }).join("");
    const grids = [0.25, 0.5, 0.75].map((ratio) => {
      const y = margin.top + ratio * (height - margin.top - margin.bottom);
      return `<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}"/>`;
    }).join("");
    return `<svg class="north-star-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metricLabel)} · ${escapeHtml(clientLabel)}">${grids}<path class="main-plan" d="${geometry.planPath}"/><path class="main-fact ${escapeHtml(tone)}" d="${geometry.factPath}"/>${labels}</svg>`;
  }

  function toneLabel(tone) {
    return ({ good: "В норме", warning: "Внимание", risk: "Риск", missing: "Нет данных" })[tone] || "Нет данных";
  }

  const reportShortLabels = {
    planfact: "П/Ф", salesPlanning: "План", mediaPlan: "Медиа", profitLoss: "P&L",
    unitEconomics: "Юнит", adv: "Реклама", mediaAdv: "Медиа Ads", funnel: "Воронка",
    weeklyDynamics: "Динамика", inventoryHistory: "Запасы", seoMonitoring: "SEO",
    wbSearchQueries: "WB SEO", wbAdSearchQueries: "WB Ads", wbEntrance: "Входы",
    abc: "ABC", product: "Товары", sku: "SKU", reviews: "Отзывы", commercialRadar: "Health",
  };

  function reportIcon(reportId) {
    if (["planfact", "profitLoss", "unitEconomics"].includes(reportId)) return '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>';
    if (["salesPlanning", "mediaPlan"].includes(reportId)) return '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h3M13 14h3"/>';
    if (["adv", "mediaAdv", "wbAdSearchQueries"].includes(reportId)) return '<path d="m4 13 11-5v8L4 11v2Zm11-3 4-2v8l-4-2M6 13l1 6h4l-2-5"/>';
    if (["funnel", "wbEntrance"].includes(reportId)) return '<path d="M4 5h16l-6 7v5l-4 2v-7L4 5Z"/>';
    if (reportId === "weeklyDynamics") return '<path d="m4 16 4-5 4 3 6-8M18 6h-4M18 6v4"/>';
    if (reportId === "inventoryHistory") return '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8M12 12v8"/>';
    if (["seoMonitoring", "wbSearchQueries"].includes(reportId)) return '<circle cx="10" cy="10" r="5"/><path d="m14 14 6 6M4 19h7"/>';
    if (reportId === "abc") return '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>';
    if (reportId === "product") return '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8"/>';
    if (reportId === "sku") return '<path d="M5 5v14M8 5v14M12 5v14M15 5v14M19 5v14"/>';
    if (reportId === "reviews") return '<path d="M4 5h16v11H9l-5 4V5Z"/><path d="M8 9h8M8 12h5"/>';
    if (reportId === "commercialRadar") return '<path d="M12 21s-8-4.6-8-11a4 4 0 0 1 7-2.6L12 9l1-1.6A4 4 0 0 1 20 10c0 6.4-8 11-8 11Z"/><path d="M7 13h3l1-3 2 6 1-3h3"/>';
    return '<path d="M5 4h10l4 4v12H5V4Z"/><path d="M15 4v5h5M8 13h8M8 16h6"/>';
  }

  function renderReportNavigation(payload) {
    const root = byId("reportRailLinks");
    if (!root) return;
    root.innerHTML = (payload.reports || []).map((report) => {
      const label = report.label || report.id;
      return `<a class="rail-action report-rail-link" href="${escapeHtml(report.url)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"><svg viewBox="0 0 24 24" aria-hidden="true">${reportIcon(report.id)}</svg><span class="rail-label">${escapeHtml(reportShortLabels[report.id] || label)}</span></a>`;
    }).join("");
  }

  function supportTile(client, metricId) {
    const definition = metricDefinition(metricId) || { label: metricId, format: "currency" };
    const metric = client.metrics?.[metricId];
    const tone = metric?.tone || "missing";
    const available = metricAvailable(metric);
    return `<button class="support-tile ${available ? "" : "is-missing"}" type="button" data-client="${escapeHtml(client.key)}" data-metric="${escapeHtml(metricId)}" ${available ? "" : "disabled"} title="${escapeHtml(definition.description || definition.label)}" aria-label="${escapeHtml(available ? `Открыть динамику: ${definition.label}, ${client.label}` : `Нет данных: ${definition.label}, ${client.label}`)}">
      <span class="support-tile-head"><b>${escapeHtml(definition.label)}</b><i class="status-dot ${escapeHtml(tone)}"></i></span>
      <span class="support-value-row"><strong>${escapeHtml(formatValue(metric?.fact, definition.format))}</strong><small class="tone-chip ${escapeHtml(tone)}">${escapeHtml(finite(metric?.completion) === null ? "—" : formatPercent(metric.completion))}</small></span>
      <span class="support-context">${escapeHtml(definition.plan_label || "План")}: ${escapeHtml(formatValue(metric?.plan, definition.format))}</span>
      <span class="support-spark">${sparkline(metric, tone)}</span>
    </button>`;
  }

  function clientCard(setting) {
    const client = clientByKey(setting.key);
    if (!client) return "";
    const definition = metricDefinition(setting.north_star) || metricDefinitions()[0] || { id: setting.north_star, label: setting.north_star, format: "currency" };
    const metric = client.metrics?.[setting.north_star];
    const tone = metric?.tone || "missing";
    const supports = (setting.support || []).slice(0, MAX_SUPPORT_METRICS);
    return `<article class="monitor-card" data-tone="${escapeHtml(tone)}">
      <header class="monitor-card-head">
        <div class="client-identity"><span class="client-avatar">${escapeHtml(initials(client.label))}</span><div><a class="client-link" href="${escapeHtml(client.dashboard_url)}">${escapeHtml(client.label)}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9"/></svg></a><small>${escapeHtml(periodLabel(client.period))} · ${escapeHtml(client.source)}</small></div></div>
        <div class="card-actions"><span class="card-status ${escapeHtml(tone)}"><i></i>${escapeHtml(toneLabel(tone))}</span><button class="icon-button edit-client" type="button" data-client="${escapeHtml(client.key)}" title="Настроить карточку" aria-label="Настроить карточку ${escapeHtml(client.label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4-1 11-11-3-3L5 16l-1 4ZM14 7l3 3"/></svg></button><button class="icon-button remove-client" type="button" data-client="${escapeHtml(client.key)}" title="Убрать карточку" aria-label="Убрать карточку ${escapeHtml(client.label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg></button></div>
      </header>
      <button class="north-star-panel" type="button" data-client="${escapeHtml(client.key)}" data-metric="${escapeHtml(setting.north_star)}" ${metricAvailable(metric) ? "" : "disabled"} aria-label="${escapeHtml(metricAvailable(metric) ? `Открыть North Star: ${definition.label}, ${client.label}` : `Нет данных: ${definition.label}, ${client.label}`)}">
        <div class="north-star-summary"><span class="north-star-label">North Star</span><h3>${escapeHtml(definition.label)}</h3><strong>${escapeHtml(formatValue(metric?.fact, definition.format))}</strong><p>${escapeHtml(definition.plan_label || "План")}: <b>${escapeHtml(formatValue(metric?.plan, definition.format))}</b><span class="north-star-completion ${escapeHtml(tone)}">${escapeHtml(finite(metric?.completion) === null ? "—" : formatPercent(metric.completion))}</span></p><small>${escapeHtml(definition.description || "")}</small></div>
        <div class="north-star-visual">${mainChart(metric, tone, client.label, definition.label)}<span class="chart-key"><i class="fact"></i>Факт<i class="plan"></i>План · ${state.horizonDays} дней</span></div>
      </button>
      <section class="support-grid" aria-label="Поддерживающие метрики ${escapeHtml(client.label)}">${supports.length ? supports.map((metricId) => supportTile(client, metricId)).join("") : '<div class="support-empty">Поддерживающие метрики ещё не выбраны.</div>'}</section>
      <footer class="monitor-card-footer"><span>${1 + supports.length} из 9 метрик настроено</span><a class="open-dashboard" href="${escapeHtml(client.dashboard_url)}">Открыть BI <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9"/></svg></a></footer>
    </article>`;
  }

  function renderClients(payload) {
    const root = byId("clientGrid");
    const boards = payload.marketplace_boards || [];
    const groups = payload.signal_groups || [];
    if (!boards.some((board) => board.clients?.length) || !groups.length) {
      root.innerHTML = `<div class="empty-state"><h3>Сигналы пока недоступны</h3><p>В реестре нет подключённых аккаунтов или источников метрик.</p></div>`;
      return;
    }
    const statusLabel = (signal) => {
      if (signal?.trend === "improving") return "Улучшается";
      if (signal?.trend === "worsening") return "Ухудшается";
      if (signal?.trend === "stable") return "Стабильно";
      return ({ good: "Норма", warning: "Внимание", risk: "Сигнал", neutral: "Факт", missing: "Нет данных" })[signal?.tone || "missing"] || "Факт";
    };
    const renderBoard = (board) => {
      const clients = board.clients || [];
      const clientHead = clients.map((client) => `<th scope="col" class="signal-client-head"><a href="${escapeHtml(client.dashboard_url)}"><span>${escapeHtml(client.label)}</span><small>${escapeHtml(periodLabel(client.period))}</small></a></th>`).join("");
      const rows = groups.map((group) => {
      const groupRow = `<tr class="signal-group-row"><th colspan="${clients.length + 1}">${escapeHtml(group.label)}</th></tr>`;
      const metricRows = (group.metrics || []).map((definition) => {
        const cells = clients.map((client) => {
          const signal = client.signals?.[definition.id];
          const available = finite(signal?.fact) !== null;
          const tone = signal?.tone || "missing";
          const trend = signal?.trend || "";
          const title = available
            ? `${definition.label}: ${formatValue(signal.fact, definition.format)} · ${statusLabel(signal)} · ${signal.source || "источник"}`
            : `${definition.label}: ${signal?.reason || "Нет данных"}`;
          const content = `<strong>${escapeHtml(formatValue(signal?.fact, definition.format))}</strong><small>${escapeHtml(statusLabel(signal))}</small>`;
          return `<td class="signal-data-cell is-${escapeHtml(tone)} ${trend ? `is-${escapeHtml(trend)}` : ""}" title="${escapeHtml(title)}">${available && signal?.dashboard_url ? `<a href="${escapeHtml(signal.dashboard_url)}" aria-label="${escapeHtml(title)}">${content}</a>` : `<span>${content}</span>`}</td>`;
        }).join("");
        return `<tr><th scope="row" class="signal-metric-head"><span>${escapeHtml(definition.label)}</span><small>${escapeHtml(group.label)}</small></th>${cells}</tr>`;
      }).join("");
      return `${groupRow}${metricRows}`;
      }).join("");
      return `<section class="marketplace-board" data-marketplace="${escapeHtml(board.marketplace)}"><header class="marketplace-board-head"><h3>${escapeHtml(board.label)}</h3><span>${clients.length} аккаунтов</span></header><div class="signal-table-scroll"><table class="signal-table"><thead><tr><th scope="col" class="signal-corner">Метрика</th>${clientHead}</tr></thead><tbody>${rows}</tbody></table></div></section>`;
    };
    root.innerHTML = boards.filter((board) => board.clients?.length).map(renderBoard).join("");
  }

  function metricOption(definition, type, checked, disabled = false) {
    const inputType = type === "north" ? "radio" : "checkbox";
    const name = type === "north" ? "north-star" : "support-metric";
    return `<label class="metric-option ${disabled ? "is-disabled" : ""}"><input type="${inputType}" name="${name}" value="${escapeHtml(definition.id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}><span><b>${escapeHtml(definition.label)}</b><small>${escapeHtml(definition.description || "")}</small></span></label>`;
  }

  function renderMetricChoices(northStar, supports) {
    byId("northStarOptions").innerHTML = (state.payload.metric_groups || []).map((group) => `<div class="option-group"><h4>${escapeHtml(group.label)}</h4>${(group.metrics || []).map((definition) => metricOption(definition, "north", definition.id === northStar)).join("")}</div>`).join("");
    byId("supportMetricOptions").innerHTML = (state.payload.metric_groups || []).map((group) => `<div class="option-group"><h4>${escapeHtml(group.label)}</h4>${(group.metrics || []).map((definition) => metricOption(definition, "support", supports.includes(definition.id), definition.id === northStar)).join("")}</div>`).join("");
    bindPickerEvents();
    updateSupportCount();
  }

  function bindPickerEvents() {
    byId("northStarOptions").querySelectorAll('input[type="radio"]').forEach((input) => input.addEventListener("change", () => {
      const supports = [...byId("supportMetricOptions").querySelectorAll('input[type="checkbox"]:checked')].map((item) => item.value).filter((value) => value !== input.value);
      renderMetricChoices(input.value, supports);
    }));
    byId("supportMetricOptions").querySelectorAll('input[type="checkbox"]').forEach((input) => input.addEventListener("change", updateSupportCount));
  }

  function updateSupportCount() {
    const inputs = [...byId("supportMetricOptions").querySelectorAll('input[type="checkbox"]')];
    const selected = inputs.filter((input) => input.checked);
    const limit = Number(state.payload?.max_support_metrics) || MAX_SUPPORT_METRICS;
    inputs.forEach((input) => { if (!input.checked) input.disabled = selected.length >= limit; });
    byId("supportCount").textContent = `${selected.length} / ${limit}`;
  }

  function openAddClient() {
    const used = new Set((state.config.clients || []).map((item) => item.key));
    const available = (state.payload?.clients || []).filter((client) => !used.has(client.key));
    if (!available.length) return;
    state.editingKey = null;
    byId("monitoringTitle").textContent = "Добавить клиента";
    byId("monitoringClient").disabled = false;
    byId("monitoringClient").innerHTML = available.map((client) => `<option value="${escapeHtml(client.key)}">${escapeHtml(client.label)}</option>`).join("");
    renderMetricChoices("sales", ["orders_7d", "sales_daily", "sales_progress", "ad_spend", "tacos", "ad_spend_daily", "budget_progress", "sales_forecast"]);
    byId("configStatus").textContent = "";
    byId("monitoringDialog").showModal();
  }

  function openClientEditor(clientKey) {
    const setting = (state.config.clients || []).find((item) => item.key === clientKey);
    const client = clientByKey(clientKey);
    if (!setting || !client) return;
    state.editingKey = clientKey;
    byId("monitoringTitle").textContent = `Настроить · ${client.label}`;
    byId("monitoringClient").innerHTML = `<option value="${escapeHtml(client.key)}">${escapeHtml(client.label)}</option>`;
    byId("monitoringClient").disabled = true;
    renderMetricChoices(setting.north_star, setting.support || []);
    byId("configStatus").textContent = "";
    byId("monitoringDialog").showModal();
  }

  async function persistConfig(nextConfig) {
    byId("configStatus").textContent = "Сохраняем…";
    const response = await fetch("api/portfolio-config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nextConfig) });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    state.config = result.config;
    byId("configStatus").textContent = "Сохранено";
    renderClients(state.payload);
    return result.config;
  }

  async function submitClientEditor(event) {
    event.preventDefault();
    const key = byId("monitoringClient").value;
    const northStar = byId("northStarOptions").querySelector('input[type="radio"]:checked')?.value;
    const support = [...byId("supportMetricOptions").querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value).slice(0, MAX_SUPPORT_METRICS);
    if (!key || !northStar) return;
    const clients = [...(state.config.clients || [])];
    const setting = { key, north_star: northStar, support };
    const index = clients.findIndex((item) => item.key === state.editingKey);
    if (index >= 0) clients[index] = setting; else clients.push(setting);
    try {
      await persistConfig({ clients });
      byId("monitoringDialog").close();
    } catch (error) {
      byId("configStatus").textContent = `Ошибка: ${error.message}`;
    }
  }

  async function removeClient(clientKey) {
    try {
      await persistConfig({ clients: (state.config.clients || []).filter((item) => item.key !== clientKey) });
    } catch (error) {
      byId("refreshStatus").textContent = `Ошибка сохранения: ${error.message}`;
    }
  }

  function renderHorizon(payload) {
    const select = byId("horizonDays");
    const options = payload.horizon_options || [7, 14, 28, 56];
    const preferred = Number(payload.default_horizon_days) || 28;
    state.horizonDays = options.includes(preferred) ? preferred : options[0];
    select.innerHTML = options.map((days) => `<option value="${Number(days)}" ${Number(days) === state.horizonDays ? "selected" : ""}>${Number(days)} дней</option>`).join("");
  }

  function openChart(clientKey, metricId) {
    const client = clientByKey(clientKey);
    const definition = metricDefinition(metricId);
    const metric = client?.metrics?.[metricId];
    if (!client || !definition || !metric) return;
    byId("chartClient").textContent = `${client.label} · ${periodLabel(client.period)} · окно ${state.horizonDays} дней`;
    byId("chartTitle").textContent = definition.label;
    byId("chartSummary").innerHTML = [[definition.plan_label || "План", formatValue(metric.plan, definition.format)], ["Факт", formatValue(metric.fact, definition.format)], ["Выполнение", formatPercent(metric.completion)]].map(([label, value]) => `<div class="chart-kpi"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
    const width = 800, height = 320, margin = { top: 22, right: 24, bottom: 36, left: 74 };
    const geometry = chartGeometry(metric.series, width, height, margin);
    if (!geometry) {
      byId("chartCanvas").innerHTML = `<div class="loading-state">Нет динамики для этой метрики.</div>`;
    } else {
      const grids = [0, .25, .5, .75, 1].map((ratio) => { const y = margin.top + ratio * (height - margin.top - margin.bottom); const value = geometry.max - ratio * geometry.range; return `<line class="chart-grid" x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}"/><text class="chart-axis" x="${margin.left - 8}" y="${y + 3}" text-anchor="end">${escapeHtml(formatValue(value, definition.format))}</text>`; }).join("");
      const labels = [0, Math.floor((geometry.rows.length - 1) / 2), geometry.rows.length - 1].filter((index, position, rows) => rows.indexOf(index) === position).map((index) => { const x = margin.left + index / Math.max(geometry.rows.length - 1, 1) * (width - margin.left - margin.right); const raw = geometry.rows[index]?.date; const label = raw ? new Date(`${raw}T00:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" }) : ""; return `<text class="chart-axis" x="${x}" y="${height - 10}" text-anchor="middle">${escapeHtml(label)}</text>`; }).join("");
      byId("chartCanvas").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="График ${escapeHtml(definition.label)} для ${escapeHtml(client.label)}">${grids}<path class="chart-plan" d="${geometry.planPath}"/><path class="chart-actual" d="${geometry.factPath}"/>${labels}</svg>`;
    }
    byId("chartDialog").showModal();
  }

  async function loadPortfolio(force = false) {
    const status = byId("refreshStatus");
    status.textContent = "Обновление…";
    byId("refreshPortfolio").disabled = true;
    try {
      const response = await fetch(`api/portfolio-overview${force ? "?refresh=1" : ""}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload.ok) throw new Error(payload.error || "Не удалось получить данные");
      state.payload = payload;
      state.config = payload.config || { clients: [] };
      renderReportNavigation(payload);
      renderHorizon(payload);
      renderClients(payload);
      byId("methodology").textContent = payload.methodology || "";
      byId("addClient").disabled = (state.config.clients || []).length >= (payload.clients || []).length;
      status.textContent = `Проверено ${new Date(payload.generated_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
    } catch (error) {
      byId("clientGrid").innerHTML = `<div class="error-state">Не удалось загрузить мониторинг: ${escapeHtml(error.message)}</div>`;
      status.textContent = "Ошибка обновления";
    } finally {
      byId("refreshPortfolio").disabled = false;
    }
  }

  byId("horizonDays").addEventListener("change", (event) => { state.horizonDays = Math.max(1, Number(event.target.value) || 28); if (state.payload) renderClients(state.payload); });
  byId("refreshPortfolio").addEventListener("click", () => loadPortfolio(true));
  byId("addClient").addEventListener("click", openAddClient);
  byId("monitoringForm").addEventListener("submit", submitClientEditor);
  byId("closeMonitoring").addEventListener("click", () => byId("monitoringDialog").close());
  byId("cancelMonitoring").addEventListener("click", () => byId("monitoringDialog").close());
  byId("monitoringDialog").addEventListener("click", (event) => { if (event.target === byId("monitoringDialog")) byId("monitoringDialog").close(); });
  byId("closeChart").addEventListener("click", () => byId("chartDialog").close());
  byId("chartDialog").addEventListener("click", (event) => { if (event.target === byId("chartDialog")) byId("chartDialog").close(); });
  loadPortfolio();
})();
