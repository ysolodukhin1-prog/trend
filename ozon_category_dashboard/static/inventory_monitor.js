(function () {
  const STATUS = {
    oos: { label: "OOS", className: "is-oos" },
    shortage: { label: "Дефицит <14 дн.", className: "is-shortage" },
    normal: { label: "Норма 14–60 дн.", className: "is-normal" },
    excess: { label: "Излишек >60 дн.", className: "is-excess" },
    no_sales: { label: "Нет продаж", className: "is-no-sales" },
  };

  function html(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function number(value, digits = 0) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function signed(value) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
    const parsed = Number(value);
    return `${parsed > 0 ? "+" : ""}${number(parsed)}`;
  }

  function date(value) {
    if (!value) return "—";
    const parts = String(value).slice(0, 10).split("-");
    return parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : html(value);
  }

  window.renderInventoryStockMonitor = function renderInventoryStockMonitor(payload) {
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    const summary = payload?.summary || {};
    const coverage = payload?.coverage || {};
    const observedDates = Array.isArray(coverage.observed_dates) ? coverage.observed_dates.map(date).join(", ") : "нет";
    return `
      <section class="inventory-monitor" data-inventory-monitor>
        <div class="inventory-monitor-coverage ${Number(coverage.coverage_pct || 0) < 80 ? "is-warning" : "is-good"}">
          <strong>Покрытие истории: ${number(coverage.observed_days)} из ${number(coverage.expected_days)} календарных дней (${number(coverage.coverage_pct, 1)}%).</strong>
          <span>Снимки: ${html(observedDates || "нет")}.</span>
          <small>Дни без снимка остаются пустыми и не интерполируются. Контур мониторинга: активный каталог ∪ фактический API-снимок.</small>
        </div>
        <div class="inventory-monitor-head">
          <div>
            <h3>Решения по SKU</h3>
            <p>${number(summary.monitoring_sku_count ?? rows.length)} SKU в мониторинге · ${number(summary.snapshot_sku_count)} в текущем API-снимке · ${number(summary.catalog_only_sku_count)} только в каталоге · ${number(summary.stock_only_sku_count)} только в снимке.</p>
          </div>
          <p>Спрос: ${number(payload?.demand_window_days)} календарных дней по ${date(payload?.demand_through_date)}. Пороги: дефицит &lt;14 дней, излишек &gt;60 дней.</p>
        </div>
        <div class="inventory-monitor-table-wrap">
          <table class="inventory-monitor-table">
            <thead><tr>
              <th>Статус</th><th>Товар / SKU</th><th class="num">Текущий</th><th class="num">Предыдущий</th><th class="num">Δ</th>
              <th class="num">Спрос 30 дн.</th><th class="num">Покрытие</th><th class="num">Готовится / поставки</th><th class="num">Дефицит 14 дн.</th><th class="num">Излишек &gt;60 дн.</th><th>Решение</th>
            </tr></thead>
            <tbody>
              ${rows.length ? rows.map((row) => {
                const status = STATUS[row.status] || STATUS.normal;
                const deltaClass = Number(row.delta_qty || 0) < 0 ? "is-negative" : (Number(row.delta_qty || 0) > 0 ? "is-positive" : "");
                return `<tr>
                  <td><span class="inventory-status ${status.className}">${html(status.label)}</span></td>
                  <td class="product"><strong>${html(row.product_name || "Без названия")}</strong><small>Ozon ${html(row.sku)}${row.seller_article ? ` · ${html(row.seller_article)}` : ""}</small>${row.current_snapshot_present === false ? '<em>Нет строки в API-снимке; для риск-скоринга остаток принят равным 0.</em>' : ""}</td>
                  <td class="num strong">${number(row.current_qty)}</td><td class="num">${number(row.previous_qty)}</td><td class="num ${deltaClass}">${signed(row.delta_qty)}</td>
                  <td class="num">${number(row.demand_30d_units, 1)}</td><td class="num">${row.days_cover === null ? "нет спроса" : `${number(row.days_cover, 1)} дн.`}</td><td class="num" title="Готовится: ${number(row.stock_preparing_qty)}; заявки: ${number(row.in_supply_orders_qty)}; в пути: ${number(row.in_transit_supply_qty)}">${number(row.inbound_qty)}</td>
                  <td class="num is-negative">${Number(row.shortage_14d || 0) > 0 ? number(row.shortage_14d, 1) : "—"}</td><td class="num">${Number(row.excess_over_60d || 0) > 0 ? number(row.excess_over_60d, 1) : "—"}</td><td class="action">${html(row.recommended_action)}</td>
                </tr>`;
              }).join("") : '<tr><td colspan="11" class="empty">Нет данных по SKU</td></tr>'}
            </tbody>
          </table>
        </div>
      </section>
    `;
  };

  const style = document.createElement("style");
  style.textContent = `
    .inventory-monitor{margin-top:24px;border:1px solid #dbe4ee;border-radius:16px;background:#fff;overflow:hidden}
    .inventory-monitor-coverage{display:grid;gap:4px;padding:14px 16px;border-bottom:1px solid #dbe4ee;background:#effaf4;color:#14532d}
    .inventory-monitor-coverage.is-warning{background:#fff8e6;color:#92400e}.inventory-monitor-coverage span,.inventory-monitor-coverage small{font-size:12px;color:inherit;opacity:.85}
    .inventory-monitor-head{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;padding:16px}.inventory-monitor-head h3{margin:0 0 4px;font-size:18px}.inventory-monitor-head p{margin:0;color:#64748b;font-size:12px;max-width:680px}
    .inventory-monitor-table-wrap{overflow:auto;max-height:720px;border-top:1px solid #e2e8f0}.inventory-monitor-table{width:100%;min-width:1540px;border-collapse:collapse;font-size:12px}
    .inventory-monitor-table th{position:sticky;top:0;z-index:1;background:#f8fafc;color:#475569;text-align:left;padding:10px;border-bottom:1px solid #cbd5e1}.inventory-monitor-table td{padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:top}.inventory-monitor-table tr:hover td{background:#f8fafc}
    .inventory-monitor-table .num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.inventory-monitor-table .strong{font-weight:700}.inventory-monitor-table .product{min-width:290px}.inventory-monitor-table .product strong,.inventory-monitor-table .product small,.inventory-monitor-table .product em{display:block}.inventory-monitor-table .product small{margin-top:4px;color:#64748b}.inventory-monitor-table .product em{margin-top:5px;color:#be123c;font-style:normal;font-size:11px}.inventory-monitor-table .action{min-width:300px;line-height:1.4}
    .inventory-status{display:inline-flex;padding:4px 8px;border-radius:999px;border:1px solid currentColor;font-weight:700;white-space:nowrap}.inventory-status.is-oos{color:#be123c;background:#fff1f2}.inventory-status.is-shortage{color:#b45309;background:#fffbeb}.inventory-status.is-normal{color:#047857;background:#ecfdf5}.inventory-status.is-excess{color:#0369a1;background:#f0f9ff}.inventory-status.is-no-sales{color:#475569;background:#f8fafc}.inventory-monitor-table .is-negative{color:#be123c}.inventory-monitor-table .is-positive{color:#047857}.inventory-monitor-table .empty{text-align:center;padding:32px;color:#64748b}
    @media(max-width:900px){.inventory-monitor-head{display:grid}.inventory-monitor{border-radius:12px}}
  `;
  document.head.appendChild(style);
})();
