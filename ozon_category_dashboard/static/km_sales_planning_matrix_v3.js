/*
 * KM Trade sales-planning matrix v3.
 * Enhances the legacy screen without changing its API/data contract.
 */

let kmSalesPlanningV3FactCollapsed = false;
let kmSalesPlanningV3DraggedColumn = null;
let kmSalesPlanningV3ValueMode = "money";
const kmSalesPlanningV3CollapsedCategories = new Set();
const kmSalesPlanningV3CollapsedProducts = new Set();
let kmSalesPlanningV3CollapseSignature = "";
const kmSalesPlanningV3MetricGroupDefinitions = [
  { id: "sales", label: "Продажи и план" },
  { id: "coefficients", label: "Коэффициенты модели" },
  { id: "inventory", label: "Остатки и поставки" },
];
const kmSalesPlanningV3ColumnOrder = { fact: [], future: [] };
const kmSalesPlanningV3ProductMonthCache = new WeakMap();

try {
  localStorage.removeItem("kokoc:km-sales-planning:fact-collapsed");
  const savedValueMode = localStorage.getItem("kokoc:km-sales-planning:value-mode");
  if (savedValueMode === "money" || savedValueMode === "units") {
    kmSalesPlanningV3ValueMode = savedValueMode;
  }
  const savedOrder = JSON.parse(localStorage.getItem("kokoc:km-sales-planning:column-order") || "{}");
  if (Array.isArray(savedOrder.fact)) kmSalesPlanningV3ColumnOrder.fact = savedOrder.fact.map(String);
  if (Array.isArray(savedOrder.future)) kmSalesPlanningV3ColumnOrder.future = savedOrder.future.map(String);
} catch (_error) {
  // UI preferences are optional.
}

function kmSalesPlanningV3IsFactMonth(row) {
  const status = String(row?.status || "").toLowerCase();
  const month = String(row?.month_start || "");
  const anchor = String(kmSalesPlanningPayload?.period?.anchor_month || "");
  return Boolean(month && anchor && month < anchor)
    || Boolean(!anchor && status.includes("факт") && status !== "текущий");
}

function kmSalesPlanningV3IsCurrentMonth(row) {
  const month = String(row?.month_start || "");
  const anchor = String(kmSalesPlanningPayload?.period?.anchor_month || "");
  return Boolean(month && anchor && month === anchor);
}
function kmSalesPlanningV3OrderedMonths(months, group) {
  const source = (months || []).filter((row) => (
    group === "fact"
      ? kmSalesPlanningV3IsFactMonth(row)
      : group === "current"
        ? kmSalesPlanningV3IsCurrentMonth(row)
        : !kmSalesPlanningV3IsFactMonth(row) && !kmSalesPlanningV3IsCurrentMonth(row)
  ));
  const byMonth = new Map(source.map((row) => [String(row.month_start), row]));
  const ordered = (kmSalesPlanningV3ColumnOrder[group] || []).map((key) => byMonth.get(key)).filter(Boolean);
  source.forEach((row) => {
    if (!ordered.includes(row)) ordered.push(row);
  });
  return ordered;
}

function kmSalesPlanningV3SaveColumnOrder() {
  try {
    localStorage.setItem("kokoc:km-sales-planning:column-order", JSON.stringify(kmSalesPlanningV3ColumnOrder));
  } catch (_error) {
    // UI preferences are optional.
  }
}

function kmSalesPlanningV3SummaryValues(rows, getter, kind, aggregate = "sum") {
  if (kind === "text") return [`${rows.length} мес.`, null, null, null];
  const values = rows
    .map((row) => getter(row))
    .filter((value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)))
    .map(Number);
  if (!values.length) return [null, null, null, null];
  if (aggregate === "actual-average-price") {
    const totalRevenue = rows.reduce((total, row) => total + Number(row.actual_revenue || 0), 0);
    const totalUnits = rows.reduce((total, row) => total + Number(row.actual_units || 0), 0);
    const weightedAverage = totalUnits > 0 ? totalRevenue / totalUnits : null;
    return [weightedAverage, weightedAverage, Math.min(...values), Math.max(...values)];
  }
  const sum = values.reduce((total, value) => total + value, 0);
  const average = sum / values.length;
  const primary = aggregate === "last"
    ? values[values.length - 1]
    : (aggregate === "avg" ? average : sum);
  return [primary, average, Math.min(...values), Math.max(...values)];
}

function kmSalesPlanningV3Cell(value, kind, marker, columnKey, extraClass = "") {
  const negative = ["end-stock", "stock-before-supply"].includes(marker)
    && value !== null
    && value !== undefined
    && Number(value) < 0;
  const content = kind === "text"
    ? escapeHtml(value || "—")
    : kmSalesPlanningValue(value, kind);
  return `<td class="num ${extraClass} ${negative ? "sales-planning-stock-negative" : ""}" data-sales-planning-col="${escapeHtml(columnKey)}">${content}</td>`;
}

function kmSalesPlanningV3MonthSuffix(row) {
  const month = String(row?.month_start || "");
  const anchor = String(kmSalesPlanningPayload?.period?.anchor_month || "");
  if (month && anchor && month < anchor) return "(ф)";
  if (month && anchor && month === anchor) return "(rr)";
  return "(п)";
}

function kmSalesPlanningV3MonthHeader(row) {
  return `${kmSalesPlanningMonth(row.month_start, true)} ${kmSalesPlanningV3MonthSuffix(row)}`;
}


function kmSalesPlanningV3Head(months) {
  const factMonths = kmSalesPlanningV3OrderedMonths(months, "fact");
  const currentMonth = kmSalesPlanningV3OrderedMonths(months, "current")[0] || null;
  const futureMonths = kmSalesPlanningV3OrderedMonths(months, "future");
  const currentLabel = currentMonth
    ? `${kmSalesPlanningMonth(currentMonth.month_start, true)} · текущий месяц`
    : "Текущий месяц";
  const currentHeaders = [
    ["current-plan", "План", "Утверждённый план; если его нет — расчётный план"],
    ["current-fact", "Факт", "Заказы с начала месяца по вчера включительно"],
    ["current-run-rate", "RR", "Прогноз месяца по текущей скорости заказов"],
    ["current-plan-fact", "% план/факт", "Факт / План × 100"],
    ["current-run-rate-plan", "% RR", "Run-Rate / План × 100"],
  ];
  return `<thead>
    <tr class="sales-planning-head-groups">
      <th class="sales-planning-article-head" rowspan="2">Статья</th>
      <th class="sales-planning-fact-group" colspan="${Math.max(factMonths.length, 1)}">Факт <small>${factMonths.length} мес.</small></th>
      ${currentMonth ? `<th class="sales-planning-current-group" colspan="5">${escapeHtml(currentLabel)}</th>` : ""}
      ${futureMonths.map((row, index) => `<th class="draggable-column sales-planning-column-header ${index === 0 ? "sales-planning-future-boundary" : ""}" rowspan="2" draggable="true" data-sales-planning-col="${escapeHtml(row.month_start)}" data-sales-planning-group="future" title="Перетащите, чтобы поменять порядок столбцов"><span class="table-header-label">${escapeHtml(kmSalesPlanningV3MonthHeader(row))}</span><span class="sales-planning-drag-handle" aria-hidden="true">⋮⋮</span></th>`).join("")}
    </tr>
    <tr class="sales-planning-head-columns">
      ${factMonths.map((row) => `<th class="draggable-column sales-planning-column-header sales-planning-fact-month" draggable="true" data-sales-planning-col="${escapeHtml(row.month_start)}" data-sales-planning-group="fact" title="Перетащите, чтобы поменять порядок столбцов"><span class="table-header-label">${escapeHtml(kmSalesPlanningV3MonthHeader(row))}</span><span class="sales-planning-drag-handle" aria-hidden="true">⋮⋮</span></th>`).join("")}
      ${currentMonth ? currentHeaders.map(([key, label, title], index) => `<th class="sales-planning-current-column ${index === 0 ? "sales-planning-current-boundary" : ""}" data-sales-planning-col="${key}" title="${escapeHtml(title)}">${escapeHtml(label)}</th>`).join("") : ""}
    </tr>
  </thead>`;
}

function kmSalesPlanningV3YearHead(months) {
  return kmSalesPlanningV3Head(months);
}


function kmSalesPlanningV3NumberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function kmSalesPlanningV3EffectiveCurrentPlan(row) {
  if (!row) return null;
  const moneyMode = kmSalesPlanningV3ValueMode === "money";
  const approved = kmSalesPlanningV3NumberOrNull(
    row[moneyMode ? "approved_plan_revenue" : "approved_plan_units"],
  );
  const calculated = kmSalesPlanningV3NumberOrNull(
    row[moneyMode ? "calculated_plan_revenue" : "calculated_plan_units"],
  );
  return approved !== null && approved > 0 ? approved : calculated;
}

function kmSalesPlanningV3CurrentComparison(row) {
  if (!row) return [null, null, null, null, null];
  const moneyMode = kmSalesPlanningV3ValueMode === "money";
  const plan = kmSalesPlanningV3EffectiveCurrentPlan(row);
  const fact = kmSalesPlanningV3NumberOrNull(
    row[moneyMode ? "actual_revenue" : "actual_units"],
  );
  const runRate = kmSalesPlanningV3NumberOrNull(
    row[moneyMode ? "forecast_revenue" : "forecast_units"],
  );
  const hasPlan = plan !== null && plan > 0;
  return [
    plan,
    fact,
    runRate,
    hasPlan && fact !== null ? (fact / plan) * 100 : null,
    hasPlan && runRate !== null ? (runRate / plan) * 100 : null,
  ];
}

function kmSalesPlanningV3AggregateCurrentComparison(rows) {
  const totals = (rows || []).reduce((result, row) => {
    const [plan, fact, runRate] = kmSalesPlanningV3CurrentComparison(row);
    if (plan !== null) { result.plan += plan; result.hasPlan = true; }
    if (fact !== null) { result.fact += fact; result.hasFact = true; }
    if (runRate !== null) { result.runRate += runRate; result.hasRunRate = true; }
    return result;
  }, { plan: 0, fact: 0, runRate: 0, hasPlan: false, hasFact: false, hasRunRate: false });
  const plan = totals.hasPlan ? totals.plan : null;
  const fact = totals.hasFact ? totals.fact : null;
  const runRate = totals.hasRunRate ? totals.runRate : null;
  const hasPlan = plan !== null && plan > 0;
  return [
    plan,
    fact,
    runRate,
    hasPlan && fact !== null ? (fact / plan) * 100 : null,
    hasPlan && runRate !== null ? (runRate / plan) * 100 : null,
  ];
}

function kmSalesPlanningV3CurrentCells(values, kind, marker) {
  const keys = ["current-plan", "current-fact", "current-run-rate", "current-plan-fact", "current-run-rate-plan"];
  return keys.map((key, index) => kmSalesPlanningV3Cell(
    values?.[index] ?? null,
    index >= 3 ? "percent" : kind,
    marker,
    key,
    `sales-planning-current-column ${index === 0 ? "sales-planning-current-boundary" : ""}`,
  )).join("");
}

function kmSalesPlanningV3Cells(
  months,
  getter,
  kind,
  marker,
  aggregate,
  currentValuesGetter,
) {
  const factMonths = kmSalesPlanningV3OrderedMonths(months, "fact");
  const currentMonth = kmSalesPlanningV3OrderedMonths(months, "current")[0] || null;
  const futureMonths = kmSalesPlanningV3OrderedMonths(months, "future");
  return [
    ...factMonths.map((row) => kmSalesPlanningV3Cell(getter(row), kind, marker, row.month_start, "sales-planning-fact-month")),
    currentMonth
      ? kmSalesPlanningV3CurrentCells(
        currentValuesGetter ? currentValuesGetter(currentMonth) : [getter(currentMonth), null, null, null, null],
        kind,
        marker,
      )
      : "",
    ...futureMonths.map((row, index) => kmSalesPlanningV3Cell(getter(row), kind, marker, row.month_start, index === 0 ? "sales-planning-future-boundary" : "")),
  ].join("");
}

function kmSalesPlanningV3YearCells(months, metric) {
  const factMonths = kmSalesPlanningV3OrderedMonths(months, "fact");
  const currentMonth = kmSalesPlanningV3OrderedMonths(months, "current")[0] || null;
  const futureMonths = kmSalesPlanningV3OrderedMonths(months, "future");
  const getter = metric.getter || ((row) => row[metric.key]);
  const factGetter = metric.factGetter || getter;
  const currentGetter = metric.currentGetter || getter;
  const futureGetter = metric.futureGetter || getter;
  return [
    ...factMonths.map((row) => kmSalesPlanningV3Cell(factGetter(row), metric.kind, metric.marker, row.month_start, "sales-planning-fact-month")),
    currentMonth ? kmSalesPlanningV3Cell(currentGetter(currentMonth), metric.kind, metric.marker, currentMonth.month_start, "sales-planning-current-column sales-planning-current-boundary") : "",
    ...futureMonths.map((row, index) => kmSalesPlanningV3Cell(futureGetter(row), metric.kind, metric.marker, row.month_start, index === 0 ? "sales-planning-future-boundary" : "")),
  ].join("");
}

renderKmSalesPlanningYearMatrix = function renderKmSalesPlanningYearMatrixV3(rows) {
  const kind = kmSalesPlanningV3TrajectoryKind();
  const label = kmSalesPlanningV3ValueMode === "money" ? "Заказы, ₽" : "Заказы, шт";
  return `<div class="finance-table-wrap sales-planning-matrix-scroll" data-sales-planning-year-matrix>
    <table class="finance-table sales-planning-pivot-table sales-planning-matrix ${kmSalesPlanningV3FactCollapsed ? "is-fact-collapsed" : ""}" data-sales-planning-matrix>
      ${kmSalesPlanningV3YearHead(rows)}
      <tbody><tr class="sales-planning-orders-row">
        <th>${escapeHtml(label)}</th>
        ${kmSalesPlanningV3Cells(
          rows,
          (month) => kmSalesPlanningV3IsFactMonth(month)
            ? month[kmSalesPlanningV3ValueMode === "money" ? "actual_revenue" : "actual_units"]
            : month[kmSalesPlanningV3ValueMode === "money" ? "forecast_revenue" : "forecast_units"],
          kind,
          null,
          "sum",
          (currentMonth) => kmSalesPlanningV3CurrentComparison(currentMonth),
        )}
      </tr></tbody>
    </table>
  </div>`;
};

function kmSalesPlanningV3ProductMonth(product, monthStart) {
  let lookup = kmSalesPlanningV3ProductMonthCache.get(product);
  if (!lookup) {
    lookup = new Map((product.monthly || []).map((row) => [row.month_start, row]));
    kmSalesPlanningV3ProductMonthCache.set(product, lookup);
  }
  return lookup.get(monthStart) || { month_start: monthStart };
}

function kmSalesPlanningV3ProductMetrics() {
  const anchorMonth = kmSalesPlanningPayload?.period?.anchor_month;
  const currentComparisonValue = (row, index) => (
    row.month_start === anchorMonth ? kmSalesPlanningV3CurrentComparison(row)[index] : null
  );
  const approvedOrCalculated = (row, approvedKey) => (
    row.month_start === anchorMonth
      ? kmSalesPlanningV3EffectiveCurrentPlan(row)
      : row[approvedKey]
  );
  return [
    { id: "approved-plan-units", group: "sales", label: "Утверждённый план, шт", valueMode: "units", getter: (row) => approvedOrCalculated(row, "approved_plan_units"), timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "approved-plan-revenue", group: "sales", label: "Утверждённый план, ₽", valueMode: "money", getter: (row) => approvedOrCalculated(row, "approved_plan_revenue"), kind: "money", timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "actual-units", group: "sales", label: "Факт, шт", valueMode: "units", getter: (row) => row.actual_units, timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "actual-revenue", group: "sales", label: "Факт, ₽", valueMode: "money", getter: (row) => row.actual_revenue, kind: "money", timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "actual-average-price", group: "sales", label: "Средняя цена продажи, ₽", valueMode: "money", getter: (row) => Number(row.actual_units || 0) > 0 ? Number(row.actual_revenue || 0) / Number(row.actual_units) : null, kind: "money", timeAggregate: "actual-average-price", categoryAggregate: "actual-average-price" },
    { id: "oos-daily-rate", group: "sales", label: "RR/день без OOS, шт", getter: (row) => row.oos_adjusted_daily_units, kind: "daily-rate", timeAggregate: "avg", categoryAggregate: "sum", title: "Средняя скорость заказов только за дни, когда доступный для продажи остаток SKU был больше нуля. Между снимками используется последнее известное состояние; дни до первого снимка не участвуют." },
    { id: "run-rate", group: "sales", label: "Run-Rate, шт", valueMode: "units", getter: (row) => currentComparisonValue(row, 2), timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "run-rate-revenue", group: "sales", label: "Run-Rate, ₽", valueMode: "money", getter: (row) => currentComparisonValue(row, 2), kind: "money", timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "plan-fact", group: "sales", label: "План/факт", getter: (row) => currentComparisonValue(row, 3), kind: "percent", timeAggregate: "avg", categoryAggregate: "plan-fact" },
    { id: "plan-run-rate", group: "sales", label: "План/RR", getter: (row) => currentComparisonValue(row, 4), kind: "percent", timeAggregate: "avg", categoryAggregate: "plan-run-rate" },
    { id: "forecast-plan-units", group: "sales", label: "Прогнозный план, шт", valueMode: "units", getter: (row) => row.month_start > anchorMonth ? row.forecast_units : null, timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "forecast-plan-revenue", group: "sales", label: "Прогнозный план, ₽", valueMode: "money", getter: (row) => row.month_start > anchorMonth ? row.forecast_revenue : null, kind: "money", timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "current-stock", group: "inventory", label: "Текущий остаток на сегодня, шт", getter: (row) => row.month_start === anchorMonth ? row.projected_start_stock : null, timeAggregate: "last", categoryAggregate: "sum" },
    { id: "start-stock", group: "inventory", label: "Остаток на начало месяца, шт", getter: (row) => row.month_start >= anchorMonth ? row.projected_start_stock : null, timeAggregate: "last", categoryAggregate: "sum" },
    { id: "confirmed-supply", group: "inventory", label: "Подтверждено к поставке, шт", getter: (row) => row.expected_supply_qty, timeAggregate: "sum", categoryAggregate: "sum" },
    { id: "stock-before-supply", group: "inventory", label: "Остаток без плана поставки, шт", getter: (row) => row.projected_end_stock_without_supply, marker: "stock-before-supply", timeAggregate: "last", categoryAggregate: "sum" },
    { id: "safety-stock", group: "inventory", label: "Страховой запас, шт", getter: (row) => row.safety_stock_qty, timeAggregate: "last", categoryAggregate: "sum", title: "Минимальный остаток на конец месяца: среднедневной прогноз спроса × страховой запас в днях." },
    { id: "recommended-supply", group: "inventory", label: "Рекомендованный план поставки, шт", getter: (row) => row.recommended_supply_qty, timeAggregate: "sum", categoryAggregate: "sum", title: "max(страховой запас − остаток без плана поставки; 0). Рассчитывается отдельно по каждому SKU." },
    { id: "end-stock", group: "inventory", label: "Остаток на конец месяца, шт", getter: (row) => row.projected_end_stock, marker: "end-stock", timeAggregate: "last", categoryAggregate: "sum" },
    { id: "category-seasonality", group: "coefficients", label: "Сезонность категории", getter: (row) => row.category_seasonality_coefficient, kind: "coefficient", timeAggregate: "avg", categoryAggregate: "avg" },
    { id: "growth", group: "coefficients", label: "Рост к базе", getter: (row) => row.effective_growth_coefficient, kind: "coefficient", timeAggregate: "avg", categoryAggregate: "avg" },
    { id: "trend", group: "coefficients", label: "Тренд", getter: (row) => row.trend_coefficient, kind: "coefficient", timeAggregate: "avg", categoryAggregate: "avg" },
    { id: "promotion", group: "coefficients", label: "Коэффициент продвижения", getter: (row) => row.promotion_level, kind: "coefficient", timeAggregate: "avg", categoryAggregate: "avg", title: "Редактируемый уровень продвижения. В коэффициенте планирования используется его отношение к уровню предыдущего месяца." },
    { id: "planning-coefficient", group: "coefficients", label: "Коэффициент планирования, ×", getter: (row) => row.planning_coefficient, kind: "coefficient", timeAggregate: "avg", categoryAggregate: "planning-coefficient", title: "Итоговый множитель прогноза: рост к базе × эффективный тренд × сезонность с ручной поправкой × продвижение. Рост применяется один раз в текущем расчётном месяце и переносится дальше через базу плана. Ограничение доступным запасом не включено." },
  ].filter((metric) => !metric.valueMode || metric.valueMode === kmSalesPlanningV3ValueMode);
}

function kmSalesPlanningV3CategoryMetricValue(items, month, metric) {
  if (metric.categoryAggregate === "text") return month.status || "—";
  if (metric.categoryAggregate === "one") return 1;
  if (["plan-fact", "plan-run-rate"].includes(metric.categoryAggregate)) {
    const comparison = kmSalesPlanningV3AggregateCurrentComparison(
      items.map((product) => kmSalesPlanningV3ProductMonth(product, month.month_start)),
    );
    return comparison[metric.categoryAggregate === "plan-fact" ? 3 : 4];
  }
  if (metric.categoryAggregate === "actual-average-price") {
    const totals = items.reduce((result, product) => {
      const row = kmSalesPlanningV3ProductMonth(product, month.month_start);
      result.revenue += Number(row.actual_revenue || 0);
      result.units += Number(row.actual_units || 0);
      return result;
    }, { revenue: 0, units: 0 });
    return totals.units > 0 ? totals.revenue / totals.units : null;
  }
  if (metric.categoryAggregate === "planning-coefficient") {
    const totals = items.reduce((result, product) => {
      const row = kmSalesPlanningV3ProductMonth(product, month.month_start);
      const baseline = Number(row.planning_baseline_units || 0);
      const coefficient = Number(row.planning_coefficient);
      if (baseline > 0 && Number.isFinite(coefficient)) {
        result.forecast += baseline * coefficient;
        result.baseline += baseline;
      }
      return result;
    }, { forecast: 0, baseline: 0 });
    return totals.baseline > 0 ? totals.forecast / totals.baseline : null;
  }
  const values = items
    .map((product) => metric.getter(kmSalesPlanningV3ProductMonth(product, month.month_start)))
    .filter((value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)))
    .map(Number);
  if (!values.length) return null;
  if (metric.categoryAggregate === "avg") {
    return values.reduce((total, value) => total + value, 0) / values.length;
  }
  return values.reduce((total, value) => total + value, 0);
}

function kmSalesPlanningV3TrajectoryValue(product, month) {
  const row = kmSalesPlanningV3ProductMonth(product, month.month_start);
  if (kmSalesPlanningV3ValueMode === "money") {
    if (kmSalesPlanningV3IsFactMonth(month)) return row.actual_revenue;
    return row.forecast_revenue;
  }
  if (kmSalesPlanningV3IsFactMonth(month)) return row.actual_units;
  return row.forecast_units;
}

function kmSalesPlanningV3TrajectoryKind() {
  return kmSalesPlanningV3ValueMode === "money" ? "money" : null;
}

function kmSalesPlanningV3ValueToggle() {
  return `<div class="sales-planning-value-toggle" data-sales-planning-value-toggle role="group" aria-label="Единицы итогов">
    ${[
      ["money", "₽"],
      ["units", "шт."],
    ].map(([mode, label]) => `<button type="button" class="${kmSalesPlanningV3ValueMode === mode ? "active" : ""}" data-sales-planning-value-mode="${mode}" aria-pressed="${kmSalesPlanningV3ValueMode === mode ? "true" : "false"}">${label}</button>`).join("")}
  </div>`;
}

function kmSalesPlanningV3TreeLabel(level, title, subtitle, key, expanded) {
  return `<th class="sales-planning-tree-label sales-planning-tree-level-${level}">
    <button type="button" data-sales-planning-${level}-toggle="${escapeHtml(key)}" aria-expanded="${expanded ? "true" : "false"}">
      <span class="sales-planning-tree-caret" aria-hidden="true">${expanded ? "−" : "+"}</span>
      <span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle || "")}</small></span>
    </button>
  </th>`;
}

function kmSalesPlanningV3MetricLabelCell(
  metric,
  className = "",
  category = "",
  detailLevel = "",
  detailOwner = "",
) {
  const detailTypes = {
    "category-seasonality": "seasonality",
    trend: "trend",
    promotion: "promotion",
    "planning-coefficient": "planning",
  };
  const detailType = detailTypes[metric.id] || "";
  const classes = [className, detailType ? "sales-planning-detail-label" : ""]
    .filter(Boolean)
    .join(" ");
  const titleAttribute = metric.title
    ? ' title="' + escapeHtml(metric.title) + '"'
    : "";
  if (!detailType) {
    return '<th class="' + classes + '"' + titleAttribute + ">"
      + escapeHtml(metric.label) + "</th>";
  }
  return '<th class="' + classes + '"' + titleAttribute
    + ' data-sales-planning-detail="' + escapeHtml(detailType) + '"'
    + ' data-sales-planning-detail-category="' + escapeHtml(category || "__all__") + '"'
    + ' data-sales-planning-detail-level="' + escapeHtml(detailLevel) + '"'
    + ' data-sales-planning-detail-owner="' + escapeHtml(detailOwner) + '"'
    + ' tabindex="0" role="button" aria-haspopup="dialog"'
    + ' aria-label="' + escapeHtml(metric.label) + ': открыть детали">'
    + escapeHtml(metric.label)
    + "</th>";
}

function kmSalesPlanningV3MetricRow(
  months,
  metric,
  getter,
  level,
  ownerKey,
  trendCategory = "",
  groupId = "",
  groupIndex = 0,
  groupLength = 1,
) {
  const classes = [
    "sales-planning-tree-metric-row",
    "sales-planning-" + level + "-metric-row",
    metric.id === "planning-coefficient" ? "sales-planning-planning-coefficient-row" : "",
    groupId ? "sales-planning-metric-group-" + groupId : "",
    groupIndex % 2 === 1 ? "is-zebra" : "",
    groupIndex === 0 ? "is-group-first" : "",
    groupIndex === groupLength - 1 ? "is-group-last" : "",
  ].filter(Boolean).join(" ");
  return '<tr class="' + classes + '" data-sales-planning-owner="' + escapeHtml(ownerKey)
    + '" data-sales-planning-metric-group="' + escapeHtml(groupId) + '">'
    + kmSalesPlanningV3MetricLabelCell(
      metric,
      "sales-planning-tree-metric-label sales-planning-tree-level-" + level,
      trendCategory,
      level,
      ownerKey,
    )
    + kmSalesPlanningV3Cells(
      months,
      getter,
      metric.kind,
      metric.marker,
      metric.timeAggregate,
      null,
    )
    + "</tr>";
}

function kmSalesPlanningV3MetricGroupRows(
  months,
  metrics,
  level,
  ownerKey,
  trendCategory,
  valueGetter,
) {
  return kmSalesPlanningV3MetricGroupDefinitions.map((group) => {
    const groupMetrics = metrics.filter((metric) => metric.group === group.id);
    return groupMetrics.map((metric, index) => kmSalesPlanningV3MetricRow(
      months,
      metric,
      (month) => valueGetter(metric, month),
      level,
      ownerKey,
      trendCategory,
      group.id,
      index,
      groupMetrics.length,
    )).join("");
  }).join("");
}

renderKmSalesPlanningProductMatrix = function renderKmSalesPlanningProductRowsV3(product, months) {
  const productKey = String(product.sku || product.article || "");
  const summary = `SKU ${product.sku || "—"} · ${financeMoney(product.price_rub)}`;
  const expanded = !kmSalesPlanningV3CollapsedProducts.has(productKey);
  const supportMetricIds = new Set([
    "category-seasonality",
    "growth",
    "trend",
    "promotion",
    "planning-coefficient",
    "start-stock",
    "end-stock",
  ]);
  const supportMetrics = kmSalesPlanningV3ProductMetrics().filter(
    (metric) => supportMetricIds.has(metric.id),
  );
  const supportRows = expanded
    ? kmSalesPlanningV3MetricGroupRows(
      months,
      supportMetrics,
      "product",
      productKey,
      product.category_name || "Без категории",
      (metric, month) => (
        kmSalesPlanningV3IsFactMonth(month)
          ? null
          : metric.getter(kmSalesPlanningV3ProductMonth(product, month.month_start))
      ),
    )
    : "";
  return `<tr class="sales-planning-tree-row sales-planning-product-row" data-sales-planning-product-key="${escapeHtml(productKey)}">
      ${kmSalesPlanningV3TreeLabel("product", product.article || product.sku, `${product.product_name || ""} · ${summary}`, productKey, expanded)}
      ${kmSalesPlanningV3Cells(
        months,
        (month) => kmSalesPlanningV3TrajectoryValue(product, month),
        kmSalesPlanningV3TrajectoryKind(),
        null,
        "sum",
        (currentMonth) => kmSalesPlanningV3CurrentComparison(
          kmSalesPlanningV3ProductMonth(product, currentMonth.month_start),
        ),
      )}
    </tr>
    ${supportRows}`;
};

renderKmSalesPlanningCategories = function renderKmSalesPlanningCategoriesV3(products, months) {
  const groups = new Map();
  products.forEach((product) => {
    const category = product.category_name || "Без категории";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(product);
  });
  const body = [...groups.entries()].map(([category, items]) => {
    const expanded = !kmSalesPlanningV3CollapsedCategories.has(category);
    const productRows = expanded
      ? items.map((product) => renderKmSalesPlanningProductMatrix(product, months)).join("")
      : "";
    return `<tr class="sales-planning-tree-row sales-planning-category-row" data-sales-planning-category-key="${escapeHtml(category)}">
        ${kmSalesPlanningV3TreeLabel("category", category, `${items.length} SKU · факт и план до конца года · ${kmSalesPlanningV3ValueMode === "money" ? "рубли" : "штуки"}`, category, expanded)}
        ${kmSalesPlanningV3Cells(
          months,
          (month) => items.reduce((total, product) => {
            const value = kmSalesPlanningV3TrajectoryValue(product, month);
            return total + (Number.isFinite(Number(value)) ? Number(value) : 0);
          }, 0),
          kmSalesPlanningV3TrajectoryKind(),
          null,
          "sum",
          (currentMonth) => kmSalesPlanningV3AggregateCurrentComparison(
            items.map((product) => kmSalesPlanningV3ProductMonth(product, currentMonth.month_start)),
          ),
        )}
      </tr>
      ${productRows}`;
  }).join("");
  return `<div class="finance-table-wrap sales-planning-matrix-scroll sales-planning-tree-scroll">
    <table class="finance-table sales-planning-product-matrix sales-planning-tree-matrix sales-planning-matrix ${kmSalesPlanningV3FactCollapsed ? "is-fact-collapsed" : ""}" data-sales-planning-matrix data-sales-planning-tree-matrix>
      ${kmSalesPlanningV3Head(months)}
      <tbody>${body}</tbody>
    </table>
  </div>`;
};

function kmSalesPlanningV3TrendDetail(category) {
  if (category === "__all__") return kmSalesPlanningPayload?.trend_detail || null;
  return (kmSalesPlanningPayload?.category_trends || []).find((row) => row.category_name === category) || null;
}

function kmSalesPlanningV3TrendSegments(dailyPoints) {
  const rows = Array.isArray(dailyPoints) ? dailyPoints : [];
  const samples = rows.map((row, index) => ({
    index,
    decade: Math.min(3, Math.max(1, Number(row.decade) || Math.floor(index / 10) + 1)),
    value: Number(row.sales_units || 0),
  }));
  return [1, 2, 3].map((decade) => {
    const decadeSamples = samples.filter((sample) => sample.decade === decade);
    if (decadeSamples.length < 2) return null;
    const meanX = (decadeSamples.length - 1) / 2;
    const meanY = decadeSamples.reduce((sum, sample) => sum + sample.value, 0) / decadeSamples.length;
    const denominator = decadeSamples.reduce((sum, _, index) => sum + ((index - meanX) ** 2), 0);
    const slope = denominator
      ? decadeSamples.reduce((sum, sample, index) => sum + ((index - meanX) * (sample.value - meanY)), 0) / denominator
      : 0;
    const intercept = meanY - slope * meanX;
    const totalSquares = decadeSamples.reduce((sum, sample) => sum + ((sample.value - meanY) ** 2), 0);
    const residualSquares = decadeSamples.reduce((sum, sample, index) => {
      const predicted = intercept + slope * index;
      return sum + ((sample.value - predicted) ** 2);
    }, 0);
    const rSquared = totalSquares > 0
      ? Math.max(0, Math.min(1, 1 - residualSquares / totalSquares))
      : null;
    return {
      decade,
      slope,
      rSquared,
      startIndex: decadeSamples[0].index,
      endIndex: decadeSamples[decadeSamples.length - 1].index,
      startValue: Math.max(0, intercept),
      endValue: Math.max(0, intercept + slope * (decadeSamples.length - 1)),
    };
  }).filter(Boolean);
}

function kmSalesPlanningV3TrendChart(dailyPoints) {
  const rows = Array.isArray(dailyPoints) ? dailyPoints : [];
  if (!rows.length) return '<div class="sales-planning-trend-empty">Нет данных для диаграммы.</div>';
  const width = 900;
  const height = 310;
  const left = 58;
  const right = 22;
  const top = 52;
  const bottom = 52;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const trendSegments = kmSalesPlanningV3TrendSegments(rows);
  const maxValue = Math.max(
    ...rows.map((row) => Number(row.sales_units || 0)),
    ...trendSegments.flatMap((segment) => [segment.startValue, segment.endValue]),
    1,
  );
  const step = rows.length > 1 ? plotWidth / (rows.length - 1) : 0;
  const yFor = (value) => top + plotHeight * (1 - Number(value || 0) / maxValue);
  const points = rows.map((row, index) => {
    const x = left + step * index;
    const y = yFor(row.sales_units);
    return { row, x, y };
  });
  const labelEvery = Math.max(Math.ceil(rows.length / 8), 1);
  const decadeWidth = plotWidth / 3;
  const trendDescription = trendSegments.map((segment) => {
    const direction = segment.slope > 0.05 ? "рост" : (segment.slope < -0.05 ? "снижение" : "стабильно");
    const rSquared = segment.rSquared === null ? "R² не определён" : `R² ${formatNumber(segment.rSquared, 2)}`;
    return `${segment.decade}-я декада: ${direction} ${formatNumber(segment.slope, 1)} шт/день, ${rSquared}`;
  }).join(". ");
  return `<svg class="sales-planning-trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="salesPlanningTrendChartTitle salesPlanningTrendChartDesc">
    <title id="salesPlanningTrendChartTitle">Ежедневные продажи за три декады: факт KM Trade и отдельный линейный тренд каждой декады</title>
    <desc id="salesPlanningTrendChartDesc">${escapeHtml(trendDescription)}</desc>
    <text x="${left}" y="13" class="trend-axis-title">Продажи, шт</text>
    <g class="trend-chart-legend" aria-hidden="true">
      <line x1="154" y1="10" x2="184" y2="10" class="trend-legend-fact"></line>
      <text x="192" y="14" class="trend-legend-label">Факт</text>
      <line x1="242" y1="10" x2="274" y2="10" class="trend-regression-line"></line>
      <text x="282" y="14" class="trend-legend-label">Линейный тренд декады</text>
    </g>
    ${[0, 1, 2].map((index) => `<rect x="${left + decadeWidth * index}" y="${top}" width="${decadeWidth}" height="${plotHeight}" class="trend-decade-band ${index % 2 ? "alternate" : ""}"></rect>`).join("")}
    ${[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const y = top + plotHeight * (1 - ratio);
      return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="trend-grid"></line><text x="${left - 8}" y="${y + 4}" text-anchor="end" class="trend-grid-label">${escapeHtml(formatNumber(maxValue * ratio, 0))}</text>`;
    }).join("")}
    ${[1, 2].map((index) => `<line x1="${left + decadeWidth * index}" y1="${top}" x2="${left + decadeWidth * index}" y2="${height - bottom}" class="trend-decade-divider"></line>`).join("")}
    ${[1, 2, 3].map((decade, index) => `<text x="${left + decadeWidth * (index + 0.5)}" y="31" text-anchor="middle" class="trend-decade-label">${decade}-я декада</text>`).join("")}
    ${trendSegments.map((segment) => {
      const signedSlope = `${segment.slope > 0 ? "+" : ""}${formatNumber(segment.slope, 1)} шт/день`;
      return `<text x="${left + decadeWidth * (segment.decade - 0.5)}" y="46" text-anchor="middle" class="trend-slope-label">${escapeHtml(signedSlope)}</text>`;
    }).join("")}
    <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="trend-axis"></line>
    <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="trend-axis"></line>
    ${trendSegments.map((segment) => {
      const direction = segment.slope > 0.05 ? "рост" : (segment.slope < -0.05 ? "снижение" : "стабильно");
      const rSquared = segment.rSquared === null ? "R² не определён" : `R² ${formatNumber(segment.rSquared, 2)}`;
      return `<line x1="${left + step * segment.startIndex}" y1="${yFor(segment.startValue)}" x2="${left + step * segment.endIndex}" y2="${yFor(segment.endValue)}" class="trend-regression-line" data-sales-planning-decade-trend="${segment.decade}"><title>${segment.decade}-я декада: ${direction}, ${escapeHtml(formatNumber(segment.slope, 1))} шт/день · ${escapeHtml(rSquared)}</title></line>`;
    }).join("")}
    <polyline points="${points.map((point) => `${point.x},${point.y}`).join(" ")}" class="trend-line"></polyline>
    ${points.map(({ row, x, y }, index) => `<g>
      <circle cx="${x}" cy="${y}" r="4" class="trend-point"><title>${escapeHtml(`${row.date_label || row.report_date}: ${formatNumber(row.sales_units, 0)} шт`)}</title></circle>
      ${(index % labelEvery === 0 || index === rows.length - 1) ? `<text x="${x}" y="${height - 20}" text-anchor="middle" class="trend-label">${escapeHtml(row.date_label || "")}</text>` : ""}
    </g>`).join("")}
  </svg>`;
}

function kmSalesPlanningV3CloseButton() {
  return `<button type="button" data-sales-planning-detail-close aria-label="Закрыть окно" title="Закрыть окно">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>
  </button>`;
}

function kmSalesPlanningV3OpenTrend(category) {
  const detail = kmSalesPlanningV3TrendDetail(category);
  if (!detail) return;
  const root = qs("financeDashboard");
  root.querySelector("[data-sales-planning-detail-modal]")?.remove();
  const dailyPoints = Array.isArray(detail.daily_points) ? detail.daily_points : [];
  root.insertAdjacentHTML("beforeend", `<div class="sales-planning-trend-modal" data-sales-planning-detail-modal role="dialog" aria-modal="true" aria-labelledby="salesPlanningTrendTitle">
    <div class="sales-planning-trend-backdrop" data-sales-planning-detail-close></div>
    <section class="sales-planning-trend-dialog">
      <header>
        <div>
          <h2 id="salesPlanningTrendTitle">Тренд · ${escapeHtml(detail.category_name || "Все выбранные категории")}</h2>
          <p>Продажи показаны по каждому дню трёх последовательных декад. Пунктир — линейный тренд внутри декады и в расчёте коэффициента не участвует. Коэффициент по суммам декад: ${escapeHtml(kmSalesPlanningCoeff(detail.trend_coefficient))}.</p>
        </div>
        ${kmSalesPlanningV3CloseButton()}
      </header>
      <div class="sales-planning-trend-body">
        ${kmSalesPlanningV3TrendChart(dailyPoints)}
        <div class="finance-table-wrap">
          <table class="finance-table sales-planning-trend-table">
            <thead><tr><th>Дата</th><th>Декада</th><th>Продажи, шт</th><th>Выручка, ₽</th><th>Δ к предыдущему дню, %</th></tr></thead>
            <tbody>${dailyPoints.map((row) => `<tr>
              <td>${escapeHtml(row.report_date || "—")}</td>
              <td>${escapeHtml(String(row.decade || "—"))}</td>
              <td class="num">${escapeHtml(formatNumber(row.sales_units, 0))}</td>
              <td class="num">${escapeHtml(financeMoney(row.sales_revenue))}</td>
              <td class="num">${row.change_pct === null || row.change_pct === undefined ? "—" : escapeHtml(`${formatNumber(row.change_pct, 1)}%`)}</td>
            </tr>`).join("")}</tbody>
          </table>
        </div>
        <p class="sales-planning-trend-source">Источник: ${escapeHtml(detail.source || "Фактические продажи KM Trade")} · 30 календарных дней ${escapeHtml(detail.period_start || "—")} — ${escapeHtml(detail.period_end || "—")}.</p>
      </div>
    </section>
  </div>`);
  root.querySelector("button[data-sales-planning-detail-close]")?.focus?.();
}

function kmSalesPlanningV3CloseDetail() {
  qs("financeDashboard")?.querySelector("[data-sales-planning-detail-modal]")?.remove();
}

function kmSalesPlanningV3SeasonalityDetail(category) {
  if (category === "__all__") return kmSalesPlanningPayload?.seasonality_detail || null;
  return (kmSalesPlanningPayload?.category_seasonality_details || [])
    .find((row) => row.category_name === category) || null;
}

function kmSalesPlanningV3SeasonalityChart(points) {
  const rows = Array.isArray(points) ? points : [];
  if (!rows.length) return '<div class="sales-planning-trend-empty">Нет помесячного профиля MPStats для выбранной категории.</div>';
  const width = 840;
  const height = 290;
  const left = 58;
  const right = 22;
  const top = 32;
  const bottom = 52;
  const values = rows.map((row) => Number(row.seasonality_index || 1));
  const minimum = Math.max(0, Math.min(...values, 1) - 0.08);
  const maximum = Math.max(...values, 1) + 0.08;
  const range = Math.max(maximum - minimum, 0.2);
  const xStep = rows.length > 1 ? (width - left - right) / (rows.length - 1) : 0;
  const yFor = (value) => top + (height - top - bottom) * (1 - (value - minimum) / range);
  const chartPoints = rows.map((row, index) => ({
    row,
    x: left + xStep * index,
    y: yFor(Number(row.seasonality_index || 1)),
  }));
  return `<svg class="sales-planning-seasonality-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Помесячный индекс сезонности категории">
    <title>Помесячный профиль сезонности: индекс календарного месяца к среднемесячному уровню ниши</title>
    <text x="${left}" y="14" class="seasonality-axis-title">Индекс сезонности</text>
    <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="trend-axis"></line>
    <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="trend-axis"></line>
    <line x1="${left}" y1="${yFor(1)}" x2="${width - right}" y2="${yFor(1)}" class="seasonality-baseline"></line>
    <polyline points="${chartPoints.map((point) => `${point.x},${point.y}`).join(" ")}" class="seasonality-line"></polyline>
    ${chartPoints.map(({ row, x, y }) => `<g>
      <circle cx="${x}" cy="${y}" r="4.5" class="seasonality-point"><title>${escapeHtml(`${row.month_label}: ${kmSalesPlanningCoeff(row.seasonality_index)}`)}</title></circle>
      <text x="${x}" y="${Math.max(y - 12, 28)}" text-anchor="middle" class="seasonality-value">${escapeHtml(kmSalesPlanningCoeff(row.seasonality_index))}</text>
      <text x="${x}" y="${height - 22}" text-anchor="middle" class="trend-label">${escapeHtml(row.month_short_label || row.month_label || "")}</text>
    </g>`).join("")}
    <text x="${left + 6}" y="${Math.max(yFor(1) - 7, 26)}" class="seasonality-baseline-label">1,00 · среднее</text>
  </svg>`;
}

function kmSalesPlanningV3OpenSeasonality(category) {
  const detail = kmSalesPlanningV3SeasonalityDetail(category);
  if (!detail) return;
  const root = qs("financeDashboard");
  root.querySelector("[data-sales-planning-detail-modal]")?.remove();
  const points = Array.isArray(detail.points) ? detail.points : [];
  root.insertAdjacentHTML("beforeend", `<div class="sales-planning-trend-modal" data-sales-planning-detail-modal role="dialog" aria-modal="true" aria-labelledby="salesPlanningSeasonalityTitle">
    <div class="sales-planning-trend-backdrop" data-sales-planning-detail-close></div>
    <section class="sales-planning-trend-dialog">
      <header>
        <div>
          <h2 id="salesPlanningSeasonalityTitle">Сезонность категории · ${escapeHtml(detail.category_name || "Все выбранные категории")}</h2>
          <p>Годовой профиль ниши MPStats по календарным месяцам. Индекс 1,00 равен среднемесячному уровню рынка.</p>
        </div>
        ${kmSalesPlanningV3CloseButton()}
      </header>
      <div class="sales-planning-trend-body">
        ${kmSalesPlanningV3SeasonalityChart(points)}
        <div class="finance-table-wrap">
          <table class="finance-table sales-planning-seasonality-table">
            <thead><tr><th>Месяц</th><th>Средние продажи ниши, шт/мес</th><th>Средняя выручка ниши, ₽/мес</th><th>Индекс месяца</th><th>Наблюдений, мес.</th></tr></thead>
            <tbody>${points.map((row) => `<tr>
              <td>${escapeHtml(row.month_label || "—")}</td>
              <td class="num">${escapeHtml(formatNumber(row.average_sales_qty, 0))}</td>
              <td class="num">${escapeHtml(financeMoney(row.average_revenue_rub))}</td>
              <td class="num">${escapeHtml(kmSalesPlanningCoeff(row.seasonality_index))}</td>
              <td class="num">${escapeHtml(formatNumber(row.observations, 0))}</td>
            </tr>`).join("")}</tbody>
          </table>
        </div>
        <p class="sales-planning-trend-source">Источник: ${escapeHtml(detail.source || "MPStats")} · история ${escapeHtml(detail.period_start || "—")} — ${escapeHtml(detail.period_end || "—")} · ${escapeHtml(detail.index_method || "")}.</p>
      </div>
    </section>
  </div>`);
  root.querySelector("button[data-sales-planning-detail-close]")?.focus?.();
}

function kmSalesPlanningV3PlanningScopeProducts(level, ownerKey, category) {
  const products = Array.isArray(kmSalesPlanningPayload?.products)
    ? kmSalesPlanningPayload.products
    : [];
  if (level === "product") {
    return products.filter(
      (product) => String(product.sku || product.article || "") === String(ownerKey),
    );
  }
  if (level === "category") {
    return products.filter(
      (product) => String(product.category_name || "Без категории") === String(category),
    );
  }
  return products;
}

function kmSalesPlanningV3PlanningScopeLabel(level, ownerKey, category, products) {
  if (level === "product") {
    const product = products[0] || {};
    return String(product.article || ownerKey || "Товар")
      + " · SKU " + String(product.sku || "—");
  }
  if (level === "category") return String(category || "Без категории");
  return "Все выбранные товары";
}

function kmSalesPlanningV3PlanningRows(level, ownerKey, category) {
  const products = kmSalesPlanningV3PlanningScopeProducts(level, ownerKey, category);
  const anchorMonth = String(kmSalesPlanningPayload?.period?.anchor_month || "");
  const months = (kmSalesPlanningPayload?.months || []).filter(
    (month) => String(month.month_start || "") > anchorMonth,
  );
  const sum = (rows, key) => rows.reduce(
    (total, row) => total + (Number.isFinite(Number(row?.[key])) ? Number(row[key]) : 0),
    0,
  );
  return months.map((month) => {
    const productMonths = products.map(
      (product) => kmSalesPlanningV3ProductMonth(product, month.month_start),
    );
    const baselineUnits = sum(productMonths, "planning_baseline_units");
    const afterGrowthUnits = sum(productMonths, "planning_after_growth_units");
    const afterTrendUnits = sum(productMonths, "planning_after_trend_units");
    const afterSeasonalityUnits = sum(productMonths, "planning_after_seasonality_units");
    const forecastUnits = sum(productMonths, "planning_forecast_units");
    return {
      month_start: month.month_start,
      baseline_units: baselineUnits,
      growth_coefficient: baselineUnits > 0 ? afterGrowthUnits / baselineUnits : null,
      after_growth_units: afterGrowthUnits,
      trend_coefficient: afterGrowthUnits > 0 ? afterTrendUnits / afterGrowthUnits : null,
      after_trend_units: afterTrendUnits,
      seasonality_coefficient: afterTrendUnits > 0
        ? afterSeasonalityUnits / afterTrendUnits
        : null,
      after_seasonality_units: afterSeasonalityUnits,
      promotion_coefficient: afterSeasonalityUnits > 0
        ? forecastUnits / afterSeasonalityUnits
        : null,
      planning_coefficient: baselineUnits > 0 ? forecastUnits / baselineUnits : null,
      forecast_units: forecastUnits,
      sku_count: products.length,
    };
  }).filter((row) => row.planning_coefficient !== null);
}

function kmSalesPlanningV3PlanningChart(rows) {
  const source = Array.isArray(rows) ? rows : [];
  if (!source.length) {
    return '<div class="sales-planning-trend-empty">Нет будущих месяцев для расчёта коэффициента.</div>';
  }
  const series = [
    { key: "growth_coefficient", label: "Рост к базе", className: "planning-growth" },
    { key: "trend_coefficient", label: "Тренд", className: "planning-trend" },
    { key: "seasonality_coefficient", label: "Сезонность", className: "planning-seasonality" },
    { key: "promotion_coefficient", label: "Продвижение", className: "planning-promotion" },
    { key: "planning_coefficient", label: "Итог", className: "planning-total" },
  ];
  const values = source.flatMap(
    (row) => series.map((item) => Number(row[item.key])).filter(Number.isFinite),
  );
  const width = 980;
  const height = 310;
  const left = 58;
  const right = 24;
  const top = 42;
  const bottom = 62;
  const minimum = Math.max(0, Math.min(...values, 1) - 0.12);
  const maximum = Math.max(...values, 1) + 0.12;
  const range = Math.max(maximum - minimum, 0.3);
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const xStep = source.length > 1 ? plotWidth / (source.length - 1) : 0;
  const yFor = (value) => top + plotHeight * (1 - (value - minimum) / range);
  const ticks = [minimum, minimum + range / 2, maximum];
  const line = (item) => {
    const points = source.map((row, index) => (
      (left + xStep * index) + "," + yFor(Number(row[item.key]))
    )).join(" ");
    return '<polyline points="' + points + '" class="planning-factor-line '
      + item.className + '"></polyline>';
  };
  const legend = series.map((item, index) => (
    '<g transform="translate(' + (left + index * 170) + ',18)">'
    + '<line x1="0" y1="0" x2="24" y2="0" class="planning-factor-legend '
    + item.className + '"></line>'
    + '<text x="31" y="4" class="planning-factor-legend-label">' + escapeHtml(item.label)
    + "</text></g>"
  )).join("");
  const labels = source.map((row, index) => {
    const x = left + xStep * index;
    const y = yFor(Number(row.planning_coefficient));
    return '<g><circle cx="' + x + '" cy="' + y
      + '" r="5" class="planning-total-point"><title>'
      + escapeHtml(kmSalesPlanningMonth(row.month_start) + ": "
        + kmSalesPlanningCoeff(row.planning_coefficient))
      + '</title></circle><text x="' + x + '" y="' + Math.max(y - 12, 34)
      + '" text-anchor="middle" class="planning-total-value">'
      + escapeHtml(kmSalesPlanningCoeff(row.planning_coefficient))
      + '</text><text x="' + x + '" y="' + (height - 22)
      + '" text-anchor="middle" class="planning-factor-month">'
      + escapeHtml(kmSalesPlanningMonth(row.month_start, true)) + "</text></g>";
  }).join("");
  const grid = ticks.map((value) => (
    '<line x1="' + left + '" y1="' + yFor(value) + '" x2="' + (width - right)
    + '" y2="' + yFor(value) + '" class="planning-factor-grid"></line>'
    + '<text x="' + (left - 8) + '" y="' + (yFor(value) + 4)
    + '" text-anchor="end" class="planning-factor-grid-label">'
    + escapeHtml(formatNumber(value, 2)) + "</text>"
  )).join("");
  return '<svg class="sales-planning-planning-chart" viewBox="0 0 ' + width + " " + height
    + '" role="img" aria-label="Факторы и итоговый коэффициент планирования по месяцам">'
    + "<title>Рост к базе, тренд, сезонность, продвижение и итоговый коэффициент планирования</title>"
    + legend + grid
    + '<line x1="' + left + '" y1="' + yFor(1) + '" x2="' + (width - right)
    + '" y2="' + yFor(1) + '" class="planning-factor-baseline"></line>'
    + series.map(line).join("") + labels + "</svg>";
}

function kmSalesPlanningV3CoefficientBreakdown(rows) {
  const displayCoefficient = (value) => (
    Number.isFinite(Number(value)) ? kmSalesPlanningCoeff(value) : "—"
  );
  const formula = (row) => (
    displayCoefficient(row.growth_coefficient)
    + " × " + displayCoefficient(row.trend_coefficient)
    + " × " + displayCoefficient(row.seasonality_coefficient)
    + " × " + displayCoefficient(row.promotion_coefficient)
  );
  const metrics = [
    { label: "Рост к базе, ×", value: (row) => displayCoefficient(row.growth_coefficient) },
    { label: "Эффективный тренд, ×", value: (row) => displayCoefficient(row.trend_coefficient) },
    { label: "Эффективная сезонность, ×", value: (row) => displayCoefficient(row.seasonality_coefficient) },
    { label: "Продвижение, ×", value: (row) => displayCoefficient(row.promotion_coefficient) },
    { label: "Подстановка в формулу", value: formula, className: "planning-coefficient-formula-row" },
    { label: "Коэффициент планирования, ×", value: (row) => displayCoefficient(row.planning_coefficient), className: "planning-coefficient-result-row" },
    { label: "Изменение к базе, %", value: (row) => (
      Number.isFinite(Number(row.planning_coefficient))
        ? formatNumber((Number(row.planning_coefficient) - 1) * 100, 1) + "%"
        : "—"
    ) },
  ];
  return '<section id="salesPlanningCoefficientBreakdown" class="sales-planning-coefficient-breakdown"'
    + ' data-sales-planning-coefficient-breakdown hidden aria-labelledby="salesPlanningCoefficientBreakdownTitle">'
    + '<header><div><span class="sales-planning-coefficient-eyebrow">Второй уровень расчёта</span>'
    + '<h3 id="salesPlanningCoefficientBreakdownTitle">Как рассчитан коэффициент планирования</h3>'
    + '<p>Рост к базе × эффективный тренд × эффективная сезонность × продвижение. На уровне кабинета и категории показаны эквивалентные агрегатные множители по сумме выбранных SKU.</p>'
    + '</div></header>'
    + '<div class="sales-planning-coefficient-sources">'
    + '<article><strong>Рост к базе</strong><span>Применяется один раз к расчётному плану текущего месяца и переносится дальше через базу предыдущего плана, без ежемесячного сложного процента.</span></article>'
    + '<article><strong>Тренд</strong><span>Продажи за 30 дней → три декады → ограничение 0,70–1,35 → вес тренда. Применяется только к первому плановому месяцу.</span></article>'
    + '<article><strong>Сезонность</strong><span>Индекс календарного месяца MPStats × ручная поправка сезонности из настроек плана.</span></article>'
    + '<article><strong>Продвижение</strong><span>Коэффициент целевого месяца: настройка категории, затем общая настройка, затем коэффициент роста.</span></article>'
    + '</div><div class="finance-table-wrap">'
    + '<table class="finance-table sales-planning-coefficient-breakdown-table">'
    + '<thead><tr><th>Множитель</th>'
    + rows.map((row) => '<th>' + escapeHtml(kmSalesPlanningMonth(row.month_start, true)) + '</th>').join("")
    + '</tr></thead><tbody>'
    + metrics.map((metric) => (
      '<tr class="' + (metric.className || "") + '"><th>' + escapeHtml(metric.label) + '</th>'
      + rows.map((row) => '<td class="num">' + escapeHtml(metric.value(row)) + '</td>').join("")
      + '</tr>'
    )).join("")
    + '</tbody></table></div>'
    + '<p class="sales-planning-coefficient-note">Проверка месяца: произведение четырёх множителей равно коэффициенту планирования. Коэффициент 1,00 не меняет базу; 0,78 уменьшает её на 22%; 1,21 увеличивает на 21%.</p>'
    + '</section>';
}

function kmSalesPlanningV3ToggleCoefficientBreakdown(trigger) {
  const modal = trigger.closest("[data-sales-planning-detail-modal]");
  const panel = modal?.querySelector("[data-sales-planning-coefficient-breakdown]");
  if (!panel) return;
  const expanded = trigger.getAttribute("aria-expanded") === "true";
  trigger.setAttribute("aria-expanded", expanded ? "false" : "true");
  panel.hidden = expanded;
  trigger.closest("tr")?.classList.toggle("is-expanded", !expanded);
  if (!expanded) panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function kmSalesPlanningV3PlanningTable(rows) {
  const metrics = [
    { label: "База месяца (текущий / прошлый план), шт", key: "baseline_units", kind: "units", className: "planning-step-start" },
    { label: "× Рост к базе", key: "growth_coefficient", kind: "coefficient" },
    { label: "= После роста, шт", key: "after_growth_units", kind: "units", className: "planning-step-result" },
    { label: "× Эффективный тренд", key: "trend_coefficient", kind: "coefficient" },
    { label: "= После тренда, шт", key: "after_trend_units", kind: "units", className: "planning-step-result" },
    { label: "× Сезонность", key: "seasonality_coefficient", kind: "coefficient" },
    { label: "= После сезонности, шт", key: "after_seasonality_units", kind: "units", className: "planning-step-result" },
    { label: "× Продвижение", key: "promotion_coefficient", kind: "coefficient" },
    { label: "= Коэффициент планирования", key: "planning_coefficient", kind: "coefficient", className: "planning-step-total", interactive: true },
    { label: "= Прогноз спроса, шт", key: "forecast_units", kind: "units", className: "planning-step-forecast" },
  ];
  const display = (value, kind) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    if (kind === "coefficient") return kmSalesPlanningCoeff(value);
    return formatNumber(value, 1);
  };
  return '<table class="finance-table sales-planning-planning-table">'
    + '<thead><tr><th>Расчёт</th>'
    + rows.map((row) => '<th>' + escapeHtml(kmSalesPlanningMonth(row.month_start, true)) + "</th>").join("")
    + "</tr></thead><tbody>"
    + metrics.map((metric) => {
      const interaction = metric.interactive
        ? ' class="planning-coefficient-disclosure" data-sales-planning-coefficient-breakdown-toggle'
          + ' tabindex="0" role="button" aria-expanded="false"'
          + ' aria-controls="salesPlanningCoefficientBreakdown"'
          + ' title="Открыть расчёт коэффициента планирования"'
        : "";
      return '<tr class="' + (metric.className || "") + '"><th' + interaction + '>'
        + escapeHtml(metric.label) + "</th>" + rows.map((row) => (
          '<td class="num">' + escapeHtml(display(row[metric.key], metric.kind)) + "</td>"
        )).join("") + "</tr>";
    }).join("")
    + "</tbody></table>";
}

function kmSalesPlanningV3OpenPlanning(category, level = "", ownerKey = "") {
  const root = qs("financeDashboard");
  root.querySelector("[data-sales-planning-detail-modal]")?.remove();
  const products = kmSalesPlanningV3PlanningScopeProducts(level, ownerKey, category);
  const rows = kmSalesPlanningV3PlanningRows(level, ownerKey, category);
  if (!rows.length) return;
  const scopeLabel = kmSalesPlanningV3PlanningScopeLabel(
    level,
    ownerKey,
    category,
    products,
  );
  const skuCount = products.length;
  const html = '<div class="sales-planning-trend-modal" data-sales-planning-detail-modal'
    + ' role="dialog" aria-modal="true" aria-labelledby="salesPlanningPlanningTitle">'
    + '<div class="sales-planning-trend-backdrop" data-sales-planning-detail-close></div>'
    + '<section class="sales-planning-trend-dialog sales-planning-planning-dialog"><header><div>'
    + '<h2 id="salesPlanningPlanningTitle">Коэффициент планирования · '
    + escapeHtml(scopeLabel) + "</h2>"
    + "<p>Первый плановый месяц: план продаж текущего месяца × коэффициент первого планового месяца. Каждый следующий месяц: "
    + "план предыдущего месяца × коэффициент планируемого месяца. "
    + "Ограничение доступным запасом в этот коэффициент не входит · "
    + escapeHtml(String(skuCount)) + " SKU.</p></div>"
    + kmSalesPlanningV3CloseButton()
    + '</header><div class="sales-planning-trend-body">'
    + '<div class="sales-planning-planning-workspace"><div class="sales-planning-planning-stage">'
    + kmSalesPlanningV3PlanningChart(rows)
    + '<div class="finance-table-wrap">' + kmSalesPlanningV3PlanningTable(rows) + "</div>"
    + kmSalesPlanningV3CoefficientBreakdown(rows)
    + "</div></div>"
    + '<p class="sales-planning-trend-source">База первого планового месяца — Run Rate текущего месяца. План М+1 = текущий RR × коэффициенты М+1; каждый следующий план = план предыдущего месяца × коэффициенты нового месяца. Агрегатные факторы рассчитаны по сумме SKU, значения округлены только при показе.</p>'
    + "</div></section></div>";
  root.insertAdjacentHTML("beforeend", html);
  root.querySelector("button[data-sales-planning-detail-close]")?.focus?.();
}

function kmSalesPlanningV3PromotionRows(category) {
  const settings = kmSalesPlanningPayload?.promotion_settings || {};
  const globalValues = settings.global || {};
  const categoryValues = category === "__all__" ? {} : (settings.by_category?.[category] || {});
  const fallback = Number(settings.default_coefficient || 1);
  return (kmSalesPlanningPayload?.months || []).map((month) => ({
    month_start: month.month_start,
    coefficient: Number(categoryValues[month.month_start] ?? globalValues[month.month_start] ?? fallback),
  }));
}

function kmSalesPlanningV3PromotionChart(rows) {
  const values = (rows || []).map((row) => {
    const value = Number(row.coefficient);
    return Number.isFinite(value) && value >= 0.1 ? value : 1;
  });
  if (!values.length) return "";
  const width = 920;
  const height = 250;
  const left = 52;
  const right = 18;
  const top = 34;
  const bottom = 50;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const minimum = Math.max(0.1, Math.min(...values, 1) - 0.1);
  const maximum = Math.max(...values, 1) + 0.1;
  const range = Math.max(maximum - minimum, 0.2);
  const yFor = (value) => top + plotHeight * (1 - (value - minimum) / range);
  const step = rows.length > 1 ? plotWidth / (rows.length - 1) : 0;
  const points = rows.map((row, index) => ({
    row,
    value: values[index],
    x: left + step * index,
    y: yFor(values[index]),
  }));
  const ticks = [minimum, minimum + range / 2, minimum + range];
  return `<svg class="sales-planning-promotion-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Коэффициент продвижения по месяцам">
    <title>Уровень продвижения по месяцам; линия обновляется при вводе</title>
    <text x="${left}" y="14" class="promotion-axis-title">Уровень</text>
    ${ticks.map((value) => `<line x1="${left}" y1="${yFor(value)}" x2="${width - right}" y2="${yFor(value)}" class="promotion-grid"></line><text x="${left - 8}" y="${yFor(value) + 4}" text-anchor="end" class="promotion-grid-label">${escapeHtml(formatNumber(value, 2))}</text>`).join("")}
    <line x1="${left}" y1="${yFor(1)}" x2="${width - right}" y2="${yFor(1)}" class="promotion-baseline"></line>
    <polyline points="${points.map((point) => `${point.x},${point.y}`).join(" ")}" class="promotion-line"></polyline>
    ${points.map(({ row, value, x, y }) => `<g>
      <circle cx="${x}" cy="${y}" r="5" class="promotion-point"><title>${escapeHtml(`${kmSalesPlanningMonth(row.month_start)}: ${formatNumber(value, 2)}`)}</title></circle>
      <text x="${x}" y="${Math.max(y - 12, 27)}" text-anchor="middle" class="promotion-value">${escapeHtml(formatNumber(value, 2))}</text>
      <text x="${x}" y="${height - 18}" text-anchor="middle" class="promotion-label">${escapeHtml(kmSalesPlanningMonth(row.month_start, true))}</text>
    </g>`).join("")}
  </svg>`;
}

function kmSalesPlanningV3PromotionRowsFromModal(modal) {
  return [...(modal?.querySelectorAll("[data-sales-planning-promotion-input]") || [])]
    .map((input) => ({
      month_start: input.dataset.salesPlanningPromotionInput,
      coefficient: input.value,
    }));
}

function kmSalesPlanningV3RefreshPromotionChart(modal) {
  const chart = modal?.querySelector("[data-sales-planning-promotion-chart]");
  if (chart) chart.innerHTML = kmSalesPlanningV3PromotionChart(kmSalesPlanningV3PromotionRowsFromModal(modal));
}

function kmSalesPlanningV3OpenPromotion(category) {
  const root = qs("financeDashboard");
  root.querySelector("[data-sales-planning-detail-modal]")?.remove();
  const rows = kmSalesPlanningV3PromotionRows(category);
  const scopeLabel = category === "__all__" ? "Общие значения" : category;
  root.insertAdjacentHTML("beforeend", `<div class="sales-planning-trend-modal" data-sales-planning-detail-modal data-sales-planning-promotion-category="${escapeHtml(category)}" role="dialog" aria-modal="true" aria-labelledby="salesPlanningPromotionTitle">
    <div class="sales-planning-trend-backdrop" data-sales-planning-detail-close></div>
    <section class="sales-planning-trend-dialog sales-planning-promotion-dialog">
      <header>
        <div>
          <h2 id="salesPlanningPromotionTitle">Коэффициент продвижения · ${escapeHtml(scopeLabel)}</h2>
          <p>Введите уровень продвижения по каждому месяцу без верхнего ограничения. В план месяца входит изменение к предыдущему периоду: текущий уровень ÷ предыдущий; 1,00 — базовый уровень.</p>
        </div>
        ${kmSalesPlanningV3CloseButton()}
      </header>
      <div class="sales-planning-trend-body">
        <div class="sales-planning-promotion-workspace">
          <div class="sales-planning-promotion-stage">
            <div data-sales-planning-promotion-chart>${kmSalesPlanningV3PromotionChart(rows)}</div>
            <div class="sales-planning-promotion-months" role="group" aria-label="Коэффициенты продвижения по месяцам">
              ${rows.map((row) => `<label>
                <span>${escapeHtml(kmSalesPlanningMonth(row.month_start, true))}</span>
                <small>${escapeHtml(String(row.month_start || "").slice(0, 4))}</small>
                <input class="finance-input" type="number" inputmode="decimal" min="0.10" step="0.01" value="${escapeHtml(row.coefficient.toFixed(2))}" data-sales-planning-promotion-input="${escapeHtml(row.month_start)}" aria-label="Коэффициент продвижения за ${escapeHtml(kmSalesPlanningMonth(row.month_start))}" />
              </label>`).join("")}
            </div>
          </div>
        </div>
        <footer class="sales-planning-promotion-actions">
          <p data-sales-planning-promotion-status aria-live="polite"></p>
          <button type="button" data-sales-planning-promotion-save aria-label="Сохранить коэффициенты продвижения" title="Сохранить коэффициенты продвижения">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l4 4L19 6"></path></svg>
          </button>
        </footer>
      </div>
    </section>
  </div>`);
  root.querySelector("[data-sales-planning-promotion-input]")?.focus?.();
}

async function kmSalesPlanningV3SavePromotion(button) {
  const modal = button.closest("[data-sales-planning-detail-modal]");
  const category = modal?.dataset.salesPlanningPromotionCategory || "__all__";
  const status = modal?.querySelector("[data-sales-planning-promotion-status]");
  const coefficients = [...(modal?.querySelectorAll("[data-sales-planning-promotion-input]") || [])]
    .map((input) => ({
      month_start: input.dataset.salesPlanningPromotionInput,
      coefficient: input.value,
    }));
  button.disabled = true;
  if (status) status.textContent = "Сохраняю…";
  try {
    const response = await fetch("/api/km-trade/sales-promotion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client: currentClient(),
        marketplace: kmSalesPlanningPayload?.marketplace || "ozon",
        category_name: category,
        coefficients,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Не удалось сохранить коэффициенты");
    await renderKmSalesPlanningDashboard();
  } catch (error) {
    if (status) status.textContent = error.message;
  } finally {
    if (button.isConnected) button.disabled = false;
  }
}

function kmSalesPlanningV3BindInteractions(root) {
  root.querySelectorAll("[data-sales-planning-category-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.salesPlanningCategoryToggle || "";
      if (kmSalesPlanningV3CollapsedCategories.has(key)) kmSalesPlanningV3CollapsedCategories.delete(key);
      else kmSalesPlanningV3CollapsedCategories.add(key);
      renderKmSalesPlanningPayload(kmSalesPlanningPayload);
    });
  });
  root.querySelectorAll("[data-sales-planning-product-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.salesPlanningProductToggle || "";
      if (kmSalesPlanningV3CollapsedProducts.has(key)) kmSalesPlanningV3CollapsedProducts.delete(key);
      else kmSalesPlanningV3CollapsedProducts.add(key);
      renderKmSalesPlanningPayload(kmSalesPlanningPayload);
    });
  });
  root.querySelectorAll("th[data-sales-planning-col][draggable='true']").forEach((th) => {
    th.addEventListener("dragstart", (event) => {
      kmSalesPlanningV3DraggedColumn = {
        key: th.dataset.salesPlanningCol,
        group: th.dataset.salesPlanningGroup,
      };
      th.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", JSON.stringify(kmSalesPlanningV3DraggedColumn));
      }
    });
    th.addEventListener("dragover", (event) => {
      const sameGroup = kmSalesPlanningV3DraggedColumn?.group === th.dataset.salesPlanningGroup;
      const differentColumn = kmSalesPlanningV3DraggedColumn?.key !== th.dataset.salesPlanningCol;
      if (!sameGroup || !differentColumn) return;
      event.preventDefault();
      th.classList.add("drag-over");
    });
    th.addEventListener("dragleave", () => th.classList.remove("drag-over"));
    th.addEventListener("drop", (event) => {
      event.preventDefault();
      th.classList.remove("drag-over");
      const targetKey = th.dataset.salesPlanningCol;
      const group = th.dataset.salesPlanningGroup;
      if (
        !kmSalesPlanningV3DraggedColumn
        || kmSalesPlanningV3DraggedColumn.group !== group
        || kmSalesPlanningV3DraggedColumn.key === targetKey
      ) return;
      const ordered = kmSalesPlanningV3OrderedMonths(kmSalesPlanningPayload?.months || [], group)
        .map((row) => String(row.month_start));
      const fromIndex = ordered.indexOf(kmSalesPlanningV3DraggedColumn.key);
      const toIndex = ordered.indexOf(targetKey);
      if (fromIndex < 0 || toIndex < 0) return;
      const [moved] = ordered.splice(fromIndex, 1);
      ordered.splice(toIndex, 0, moved);
      kmSalesPlanningV3ColumnOrder[group] = ordered;
      kmSalesPlanningV3SaveColumnOrder();
      renderKmSalesPlanningPayload(kmSalesPlanningPayload);
    });
    th.addEventListener("dragend", () => {
      kmSalesPlanningV3DraggedColumn = null;
      root.querySelectorAll(".dragging,.drag-over").forEach((item) => {
        item.classList.remove("dragging", "drag-over");
      });
    });
  });
}

const renderKmSalesPlanningPayloadV2 = renderKmSalesPlanningPayload;
renderKmSalesPlanningPayload = function renderKmSalesPlanningPayloadV3(payload) {
  const products = payload?.products || [];
  const firstProduct = products[0] || {};
  const lastProduct = products[products.length - 1] || {};
  const collapseSignature = [
    payload?.client || "",
    payload?.marketplace || "",
    products.length,
    firstProduct.sku || firstProduct.article || "",
    lastProduct.sku || lastProduct.article || "",
  ].join(":");
  if (collapseSignature !== kmSalesPlanningV3CollapseSignature) {
    kmSalesPlanningV3CollapseSignature = collapseSignature;
    kmSalesPlanningV3CollapsedCategories.clear();
    kmSalesPlanningV3CollapsedProducts.clear();
    products.forEach((product) => {
      kmSalesPlanningV3CollapsedCategories.add(String(product.category_name || "Без категории"));
      kmSalesPlanningV3CollapsedProducts.add(String(product.sku || product.article || ""));
    });
  }
  renderKmSalesPlanningPayloadV2(payload);
  const root = qs("financeDashboard");
  const yearSection = root.querySelector("[data-sales-planning-year-matrix]")?.closest(".finance-card");
  const yearHeader = yearSection?.querySelector(".finance-card-head");
  const yearDescription = yearHeader?.querySelector("p");
  const treeSection = root.querySelector("[data-sales-planning-tree-matrix]")?.closest(".finance-card");
  const treeDescription = treeSection?.querySelector(".finance-card-head p");
  root.querySelectorAll("[data-sales-planning-value-toggle]").forEach((toggle) => toggle.remove());
  if (yearDescription) {
    yearDescription.textContent = "Прошлые месяцы — факт; текущий месяц — план, факт, RR и выполнение; будущие месяцы — каскадный план. Одна строка заказов.";
  }
  if (treeDescription) {
    treeDescription.textContent = "Категории → товары. Каждая строка показывает заказы: прошлый факт, текущие план/факт/RR и будущий каскадный план.";
  }
  if (yearHeader) {
    yearHeader.insertAdjacentHTML("beforeend", kmSalesPlanningV3ValueToggle());
  }
  kmSalesPlanningV3BindInteractions(root);
};

document.addEventListener("click", (event) => {
  const detailLabel = event.target.closest("[data-sales-planning-detail]");
  if (detailLabel && state.dashboard === "salesPlanning") {
    const category = detailLabel.dataset.salesPlanningDetailCategory || "__all__";
    const detailType = detailLabel.dataset.salesPlanningDetail;
    const detailLevel = detailLabel.dataset.salesPlanningDetailLevel || "";
    const detailOwner = detailLabel.dataset.salesPlanningDetailOwner || "";
    if (detailType === "seasonality") kmSalesPlanningV3OpenSeasonality(category);
    if (detailType === "trend") kmSalesPlanningV3OpenTrend(category);
    if (detailType === "promotion") kmSalesPlanningV3OpenPromotion(category);
    if (detailType === "planning") {
      kmSalesPlanningV3OpenPlanning(category, detailLevel, detailOwner);
    }
    return;
  }
  const coefficientBreakdownToggle = event.target.closest(
    "[data-sales-planning-coefficient-breakdown-toggle]",
  );
  if (coefficientBreakdownToggle) {
    kmSalesPlanningV3ToggleCoefficientBreakdown(coefficientBreakdownToggle);
    return;
  }
  const detailClose = event.target.closest("[data-sales-planning-detail-close]");
  if (detailClose) {
    kmSalesPlanningV3CloseDetail();
    return;
  }
  const promotionSave = event.target.closest("[data-sales-planning-promotion-save]");
  if (promotionSave) {
    kmSalesPlanningV3SavePromotion(promotionSave);
    return;
  }
  const valueModeButton = event.target.closest("[data-sales-planning-value-mode]");
  if (valueModeButton && state.dashboard === "salesPlanning") {
    const nextMode = valueModeButton.dataset.salesPlanningValueMode;
    if (nextMode !== "money" && nextMode !== "units") return;
    kmSalesPlanningV3ValueMode = nextMode;
    try {
      localStorage.setItem("kokoc:km-sales-planning:value-mode", nextMode);
    } catch (_error) {
      // UI preferences are optional.
    }
    renderKmSalesPlanningPayload(kmSalesPlanningPayload);
    return;
  }
  const toggle = event.target.closest("[data-sales-planning-fact-toggle]");
  if (!toggle || state.dashboard !== "salesPlanning") return;
  kmSalesPlanningV3FactCollapsed = toggle.dataset.salesPlanningFactToggle === "collapse";
  try {
    localStorage.setItem(
      "kokoc:km-sales-planning:fact-collapsed",
      kmSalesPlanningV3FactCollapsed ? "1" : "0",
    );
  } catch (_error) {
    // UI preferences are optional.
  }
  document.querySelectorAll("[data-sales-planning-matrix]").forEach((table) => {
    table.classList.toggle("is-fact-collapsed", kmSalesPlanningV3FactCollapsed);
  });
});

document.addEventListener("input", (event) => {
  const promotionInput = event.target.closest?.("[data-sales-planning-promotion-input]");
  if (!promotionInput) return;
  kmSalesPlanningV3RefreshPromotionChart(
    promotionInput.closest("[data-sales-planning-detail-modal]"),
  );
});

document.addEventListener("keydown", (event) => {
  const coefficientBreakdownToggle = event.target.closest?.(
    "[data-sales-planning-coefficient-breakdown-toggle]",
  );
  if (coefficientBreakdownToggle && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    coefficientBreakdownToggle.click();
    return;
  }
  const detailLabel = event.target.closest?.("[data-sales-planning-detail]");
  if (detailLabel && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    detailLabel.click();
    return;
  }
  if (event.key === "Escape") {
    kmSalesPlanningV3CloseDetail();
  }
});
