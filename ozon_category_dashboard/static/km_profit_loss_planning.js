// Planned P&L mode for KM Trade. Wraps the factual report without changing its contract.
(function initKmPlannedProfitLoss() {
  const actualRenderer = window.renderProfitLossDashboard;
  if (typeof actualRenderer !== "function") return;

  let mode = "actual";
  let lastPayload = null;

  function asNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function rub(value) {
    const parsed = asNumber(value);
    return parsed === null ? "—" : `${formatNumber(parsed, 0)} ₽`;
  }

  function pct(value) {
    const parsed = asNumber(value);
    return parsed === null ? "—" : `${formatNumber(parsed, 1)}%`;
  }

  function month(value) {
    const raw = String(value || "").slice(0, 7);
    const date = new Date(`${raw}-01T00:00:00`);
    return Number.isNaN(date.getTime())
      ? raw
      : date.toLocaleDateString("ru-RU", { month: "short", year: "numeric" });
  }

  function tone(value) {
    const parsed = asNumber(value);
    return parsed !== null && parsed < 0 ? "negative" : "";
  }

  function tabs() {
    return `
      <div class="pl3-mode-tabs" role="tablist" aria-label="Режим отчёта P&L">
        <button type="button" role="tab" data-pl-mode="actual" aria-selected="${mode === "actual"}" class="${mode === "actual" ? "is-active" : ""}">Факт P&amp;L</button>
        <button type="button" role="tab" data-pl-mode="plan" aria-selected="${mode === "plan"}" class="${mode === "plan" ? "is-active" : ""}">План до конца года</button>
      </div>
    `;
  }

  function kpi(label, value, formatter) {
    return `
      <article class="pl3-kpi">
        <span>${escapeHtml(label)}</span>
        <strong class="${tone(value)}">${formatter(value)}</strong>
      </article>
    `;
  }

  function planStatement(rows) {
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table">
          <thead><tr><th>Статья</th><th class="num">Сумма</th><th class="num">% плана</th></tr></thead>
          <tbody>
            ${(rows || []).map((row) => `
              <tr class="${escapeHtml(row.kind || "expense")}">
                <td>${escapeHtml(row.label || "")}</td>
                <td class="num ${tone(row.amount)}">${rub(row.amount)}</td>
                <td class="num ${tone(row.revenue_pct)}">${pct(row.revenue_pct)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function monthlyTrajectory(rows) {
    const source = rows || [];
    const maximum = Math.max(1, ...source.map((row) => asNumber(row.revenue) || 0));
    return `
      <div class="pl3-plan-trajectory" role="img" aria-label="План продаж и известный финансовый результат по месяцам">
        ${source.map((row) => {
          const width = Math.max(2, (asNumber(row.revenue) || 0) / maximum * 100);
          return `
            <div class="pl3-plan-trajectory-row">
              <span>${escapeHtml(month(row.month))}</span>
              <div><i style="width: ${width}%"></i></div>
              <strong>${rub(row.revenue)}</strong>
              <em class="${tone(row.known_profit)}">${rub(row.known_profit)}</em>
            </div>
          `;
        }).join("")}
        <div class="pl3-plan-trajectory-legend"><span><i></i>План продаж</span><span>Справа — известный финрез</span></div>
      </div>
    `;
  }

  function monthlyTable(rows) {
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-wide-table pl3-plan-month-table">
          <thead><tr><th>Месяц</th><th class="num">План продаж</th><th class="num">Шт.</th><th class="num">Покрытие</th><th class="num">Себестоимость</th><th class="num">Расходы Ozon</th><th class="num">Налоги</th><th class="num">Известный финрез</th><th class="num">Маржа модели</th><th class="num">Итоговая прибыль</th></tr></thead>
          <tbody>
            ${(rows || []).map((row) => `
              <tr>
                <td>${escapeHtml(month(row.month))}</td>
                <td class="num">${rub(row.revenue)}</td>
                <td class="num">${formatNumber(row.units || 0, 0)}</td>
                <td class="num">${pct(row.model_coverage_revenue_pct)}</td>
                <td class="num">${rub(row.cogs)}</td>
                <td class="num">${rub(row.ozon_costs)}</td>
                <td class="num">${rub(row.taxes)}</td>
                <td class="num emphasis ${tone(row.known_profit)}">${rub(row.known_profit)}</td>
                <td class="num ${tone(row.known_margin_pct)}">${pct(row.known_margin_pct)}</td>
                <td class="num emphasis ${tone(row.net_profit)}">${rub(row.net_profit)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function gapTable(rows) {
    const gaps = (rows || []).filter((row) => row.model_complete === false);
    if (!gaps.length) return '<div class="pl3-empty">Все товары плана полностью покрыты юнит-экономикой.</div>';
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-plan-gap-table">
          <thead><tr><th>Товар</th><th class="num">План продаж</th><th class="num">Покрытие</th><th>Что заполнить</th></tr></thead>
          <tbody>
            ${gaps.map((row) => `
              <tr>
                <td class="pl3-product"><strong>${escapeHtml(row.article || row.sku)}</strong><span>${escapeHtml(row.product_name || "")}</span><small>Ozon ${escapeHtml(row.sku)} · ${escapeHtml(row.category_name || "Без категории")}</small></td>
                <td class="num">${rub(row.revenue)}</td>
                <td class="num">${pct(row.model_coverage_pct)}</td>
                <td>${escapeHtml((row.missing_inputs || []).join(", ") || "неполная модель")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function readGoals(plan) {
    const defaults = plan.goal_defaults || {};
    const root = qs("financeDashboard");
    const read = (key, fallback) => {
      const parsed = Number(root?.querySelector(`[data-pl-goal="${key}"]`)?.value);
      return Number.isFinite(parsed) ? Math.max(parsed, 0) : Number(fallback || 0);
    };
    return {
      targetProfit: read("target-profit", defaults.target_profit_rub),
      targetMargin: read("target-margin", defaults.target_margin_pct),
      fixedExpenses: read("fixed-expenses", defaults.fixed_expenses_rub),
    };
  }

  function calculateGoal(plan, goals) {
    const totals = plan.totals || {};
    const marginPct = asNumber(totals.known_margin_pct);
    const baselineRevenue = Math.max(asNumber(totals.revenue) || 0, 0);
    const baselineUnits = Math.max(asNumber(totals.units) || 0, 0);
    if (marginPct === null) return { status: "unavailable" };
    const rate = marginPct / 100;
    const targetRate = goals.targetMargin / 100;
    if (rate <= 0) return { status: "negative", marginPct };
    if (targetRate > rate || (targetRate === rate && goals.fixedExpenses > 0)) {
      return { status: "margin", marginPct };
    }
    const forProfit = (goals.targetProfit + goals.fixedExpenses) / rate;
    const forMargin = goals.fixedExpenses > 0 && targetRate > 0
      ? goals.fixedExpenses / (rate - targetRate)
      : 0;
    const requiredRevenue = Math.max(forProfit, forMargin, 0);
    const scale = baselineRevenue > 0 ? requiredRevenue / baselineRevenue : null;
    return {
      status: "ok",
      marginPct,
      baselineRevenue,
      requiredRevenue,
      requiredUnits: scale === null ? null : baselineUnits * scale,
      delta: requiredRevenue - baselineRevenue,
      scale,
    };
  }

  function goalResult(plan) {
    const goals = readGoals(plan);
    const result = calculateGoal(plan, goals);
    const totals = plan.totals || {};
    const coverageNote = `Расчёт основан на ${pct(totals.model_coverage_revenue_pct)} плановой выручки, покрытой юнит-моделью.`;
    if (result.status === "unavailable") {
      return '<div class="pl3-goal-status is-blocked"><strong>Недостаточно данных для расчёта.</strong><span>Заполните юнит-экономику товаров плана.</span></div>';
    }
    if (result.status === "negative") {
      const perMillion = result.marginPct / 100 * 1_000_000;
      return `
        <div class="pl3-goal-status is-blocked">
          <strong>Цель недостижима ростом продаж при текущей юнит-экономике.</strong>
          <span>Маржа модели ${pct(result.marginPct)}: каждый дополнительный 1 млн ₽ продаж даёт около ${rub(perMillion)} финреза. Сначала измените цену, ДРР, комиссию, логистику, себестоимость или SKU-микс.</span>
          <small>${escapeHtml(coverageNote)}</small>
        </div>
      `;
    }
    if (result.status === "margin") {
      return `
        <div class="pl3-goal-status is-blocked">
          <strong>Целевая маржа выше доступной маржи модели.</strong>
          <span>Маржа модели ${pct(result.marginPct)}, цель ${pct(goals.targetMargin)}. Масштаб продаж не меняет переменную маржу — нужна другая юнит-экономика или SKU-микс.</span>
          <small>${escapeHtml(coverageNote)}</small>
        </div>
      `;
    }
    const meetsGoal = result.delta <= 0;
    return `
      <div class="pl3-goal-status">
        <strong>${meetsGoal ? "Текущий план уже выполняет заданные цели" : "Требуемый план продаж рассчитан"}</strong>
        <span>${escapeHtml(coverageNote)}</span>
      </div>
      <div class="pl3-goal-result-grid">
        ${kpi("Требуемые продажи", result.requiredRevenue, rub)}
        ${kpi("Требуемый объём", result.requiredUnits, (value) => value === null ? "—" : `${formatNumber(value, 0)} шт.`)}
        ${kpi("Изменение к плану", result.delta, rub)}
        ${kpi("Масштаб плана", result.scale, (value) => value === null ? "—" : `${formatNumber(value, 2)}×`)}
      </div>
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-goal-month-table">
          <thead><tr><th>Месяц</th><th class="num">Текущий план</th><th class="num">Требуемый план</th><th class="num">Изменение</th></tr></thead>
          <tbody>
            ${(plan.months || []).map((row) => {
              const share = result.baselineRevenue > 0 ? (asNumber(row.revenue) || 0) / result.baselineRevenue : 0;
              const required = result.requiredRevenue * share;
              const delta = required - (asNumber(row.revenue) || 0);
              return `<tr><td>${escapeHtml(month(row.month))}</td><td class="num">${rub(row.revenue)}</td><td class="num">${rub(required)}</td><td class="num ${tone(delta)}">${rub(delta)}</td></tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function goalCard(plan) {
    const defaults = plan.goal_defaults || {};
    return `
      <section class="pl3-card pl3-goal-card">
        <header class="pl3-title"><div><h3>Какой план нужен для цели</h3><p>Обратный расчёт сохраняет текущий SKU-микс и юнит-экономику</p></div></header>
        <div class="pl3-goal-controls">
          <label><span>Целевая чистая прибыль до конца года, ₽</span><input type="number" min="0" step="10000" data-pl-goal="target-profit" value="${escapeHtml(defaults.target_profit_rub ?? 0)}"></label>
          <label><span>Целевая чистая маржа, %</span><input type="number" min="0" step="0.1" data-pl-goal="target-margin" value="${escapeHtml(defaults.target_margin_pct ?? 0)}"></label>
          <label><span>Доп. постоянные расходы до конца года, ₽</span><input type="number" min="0" step="10000" data-pl-goal="fixed-expenses" value="${escapeHtml(defaults.fixed_expenses_rub ?? 0)}"></label>
        </div>
        <div data-pl-goal-result>${goalResult(plan)}</div>
      </section>
    `;
  }

  function renderPlan(payload) {
    const plan = payload.plan;
    const totals = plan.totals || {};
    const warning = totals.model_complete
      ? ""
      : `<div class="pl3-warning">Юнит-модель покрывает ${pct(totals.model_coverage_revenue_pct)} плановой выручки. Известный финрез рассчитан только на покрытой части; итоговая прибыль скрыта до 100% покрытия.</div>`;
    const root = qs("financeDashboard");
    root.innerHTML = `
      <div class="pl3" data-km-trade-pl data-pl-layout="konstex-parity">
        ${tabs()}
        ${warning}
        <section class="pl3-kpi-grid pl3-plan-kpis">
          ${kpi("План продаж", totals.revenue, rub)}
          ${kpi("План, шт.", totals.units, (value) => value === null ? "—" : `${formatNumber(value, 0)} шт.`)}
          ${kpi("Известный финрез", totals.known_profit, rub)}
          ${kpi("Маржа модели", totals.known_margin_pct, pct)}
          ${kpi("ROI модели", totals.roi_pct, pct)}
          ${kpi("Покрытие модели", totals.model_coverage_revenue_pct, pct)}
        </section>
        <section class="pl3-report-card" data-pl-mode-panel="plan">
          <header class="pl3-report-title"><div><h2>Плановый P&amp;L до конца года</h2><p>${escapeHtml(month(plan.period?.date_from))} — ${escapeHtml(month(plan.period?.date_to))} · ${formatNumber(totals.plan_sku_count || 0, 0)} SKU</p></div></header>
          <div class="pl3-grid">
            <section class="pl3-card"><header class="pl3-title"><div><h3>Структура планового P&amp;L</h3><p>Продажи из плана, расходы рассчитаны bottom-up по SKU</p></div></header>${planStatement(plan.statement || [])}</section>
            <section class="pl3-card"><header class="pl3-title"><div><h3>Траектория до конца года</h3><p>План продаж и известный финансовый результат</p></div></header>${monthlyTrajectory(plan.months || [])}</section>
          </div>
          ${goalCard(plan)}
          <section class="pl3-card"><header class="pl3-title"><div><h3>Эффективность по месяцам</h3><p>Продажи, покрытие юнит-моделью и финансовый результат</p></div></header>${monthlyTable(plan.months || [])}</section>
          <section class="pl3-card"><header class="pl3-title"><div><h3>Пробелы юнит-экономики</h3><p>Товары, из-за которых итоговая прибыль пока недоступна</p></div></header>${gapTable(plan.products || [])}</section>
          ${financeMethodology(plan.methodology)}
        </section>
      </div>
    `;
  }

  function render(payload) {
    lastPayload = payload;
    if (mode === "plan" && payload?.plan) {
      renderPlan(payload);
      return;
    }
    mode = "actual";
    actualRenderer(payload);
    qs("financeDashboard")?.querySelector(".pl3")?.insertAdjacentHTML("afterbegin", tabs());
  }

  window.renderProfitLossDashboard = render;

  qs("financeDashboard")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-pl-mode]");
    if (!button) return;
    const next = button.dataset.plMode;
    if (next === "plan" && !lastPayload?.plan) {
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = "Загрузка плана...";
      try {
        const params = new URLSearchParams({
          client: currentClient(),
          marketplace: currentMarketplace(),
          date_from: String(lastPayload?.date_from || qs("date_from")?.value || ""),
          date_to: String(lastPayload?.date_to || qs("date_to")?.value || ""),
          include_plan: "1",
        });
        const payload = await getFinanceJsonWithRetry(`/api/km-trade/pl?${params.toString()}`);
        lastPayload = { ...lastPayload, plan: payload.plan };
      } catch (error) {
        setStatusError(error);
        return;
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
    mode = next === "plan" ? "plan" : "actual";
    render(lastPayload);
  });

  qs("financeDashboard")?.addEventListener("input", (event) => {
    if (!event.target.closest("[data-pl-goal]") || !lastPayload?.plan) return;
    const holder = qs("financeDashboard")?.querySelector("[data-pl-goal-result]");
    if (holder) holder.innerHTML = goalResult(lastPayload.plan);
  });
})();
