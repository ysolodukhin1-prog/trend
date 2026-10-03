// Shared P&L visual contract for KM Trade. Mirrors the Konstex report structure.
(function initKmProfitLossParity() {
  function amount(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function money(value) {
    const number = amount(value);
    return number === null ? "—" : `${formatNumber(number, 0)} ₽`;
  }

  function percent(value) {
    const number = amount(value);
    return number === null ? "—" : `${formatNumber(number, 1)}%`;
  }

  function tone(value) {
    const number = amount(value);
    return number !== null && number < 0 ? "negative" : "";
  }

  function monthLabel(value) {
    const raw = String(value || "").slice(0, 7);
    const date = new Date(`${raw}-01T00:00:00`);
    return Number.isNaN(date.getTime())
      ? raw
      : date.toLocaleDateString("ru-RU", { month: "short", year: "numeric" });
  }

  function compactMoney(value) {
    const number = amount(value);
    if (number === null) return "—";
    const absolute = Math.abs(number);
    if (absolute >= 1_000_000) return `${formatNumber(number / 1_000_000, 1)} млн`;
    if (absolute >= 1_000) return `${formatNumber(number / 1_000, 0)} тыс`;
    return money(number);
  }

  function statementChart(rows) {
    const chartRows = (rows || []).filter(
      (row) => !["gross_profit", "profit_before_tax"].includes(row.key),
    );
    const width = 840;
    const labelWidth = 285;
    const plotWidth = 470;
    const rowHeight = 34;
    const height = Math.max(330, chartRows.length * rowHeight + 30);
    const max = Math.max(
      1,
      ...chartRows.map((row) => Math.abs(amount(row.amount) || 0)),
    );
    const zero = labelWidth + plotWidth / 2;
    const scale = plotWidth / 2 / max;
    return `
      <svg class="pl3-chart pl3-statement-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Структура P&L">
        <line x1="${zero}" y1="8" x2="${zero}" y2="${height - 14}" class="pl3-axis" />
        ${chartRows.map((row, index) => {
          const value = amount(row.amount);
          const numeric = value || 0;
          const barWidth = value === null ? 0 : Math.max(1, Math.abs(numeric) * scale);
          const x = numeric >= 0 ? zero : zero - barWidth;
          const y = 10 + index * rowHeight;
          const fill = numeric < 0 ? "#f04438" : "#12b76a";
          const labelX = numeric >= 0 ? x + barWidth + 7 : x - 7;
          return `
            <g data-pl-chart-row="${escapeHtml(row.key)}">
              <text x="4" y="${y + 17}" class="pl3-axis-label">${escapeHtml(row.label)}</text>
              ${value === null ? "" : `<rect x="${x}" y="${y + 2}" width="${barWidth}" height="21" rx="5" fill="${fill}" opacity=".86" />`}
              <text x="${labelX}" y="${y + 18}" text-anchor="${numeric >= 0 ? "start" : "end"}" class="pl3-chart-value ${tone(value)}">${escapeHtml(money(value))}</text>
            </g>
          `;
        }).join("")}
      </svg>
    `;
  }

  function monthlyChart(rows) {
    const source = rows || [];
    if (!source.length) return '<div class="pl3-empty">Нет данных за период</div>';
    const width = 840;
    const height = 360;
    const left = 70;
    const right = 70;
    const top = 30;
    const bottom = 76;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const revenueMax = Math.max(1, ...source.map((row) => amount(row.revenue) || 0));
    const profits = source.map((row) => amount(row.net_profit)).filter((value) => value !== null);
    const profitMin = Math.min(0, ...profits);
    const profitMax = Math.max(0, ...profits);
    const profitSpan = Math.max(1, profitMax - profitMin);
    const step = plotWidth / Math.max(source.length, 1);
    const x = (index) => left + step * index + step / 2;
    const revenueY = (value) => top + plotHeight - ((amount(value) || 0) / revenueMax) * plotHeight;
    const profitY = (value) => top + ((profitMax - (amount(value) || 0)) / profitSpan) * plotHeight;
    const points = source
      .map((row, index) => amount(row.net_profit) === null ? null : `${x(index)},${profitY(row.net_profit)}`)
      .filter(Boolean)
      .join(" ");
    const barWidth = Math.min(54, step * .54);
    const zeroY = profitY(0);
    return `
      <svg class="pl3-chart pl3-monthly-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="P&L по месяцам">
        ${[0, .5, 1].map((ratio) => {
          const gridY = top + ratio * plotHeight;
          return `<line x1="${left}" y1="${gridY}" x2="${width - right}" y2="${gridY}" class="pl3-grid-line" />
            <text x="${left - 8}" y="${gridY + 4}" text-anchor="end" class="pl3-axis-label">${escapeHtml(compactMoney(revenueMax * (1 - ratio)))}</text>`;
        }).join("")}
        <line x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}" class="pl3-axis" />
        <line x1="${left}" y1="${zeroY}" x2="${width - right}" y2="${zeroY}" class="pl3-profit-zero" />
        ${source.map((row, index) => {
          const currentX = x(index);
          const currentY = revenueY(row.revenue);
          return `<rect x="${currentX - barWidth / 2}" y="${currentY}" width="${barWidth}" height="${top + plotHeight - currentY}" rx="6" fill="#d0d5dd" />
            <text x="${currentX}" y="${top + plotHeight + 24}" text-anchor="middle" class="pl3-axis-label">${escapeHtml(monthLabel(row.month))}</text>`;
        }).join("")}
        ${points.split(" ").length > 1 ? `<polyline points="${points}" fill="none" stroke="#ff4d58" stroke-width="4" stroke-linejoin="round" stroke-linecap="round" />` : ""}
        ${source.map((row, index) => {
          const value = amount(row.net_profit);
          if (value === null) return "";
          const currentX = x(index);
          const currentY = profitY(value);
          return `<circle cx="${currentX}" cy="${currentY}" r="5" fill="${value < 0 ? "#f04438" : "#111827"}" />
            <text x="${currentX}" y="${currentY - 11}" text-anchor="middle" class="pl3-chart-value ${tone(value)}">${escapeHtml(compactMoney(value))}</text>`;
        }).join("")}
        <g transform="translate(${left} ${height - 23})">
          <rect width="14" height="10" rx="2" fill="#d0d5dd" />
          <text x="21" y="9" class="pl3-axis-label">Выручка</text>
          <line x1="112" y1="5" x2="140" y2="5" stroke="#ff4d58" stroke-width="4" />
          <text x="148" y="9" class="pl3-axis-label">Результат модели</text>
        </g>
      </svg>
    `;
  }

  function expenseEditor(payload) {
    const source = payload.expense_items?.length ? payload.expense_items : (payload.expenses || []).map(r=>({label:r.expense_name,amount_rub:r.amount,comment:r.notes,is_active:true}));
    const rows = [
      ...source,
      {
        is_active: true,
        expense_month: String(payload.date_from || "").slice(0, 7),
        category: "Операционные расходы",
        label: "",
        amount_rub: "",
        comment: "",
      },
    ];
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-expense-table">
          <thead><tr><th>Активно</th><th>Статья</th><th class="num">Сумма</th><th>Комментарий</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr class="finance-expense-row" data-pl-expense-row>
                <td><input type="checkbox" ${row.is_active === false ? "" : "checked"} /></td>
                <td><input data-expense-field="expense_name" value="${escapeHtml(row.label || "")}" placeholder="Новая статья" /></td>
                <td class="num"><input data-expense-field="amount" type="number" step="0.01" value="${escapeHtml(row.amount_rub ?? "")}" /></td>
                <td><input data-expense-field="notes" value="${escapeHtml(row.comment || "")}" /></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      <button type="button" class="pl3-primary" data-finance-action="save-expenses">Сохранить статьи затрат</button>
    `;
  }

  function statementTable(rows) {
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table">
          <thead><tr><th>Статья</th><th class="num">Сумма</th><th class="num">% выручки</th></tr></thead>
          <tbody>
            ${(rows || []).map((row) => `
              <tr class="${escapeHtml(row.kind || "expense")}">
                <td>${escapeHtml(row.label)}</td>
                <td class="num ${tone(row.amount)}">${money(row.amount)}</td>
                <td class="num ${tone(row.revenue_pct)}">${percent(row.revenue_pct)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function monthlyTable(rows, marketplace) {
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-wide-table">
          <thead><tr><th>Месяц</th><th class="num">Выручка</th><th class="num">Себестоимость</th><th class="num">Валовая прибыль</th><th class="num">Расходы ${escapeHtml(marketplace)}</th><th class="num">Расходы продавца</th><th class="num">Доп. затраты</th><th class="num">Налоги</th><th class="num">Результат модели</th><th class="num">Маржа</th></tr></thead>
          <tbody>
            ${(rows || []).map((row) => `
              <tr>
                <td>${escapeHtml(monthLabel(row.month))}</td>
                <td class="num">${money(row.revenue)}</td>
                <td class="num">${row.cogs_complete === false ? "—" : money(row.cogs)}</td>
                <td class="num ${tone(row.gross_profit)}">${money(row.gross_profit)}</td>
                <td class="num">${money(row.ozon_costs)}</td>
                <td class="num">${money(row.seller_costs)}</td>
                <td class="num">${money(row.manual_expenses || 0)}</td>
                <td class="num">${money(row.taxes)}</td>
                <td class="num emphasis ${tone(row.net_profit)}">${money(row.net_profit)}</td>
                <td class="num ${tone(row.margin_pct)}">${percent(row.margin_pct)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function productTable(rows, marketplace) {
    return `
      <div class="pl3-table-wrap">
        <table class="pl3-table pl3-product-table">
          <thead><tr><th>Товар</th><th class="num">Продано</th><th class="num">Выручка</th><th class="num">Себестоимость</th><th class="num">Валовая прибыль</th><th class="num">Комиссия</th><th class="num">Логистика</th><th class="num">Хранение</th><th class="num">Реклама</th><th class="num">Прочие</th><th class="num">Налоги</th><th class="num">Результат модели</th><th class="num">Маржа</th></tr></thead>
          <tbody>
            ${(rows || []).map((row) => {
              const other = (amount(row.other_ozon) || 0) + (amount(row.seller_costs) || 0);
              const taxes = amount(row.income_tax) === null ? null : Number(row.income_tax) + (amount(row.vat) || 0);
              return `
                <tr>
                  <td class="pl3-product"><strong>${escapeHtml(row.name || row.article || row.sku)}</strong><span>${escapeHtml(row.full_name || row.product_name || "")}</span><small>${escapeHtml(marketplace)} ${escapeHtml(row.sku)} · ${escapeHtml(row.category || row.category_name || "Без категории")}${row.cogs_known ? "" : " · себестоимость: цена / 3"}</small></td>
                  <td class="num">${formatNumber(row.units || 0, 0)}</td>
                  <td class="num">${money(row.seller_revenue ?? row.revenue)}</td>
                  <td class="num muted">${money(row.cogs)}</td>
                  <td class="num ${tone(row.gross_profit)}">${money(row.gross_profit)}</td>
                  <td class="num">${money(row.commission)}</td>
                  <td class="num">${money(row.delivery ?? row.logistics)}</td>
                  <td class="num">${money(row.storage)}</td>
                  <td class="num">${money(row.advertising)}</td>
                  <td class="num">${money(other)}</td>
                  <td class="num">${money(taxes)}</td>
                  <td class="num emphasis ${tone(row.net_profit)}">${money(row.net_profit)}</td>
                  <td class="num ${tone(row.margin_pct)}">${percent(row.margin_pct)}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function kpi(label, value, isPercent = false) {
    return `
      <article class="pl3-kpi" data-pl-kpi="${escapeHtml(label)}">
        <span>${escapeHtml(label)}</span>
        <strong class="${tone(value)}">${isPercent ? percent(value) : money(value)}</strong>
      </article>
    `;
  }

  window.renderProfitLossDashboard = function renderProfitLossDashboard(payload) {
    const root = qs("financeDashboard");
    const marketplace = payload.marketplace === "wb" ? "WB" : "Ozon";
    if (payload.available === false) {
      root.innerHTML = `
        <div class="pl3" data-km-trade-pl data-pl-layout="konstex-parity">
          <section class="pl3-report-card">
            <header class="pl3-report-title"><div><h2>Отчёт о прибылях и убытках</h2><p>${escapeHtml(marketplace)} · ${escapeHtml(financeFormatRuDate(payload.date_from))} — ${escapeHtml(financeFormatRuDate(payload.date_to))}</p></div></header>
            <div class="pl3-toolbar-card">${financeDateToolbar(payload)}</div>
            <section class="pl3-card pl3-empty"><h3>P&amp;L ${escapeHtml(marketplace)} пока не сформирован</h3><p>${escapeHtml(payload.message || "Финансовые данные для выбранной площадки пока недоступны.")}</p></section>
            ${financeMethodology(payload.methodology)}
          </section>
        </div>
      `;
      return;
    }
    const totals = payload.totals || {};
    if (payload.model_notice) {
      payload = {...payload, products:(payload.products||[]).map(r=>({...r,net_profit:r.management_result})),
        months:(payload.monthly||[]).map(r=>({...r,net_profit:r.management_result}))};
    }
    const taxWarning = totals.tax_configured
      ? ""
      : '<div class="pl3-warning">Налог не задан. Результат модели показан до налога с дохода.</div>';
    const cogsWarning = Number(totals.cogs_coverage_units_pct || 0) >= 100
      ? ""
      : `<div class="pl3-warning">Покрытие себестоимостью ${formatNumber(Number(totals.cogs_coverage_units_pct || 0), 2)}%. Незаполненная себестоимость рассчитана как цена / 3; оценка не записывается в фактическую стоимость.</div>`;
    const period = `${financeFormatRuDate(payload.date_from)} — ${financeFormatRuDate(payload.date_to)}`;
    root.innerHTML = `
      <div class="pl3" data-km-trade-pl data-pl-layout="konstex-parity">
        ${taxWarning}${cogsWarning}
        <section class="pl3-kpi-grid">
          ${kpi("Выручка продавца", totals.seller_revenue ?? totals.revenue)}
          ${kpi("Валовая прибыль", totals.gross_profit)}
          ${kpi(`Расходы ${marketplace}`, totals.ozon_costs)}
          ${kpi(totals.tax_configured ? "Результат модели" : "Результат до налога", totals.management_result ?? totals.net_profit)}
          ${kpi("Маржа модели", totals.management_margin_pct ?? totals.margin_pct, true)}
        </section>

        <section class="pl3-report-card">
          <header class="pl3-report-title">
            <div><h2>Отчёт о прибылях и убытках</h2><p>${escapeHtml(period)}</p></div>
          </header>
          <div class="pl3-toolbar-card">
            ${financeDateToolbar(payload)}
            <button type="button" class="pl3-primary pl3-export" data-km-pl-export>Экспорт Excel</button>
          </div>

          <div class="pl3-grid">
            <section class="pl3-card" data-pl-statement-chart>
              <header class="pl3-title"><div><h3>Структура P&amp;L</h3><p>Положительные статьи и расходы за выбранный период</p></div></header>
              <div class="pl3-chart-wrap">${statementChart(payload.statement || [])}</div>
            </section>
            <section class="pl3-card" data-pl-monthly-chart>
              <header class="pl3-title"><div><h3>Динамика по месяцам</h3><p>Выручка и результат модели</p></div></header>
              <div class="pl3-chart-wrap">${monthlyChart(payload.months || payload.monthly || [])}</div>
            </section>
          </div>

          <section class="pl3-card" data-pl-expenses>
            <header class="pl3-title"><div><h3>Статьи затрат</h3><p>Редактируются расходы выбранного периода; пересечения распределены по календарным дням</p></div></header>
            ${expenseEditor(payload)}
          </section>

          <section class="pl3-card" data-pl-statement-table>
            <header class="pl3-title"><div><h3>P&amp;L</h3><p>Налоги рассчитаны по настройкам юнит-экономики: доход ${percent(payload.taxes?.income_tax_pct)}, НДС ${percent(payload.taxes?.vat_pct)}</p></div></header>
            ${statementTable(payload.statement || [])}
          </section>

          <section class="pl3-card" data-pl-monthly-table>
            <header class="pl3-title"><div><h3>По месяцам</h3><p>Сходимость с итоговым P&amp;L</p></div></header>
            ${monthlyTable(payload.months || payload.monthly || [], marketplace)}
          </section>

          <section class="pl3-card" data-pl-product-table>
            <header class="pl3-title"><div><h3>По товарам</h3><p>Расходы ${escapeHtml(marketplace)} с точной привязкой к SKU; общие начисления отражаются в итоговом P&amp;L отдельно</p></div></header>
            ${productTable(payload.products || [], marketplace)}
          </section>

          ${financeMethodology(payload.methodology)}
        </section>
      </div>
    `;
  };

  qs("financeDashboard")?.addEventListener("click", (event) => {
    const exportButton = event.target.closest("[data-km-pl-export]");
    if (!exportButton) return;
    const params = new URLSearchParams({
      date_from: qs("financeDateFrom")?.value || "",
      date_to: qs("financeDateTo")?.value || "",
      client: currentClient(),
      marketplace: currentMarketplace(),
    });
    const prefix = window.location.pathname.startsWith("/glory/") ? "/glory" : "";
    window.location.href = `${prefix}/api/km-trade/pl-export?${params.toString()}`;
  });
})();
