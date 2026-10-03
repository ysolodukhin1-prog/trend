let kmMediaPlanPayload = null;

const kmMediaReferenceLabels = {
  ctr_pct: "CTR, %",
  click_to_cart_pct: "CR: клик → корзина, %",
  cart_to_order_pct: "CRO: корзина → заказ, %",
  organic_share_pct: "Organic Share, %",
  ad_ctr_pct: "CTR рекламы, %",
  cpc_rub: "CPC, ₽",
  tacos_pct: "TACoS, %",
  acos_pct: "ACoS, %",
};

const kmMediaMetricDescriptions = {
  ad_ctr_pct: "CTR рекламы = рекламные переходы / рекламные показы × 100.",
  cpc_rub: "CPC = рекламные расходы / рекламные переходы.",
  ecpm_rub: "eCPM = рекламные расходы / рекламные показы × 1000. Для CPC-модели: CPC × CTR рекламы (%) × 10.",
};

function kmMediaMonth(value) {
  const parsed = new Date(`${String(value || "")}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? String(value || "") : parsed.toLocaleDateString("ru-RU", { month: "short" });
}

function kmMediaPlanClientLabel(value = currentClient()) {
  return typeof clientLabel === "function" ? clientLabel(value) : value;
}

function kmMediaValue(value, kind, scenario) {
  if (value === null || value === undefined || value === "") return "—";
  if (scenario === "plan_fact_pct" || scenario === "plan_rr_pct" || kind === "percent") return `${formatNumber(value, 1)}%`;
  if (kind === "money") return financeMoney(value);
  if (kind === "coefficient") return formatNumber(value, 2);
  return formatNumber(value, 0);
}

function kmMediaReferenceBlock(payload) {
  return `<section class="finance-card km-media-reference">
    <header class="finance-card-head">
      <div><h2>Экспертные референсы</h2><p>По умолчанию — взвешенный факт трёх завершённых месяцев. Ручное значение имеет приоритет.</p></div>
      <div class="km-media-actions">
        <button type="button" class="sales-planning-icon-button is-primary" data-media-plan-action="save" aria-label="Сохранить медиаплан" title="Сохранить медиаплан">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h12l2 2v14H5zM8 4v6h8V4M8 20v-6h8v6" /></svg>
        </button>
        <button type="button" class="ghost sales-planning-icon-button" data-media-plan-action="reset" aria-label="Вернуть фактические референсы" title="Вернуть фактические референсы">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4v6h6M5.5 15a7 7 0 1 0 1.5-8.5L4 10" /></svg>
        </button>
      </div>
    </header>
    <div class="km-media-reference-grid">
      ${Object.entries(kmMediaReferenceLabels).map(([key, label]) => `<label>
        <span>${escapeHtml(label)}</span>
        <input class="finance-input" data-media-reference="${key}" type="number" min="0" ${key === "cpc_rub" ? "" : 'max="100"'} step="0.1" value="${payload.references?.[key] ?? ""}" />
        <small>${payload.reference_sources?.[key] === "manual" ? "ручной" : "сглаженный факт"}</small>
      </label>`).join("")}
    </div>
  </section>`;
}

function kmMediaPlanTable(payload) {
  const groups = [];
  (payload.columns || []).forEach((column) => {
    const previous = groups[groups.length - 1];
    if (previous?.month_start === column.month_start) previous.count += 1;
    else groups.push({ month_start: column.month_start, count: 1 });
  });
  return `<div class="km-media-table-wrap">
    <table class="finance-table km-media-table">
      <thead>
        <tr><th rowspan="2">Статья</th>${groups.map((group) => `<th colspan="${group.count}">${escapeHtml(kmMediaMonth(group.month_start))}</th>`).join("")}</tr>
        <tr>${(payload.columns || []).map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>
      </thead>
      <tbody>${(payload.rows || []).map((row) => {
        const description = kmMediaMetricDescriptions[row.key] || "";
        return `<tr data-media-metric="${row.key}">
        <th${description ? ` title="${escapeHtml(description)}"` : ""}>${escapeHtml(row.label)}${description ? '<span class="km-media-metric-help" aria-hidden="true">ⓘ</span>' : ""}</th>
        ${(payload.columns || []).map((column) => {
          const value = row.values?.[column.key];
          const editable = row.key === "planning_coefficient" && column.scenario === "plan" && column.month_start >= payload.period.anchor_month;
          return `<td class="num ${column.scenario === "plan" ? "is-plan" : ""}">${editable
            ? `<input data-media-coefficient="${column.month_start}" type="number" min="0.1" step="0.05" value="${value ?? 1}" aria-label="Коэффициент планирования ${escapeHtml(kmMediaMonth(column.month_start))}" />`
            : kmMediaValue(value, row.kind, column.scenario)}</td>`;
        }).join("")}
      </tr>`;
      }).join("")}</tbody>
    </table>
  </div>`;
}

function renderKmMediaPlanPayload(payload) {
  kmMediaPlanPayload = payload;
  const root = qs("financeDashboard");
  const reportClientLabel = kmMediaPlanClientLabel(payload.client || currentClient());
  root.innerHTML = `
    <section class="finance-card km-media-intro">
      <header class="finance-card-head"><div><h2>Медиаплан ${escapeHtml(reportClientLabel)}</h2><p>Обратная декомпозиция утверждённого плана продаж: заказы → корзины → переходы → показы.</p></div><span class="sales-planning-freshness">Факт по ${escapeHtml(payload.period?.fact_to || "—")} · Ozon</span></header>
    </section>
    ${kmMediaReferenceBlock(payload)}
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>План по месяцам</h2><p>Завершённые месяцы — факт; текущий — план, факт, Run Rate и выполнение; будущие — план.</p></div></header>
      ${kmMediaPlanTable(payload)}
    </section>
    ${(payload.warnings || []).map((warning) => `<div class="finance-alert finance-alert-warning">${escapeHtml(warning)}</div>`).join("")}
    ${financeMethodology(payload.methodology)}
  `;
}

async function renderKmMediaPlanDashboard() {
  setFinanceShellVisible(true);
  qs("dashboardTitle").textContent = "Медиаплан";
  const requestedClient = currentClient();
  const requestedClientLabel = kmMediaPlanClientLabel(requestedClient);
  qs("status").textContent = `${requestedClientLabel} · Ozon: рассчитываем медиаплан...`;
  const root = qs("financeDashboard");
  root.innerHTML = '<div class="km-sales-plan-loading">Декомпозируем план продаж по воронке…</div>';
  const params = new URLSearchParams({ client: requestedClient, marketplace: "ozon" });
  const payload = await getJson(`/api/km-trade/media-plan?${params.toString()}`);
  if (state.dashboard !== "mediaPlan" || currentClient() !== requestedClient) return;
  renderKmMediaPlanPayload(payload);
  qs("status").textContent = `${requestedClientLabel} · Ozon: медиаплан рассчитан по факту до ${payload.period?.fact_to || "—"}`;
}

async function saveKmMediaPlan(reset = false) {
  const body = reset ? { reset: true } : {
    references: Object.fromEntries([...document.querySelectorAll("[data-media-reference]")].map((input) => [input.dataset.mediaReference, input.value])),
    coefficients: [...document.querySelectorAll("[data-media-coefficient]")].map((input) => ({ month_start: input.dataset.mediaCoefficient, coefficient: input.value })),
  };
  body.client = currentClient();
  const payload = await postJson("/api/km-trade/media-plan", body);
  renderKmMediaPlanPayload(payload);
  qs("status").textContent = reset ? "Референсы возвращены к сглаженному факту; коэффициенты — к 1,00" : "Медиаплан сохранён и пересчитан";
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-media-plan-action]");
  if (!button) return;
  button.disabled = true;
  saveKmMediaPlan(button.dataset.mediaPlanAction === "reset")
    .catch((error) => { qs("status").textContent = `Ошибка медиаплана: ${error.message}`; })
    .finally(() => { if (button.isConnected) button.disabled = false; });
});
