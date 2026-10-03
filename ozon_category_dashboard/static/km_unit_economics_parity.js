(function () {
  "use strict";

  let activeTab = "plan";
  let lastPayload = null;
  const scenarioDrafts = new Map();
  const collapsedModelGroups = new Set();

  const money = (value, digits = 0) => {
    if (value === null || value === undefined || value === "") return "не рассчитан";
    return `${formatNumber(value, digits)} ₽`;
  };

  const percent = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    return `${formatNumber(value, 1)}%`;
  };

  const inputValue = (value) => (
    value === null || value === undefined ? "" : escapeHtml(value)
  );

  const compactDate = (value) => {
    const parts = String(value || "").slice(0, 10).split("-");
    return parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : "";
  };

  const acquiringDisplay = (row) => {
    const snapshotDate = compactDate(row.snapshot_date);
    const planRate = percent(row.model_acquiring_pct);
    const actualRate = percent(row.actual_acquiring_pct);
    const title = row.model_acquiring_pct == null
      ? "Ставка отсутствует в последнем снимке Ozon Seller API"
      : `Ставка расчёта ${planRate} из Ozon Seller API${snapshotDate ? ` на ${snapshotDate}` : ""}; фактическая ставка за период: ${actualRate}`;
    return {
      title,
      content: `${planRate}<small>${snapshotDate ? `API ${snapshotDate}` : "нет снимка API"}</small>`,
    };
  };

  const metricTone = (value) => {
    if (value === null || value === undefined) return "is-missing";
    if (Number(value) < 0) return "is-negative";
    if (Number(value) > 0) return "is-positive";
    return "";
  };

  const dimensionMissingRowAttrs = (row, extraClass = "") => {
    const classes = [
      extraClass,
      row && row.dimension_missing_reason ? "kmue-row-missing-dimensions" : "",
    ].filter(Boolean).join(" ");
    const title = row && row.dimension_missing_reason
      ? ` title="${escapeHtml(row.dimension_missing_reason)}"`
      : "";
    return `${classes ? ` class="${classes}"` : ""}${title}`;
  };

  const productCell = (row) => {
    const dimensions = [row.depth, row.width, row.height].every((value) => value != null)
      ? `${formatNumber(row.depth, 0)}×${formatNumber(row.width, 0)}×${formatNumber(row.height, 0)} ${escapeHtml(row.dimension_unit || "")}`
      : "габариты не получены";
    const weight = row.weight == null
      ? ""
      : ` · ${formatNumber(row.weight, 0)} ${escapeHtml(row.weight_unit || "")}`;
    return `
      <td class="kmue-product kmue-sticky">
        <strong>${escapeHtml(row.article || row.sku || "Без артикула")}</strong>
        <span>${escapeHtml(row.product_name || "")}</span>
        <small>Ozon SKU ${escapeHtml(row.sku || "—")} · ${escapeHtml(String(row.delivery_schema || "FBO").toUpperCase())}</small>
        <small>${dimensions}${weight} · ${escapeHtml(row.dimension_source || "Ozon API")}</small>
      </td>
    `;
  };

  const modelProductCell = (row) => `
    <td
      class="kmue-product kmue-model-product kmue-sticky"
      title="${escapeHtml(`${row.article || row.sku || "Без артикула"} · ${row.product_name || ""} · Ozon SKU ${row.sku || "—"}`)}"
    >
      <strong>${escapeHtml(row.article || row.sku || "Без артикула")}</strong>
      <span>${escapeHtml(row.product_name || "")}</span>
      <small>SKU ${escapeHtml(row.sku || "—")}</small>
    </td>
  `;

  const numberInput = (row, field, options = {}) => {
    const {
      placeholder = "",
      step = "0.01",
      min = "0",
      max = "",
      className = "",
    } = options;
    return `<input
      class="kmue-cell-input ${className}"
      data-finance-field="${escapeHtml(field)}"
      type="number"
      step="${step}"
      min="${min}"
      ${max !== "" ? `max="${max}"` : ""}
      value="${inputValue(row[field])}"
      placeholder="${escapeHtml(placeholder)}"
    />`;
  };

  const moneyCell = (value, extra = "") => `
    <td class="num ${metricTone(value)}">${money(value, 2)}${extra}</td>
  `;

  const titledMoneyCell = (value, title = "") => `
    <td class="num ${metricTone(value)}" title="${escapeHtml(title)}">${money(value, 2)}</td>
  `;

  const completenessWarnings = (payload) => {
    const totals = payload.totals || {};
    const rows = payload.rows || [];
    const cogsCount = rows.filter((row) => row.cogs_status === "ok").length;
    const missingPlanSourceCount = rows.filter((row) => (
      row.commission_pct == null
      || row.model_acquiring_pct == null
      || row.advertising_pct == null
      || row.expected_reverse_logistics_per_unit == null
    )).length;
    const warnings = [];
    if (!totals.tax_configured) {
      warnings.push("Задайте налог с дохода: до этого прибыль, МРЦ и РРЦ намеренно не рассчитываются.");
    }
    if (cogsCount < rows.length) {
      warnings.push(`Себестоимость заполнена для ${cogsCount} из ${rows.length} SKU. Ozon Seller API не передаёт это поле — внесите себестоимость в модели или загрузите файл. Пустая себестоимость не считается нулевой.`);
    }
    if (missingPlanSourceCount > 0) {
      warnings.push(`Для ${missingPlanSourceCount} из ${rows.length} SKU не хватает планового тарифа или явной цели ДРР/возвратов. Фактические начисления намеренно не подставляются: МРЦ, РРЦ и ROI для таких строк остаются не рассчитаны.`);
    }
    return warnings.map((text) => `<div class="kmue-alert">${escapeHtml(text)}</div>`).join("");
  };

  const unitPagination = (payload) => {
    const pagination = payload.pagination || {};
    const page = Number(pagination.page || 1);
    const totalPages = Number(pagination.total_pages || 1);
    const total = Number(pagination.total || (payload.rows || []).length);
    if (totalPages <= 1) return "";
    return `
      <nav class="kmue-pagination" aria-label="Страницы товаров">
        <span>SKU ${formatNumber(total, 0)} · страница ${page} из ${totalPages}</span>
        <div>
          <button type="button" class="ghost" data-kmue-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Назад</button>
          <button type="button" class="ghost" data-kmue-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>Далее</button>
        </div>
      </nav>`;
  };

  const globalSettings = (payload) => {
    const values = payload.global_settings || {};
    const fields = [
      ["tax_pct", "Налог с дохода, %", "", "0.01", "0", "100"],
      ["vat_pct", "НДС, %", "0", "0.01", "0", "100"],
      ["acquiring_pct", "Эквайринг в расчёте, %", "Тариф Ozon", "0.01", "0", "100"],
      ["capital_days", "Срок капитала, дней", "30", "1", "0", "3650"],
      ["capital_rate_pct", "Стоимость денег, % год", "20", "0.01", "0", "100"],
      ["advertising_pct", "ДРР заказов, %", "Задайте цель", "0.01", "0", "100"],
      ["return_rate_pct", "Невыкуп / возвраты, %", "Задайте цель", "0.01", "0", "100"],
      ["mrc_margin_pct", "Цель по марже МРЦ, %", "0", "0.01", "0", "100"],
      ["rrc_margin_pct", "Цель по марже РРЦ, %", "20", "0.01", "0", "100"],
    ];
    return `
      <div class="kmue-settings-grid">
        ${fields.map(([key, label, placeholder, step, min, max]) => `
          <label>
            <span>${escapeHtml(label)}</span>
            <input
              data-finance-global="${key}"
              type="number"
              step="${step}"
              min="${min}"
              max="${max}"
              value="${inputValue(values[key])}"
              placeholder="${escapeHtml(placeholder)}"
            />
          </label>
        `).join("")}
      </div>
      <div class="kmue-settings-actions">
        <button type="button" class="ghost kmue-icon-button kmue-save-action" data-finance-action="save-unit" aria-label="Сохранить и пересчитать" title="Сохранить и пересчитать">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h11l3 3v15H5zM8 3v6h8V3M8 21v-7h8v7" /></svg>
        </button>
        <a class="ghost kmue-icon-button" href="/km_trade_cogs_template.xlsx" download aria-label="Скачать шаблон себестоимости" title="Скачать шаблон себестоимости">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 19h14" /></svg>
        </a>
        <button type="button" class="ghost kmue-icon-button" data-finance-action="import-cogs" aria-label="Загрузить себестоимость" title="Загрузить себестоимость">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21V10m0 0 4 4m-4-4-4 4M5 5h14" /></svg>
        </button>
        <input type="file" accept=".xlsx" data-finance-cogs-file hidden />
      </div>
      <p class="kmue-explainer">
        МРЦ и РРЦ рассчитываются автоматически на одну продажу под отдельные цели маржи. План использует только явные цели продавца
        и текущие тарифы Ozon API по SKU. Исключение — CrossDock и SupplyInbound: из-за отсутствия SKU в начислениях они
        нормализуются на одну продажу по всему доступному финансовому факту кабинета и входят в логистику. ДРР заказов
        пересчитывается на выкупленную единицу делением на долю выкупа. Остальные фактические начисления
        показаны отдельно для сверки и не подставляются в МРЦ, РРЦ или ROI.
      </p>
    `;
  };

  const numericOrNull = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const unitMethodCatalog = {
    ozon: [
      ["Себестоимость товара", "₽/шт", "Закупка или производство", "SKU", "Ручной ввод / импорт XLSX → ozon_unit_product_settings.cogs_per_unit"],
      ["Фулфилмент продавца", "₽/шт", "Сборка, упаковка, маркировка", "SKU", "Ручной ввод → ozon_unit_product_settings.fulfillment_per_unit"],
      ["Доставка до Ozon", "₽/шт", "Рейс поставки ÷ единицы или объём", "Поставка", "Ручной ввод → ozon_unit_product_settings.inbound_per_unit"],
      ["Кросс-докинг", "₽/продажу", "Весь факт CrossDock ÷ все продажи истории", "Кабинет → продажа", "Ozon Finance API → ozon_finance_lines (CrossDock), вся история"],
      ["Приёмка Ozon", "₽/продажу", "Весь факт SupplyInbound ÷ все продажи истории", "Кабинет → продажа", "Ozon Finance API → ozon_finance_lines (SupplyInbound), вся история"],
      ["Комиссия Ozon", "% цены", "Тариф категории и схемы", "SKU", "Ozon Seller API /v5/product/info/prices → sales_percent_fbo/fbs"],
      ["Эквайринг", "% цены", "Сумма эквайринга из текущего снимка ÷ цена", "SKU", "Ozon Seller API /v5/product/info/prices → acquiring"],
      ["Прямая логистика", "₽/продажу", "Текущий тариф Ozon × объём", "SKU", "Ozon Seller API /v5/product/info/prices + /v4/product/info/attributes"],
      ["Возвраты", "₽/продажу", "Тариф возврата × явно заданная доля", "SKU", "Ozon Seller API /v5/product/info/prices + цель возвратов SKU / кабинета"],
      ["Реклама", "% цены выкупа", "ДРР заказов ÷ доля выкупа", "SKU", "Ручная цель SKU / кабинета + невыкуп → target_advertising_pct / advertising_pct / return_rate_pct"],
      ["Налог и НДС", "% выручки", "Ставки продавца", "Продажа", "Настройки кабинета → ozon_unit_global_settings.tax_pct / vat_pct"],
      ["Стоимость капитала", "₽/шт", "(себес + подготовка) × ставка × дни / 365", "SKU", "Настройки кабинета → capital_rate_pct / capital_days"],
      ["Общие расходы", "₽/период", "Зарплаты, аренда, ПО и неподтверждённые счета", "Только P&L", "Ozon Finance API / управленческий учёт → P&L без распределения по SKU"],
    ],
    wb: [
      ["Себестоимость и упаковка", "₽/шт", "Закупка, производство, маркировка", "SKU", "Настройки SKU / ERP"],
      ["Доставка до WB", "₽/шт", "Рейс поставки ÷ единицы/объём", "Поставка", "Настройки поставки"],
      ["Приёмка WB", "₽/шт", "Тариф склада × объём × коэффициент", "Поставка", "WB Tariffs API / настройки поставки"],
      ["Хранение WB", "₽/шт", "Литро-дни × тариф и коэффициент склада", "SKU/дни", "WB Tariffs API + отчёт хранения"],
      ["Комиссия / КВВ", "% цены", "Категория, модель и скидки КВВ", "SKU", "WB Content / Tariffs API"],
      ["Логистика до покупателя", "₽/заказ", "Объём × тариф × коэффициенты", "SKU", "WB Tariffs API"],
      ["Отказ и возврат", "₽/заказ", "Доля невыкупа × обратная логистика", "SKU", "WB Tariffs API + целевой выкуп"],
      ["Реклама", "% выручки", "Факт кампаний или целевой ДРР", "SKU/кампания", "WB Promotion API / цель ДРР"],
      ["Штрафы и платные услуги", "по факту", "Точно на SKU; общий счёт — P&L", "SKU + P&L", "Финансовый отчёт WB"],
      ["Налог, НДС и капитал", "% / ₽", "Ставки продавца и срок оборота", "Продажа", "Настройки кабинета"],
      ["Общие расходы", "₽/период", "Команда, аренда, сервисы, управление", "Только P&L", "Управленческий учёт / P&L"],
    ],
  };

  const unitMethodCatalogTable = (rows) => `
    <div class="kmue-method-table-wrap">
      <table class="kmue-method-table kmue-method-catalog">
        <thead><tr><th>Статья</th><th>Формат</th><th>Как считается</th><th>Зерно</th><th>Источник данных</th></tr></thead>
        <tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;

  const unitMethodTable = (headers, rows) => `
    <div class="kmue-method-table-wrap">
      <table class="kmue-method-table">
        <thead><tr>${headers.map((value) => `<th>${escapeHtml(value)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;

  const methodSettingRows = (payload) => {
    const values = payload.global_settings || {};
    return [
      ["Налог с дохода", percent(values.tax_pct), "Настройка кабинета"],
      ["НДС", percent(values.vat_pct || 0), "Настройка кабинета"],
      ["ДРР заказов", percent(values.advertising_pct), "На выкуп: ДРР ÷ (1 − невыкуп)"],
      ["Невыкуп / возвраты", percent(values.return_rate_pct), "Поправка рекламы и ожидаемая обратная логистика"],
      ["Стоимость капитала", `${formatNumber(values.capital_rate_pct || 0, 1)}% × ${formatNumber(values.capital_days || 0, 0)} дн.`, "Настройка кабинета"],
      ["Маржа МРЦ", percent(values.mrc_margin_pct), "Нижняя граница репрайсера"],
      ["Маржа РРЦ", percent(values.rrc_margin_pct), "Основная целевая цена"],
    ];
  };

  const methodologyChecks = (payload) => {
    const totals = payload.totals || {};
    const values = payload.global_settings || {};
    const rows = payload.rows || [];
    const mrc = numericOrNull(values.mrc_margin_pct);
    const rrc = numericOrNull(values.rrc_margin_pct);
    const tariffCount = rows.filter((row) => numericOrNull(row.commission_pct) !== null).length;
    const cogsCount = rows.filter((row) => row.cogs_status === "ok").length;
    const checks = [
      [Boolean(totals.tax_configured), "Налог задан", "Без налога прибыль, МРЦ и РРЦ не рассчитываются"],
      [rows.length > 0 && cogsCount === rows.length, `Себестоимость: ${cogsCount}/${rows.length} SKU`, "Пустая себестоимость не считается нулевой"],
      [mrc !== null && rrc !== null && mrc <= rrc, `Цели маржи: МРЦ ${percent(mrc)} ≤ РРЦ ${percent(rrc)}`, "МРЦ не должна превышать РРЦ"],
      [rows.length > 0 && tariffCount === rows.length, `Комиссия Ozon: ${tariffCount}/${rows.length} SKU`, "Последний тарифный снимок по SKU и схеме"],
    ];
    return `<ul>${checks.map(([ok, label, note]) => `
      <li class="${ok ? "is-ok" : "is-warning"}">
        <strong>${ok ? "✓" : "!"} ${escapeHtml(label)}</strong>
        <span>${escapeHtml(note)}</span>
      </li>
    `).join("")}</ul>`;
  };

  const unitMethodologyDialog = (payload) => {
    return `
      <dialog class="kmue-method-dialog" data-kmue-method-dialog>
        <div class="kmue-method-shell">
          <header>
            <div>
              <span class="kmue-method-eyebrow">МЕТОДИКА РАСЧЁТА</span>
              <h2>Проверка расчёта KM Trade · Ozon</h2>
              <p>Формулы, действующие параметры, тарифы и правила распределения.</p>
            </div>
            <button type="button" class="kmue-method-close" data-kmue-action="close-methodology" aria-label="Закрыть">×</button>
          </header>
          <section class="kmue-method-formula">
            <strong>Прибыль/шт = Цена − Цена × (Комиссия + Эквайринг + Налог + НДС + ДРР заказов ÷ доля выкупа) / 100 − Постоянные расходы/шт.</strong>
            <p>Постоянные расходы/шт = себестоимость + расходы продавца + доставка Ozon + кросс-докинг + приёмка + обратная логистика + стоимость капитала.</p>
            <p>МРЦ/РРЦ = Постоянные расходы/шт ÷ [1 − (Переменная ставка + Целевая маржа МРЦ/РРЦ) / 100]. Стоимость капитала = (себестоимость + расходы продавца) × ставка × дни / 365.</p>
            <p>ROI МРЦ/РРЦ = прибыль на единицу ÷ полные расходы на единицу × 100%.</p>
            <p>CrossDock и SupplyInbound включаются как средние начисления на продажу за всю доступную историю. Фактические комиссия, эквайринг, логистика, реклама Finance, прочее Ozon и хранение нужны только для сверки/P&amp;L и не заменяют остальные плановые источники.</p>
          </section>
          <section class="kmue-method-checks">
            <div class="kmue-method-section-head"><div><h3>Контроль корректности</h3><p>Проверки, без которых итог нельзя считать надёжным.</p></div></div>
            ${methodologyChecks(payload)}
          </section>
          <section>
            <div class="kmue-method-section-head"><div><h3>Параметры текущего расчёта</h3><p>Значения применяются к расчёту одной продажи и к среднему факту выбранного периода.</p></div></div>
            ${unitMethodTable(["Параметр", "Значение", "Источник / назначение"], methodSettingRows(payload))}
          </section>
          <section>
            <div class="kmue-method-section-head"><div><h3>Состав расчёта на единицу Ozon</h3><p>Статья, формат, правило, зерно и конкретный источник данных.</p></div></div>
            ${unitMethodCatalogTable(unitMethodCatalog.ozon)}
          </section>
          <section class="kmue-method-decision">
            <h3>Правило распределения</h3>
            <ol>
              <li>Есть точный SKU в начислении — относим напрямую.</li>
              <li>Есть документ поставки/кампании — распределяем по причинному драйверу: единицы, объём, литро-дни или атрибуция рекламы.</li>
              <li>Драйвера нет или связь нестабильна — расход остаётся в P&L; в юнитку попадает только после подтверждения методики.</li>
            </ol>
          </section>
        </div>
      </dialog>
    `;
  };

  const pricePosition = (value, mrc, rrc) => {
    const price = numericOrNull(value);
    const floor = numericOrNull(mrc);
    const target = numericOrNull(rrc);
    if (price === null || floor === null || target === null) {
      return { label: "нет расчёта", tone: "missing" };
    }
    if (price < floor) return { label: "ниже МРЦ", tone: "danger" };
    if (price <= target) return { label: "в коридоре", tone: "good" };
    return { label: "выше РРЦ", tone: "high" };
  };

  const positionBadge = (value, mrc, rrc) => {
    const position = pricePosition(value, mrc, rrc);
    return `<span class="kmue-position kmue-position-${position.tone}">${escapeHtml(position.label)}</span>`;
  };

  const scenarioCostSum = (scenario, fields) => {
    const values = fields.map((field) => numericOrNull(scenario?.[field]));
    if (values.some((value) => value === null)) return null;
    return values.reduce((sum, value) => sum + value, 0);
  };

  const advertisingAdjustmentTitle = (row, scenario = {}) => {
    const drr = numericOrNull(row.advertising_pct);
    const nonBuyout = numericOrNull(row.return_rate_pct_effective);
    const effective = numericOrNull(scenario.advertising_pct_effective)
      ?? numericOrNull(row.advertising_pct_buyout_effective);
    if (drr === null || nonBuyout === null || effective === null || nonBuyout >= 100) {
      return row.advertising_adjustment_source || "Нужны ДРР и доля невыкупа менее 100%";
    }
    return `${formatNumber(drr, 2)}% ДРР ÷ ${formatNumber(100 - nonBuyout, 2)}% выкупа = ${formatNumber(effective, 2)}% цены выкупленной единицы`;
  };

  const scenarioRoi = (scenario) => {
    const profit = numericOrNull(scenario?.profit);
    const totalCosts = numericOrNull(scenario?.total_costs);
    return profit === null || !totalCosts ? null : profit / totalCosts * 100;
  };

  const sellerTotalPerUnit = (row) => {
    const values = [
      row.cogs_per_unit,
      row.fulfillment_per_unit,
      row.inbound_per_unit,
      row.other_per_unit,
      row.capital_cost_per_unit,
    ].map(numericOrNull);
    if (values.some((value) => value === null)) return null;
    return values.reduce((sum, value) => sum + value, 0);
  };

  const MODEL_SECTIONS = [
    {
      key: "common", title: "Общие данные", className: "kmue-section-common", groups: [
        { key: "inputs", className: "kmue-group-inputs", title: "1. Вводные", columns: 9 },
        { key: "tariffs", className: "kmue-group-tariffs", title: "2. Тарифы Ozon", columns: 2 },
        { key: "logistics", className: "kmue-group-logistics", title: "3. Логистика Ozon · ₽/шт", columns: 5 },
        { key: "seller", className: "kmue-group-seller", title: "4. Расходы продавца · ₽/шт", columns: 5 },
      ],
    },
    {
      key: "rrc", title: "Расчёт РРЦ", className: "kmue-section-rrc", groups: [
        { key: "goal-rrc", className: "kmue-group-goal-rrc", title: "1. Цель РРЦ", columns: 1 },
        { key: "marketplace-rrc", className: "kmue-group-marketplace", title: "2. Расходы Ozon РРЦ · ₽/шт", columns: 4 },
        { key: "tax-rrc", className: "kmue-group-tax", title: "3. Налоги РРЦ · ₽/шт", columns: 2 },
        { key: "price-rrc", className: "kmue-group-price", title: "4. Цена РРЦ", columns: 1 },
        { key: "roi-rrc", className: "kmue-group-roi", title: "5. Результат РРЦ", columns: 2 },
      ],
    },
    {
      key: "mrc", title: "Расчёт МРЦ", className: "kmue-section-mrc", groups: [
        { key: "goal-mrc", className: "kmue-group-goal-mrc", title: "1. Цель МРЦ", columns: 1 },
        { key: "marketplace-mrc", className: "kmue-group-marketplace", title: "2. Расходы Ozon МРЦ · ₽/шт", columns: 4 },
        { key: "tax-mrc", className: "kmue-group-tax", title: "3. Налоги МРЦ · ₽/шт", columns: 2 },
        { key: "price-mrc", className: "kmue-group-price", title: "4. Цена МРЦ", columns: 1 },
        { key: "roi-mrc", className: "kmue-group-roi", title: "5. Результат МРЦ", columns: 2 },
      ],
    },
  ];

  const MODEL_GROUPS = MODEL_SECTIONS.flatMap((section) => section.groups);

  const ACTUAL_GROUPS = [
    { key: "actual-sales", className: "kmue-group-inputs", title: "1. Продажи за период", columns: 3 },
    { key: "actual-ozon", className: "kmue-group-marketplace", title: "2. Расходы Ozon за период", columns: 21 },
    { key: "actual-seller", className: "kmue-group-seller", title: "3. Расходы продавца за период", columns: 9 },
    { key: "actual-tax", className: "kmue-group-tax", title: "4. Налоги и деньги за период", columns: 12 },
    { key: "actual-result", className: "kmue-group-roi", title: "5. Экономический результат", columns: 6 },
    { key: "actual-corridor", className: "kmue-group-price", title: "6. Ценовой коридор", columns: 3 },
  ];

  const SCENARIO_GROUPS = [
    { key: "scenario-inputs", className: "kmue-group-inputs", title: "1. Вводные сценария", columns: 6 },
    { key: "scenario-ozon", className: "kmue-group-marketplace", title: "2. Расходы Ozon · ₽/шт", columns: 6 },
    { key: "scenario-seller", className: "kmue-group-seller", title: "3. Расходы продавца · ₽/шт", columns: 3 },
    { key: "scenario-tax", className: "kmue-group-tax", title: "4. Налоги · ₽/шт", columns: 3 },
    { key: "scenario-result", className: "kmue-group-roi", title: "5. Результат сценария", columns: 6 },
    { key: "scenario-corridor", className: "kmue-group-price", title: "6. Ценовой коридор", columns: 3 },
  ];

  const modelGroupCellAttrs = (key, visibility = "expanded") => {
    const collapsed = collapsedModelGroups.has(key);
    const hidden = visibility === "collapsed" ? !collapsed : collapsed;
    return `data-kmue-group-cell="${key}" data-kmue-group-visibility="${visibility}"${hidden ? " hidden" : ""}`;
  };

  const modelGroupHeader = (group, tableId = "kmue-model-table") => {
    const collapsed = collapsedModelGroups.has(group.key);
    const label = collapsed
      ? `Развернуть блок «${group.title}»`
      : `Свернуть блок «${group.title}»`;
    return `
      <th
        class="kmue-group-head ${group.className} kmue-block-start"
        colspan="${collapsed ? 1 : group.columns}"
        scope="colgroup"
        data-kmue-group-head="${group.key}"
        data-kmue-group-label="${escapeHtml(group.title)}"
        data-kmue-expanded-colspan="${group.columns}"
        data-kmue-collapsed="${collapsed}"
      >
        <span class="kmue-group-head-inner">
          <span>${escapeHtml(group.title)}</span>
          <button
            type="button"
            class="kmue-group-toggle ${collapsed ? "is-collapsed" : ""}"
            data-kmue-action="toggle-model-group"
            data-kmue-group-key="${group.key}"
            aria-expanded="${!collapsed}"
            aria-controls="${escapeHtml(tableId)}"
            aria-label="${escapeHtml(label)}"
            title="${escapeHtml(label)}"
          ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg></button>
        </span>
      </th>
    `;
  };

  const modelSectionHeader = (section) => {
    const groupKeys = section.groups.map((group) => group.key);
    const columns = section.groups.reduce(
      (sum, group) => sum + (collapsedModelGroups.has(group.key) ? 1 : group.columns),
      0,
    );
    return `
      <th
        class="kmue-section-head ${section.className} kmue-block-start"
        colspan="${columns}"
        scope="colgroup"
        data-kmue-section-head="${section.key}"
        data-kmue-section-groups="${groupKeys.join(",")}"
      >${escapeHtml(section.title)}</th>
    `;
  };

  const modelLeafCell = (key, label, classes = "", title = "", visibility = "expanded") => `
    <th
      class="${classes} ${visibility === "collapsed" ? "kmue-collapsed-leaf" : ""}"
      scope="col"
      ${title ? `title="${escapeHtml(title)}"` : ""}
      ${modelGroupCellAttrs(key, visibility)}
    >${label}</th>
  `;

  const modelTableCell = (content, key, classes = "", title = "", visibility = "expanded", attributes = "") => `
    <td
      class="${classes} ${visibility === "collapsed" ? "kmue-collapsed-summary" : ""}"
      ${title ? `title="${escapeHtml(title)}"` : ""}
      ${attributes}
      ${modelGroupCellAttrs(key, visibility)}
    >${content}</td>
  `;

  const modelMoneyCell = (value, key, classes = "", title = "", visibility = "expanded") => modelTableCell(
    money(value, 2),
    key,
    `num ${classes} ${metricTone(value)}`,
    title,
    visibility,
  );

  const actualMetricLeafCells = (key, label, classes = "", title = "") => [
    modelLeafCell(key, `${label}, ₽`, `kmue-actual-article-start ${classes}`, title),
    modelLeafCell(key, `${label}, ₽/шт`, classes, title),
    modelLeafCell(key, `${label}, % выручки`, classes, title),
  ].join("");

  const actualMetricCells = (amount, units, revenue, key, classes = "", title = "") => {
    const normalizedAmount = numericOrNull(amount);
    const normalizedUnits = numericOrNull(units);
    const normalizedRevenue = numericOrNull(revenue);
    const perUnit = normalizedAmount === null || !normalizedUnits
      ? null
      : normalizedAmount / normalizedUnits;
    const revenuePct = normalizedAmount === null || !normalizedRevenue
      ? null
      : normalizedAmount / normalizedRevenue * 100;
    return [
      modelMoneyCell(normalizedAmount, key, `kmue-actual-article-start ${classes}`, title),
      modelMoneyCell(perUnit, key, classes, title),
      modelTableCell(
        percent(revenuePct),
        key,
        `num ${classes} ${metricTone(revenuePct)}`,
        title,
      ),
    ].join("");
  };

  const actualNullableSum = (values) => {
    const normalized = values.map(numericOrNull);
    if (!normalized.length || normalized.some((value) => value === null)) return null;
    return normalized.reduce((sum, value) => sum + value, 0);
  };

  const actualFactForRow = (row) => {
    const units = Math.abs(Number(row.actual_units || 0));
    const revenue = numericOrNull(row.actual_revenue);
    const hasPeriodActivity = [
      row.actual_revenue,
      row.actual_commission,
      row.actual_acquiring,
      row.actual_forward_logistics,
      row.actual_reverse_logistics,
      row.actual_finance_advertising,
      row.actual_advertising_expense,
      row.actual_other_ozon,
      row.actual_tax,
      row.actual_vat,
      row.actual_capital,
      row.actual_profit,
    ].some((value) => {
      const normalized = numericOrNull(value);
      return normalized !== null && Math.abs(normalized) > 0;
    });
    if (!hasPeriodActivity) return null;
    const expenseFromAccrual = (value) => {
      const normalized = numericOrNull(value);
      return normalized === null ? null : -normalized;
    };
    const perUnitAmount = (value) => {
      const normalized = numericOrNull(value);
      return normalized === null ? null : normalized * units;
    };
    const supplyLogistics = Object.hasOwn(row, "management_result") ? 0 : actualNullableSum([
      perUnitAmount(row.crossdock_per_unit),
      perUnitAmount(row.acceptance_per_unit),
    ]);
    const ozon = {
      commission: expenseFromAccrual(row.actual_commission),
      acquiring: expenseFromAccrual(row.actual_acquiring),
      logistics: actualNullableSum([
        expenseFromAccrual(row.actual_forward_logistics),
        supplyLogistics,
      ]),
      returns: expenseFromAccrual(row.actual_reverse_logistics),
      advertising: numericOrNull(row.actual_advertising_expense),
      other: expenseFromAccrual(row.actual_other_ozon),
    };
    ozon.total = actualNullableSum([
      ozon.commission,
      ozon.acquiring,
      ozon.logistics,
      ozon.returns,
      ozon.advertising,
      ozon.other,
    ]);
    const seller = {
      cogs: numericOrNull(row.actual_cogs) ?? perUnitAmount(row.effective_cogs_per_unit ?? row.cogs_per_unit),
      expenses: numericOrNull(row.actual_seller_costs) ?? actualNullableSum([
        perUnitAmount(row.fulfillment_per_unit),
        perUnitAmount(row.inbound_per_unit),
        perUnitAmount(row.other_per_unit),
      ]),
    };
    seller.total = actualNullableSum([seller.cogs, seller.expenses]);
    const tax = {
      income: numericOrNull(row.actual_tax),
      vat: numericOrNull(row.actual_vat),
      capital: Object.hasOwn(row, "management_result") ? 0 : numericOrNull(row.actual_capital),
    };
    tax.total = actualNullableSum([tax.income, tax.vat, tax.capital]);
    const profit = numericOrNull(row.management_result ?? row.actual_profit);
    const totalCosts = revenue === null || profit === null ? null : revenue - profit;
    return {
      row,
      isTotal: false,
      revenue,
      units,
      averagePrice: revenue === null || !units ? null : revenue / units,
      ozon,
      seller,
      tax,
      totalCosts,
      profit,
      marginPct: revenue && profit !== null ? profit / revenue * 100 : null,
    };
  };

  const aggregateActualFacts = (facts) => {
    const sum = (getter) => actualNullableSum(facts.map(getter));
    const revenue = sum((fact) => fact.revenue);
    const units = sum((fact) => fact.units);
    const ozon = {
      commission: sum((fact) => fact.ozon.commission),
      acquiring: sum((fact) => fact.ozon.acquiring),
      logistics: sum((fact) => fact.ozon.logistics),
      returns: sum((fact) => fact.ozon.returns),
      advertising: sum((fact) => fact.ozon.advertising),
      other: sum((fact) => fact.ozon.other),
      total: sum((fact) => fact.ozon.total),
    };
    const seller = {
      cogs: sum((fact) => fact.seller.cogs),
      expenses: sum((fact) => fact.seller.expenses),
      total: sum((fact) => fact.seller.total),
    };
    const tax = {
      income: sum((fact) => fact.tax.income),
      vat: sum((fact) => fact.tax.vat),
      capital: sum((fact) => fact.tax.capital),
      total: sum((fact) => fact.tax.total),
    };
    const profit = sum((fact) => fact.profit);
    const totalCosts = sum((fact) => fact.totalCosts);
    return {
      row: null,
      isTotal: true,
      skuCount: facts.length,
      revenue,
      units,
      averagePrice: revenue === null || !units ? null : revenue / units,
      ozon,
      seller,
      tax,
      totalCosts,
      profit,
      marginPct: revenue && profit !== null ? profit / revenue * 100 : null,
    };
  };

  const modelTable = (rows) => `
    <div class="kmue-table-scroll kmue-model-scroll kmue-grouped-scroll">
      <table
        id="kmue-model-table"
        class="kmue-table kmue-model-table kmue-grouped-table kmue-model-structured-table kmue-konstex-table"
      >
        <thead>
          <tr class="kmue-model-section-row">
            <th class="kmue-sticky kmue-model-product-head" rowspan="3" scope="col">Товар</th>
            ${MODEL_SECTIONS.map(modelSectionHeader).join("")}
          </tr>
          <tr class="kmue-model-group-row">
            ${MODEL_GROUPS.map((group) => modelGroupHeader(group, "kmue-model-table")).join("")}
          </tr>
          <tr class="kmue-model-leaf-row">
            ${modelLeafCell("inputs", "Цена сейчас, ₽/шт", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("inputs", "Текущая цена, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("inputs", "Себестоимость, ₽/шт")}
            ${modelLeafCell("inputs", "Налог с дохода, %")}
            ${modelLeafCell("inputs", "НДС, %")}
            ${modelLeafCell("inputs", "ДРР заказов, %")}
            ${modelLeafCell("inputs", "Невыкуп / возвраты, %")}
            ${modelLeafCell("inputs", "Д×Ш×В, мм")}
            ${modelLeafCell("inputs", "Объём, л")}
            ${modelLeafCell("inputs", "Вес, г")}
            ${modelLeafCell("tariffs", "Комиссия Ozon, %", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("tariffs", "Комиссия Ozon, %", "kmue-block-start")}
            ${modelLeafCell("tariffs", "Эквайринг, %<small>по SKU из Ozon API</small>")}
            ${modelLeafCell("logistics", "Итого логистика Ozon, ₽/шт", "kmue-block-start kmue-expense-total", "Доставка + возвраты + кросс-докинг + приёмка", "collapsed")}
            ${modelLeafCell("logistics", "Доставка Ozon, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("logistics", "Ожидаемые возвраты Ozon, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("logistics", "Кросс-докинг, ₽/шт<small>ср. факт всей истории</small>", "kmue-expense-article")}
            ${modelLeafCell("logistics", "Приёмка Ozon, ₽/шт<small>ср. факт всей истории</small>", "kmue-expense-article")}
            ${modelLeafCell("logistics", "Итого логистика Ozon, ₽/шт", "kmue-expense-total", "Доставка + возвраты + кросс-докинг + приёмка")}
            ${modelLeafCell("seller", "Итого продавца, ₽/шт", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("seller", "Фулфилмент продавца, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("seller", "Доставка до Ozon, ₽/шт")}
            ${modelLeafCell("seller", "Прочее продавца, ₽/шт")}
            ${modelLeafCell("seller", "Стоимость денег, ₽/шт")}
            ${modelLeafCell("seller", "Итого продавца с себестоимостью, ₽/шт", "", "Себестоимость + все расходы продавца + стоимость денег")}
            ${modelLeafCell("goal-rrc", "Маржа РРЦ, %", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("goal-rrc", "Целевая маржа РРЦ, %", "kmue-block-start")}
            ${modelLeafCell("marketplace-rrc", "Итого Ozon РРЦ, ₽/шт<small>включая логистику</small>", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("marketplace-rrc", "Комиссия РРЦ, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("marketplace-rrc", "Эквайринг РРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("marketplace-rrc", "Реклама РРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("marketplace-rrc", "Итого Ozon РРЦ, ₽/шт<small>включая логистику</small>", "kmue-expense-total")}
            ${modelLeafCell("tax-rrc", "Налог + НДС РРЦ, ₽/шт", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("tax-rrc", "Налог при РРЦ, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("tax-rrc", "НДС при РРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("price-rrc", "РРЦ, ₽/шт", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("price-rrc", "РРЦ, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("roi-rrc", "ROI РРЦ, %", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("roi-rrc", "Прибыль при РРЦ, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("roi-rrc", "ROI РРЦ, %")}
            ${modelLeafCell("goal-mrc", "Маржа МРЦ, %", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("goal-mrc", "Целевая маржа МРЦ, %", "kmue-block-start")}
            ${modelLeafCell("marketplace-mrc", "Итого Ozon МРЦ, ₽/шт<small>включая логистику</small>", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("marketplace-mrc", "Комиссия МРЦ, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("marketplace-mrc", "Эквайринг МРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("marketplace-mrc", "Реклама МРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("marketplace-mrc", "Итого Ozon МРЦ, ₽/шт<small>включая логистику</small>", "kmue-expense-total")}
            ${modelLeafCell("tax-mrc", "Налог + НДС МРЦ, ₽/шт", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("tax-mrc", "Налог при МРЦ, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("tax-mrc", "НДС при МРЦ, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("price-mrc", "МРЦ, ₽/шт", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("price-mrc", "МРЦ, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("roi-mrc", "ROI МРЦ, %", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("roi-mrc", "Прибыль при МРЦ, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("roi-mrc", "ROI МРЦ, %")}
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => {
            const mrc = row.mrc_scenario || {};
            const rrc = row.rrc_scenario || {};
            const mrcMarketplace = scenarioCostSum(mrc, ["commission", "acquiring", "advertising", "forward_logistics", "reverse_logistics"]);
            const rrcMarketplace = scenarioCostSum(rrc, ["commission", "acquiring", "advertising", "forward_logistics", "reverse_logistics"]);
            const sharedLogistics = scenarioCostSum(row, ["tariff_forward_logistics_per_unit", "expected_reverse_logistics_per_unit", "crossdock_per_unit", "acceptance_per_unit"]);
            const rrcTaxTotal = scenarioCostSum(rrc, ["tax", "vat"]);
            const mrcTaxTotal = scenarioCostSum(mrc, ["tax", "vat"]);
            const mrcRoi = scenarioRoi(mrc);
            const rrcRoi = scenarioRoi(rrc);
            const adPlaceholder = row.advertising_pct == null ? "задайте цель" : String(row.advertising_pct);
            const returnPlaceholder = row.return_rate_pct_effective == null ? "задайте цель" : String(row.return_rate_pct_effective);
            const acquiring = acquiringDisplay(row);
            const dimensionTitle = row.dimension_missing_reason || row.dimension_source || "Ozon API";
            return `
              <tr${dimensionMissingRowAttrs(row)} data-finance-sku-row="${escapeHtml(row.sku)}" data-kmue-model-row="${escapeHtml(row.sku)}">
                ${modelProductCell(row)}
                ${modelMoneyCell(row.current_price, "inputs", "kmue-block-start", "", "collapsed")}
                ${modelMoneyCell(row.current_price, "inputs", "kmue-block-start")}
                ${modelTableCell(numberInput(row, "cogs_per_unit", { placeholder: row.effective_cogs_per_unit == null ? "не задана" : `расчёт ${row.effective_cogs_per_unit.toFixed(2)}` }), "inputs")}
                ${modelTableCell(percent(row.tax_pct), "inputs", "num")}
                ${modelTableCell(percent(row.vat_pct_effective), "inputs", "num")}
                ${modelTableCell(numberInput(row, "target_advertising_pct", { placeholder: adPlaceholder, max: "100" }), "inputs", "", row.advertising_adjustment_source || "ДРР заказов ÷ доля выкупа")}
                ${modelTableCell(numberInput(row, "return_rate_pct", { placeholder: returnPlaceholder, max: "100" }), "inputs", "", row.reverse_logistics_source || "Невыкуп / возвраты")}
                ${modelTableCell(`
                  <span class="kmue-dimensions-grid">
                    ${numberInput(row, "depth", { placeholder: row.ozon_depth == null ? "" : String(row.ozon_depth), className: "kmue-dimension-input" })}
                    ${numberInput(row, "width", { placeholder: row.ozon_width == null ? "" : String(row.ozon_width), className: "kmue-dimension-input" })}
                    ${numberInput(row, "height", { placeholder: row.ozon_height == null ? "" : String(row.ozon_height), className: "kmue-dimension-input" })}
                  </span>
                `, "inputs", "kmue-dimensions", dimensionTitle)}
                ${modelTableCell(row.model_volume_l == null ? "—" : formatNumber(row.model_volume_l, 3), "inputs", "num", row.dimension_source || "Ozon API")}
                ${modelTableCell(numberInput(row, "weight", { placeholder: row.ozon_weight == null ? "" : String(row.ozon_weight), className: "kmue-dimension-input" }), "inputs", "", dimensionTitle)}
                ${modelTableCell(percent(row.commission_pct), "tariffs", "num kmue-block-start", "Тариф категории и схемы Ozon", "collapsed")}
                ${modelTableCell(percent(row.commission_pct), "tariffs", "num kmue-block-start", "Тариф категории и схемы Ozon")}
                ${modelTableCell(acquiring.content, "tariffs", "num kmue-acquiring-rate", acquiring.title)}
                ${modelMoneyCell(sharedLogistics, "logistics", "kmue-block-start kmue-expense-total", "Доставка + возвраты + кросс-докинг + приёмка", "collapsed")}
                ${modelMoneyCell(row.tariff_forward_logistics_per_unit, "logistics", "kmue-block-start kmue-expense-article", row.forward_logistics_source || "Текущий тариф Ozon по объёму")}
                ${modelMoneyCell(row.expected_reverse_logistics_per_unit, "logistics", "kmue-expense-article", row.reverse_logistics_source || "Тариф возврата × ожидаемая доля возвратов")}
                ${modelMoneyCell(row.crossdock_per_unit, "logistics", "kmue-expense-article kmue-derived-cost", row.crossdock_source || "Среднее CrossDock за всю историю")}
                ${modelMoneyCell(row.acceptance_per_unit, "logistics", "kmue-expense-article kmue-derived-cost", row.acceptance_source || "Среднее SupplyInbound за всю историю")}
                ${modelMoneyCell(sharedLogistics, "logistics", "kmue-expense-total", "Доставка + возвраты + кросс-докинг + приёмка")}
                ${modelMoneyCell(sellerTotalPerUnit(row), "seller", "kmue-block-start", "Себестоимость + расходы продавца + стоимость денег", "collapsed")}
                ${modelTableCell(numberInput(row, "fulfillment_per_unit"), "seller", "kmue-block-start")}
                ${modelTableCell(numberInput(row, "inbound_per_unit"), "seller")}
                ${modelTableCell(numberInput(row, "other_per_unit"), "seller")}
                ${modelMoneyCell(row.capital_cost_per_unit, "seller")}
                ${modelMoneyCell(sellerTotalPerUnit(row), "seller", "", "Себестоимость + расходы продавца + стоимость денег")}
                ${modelTableCell(percent(row.rrc_margin_pct_effective), "goal-rrc", "num kmue-block-start", "", "collapsed")}
                ${modelTableCell(percent(row.rrc_margin_pct_effective), "goal-rrc", "num kmue-block-start")}
                ${modelMoneyCell(rrcMarketplace, "marketplace-rrc", "kmue-block-start kmue-expense-total", "Комиссия + эквайринг + реклама при РРЦ + единая логистика Ozon", "collapsed")}
                ${modelMoneyCell(rrc.commission, "marketplace-rrc", "kmue-block-start kmue-expense-article")}
                ${modelMoneyCell(rrc.acquiring, "marketplace-rrc", "kmue-expense-article")}
                ${modelMoneyCell(rrc.advertising, "marketplace-rrc", "kmue-expense-article", advertisingAdjustmentTitle(row, rrc))}
                ${modelMoneyCell(rrcMarketplace, "marketplace-rrc", "kmue-expense-total", "Комиссия + эквайринг + реклама при РРЦ + единая логистика Ozon")}
                ${modelMoneyCell(rrcTaxTotal, "tax-rrc", "kmue-block-start kmue-expense-total", "Налог с дохода + НДС при РРЦ", "collapsed")}
                ${modelMoneyCell(rrc.tax, "tax-rrc", "kmue-block-start kmue-expense-article")}
                ${modelMoneyCell(rrc.vat, "tax-rrc", "kmue-expense-article")}
                ${modelMoneyCell(rrc.price, "price-rrc", "kmue-block-start", "", "collapsed")}
                ${modelMoneyCell(rrc.price, "price-rrc", "kmue-block-start")}
                ${modelTableCell(percent(rrcRoi), "roi-rrc", `num kmue-block-start ${metricTone(rrcRoi)}`, "", "collapsed")}
                ${modelMoneyCell(rrc.profit, "roi-rrc", "kmue-block-start")}
                ${modelTableCell(percent(rrcRoi), "roi-rrc", `num ${metricTone(rrcRoi)}`)}
                ${modelTableCell(percent(row.mrc_margin_pct_effective), "goal-mrc", "num kmue-block-start", "", "collapsed")}
                ${modelTableCell(percent(row.mrc_margin_pct_effective), "goal-mrc", "num kmue-block-start")}
                ${modelMoneyCell(mrcMarketplace, "marketplace-mrc", "kmue-block-start kmue-expense-total", "Комиссия + эквайринг + реклама при МРЦ + единая логистика Ozon", "collapsed")}
                ${modelMoneyCell(mrc.commission, "marketplace-mrc", "kmue-block-start kmue-expense-article")}
                ${modelMoneyCell(mrc.acquiring, "marketplace-mrc", "kmue-expense-article")}
                ${modelMoneyCell(mrc.advertising, "marketplace-mrc", "kmue-expense-article", advertisingAdjustmentTitle(row, mrc))}
                ${modelMoneyCell(mrcMarketplace, "marketplace-mrc", "kmue-expense-total", "Комиссия + эквайринг + реклама при МРЦ + единая логистика Ozon")}
                ${modelMoneyCell(mrcTaxTotal, "tax-mrc", "kmue-block-start kmue-expense-total", "Налог с дохода + НДС при МРЦ", "collapsed")}
                ${modelMoneyCell(mrc.tax, "tax-mrc", "kmue-block-start kmue-expense-article")}
                ${modelMoneyCell(mrc.vat, "tax-mrc", "kmue-expense-article")}
                ${modelMoneyCell(mrc.price, "price-mrc", "kmue-block-start", "", "collapsed")}
                ${modelMoneyCell(mrc.price, "price-mrc", "kmue-block-start")}
                ${modelTableCell(percent(mrcRoi), "roi-mrc", `num kmue-block-start ${metricTone(mrcRoi)}`, "", "collapsed")}
                ${modelMoneyCell(mrc.profit, "roi-mrc", "kmue-block-start")}
                ${modelTableCell(percent(mrcRoi), "roi-mrc", `num ${metricTone(mrcRoi)}`)}
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
    <p class="kmue-table-note">CrossDock и приёмка Ozon входят в единый блок логистики как средние фактические расходы на выкупленную единицу за всю историю Finance. Доставка, ожидаемые возвраты, CrossDock и приёмка включены в итог каждого ценового сценария. Реклама считается на выкуп: ДРР заказов делится на долю выкупа. Остальные расходы продавца вводятся отдельно. Значения статей обычные, итоговые суммы жирные; каждый блок сворачивается без потери введённых значений.</p>
  `;

  const actualTable = (rows) => {
    const marketplace = financeUnitPayload?.marketplace === "wb" ? "WB" : "Ozon";
    const facts = rows.map(actualFactForRow).filter(Boolean);
    const renderedFacts = facts.length ? [aggregateActualFacts(facts), ...facts] : [];
    return `
      <div class="kmue-table-scroll kmue-model-scroll kmue-grouped-scroll">
        <table id="kmue-actual-table" class="kmue-table kmue-actual-table kmue-grouped-table kmue-konstex-table">
          <thead>
            <tr class="kmue-model-group-row">
              <th class="kmue-sticky kmue-model-product-head" rowspan="2" scope="col">Товар</th>
              ${ACTUAL_GROUPS.map((group) => modelGroupHeader(group, "kmue-actual-table")).join("")}
            </tr>
            <tr class="kmue-model-leaf-row">
              ${modelLeafCell("actual-sales", "Продажи, ₽", "kmue-block-start kmue-expense-total", "Выручка за выбранный период", "collapsed")}
              ${modelLeafCell("actual-sales", "Продажи, ₽", "kmue-block-start kmue-expense-total")}
              ${modelLeafCell("actual-sales", "Продажи, шт", "kmue-expense-total")}
              ${modelLeafCell("actual-sales", "Средняя цена, ₽/шт", "kmue-expense-total")}
              ${modelLeafCell("actual-ozon", "Итого Ozon, ₽", "kmue-block-start kmue-expense-total", "Расходы Ozon за период", "collapsed")}
              ${actualMetricLeafCells("actual-ozon", "Комиссия", "kmue-block-start kmue-expense-article")}
              ${actualMetricLeafCells("actual-ozon", "Эквайринг", "kmue-expense-article")}
              ${actualMetricLeafCells("actual-ozon", `Логистика и обработка ${marketplace}`, "kmue-expense-article", "Начисленная логистика периода; исторические средние не прибавляются повторно")}
              ${actualMetricLeafCells("actual-ozon", `Возвраты ${marketplace}`, "kmue-expense-article")}
              ${actualMetricLeafCells("actual-ozon", "Реклама", "kmue-expense-article", "Финансовая сумма Ozon распределена по SKU пропорционально расходам Performance API")}
              ${actualMetricLeafCells("actual-ozon", `Прочее ${marketplace}`, "kmue-expense-article")}
              ${actualMetricLeafCells("actual-ozon", `Итого ${marketplace}`, "kmue-expense-total")}
              ${modelLeafCell("actual-seller", "Итого продавца, ₽", "kmue-block-start kmue-expense-total", "Себестоимость реализованного + расходы продавца", "collapsed")}
              ${actualMetricLeafCells("actual-seller", "Себестоимость реализованного", "kmue-block-start kmue-expense-article")}
              ${actualMetricLeafCells("actual-seller", "Расходы продавца", "kmue-expense-article", "Фулфилмент продавца, доставка до Ozon и прочее × проданные единицы")}
              ${actualMetricLeafCells("actual-seller", "Итого продавца", "kmue-expense-total")}
              ${modelLeafCell("actual-tax", "Итого налоги и деньги, ₽", "kmue-block-start kmue-expense-total", "Налог + НДС + стоимость денег", "collapsed")}
              ${actualMetricLeafCells("actual-tax", "Налог", "kmue-block-start kmue-expense-article")}
              ${actualMetricLeafCells("actual-tax", "НДС", "kmue-expense-article")}
              ${actualMetricLeafCells("actual-tax", "Стоимость денег", "kmue-expense-article")}
              ${actualMetricLeafCells("actual-tax", "Итого налоги и деньги", "kmue-expense-total")}
              ${modelLeafCell("actual-result", "Вклад модели*, ₽", "kmue-block-start kmue-expense-total", "До нераспределённых расходов кабинета", "collapsed")}
              ${actualMetricLeafCells("actual-result", "Полные расходы", "kmue-block-start kmue-expense-total", "Выручка − экономический результат")}
              ${actualMetricLeafCells("actual-result", "Вклад модели*", "kmue-expense-total", "До нераспределённых расходов кабинета")}
              ${modelLeafCell("actual-corridor", "Позиция цены", "kmue-block-start", "", "collapsed")}
              ${modelLeafCell("actual-corridor", "МРЦ, ₽/шт", "kmue-block-start")}
              ${modelLeafCell("actual-corridor", "РРЦ, ₽/шт")}
              ${modelLeafCell("actual-corridor", "Позиция цены")}
            </tr>
          </thead>
          <tbody>
            ${renderedFacts.map((fact) => {
              const row = fact.row;
              const rowClass = fact.isTotal ? "kmue-actual-summary-row" : "";
              const product = fact.isTotal
                ? `
                  <td class="kmue-product kmue-model-product kmue-sticky">
                    <strong>ИТОГО ЗА ПЕРИОД</strong>
                    <span>${fact.skuCount} SKU с операциями</span>
                    <small>${formatNumber(fact.units, 2)} шт</small>
                  </td>
                `
                : modelProductCell(row);
              const corridorCollapsed = fact.isTotal
                ? modelTableCell("—", "actual-corridor", "kmue-block-start", "", "collapsed")
                : modelTableCell(
                  positionBadge(fact.averagePrice, row.mrc_price, row.rrc_price),
                  "actual-corridor",
                  "kmue-block-start",
                  "",
                  "collapsed",
                );
              const corridorExpanded = fact.isTotal
                ? [
                  modelTableCell("—", "actual-corridor", "kmue-block-start"),
                  modelTableCell("—", "actual-corridor"),
                  modelTableCell("—", "actual-corridor"),
                ].join("")
                : [
                  modelMoneyCell(row.mrc_price, "actual-corridor", "kmue-block-start"),
                  modelMoneyCell(row.rrc_price, "actual-corridor"),
                  modelTableCell(positionBadge(fact.averagePrice, row.mrc_price, row.rrc_price), "actual-corridor"),
                ].join("");
              return `
                <tr${dimensionMissingRowAttrs(row, rowClass)}>
                  ${product}
                  ${modelMoneyCell(fact.revenue, "actual-sales", "kmue-block-start kmue-expense-total", "Выручка за выбранный период", "collapsed")}
                  ${modelMoneyCell(fact.revenue, "actual-sales", "kmue-block-start kmue-expense-total")}
                  ${modelTableCell(formatNumber(fact.units, 2), "actual-sales", "num kmue-expense-total")}
                  ${modelMoneyCell(fact.averagePrice, "actual-sales", "kmue-expense-total")}
                  ${modelMoneyCell(fact.ozon.total, "actual-ozon", "kmue-block-start kmue-expense-total", "Расходы Ozon за период", "collapsed")}
                  ${actualMetricCells(fact.ozon.commission, fact.units, fact.revenue, "actual-ozon", "kmue-block-start kmue-expense-article")}
                  ${actualMetricCells(fact.ozon.acquiring, fact.units, fact.revenue, "actual-ozon", "kmue-expense-article")}
                  ${actualMetricCells(fact.ozon.logistics, fact.units, fact.revenue, "actual-ozon", "kmue-expense-article", "Фактическая доставка + исторические средние CrossDock и приёмки")}
                  ${actualMetricCells(fact.ozon.returns, fact.units, fact.revenue, "actual-ozon", "kmue-expense-article")}
                  ${actualMetricCells(fact.ozon.advertising, fact.units, fact.revenue, "actual-ozon", "kmue-expense-article", row ? row.actual_advertising_source : "Finance Ozon распределён по SKU по доле Performance API")}
                  ${actualMetricCells(fact.ozon.other, fact.units, fact.revenue, "actual-ozon", "kmue-expense-article")}
                  ${actualMetricCells(fact.ozon.total, fact.units, fact.revenue, "actual-ozon", "kmue-expense-total")}
                  ${modelMoneyCell(fact.seller.total, "actual-seller", "kmue-block-start kmue-expense-total", "Себестоимость реализованного + расходы продавца", "collapsed")}
                  ${actualMetricCells(fact.seller.cogs, fact.units, fact.revenue, "actual-seller", "kmue-block-start kmue-expense-article")}
                  ${actualMetricCells(fact.seller.expenses, fact.units, fact.revenue, "actual-seller", "kmue-expense-article")}
                  ${actualMetricCells(fact.seller.total, fact.units, fact.revenue, "actual-seller", "kmue-expense-total")}
                  ${modelMoneyCell(fact.tax.total, "actual-tax", "kmue-block-start kmue-expense-total", "Налог + НДС + стоимость денег", "collapsed")}
                  ${actualMetricCells(fact.tax.income, fact.units, fact.revenue, "actual-tax", "kmue-block-start kmue-expense-article")}
                  ${actualMetricCells(fact.tax.vat, fact.units, fact.revenue, "actual-tax", "kmue-expense-article")}
                  ${actualMetricCells(fact.tax.capital, fact.units, fact.revenue, "actual-tax", "kmue-expense-article")}
                  ${actualMetricCells(fact.tax.total, fact.units, fact.revenue, "actual-tax", "kmue-expense-total")}
                  ${modelMoneyCell(fact.profit, "actual-result", "kmue-block-start kmue-expense-total", "До нераспределённых расходов кабинета", "collapsed")}
                  ${actualMetricCells(fact.totalCosts, fact.units, fact.revenue, "actual-result", "kmue-block-start kmue-expense-total")}
                  ${actualMetricCells(fact.profit, fact.units, fact.revenue, "actual-result", "kmue-expense-total")}
                  ${corridorCollapsed}
                  ${corridorExpanded}
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
      <p class="kmue-table-note">Факт охватывает выбранный период. Для каждой статьи показаны сумма, ₽; сумма на проданную единицу, ₽/шт; и доля фактической выручки, %. Итоговая строка суммирует все SKU с финансовыми операциями, включая расходы при нулевых продажах; для них ₽/шт и % выводятся как «—». CrossDock и приёмка рассчитаны как подтверждённые средние всей истории × фактические единицы периода. Реклама сверяется с общей суммой Finance Ozon и распределяется между SKU по доле фактических расходов Performance API, поэтому входит в экономический результат без задвоения. * Экономический результат учитывает расходы, привязанные к SKU или распределённые подтверждённым правилом; хранение и прочие общие начисления без драйвера остаются в P&amp;L.</p>
    `;
  };

  const defaultScenarioDraft = (row, payload) => ({
    price: numericOrNull(row.current_price),
    commission_pct: numericOrNull(row.commission_pct),
    cogs_per_unit: numericOrNull(row.cogs_per_unit),
    tax_pct: numericOrNull(payload.global_settings?.tax_pct),
  });

  const getScenarioDraft = (row, payload) => {
    const key = String(row.sku);
    if (!scenarioDrafts.has(key)) scenarioDrafts.set(key, defaultScenarioDraft(row, payload));
    return scenarioDrafts.get(key);
  };

  const calculateScenario = (row, draft, payload) => {
    const price = numericOrNull(draft.price);
    const commissionPct = numericOrNull(draft.commission_pct);
    const cogs = numericOrNull(draft.cogs_per_unit);
    const taxPct = numericOrNull(draft.tax_pct);
    const acquiringPct = numericOrNull(row.model_acquiring_pct);
    const vatPct = numericOrNull(payload.global_settings?.vat_pct) ?? numericOrNull(row.vat_pct_effective) ?? 0;
    const advertisingPct = numericOrNull(row.advertising_pct);
    const nonBuyoutPct = numericOrNull(row.return_rate_pct_effective);
    const buyoutRatePct = nonBuyoutPct === null ? null : 100 - nonBuyoutPct;
    const advertisingPctEffective = advertisingPct === null || buyoutRatePct === null || buyoutRatePct <= 0
      ? null
      : advertisingPct * 100 / buyoutRatePct;
    const tariffForward = numericOrNull(row.tariff_forward_logistics_per_unit);
    const crossdock = numericOrNull(row.crossdock_per_unit);
    const acceptance = numericOrNull(row.acceptance_per_unit);
    const supplyLogistics = crossdock === null || acceptance === null ? null : crossdock + acceptance;
    const forward = tariffForward === null || supplyLogistics === null ? null : tariffForward + supplyLogistics;
    const reverse = numericOrNull(row.expected_reverse_logistics_per_unit);
    const sellerCosts = (numericOrNull(row.fulfillment_per_unit) || 0)
      + (numericOrNull(row.inbound_per_unit) || 0)
      + (numericOrNull(row.other_per_unit) || 0);
    const capitalDays = numericOrNull(row.capital_days_effective) || 0;
    const capitalRate = numericOrNull(row.capital_rate_pct_effective) || 0;
    const preparationCosts = supplyLogistics === null ? null : sellerCosts + supplyLogistics;
    const capital = cogs === null || preparationCosts === null ? null : (cogs + preparationCosts) * capitalRate / 100 * capitalDays / 365;
    const sellerTotal = cogs === null || capital === null ? null : cogs + sellerCosts + capital;
    if (
      price === null
      || commissionPct === null
      || acquiringPct === null
      || advertisingPctEffective === null
      || forward === null
      || reverse === null
      || cogs === null
      || taxPct === null
      || capital === null
    ) {
      return {
        price, cogs, forward, reverse, supplyLogistics, sellerCosts, capital, sellerTotal,
        advertisingPctEffective, buyoutRatePct,
        commission: null, acquiring: null, advertising: null,
        marketplaceCosts: null, tax: null, vat: null, taxTotal: null,
        fixedCosts: null, totalCosts: null, profitUnit: null,
        marginPct: null, roi: null, deltaPlan: null, deltaFact: null,
      };
    }
    const commission = price * commissionPct / 100;
    const acquiring = price * acquiringPct / 100;
    const tax = price * taxPct / 100;
    const vat = price * vatPct / 100;
    const advertising = price * advertisingPctEffective / 100;
    const marketplaceCosts = commission + acquiring + advertising + forward + reverse;
    const taxTotal = tax + vat;
    const fixedCosts = forward + reverse + sellerTotal;
    const totalCosts = marketplaceCosts + taxTotal + sellerTotal;
    const profitUnit = price - totalCosts;
    const planUnit = numericOrNull(row.rrc_scenario?.profit);
    const factUnit = numericOrNull(row.actual_profit_per_unit);
    return {
      price, cogs, commission, acquiring, tax, vat, advertising,
      advertisingPctEffective, buyoutRatePct,
      forward, reverse, supplyLogistics, marketplaceCosts, sellerCosts, capital, sellerTotal,
      taxTotal, fixedCosts, totalCosts, profitUnit,
      marginPct: price ? profitUnit / price * 100 : null,
      roi: totalCosts ? profitUnit / totalCosts * 100 : null,
      deltaPlan: planUnit == null ? null : profitUnit - planUnit,
      deltaFact: factUnit == null ? null : profitUnit - factUnit,
    };
  };

  const scenarioInput = (field, value, options = {}) => `
    <input
      class="kmue-cell-input kmue-scenario-input"
      type="number"
      data-kmue-scenario-field="${escapeHtml(field)}"
      value="${inputValue(value)}"
      step="${options.step || "0.01"}"
      min="${options.min ?? "0"}"
      ${options.max != null ? `max="${options.max}"` : ""}
      placeholder="${escapeHtml(options.placeholder || "")}"
    />
  `;

  const scenarioOutputCell = (value, key, output, classes = "", title = "", visibility = "expanded", formatter = (item) => money(item, 2)) => modelTableCell(
    formatter(value),
    key,
    `num ${classes} ${metricTone(value)}`,
    title,
    visibility,
    `data-kmue-scenario-output="${escapeHtml(output)}"`,
  );

  const scenarioTable = (rows, payload) => `
    <div class="kmue-table-scroll kmue-model-scroll kmue-grouped-scroll">
      <table id="kmue-scenario-table" class="kmue-table kmue-scenario-table kmue-grouped-table kmue-konstex-table">
        <thead>
          <tr class="kmue-model-group-row">
            <th class="kmue-sticky kmue-model-product-head" rowspan="2" scope="col">Товар</th>
            ${SCENARIO_GROUPS.map((group) => modelGroupHeader(group, "kmue-scenario-table")).join("")}
          </tr>
          <tr class="kmue-model-leaf-row">
            ${modelLeafCell("scenario-inputs", "Новая цена, ₽/шт", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("scenario-inputs", "Текущая цена, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("scenario-inputs", "Новая цена, ₽/шт")}
            ${modelLeafCell("scenario-inputs", "Новая комиссия, %")}
            ${modelLeafCell("scenario-inputs", "Новая себестоимость, ₽/шт")}
            ${modelLeafCell("scenario-inputs", "Налог, %")}
            ${modelLeafCell("scenario-inputs", "Действие")}
            ${modelLeafCell("scenario-ozon", "Итого Ozon, ₽/шт", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("scenario-ozon", "Комиссия, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("scenario-ozon", "Эквайринг, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("scenario-ozon", "Реклама, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("scenario-ozon", "Логистика Ozon, ₽/шт", "kmue-expense-article", "Доставка + CrossDock + приёмка")}
            ${modelLeafCell("scenario-ozon", "Обратная логистика, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("scenario-ozon", "Итого Ozon, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-seller", "Итого продавца, ₽/шт", "kmue-block-start kmue-expense-total", "Себестоимость + расходы продавца + стоимость денег", "collapsed")}
            ${modelLeafCell("scenario-seller", "Расходы продавца, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("scenario-seller", "Стоимость денег, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("scenario-seller", "Итого продавца, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-tax", "Итого налоги, ₽/шт", "kmue-block-start kmue-expense-total", "Налог + НДС", "collapsed")}
            ${modelLeafCell("scenario-tax", "Налог, ₽/шт", "kmue-block-start kmue-expense-article")}
            ${modelLeafCell("scenario-tax", "НДС, ₽/шт", "kmue-expense-article")}
            ${modelLeafCell("scenario-tax", "Итого налоги, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-result", "Финрез, ₽/шт", "kmue-block-start kmue-expense-total", "", "collapsed")}
            ${modelLeafCell("scenario-result", "Полные расходы, ₽/шт", "kmue-block-start kmue-expense-total")}
            ${modelLeafCell("scenario-result", "Финрез, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-result", "Маржа, %", "kmue-expense-total")}
            ${modelLeafCell("scenario-result", "ROI, %", "kmue-expense-total")}
            ${modelLeafCell("scenario-result", "Δ к РРЦ, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-result", "Δ к факту, ₽/шт", "kmue-expense-total")}
            ${modelLeafCell("scenario-corridor", "Позиция цены", "kmue-block-start", "", "collapsed")}
            ${modelLeafCell("scenario-corridor", "Безубыток / МРЦ, ₽/шт", "kmue-block-start")}
            ${modelLeafCell("scenario-corridor", "РРЦ, ₽/шт")}
            ${modelLeafCell("scenario-corridor", "Позиция цены")}
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => {
            const draft = getScenarioDraft(row, payload);
            const result = calculateScenario(row, draft, payload);
            return `
              <tr${dimensionMissingRowAttrs(row)} data-kmue-scenario-row="${escapeHtml(row.sku)}">
                ${modelProductCell(row)}
                ${scenarioOutputCell(result.price, "scenario-inputs", "priceSummary", "kmue-block-start kmue-expense-total", "", "collapsed")}
                ${modelMoneyCell(row.current_price, "scenario-inputs", "kmue-block-start")}
                ${modelTableCell(scenarioInput("price", draft.price, { placeholder: row.current_price == null ? "" : String(row.current_price) }), "scenario-inputs")}
                ${modelTableCell(scenarioInput("commission_pct", draft.commission_pct, { max: 100 }), "scenario-inputs")}
                ${modelTableCell(scenarioInput("cogs_per_unit", draft.cogs_per_unit, { placeholder: "введите себес" }), "scenario-inputs")}
                ${modelTableCell(scenarioInput("tax_pct", draft.tax_pct, { max: 100, placeholder: "введите налог" }), "scenario-inputs")}
                ${modelTableCell(`<button type="button" class="ghost kmue-reset-row" data-kmue-action="reset-scenario" aria-label="Вернуть значения модели" title="Вернуть значения модели"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4v6h6M5.5 14a7 7 0 1 0 1.8-7.2L4 10" /></svg></button>`, "scenario-inputs")}
                ${scenarioOutputCell(result.marketplaceCosts, "scenario-ozon", "marketplaceCosts", "kmue-block-start kmue-expense-total", "", "collapsed")}
                ${scenarioOutputCell(result.commission, "scenario-ozon", "commission", "kmue-block-start kmue-expense-article")}
                ${scenarioOutputCell(result.acquiring, "scenario-ozon", "acquiring", "kmue-expense-article")}
                ${scenarioOutputCell(result.advertising, "scenario-ozon", "advertising", "kmue-expense-article", advertisingAdjustmentTitle(row, { advertising_pct_effective: result.advertisingPctEffective }))}
                ${scenarioOutputCell(result.forward, "scenario-ozon", "forward", "kmue-expense-article")}
                ${scenarioOutputCell(result.reverse, "scenario-ozon", "reverse", "kmue-expense-article")}
                ${scenarioOutputCell(result.marketplaceCosts, "scenario-ozon", "marketplaceCosts", "kmue-expense-total")}
                ${scenarioOutputCell(result.sellerTotal, "scenario-seller", "sellerTotal", "kmue-block-start kmue-expense-total", "Себестоимость + расходы продавца + стоимость денег", "collapsed")}
                ${scenarioOutputCell(result.sellerCosts, "scenario-seller", "sellerCosts", "kmue-block-start kmue-expense-article")}
                ${scenarioOutputCell(result.capital, "scenario-seller", "capital", "kmue-expense-article")}
                ${scenarioOutputCell(result.sellerTotal, "scenario-seller", "sellerTotal", "kmue-expense-total")}
                ${scenarioOutputCell(result.taxTotal, "scenario-tax", "taxTotal", "kmue-block-start kmue-expense-total", "Налог + НДС", "collapsed")}
                ${scenarioOutputCell(result.tax, "scenario-tax", "tax", "kmue-block-start kmue-expense-article")}
                ${scenarioOutputCell(result.vat, "scenario-tax", "vat", "kmue-expense-article")}
                ${scenarioOutputCell(result.taxTotal, "scenario-tax", "taxTotal", "kmue-expense-total")}
                ${scenarioOutputCell(result.profitUnit, "scenario-result", "profitUnit", "kmue-block-start kmue-expense-total", "", "collapsed")}
                ${scenarioOutputCell(result.totalCosts, "scenario-result", "totalCosts", "kmue-block-start kmue-expense-total")}
                ${scenarioOutputCell(result.profitUnit, "scenario-result", "profitUnit", "kmue-expense-total")}
                ${scenarioOutputCell(result.marginPct, "scenario-result", "marginPct", "kmue-expense-total", "", "expanded", percent)}
                ${scenarioOutputCell(result.roi, "scenario-result", "roi", "kmue-expense-total", "", "expanded", percent)}
                ${scenarioOutputCell(result.deltaPlan, "scenario-result", "deltaPlan", "kmue-expense-total")}
                ${scenarioOutputCell(result.deltaFact, "scenario-result", "deltaFact", "kmue-expense-total")}
                ${modelTableCell(positionBadge(result.price, row.mrc_price, row.rrc_price), "scenario-corridor", "kmue-block-start", "", "collapsed", 'data-kmue-scenario-output="position"')}
                ${modelMoneyCell(row.mrc_price, "scenario-corridor", "kmue-block-start")}
                ${modelMoneyCell(row.rrc_price, "scenario-corridor")}
                ${modelTableCell(positionBadge(result.price, row.mrc_price, row.rrc_price), "scenario-corridor", "", "", "expanded", 'data-kmue-scenario-output="position"')}
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
    <p class="kmue-table-note">Сценарий считается на одну выкупленную единицу и не использует остаток или плановое количество. Логистика включает доставку, CrossDock и приёмку; рекламный расход = ДРР заказов ÷ доля выкупа. Значения статей обычные, итоги жирные. Каждый блок сворачивается независимо; ручные значения сохраняются.</p>
  `;

  const updateScenarioRow = (tr, row, payload) => {
    const draft = getScenarioDraft(row, payload);
    tr.querySelectorAll("[data-kmue-scenario-field]").forEach((input) => {
      draft[input.dataset.kmueScenarioField] = numericOrNull(input.value);
    });
    const result = calculateScenario(row, draft, payload);
    const set = (key, value, formatter = (item) => money(item, 2)) => {
      tr.querySelectorAll(`[data-kmue-scenario-output="${key}"]`).forEach((cell) => {
        cell.innerHTML = formatter(value);
        if (["profitUnit", "marginPct", "roi", "deltaPlan", "deltaFact"].includes(key)) {
          cell.classList.remove("is-negative", "is-positive", "is-missing");
          const tone = metricTone(value);
          if (tone) cell.classList.add(tone);
        }
      });
    };
    set("priceSummary", result.price);
    set("commission", result.commission);
    set("acquiring", result.acquiring);
    set("advertising", result.advertising);
    set("forward", result.forward);
    set("reverse", result.reverse);
    set("marketplaceCosts", result.marketplaceCosts);
    set("sellerCosts", result.sellerCosts);
    set("capital", result.capital);
    set("sellerTotal", result.sellerTotal);
    set("tax", result.tax);
    set("vat", result.vat);
    set("taxTotal", result.taxTotal);
    set("totalCosts", result.totalCosts);
    set("profitUnit", result.profitUnit);
    set("marginPct", result.marginPct, percent);
    set("roi", result.roi, percent);
    set("deltaPlan", result.deltaPlan);
    set("deltaFact", result.deltaFact);
    set("position", result.price, (value) => positionBadge(value, row.mrc_price, row.rrc_price));
  };

  function render(payload) {
    if (!payload) return;
    const root = qs("financeDashboard");
    if (!root) return;
    financeUnitPayload = payload;
    if (payload.available === false) {
      root.innerHTML = `
        <section class="finance-card finance-alert finance-alert-warning" role="status">
          <h2>Юнит-экономика WB пока недоступна</h2>
          <p>${escapeHtml(payload.message || "Детальные расходы WB ещё не нормализованы по SKU.")}</p>
        </section>`;
      return;
    }
    const marketplace = payload.marketplace === "wb" ? "WB" : "Ozon";
    const client = clientLabel(payload.client);
    if (lastPayload !== payload) {
      activeTab = payload.model_read_only ? "actual" : "plan";
      collapsedModelGroups.clear();
      scenarioDrafts.clear();
      lastPayload = payload;
    }
    document.querySelector(".visuals")?.classList.remove("ku3-dashboard");
    const rows = payload.rows || [];
    const table = activeTab === "actual"
      ? actualTable(rows)
      : activeTab === "forecast"
        ? scenarioTable(rows, payload)
        : modelTable(rows);
    root.innerHTML = `
      <section class="kmue-report">
        <header class="kmue-report-head">
          <div>
            <h2>Юнит-экономика ${escapeHtml(client)}</h2>
            <p>Модель и сценарий на единицу; продажи, расходы и результат за период · ${financeFormatRuDate(payload.date_from)} — ${financeFormatRuDate(payload.date_to)}</p>
          </div>
          <span class="kmue-api-badge">Модель/сценарий на 1 единицу · факт за период · Тарифы ${marketplace} API · ${marketplace}</span>
        </header>
        <div class="kmue-settings-card">
          <div class="kmue-settings-row">
            ${financeDateToolbar(payload, "Период факта")}
            ${payload.model_read_only ? "" : globalSettings(payload)}
          </div>
        </div>
        ${payload.model_notice ? `<p class="pl3-warning">${escapeHtml(payload.model_notice)} Без ставки налога вклад показан до налога с дохода.</p>` : ""}${completenessWarnings(payload)}
        <div class="kmue-tabs" role="tablist" aria-label="Режимы юнит-экономики">
          ${[
            ["plan", "1. Модель на единицу", '<path d="M5 20V10m7 10V4m7 16v-7M3 20h18" />'],
            ["actual", "2. Факт за период", '<path d="M5 4h14v16H5zM8 8h8M8 12h3m2 0h3m-8 4h3m2 0h3" />'],
            ["forecast", "3. Сценарий на единицу", '<path d="m4 17 5-5 4 3 7-9M15 6h5v5" />'],
          ].map(([key, label, icon]) => `
            <button
              type="button"
              role="tab"
              ${payload.model_read_only && key !== "actual" ? "disabled" : ""} aria-selected="${activeTab === key}"
              aria-label="${label}"
              title="${label}"
              class="kmue-icon-tab ${activeTab === key ? "active" : ""}"
              data-finance-unit-tab="${key}"
            ><svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg></button>
          `).join("")}
        </div>
        <section class="kmue-table-card">
          <header>
            <div>
              <h3>${activeTab === "actual" ? "Факт за период" : activeTab === "forecast" ? "Сценарий на единицу" : "Расчёт на единицу"}</h3>
              <p>${activeTab === "actual"
                ? "Продажи, расходы по статьям и экономический результат: сумма за период, на единицу и в % выручки."
                : activeTab === "forecast"
                  ? "Что изменится на одной продаже при другой цене, комиссии, себестоимости или налоге."
                  : "Тарифы и затраты по SKU формируют безубыток, МРЦ, РРЦ и прибыль с одной продажи."}</p>
            </div>
            <div class="kmue-header-actions">
              <button type="button" class="ghost kmue-icon-button" data-kmue-action="methodology" aria-label="Методика расчёта" title="Методика расчёта">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h10a2 2 0 0 1 2 2v16H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 5h6M9 12h2m2 0h2m-6 4h2m2 0h2" /></svg>
              </button>
              ${activeTab === "plan" ? '<button type="button" class="ghost kmue-icon-button kmue-save-action" data-finance-action="save-unit" aria-label="Сохранить модель" title="Сохранить модель"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h11l3 3v15H5zM8 3v6h8V3M8 21v-7h8v7" /></svg></button>' : ""}
            </div>
          </header>
          ${table}
          ${unitPagination(payload)}
        </section>
        ${unitMethodologyDialog(payload)}
      </section>
    `;
  }

  const originalRender = window.renderUnitEconomicsDashboard;
  window.renderUnitEconomicsDashboard = render;

  const root = qs("financeDashboard");
  root?.addEventListener("click", (event) => {
    const pageButton = event.target.closest("[data-kmue-page]");
    if (pageButton && !pageButton.disabled) {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.__financeUnitPage = Number(pageButton.dataset.kmuePage || 1);
      root.innerHTML = '<section class="finance-card finance-loading-card" role="status"><h2>Загрузка страницы товаров</h2><p>Получаем 100 SKU без блокировки браузера…</p></section>';
      renderKmFinanceDashboard().catch((error) => {
        qs("status").textContent = `Ошибка загрузки страницы: ${error.message}`;
      });
      return;
    }
    const tab = event.target.closest("[data-finance-unit-tab]");
    if (tab && lastPayload) {
      event.preventDefault();
      event.stopImmediatePropagation();
      try {
        captureFinanceUnitDraft();
        activeTab = tab.dataset.financeUnitTab || "plan";
        render(lastPayload);
      } catch (error) {
        qs("status").textContent = `Ошибка переключения вкладки: ${error.message}`;
      }
      return;
    }
    const groupToggle = event.target.closest('[data-kmue-action="toggle-model-group"]');
    if (groupToggle) {
      event.preventDefault();
      const table = groupToggle.closest("table");
      const key = String(groupToggle.dataset.kmueGroupKey || "");
      if (!table || !key) return;
      const header = table.querySelector(`[data-kmue-group-head="${key}"]`);
      if (!header) return;
      const title = String(header.dataset.kmueGroupLabel || key);
      const collapsed = !collapsedModelGroups.has(key);
      if (collapsed) collapsedModelGroups.add(key);
      else collapsedModelGroups.delete(key);
      if (header) {
        header.colSpan = collapsed ? 1 : Number(header.dataset.kmueExpandedColspan || 1);
        header.dataset.kmueCollapsed = String(collapsed);
      }
      table.querySelectorAll("[data-kmue-section-groups]").forEach((sectionHeader) => {
        const sectionKeys = String(sectionHeader.dataset.kmueSectionGroups || "").split(",");
        if (!sectionKeys.includes(key)) return;
        sectionHeader.colSpan = sectionKeys.reduce((sum, groupKey) => {
          const groupHeader = table.querySelector(`[data-kmue-group-head="${groupKey}"]`);
          return sum + Number(groupHeader?.colSpan || 0);
        }, 0);
      });
      table.querySelectorAll(`[data-kmue-group-cell="${key}"]`).forEach((cell) => {
        const isCollapsedSummary = cell.dataset.kmueGroupVisibility === "collapsed";
        cell.hidden = collapsed ? !isCollapsedSummary : isCollapsedSummary;
      });
      const label = collapsed ? `Развернуть блок «${title}»` : `Свернуть блок «${title}»`;
      groupToggle.setAttribute("aria-expanded", String(!collapsed));
      groupToggle.setAttribute("aria-label", label);
      groupToggle.setAttribute("title", label);
      groupToggle.classList.toggle("is-collapsed", collapsed);
      return;
    }
    const methodology = event.target.closest('[data-kmue-action="methodology"]');
    if (methodology) {
      event.preventDefault();
      root.querySelector("[data-kmue-method-dialog]")?.showModal();
      return;
    }
    const closeMethodology = event.target.closest('[data-kmue-action="close-methodology"]');
    if (closeMethodology) {
      event.preventDefault();
      closeMethodology.closest("dialog")?.close();
      return;
    }
    const resetScenario = event.target.closest('[data-kmue-action="reset-scenario"]');
    if (resetScenario && lastPayload) {
      const tr = resetScenario.closest("[data-kmue-scenario-row]");
      if (tr) {
        scenarioDrafts.delete(String(tr.dataset.kmueScenarioRow));
        render(lastPayload);
      }
      return;
    }
    const repaint = event.target.closest('[data-kmue-action="repaint"]');
    if (repaint && lastPayload) {
      try {
        captureFinanceUnitDraft();
        render(lastPayload);
      } catch (error) {
        qs("status").textContent = `Ошибка: ${error.message}`;
      }
    }
  }, true);

  root?.addEventListener("input", (event) => {
    if (!lastPayload) return;
    const rowsBySku = new Map((lastPayload.rows || []).map((row) => [String(row.sku), row]));
    const scenarioRow = event.target.closest("[data-kmue-scenario-row]");
    if (scenarioRow && event.target.matches("[data-kmue-scenario-field]")) {
      const row = rowsBySku.get(String(scenarioRow.dataset.kmueScenarioRow));
      if (row) updateScenarioRow(scenarioRow, row, lastPayload);
      return;
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("#navUnitEconomics")) return;
    queueMicrotask(() => {
      if (typeof currentClient === "function" && !["km_trade", "konstex"].includes(currentClient())) {
        qs("financeDashboard")?.classList.add("hidden");
      }
    });
  }, true);

  document.addEventListener("change", (event) => {
    if (event.target.id !== "clientSelect") return;
    if (!["km_trade", "konstex"].includes(event.target.value)) {
      qs("financeDashboard")?.classList.add("hidden");
      document.querySelector(".visuals")?.classList.remove("kmue-dashboard");
    }
  }, true);

  window.__kmUnitEconomicsParity = {
    render,
    originalRender,
    version: "20260803-km-unit-ad-allocation-v19",
  };
})();
