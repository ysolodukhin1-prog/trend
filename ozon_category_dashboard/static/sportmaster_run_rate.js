(function () {
  const baseRenderPlanFactKpis = renderPlanFactKpis;
  const baseRenderPlanFactCharts = renderPlanFactCharts;

  function inputNumber(value) {
    const numeric = Number(value || 0);
    return numeric ? String(numeric) : "";
  }

  function marketplaceName(value) {
    if (value === "ozon") return "Ozon";
    if (value === "wb") return "WB";
    return "Итого";
  }

  function monthName(value) {
    const parsed = parseIsoDate(String(value || "").slice(0, 10));
    return parsed
      ? parsed.toLocaleDateString("ru-RU", { month: "long", year: "numeric" })
      : "месяц не выбран";
  }

  function sportmasterPlanEditor(row) {
    return `
      <section class="sportmaster-plan-editor" data-sportmaster-plan-editor>
        <header>
          <div>
            <h2>Run-Rate Sportmaster · ${escapeHtml(marketplaceName(row.marketplace))}</h2>
            <p>${escapeHtml(monthName(row.plan_month))}. Значения сохраняются в Sportmaster-БД.</p>
          </div>
          <div class="sportmaster-plan-actions">
            <span id="sportmasterRunRatePlanStatus" class="sportmaster-plan-status"></span>
            <button id="sportmasterRunRatePlanSave" type="button">Сохранить</button>
          </div>
        </header>
        <table aria-label="Ручной план Run-Rate">
          <thead>
            <tr><th>Показатель</th><th>План</th><th>Единица</th></tr>
          </thead>
          <tbody>
            <tr>
              <th>План по заказам</th>
              <td><input id="sportmasterOrdersPlan" type="number" min="0" step="1" value="${escapeHtml(inputNumber(row.orders_plan_qty))}" /></td>
              <td>руб</td>
            </tr>
            <tr>
              <th>План по выручке</th>
              <td><input id="sportmasterRevenuePlan" type="number" min="0" step="1000" value="${escapeHtml(inputNumber(row.revenue_plan_rub))}" /></td>
              <td>руб</td>
            </tr>
            <tr>
              <th>План по расходам на продвижение</th>
              <td><input id="sportmasterPromotionPlan" type="number" min="0" step="1000" value="${escapeHtml(inputNumber(row.promotion_plan_rub))}" /></td>
              <td>руб</td>
            </tr>
          </tbody>
        </table>
      </section>
    `;
  }

  async function saveSportmasterRunRatePlan(row) {
    const button = qs("sportmasterRunRatePlanSave");
    const status = qs("sportmasterRunRatePlanStatus");
    if (!button || !row.plan_month) return;
    const value = (id) => {
      const numeric = Number(String(qs(id)?.value || "0").replace(",", "."));
      return Number.isFinite(numeric) && numeric >= 0 ? numeric : 0;
    };
    button.disabled = true;
    button.textContent = "Сохраняю...";
    status.textContent = "Запись в БД...";
    try {
      await postJson("/api/planfact-plan", {
        client: "sportmaster",
        marketplace: row.marketplace || qs("marketplace")?.value || "total",
        plan_month: row.plan_month,
        orders_plan_qty: value("sportmasterOrdersPlan"),
        revenue_plan_rub: value("sportmasterRevenuePlan"),
        promotion_plan_rub: value("sportmasterPromotionPlan"),
      });
      status.textContent = "План сохранён";
      await loadData();
    } catch (error) {
      status.textContent = `Ошибка: ${error.message || error}`;
    } finally {
      button.disabled = false;
      button.textContent = "Сохранить";
    }
  }

  function renderSportmasterRunRateKpis(row) {
    setPlanFactKpiGroups([
      {
        title: "Run-Rate",
        groups: [
          {
            title: "Заказы",
            items: [
              { label: "Факт MTD", value: formatNumber(row.orders_qty) },
              { label: "RR с начала месяца", value: formatNumber(row.orders_runrate_qty) },
              { label: "RR за последние 7 дней", value: formatNumber(row.orders_recent_runrate_qty) },
              ...(Number(row.orders_plan_qty || 0) > 0
                ? [{ label: "Прогноз выполнения", value: pctText(row.orders_recent_runrate_pct) }]
                : []),
            ],
          },
          {
            title: "Выручка",
            items: [
              { label: "Факт MTD", value: formatNumber(row.revenue_rub) },
              { label: "RR с начала месяца", value: formatNumber(row.sales_runrate_rub) },
              { label: "RR за последние 7 дней", value: formatNumber(row.sales_recent_runrate_rub) },
              ...(Number(row.revenue_plan_rub || 0) > 0
                ? [{ label: "Прогноз выполнения", value: pctText(row.sales_recent_runrate_pct) }]
                : []),
            ],
          },
          {
            title: "Продвижение",
            items: [
              { label: "Факт MTD", value: formatNumber(row.promotion_expense_rub) },
              { label: "RR с начала месяца", value: formatNumber(row.ad_spend_runrate_rub) },
              { label: "RR за последние 7 дней", value: formatNumber(row.ad_spend_recent_runrate_rub) },
              ...(Number(row.promotion_plan_rub || 0) > 0
                ? [{ label: "Прогноз выполнения", value: pctText(row.ad_spend_recent_runrate_pct) }]
                : []),
            ],
          },
        ],
      },
    ]);
    const container = document.querySelector(".kpis");
    container?.insertAdjacentHTML("afterbegin", sportmasterPlanEditor(row));
    qs("sportmasterRunRatePlanSave")?.addEventListener("click", () => saveSportmasterRunRatePlan(row));
  }

  function sportmasterRunRateCard(row, config) {
    const badges = [
      { value: row[config.factPct], x: 168, y: 104 },
      { value: row[config.runRatePct], x: 296, y: 86 },
      { value: row[config.recentPct], x: 414, y: 116 },
    ];
    return renderPlanFactMiniBars({
      title: config.title,
      bars: [
        { label: "План", value: row[config.plan], color: "#a855f7" },
        { label: "Факт MTD", value: row[config.fact], color: "#0f766e" },
        { label: "RR MTD", value: row[config.runRate], color: "#38bdf8" },
        { label: "RR 7 дней", value: row[config.recent], color: "#f59e0b" },
      ],
      badges: Number(row[config.plan] || 0) > 0 ? badges : [],
    });
  }

  function renderSportmasterDailyDynamics(dailyRows, group, title, subtitle) {
    const rows = group === "revenue"
      ? dailyRows.map((item) => ({ ...item, orders_rub: item.orders_qty }))
      : dailyRows;
    return `
      <section class="sportmaster-daily-dynamics" data-sportmaster-daily-dynamics="${group}">
        <header>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(subtitle)}</p>
        </header>
        ${renderPlanFactChart(rows, group)}
      </section>
    `;
  }

  function renderSourceFreshness(row) {
    const rows = Array.isArray(row.source_freshness) ? row.source_freshness : [];
    if (!rows.length) return "";
    const newest = rows.map((item) => String(item.date_to || "")).filter(Boolean).sort().at(-1) || "";
    return `
      <section class="sportmaster-source-freshness">
        <h3>Свежесть источников</h3>
        <p>Run-Rate использует только загруженные дни. Отстающие источники подсвечены.</p>
        <div class="sportmaster-source-grid">
          ${rows.map((item) => {
            const dateTo = String(item.date_to || "");
            const lagging = Boolean(newest && dateTo && dateTo < newest);
            return `
              <div class="sportmaster-source-item ${lagging ? "is-lagging" : ""}">
                <strong>${escapeHtml(item.label || "")}</strong>
                <span>${dateTo ? `по ${escapeHtml(formatShortDate(dateTo))}` : "нет данных за месяц"}</span>
              </div>
            `;
          }).join("")}
        </div>
      </section>
    `;
  }

  renderPlanFactKpis = function (row = {}) {
    if (row.report_kind === "sportmaster_run_rate") {
      renderSportmasterRunRateKpis(row);
      return;
    }
    baseRenderPlanFactKpis(row);
  };

  renderPlanFactCharts = function (dailyRows, monthlyRows, scorecardRows = []) {
    const row = scorecardRows[0] || {};
    if (row.report_kind !== "sportmaster_run_rate") {
      baseRenderPlanFactCharts(dailyRows, monthlyRows, scorecardRows);
      return;
    }
    lastPlanFactDailyRows = dailyRows;
    lastPlanFactMonthlyRows = monthlyRows;
    lastPlanFactScorecardRows = scorecardRows;
    qs("primaryChartTitle").textContent = "Заказы и выручка";
    qs("chartMetric").textContent = "факт MTD и два Run-Rate прогноза";
    qs("barChart").classList.remove("bar-chart");
    qs("barChart").innerHTML = `
      <div class="sportmaster-runrate-card-grid two">
        ${sportmasterRunRateCard(row, {
          title: "Заказы, шт",
          plan: "orders_plan_qty",
          fact: "orders_qty",
          runRate: "orders_runrate_qty",
          recent: "orders_recent_runrate_qty",
          factPct: "orders_plan_fact_pct",
          runRatePct: "orders_runrate_pct",
          recentPct: "orders_recent_runrate_pct",
        })}
        ${sportmasterRunRateCard(row, {
          title: "Выручка, руб",
          plan: "revenue_plan_rub",
          fact: "revenue_rub",
          runRate: "sales_runrate_rub",
          recent: "sales_recent_runrate_rub",
          factPct: "sales_plan_fact_pct",
          runRatePct: "sales_runrate_pct",
          recentPct: "sales_recent_runrate_pct",
        })}
      </div>
      ${renderSportmasterDailyDynamics(
        dailyRows,
        "revenue",
        "Динамика заказов и выручки по дням",
        "Фактические значения, накопительный итог и плановые траектории при заполненном плане",
      )}
    `;
    qs("secondaryChartTitle").textContent = "Расходы на продвижение";
    qs("secondaryChartMetric").textContent = "товарная + медийная реклама, факт MTD и прогнозы";
    qs("abcChart").classList.remove("bar-chart");
    qs("abcChart").innerHTML = `
      <div class="sportmaster-runrate-card-grid">
        ${sportmasterRunRateCard(row, {
          title: "Расходы на продвижение, руб",
          plan: "promotion_plan_rub",
          fact: "promotion_expense_rub",
          runRate: "ad_spend_runrate_rub",
          recent: "ad_spend_recent_runrate_rub",
          factPct: "ad_spend_plan_fact_pct",
          runRatePct: "ad_spend_runrate_pct",
          recentPct: "ad_spend_recent_runrate_pct",
        })}
      </div>
      ${renderSportmasterDailyDynamics(
        dailyRows,
        "expense",
        "Динамика расходов на продвижение по дням",
        "Товарная и медийная реклама, дневные расходы, накопительный итог и бюджет",
      )}
      ${renderSourceFreshness(row)}
    `;
    qs("abcChart").closest(".panel")?.classList.remove("hidden");
  };
})();
