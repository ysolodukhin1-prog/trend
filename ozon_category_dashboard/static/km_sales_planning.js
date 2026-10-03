let kmSalesPlanningPayload = null;

function kmSalesPlanningMarketplaces() {
  const configured = typeof clientMarketplaceIds === "function"
    ? clientMarketplaceIds()
    : ["ozon", "wb"];
  const supported = configured.filter((value) => value === "ozon" || value === "wb");
  return supported.length ? supported : ["ozon"];
}

function kmSalesPlanningMarketplace() {
  const supported = kmSalesPlanningMarketplaces();
  const values = [
    qs("salesPlanningMarketplace")?.value,
    new URLSearchParams(window.location.search).get("marketplace"),
    qs("marketplace")?.value,
  ];
  return values.find((value) => supported.includes(value)) || supported[0];
}

function kmSalesPlanningMarketplaceLabel(value = kmSalesPlanningMarketplace()) {
  return value === "wb" ? "WB" : "Ozon";
}

function kmSalesPlanningSetMarketplace(value) {
  const supported = kmSalesPlanningMarketplaces();
  const marketplace = supported.includes(value) ? value : supported[0];
  const globalSelect = qs("marketplace");
  if (globalSelect) globalSelect.value = marketplace;
  const url = new URL(window.location.href);
  url.searchParams.set("marketplace", marketplace);
  window.history.replaceState({}, "", url);
  return marketplace;
}

function kmSalesPlanningClientLabel(value = currentClient()) {
  return typeof clientLabel === "function" ? clientLabel(value) : value;
}

function kmSalesPlanningMonth(value, short = false) {
  const parsed = parseIsoDate(value);
  return parsed
    ? parsed.toLocaleDateString("ru-RU", short ? { month: "short" } : { month: "long", year: "numeric" })
    : String(value || "");
}

function kmSalesPlanningPct(value) {
  return value === null || value === undefined ? "—" : `${formatNumber(value, 1)}%`;
}

function kmSalesPlanningCoeff(value) {
  return value === null || value === undefined ? "—" : formatNumber(value, 2);
}

function kmSalesPlanningValue(value, kind = "number") {
  if (value === null || value === undefined || value === "") return "—";
  if (kind === "money") return financeMoney(value);
  if (kind === "percent") return kmSalesPlanningPct(value);
  if (kind === "coefficient") return kmSalesPlanningCoeff(value);
  if (kind === "daily-rate") return formatNumber(value, 2);
  return formatNumber(value, 0);
}

function kmSalesPlanningSelect(id, label, options, selected, emptyLabel) {
  return `<label>${escapeHtml(label)}
    <select id="${id}" class="finance-input">
      <option value="">${escapeHtml(emptyLabel)}</option>
      ${(options || []).map((option) => `<option value="${escapeHtml(option)}" ${option === selected ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
    </select>
  </label>`;
}

function renderKmSalesPlanningChart(rows) {
  const maxValue = Math.max(...(rows || []).flatMap((row) => [
    Number(row.actual_revenue || 0),
    Number(row.forecast_revenue || 0),
    Number(row.recommended_revenue || 0),
  ]), 1);
  const bar = (row, key, className) => {
    const value = Number(row[key] || 0);
    const height = value ? Math.max(value / maxValue * 100, 1) : 0;
    return `<i class="${className}" style="height:${height}%" title="${escapeHtml(financeMoney(value))}"><span>${value ? escapeHtml(financeMoney(value)) : ""}</span></i>`;
  };
  return `<div class="sales-planning-chart-scroll">
    <div class="sales-planning-columns" data-sales-planning-horizontal-chart>
      ${(rows || []).map((row) => `<div class="sales-planning-month-column">
        <div class="sales-planning-column-bars">
          ${bar(row, "actual_revenue", "actual")}
          ${bar(row, "forecast_revenue", "forecast")}
          ${bar(row, "recommended_revenue", "plan")}
        </div>
        <strong>${escapeHtml(`${`${kmSalesPlanningMonth(row.month_start, true)} ${row.month_start < kmSalesPlanningPayload?.period?.anchor_month ? "(ф)" : row.month_start === kmSalesPlanningPayload?.period?.anchor_month ? "(rr)" : "(п)"}`} ${row.month_start < kmSalesPlanningPayload?.period?.anchor_month ? "(ф)" : row.month_start === kmSalesPlanningPayload?.period?.anchor_month ? "(rr)" : "(п)"}`)}</strong>
      </div>`).join("")}
    </div>
  </div>
  <div class="sales-planning-legend"><span><i class="actual"></i>Факт</span><span><i class="forecast"></i>Прогноз спроса</span><span><i class="plan"></i>Системный план</span></div>`;
}

function renderKmSalesPlanningYearMatrix(rows) {
  const metrics = [
    ["Факт, шт", "actual_units"],
    ["Факт, ₽", "actual_revenue", "money"],
    ["RR/день без OOS, шт", "oos_adjusted_daily_units", "daily-rate"],
    ["Прогноз спроса, шт", "forecast_units"],
    ["Прогноз спроса, ₽", "forecast_revenue", "money"],
    ["Системный план, шт", "recommended_units"],
    ["Системный план, ₽", "recommended_revenue", "money"],
    ["Текущий остаток / остаток на начало, шт", "projected_start_stock"],
    ["Поставки, шт", "expected_supply_qty"],
    ["Остаток на конец, шт", "projected_end_stock"],
    ["Сезонность категории", "category_seasonality_coefficient", "coefficient"],
    ["Тренд", "trend_coefficient", "coefficient"],
    ["Коэффициент продвижения", "promotion_level", "coefficient"],
  ];
  return `<div class="finance-table-wrap" data-sales-planning-year-matrix>
    <table class="finance-table sales-planning-pivot-table">
      <thead><tr><th>Статья</th>${rows.map((row) => `<th>${escapeHtml(kmSalesPlanningMonth(row.month_start, true))}</th>`).join("")}</tr></thead>
      <tbody>${metrics.map(([label, key, kind]) => `<tr>
        <th>${escapeHtml(label)}</th>
        ${rows.map((row) => {
          const value = row[key];
          const negative = key === "projected_end_stock" && Number(value) < 0;
          return `<td class="num ${negative ? "sales-planning-stock-negative" : ""}">${kind === "text" ? escapeHtml(value || "—") : kmSalesPlanningValue(value, kind)}</td>`;
        }).join("")}
      </tr>`).join("")}</tbody>
    </table>
  </div>`;
}

function renderKmSalesPlanningProductMatrix(product, months) {
  const lookup = new Map((product.monthly || []).map((row) => [row.month_start, row]));

  const nextMonth = kmSalesPlanningPayload?.period?.next_month;
  const metrics = [

    ["План, шт", (row) => row.plan_units],
    ["Факт, шт", (row) => row.actual_units],
    ["Run-Rate, шт", (row) => row.run_rate_units],
    ["RR/день без OOS, шт", (row) => row.oos_adjusted_daily_units, "daily-rate"],
    ["План/факт", (row) => row.plan_fact_pct, "percent"],

    ["Следующий месяц — утверждённый план, шт", (row) => row.month_start === nextMonth ? row.approved_plan_units : null],
    ["Текущий остаток / остаток на начало, шт", (row) => row.projected_start_stock],
    ["Поставки, шт", (row) => row.expected_supply_qty],
    ["Сезонность категории", (row) => row.category_seasonality_coefficient, "coefficient"],
    ["Тренд", (row) => row.trend_coefficient, "coefficient"],
    ["Коэффициент продвижения", (row) => row.promotion_level, "coefficient"],
    ["Прогноз месяца по модели, шт", (row) => row.forecast_units],
    ["Остаток на конец месяца, шт", (row) => row.projected_end_stock, "number", "end-stock"],
  ];
  return `<details class="sales-planning-product" data-sales-planning-product>
    <summary>
      <span><strong>${escapeHtml(product.article || product.sku)}</strong>${escapeHtml(product.product_name || "")}</span>
      <small>SKU ${escapeHtml(product.sku)} · цена ${escapeHtml(financeMoney(product.price_rub))} · прогноз следующего ${escapeHtml(formatNumber(product.next_month_demand_units, 0))} шт</small>
    </summary>
    <div class="finance-table-wrap">
      <table class="finance-table sales-planning-product-matrix">
        <thead><tr><th>Статья</th>${months.map((month) => `<th>${escapeHtml(`${kmSalesPlanningMonth(month.month_start, true)} ${month.month_start < kmSalesPlanningPayload?.period?.anchor_month ? "(ф)" : month.month_start === kmSalesPlanningPayload?.period?.anchor_month ? "(rr)" : "(п)"}`)}</th>`).join("")}</tr></thead>
        <tbody>${metrics.map(([label, getter, kind, marker]) => `<tr>
          <th>${escapeHtml(label)}</th>
          ${months.map((month) => {
            const row = lookup.get(month.month_start) || { month_start: month.month_start };
            const value = getter(row);
            const negative = marker === "end-stock" && Number(value) < 0;
            return `<td class="num ${negative ? "sales-planning-stock-negative" : ""}">${kind === "text" ? escapeHtml(value || "—") : kmSalesPlanningValue(value, kind)}</td>`;
          }).join("")}
        </tr>`).join("")}</tbody>
      </table>
    </div>
  </details>`;
}

function renderKmSalesPlanningCategories(products, months) {
  const groups = new Map();
  products.forEach((product) => {
    const category = product.category_name || "Без категории";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(product);
  });
  return [...groups.entries()].map(([category, items], index) => `<details class="sales-planning-category" ${index < 2 ? "open" : ""} data-sales-planning-category>
    <summary><span>${escapeHtml(category)}</span><small>${formatNumber(items.length, 0)} SKU</small></summary>
    <div class="sales-planning-category-products">${items.map((product) => renderKmSalesPlanningProductMatrix(product, months)).join("")}</div>
  </details>`).join("");
}

function renderKmSalesPlanningPayload(payload) {
  kmSalesPlanningPayload = payload;
  const root = qs("financeDashboard");
  const totals = payload.totals || {};
  const coefficients = payload.coefficients || {};
  const filters = payload.filters || {};
  const rows = payload.months || [];
  const products = payload.products || [];
  const marketplace = payload.marketplace === "wb" ? "wb" : "ozon";
  const marketplaceLabel = kmSalesPlanningMarketplaceLabel(marketplace);
  const reportClientLabel = kmSalesPlanningClientLabel(payload.client || currentClient());
  root.innerHTML = `
    <section class="finance-card sales-planning-controls" data-sales-planning-toolbar>
      <header class="finance-card-head sales-planning-controls-head">
        <div><h2>План продаж ${escapeHtml(reportClientLabel)}</h2><p>Годовая модель по товарам: факт, Run-Rate, прогноз спроса и последовательный расчёт запасов.</p></div>
        <span class="sales-planning-freshness">Факт по ${escapeHtml(payload.period?.available_to || "—")} · ${escapeHtml(marketplaceLabel)}</span>
      </header>
      <div class="sales-planning-filter-grid">
        <label>Площадка
          <select id="salesPlanningMarketplace" class="finance-input">
            ${kmSalesPlanningMarketplaces().map((value) => `<option value="${value}" ${marketplace === value ? "selected" : ""}>${escapeHtml(kmSalesPlanningMarketplaceLabel(value))}</option>`).join("")}
          </select>
        </label>
        ${kmSalesPlanningSelect("salesPlanningCategory", "Категория", filters.categories, filters.selected_category, "Все категории")}
        ${kmSalesPlanningSelect("salesPlanningProduct", "Товар", filters.products, filters.selected_product, "Все товары")}
        <label>ID / артикул / название<input id="salesPlanningArticle" class="finance-input" value="${escapeHtml(filters.selected_article || "")}" placeholder="Поиск по SKU или товару" /></label>
        <label>Рост к базе, %<input id="salesPlanningGrowth" class="finance-input" type="number" min="-50" max="100" step="1" value="${escapeHtml(coefficients.growth_pct)}" /></label>
        <label>Вес тренда, %<input id="salesPlanningTrend" class="finance-input" type="number" min="0" max="100" step="5" value="${escapeHtml(coefficients.trend_weight_pct)}" /></label>
        <label>Вес остатков, %<input id="salesPlanningStock" class="finance-input" type="number" min="0" max="100" step="5" value="${escapeHtml(coefficients.stock_weight_pct)}" /></label>
        <label>Доп. сезонная поправка, %<input id="salesPlanningSeasonality" class="finance-input" type="number" min="-50" max="100" step="1" value="${escapeHtml(coefficients.seasonality_pct)}" /></label>
        <label>Страховой запас, дней<input id="salesPlanningSafety" class="finance-input" type="number" min="0" max="180" step="1" value="${escapeHtml(coefficients.safety_stock_days)}" /></label>
      </div>
      <div class="sales-planning-actions">
        <button type="button" class="sales-planning-icon-button is-primary" data-sales-planning-action="apply" aria-label="Рассчитать план" title="Рассчитать план">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18V6m0 12h16M7 15l4-4 3 2 5-7M16 6h3v3" /></svg>
        </button>
        <button type="button" class="ghost sales-planning-icon-button" data-sales-planning-action="reset" aria-label="Сбросить коэффициенты" title="Сбросить коэффициенты">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4v6h6M5.5 15a7 7 0 1 0 1.5-8.5L4 10" /></svg>
        </button>
      </div>
    </section>
    <section class="finance-kpi-grid sales-planning-kpis" data-sales-planning-kpis>
      ${financeMetricCard("Факт текущего месяца", financeMoney(totals.current_actual_revenue), `по ${payload.period?.available_to || "—"}`)}
      ${financeMetricCard("Прогноз текущего месяца", financeMoney(totals.current_forecast_revenue), "Run-Rate по текущему темпу")}
      ${financeMetricCard("План следующего месяца", financeMoney(totals.next_month_plan_revenue), kmSalesPlanningMonth(payload.period?.next_month))}
      ${financeMetricCard("План до конца года", financeMoney(totals.year_end_plan_revenue), "включая следующий месяц")}
      ${financeMetricCard("Остаток + в пути", `${formatNumber(Number(totals.stock_available_qty || 0) + Number(totals.stock_inbound_qty || 0), 0)} шт`, `доступно ${formatNumber(totals.stock_available_qty, 0)} шт`)}
      ${financeMetricCard("Обеспеченность следующего месяца", kmSalesPlanningPct(totals.next_month_stock_readiness_pct), `${formatNumber(totals.selected_sku_count, 0)} SKU`, Number(totals.next_month_stock_readiness_pct || 0) < 100 ? "warning" : "good")}
    </section>
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>Траектория до конца года</h2><p>Месяцы идут слева направо; столбцы показывают факт, прогноз спроса и системный план.</p></div></header>
      ${renderKmSalesPlanningChart(rows)}
    </section>
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>План по месяцам</h2><p>Месяцы — в колонках, статьи плана — в строках.</p></div></header>
      ${renderKmSalesPlanningYearMatrix(rows)}
    </section>
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>Годовой план по товарам и запасам</h2><p>Разверните категорию, затем нужный артикул. Отрицательный остаток подсвечивается красным.</p></div></header>
      <div class="sales-planning-category-list">${renderKmSalesPlanningCategories(products, rows)}</div>
    </section>
    ${(payload.warnings || []).map((warning) => `<div class="finance-alert finance-alert-warning">${escapeHtml(warning)}</div>`).join("")}
    ${financeMethodology(payload.methodology)}
  `;
}

function kmSalesPlanningParams(reset = false, marketplaceOverride = "") {
  const params = new URLSearchParams();
  params.set("client", currentClient());
  params.set("marketplace", marketplaceOverride || kmSalesPlanningMarketplace());
  const urlParams = new URLSearchParams(window.location.search);
  ["date_from", "date_to"].forEach((key) => {
    const value = urlParams.get(key);
    if (value) params.set(key, value);
  });
  if (reset) return params;
  [
    ["salesPlanningCategory", "category"],
    ["salesPlanningProduct", "product"],
    ["salesPlanningArticle", "article"],
    ["salesPlanningGrowth", "growth_pct"],
    ["salesPlanningTrend", "trend_weight_pct"],
    ["salesPlanningStock", "stock_weight_pct"],
    ["salesPlanningSeasonality", "seasonality_pct"],
    ["salesPlanningSafety", "safety_stock_days"],
  ].forEach(([id, key]) => {
    const value = qs(id)?.value?.trim();
    if (value) params.set(key, value);
  });
  return params;
}

async function renderKmSalesPlanningDashboard(options = {}) {
  setFinanceShellVisible(true);
  qs("dashboardTitle").textContent = "План продаж";
  const requestedClient = currentClient();
  const requestedClientLabel = kmSalesPlanningClientLabel(requestedClient);
  const marketplace = kmSalesPlanningSetMarketplace(options.marketplace || kmSalesPlanningMarketplace());
  const marketplaceLabel = kmSalesPlanningMarketplaceLabel(marketplace);
  qs("status").textContent = `${requestedClientLabel} · ${marketplaceLabel}: рассчитываем план продаж...`;
  const root = qs("financeDashboard");
  if (!root.innerHTML.trim()) root.innerHTML = '<div class="km-sales-plan-loading">Расчёт прогноза и проверка остатков…</div>';
  const params = kmSalesPlanningParams(Boolean(options.reset), marketplace);
  const payload = await getJson(`/api/km-trade/sales-forecast?${params.toString()}`);
  if (state.dashboard !== "salesPlanning" || currentClient() !== requestedClient) return;
  renderKmSalesPlanningPayload(payload);
  qs("status").textContent = `${requestedClientLabel} · ${kmSalesPlanningMarketplaceLabel(payload.marketplace)}: план продаж рассчитан по данным до ${payload.period?.available_to || "—"} · обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
}

document.addEventListener("change", (event) => {
  if (event.target?.id !== "salesPlanningMarketplace") return;
  const marketplace = kmSalesPlanningSetMarketplace(event.target.value);
  renderKmSalesPlanningDashboard({ reset: true, marketplace }).catch((error) => {
    qs("status").textContent = `Ошибка расчёта плана продаж: ${error.message}`;
  });
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-sales-planning-action]");
  if (!button) return;
  button.disabled = true;
  renderKmSalesPlanningDashboard({ reset: button.dataset.salesPlanningAction === "reset" })
    .catch((error) => {
      qs("status").textContent = `Ошибка расчёта плана продаж: ${error.message}`;
    })
    .finally(() => {
      if (button.isConnected) button.disabled = false;
    });
});
