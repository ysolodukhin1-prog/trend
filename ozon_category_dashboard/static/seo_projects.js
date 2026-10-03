(function () {
  "use strict";

  const seoState = {
    client: "", marketplace: "ozon", marketplaces: ["ozon"], projectKind: "monitoring", projectId: "", detail: null, root: null,
    builderSelected: new Map(), builderPendingCategories: new Set(), builderAppliedCategories: new Set(), builderCandidates: null, builderPage: 1,
    builderAppliedFilters: {}, builderActiveCategory: "", builderVisibleCategories: [],
    builderCategorySort: { key: "sku_count", dir: "desc" }, builderCategoryFilters: {},
    builderSkuSort: { key: "sales_14d_rub", dir: "desc" }, builderSkuFilters: {},
    builderEditProjectId: "",
    projectFilters: {}, projectSkuFilters: {}, projectSkuSort: { key: "sales_14d_rub", dir: "desc" }, projectPage: 1, projectCandidates: null,
    activeRuns: new Set(), skuWorkspaceReturnFocus: null, skuWorkspaceRun: null,
    fullRunJobId: "", fullRunPollTimer: null,
  };
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (value, digits = 0) => value === null || value === undefined || value === "" ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const pct = (value) => value === null || value === undefined ? "—" : `${num(value, 1)}%`;
  const rub = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    const amount = Number(value);
    if (Math.abs(amount) >= 1000000) return `${num(amount / 1000000, 1)} млн ₽`;
    if (Math.abs(amount) >= 1000) return `${num(amount / 1000, 1)} тыс. ₽`;
    return `${num(amount)} ₽`;
  };
  const shortDate = (value) => value ? new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU") : "—";
  const dateText = (value) => value ? new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
  const statusLabels = { draft: "Черновик", active: "Ожидает данных", ready: "Актуален", partial: "Частично", error: "Ошибка" };
  const segmentLabels = { priority: "Приоритет", core: "Ядро", tail: "Хвост" };
  const workspaceCopy = () => seoState.projectKind === "generation"
    ? { title: "Проекты генерации SEO", empty: "Проектов генерации SEO пока нет", description: "Группы товаров, сбор и обогащение ключей для последующей генерации SEO" }
    : { title: "Проекты мониторинга SEO", empty: "Проектов мониторинга SEO пока нет", description: "Группы SKU, снимки поисковых запросов и динамика органической эффективности" };

  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function post(url, body, options = {}) {
    return json(url, { ...options, method: "POST", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, body: JSON.stringify(body) });
  }

  async function downloadProjectContentExport(button) {
    button.disabled = true;
    const plan = seoState.root.querySelector("[data-seo-collect-plan]");
    try {
      if (plan) plan.textContent = "Формирую Excel по всем SKU текущего проекта…";
      const response = await fetch("/api/seo-project-content/export?dashboard=seoMonitoring", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client: seoState.client,
          project_id: seoState.projectId,
          project_kind: seoState.projectKind,
        }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || `HTTP ${response.status}`);
      }
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plain = disposition.match(/filename="?([^";]+)"?/i);
      const filename = encoded ? decodeURIComponent(encoded[1]) : (plain ? plain[1] : "seo_project_export.xlsx");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (plan) plan.textContent = `Excel готов: ${filename}`;
    } catch (error) {
      const message = `Экспорт не сформирован: ${error.message}`;
      if (plan) plan.textContent = message;
      showCollectionFailure("Экспорт XLSX · ошибка", message);
    } finally {
      button.disabled = false;
    }
  }

  function ensureRoot() {
    if (seoState.root?.isConnected) return seoState.root;
    const existing = document.getElementById("seoProjectsWorkspace");
    if (existing) {
      seoState.root = existing;
      return existing;
    }
    const root = document.createElement("section");
    root.id = "seoProjectsWorkspace";
    root.className = "seo-projects-workspace";
    root.setAttribute("aria-label", "SEO-проекты");
    const anchor = document.querySelector(".kpis");
    anchor?.parentElement?.insertBefore(root, anchor);
    seoState.root = root;
    return root;
  }

  function setProjectParam(projectId) {
    const url = new URL(window.location.href);
    if (projectId) url.searchParams.set("seo_project", projectId);
    else url.searchParams.delete("seo_project");
    history.replaceState({}, "", url);
    seoState.projectId = projectId || "";
  }

  function setCreateParam(active) {
    const url = new URL(window.location.href);
    if (active) url.searchParams.set("seo_new", "1");
    else {
      url.searchParams.delete("seo_new");
      url.searchParams.delete("seo_marketplace");
    }
    history.replaceState({}, "", url);
  }

  function setCreateMarketplace(marketplace) {
    const normalized = marketplace === "wb" || marketplace === "yandex_market" ? marketplace : "ozon";
    const url = new URL(window.location.href);
    url.searchParams.set("seo_marketplace", normalized);
    url.searchParams.set("marketplace", normalized);
    history.replaceState({}, "", url);
    seoState.marketplace = normalized;
  }

  const marketplaceLabel = (marketplace) => marketplace === "wb" ? "WB" : marketplace === "yandex_market" ? "Яндекс Маркет" : "Ozon";
  const generationDefaultName = (marketplace) => `${marketplaceLabel(marketplace)} · ${new Date().toLocaleDateString("ru-RU")} · Генерация SEO`;

  function openMarketplaceChooser() {
    setProjectParam("");
    setCreateParam(false);
    const marketplaces = seoState.marketplaces.length ? seoState.marketplaces : ["ozon"];
    seoState.root.innerHTML = `<div class="seo-marketplace-overlay" data-seo-marketplace-overlay><section class="seo-marketplace-dialog" role="dialog" aria-modal="true" aria-labelledby="seoMarketplaceTitle"><button type="button" class="seo-marketplace-close" data-seo-marketplace-cancel aria-label="Закрыть выбор площадки" title="Закрыть"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button><span class="seo-marketplace-overline">Новый проект</span><h2 id="seoMarketplaceTitle">Выберите площадку</h2><p>Проект генерации SEO будет создан только для выбранной площадки.</p><div class="seo-marketplace-options" role="list">${marketplaces.map((marketplace) => `<button type="button" class="seo-marketplace-option" data-seo-marketplace-choice="${esc(marketplace)}"><span>${esc(marketplaceLabel(marketplace))}</span><strong>${esc(generationDefaultName(marketplace))}</strong><small>Открыть выбор товаров →</small></button>`).join("")}</div><button type="button" class="seo-project-btn ghost" data-seo-marketplace-cancel>Вернуться к проектам</button></section></div>`;
    seoState.root.querySelectorAll("[data-seo-marketplace-cancel]").forEach((button) => button.addEventListener("click", () => loadList()));
    seoState.root.querySelectorAll("[data-seo-marketplace-choice]").forEach((button) => button.addEventListener("click", () => {
      setCreateMarketplace(button.dataset.seoMarketplaceChoice);
      openCreateWorkspace(false);
    }));
    seoState.root.querySelector("[data-seo-marketplace-choice]")?.focus();
  }

  function startNewProject() {
    clearBuilderUrlState();
    if (seoState.projectKind === "generation") openMarketplaceChooser();
    else openCreateWorkspace(false);
  }

  function sourceNote(marketplace) {
    if (marketplace === "yandex_market") return "Источник: каталог и наблюдаемые заказы Яндекс Маркета. Неподключённые поисковые метрики остаются «—», а не заменяются данными другой площадки.";
    return marketplace === "wb"
      ? "Источник: дневной отчёт WB по поисковым запросам. Переходы используются как трафик; отсутствующие поля не заменяются нулями."
      : "Источник: Ozon Seller API product-queries/details. Ключи — запросы, по которым карточка была найдена; API возвращает максимум 15 фраз по SKU за запуск.";
  }

  function projectList(rows) {
    if (!rows.length) return `<div class="seo-project-empty"><h3>${esc(workspaceCopy().empty)}</h3><p>Создайте проект, выберите группу SKU и запустите первый сбор поисковых запросов.</p><button class="seo-project-btn primary" data-seo-new>Новый проект</button></div>`;
    return `<div class="seo-project-table-wrap"><table class="seo-project-table"><thead><tr><th>Проект</th><th>MP</th><th>SKU</th><th>Ключи</th><th>Ср. позиция</th><th>Трафик</th><th>Конверсия</th><th>Обновлён</th><th>Статус</th><th></th></tr></thead><tbody>${rows.map((row) => `<tr>
      <td><div class="seo-project-name" data-seo-name-wrap="${esc(row.project_id)}"><div class="seo-project-name-line"><strong data-seo-rename="${esc(row.project_id)}" data-seo-current-name="${esc(row.name)}" title="Дважды нажмите, чтобы переименовать">${esc(row.name)}</strong><button type="button" class="seo-project-delete-btn" data-seo-delete="${esc(row.project_id)}" data-seo-current-name="${esc(row.name)}" aria-label="Удалить проект ${esc(row.name)}" title="Удалить проект"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg></button></div><span>${esc(row.date_from || "—")} — ${esc(row.date_to || "—")}</span></div></td>
      <td>${esc(String(row.marketplace || "").toUpperCase())}</td><td class="number">${num(row.sku_count)}</td><td class="number">${row.has_data ? num(row.keyword_count) : "—"}</td><td class="number">${num(row.average_position, 1)}</td><td class="number">${num(row.traffic)}</td><td class="number">${pct(row.conversion_pct)}</td><td>${dateText(row.last_refreshed_at)}</td>
      <td><span class="seo-project-status ${esc(row.status)}">${esc(statusLabels[row.status] || row.status)}</span></td><td><button class="seo-project-btn ghost" data-seo-open="${esc(row.project_id)}" aria-label="Открыть ${esc(row.name)}">Открыть →</button></td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderList(rows) {
    const copy = workspaceCopy();
    seoState.root.innerHTML = `<header class="seo-projects-head"><div><h2>${esc(copy.title)}</h2><p>${esc(copy.description)}</p></div><div class="seo-projects-actions"><input class="seo-project-filter" type="search" placeholder="Найти проект" data-seo-search><button class="seo-project-btn primary" data-seo-new>Новый проект</button></div></header><div data-seo-list>${projectList(rows)}</div><p class="seo-project-source-note">Разделы мониторинга и генерации независимы. Метрики без данных показываются как «—».</p>`;
    const repaint = () => {
      const query = seoState.root.querySelector("[data-seo-search]")?.value.trim().toLowerCase() || "";
      seoState.root.querySelector("[data-seo-list]").innerHTML = projectList(rows.filter((row) => !query || String(row.name).toLowerCase().includes(query)));
      bindListActions(rows);
    };
    seoState.root.querySelector("[data-seo-search]")?.addEventListener("input", repaint);
    bindListActions(rows);
  }

  function bindListActions(rows) {
    seoState.root.querySelectorAll("[data-seo-open]").forEach((button) => button.addEventListener("click", async () => {
      setProjectParam(button.dataset.seoOpen);
      await loadDetail();
    }));
    seoState.root.querySelectorAll("[data-seo-rename]").forEach((target) => target.addEventListener("dblclick", () => beginProjectRename(target, async () => loadList())));
    seoState.root.querySelectorAll("[data-seo-delete]").forEach((button) => button.addEventListener("click", () => beginProjectDelete(button, async () => loadList())));
    seoState.root.querySelectorAll("[data-seo-new]").forEach((button) => button.addEventListener("click", startNewProject));
  }

  function beginProjectRename(button, onSaved) {
    const projectId = button.dataset.seoRename;
    const currentName = button.dataset.seoCurrentName || "";
    const host = button.closest("[data-seo-name-wrap]");
    if (!host || host.querySelector("[data-seo-rename-input]")) return;
    const original = host.innerHTML;
    host.innerHTML = `<div class="seo-project-rename"><input type="text" maxlength="160" value="${esc(currentName)}" data-seo-rename-input aria-label="Новое название проекта"><button type="button" class="seo-project-rename-action save" data-seo-rename-save aria-label="Сохранить название" title="Сохранить"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg></button><button type="button" class="seo-project-rename-action" data-seo-rename-cancel aria-label="Отменить переименование" title="Отменить"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div><span class="seo-project-rename-error" data-seo-rename-error hidden></span>`;
    const input = host.querySelector("[data-seo-rename-input]");
    const cancel = () => {
      host.innerHTML = original;
      const restored = host.querySelector("[data-seo-rename]");
      restored?.addEventListener("dblclick", () => beginProjectRename(restored, onSaved));
      const restoredDelete = host.querySelector("[data-seo-delete]");
      restoredDelete?.addEventListener("click", () => beginProjectDelete(restoredDelete, onSaved));
    };
    const save = async () => {
      const name = input.value.trim();
      const error = host.querySelector("[data-seo-rename-error]");
      if (!name) { error.hidden = false; error.textContent = "Введите название"; input.focus(); return; }
      host.querySelectorAll("button").forEach((control) => { control.disabled = true; });
      try {
        await post("/api/seo-projects/rename?dashboard=seoMonitoring", { client: seoState.client, project_kind: seoState.projectKind, project_id: projectId, name });
        await onSaved();
      } catch (renameError) {
        error.hidden = false;
        error.textContent = renameError.message;
        host.querySelectorAll("button").forEach((control) => { control.disabled = false; });
      }
    };
    host.querySelector("[data-seo-rename-save]")?.addEventListener("click", save);
    host.querySelector("[data-seo-rename-cancel]")?.addEventListener("click", cancel);
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") save(); if (event.key === "Escape") cancel(); });
    input.focus();
    input.select();
  }

  function beginProjectDelete(button, onDeleted) {
    const projectId = button.dataset.seoDelete;
    const currentName = button.dataset.seoCurrentName || "";
    const host = button.closest("[data-seo-name-wrap]");
    if (!host || host.querySelector("[data-seo-delete-confirm]")) return;
    const original = host.innerHTML;
    host.innerHTML = `<div class="seo-project-delete-confirm"><span>Удалить «${esc(currentName)}» со снимками?</span><button type="button" class="seo-project-rename-action danger" data-seo-delete-confirm aria-label="Подтвердить удаление проекта" title="Удалить"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg></button><button type="button" class="seo-project-rename-action" data-seo-delete-cancel aria-label="Отменить удаление" title="Отменить"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div><span class="seo-project-rename-error" data-seo-delete-error hidden></span>`;
    const cancel = () => {
      host.innerHTML = original;
      const restoredRename = host.querySelector("[data-seo-rename]");
      restoredRename?.addEventListener("dblclick", () => beginProjectRename(restoredRename, async () => loadList()));
      const restoredDelete = host.querySelector("[data-seo-delete]");
      restoredDelete?.addEventListener("click", () => beginProjectDelete(restoredDelete, onDeleted));
    };
    const confirm = async () => {
      const error = host.querySelector("[data-seo-delete-error]");
      host.querySelectorAll("button").forEach((control) => { control.disabled = true; });
      try {
        await post("/api/seo-projects/delete?dashboard=seoMonitoring", { client: seoState.client, project_kind: seoState.projectKind, project_id: projectId });
        if (seoState.projectId === projectId) setProjectParam("");
        await onDeleted();
      } catch (deleteError) {
        error.hidden = false;
        error.textContent = deleteError.message;
        host.querySelectorAll("button").forEach((control) => { control.disabled = false; });
      }
    };
    host.querySelector("[data-seo-delete-confirm]")?.addEventListener("click", confirm);
    host.querySelector("[data-seo-delete-cancel]")?.addEventListener("click", cancel);
    host.querySelector("[data-seo-delete-cancel]")?.focus();
  }

  function renderBuilderSelection() {
    seoState.root.querySelectorAll("[data-seo-selected-count]").forEach((count) => {
      count.textContent = `${num(seoState.builderSelected.size)} SKU`;
    });
    const status = seoState.root.querySelector("[data-seo-selection-status]");
    if (status) status.textContent = seoState.builderSelected.size ? `Выбрано ${num(seoState.builderSelected.size)} SKU` : "SKU не выбраны";
    const clear = seoState.root.querySelector("[data-seo-clear-selection]");
    if (clear) clear.disabled = !seoState.builderSelected.size;
  }

  function catalogFilterFieldsHtml(marketplace = "ozon", locked = false) {
    const marketplaceField = `${locked ? `<input type="hidden" name="marketplace" value="${esc(marketplace)}">` : ""}<label>Площадка<select ${locked ? "disabled" : 'name="marketplace"'} ${locked ? 'title="Площадка проекта задана при создании"' : ""}><option value="ozon" ${marketplace === "ozon" ? "selected" : ""}>Ozon</option><option value="wb" ${marketplace === "wb" ? "selected" : ""}>WB</option><option value="yandex_market" ${marketplace === "yandex_market" ? "selected" : ""}>Яндекс Маркет</option></select></label>`;
    return marketplaceField + `<label>Поиск<input name="query" type="search" placeholder="Название или SKU"></label><label>Категория<select name="catalog_category"><option value="">Все категории</option></select></label><label>Подкатегория<select name="subcategory"><option value="">Все подкатегории</option></select></label><label>Бренд<select name="brand"><option value="">Все бренды</option></select></label><label>Модель<input name="gj_model" type="search" placeholder="Код или название модели"></label><label>Ассортимент<select name="assortment_bia"><option value="">Весь ассортимент</option></select></label><label>Товарная группа<select name="tg"><option value="">Все товарные группы</option></select></label><label>ТГ+<select name="tg_plus"><option value="">Все ТГ+</option></select></label><label>Целевая группа<select name="cg"><option value="">Все ЦГ</option></select></label><label>Пол<select name="gender"><option value="">Все значения</option></select></label><label>Возраст<select name="age"><option value="">Все возрасты</option></select></label><label>Коллекция<select name="collection"><option value="">Все коллекции</option></select></label><label>Сезон<select name="season"><option value="">Все сезоны</option></select></label><label>Стиль<select name="style"><option value="">Все стили</option></select></label><label>Цвет<select name="color"><option value="">Все цвета</option></select></label><label>Материал<select name="material"><option value="">Все материалы</option></select></label><label>Состав материала<select name="material_composition"><option value="">Любой состав</option></select></label><label>Российский размер<select name="russian_size"><option value="">Все размеры</option></select></label><label>Размер производителя<select name="manufacturer_size"><option value="">Все размеры</option></select></label><label>Целевая аудитория<select name="target_audience"><option value="">Все аудитории</option></select></label><label>Остаток<select name="availability"><option value="">Любой остаток</option></select></label><label>Рейтинг от<input name="rating_min" type="number" min="0" max="5" step="0.1" placeholder="Например, 4.5"></label>`;
  }

  const builderFilterNames = [
    "query", "catalog_category", "subcategory", "gj_model", "assortment_bia", "tg", "tg_plus", "cg", "season",
    "brand", "gender", "age", "collection", "style", "color", "material", "material_composition",
    "russian_size", "manufacturer_size", "target_audience", "availability", "rating_min",
  ];

  const builderFilterLabels = {
    query: "Поиск", catalog_category: "Категория", subcategory: "Подкатегория", gj_model: "Модель", assortment_bia: "Ассортимент",
    tg: "Товарная группа", tg_plus: "ТГ+", cg: "Целевая группа", season: "Сезон", brand: "Бренд", gender: "Пол", age: "Возраст",
    collection: "Коллекция", style: "Стиль", color: "Цвет", material: "Материал", material_composition: "Состав материала",
    russian_size: "Российский размер", manufacturer_size: "Размер производителя", target_audience: "Целевая аудитория",
    availability: "Остаток", rating_min: "Рейтинг от",
  };

  const builderUrlParams = {
    filters: "seo_builder_filters", category: "seo_builder_category", categories: "seo_builder_categories", categoryFilters: "seo_builder_category_filters",
    categorySort: "seo_builder_category_sort", skuFilters: "seo_builder_sku_filters", skuSort: "seo_builder_sku_sort", page: "seo_builder_page",
  };

  function parseUrlObject(value) {
    if (!value) return {};
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) { return {}; }
  }

  function validBuilderFilters(value) {
    return Object.fromEntries(builderFilterNames.filter((key) => typeof value?.[key] === "string" && value[key]).map((key) => [key, value[key]]));
  }

  function validColumnFilters(scope, value) {
    const columns = new Map(tableColumns(scope).map((column) => [column.key, column]));
    return Object.fromEntries(Object.entries(value || {}).flatMap(([key, filter]) => {
      const column = columns.get(key);
      if (!column || !filter) return [];
      if (column.type === "enum") {
        const allowed = new Set(column.options.map(([option]) => option));
        const selected = (Array.isArray(filter.value) ? filter.value : [filter.value]).map(String).filter((item) => allowed.has(item));
        return selected.length ? [[key, { op: "in", value: [...new Set(selected)] }]] : [];
      }
      return typeof filter.value === "string" && filter.value ? [[key, { op: String(filter.op || "eq"), value: filter.value }]] : [];
    }));
  }

  function validSort(scope, value, fallback) {
    const keys = new Set(tableColumns(scope).map((column) => column.key));
    return value && keys.has(value.key) ? { key: value.key, dir: value.dir === "asc" ? "asc" : "desc" } : fallback;
  }

  function restoreBuilderUrlState() {
    const params = new URL(window.location.href).searchParams;
    seoState.builderAppliedFilters = validBuilderFilters(parseUrlObject(params.get(builderUrlParams.filters)));
    seoState.builderActiveCategory = params.get(builderUrlParams.category) || "";
    const restoredCategories = parseUrlObject(params.get(builderUrlParams.categories));
    seoState.builderAppliedCategories = new Set(Array.isArray(restoredCategories.values) ? restoredCategories.values.map(String).filter(Boolean) : []);
    seoState.builderPendingCategories = new Set(seoState.builderAppliedCategories);
    seoState.builderCategoryFilters = validColumnFilters("category", parseUrlObject(params.get(builderUrlParams.categoryFilters)));
    seoState.builderSkuFilters = validColumnFilters("sku", parseUrlObject(params.get(builderUrlParams.skuFilters)));
    seoState.builderCategorySort = validSort("category", parseUrlObject(params.get(builderUrlParams.categorySort)), seoState.builderCategorySort);
    seoState.builderSkuSort = validSort("sku", parseUrlObject(params.get(builderUrlParams.skuSort)), seoState.builderSkuSort);
    seoState.builderPage = Math.max(1, Number.parseInt(params.get(builderUrlParams.page) || "1", 10) || 1);
  }

  function syncBuilderUrlState() {
    const url = new URL(window.location.href);
    if (url.searchParams.get("seo_new") !== "1") return;
    seoState.builderAppliedFilters = validBuilderFilters(seoState.builderAppliedFilters);
    const setJson = (key, value, empty) => empty ? url.searchParams.delete(key) : url.searchParams.set(key, JSON.stringify(value));
    setJson(builderUrlParams.filters, seoState.builderAppliedFilters, !Object.keys(seoState.builderAppliedFilters).length);
    seoState.builderActiveCategory ? url.searchParams.set(builderUrlParams.category, seoState.builderActiveCategory) : url.searchParams.delete(builderUrlParams.category);
    setJson(builderUrlParams.categories, { values: [...seoState.builderAppliedCategories] }, !seoState.builderAppliedCategories.size);
    setJson(builderUrlParams.categoryFilters, seoState.builderCategoryFilters, !Object.keys(seoState.builderCategoryFilters).length);
    setJson(builderUrlParams.skuFilters, seoState.builderSkuFilters, !Object.keys(seoState.builderSkuFilters).length);
    setJson(builderUrlParams.categorySort, seoState.builderCategorySort, seoState.builderCategorySort.key === "sku_count" && seoState.builderCategorySort.dir === "desc");
    setJson(builderUrlParams.skuSort, seoState.builderSkuSort, seoState.builderSkuSort.key === "sales_14d_rub" && seoState.builderSkuSort.dir === "desc");
    seoState.builderPage > 1 ? url.searchParams.set(builderUrlParams.page, String(seoState.builderPage)) : url.searchParams.delete(builderUrlParams.page);
    history.replaceState({}, "", url);
  }

  function builderStateSnapshot() {
    return {
      filters: validBuilderFilters(seoState.builderAppliedFilters),
      category: seoState.builderActiveCategory || "",
      categories: [...seoState.builderAppliedCategories],
      category_filters: seoState.builderCategoryFilters,
      sku_filters: seoState.builderSkuFilters,
      category_sort: seoState.builderCategorySort,
      sku_sort: seoState.builderSkuSort,
    };
  }

  function applyBuilderStateSnapshot(state) {
    if (!state || typeof state !== "object") return;
    seoState.builderAppliedFilters = validBuilderFilters(state.filters);
    seoState.builderActiveCategory = typeof state.category === "string" ? state.category : "";
    seoState.builderAppliedCategories = new Set(Array.isArray(state.categories) ? state.categories.map(String).filter(Boolean) : []);
    seoState.builderPendingCategories = new Set(seoState.builderAppliedCategories);
    seoState.builderCategoryFilters = validColumnFilters("category", state.category_filters);
    seoState.builderSkuFilters = validColumnFilters("sku", state.sku_filters);
    seoState.builderCategorySort = validSort("category", state.category_sort, seoState.builderCategorySort);
    seoState.builderSkuSort = validSort("sku", state.sku_sort, seoState.builderSkuSort);
    seoState.builderPage = 1;
  }

  function clearBuilderUrlState() {
    const url = new URL(window.location.href);
    Object.values(builderUrlParams).forEach((key) => url.searchParams.delete(key));
    history.replaceState({}, "", url);
  }

  const seoSignalFilterOptions = [["high", "SEO нужно"], ["attention", "Проверить SEO"], ["no_signal", "Без сигнала"], ["no_stock", "Нет остатка"], ["no_data", "Нет данных"]];

  const builderTableColumns = {
    category: [
      { key: "category_name", label: "Категория", type: "text" },
      { key: "sku_count", label: "SKU", type: "number" },
      { key: "total_stock_qty", label: "Остаток", type: "number" },
      { key: "orders_14d", label: "Заказы 14 д", type: "number" },
      { key: "sales_14d_rub", label: "Продажи 14 д", type: "number" },
      { key: "average_position_14d", label: "Ср. позиция 14 д", type: "number" },
      { key: "orders_trend_pct", label: "Динамика заказов", type: "number" },
      { key: "seo_signal", label: "SEO-сигнал", type: "enum", options: seoSignalFilterOptions },
      { key: "average_card_rating", label: "Рейтинг карточки", type: "number" },
      { key: "average_review_rating", label: "По отзывам", type: "number" },
    ],
    sku: [
      { key: "product_name", label: "Товар", type: "text" },
      { key: "sku", label: "SKU", type: "text" },
      { key: "gj_model", label: "Модель", type: "text" },
      { key: "total_stock_qty", label: "Остаток", type: "number" },
      { key: "orders_14d", label: "Заказы 14 д", type: "number" },
      { key: "sales_14d_rub", label: "Продажи 14 д", type: "number" },
      { key: "average_position_14d", label: "Ср. позиция 14 д", type: "number" },
      { key: "orders_trend_pct", label: "Динамика заказов", type: "number" },
      { key: "seo_signal", label: "SEO-сигнал", type: "enum", options: seoSignalFilterOptions },
      { key: "reyting_kartochki", label: "Рейтинг карточки", type: "number" },
      { key: "reyting_po_otzyvam", label: "По отзывам", type: "number" },
    ],
  };

  builderTableColumns.project = [
    ...builderTableColumns.sku,
    { key: "api_keyword_count", label: "Ключи API", type: "number" },
    { key: "mpstats_keyword_count", label: "Ключи MPStats", type: "number" },
    { key: "niche_name", label: "Ниша", type: "text" },
    { key: "search_intent", label: "Интент", type: "text" },
    { key: "niche_competitor_count", label: "Конкуренты по нише", type: "number" },
    { key: "competitor_keyword_count", label: "Ключи конкурентов", type: "number" },
    { key: "review_count", label: "Свои отзывы", type: "number" },
    { key: "question_count", label: "Свои вопросы", type: "number" },
    { key: "competitor_review_count", label: "Отзывы конкурентов", type: "number" },
    { key: "competitor_question_status", label: "Вопросы конкурентов", type: "action" },
    { key: "customer_voice_claim_count", label: "SEO-клеймы", type: "number" },
    { key: "product_card", label: "Характеристики", type: "action" },
  ];

  builderTableColumns.projectWb = [
    { key: "product_name", label: "Товар", type: "text" },
    { key: "sku", label: "SKU", type: "text" },
    { key: "gj_model", label: "Модель", type: "text" },
    { key: "niche_name", label: "Ниша", type: "text" },
    { key: "search_intent", label: "Интент", type: "text" },
    { key: "seo_signal", label: "SEO-сигнал", type: "enum", options: seoSignalFilterOptions },
    { key: "api_keyword_count", label: "Ключи WB", type: "number" },
    { key: "mpstats_keyword_count", label: "Ключи MPStats", type: "number" },
    { key: "review_count", label: "Отзывы", type: "number" },
    { key: "question_count", label: "Вопросы", type: "number" },
    { key: "niche_competitor_count", label: "Конкуренты", type: "number" },
    { key: "competitor_keyword_count", label: "Ключи конкурентов", type: "number" },
    { key: "competitor_review_count", label: "Отзывы конкурентов", type: "number" },
    { key: "customer_voice_claim_count", label: "SEO-клеймы", type: "number" },
    { key: "product_card", label: "Характеристики", type: "action" },
    { key: "total_stock_qty", label: "Остаток", type: "number" },
    { key: "orders_14d", label: "Заказы 14 д", type: "number" },
    { key: "sales_14d_rub", label: "Продажи 14 д", type: "number" },
    { key: "average_position_14d", label: "Ср. позиция 14 д", type: "number" },
    { key: "orders_trend_pct", label: "Динамика заказов", type: "number" },
    { key: "reyting_kartochki", label: "Рейтинг карточки", type: "number" },
    { key: "reyting_po_otzyvam", label: "По отзывам", type: "number" },
  ];

  function projectMarketplace() {
    return String(seoState.detail?.project?.marketplace || seoState.marketplace || "").toLowerCase();
  }

  function tableColumns(scope, marketplace = "") {
    if (scope === "project" && String(marketplace || projectMarketplace()).toLowerCase() === "wb") return builderTableColumns.projectWb;
    return builderTableColumns[scope];
  }

  const textFilterOperators = [["contains", "содержит"], ["not_contains", "не содержит"], ["eq", "равно"], ["neq", "не равно"]];
  const numberFilterOperators = [["eq", "равно"], ["neq", "не равно"], ["gt", "больше"], ["gte", "больше или равно"], ["lt", "меньше"], ["lte", "меньше или равно"]];

  function builderTableState(scope) {
    if (scope === "category") return { sort: seoState.builderCategorySort, filters: seoState.builderCategoryFilters };
    if (scope === "project") return { sort: seoState.projectSkuSort, filters: seoState.projectSkuFilters };
    return { sort: seoState.builderSkuSort, filters: seoState.builderSkuFilters };
  }

  function columnFilterValues(filter) {
    if (Array.isArray(filter?.value)) return filter.value.map(String).filter(Boolean);
    return filter?.value ? [String(filter.value)] : [];
  }

  function builderTableHead(scope, marketplace = "") {
    const state = builderTableState(scope);
    const master = scope === "project"
      ? ""
      : scope === "category"
        ? '<th class="seo-check-col"><input type="checkbox" data-seo-category-master aria-label="Отметить все видимые категории" title="Отметить все видимые категории"></th>'
        : '<th class="seo-check-col"><input type="checkbox" data-seo-sku-master aria-label="Выбрать все SKU на странице" title="Выбрать все SKU на странице"></th>';
    const cells = tableColumns(scope, marketplace).map((column) => {
      if (column.type === "action") return `<th class="seo-table-head-cell seo-table-action-head" data-seo-head="${scope}:${column.key}"><span>${esc(column.label)}</span></th>`;
      const active = state.sort.key === column.key;
      const filter = state.filters[column.key];
      const operators = column.type === "number" ? numberFilterOperators : textFilterOperators;
      const defaultOp = column.type === "number" ? "eq" : "contains";
      const selectedValues = columnFilterValues(filter);
      const hasFilter = selectedValues.length > 0;
      const filterFields = column.type === "enum"
        ? `<span class="seo-enum-filter-hint">Выберите один или несколько</span><div class="seo-enum-filter-options">${column.options.map(([value, label]) => `<label><input type="checkbox" data-seo-filter-value value="${esc(value)}" ${selectedValues.includes(value) ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")}</div>`
        : `<label>Условие<select data-seo-filter-op>${operators.map(([value, label]) => `<option value="${value}" ${(filter?.op || defaultOp) === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>Значение<input data-seo-filter-value type="${column.type === "number" ? "number" : "text"}" ${column.type === "number" ? 'step="any" inputmode="decimal"' : ""} value="${esc(filter?.value || "")}" placeholder="Значение"></label>`;
      return `<th class="seo-table-head-cell ${column.type === "number" ? "number" : ""} ${active ? "is-sorted" : ""} ${hasFilter ? "is-filtered" : ""}" data-seo-head="${scope}:${column.key}" aria-sort="${active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none"}"><div class="seo-table-head-control"><button type="button" class="seo-table-sort" data-seo-table-sort="${scope}:${column.key}" title="Сортировать по «${esc(column.label)}»"><span>${esc(column.label)}</span><svg data-seo-sort-icon viewBox="0 0 16 16" aria-hidden="true"><path d="M5 6l3-3 3 3M11 10l-3 3-3-3"/></svg></button><button type="button" class="seo-table-filter-trigger ${hasFilter ? "active" : ""}" data-seo-filter-trigger="${scope}:${column.key}" aria-label="Фильтр по столбцу ${esc(column.label)}" title="Фильтр столбца" aria-expanded="false"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12L9.5 8v4l-3 1V8z"/></svg></button><div class="seo-table-filter-popover" data-seo-filter-popover="${scope}:${column.key}" hidden><strong>${esc(column.label)}</strong>${filterFields}<div><button type="button" class="seo-project-btn ghost" data-seo-filter-clear="${scope}:${column.key}">Сбросить</button><button type="button" class="seo-project-btn primary" data-seo-filter-apply="${scope}:${column.key}">Применить</button></div></div></div></th>`;
    }).join("");
    return `<thead><tr>${master}${cells}</tr></thead>`;
  }

  function categoryComparableValue(row, key) {
    if (key === "seo_signal") return row.seo_signal || "no_data";
    return row[key];
  }

  function matchesColumnFilter(value, filter, type) {
    if (!columnFilterValues(filter).length) return true;
    if (type === "enum") return columnFilterValues(filter).includes(String(value ?? ""));
    if (type === "number") {
      const left = Number(value), right = Number(String(filter.value).replace(",", "."));
      if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
      return ({ eq: left === right, neq: left !== right, gt: left > right, gte: left >= right, lt: left < right, lte: left <= right })[filter.op] ?? true;
    }
    const left = String(value ?? "").toLocaleLowerCase("ru-RU");
    const right = String(filter.value).toLocaleLowerCase("ru-RU");
    return ({ contains: left.includes(right), not_contains: !left.includes(right), eq: left === right, neq: left !== right })[filter.op] ?? true;
  }

  function categoryRowsForDisplay() {
    const columns = builderTableColumns.category;
    const filters = seoState.builderCategoryFilters;
    const rows = [...(seoState.builderCandidates?.category_rows || [])].filter((row) => columns.every((column) => matchesColumnFilter(categoryComparableValue(row, column.key), filters[column.key], column.type)));
    const { key, dir } = seoState.builderCategorySort;
    const column = columns.find((item) => item.key === key) || columns[0];
    rows.sort((leftRow, rightRow) => {
      const left = categoryComparableValue(leftRow, column.key), right = categoryComparableValue(rightRow, column.key);
      if (left === null || left === undefined || left === "") return 1;
      if (right === null || right === undefined || right === "") return -1;
      const compared = column.type === "number" ? Number(left) - Number(right) : String(left).localeCompare(String(right), "ru", { numeric: true, sensitivity: "base" });
      return dir === "asc" ? compared : -compared;
    });
    return rows;
  }

  function refreshBuilderTable(scope) {
    if (scope === "category") renderCategoryTable();
    else if (scope === "project") loadProjectSkus(1);
    else loadCandidates(1);
  }

  function renderBuilderTableHead(scope) {
    const table = seoState.root.querySelector(`[data-seo-table="${scope}"]`);
    const current = table?.querySelector("thead");
    if (!current) return;
    current.outerHTML = builderTableHead(scope);
    bindBuilderTableHeader(scope);
  }

  function bindBuilderTableHeader(scope) {
    const table = seoState.root.querySelector(`[data-seo-table="${scope}"]`);
    if (!table) return;
    table.querySelectorAll(`[data-seo-table-sort^="${scope}:"]`).forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.seoTableSort.split(":", 2)[1];
      const state = builderTableState(scope);
      if (state.sort.key === key) state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
      else { state.sort.key = key; state.sort.dir = "asc"; }
      syncBuilderUrlState();
      renderBuilderTableHead(scope);
      refreshBuilderTable(scope);
    }));
    table.querySelectorAll(`[data-seo-filter-trigger^="${scope}:"]`).forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.seoFilterTrigger;
      table.querySelectorAll("[data-seo-filter-popover]").forEach((panel) => { if (panel.dataset.seoFilterPopover !== key) panel.hidden = true; });
      const panel = table.querySelector(`[data-seo-filter-popover="${key}"]`);
      if (panel) panel.hidden = !panel.hidden;
      button.setAttribute("aria-expanded", String(panel ? !panel.hidden : false));
    }));
    table.querySelectorAll(`[data-seo-filter-apply^="${scope}:"]`).forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.seoFilterApply.split(":", 2)[1];
      const panel = button.closest("[data-seo-filter-popover]");
      const state = builderTableState(scope);
      const column = tableColumns(scope).find((item) => item.key === key);
      if (column?.type === "enum") {
        const values = [...panel.querySelectorAll("[data-seo-filter-value]:checked")].map((input) => input.value);
        if (values.length) state.filters[key] = { op: "in", value: values };
        else delete state.filters[key];
      } else {
        const value = panel?.querySelector("[data-seo-filter-value]")?.value.trim() || "";
        const op = panel?.querySelector("[data-seo-filter-op]")?.value || "eq";
        if (value) state.filters[key] = { op, value };
        else delete state.filters[key];
      }
      syncBuilderUrlState();
      renderBuilderTableHead(scope);
      refreshBuilderTable(scope);
    }));
    table.querySelectorAll(`[data-seo-filter-clear^="${scope}:"]`).forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.seoFilterClear.split(":", 2)[1];
      delete builderTableState(scope).filters[key];
      syncBuilderUrlState();
      renderBuilderTableHead(scope);
      refreshBuilderTable(scope);
    }));
    const master = table.querySelector(scope === "category" ? "[data-seo-category-master]" : "[data-seo-sku-master]");
    master?.addEventListener("change", (event) => {
      if (scope === "category") {
        (seoState.builderVisibleCategories || []).map((row) => row.category_name).forEach((category) => {
          if (event.target.checked) seoState.builderPendingCategories.add(category);
          else seoState.builderPendingCategories.delete(category);
        });
        renderCategoryTable();
        return;
      }
      const pageRows = seoState.builderCandidates?.rows || [];
      pageRows.forEach((row) => {
        if (event.target.checked) seoState.builderSelected.set(String(row.sku), { ...row, found: true, selection_source: "sku" });
        else seoState.builderSelected.delete(String(row.sku));
      });
      renderBuilderSelection(); renderCandidateTable(); renderCategoryTable();
    });
  }

  function trendCell(row, key = "orders") {
    const direction = row[`${key}_trend_direction`] || "no_data";
    const delta = row[`${key}_trend_pct`];
    const labels = { up: "Растёт", down: "Снижается", stable: "Без изменений", no_data: "Нет данных" };
    const marks = { up: "↑", down: "↓", stable: "→", no_data: "—" };
    const comparison = delta === null || delta === undefined ? "" : ` · ${delta > 0 ? "+" : ""}${num(delta, 1)}%`;
    return `<span class="seo-metric-trend ${esc(direction)}" title="Текущие 7 дней к предыдущим 7 дням">${marks[direction]} ${labels[direction]}${comparison}</span>`;
  }

  function seoSignalCell(row) {
    const labels = { high: "SEO нужно", attention: "Проверить SEO", no_signal: "Без сигнала", no_stock: "Нет остатка", no_queries: "Нет запросов", no_data: "Нет данных" };
    return `<span class="seo-signal ${esc(row.seo_signal || "no_data")}" title="${esc(row.seo_signal_reason || "Нет данных для оценки")}">${esc(labels[row.seo_signal] || labels.no_data)}</span>`;
  }

  function optionList(values, selected = "", emptyLabel = "Все") {
    return `<option value="">${esc(emptyLabel)}</option>${(values || []).map((value) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(value)}</option>`).join("")}`;
  }

  function builderFilterParams(extra = {}) {
    const result = { ...seoState.builderAppliedFilters, category: seoState.builderActiveCategory, ...extra };
    if (seoState.builderAppliedCategories.size) result.categories = JSON.stringify([...seoState.builderAppliedCategories]);
    if (Object.prototype.hasOwnProperty.call(result, "query")) {
      result.q = result.query;
      delete result.query;
    }
    return result;
  }

  function updateCategorySelectionUi(message = "") {
    const categories = seoState.builderVisibleCategories || [];
    const visibleNames = categories.map((row) => row.category_name);
    seoState.root.querySelectorAll("[data-seo-category-check]").forEach((input) => {
      input.checked = seoState.builderPendingCategories.has(input.dataset.seoCategoryCheck);
      input.indeterminate = false;
    });
    const selectedVisible = visibleNames.filter((name) => seoState.builderPendingCategories.has(name)).length;
    const master = seoState.root.querySelector("[data-seo-category-master]");
    if (master) {
      master.checked = visibleNames.length > 0 && selectedVisible === visibleNames.length;
      master.indeterminate = selectedVisible > 0 && selectedVisible < visibleNames.length;
    }
    const status = seoState.root.querySelector("[data-seo-category-selection-status]");
    const selectionApplied = seoState.builderPendingCategories.size === seoState.builderAppliedCategories.size
      && [...seoState.builderPendingCategories].every((name) => seoState.builderAppliedCategories.has(name));
    if (status) status.textContent = message || (seoState.builderPendingCategories.size
      ? (selectionApplied ? `Фильтр категорий: ${num(seoState.builderAppliedCategories.size)}` : `Выбрано для фильтра: ${num(seoState.builderPendingCategories.size)} · нажмите «Применить»`)
      : "Категории не выбраны");
  }

  function renderCategoryTable() {
    const target = seoState.root.querySelector("[data-seo-category-rows]");
    const data = seoState.builderCandidates;
    if (!target || !data) return;
    const visibleRows = categoryRowsForDisplay();
    seoState.builderVisibleCategories = visibleRows;
    target.innerHTML = visibleRows.map((row) => {
      const checked = seoState.builderPendingCategories.has(row.category_name);
      return `<tr class="${row.category_name === seoState.builderActiveCategory || seoState.builderAppliedCategories.has(row.category_name) ? "is-active" : ""}" data-seo-category-row="${esc(row.category_name)}"><td><input type="checkbox" data-seo-category-check="${esc(row.category_name)}" ${checked ? "checked" : ""} aria-label="Фильтровать SKU по категории ${esc(row.category_name)}"></td><td><button type="button" class="seo-category-link" data-seo-category-filter="${esc(row.category_name)}">${esc(row.category_name)}</button></td><td class="number">${num(row.sku_count)}</td><td class="number">${num(row.total_stock_qty)}</td><td class="number">${num(row.orders_14d)}</td><td class="number">${rub(row.sales_14d_rub)}</td><td class="number">${num(row.average_position_14d, 1)}</td><td>${trendCell(row)}</td><td>${seoSignalCell(row)}</td><td class="number">${num(row.average_card_rating, 1)}</td><td class="number">${num(row.average_review_rating, 1)}</td></tr>`;
    }).join("") || `<tr><td colspan="11">${esc(data.catalog_missing_reason || "Категории по выбранным фильтрам не найдены")}</td></tr>`;
    target.querySelectorAll("[data-seo-category-filter]").forEach((button) => button.addEventListener("click", async () => {
      seoState.builderAppliedCategories.clear();
      seoState.builderPendingCategories.clear();
      seoState.builderActiveCategory = seoState.builderActiveCategory === button.dataset.seoCategoryFilter ? "" : button.dataset.seoCategoryFilter;
      syncBuilderUrlState();
      await loadCandidates(1);
    }));
    target.querySelectorAll("[data-seo-category-check]").forEach((input) => input.addEventListener("change", () => {
      const category = input.dataset.seoCategoryCheck;
      if (input.checked) seoState.builderPendingCategories.add(category);
      else seoState.builderPendingCategories.delete(category);
      updateCategorySelectionUi();
    }));
    updateCategorySelectionUi();
    const meta = seoState.root.querySelector("[data-seo-category-meta]");
    if (meta) meta.textContent = visibleRows.length === (data.category_rows || []).length
      ? `${num(visibleRows.length)} категорий`
      : `${num(visibleRows.length)} из ${num((data.category_rows || []).length)} категорий`;
  }

  function renderCandidateTable() {
    const target = seoState.root.querySelector("[data-seo-catalog-rows]");
    const data = seoState.builderCandidates;
    if (!target || !data) return;
    target.innerHTML = (data.rows || []).map((row) => { const product = row.product_name || "Без названия"; const category = `${row.category_name || "—"} · ${row.subcategory_name || "—"}`; const model = row.gj_model || "—"; return `<tr><td><input type="checkbox" data-seo-candidate value="${esc(row.sku)}" ${seoState.builderSelected.has(String(row.sku)) ? "checked" : ""} aria-label="Выбрать SKU ${esc(row.sku)}"></td><td class="seo-builder-product"><strong title="${esc(product)}">${esc(product)}</strong><span title="${esc(category)}">${esc(category)}</span></td><td class="seo-builder-sku" title="${esc(row.sku)}">${esc(row.sku)}</td><td title="${esc(model)}">${esc(model)}</td><td class="number">${num(row.total_stock_qty)}</td><td class="number">${num(row.orders_14d)}</td><td class="number">${rub(row.sales_14d_rub)}</td><td class="number">${num(row.average_position_14d, 1)}</td><td>${trendCell(row)}</td><td>${seoSignalCell(row)}</td><td class="number">${num(row.reyting_kartochki, 1)}</td><td class="number">${num(row.reyting_po_otzyvam, 1)}</td></tr>`; }).join("") || `<tr><td colspan="12">${esc(data.catalog_missing_reason || "По выбранным фильтрам товары не найдены")}</td></tr>`;
    target.querySelectorAll("[data-seo-candidate]").forEach((input) => input.addEventListener("change", () => {
      const row = (data.rows || []).find((item) => String(item.sku) === input.value);
      if (input.checked && row) seoState.builderSelected.set(input.value, { ...row, found: true, selection_source: "sku" });
      else seoState.builderSelected.delete(input.value);
      renderBuilderSelection();
      renderCandidateTable();
      renderCategoryTable();
    }));
    const pageRows = data.rows || [];
    const selectedOnPage = pageRows.filter((row) => seoState.builderSelected.has(String(row.sku))).length;
    const master = seoState.root.querySelector("[data-seo-sku-master]");
    if (master) {
      master.checked = pageRows.length > 0 && selectedOnPage === pageRows.length;
      master.indeterminate = selectedOnPage > 0 && selectedOnPage < pageRows.length;
    }
    const meta = seoState.root.querySelector("[data-seo-catalog-meta]");
    if (meta) meta.textContent = `Найдено ${num(data.total)} · страница ${num(data.page)} из ${num(data.total_pages)}`;
    const prev = seoState.root.querySelector("[data-seo-page-prev]");
    const next = seoState.root.querySelector("[data-seo-page-next]");
    if (prev) prev.disabled = data.page <= 1;
    if (next) next.disabled = data.page >= data.total_pages;
  }

  function populateBuilderFilterOptions(data) {
    const form = seoState.root.querySelector("[data-seo-create-form]");
    if (!form || !data) return;
    const allSubcategories = [...new Set(Object.values(data.subcategories_by_category || {}).flat())].sort();
    const options = { subcategory: allSubcategories, ...(data.filter_options || {}) };
    options.catalog_category = data.category_values || [];
    const labels = { catalog_category: "Все категории", subcategory: "Все подкатегории", gj_model: "Все модели", assortment_bia: "Весь ассортимент", tg: "Все товарные группы", tg_plus: "Все ТГ+", cg: "Все ЦГ", season: "Все сезоны", brand: "Все бренды", gender: "Все значения", age: "Все возрасты", collection: "Все коллекции", style: "Все стили", color: "Все цвета", material: "Все материалы", material_composition: "Любой состав", russian_size: "Все размеры", manufacturer_size: "Все размеры", target_audience: "Все аудитории", availability: "Любой остаток" };
    Object.entries(options).forEach(([name, values]) => {
      if (form[name]) form[name].innerHTML = optionList(values, seoState.builderAppliedFilters[name] || form[name].value, labels[name] || "Все");
    });
    if (form.availability) {
      form.availability.querySelector('option[value="in_stock"]')?.replaceChildren("В наличии");
      form.availability.querySelector('option[value="out_of_stock"]')?.replaceChildren("Нет остатка");
    }
    applyMetricSourceText(data.metric_sources);
  }

  function applyMetricSourceText(sources) {
    const source = sources || {};
    seoState.root.querySelectorAll("[data-seo-metric-source]").forEach((node) => {
      node.textContent = `Заказы и продажи по ${shortDate(source.funnel_date_to)} · позиции по ${shortDate(source.position_date_to)}`;
    });
  }

  async function fetchFilteredCandidates(extra = {}) {
    const form = seoState.root.querySelector("[data-seo-create-form]");
    const params = new URLSearchParams({ client: seoState.client, marketplace: form.marketplace.value, dashboard: "seoMonitoring", selection: "1", ...builderFilterParams(extra) });
    return json(`/api/seo-project-candidates?${params}`);
  }

  async function applyPendingCategories() {
    const status = seoState.root.querySelector("[data-seo-category-selection-status]");
    const applyButton = seoState.root.querySelector("[data-seo-category-apply]");
    try {
      if (applyButton) applyButton.disabled = true;
      if (status) status.textContent = "Применяю фильтр категорий…";
      seoState.builderAppliedCategories = new Set(seoState.builderPendingCategories);
      seoState.builderActiveCategory = "";
      syncBuilderUrlState();
      await loadCandidates(1);
      updateCategorySelectionUi(seoState.builderAppliedCategories.size
        ? `Фильтр категорий: ${num(seoState.builderAppliedCategories.size)}`
        : "Фильтр категорий не задан");
    } catch (error) {
      if (status) status.textContent = error.message;
      updateCategorySelectionUi(error.message);
    } finally {
      if (applyButton) applyButton.disabled = false;
    }
  }

  function resetPendingCategories() {
    seoState.builderPendingCategories.clear();
    seoState.builderAppliedCategories.clear();
    seoState.builderActiveCategory = "";
    syncBuilderUrlState();
    loadCandidates(1);
    updateCategorySelectionUi("Фильтр категорий сброшен");
  }

  async function selectAllFiltered() {
    const button = seoState.root.querySelector("[data-seo-select-all-filtered]");
    const status = seoState.root.querySelector("[data-seo-selection-status]");
    const form = seoState.root.querySelector("[data-seo-create-form]");
    if (button) button.disabled = true;
    if (status) status.textContent = "Выбираю все SKU по фильтру…";
    const params = new URLSearchParams({
      client: seoState.client, marketplace: form?.marketplace?.value || seoState.marketplace,
      dashboard: "seoMonitoring", selection: "1", ...builderFilterParams(),
      sort_col: seoState.builderSkuSort.key, sort_dir: seoState.builderSkuSort.dir,
      column_filters: JSON.stringify(seoState.builderSkuFilters),
    });
    try {
      const data = await json(`/api/seo-project-candidates?${params}`);
      const rows = data.rows || [];
      seoState.builderSelected = new Map(rows.map((row) => [String(row.sku), { ...row, found: true, selection_source: "filter" }]));
      renderBuilderSelection();
      renderCandidateTable();
      renderCategoryTable();
      if (status) status.textContent = `Выбрано ${num(rows.length)} SKU — все найденные по фильтру`;
    } catch (error) {
      if (status) status.textContent = `Не удалось выбрать все SKU: ${error.message}`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function loadCandidates(page = 1) {
    seoState.builderPage = Math.max(1, Number.parseInt(page, 10) || 1);
    syncBuilderUrlState();
    const rows = seoState.root.querySelector("[data-seo-catalog-rows]");
    const categories = seoState.root.querySelector("[data-seo-category-rows]");
    if (rows) rows.innerHTML = '<tr><td colspan="12">Загружаю SKU и метрики…</td></tr>';
    if (categories) categories.innerHTML = '<tr><td colspan="11">Загружаю категории и метрики…</td></tr>';
    const form = seoState.root.querySelector("[data-seo-create-form]");
    const params = new URLSearchParams({
      client: seoState.client, marketplace: form?.marketplace?.value || seoState.marketplace,
      dashboard: "seoMonitoring", limit: "50", page: String(seoState.builderPage), ...builderFilterParams(),
      sort_col: seoState.builderSkuSort.key, sort_dir: seoState.builderSkuSort.dir,
      column_filters: JSON.stringify(seoState.builderSkuFilters),
    });
    try {
      seoState.builderCandidates = await json(`/api/seo-project-candidates?${params}`);
      seoState.builderPage = seoState.builderCandidates.page || 1;
      syncBuilderUrlState();
      populateBuilderFilterOptions(seoState.builderCandidates);
      renderCategoryTable();
      renderCandidateTable();
    } catch (error) {
      if (rows) rows.innerHTML = `<tr><td colspan="12">Не удалось загрузить каталог: ${esc(error.message)}</td></tr>`;
      if (categories) categories.innerHTML = `<tr><td colspan="11">Не удалось загрузить категории</td></tr>`;
    }
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
      reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
      reader.readAsDataURL(file);
    });
  }

  async function parseTemplateFile(file, statusSelector = "[data-seo-upload-status]", previewSelector = "[data-seo-upload-preview]") {
    const status = seoState.root.querySelector(statusSelector);
    if (!file) return;
    if (!/\.(xlsx|csv)$/i.test(file.name)) { if (status) { status.className = "seo-upload-status error"; status.textContent = "Поддерживаются только XLSX и CSV"; } return; }
    if (status) { status.className = "seo-upload-status active"; status.textContent = `Проверяю ${file.name}…`; }
    try {
      const form = seoState.root.querySelector("[data-seo-create-form]");
      const data = await post("/api/seo-project-template/parse?dashboard=seoMonitoring", { client: seoState.client, marketplace: form.marketplace.value, filename: file.name, file_base64: await fileToBase64(file) });
      const newRows = (data.rows || []).filter((row) => !seoState.builderSelected.has(String(row.sku)));
      newRows.forEach((row) => seoState.builderSelected.set(String(row.sku), { ...row, selection_source: "file" }));
      if (status) { status.className = `seo-upload-status ${data.not_found_count ? "warning" : "done"}`; status.textContent = `Загружено ${num(data.row_count)} SKU · найдено ${num(data.found_count)} · не найдено ${num(data.not_found_count)} · дублей ${num((data.duplicates || []).length)} · ошибок строк ${num((data.invalid || []).length)}`; }
      const preview = seoState.root.querySelector(previewSelector);
      if (preview) preview.innerHTML = (data.rows || []).slice(0, 100).map((row) => `<tr><td>${num(row.row)}</td><td class="seo-builder-sku">${esc(row.sku)}</td><td>${esc(row.product_name || "—")}</td><td><span class="seo-template-state ${row.found ? "found" : "missing"}">${row.found ? "Найден" : "Не найден"}</span></td></tr>`).join("");
      renderBuilderSelection();
      renderCandidateTable();
      renderCategoryTable();
    } catch (error) {
      if (status) { status.className = "seo-upload-status error"; status.textContent = error.message; }
    }
  }

  function setBuilderSourceMode(mode) {
    seoState.root.querySelectorAll("[data-seo-source-tab]").forEach((button) => {
      const active = button.dataset.seoSourceTab === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    seoState.root.querySelectorAll("[data-seo-source-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.seoSourcePanel !== mode;
    });
  }

  async function parseSkuList() {
    const input = seoState.root.querySelector("[data-seo-list-input]");
    const status = seoState.root.querySelector("[data-seo-list-status]");
    const skus = [...new Set(String(input?.value || "").split(/[\s,;]+/).map((value) => value.trim()).filter(Boolean))];
    if (!skus.length) {
      if (status) { status.className = "seo-upload-status error"; status.textContent = "Вставьте хотя бы один SKU"; }
      input?.focus();
      return;
    }
    const csv = `sku\n${skus.map((sku) => `"${sku.replaceAll('"', '""')}"`).join("\n")}`;
    await parseTemplateFile(new File([csv], "sku-list.csv", { type: "text/csv;charset=utf-8" }), "[data-seo-list-status]", "[data-seo-list-preview]");
  }

  function openCreateWorkspace(restoreFromUrl = false, editProject = null) {
    setCreateParam(true);
    if (!editProject) setProjectParam("");
    seoState.builderEditProjectId = editProject?.project?.project_id || "";
    seoState.builderSelected = new Map();
    seoState.builderPendingCategories = new Set();
    seoState.builderAppliedCategories = new Set();
    seoState.builderCandidates = null;
    seoState.builderAppliedFilters = {};
    seoState.builderActiveCategory = "";
    seoState.builderVisibleCategories = [];
    seoState.builderCategorySort = { key: "sku_count", dir: "desc" };
    seoState.builderCategoryFilters = {};
    seoState.builderSkuSort = { key: "sales_14d_rub", dir: "desc" };
    seoState.builderSkuFilters = {};
    seoState.builderPage = 1;
    if (editProject) applyBuilderStateSnapshot(editProject.project?.sku_filters);
    if (editProject) (editProject.skus || []).forEach((row) => seoState.builderSelected.set(String(row.sku), { sku: String(row.sku), product_name: row.product_name, found: true, selection_source: "project" }));
    if (restoreFromUrl) restoreBuilderUrlState();
    const editing = Boolean(editProject);
    const editedProject = editProject?.project || null;
    const from = editedProject?.date_from || document.querySelector("#date_from")?.value || "";
    const to = editedProject?.date_to || document.querySelector("#date_to")?.value || "";
    const builderMarketplace = editedProject?.marketplace || seoState.marketplace;
    const marketplaceLocked = !editing && seoState.projectKind === "generation";
    const newProjectTitle = seoState.projectKind === "generation" ? generationDefaultName(builderMarketplace) : "Новый проект мониторинга SEO";
    seoState.root.innerHTML = `<form class="seo-builder" data-seo-create-form><header class="seo-builder-head"><div><button type="button" class="seo-project-btn ghost" data-seo-builder-back>${editing ? "← В проект" : "← Все проекты"}</button><h2>${editing ? `Выбор товаров · ${esc(editedProject.name)}` : esc(newProjectTitle)}</h2><p>${editing ? "Измените группу товаров любым из трёх способов и сохраните её." : "Первый шаг — выберите товары для SEO. Проект получит это название автоматически; после создания измените его двойным нажатием."}</p></div><div class="seo-builder-actions"><span data-seo-selected-count>0 SKU</span><button type="submit" class="seo-project-btn primary">${editing ? "Сохранить товары" : "Создать проект"}</button></div></header>
      ${editing ? "" : `<section class="seo-builder-settings"><label>Дата с<input name="date_from" type="date" value="${esc(from)}"></label><label>Дата по<input name="date_to" type="date" value="${esc(to)}"></label></section>`}
      <section class="seo-builder-source"><div class="seo-builder-section-head"><div><h3>1. Выберите товары для SEO</h3><p>Выбор из разных источников объединяется без дублей.</p></div></div><div class="seo-source-tabs" role="tablist" aria-label="Способ выбора товаров"><button type="button" class="active" role="tab" aria-selected="true" data-seo-source-tab="list">Загрузить из списка</button><button type="button" role="tab" aria-selected="false" data-seo-source-tab="file">Загрузить файлом</button><button type="button" role="tab" aria-selected="false" data-seo-source-tab="catalog">Выбрать из ассортимента</button></div></section>
      <section class="seo-builder-manual" data-seo-source-panel="list"><div class="seo-builder-section-head"><div><h3>Список SKU</h3><p>Вставьте SKU через перенос строки, пробел, запятую или точку с запятой.</p></div><button type="button" class="seo-project-btn primary" data-seo-list-apply>Добавить товары</button></div><textarea data-seo-list-input placeholder="SKU-001&#10;SKU-002&#10;SKU-003" aria-label="Список SKU"></textarea><div class="seo-upload-status" data-seo-list-status>Список ещё не добавлен</div><div class="seo-upload-preview"><table class="seo-project-table"><thead><tr><th>Строка</th><th>SKU</th><th>Название</th><th>Статус</th></tr></thead><tbody data-seo-list-preview></tbody></table></div></section>
      <section class="seo-builder-upload" data-seo-source-panel="file" hidden><div class="seo-builder-section-head"><div><h3>Загрузка файлом</h3><p>XLSX или CSV · выбор объединяется без дублей</p></div><a class="seo-project-btn ghost" href="/api/seo-project-template?dashboard=seoMonitoring" download>Скачать шаблон</a></div><label class="seo-dropzone" data-seo-dropzone><input type="file" accept=".xlsx,.csv" data-seo-file><strong>Перетащите файл сюда</strong><span>или нажмите, чтобы выбрать</span></label><div class="seo-upload-status" data-seo-upload-status>Файл ещё не выбран</div><div class="seo-upload-preview"><table class="seo-project-table"><thead><tr><th>Строка</th><th>SKU</th><th>Название</th><th>Статус</th></tr></thead><tbody data-seo-upload-preview></tbody></table></div></section>
      <div data-seo-source-panel="catalog" hidden><section class="seo-builder-filter-panel"><div class="seo-builder-section-head"><div><h3>Фильтры ассортимента</h3><p>Тот же отбор, категории и SEO-индикация, что в отчёте мониторинга. Изменения вступают в силу после применения.</p></div><div class="seo-builder-filter-actions"><button type="button" class="seo-project-btn ghost" data-seo-reset-catalog>Сбросить фильтры</button><button type="button" class="seo-project-btn primary" data-seo-apply-catalog>Применить фильтры</button></div></div><div class="seo-builder-filters">${catalogFilterFieldsHtml(builderMarketplace, marketplaceLocked)}</div></section>
      <div class="seo-builder-catalog-grid"><section class="seo-builder-categories"><div class="seo-builder-section-head"><div><h3>Категории</h3><p>Клик по названию фильтрует таблицу SKU · <span data-seo-metric-source>источники уточняются</span></p></div><div class="seo-builder-category-head-actions"><span data-seo-category-meta>Загрузка…</span><span data-seo-category-selection-status>Категории не выбраны</span><div class="seo-builder-category-tools"><button type="button" class="seo-builder-icon-btn apply" data-seo-category-apply aria-label="Применить выбор категорий" title="Применить выбор категорий"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg></button><button type="button" class="seo-builder-icon-btn" data-seo-category-reset aria-label="Сбросить выбор категорий" title="Сбросить выбор категорий"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4v6h6M5.5 15a7 7 0 1 0 .8-7.8L4 10"/></svg></button></div></div></div><div class="seo-project-table-wrap seo-builder-category-table"><table class="seo-project-table" data-seo-table="category">${builderTableHead("category")}<tbody data-seo-category-rows><tr><td colspan="11">Загружаю категории и метрики…</td></tr></tbody></table></div></section>
      <section class="seo-builder-catalog"><div class="seo-builder-section-head"><div><h3>Товары</h3><p>Мастер-чекбокс отмечает текущую страницу, «Выбрать все по фильтру» — весь отбор целиком · <span data-seo-metric-source>источники уточняются</span></p></div><div class="seo-builder-catalog-head-actions"><span data-seo-catalog-meta>Загрузка…</span><button type="button" class="seo-project-btn primary" data-seo-select-all-filtered>Выбрать все по фильтру</button></div></div><div class="seo-project-table-wrap seo-builder-table"><table class="seo-project-table" data-seo-table="sku">${builderTableHead("sku")}<tbody data-seo-catalog-rows><tr><td colspan="12">Загружаю SKU и метрики…</td></tr></tbody></table></div><div class="seo-builder-pagination"><span data-seo-selection-status>SKU не выбраны</span><button type="button" class="seo-project-btn ghost" data-seo-clear-selection>Сбросить выбор</button><div><button type="button" class="seo-project-btn ghost" data-seo-page-prev>Назад</button><button type="button" class="seo-project-btn ghost" data-seo-page-next>Вперёд</button></div></div></section></div></div>
      <div data-seo-form-error class="seo-project-progress error" hidden></div><footer class="seo-builder-footer"><button type="button" class="seo-project-btn" data-seo-builder-back>${editing ? "Отменить изменения" : "Отменить создание"}</button><div><span>Выбрано <strong data-seo-selected-count>0 SKU</strong></span><button type="submit" class="seo-project-btn primary">${editing ? "Сохранить группу SKU" : "Создать проект"}</button></div></footer></form>`;
    const form = seoState.root.querySelector("[data-seo-create-form]");
    renderBuilderSelection();
    setBuilderSourceMode("list");
    bindBuilderTableHeader("category");
    bindBuilderTableHeader("sku");
    form.querySelectorAll("[data-seo-source-tab]").forEach((button) => button.addEventListener("click", () => setBuilderSourceMode(button.dataset.seoSourceTab)));
    form.querySelector("[data-seo-list-apply]")?.addEventListener("click", parseSkuList);
    builderFilterNames.forEach((name) => { if (form[name] && seoState.builderAppliedFilters[name]) form[name].value = seoState.builderAppliedFilters[name]; });
    form.querySelectorAll("[data-seo-builder-back]").forEach((button) => button.addEventListener("click", async () => {
      clearBuilderUrlState();
      setCreateParam(false);
      const editedId = seoState.builderEditProjectId;
      seoState.builderEditProjectId = "";
      if (editedId) { setProjectParam(editedId); await loadDetail(); }
      else await loadList();
    }));
    form.querySelector('select[name="marketplace"]')?.addEventListener("change", () => {
      seoState.marketplace = form.marketplace.value;
      loadCandidates(1);
    });
    form.querySelector("[data-seo-apply-catalog]")?.addEventListener("click", () => {
      seoState.builderAppliedFilters = Object.fromEntries(builderFilterNames.map((name) => [name, form[name]?.value || ""]).filter(([, value]) => value));
      seoState.builderActiveCategory = "";
      seoState.builderPendingCategories.clear();
      seoState.builderAppliedCategories.clear();
      syncBuilderUrlState();
      loadCandidates(1);
    });
    form.querySelector("[data-seo-reset-catalog]")?.addEventListener("click", () => {
      builderFilterNames.forEach((name) => { if (form[name]) form[name].value = ""; });
      seoState.builderAppliedFilters = {};
      seoState.builderActiveCategory = "";
      seoState.builderPendingCategories.clear();
      seoState.builderAppliedCategories.clear();
      syncBuilderUrlState();
      loadCandidates(1);
    });
    form.querySelector(".seo-builder-filter-panel")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); form.querySelector("[data-seo-apply-catalog]")?.click(); }
    });
    form.marketplace?.addEventListener("change", () => {
      seoState.builderAppliedFilters = {};
      seoState.builderActiveCategory = "";
      seoState.builderPendingCategories.clear();
      seoState.builderAppliedCategories.clear();
      builderFilterNames.forEach((name) => { if (form[name]) form[name].value = ""; });
      syncBuilderUrlState();
      loadCandidates(1);
    });
    form.querySelector("[data-seo-category-apply]")?.addEventListener("click", applyPendingCategories);
    form.querySelector("[data-seo-category-reset]")?.addEventListener("click", resetPendingCategories);
    form.querySelector("[data-seo-page-prev]")?.addEventListener("click", () => loadCandidates(Math.max(1, seoState.builderPage - 1)));
    form.querySelector("[data-seo-page-next]")?.addEventListener("click", () => loadCandidates(seoState.builderPage + 1));
    form.querySelector("[data-seo-select-all-filtered]")?.addEventListener("click", () => selectAllFiltered());
    form.querySelector("[data-seo-clear-selection]")?.addEventListener("click", () => {
      seoState.builderSelected.clear();
      renderBuilderSelection();
      renderCandidateTable();
      renderCategoryTable();
    });
    const fileInput = form.querySelector("[data-seo-file]");
    fileInput?.addEventListener("change", () => parseTemplateFile(fileInput.files?.[0]));
    const dropzone = form.querySelector("[data-seo-dropzone]");
    ["dragenter", "dragover"].forEach((name) => dropzone?.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("active"); }));
    ["dragleave", "drop"].forEach((name) => dropzone?.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("active"); if (name === "drop") parseTemplateFile(event.dataTransfer?.files?.[0]); }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButtons = [...form.querySelectorAll('button[type="submit"]')];
      const errorBox = form.querySelector("[data-seo-form-error]");
      submitButtons.forEach((button) => { button.disabled = true; });
      try {
        const values = new FormData(form);
        const skus = [...seoState.builderSelected.values()].map(({ sku, product_name }) => ({ sku, product_name }));
        if (seoState.builderEditProjectId) {
          const projectId = seoState.builderEditProjectId;
          await post("/api/seo-projects/skus?dashboard=seoMonitoring", { client: seoState.client, project_kind: seoState.projectKind, project_id: projectId, skus, marketplace: values.get("marketplace"), sku_filters: builderStateSnapshot() });
          seoState.builderEditProjectId = "";
          clearBuilderUrlState();
          setCreateParam(false);
          setProjectParam(projectId);
          await loadDetail();
          return;
        }
        const clientLabel = document.querySelector("#clientSelect option:checked")?.textContent?.trim() || seoState.client;
        const data = await post("/api/seo-projects?dashboard=seoMonitoring", { client: seoState.client, project_kind: seoState.projectKind, client_label: clientLabel, marketplace: values.get("marketplace"), date_from: values.get("date_from"), date_to: values.get("date_to"), skus, sku_filters: builderStateSnapshot() });
        seoState.marketplace = String(values.get("marketplace") || seoState.marketplace);
        setCreateParam(false);
        setProjectParam(data.project_id);
        await loadDetail();
      } catch (error) {
        errorBox.hidden = false;
        errorBox.textContent = error.message;
      } finally { submitButtons.forEach((button) => { button.disabled = false; }); }
    });
    loadCandidates(seoState.builderPage);
  }


  const today = () => new Date().toISOString().slice(0, 10);
  const ruDate = (value) => (value ? new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU") : "");

  const OZON_LAG_DAYS = 2;      // Ozon calculates query analytics 1-2 days after the fact
  const OZON_HISTORY_DAYS = 30; // inside the last month any day works; deeper data is weekly only

  function latestCollectDay() {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - OZON_LAG_DAYS).toISOString().slice(0, 10);
  }

  function defaultCollectFrom(data) {
    // A project without snapshots starts with the whole last month; later runs continue from the last day.
    const collected = (data?.dynamics || []).map((row) => String(row.snapshot_date)).sort();
    const last = new Date(`${latestCollectDay()}T00:00:00`);
    if (collected.length) {
      const next = new Date(`${collected[collected.length - 1]}T00:00:00`);
      next.setDate(next.getDate() + 1);
      return next <= last ? next.toISOString().slice(0, 10) : latestCollectDay();
    }
    last.setDate(last.getDate() - (OZON_HISTORY_DAYS - 1));
    return last.toISOString().slice(0, 10);
  }

  function collectDayList(from, to) {
    const days = [];
    const cursor = new Date(`${from}T00:00:00`);
    const end = new Date(`${to}T00:00:00`);
    while (cursor <= end && days.length < 120) {
      days.push(cursor.toISOString().slice(0, 10));
      cursor.setDate(cursor.getDate() + 1);
    }
    return days;
  }

  function collectSourceNote(marketplace) {
    if (marketplace === "yandex_market") return "Источник Яндекс Маркета: каталог, товарные позиции заказов и доступный снимок остатков. Поисковые запросы Seller API и MPStats для Яндекса не подключены; такие этапы будут явно недоступны.";
    return marketplace === "wb"
      ? "Источники WB: поисковые запросы и карточки из базы, MPStats-ключи, отзывы/вопросы из синхронизированного WB Seller API. Недоступные источники остаются «Недоступно», не нулём."
      : "Источник Ozon: Seller API /v1/analytics/product-queries/details. До 15 фраз на SKU за прогон; SKU уходят пакетами по 100 в одном запросе.";
  }

  function collectPeriodWarning(options, marketplace) {
    // Ozon calculates query analytics with a 1–2 day lag; any interval inside the last
    // month works, older data is weekly and needs a Premium subscription.
    if (marketplace === "wb" || marketplace === "yandex_market") return "";
    const from = new Date(`${options.date_from}T00:00:00`);
    const to = new Date(`${options.date_to}T00:00:00`);
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return "Проверьте даты периода";
    if (to < from) return "Дата «по» раньше даты «с»";
    const now = new Date();
    const latest = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 2);
    const asRu = (value) => value.toLocaleDateString("ru-RU");
    if (to > latest) return `Ozon считает аналитику 1–2 дня: возьмите период до ${asRu(latest)}`;
    return "";
  }

  function collectParams() {
    const root = seoState.root;
    const value = (selector) => root.querySelector(selector)?.value || "";
    return {
      date_from: value("[data-seo-collect-from]"),
      date_to: value("[data-seo-collect-to]"),
      snapshot_date: value("[data-seo-collect-snapshot]") || today(),
      limit_by_sku: Number.parseInt(value("[data-seo-collect-limit]") || "15", 10) || 15,
      sort_by: value("[data-seo-collect-sort]") || "BY_SEARCHES",
      all_sorts: seoState.root.querySelector("[data-seo-collect-all-sorts]")?.checked ? "1" : "",
      split_by_days: seoState.root.querySelector("[data-seo-collect-split]")?.checked ? "1" : "",
      sort_dir: value("[data-seo-collect-dir]") || "DESCENDING",
      scope: value("[data-seo-collect-scope]") || "filtered",
      feedback_days: Number.parseInt(value("[data-seo-feedback-days]") || "90", 10) || 90,
    };
  }

  async function collectScopeSkus(scope) {
    if (scope === "all") return (seoState.detail?.skus || []).map((row) => String(row.sku));
    const params = new URLSearchParams({
      client: seoState.client, marketplace: seoState.detail?.project?.marketplace || seoState.marketplace,
      dashboard: "seoMonitoring", selection: "1", project_id: seoState.projectId,
      sort_col: seoState.projectSkuSort.key, sort_dir: seoState.projectSkuSort.dir,
      column_filters: JSON.stringify(seoState.projectSkuFilters), ...projectFilterParams(),
    });
    const data = await json(`/api/seo-project-candidates?${params}`);
    return (data.rows || []).map((row) => String(row.sku));
  }

  function savedFiltersChips(state) {
    const chips = savedFilterList(state);
    if (!chips.length) return '<span class="seo-project-detail-meta">фильтры отбора не сохранены</span>';
    return `<span class="seo-project-saved-filters">${chips.slice(0, 3).map((chip) => `<em title="${esc(chip)}">${esc(chip)}</em>`).join("")}${chips.length > 3 ? `<em title="${esc(chips.slice(3).join(" · "))}">+${chips.length - 3}</em>` : ""}</span>`;
  }

  function savedFilterList(state) {
    if (!state || typeof state !== "object") return [];
    const chips = Object.entries(validBuilderFilters(state.filters)).map(([key, value]) => `${builderFilterLabels[key] || key}: ${value}`);
    const categories = Array.isArray(state.categories) ? state.categories.filter(Boolean) : [];
    if (categories.length) chips.push(`Категории: ${categories.join(", ")}`);
    const columnFilters = Object.keys(validColumnFilters("sku", state.sku_filters)).length + Object.keys(validColumnFilters("category", state.category_filters)).length;
    if (columnFilters) chips.push(`Фильтры по колонкам: ${columnFilters}`);
    return chips;
  }

  function savedFiltersNote(state) {
    if (!state || typeof state !== "object") return '<p class="seo-project-source-note">Фильтры отбора SKU не сохранены — откройте «К выбору SKU», задайте их и сохраните группу.</p>';
    const chips = Object.entries(validBuilderFilters(state.filters)).map(([key, value]) => `${builderFilterLabels[key] || key}: ${value}`);
    const categories = Array.isArray(state.categories) ? state.categories.filter(Boolean) : [];
    if (categories.length) chips.push(`Категории: ${categories.join(", ")}`);
    const columnFilters = Object.keys(validColumnFilters("sku", state.sku_filters)).length + Object.keys(validColumnFilters("category", state.category_filters)).length;
    if (columnFilters) chips.push(`Фильтры по колонкам: ${columnFilters}`);
    if (!chips.length) return '<p class="seo-project-source-note">Фильтры отбора SKU сохранены пустыми — отбор шёл по всему каталогу.</p>';
    return `<div class="seo-project-saved-filters"><span>Фильтры отбора SKU</span>${chips.map((chip) => `<em title="${esc(chip)}">${esc(chip)}</em>`).join("")}</div>`;
  }

  function seoCollectActionIcon(name) {
    const icons = {
      search: '<circle cx="11" cy="11" r="6"></circle><path d="m16 16 4 4"></path>',
      database: '<ellipse cx="12" cy="5" rx="7" ry="3"></ellipse><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"></path><path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"></path>',
      filter: '<path d="M4 5h16l-6 7v5l-4 2v-7z"></path><path d="m16 17 2 2 3-4"></path>',
      target: '<circle cx="12" cy="12" r="8"></circle><circle cx="12" cy="12" r="3"></circle><path d="M12 2v3M22 12h-3"></path>',
      users: '<path d="M16 20v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2"></path><circle cx="9.5" cy="7" r="4"></circle><path d="M17 11a4 4 0 0 1 4 4v2"></path>',
      listSearch: '<path d="M4 6h9M4 11h7M4 16h5"></path><circle cx="16" cy="15" r="4"></circle><path d="m19 18 2 2"></path>',
      shield: '<path d="M12 3 5 6v5c0 4.6 2.8 7.8 7 10 4.2-2.2 7-5.4 7-10V6z"></path><path d="m9 12 2 2 4-5"></path>',
      review: '<path d="M5 5h14v11H9l-4 3z"></path><path d="M8 9h8M8 12h5"></path>',
      question: '<circle cx="12" cy="12" r="9"></circle><path d="M9.7 9a2.5 2.5 0 1 1 4.4 1.6c-1 .8-2.1 1.2-2.1 2.9"></path><path d="M12 17h.01"></path>',
      claims: '<path d="m12 3 2.1 4.3L19 8l-3.5 3.4.8 4.8L12 14l-4.3 2.2.8-4.8L5 8l4.9-.7z"></path><path d="m9.5 11.2 1.5 1.5 3.5-3.5"></path>',
      context: '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"></path><path d="M10 7h4M7 10v4M17 10v4M10 17h4"></path>',
      allocation: '<path d="M5 4h14v16H5z"></path><path d="M8 8h8M8 12h5M8 16h7"></path><path d="m15 12 1 1 2-3"></path>',
      write: '<path d="M4 20h4l11-11-4-4L4 16z"></path><path d="m13 7 4 4M4 20l4-1-3-3z"></path>',
      expert: '<path d="M12 3 5 6v5c0 4.6 2.8 7.8 7 10 4.2-2.2 7-5.4 7-10V6z"></path><path d="M9 9h6M9 13h3"></path><path d="m13 15 1.5 1.5L18 13"></path>',
      export: '<path d="M7 3h7l4 4v14H7z"></path><path d="M14 3v5h5M12 11v6M9.5 14.5 12 17l2.5-2.5"></path>',
      play: '<circle cx="12" cy="12" r="9"></circle><path d="m10 8 6 4-6 4z"></path>',
    };
    return `<svg class="seo-collect-action-icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.search}</svg>`;
  }

  function seoCollectAction(attribute, label, icon, title = label) {
    return `<button type="button" class="seo-project-btn seo-stage-action seo-collect-icon-action" ${attribute} aria-label="${esc(label)}" title="${esc(title)}">${seoCollectActionIcon(icon)}<span>${esc(label)}</span></button>`;
  }

  function seoUnavailableAction(label, icon, title) {
    return `<button type="button" class="seo-project-btn seo-stage-action seo-collect-icon-action is-unavailable" data-seo-stage-unavailable disabled aria-disabled="true" aria-label="${esc(label)} — недоступно" title="${esc(title)}">${seoCollectActionIcon(icon)}<span>${esc(label)}</span><em>нет источника</em></button>`;
  }

  const fullRunStageMeta = [
    ["seller_keywords", "Запросы", "search"], ["mpstats", "MPStats", "database"],
    ["keyword_analysis", "Очистить ключи", "filter"], ["intents", "Интенты", "target"],
    ["competitors", "Конкуренты", "users"], ["competitor_keywords", "Ключи конкурентов", "listSearch"],
    ["competitor_analysis", "Очистить конкурентов", "shield"], ["customer_messages", "Отзывы", "review"],
    ["customer_voice", "SEO-клеймы", "claims"], ["semantic_context", "SEO-контекст", "context"],
    ["allocation", "SEO-разметка", "allocation"], ["draft", "SEO-тексты", "write"],
    ["review", "Эксперт", "expert"],
  ];

  function fullRunRoadmapHtml() {
    return `<section class="seo-full-run-roadmap" data-seo-full-run-roadmap hidden aria-live="polite">
      <header><div><strong data-seo-full-run-title>Полный ран</strong><span data-seo-full-run-summary>Подготовка</span></div><div class="seo-full-run-controls"><span data-seo-full-run-percent>0%</span><button type="button" class="seo-project-btn ghost" data-seo-full-run-stop hidden>Остановить</button></div></header>
      <div class="seo-full-run-track" role="list" aria-label="Этапы полного рана">${fullRunStageMeta.map(([id, label, icon], index) => `<div class="seo-full-run-step is-pending" data-seo-full-run-stage="${id}" role="listitem"><span class="seo-full-run-node">${seoCollectActionIcon(icon)}<i>${index + 1}</i></span><em>${esc(label)}</em><small data-seo-full-run-stage-progress>0/${num(seoState.detail?.summary?.sku_count || 0)}</small></div>`).join("")}</div>
      <div class="seo-full-run-bar"><span data-seo-full-run-bar></span></div>
    </section>`;
  }

  function durationText(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "—";
    const value = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(value / 3600), minutes = Math.floor((value % 3600) / 60), rest = value % 60;
    if (hours) return `${hours} ч ${minutes} мин`;
    if (minutes) return `${minutes} мин ${rest} сек`;
    return `${rest} сек`;
  }

  function renderFullRunJob(job) {
    const roadmap = seoState.root.querySelector("[data-seo-full-run-roadmap]");
    if (!roadmap || !job) return;
    roadmap.hidden = false;
    const active = ["queued", "running", "stopping"].includes(job.status);
    const overall = Number(job.overall_percent ?? (job.status === "completed" ? 100 : 0));
    const stageMap = new Map((job.stages || []).map((stage) => [stage.id, stage]));
    roadmap.classList.toggle("is-active", active);
    roadmap.classList.toggle("has-errors", Number(job.error_count || 0) > 0);
    roadmap.querySelectorAll("[data-seo-full-run-stage]").forEach((node) => {
      const stage = stageMap.get(node.dataset.seoFullRunStage) || {};
      const status = stage.status || "pending";
      node.className = `seo-full-run-step is-${status}`;
      const value = node.querySelector("[data-seo-full-run-stage-progress]");
      if (value) value.textContent = `${num(stage.done || 0)}/${num(stage.total ?? job.total_skus ?? 0)}`;
    });
    const activeStage = stageMap.get(job.current_stage) || null;
    const summary = roadmap.querySelector("[data-seo-full-run-summary]");
    const started = job.started_at ? new Date(job.started_at) : null;
    const elapsed = started ? Math.max(0, (Date.now() - started.getTime()) / 1000) : 0;
    if (summary) {
      summary.textContent = active
        ? `${activeStage?.label || "Подготовка"} · ${num(job.current_stage_done || 0)}/${num(job.current_stage_total || job.total_skus || 0)} SKU · прошло ${durationText(elapsed)} · ETA ${durationText(job.eta_seconds)}`
        : `${job.status === "completed" ? "Завершён" : job.status === "partial" ? "Завершён частично" : job.status === "stopped" ? "Остановлен" : "Завершён с ошибками"} · ${num(job.total_skus)} SKU · ошибок источников ${num(job.error_count || 0)}`;
    }
    const percent = roadmap.querySelector("[data-seo-full-run-percent]");
    if (percent) percent.textContent = `${num(overall, 1)}%`;
    const bar = roadmap.querySelector("[data-seo-full-run-bar]");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, overall))}%`;
    const runButton = seoState.root.querySelector("[data-seo-full-run]");
    if (runButton) {
      runButton.disabled = active;
      runButton.classList.toggle("is-running", active);
      const label = runButton.querySelector("span");
      if (label) label.textContent = active ? "Полный ран идёт" : "Полный ран";
    }
    const stop = roadmap.querySelector("[data-seo-full-run-stop]");
    if (stop) {
      stop.hidden = !active;
      stop.disabled = job.status === "stopping";
      stop.dataset.jobId = job.job_id || "";
      stop.textContent = job.status === "stopping" ? "Останавливаю" : "Остановить";
    }
    const terminalStatus = seoState.root.querySelector("[data-seo-terminal-status]");
    const terminal = seoState.root.querySelector("[data-seo-progress]");
    if (terminalStatus) terminalStatus.textContent = active ? `Полный ран · ${activeStage?.label || "подготовка"}` : "Полный ран · итог";
    if (terminal && Array.isArray(job.log_lines)) {
      terminal.className = `seo-project-progress${job.status === "error" ? " error" : ""}`;
      terminal.textContent = `${job.log_lines.join("\n")}\n`;
      terminal.scrollTop = terminal.scrollHeight;
    }
  }

  function clearFullRunPoll() {
    if (seoState.fullRunPollTimer) window.clearTimeout(seoState.fullRunPollTimer);
    seoState.fullRunPollTimer = null;
  }

  async function pollFullRunJob() {
    clearFullRunPoll();
    const params = new URLSearchParams({ client: seoState.client, project_id: seoState.projectId, dashboard: "seoMonitoring" });
    if (seoState.fullRunJobId) params.set("job_id", seoState.fullRunJobId);
    const payload = await json(`/api/seo-project-full-run/status?${params}`);
    const job = payload.job;
    if (!job) return;
    seoState.fullRunJobId = job.job_id || "";
    renderFullRunJob(job);
    if (["queued", "running", "stopping"].includes(job.status)) {
      seoState.fullRunPollTimer = window.setTimeout(() => pollFullRunJob().catch((error) => showCollectionFailure("Полный ран · статус", error.message)), 1500);
    } else {
      await loadProjectSkus(seoState.projectPage).catch(() => {});
    }
  }

  async function startFullRun(button) {
    button.disabled = true;
    const options = collectParams();
    const result = await post("/api/seo-project-full-run/start?dashboard=seoMonitoring", {
      client: seoState.client, project_id: seoState.projectId, project_kind: seoState.projectKind,
      date_from: options.date_from, date_to: options.date_to, period_days: options.feedback_days,
    });
    seoState.fullRunJobId = result.job?.job_id || "";
    renderFullRunJob(result.job);
    await pollFullRunJob();
  }

  async function stopFullRun(button) {
    const jobId = button.dataset.jobId || seoState.fullRunJobId;
    if (!jobId) return;
    button.disabled = true;
    const result = await post("/api/seo-project-full-run/stop?dashboard=seoMonitoring", {
      client: seoState.client, project_id: seoState.projectId, job_id: jobId,
    });
    if (result.job) renderFullRunJob(result.job);
  }

  function detailHtml(data) {
    const p = data.project, s = data.summary;
    const marketplace = String(p.marketplace || "").toLowerCase();
    const isOzon = marketplace === "ozon";
    const projectColumns = tableColumns("project", marketplace);
    const competitorSourceNote = `конкуренты: MPStats ${isOzon ? "Ozon" : "WB"} «Товары в поиске»`;
    const competitorActions = `${seoCollectAction("data-seo-collect-competitors", "Конкуренты", "users", "Собрать конкурентов из MPStats")}${seoCollectAction("data-seo-collect-competitor-keywords", "Ключи конкурентов", "listSearch", "Собрать ключи конкурентов из MPStats")}${seoCollectAction("data-seo-analyze-competitor-keywords", "Очистить конкурентов", "shield", "Очистить и ранжировать ключи конкурентов")}`;
    const questionsAction = isOzon
      ? seoCollectAction("data-seo-collect-questions", "Вопросы", "question", "Отдельно обновить вопросы своих SKU через Ozon Seller API")
      : seoUnavailableAction("Вопросы", "question", "Для WB отзывы и вопросы своих SKU синхронизируются общим этапом «Отзывы»; отдельный запуск не требуется");
    return `<header class="seo-project-detail-head"><button class="seo-project-btn ghost" data-seo-back>← Все проекты</button><div class="seo-project-detail-name" data-seo-name-wrap="${esc(p.project_id)}"><div class="seo-project-name-line"><h2 data-seo-rename="${esc(p.project_id)}" data-seo-current-name="${esc(p.name)}" title="Дважды нажмите, чтобы переименовать">${esc(p.name)}</h2></div></div><span class="seo-project-detail-meta">${esc(String(p.marketplace).toUpperCase())} · ${esc(p.date_from || "период не задан")} — ${esc(p.date_to || "—")} · ${esc(statusLabels[p.status] || p.status)}</span>${savedFiltersChips(p.sku_filters)}<button class="seo-project-btn ghost" data-seo-ai-settings aria-label="Настроить модели ИИ" title="Настроить модели ИИ">Настройки ИИ</button><button class="seo-project-btn ghost" data-seo-edit-skus="${esc(p.project_id)}">← К выбору SKU (${num(s.sku_count)})</button></header>
      <section class="seo-builder-catalog" data-seo-project-catalog><div class="seo-builder-section-head"><div><h3>SKU проекта</h3><p>Те же параметры, что и в отборе · <span data-seo-metric-source>источники уточняются</span> · ${esc(competitorSourceNote)}</p></div><span data-seo-project-meta>Загрузка…</span></div><div class="seo-project-table-wrap seo-builder-table"><table class="seo-project-table" data-seo-table="project" data-marketplace="${esc(marketplace)}">${builderTableHead("project", marketplace)}<tbody data-seo-project-rows><tr><td colspan="${projectColumns.length}">Загружаю SKU проекта…</td></tr></tbody></table></div><div class="seo-builder-pagination"><span data-seo-project-status>Группа проекта: ${num(s.sku_count)} SKU</span><div><button type="button" class="seo-project-btn ghost" data-seo-project-prev>Назад</button><button type="button" class="seo-project-btn ghost" data-seo-project-next>Вперёд</button></div></div></section>
      <section class="seo-project-collect" data-seo-collect><div class="seo-builder-section-head"><div><h3>Сбор и анализ поисковых запросов</h3><p>${esc(collectSourceNote(p.marketplace))}</p></div><div class="seo-collect-action-groups"><div class="seo-collect-action-toolbar" aria-label="Этапы сбора, анализа и экспорта"><div class="seo-collect-full-run-action" role="group" aria-label="Полный ран">${seoCollectAction("data-seo-full-run", "Полный ран", "play", "Запустить все этапы по всем SKU проекта")}</div><div class="seo-collect-keyword-actions" role="group" aria-label="Ключи и конкуренты">${seoCollectAction("data-seo-refresh-all", "Запросы", "search", "Собрать запросы Seller API")}${seoCollectAction("data-seo-collect-mpstats", "MPStats", "database", "Собрать из MPStats")}${seoCollectAction("data-seo-analyze-keywords", "Очистить ключи", "filter", "Очистить и ранжировать свои ключи")}${seoCollectAction("data-seo-generate-intents", "Интенты", "target", "Определить интенты товаров")}${competitorActions}</div><div class="seo-collect-voice-actions" role="group" aria-label="Отзывы, вопросы, SEO-клеймы и экспорт">${seoCollectAction("data-seo-collect-customer-messages", "Отзывы", "review", marketplace === "wb" ? "Отзывы и вопросы своих SKU из синхронизированного WB Seller API; отзывы конкурентов — когда конкуренты доступны" : "Отзывы своих SKU и топ-10 конкурентов; вопросы своих SKU также обновятся")}${questionsAction}${seoCollectAction("data-seo-analyze-customer-voice", "SEO-клеймы", "claims", "Клеймы с подтверждениями из своих отзывов, вопросов и отзывов конкурентов")}${seoCollectAction("data-seo-prepare-semantic-context", "SEO-контекст", "context", "Собрать компактный контекст для формирования семантического ядра")}${seoCollectAction("data-seo-generate-content-allocation", "SEO-разметка", "allocation", "Выбрать ключи, характеристики и клеймы для полей карточки")}${seoCollectAction("data-seo-generate-content-drafts", "SEO-тексты", "write", marketplace === "wb" ? "Сгенерировать черновики названий до 60 символов, описаний и внутренних SEO-меток" : "Сгенерировать черновики названий, описаний и хештегов")}${seoCollectAction("data-seo-review-content-drafts", "Эксперт", "expert", "Проверить русский язык, логику, запреты и соответствие характеристикам")}${seoCollectAction("data-seo-export-content", "Экспорт", "export", "Скачать Excel по всем SKU проекта")}</div></div></div></div>
      ${fullRunRoadmapHtml()}
      <div class="seo-collect-params">
        <label><span class="seo-collect-hint" title="За какие даты Ozon считает поисковые запросы. Внутри последнего месяца подходит любой интервал, вплоть до одного дня, но расчёт идёт 1–2 дня, поэтому вчерашние даты ещё пустые. Периоды глубже месяца Ozon отдаёт только по неделям и только с подпиской Premium. Период в шапке проекта при этом не меняется.">Период сбора</span><span class="wb-analytics-range" data-wb-range data-wb-range-prefix="seo_collect"><button type="button" class="dropdown-toggle wb-analytics-range-toggle" data-wb-range-toggle>${esc(ruDate(defaultCollectFrom(data)))} - ${esc(ruDate(latestCollectDay()))}</button><span class="date-range-menu wb-analytics-date-menu hidden" data-wb-range-menu></span></span><input type="hidden" data-wb-field="seo_collect_start" data-seo-collect-from value="${esc(defaultCollectFrom(data))}"><input type="hidden" data-wb-field="seo_collect_end" data-seo-collect-to value="${esc(latestCollectDay())}"></label>
        <label><span class="seo-collect-hint" title="Дата, под которой строки лягут в базу как один замер. Повторный сбор с той же датой перезапишет строки этого замера, с новой датой — добавит отдельную точку, по которой потом считается динамика позиций и трафика.">Дата снимка</span><input type="date" data-seo-collect-snapshot value="${esc(today())}"></label>
        <label><span class="seo-collect-hint" title="Сколько фраз запросить по каждому SKU. Ozon отдаёт максимум 15 за один запуск, и в этот лимит попадают фразы по выбранной сортировке.">Ключей на SKU</span><input type="number" min="1" max="15" step="1" data-seo-collect-limit value="15" ${p.marketplace !== "wb" ? "" : "disabled"}></label>
        <label class="seo-collect-toggle"><span class="seo-collect-hint" title="Каждый день периода собирается отдельно и ложится своим снимком — так у проекта появляется дневная история позиций и трафика вместо одной усреднённой точки. Для проекта без снимков включено по умолчанию: берём последний месяц по дням.">Сбор по дням</span><input type="checkbox" data-seo-collect-split ${(data.dynamics || []).length ? "" : "checked"}></label>
        <label class="seo-collect-toggle"><span class="seo-collect-hint" title="Прогон по всем пяти сортировкам подряд: каждая отдаёт свой топ-15, объединение даёт около 50–60 фраз на SKU вместо 15. Запросов к API станет в пять раз больше.">Все сортировки</span><input type="checkbox" data-seo-collect-all-sorts></label>
        <label><span class="seo-collect-hint" title="Чем определяется, какие 15 фраз попадут в выборку. Каждая сортировка даёт свой срез: по числу запросов, просмотрам, позиции, конверсии или GMV. Прогоны с разными сортировками дополняют друг друга — так на один SKU можно накопить намного больше 15 фраз. BY_VIEWS, BY_POSITION и BY_CONVERSION требуют подписки Premium или Premium Plus.">Сортировка</span><select data-seo-collect-sort ${p.marketplace !== "wb" ? "" : "disabled"}><option value="BY_SEARCHES">По числу запросов</option><option value="BY_VIEWS">По просмотрам</option><option value="BY_POSITION">По позиции</option><option value="BY_CONVERSION">По конверсии</option><option value="BY_GMV">По продажам (GMV)</option></select></label>
        <label><span class="seo-collect-hint" title="По убыванию — сначала самые сильные фразы (обычный режим). По возрастанию — начиная со слабых, если нужен хвост.">Направление</span><select data-seo-collect-dir ${p.marketplace !== "wb" ? "" : "disabled"}><option value="DESCENDING">По убыванию</option><option value="ASCENDING">По возрастанию</option></select></label>
        <label><span class="seo-collect-hint" title="«Только отфильтрованные» — SKU, которые сейчас остались в таблице выше после фильтров каталога и колонок. «Вся группа» — все SKU проекта. По каждому SKU уходит отдельный запрос к API, за один прогон обрабатывается до 50 SKU.">Какие SKU</span><select data-seo-collect-scope><option value="filtered">Отфильтрованные</option><option value="all">Вся группа (${num(s.sku_count)})</option></select></label>
        <label><span class="seo-collect-hint" title="90 дней дают устойчивее материал для SEO; 30 дней удобны для проверки свежих изменений. Недоступный источник показывается явно и не считается нулём.">Отзывы, вопросы, конкуренты</span><select data-seo-feedback-days><option value="30">30 дней</option><option value="90" selected>90 дней</option></select></label>
      </div>
      <p class="seo-project-source-note" data-seo-collect-plan></p></section>
      <section class="seo-collection-terminal" data-seo-terminal><header><div><strong>Терминал выполнения скриптов</strong><span data-seo-terminal-status>Ожидание</span></div><div><span>все этапы проекта · сбор и обработка по SKU</span><button type="button" class="seo-project-btn ghost seo-terminal-stop" data-seo-stop-competitor-ai hidden>Остановить</button></div></header><pre class="seo-project-progress" data-seo-progress aria-live="polite">Терминал готов.\nВыберите этап в панели выше и запустите скрипт.</pre></section>`;
  }

  function showCollectionTerminal(provider) {
    const workspaceTerminal = seoState.root.querySelector("[data-seo-workspace-terminal]");
    if (workspaceTerminal && seoState.skuWorkspaceRun) {
      workspaceTerminal.hidden = false;
      workspaceTerminal.classList.add("is-active");
      const workspaceStatus = workspaceTerminal.querySelector("[data-seo-workspace-terminal-status]");
      if (workspaceStatus) workspaceStatus.textContent = provider;
      return workspaceTerminal.querySelector("[data-seo-workspace-progress]");
    }
    const terminal = seoState.root.querySelector("[data-seo-terminal]");
    const status = seoState.root.querySelector("[data-seo-terminal-status]");
    if (terminal) terminal.hidden = false;
    if (status) status.textContent = provider;
    return seoState.root.querySelector("[data-seo-progress]");
  }

  function restoreCollectionTerminal(provider, log, errors) {
    const box = showCollectionTerminal(provider);
    if (!box) return;
    box.className = `seo-project-progress${errors ? " error" : ""}`;
    box.textContent = log;
    box.scrollTop = box.scrollHeight;
  }

  function showCollectionFailure(provider, message) {
    const box = showCollectionTerminal(provider);
    if (!box) return;
    box.className = "seo-project-progress error";
    box.textContent = `ОШИБКА: ${message}\n`;
    box.scrollTop = box.scrollHeight;
  }

  async function finishCollectionRun(provider, log, errors) {
    const workspaceRun = seoState.skuWorkspaceRun;
    if (workspaceRun) {
      const stopped = workspaceRun.stopRequested;
      const finalLog = stopped
        ? `${log}\nОСТАНОВЛЕНО В ПАНЕЛИ: запрос браузера отменён. Сервер мог успеть завершить уже принятый пакет; «Возобновить» безопасно повторяет идемпотентный этап.\n`
        : log;
      restoreCollectionTerminal(provider, finalLog, stopped ? 0 : errors);
      await loadProjectSkus(seoState.projectPage);
      return;
    }
    await loadDetail();
    restoreCollectionTerminal(provider, log, errors);
  }

  const COLLECT_BATCH = 100;
  const MPSTATS_COLLECT_BATCH = 10;
  const CUSTOMER_MESSAGES_BATCH = 2;
  const CUSTOMER_QUESTIONS_BATCH = 50;
  const CUSTOMER_VOICE_BATCH = 2;

  async function postCollectionBatchWithRetry(path, payload, box, label) {
    const pauses = [2, 5, 10];
    for (let attempt = 0; attempt <= pauses.length; attempt += 1) {
      try {
        const signal = seoState.skuWorkspaceRun?.controller?.signal;
        if (signal?.aborted) throw new DOMException("Запуск остановлен", "AbortError");
        return await post(path, payload, signal ? { signal } : {});
      } catch (error) {
        if (error?.name === "AbortError" || seoState.skuWorkspaceRun?.stopRequested) throw error;
        if (/уже выполняется/i.test(error.message || "")) throw error;
        if (attempt >= pauses.length) throw error;
        const waitSeconds = pauses[attempt];
        box.textContent += `ПОВТОР: ${label} · попытка ${attempt + 2}/${pauses.length + 1} через ${waitSeconds} сек. · ${error.message}\n`;
        for (let left = waitSeconds; left > 0; left -= 1) {
          box.textContent += `ОЖИДАНИЕ: ${label} · осталось ${left} сек.\n`;
          box.scrollTop = box.scrollHeight;
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
      }
    }
    throw new Error("Не удалось выполнить батч после повторов");
  }

  async function refreshSkus(skus, options = {}) {
    const box = showCollectionTerminal("Seller API");
    const project = seoState.detail.project;
    const dateFrom = options.date_from || project.date_from || "";
    const dateTo = options.date_to || project.date_to || "";
    const snapshotDate = options.snapshot_date || today();
    const limitBySku = Math.min(15, Math.max(1, Number.parseInt(options.limit_by_sku, 10) || 15));
    const sortBy = options.sort_by || "BY_SEARCHES";
    const sortDir = options.sort_dir || "DESCENDING";
    const started = Date.now();
    let rows = 0, errors = 0;
    box.className = "seo-project-progress";
    const allSorts = options.all_sorts === "1";
    const days = options.split_by_days === "1" ? collectDayList(dateFrom, dateTo) : [null];
    const batches = [];
    for (let index = 0; index < skus.length; index += COLLECT_BATCH) batches.push(skus.slice(index, index + COLLECT_BATCH));
    box.textContent = `ПЛАН: ${skus.length} SKU · ${batches.length} батчей по ${COLLECT_BATCH} SKU · ${days[0] ? `дней ${days.length} (${dateFrom}..${dateTo}), снимок = сам день` : `период ${dateFrom || "—"}..${dateTo || "—"}, снимок ${snapshotDate}`} · до ${limitBySku} фраз на SKU · сортировка ${allSorts ? "все пять" : `${sortBy}/${sortDir}`}
`;
    for (const day of days) {
      if (day) box.textContent += `ДЕНЬ ${day}` + "\n";
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU в батче ${batches[i].length} | строк ${rows} | ошибок ${errors} | ETA ${eta} сек.
`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-projects/refresh?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, skus: batches[i],
          date_from: day || dateFrom, date_to: day || dateTo, snapshot_date: day || snapshotDate,
          limit_by_sku: limitBySku, sort_by: sortBy, sort_dir: sortDir, all_sorts: allSorts ? "1" : "",
        }, box, `${day ? `день ${day} · ` : ""}батч ${i + 1}/${batches.length}`);
        rows += Number(result.row_count || 0);
        errors += (result.errors || []).length;
        (result.errors || []).forEach((item) => { box.textContent += `ОШИБКА: SKU ${item.sku || ""} | сортировка ${item.sort_by || "—"} | ${item.error || "источник не вернул данные"}
`; });
        box.textContent += `[${i + 1}/${batches.length}] батч готов: строк ${result.row_count}, сортировок ${(result.sorts_used || []).length}
`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА: батч ${i + 1}/${batches.length} | ${error.message}
`;
      }
      box.scrollTop = box.scrollHeight;
    }
    }
    box.textContent += `ИТОГ: SKU ${skus.length} | строк ${rows} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.
`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Seller API", finalLog, errors);
  }

  async function refreshMpstatsSkus(skus, options = {}) {
    const box = showCollectionTerminal("MPStats");
    const project = seoState.detail.project;
    const dateFrom = options.date_from || project.date_from || "";
    const dateTo = options.date_to || project.date_to || "";
    const snapshotDate = options.snapshot_date || today();
    const batches = [];
    for (let index = 0; index < skus.length; index += MPSTATS_COLLECT_BATCH) batches.push(skus.slice(index, index + MPSTATS_COLLECT_BATCH));
    const started = Date.now();
    let rows = 0, errors = 0, empty = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН MPSTATS: ${skus.length} SKU · ${batches.length} батчей по ${MPSTATS_COLLECT_BATCH} SKU · период ${dateFrom}..${dateTo} · снимок ${snapshotDate} · отсутствующие у MPStats метрики сохраняются как «—»\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU в батче ${batches[i].length} | строк ${rows} | пусто ${empty} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-projects/collect-mpstats?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], date_from: dateFrom, date_to: dateTo, snapshot_date: snapshotDate,
        }, box, `MPStats · батч ${i + 1}/${batches.length}`);
        rows += Number(result.row_count || 0);
        errors += (result.errors || []).length;
        empty += Number(result.empty_count || 0);
        (result.errors || []).forEach((item) => { box.textContent += `ОШИБКА MPSTATS: SKU ${item.sku || ""} | ${item.error || "источник не вернул данные"}\n`; });
        box.textContent += `[${i + 1}/${batches.length}] батч готов: строк ${num(result.row_count)} · без ключей ${num(result.empty_count)}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА MPSTATS: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ MPSTATS: SKU ${skus.length} | строк ${rows} | без ключей ${empty} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("MPStats", finalLog, errors);
  }

  async function collectCustomerMessageSkus(skus, options = {}) {
    const box = showCollectionTerminal("Отзывы + вопросы + конкуренты");
    const periodDays = [30, 90].includes(Number(options.feedback_days)) ? Number(options.feedback_days) : 90;
    const batches = [];
    for (let index = 0; index < skus.length; index += CUSTOMER_MESSAGES_BATCH) batches.push(skus.slice(index, index + CUSTOMER_MESSAGES_BATCH));
    const started = Date.now();
    let reviews = 0, questions = 0, competitorReviews = 0, competitorRequests = 0;
    let reviewSkus = 0, questionSkus = 0, competitorReviewSkus = 0, errors = 0;
    box.className = "seo-project-progress";
    const marketplace = String(seoState.detail?.project?.marketplace || seoState.marketplace).toLowerCase();
    const sourcePlan = marketplace === "wb" ? "свои отзывы и вопросы: синхронизированный WB Seller API · конкуренты: только при доступном источнике" : "свои и конкуренты: MPStats Ozon · вопросы: Ozon Seller API";
    box.textContent = `ПЛАН ОТЗЫВОВ: ${skus.length} SKU · ${batches.length} батчей по ${CUSTOMER_MESSAGES_BATCH} SKU · до ${CUSTOMER_MESSAGES_BATCH * 10} конкурентов на батч · последние ${periodDays} дней · ${sourcePlan}\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | своих отзывов ${reviews} | отзывов конкурентов ${competitorReviews} | вопросов ${questions} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-customer-messages/collect?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], period_days: periodDays,
        }, box, `отзывы + вопросы + конкуренты · батч ${i + 1}/${batches.length}`);
        reviews += Number(result.review_count || 0);
        questions += Number(result.question_count || 0);
        competitorReviews += Number(result.competitor_review_count || 0);
        competitorRequests += Number(result.competitor_review_requests || 0);
        reviewSkus += Number(result.review_skus || 0);
        questionSkus += Number(result.question_skus || 0);
        competitorReviewSkus += Number(result.competitor_review_skus || 0);
        errors += (result.errors || []).length;
        (result.errors || []).forEach((item) => { box.textContent += `ОШИБКА ИСТОЧНИКА: ${item.sku ? `SKU ${item.sku} · ` : ""}${item.competitor_sku ? `конкурент ${item.competitor_sku} · ` : ""}${item.type || "источник"} | ${item.error || "нет деталей"}\n`; });
        box.textContent += `[${i + 1}/${batches.length}] батч готов: своих отзывов ${num(result.review_count)} · отзывов конкурентов ${num(result.competitor_review_count)} по ${num(result.competitor_count)} конкурентам · вопросов ${num(result.question_count)} · период ${num(result.period_days)} дней · ${result.partial ? "частично" : "полностью"}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА ОТЗЫВОВ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ: SKU ${skus.length} | своих отзывов ${reviews} по ${reviewSkus} SKU | отзывов конкурентов ${competitorReviews} по ${competitorReviewSkus} нашим SKU (${competitorRequests} запросов) | вопросов ${questions} по ${questionSkus} SKU | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Отзывы + вопросы + конкуренты", finalLog, errors);
  }

  async function collectQuestionSkus(skus, options = {}) {
    const box = showCollectionTerminal("Вопросы Ozon");
    const periodDays = [30, 90].includes(Number(options.feedback_days)) ? Number(options.feedback_days) : 90;
    const batches = [];
    for (let index = 0; index < skus.length; index += CUSTOMER_QUESTIONS_BATCH) batches.push(skus.slice(index, index + CUSTOMER_QUESTIONS_BATCH));
    const started = Date.now();
    let questions = 0, questionSkus = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН ВОПРОСОВ: ${skus.length} SKU · ${batches.length} батчей по ${CUSTOMER_QUESTIONS_BATCH} SKU · последние ${periodDays} дней · источник: Ozon Seller API · вопросы конкурентов: источник Ozon/MPStats недоступен\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | вопросов ${questions} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-questions/collect?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], period_days: periodDays,
        }, box, `вопросы · батч ${i + 1}/${batches.length}`);
        questions += Number(result.question_count || 0);
        questionSkus += Number(result.question_skus || 0);
        errors += (result.errors || []).length;
        (result.errors || []).forEach((item) => { box.textContent += `ОШИБКА ИСТОЧНИКА: ${item.type || "вопросы"} | ${item.error || "нет деталей"}\n`; });
        box.textContent += `[${i + 1}/${batches.length}] батч готов: вопросов ${num(result.question_count)} · SKU проверено ${num(result.question_skus)} · период ${num(result.period_days)} дней\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА ВОПРОСОВ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ ВОПРОСОВ: SKU ${skus.length} | вопросов ${questions} | источник проверен по ${questionSkus} SKU | вопросов конкурентов нет: MPStats Ozon FAQ недоступен | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Вопросы Ozon", finalLog, errors);
  }

  async function analyzeCustomerVoiceSkus(skus) {
    const box = showCollectionTerminal("Customer voice → SEO-клеймы");
    const batches = [];
    for (let index = 0; index < skus.length; index += CUSTOMER_VOICE_BATCH) batches.push(skus.slice(index, index + CUSTOMER_VOICE_BATCH));
    const started = Date.now();
    let analyzedSkus = 0, messages = 0, claims = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН КЛЕЙМОВ: ${skus.length} SKU · ${batches.length} батчей по ${CUSTOMER_VOICE_BATCH} SKU · свои отзывы + свои вопросы + отзывы топ-10 конкурентов → клеймы с source refs → статус «можно использовать» или «проверить»\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | сообщений ${messages} | клеймов ${claims} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-customer-voice/analyze?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i],
        }, box, `клеймы · батч ${i + 1}/${batches.length}`);
        analyzedSkus += Number(result.analyzed_skus || 0);
        messages += Number(result.message_count || 0);
        claims += Number(result.claim_count || 0);
        errors += (result.errors || []).length;
        (result.errors || []).forEach((item) => { box.textContent += `ОШИБКА АНАЛИЗА: SKU ${item.sku || "—"} | ${item.error || "нет деталей"}\n`; });
        box.textContent += `[${i + 1}/${batches.length}] батч готов: сообщений ${num(result.message_count)} · клеймов ${num(result.claim_count)} · модель ${result.model || "—"} · ${result.partial ? "частично" : "полностью"}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА КЛЕЙМОВ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ КЛЕЙМОВ: SKU ${skus.length} | проанализировано ${analyzedSkus} | сообщений ${messages} | клеймов ${claims} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Customer voice → SEO-клеймы", finalLog, errors);
  }

  async function prepareSemanticContextSkus(skus) {
    const box = showCollectionTerminal("Подготовка SEO-контекста");
    const batchSize = 20, batches = [];
    for (let index = 0; index < skus.length; index += batchSize) batches.push(skus.slice(index, index + batchSize));
    const started = Date.now(); let prepared = 0, partial = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН SEO-КОНТЕКСТА: ${skus.length} SKU · ${batches.length} батчей по ${batchSize} · текущий контент + SEO-характеристики + очищенные свои ключи + очищенные ключи конкурентов + клеймы → компактный пакет для ИИ\n`;
    for (let index = 0; index < batches.length; index += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = index ? Math.round((elapsed / index) * (batches.length - index)) : "—";
      box.textContent += `ПРОГРЕСС: ${index + 1}/${batches.length} (${Math.round((index + 1) * 100 / batches.length)}%) | SKU ${batches[index].length} | готово ${prepared} | неполных ${partial} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-semantic-context/prepare?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind,
          project_id: seoState.projectId, skus: batches[index],
        }, box, `SEO-контекст · батч ${index + 1}/${batches.length}`);
        prepared += Number(result.prepared || 0); partial += Number(result.partial || 0);
        errors += (result.errors || []).length;
        box.textContent += `[${index + 1}/${batches.length}] готово: SKU ${num(result.prepared)} · полных ${num(result.complete)} · неполных ${num(result.partial)}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА SEO-КОНТЕКСТА: батч ${index + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ SEO-КОНТЕКСТА: SKU ${skus.length} | подготовлено ${prepared} | неполных ${partial} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    await finishCollectionRun("Подготовка SEO-контекста", box.textContent, errors);
    return { prepared, partial, errors };
  }

  async function generateContentAllocationSku(sku) {
    const box = showCollectionTerminal("ИИ-разметка SEO по полям Ozon");
    const started = Date.now();
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН SEO-РАЗМЕТКИ: 1 SKU · проверенный SEO-контекст → ключи названия → ключи описания → подтверждённые клеймы → активные фразы мониторинга\n`;
    box.textContent += `ПРОГРЕСС: 0/1 (0%) | SKU ${sku} | вызов основной модели · резерв только при ошибке | ETA до 120 сек.\n`;
    try {
      const result = await post("/api/seo-project-content-allocation/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind,
        project_id: seoState.projectId, sku,
      });
      box.textContent += `ПРОГРЕСС: 1/1 (100%) | SKU ${sku} | выбрано ${num(result.selected)} фраз | модель ${result.model || "—"}\n`;
      box.textContent += `ИТОГ SEO-РАЗМЕТКИ: статус ${result.status || "—"} | мониторинг ${num(result.selected)} ключей | ошибок 0 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      await finishCollectionRun("ИИ-разметка SEO по полям Ozon", box.textContent, 0);
      return result;
    } catch (error) {
      box.classList.add("error");
      box.textContent += `ОШИБКА SEO-РАЗМЕТКИ: ${error.message}\nИТОГ: SKU 1 | готово 0 | ошибок 1 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      await finishCollectionRun("ИИ-разметка SEO по полям Ozon", box.textContent, 1);
      throw error;
    }
  }

  async function generateContentAllocationSkus(skus) {
    const box = showCollectionTerminal("ИИ-разметка SEO по полям Ozon");
    const started = Date.now(), workers = Math.min(3, Math.max(1, skus.length));
    let completed = 0, partial = 0, selected = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН SEO-РАЗМЕТКИ: ${skus.length} SKU · ${workers} параллельных воркера · SEO-контекст → ключи и характеристики по полям → мониторинг\n`;
    for (let offset = 0; offset < skus.length; offset += workers) {
      const group = skus.slice(offset, offset + workers);
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = completed ? Math.round((elapsed / completed) * (skus.length - completed)) : "—";
      box.textContent += `ПРОГРЕСС: ${completed}/${skus.length} (${Math.round(completed * 100 / skus.length)}%) | группа ${group.join(", ")} | готово ${completed} | частично ${partial} | ошибок ${errors} | ETA ${eta} сек.\n`;
      const results = await Promise.allSettled(group.map((sku) => post("/api/seo-project-content-allocation/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku,
      })));
      results.forEach((result, index) => {
        const sku = group[index];
        if (result.status === "fulfilled") {
          completed += 1; selected += Number(result.value.selected || 0);
          if (result.value.status === "partial") partial += 1;
          box.textContent += `[${completed + errors}/${skus.length}] SKU ${sku}: ${result.value.status || "ok"} · ключей ${num(result.value.selected)} · ${result.value.model || "—"}\n`;
        } else {
          errors += 1;
          box.textContent += `ОШИБКА SEO-РАЗМЕТКИ: SKU ${sku} | ${result.reason?.message || result.reason}\n`;
        }
      });
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ SEO-РАЗМЕТКИ: SKU ${skus.length} | готово ${completed} | частично ${partial} | ключей мониторинга ${selected} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    await finishCollectionRun("ИИ-разметка SEO по полям Ozon", box.textContent, errors);
    return { completed, partial, errors };
  }

  async function generateContentDraftSku(sku) {
    const box = showCollectionTerminal("Генерация SEO-текста");
    const started = Date.now();
    box.className = "seo-project-progress";
    const marketplace = String(seoState.detail?.project?.marketplace || seoState.marketplace).toLowerCase();
    const titleLimit = marketplace === "wb" ? 60 : 200;
    box.textContent = `ПЛАН SEO-ТЕКСТА: 1 SKU · SEO-разметка → название до ${titleLimit} символов + описание + внутренние SEO-метки · без публикации на маркетплейс\n`;
    box.textContent += `ПРОГРЕСС: 0/1 (0%) | SKU ${sku} | Codex gpt-5.6-luna · reasoning low · резерв только при реальном сбое | ETA до 120 сек.\n`;
    try {
      const result = await post("/api/seo-project-content-draft/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku,
      });
      box.textContent += `ПРОГРЕСС: 1/1 (100%) | SKU ${sku} | название ${String(result.draft?.title || "").length} зн. | хештегов ${num(result.draft?.hashtags?.length)} | модель ${result.model || "—"}\n`;
      box.textContent += `ИТОГ SEO-ТЕКСТА: статус ${result.status || "—"} | черновик сохранён | публикация не выполнялась | ошибок 0 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      await finishCollectionRun("Генерация SEO-текста", box.textContent, 0);
      return result;
    } catch (error) {
      box.classList.add("error");
      box.textContent += `ОШИБКА SEO-ТЕКСТА: ${error.message}\nИТОГ: SKU 1 | готово 0 | ошибок 1 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      await finishCollectionRun("Генерация SEO-текста", box.textContent, 1);
      throw error;
    }
  }

  async function generateContentDraftSkus(skus) {
    const box = showCollectionTerminal("Генерация SEO-текстов");
    const started = Date.now(), workers = Math.min(3, Math.max(1, skus.length));
    let completed = 0, partial = 0, errors = 0;
    box.className = "seo-project-progress";
    const marketplace = String(seoState.detail?.project?.marketplace || seoState.marketplace).toLowerCase();
    const titleLimit = marketplace === "wb" ? 60 : 200;
    box.textContent = `ПЛАН SEO-ТЕКСТОВ: ${skus.length} SKU · ${workers} параллельных воркера · готовая SEO-разметка → название до ${titleLimit} символов + описание + внутренние SEO-метки · без публикации на маркетплейс\n`;
    for (let offset = 0; offset < skus.length; offset += workers) {
      const group = skus.slice(offset, offset + workers);
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = completed ? Math.round((elapsed / completed) * (skus.length - completed)) : "—";
      box.textContent += `ПРОГРЕСС: ${completed}/${skus.length} (${Math.round(completed * 100 / skus.length)}%) | группа ${group.join(", ")} | готово ${completed} | частично ${partial} | ошибок ${errors} | ETA ${eta} сек.\n`;
      const results = await Promise.allSettled(group.map((sku) => post("/api/seo-project-content-draft/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku,
      })));
      results.forEach((result, index) => {
        const sku = group[index];
        if (result.status === "fulfilled") {
          completed += 1;
          if (result.value.status === "partial") partial += 1;
          box.textContent += `[${completed + errors}/${skus.length}] SKU ${sku}: ${result.value.status || "ok"} · название ${String(result.value.draft?.title || "").length} зн. · хештегов ${num(result.value.draft?.hashtags?.length)} · ${result.value.model || "—"}\n`;
        } else {
          errors += 1;
          box.textContent += `ОШИБКА SEO-ТЕКСТА: SKU ${sku} | ${result.reason?.message || result.reason}\n`;
        }
      });
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ SEO-ТЕКСТОВ: SKU ${skus.length} | готово ${completed} | частично ${partial} | ошибок ${errors} | публикация не выполнялась | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    await finishCollectionRun("Генерация SEO-текстов", box.textContent, errors);
    return { completed, partial, errors };
  }

  async function reviewContentDraftSku(sku) {
    const box = showCollectionTerminal("Экспертная оценка SEO-текста");
    const started = Date.now();
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН ЭКСПЕРТНОЙ ОЦЕНКИ: 1 SKU · русский язык + логика + запреты Ozon + сверка пола, материала, цвета и подтверждённых характеристик\n`;
    box.textContent += `ПРОГРЕСС: 0/1 (0%) | SKU ${sku} | Codex gpt-5.6-terra · reasoning medium · до 4 раундов точечных правок | ETA до 480 сек.\n`;
    try {
      const result = await post("/api/seo-project-content-review/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, apply_fixes: true,
      });
      const review = result.review || {};
      const remediation = result.remediation || {};
      if (remediation.attempted) box.textContent += `ТОЧЕЧНАЯ РЕДАКТУРА: предложено ${num((remediation.suggested_edits || []).length)} | применено ${num((remediation.applied_edits || []).length)} | принято после перепроверки ${remediation.accepted ? "да" : "нет"}\n`;
      box.textContent += `ПРОГРЕСС: 1/1 (100%) | SKU ${sku} | вердикт ${review.verdict || "—"} · балл ${num(review.score)} · блокирующих фактов ${num(review.hard_failures?.length)} | ${result.model || "—"}\n`;
      box.textContent += `ИТОГ ЭКСПЕРТИЗЫ: ${review.publication_allowed ? "текст прошёл шлюз" : "текст заблокирован до исправления"} | публикация не выполнялась | ошибок запуска 0 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      if (!review.publication_allowed) box.classList.add("error");
      await finishCollectionRun("Экспертная оценка SEO-текста", box.textContent, review.publication_allowed ? 0 : 1);
      return result;
    } catch (error) {
      box.classList.add("error");
      box.textContent += `ОШИБКА ЭКСПЕРТИЗЫ: ${error.message}\nИТОГ: SKU 1 | проверено 0 | ошибок 1 | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
      await finishCollectionRun("Экспертная оценка SEO-текста", box.textContent, 1);
      throw error;
    }
  }

  async function reviewContentDraftSkus(skus) {
    const box = showCollectionTerminal("Экспертная оценка SEO-текстов");
    const started = Date.now(), workers = Math.min(2, Math.max(1, skus.length));
    let approved = 0, blocked = 0, errors = 0, processed = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН ЭКСПЕРТИЗЫ: ${skus.length} SKU · ${workers} параллельных воркера · Terra medium · фактологические конфликты блокируют готовность\n`;
    for (let offset = 0; offset < skus.length; offset += workers) {
      const group = skus.slice(offset, offset + workers);
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = processed ? Math.round((elapsed / processed) * (skus.length - processed)) : "—";
      box.textContent += `ПРОГРЕСС: ${processed}/${skus.length} (${Math.round(processed * 100 / skus.length)}%) | группа ${group.join(", ")} | прошло ${approved} | заблокировано ${blocked} | ошибок ${errors} | ETA ${eta} сек.\n`;
      const results = await Promise.allSettled(group.map((sku) => post("/api/seo-project-content-review/generate?dashboard=seoMonitoring", {
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, apply_fixes: true,
      })));
      results.forEach((result, index) => {
        const sku = group[index]; processed += 1;
        if (result.status === "fulfilled") {
          const review = result.value.review || {};
          if (review.publication_allowed) approved += 1; else blocked += 1;
          box.textContent += `[${processed}/${skus.length}] SKU ${sku}: ${review.verdict || "—"} · ${num(review.score)} баллов · конфликтов ${num(review.hard_failures?.length)} · точечных правок ${num((result.value.remediation?.applied_edits || []).length)} · ${result.value.model || "—"}\n`;
        } else {
          errors += 1;
          box.textContent += `ОШИБКА ЭКСПЕРТИЗЫ: SKU ${sku} | ${result.reason?.message || result.reason}\n`;
        }
      });
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ ЭКСПЕРТИЗЫ: SKU ${skus.length} | прошло ${approved} | заблокировано ${blocked} | ошибок ${errors} | публикация не выполнялась | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (blocked || errors) box.classList.add("error");
    await finishCollectionRun("Экспертная оценка SEO-текстов", box.textContent, blocked + errors);
    return { approved, blocked, errors };
  }

  async function analyzeKeywordSkus(skus) {
    const box = showCollectionTerminal("Очистка + ранжирование");
    const batchSize = 5;
    const batches = [];
    for (let index = 0; index < skus.length; index += batchSize) batches.push(skus.slice(index, index + batchSize));
    const started = Date.now();
    let analyzed = 0, kept = 0, rejected = 0, review = 0, priority = 0, aiChecked = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН ОЧИСТКИ: ${skus.length} SKU · ${batches.length} батчей по ${batchSize} SKU · локальные hard-gates SEO-бота → AI для неоднозначных фраз → ВЧ/СЧ/НЧ отдельно для API и MPStats → общий приоритет\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | проверено ${analyzed} | оставлено ${kept} | удалено ${rejected} | проверить ${review} | приоритет ${priority} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-keywords/analyze?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], ai_limit: 3000,
        }, box, `очистка · батч ${i + 1}/${batches.length}`);
        analyzed += Number(result.analyzed || 0);
        kept += Number(result.kept || 0);
        rejected += Number(result.rejected || 0);
        review += Number(result.review || 0);
        priority += Number(result.priority || 0);
        aiChecked += Number(result.ai_checked || 0);
        box.textContent += `[${i + 1}/${batches.length}] готово: ключей ${num(result.analyzed)} · релевантных ${num(result.kept)} · отклонено ${num(result.rejected)} · AI ${num(result.ai_checked)} · приоритет ${num(result.priority)}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА ОЧИСТКИ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ ОЧИСТКИ: SKU ${skus.length} | проверено ${analyzed} | релевантных ${kept} | отклонено ${rejected} | проверить ${review} | AI ${aiChecked} | приоритетных ${priority} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Очистка + ранжирование", finalLog, errors);
  }

  async function generateIntentSkus(skus) {
    const box = showCollectionTerminal("Определение интентов");
    const batchSize = 10;
    const batches = [];
    for (let index = 0; index < skus.length; index += batchSize) batches.push(skus.slice(index, index + batchSize));
    const started = Date.now();
    let generated = 0, ai = 0, fallback = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН ИНТЕНТОВ: ${skus.length} SKU · ${batches.length} батчей по ${batchSize} SKU · название + описание + характеристики + очищенные ключи API/MPStats → одна основная поисковая сущность\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | готово ${generated} | AI ${ai} | резерв ${fallback} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-intents/generate?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i],
        }, box, `интенты · батч ${i + 1}/${batches.length}`);
        generated += Number(result.generated || 0);
        ai += Number(result.ai_generated || 0);
        fallback += Number(result.fallback_generated || 0);
        box.textContent += `[${i + 1}/${batches.length}] готово: интентов ${num(result.generated)} · AI ${num(result.ai_generated)} · резерв ${num(result.fallback_generated)}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА ИНТЕНТОВ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ ИНТЕНТОВ: SKU ${skus.length} | готово ${generated} | AI ${ai} | резерв ${fallback} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun("Определение интентов", finalLog, errors);
  }

  async function collectCompetitorSkus(skus, options = {}) {
    const marketplaceLabel = projectMarketplace() === "wb" ? "WB" : "Ozon";
    const box = showCollectionTerminal(`MPStats ${marketplaceLabel} · Товары в поиске`);
    const batchSize = 10;
    const batches = [];
    for (let index = 0; index < skus.length; index += batchSize) batches.push(skus.slice(index, index + batchSize));
    const started = Date.now();
    let rows = 0, requests = 0, reused = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН КОНКУРЕНТОВ: ${skus.length} SKU · ${batches.length} батчей по ${batchSize} SKU · MPStats ${marketplaceLabel} «Товары в поиске» · запрос = интент товара · топ-5 выдачи · ${options.date_from}..${options.date_to}\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000);
      const eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | конкурентов ${rows} | запросов ${requests} | повторно ${reused} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-niche-competitors/collect?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], date_from: options.date_from, date_to: options.date_to, competitor_limit: 5,
        }, box, `конкуренты · батч ${i + 1}/${batches.length}`);
        rows += Number(result.row_count || 0);
        requests += Number(result.requests || 0);
        reused += Number(result.reused || 0);
        errors += (result.errors || []).length;
        box.textContent += `[${i + 1}/${batches.length}] готово: строк ${num(result.row_count)} · запросов MPStats ${num(result.requests)} · переиспользовано ${num(result.reused)} · ошибок ${(result.errors || []).length}\n`;
      } catch (error) {
        errors += 1;
        box.textContent += `ОШИБКА КОНКУРЕНТОВ: батч ${i + 1}/${batches.length} | ${error.message}\n`;
      }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ КОНКУРЕНТОВ: SKU ${skus.length} | строк ${rows} | запросов MPStats ${requests} | переиспользовано ${reused} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error");
    const finalLog = box.textContent;
    await finishCollectionRun(`MPStats ${marketplaceLabel} · Товары в поиске`, finalLog, errors);
  }

  async function collectCompetitorKeywordSkus(skus, options = {}) {
    const box = showCollectionTerminal("MPStats · ключи конкурентов");
    const batchSize = 5, batches = [];
    for (let index = 0; index < skus.length; index += batchSize) batches.push(skus.slice(index, index + batchSize));
    const started = Date.now(); let rows = 0, requests = 0, reused = 0, errors = 0;
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН КЛЮЧЕЙ КОНКУРЕНТОВ: ${skus.length} SKU · ${batches.length} батчей по ${batchSize} · до 5 конкурентов на SKU · максимум 50 ключей на конкурента · MPStats карточные ключи · ${options.date_from}..${options.date_to}\n`;
    for (let i = 0; i < batches.length; i += 1) {
      const elapsed = Math.max(1, (Date.now() - started) / 1000), eta = i ? Math.round((elapsed / i) * (batches.length - i)) : "—";
      box.textContent += `ПРОГРЕСС: ${i + 1}/${batches.length} (${Math.round((i + 1) * 100 / batches.length)}%) | SKU ${batches[i].length} | ключей ${rows} | запросов ${requests} | кэш ${reused} | ошибок ${errors} | ETA ${eta} сек.\n`;
      try {
        const result = await postCollectionBatchWithRetry("/api/seo-project-competitor-keywords/collect?dashboard=seoMonitoring", {
          client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
          skus: batches[i], date_from: options.date_from, date_to: options.date_to,
          keyword_limit_per_competitor: 50,
        }, box, `ключи конкурентов · батч ${i + 1}/${batches.length}`);
        rows += Number(result.row_count || 0); requests += Number(result.requests || 0); reused += Number(result.reused || 0); errors += (result.errors || []).length;
        box.textContent += `[${i + 1}/${batches.length}] готово: ключей ${num(result.row_count)} · конкурентов ${num(result.competitor_count)} · запросов ${num(result.requests)} · кэш ${num(result.reused)} · ошибок ${(result.errors || []).length}\n`;
      } catch (error) { errors += 1; box.textContent += `ОШИБКА КЛЮЧЕЙ КОНКУРЕНТОВ: ${error.message}\n`; }
      box.scrollTop = box.scrollHeight;
    }
    box.textContent += `ИТОГ: SKU ${skus.length} | ключей ${rows} | запросов MPStats ${requests} | кэш ${reused} | ошибок ${errors} | ${Math.round((Date.now() - started) / 1000)} сек.\n`;
    if (errors) box.classList.add("error"); const finalLog = box.textContent; await finishCollectionRun("MPStats · ключи конкурентов", finalLog, errors);
  }

  async function analyzeCompetitorKeywordSkus(skus) {
    const runKey = `competitor-keywords:${seoState.projectId}`;
    if (seoState.activeRuns.has(runKey)) throw new Error("Очистка ключей конкурентов уже запущена в этой вкладке");
    const box = showCollectionTerminal("AI · ключи конкурентов");
    box.className = "seo-project-progress";
    box.textContent = `ПЛАН AI: ${skus.length} SKU · один серверный запуск · минус-словарь → локальные правила SEO-бота → кэш уникальных «интент + запрос» → ИИ только для остатка → ВЧ/СЧ/НЧ и приоритет\n`;
    const signal = seoState.skuWorkspaceRun?.controller?.signal;
    const result = await post("/api/seo-project-competitor-keywords/analyze-job/start?dashboard=seoMonitoring", {
      client: seoState.client, project_kind: seoState.projectKind,
      project_id: seoState.projectId, skus,
    }, signal ? { signal } : {});
    if (seoState.skuWorkspaceRun) seoState.skuWorkspaceRun.jobId = result.job.job_id;
    await pollCompetitorAnalysisJob(result.job.job_id, box);
  }

  function competitorJobLine(job) {
    const eta = job.eta_seconds == null ? "—" : `${num(job.eta_seconds)} сек.`;
    return `ПРОГРЕСС: ${num(job.done_skus)}/${num(job.total_skus)} (${num(job.percent, 1)}%) | SKU ${esc(job.current_sku || "—")} | проверено ${num(job.analyzed)} | релевантных ${num(job.kept)} | мусор ${num(job.rejected)} | проверить ${num(job.review)} | кэш ${num(job.cache_hits)} | ИИ ${num(job.ai_checked)} | ошибок ${num(job.error_count)} | ETA ${eta}`;
  }

  async function pollCompetitorAnalysisJob(jobId, existingBox = null) {
    const runKey = `competitor-keywords:${seoState.projectId}`;
    if (seoState.activeRuns.has(runKey)) return;
    seoState.activeRuns.add(runKey);
    const box = existingBox || showCollectionTerminal("AI · ключи конкурентов");
    const stopButton = seoState.root.querySelector("[data-seo-stop-competitor-ai]");
    if (stopButton) { stopButton.hidden = false; stopButton.dataset.jobId = jobId; stopButton.disabled = false; }
    let lastMessage = "";
    try {
      while (seoState.projectId) {
        const params = new URLSearchParams({
          client: seoState.client, project_id: seoState.projectId,
          job_id: jobId, dashboard: "seoMonitoring",
        });
        const payload = await json(`/api/seo-project-competitor-keywords/analyze-job/status?${params}`);
        const job = payload.job;
        if (!job) throw new Error("Серверное задание не найдено");
        box.className = `seo-project-progress${job.status === "error" ? " error" : ""}`;
        const lines = [
          `ПЛАН AI: ${num(job.total_skus)} SKU · один серверный запуск · checkpoint после каждого пакета · повторы запросов берутся из кэша`,
          competitorJobLine(job),
        ];
        if (job.last_message) lines.push(job.last_message);
        if (job.last_error && job.last_error !== lastMessage) lines.push(`ОШИБКА: ${job.last_error}`);
        box.textContent = `${lines.join("\n")}\n`;
        box.scrollTop = box.scrollHeight;
        lastMessage = job.last_error || "";
        if (!job.is_active) {
          const finalLabel = job.status === "completed" ? "ИТОГ AI" : (job.status === "stopped" ? "ОСТАНОВЛЕНО" : "ИТОГ С ОШИБКАМИ");
          box.textContent += `${finalLabel}: проверено ${num(job.analyzed)} | релевантных ${num(job.kept)} | удалено ${num(job.rejected)} | проверить ${num(job.review)} | кэш ${num(job.cache_hits)} | ИИ ${num(job.ai_checked)} | ошибок ${num(job.error_count)}\n`;
          if (job.status === "error") box.classList.add("error");
          await loadProjectSkus(seoState.projectPage);
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    } finally {
      seoState.activeRuns.delete(runKey);
      if (stopButton) { stopButton.hidden = true; stopButton.dataset.jobId = ""; stopButton.disabled = false; }
    }
  }

  async function resumeCompetitorAnalysisJob() {
    const params = new URLSearchParams({
      client: seoState.client, project_id: seoState.projectId, dashboard: "seoMonitoring",
    });
    const payload = await json(`/api/seo-project-competitor-keywords/analyze-job/status?${params}`);
    if (payload.job?.is_active) {
      const box = showCollectionTerminal("AI · ключи конкурентов");
      box.className = "seo-project-progress";
      box.textContent = "ВОЗОБНОВЛЕНИЕ: найден незавершённый серверный запуск. Подключаю терминал к сохранённому checkpoint.\n";
      await pollCompetitorAnalysisJob(payload.job.job_id, box);
    }
  }

  function applyProjectFilterState(state) {
    // A saved project is already a fixed SKU group. Selection-time filters must not narrow it again.
    seoState.projectFilters = {};
    seoState.projectSkuFilters = {};
    seoState.projectSkuSort = validSort("sku", state?.sku_sort, { key: "sales_14d_rub", dir: "desc" });
    seoState.projectPage = 1;
    seoState.projectCandidates = null;
  }

  function projectFilterParams() {
    const result = { ...seoState.projectFilters };
    if (Object.prototype.hasOwnProperty.call(result, "query")) {
      result.q = result.query;
      delete result.query;
    }
    return result;
  }

  async function loadProjectSkus(page = 1) {
    seoState.projectPage = Math.max(1, Number.parseInt(page, 10) || 1);
    const rows = seoState.root.querySelector("[data-seo-project-rows]");
    if (rows) rows.innerHTML = `<tr><td colspan="${tableColumns("project").length}">Загружаю SKU проекта…</td></tr>`;
    const params = new URLSearchParams({
      client: seoState.client, marketplace: seoState.detail?.project?.marketplace || seoState.marketplace,
      dashboard: "seoMonitoring", project_id: seoState.projectId, limit: "50", page: String(seoState.projectPage),
      sort_col: seoState.projectSkuSort.key, sort_dir: seoState.projectSkuSort.dir,
      column_filters: JSON.stringify(seoState.projectSkuFilters), ...projectFilterParams(),
    });
    try {
      seoState.projectCandidates = await json(`/api/seo-project-candidates?${params}`);
      renderProjectTable();
    } catch (error) {
      if (rows) rows.innerHTML = `<tr><td colspan="${tableColumns("project").length}">Не удалось загрузить SKU проекта: ${esc(error.message)}</td></tr>`;
    }
  }

  function renderProjectTable() {
    const target = seoState.root.querySelector("[data-seo-project-rows]");
    const data = seoState.projectCandidates;
    if (!target || !data) return;
    target.innerHTML = (data.rows || []).map((row) => {
      const product = row.product_name || "Без названия";
      const model = row.gj_model || "—";
      const keywordButton = (source, count, label) => count === null || count === undefined
        ? "—"
        : `<button type="button" class="seo-cell-icon-btn" data-seo-keywords-open="${source}" data-seo-sku="${esc(row.sku)}" data-seo-product="${esc(product)}" aria-label="${esc(label)} для SKU ${esc(row.sku)}" title="${esc(label)}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8"/></svg><span>${num(count)}</span></button>`;
      const competitorButton = row.niche_competitor_count === null || row.niche_competitor_count === undefined
        ? "—"
        : `<button type="button" class="seo-cell-icon-btn" data-seo-competitors-open="${esc(row.sku)}" data-seo-product="${esc(product)}" aria-label="Конкуренты по интенту для SKU ${esc(row.sku)}" title="MPStats ${projectMarketplace() === "wb" ? "WB" : "Ozon"} · Товары в поиске"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8"/></svg><span>${num(row.niche_competitor_count)}</span></button>`;
      const competitorKeywordButton = row.competitor_keyword_count === null || row.competitor_keyword_count === undefined ? "—" : `<button type="button" class="seo-cell-icon-btn" data-seo-competitor-keywords-open="${esc(row.sku)}" data-seo-product="${esc(product)}" title="Ключи товаров-конкурентов"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8"/></svg><span>${num(row.competitor_keyword_count)}</span></button>`;
      const customerMessageButton = (type, count, status, error, label) => {
        if (!status) return "—";
        const failed = status !== "ok";
        const title = failed ? `${label}: ${error || (status === "unavailable" ? "источник недоступен" : "ошибка источника")}` : `${label} · ${num(row.customer_message_period_days)} дней`;
        return `<button type="button" class="seo-cell-icon-btn ${failed ? "has-error" : ""}" data-seo-customer-messages-open="${type}" data-seo-sku="${esc(row.sku)}" data-seo-product="${esc(product)}" aria-label="${esc(label)} для SKU ${esc(row.sku)}" title="${esc(title)}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v9H9l-4 3v-3H4z"/></svg><span>${failed ? "!" : num(count)}</span></button>`;
      };
      const competitorReviewButton = !row.competitor_review_status ? "—" : `<button type="button" class="seo-cell-icon-btn ${row.competitor_review_status === "ok" ? "" : "has-error"}" data-seo-competitor-reviews-open="${esc(row.sku)}" data-seo-product="${esc(product)}" title="${esc(row.competitor_review_status === "ok" ? `Отзывы топ-10 конкурентов · ${num(row.competitor_review_period_days)} дней` : (row.competitor_review_error || "Ошибка сбора отзывов конкурентов"))}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v9H9l-4 3v-3H4zM7 7h6M7 10h4"/></svg><span>${row.competitor_review_status === "ok" ? num(row.competitor_review_count) : "!"}</span></button>`;
      const competitorQuestionCell = '<span class="seo-stage-unavailable" title="MPStats Ozon FAQ для товаров-конкурентов недоступен; значение не считается нулём">Недоступно</span>';
      const claimsButton = !row.customer_voice_status ? "—" : `<button type="button" class="seo-cell-icon-btn ${row.customer_voice_status === "ok" ? "" : "has-error"}" data-seo-customer-voice-open="${esc(row.sku)}" data-seo-product="${esc(product)}" title="${esc(row.customer_voice_status === "ok" ? `Клеймы из customer voice · ${row.customer_voice_model || "модель"}` : (row.customer_voice_error || "Анализ не выполнен"))}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v12H4zM7 8h6M7 11h4"/></svg><span>${row.customer_voice_status === "ok" ? num(row.customer_voice_claim_count) : "!"}</span></button>`;
      const cell = (key, value, className = "", title = "") => `<td data-seo-column="${key}" ${className ? `class="${className}"` : ""} ${title ? `title="${esc(title)}"` : ""}>${value}</td>`;
      const cells = {
        product_name: cell("product_name", `<strong title="${esc(product)}">${esc(product)}</strong><span title="${esc(row.sku)}">${esc(row.sku)}</span>`, "seo-builder-product"),
        sku: cell("sku", `<button type="button" class="seo-sku-workspace-trigger" data-seo-sku-workspace="${esc(row.sku)}" aria-label="Открыть рабочую карточку SKU ${esc(row.sku)}" title="Открыть рабочую карточку SKU">${esc(row.sku)}</button>`, "seo-builder-sku"),
        gj_model: cell("gj_model", esc(model), "", model),
        total_stock_qty: cell("total_stock_qty", num(row.total_stock_qty), "number"),
        orders_14d: cell("orders_14d", num(row.orders_14d), "number"),
        sales_14d_rub: cell("sales_14d_rub", rub(row.sales_14d_rub), "number"),
        average_position_14d: cell("average_position_14d", num(row.average_position_14d, 1), "number"),
        orders_trend_pct: cell("orders_trend_pct", trendCell(row)),
        seo_signal: cell("seo_signal", seoSignalCell(row)),
        reyting_kartochki: cell("reyting_kartochki", num(row.reyting_kartochki, 1), "number"),
        reyting_po_otzyvam: cell("reyting_po_otzyvam", num(row.reyting_po_otzyvam, 1), "number"),
        api_keyword_count: cell("api_keyword_count", keywordButton("api", row.api_keyword_count, projectMarketplace() === "wb" ? "Ключи WB" : "Ключи API"), "number"),
        mpstats_keyword_count: cell("mpstats_keyword_count", keywordButton("mpstats", row.mpstats_keyword_count, "Ключи MPStats"), "number"),
        niche_name: cell("niche_name", esc(row.niche_name || "—"), "", row.niche_name || "Ниша не определена в каталоге"),
        search_intent: cell("search_intent", esc(row.search_intent || "—"), "", row.search_intent || "Интент ещё не определён"),
        niche_competitor_count: cell("niche_competitor_count", competitorButton, "number"),
        competitor_keyword_count: cell("competitor_keyword_count", competitorKeywordButton, "number"),
        review_count: cell("review_count", customerMessageButton("review", row.review_count, row.review_status, row.review_error, projectMarketplace() === "wb" ? "Отзывы WB" : "Свои отзывы"), "number"),
        question_count: cell("question_count", customerMessageButton("question", row.question_count, row.question_status, row.question_error, projectMarketplace() === "wb" ? "Вопросы WB" : "Свои вопросы"), "number"),
        competitor_review_count: cell("competitor_review_count", competitorReviewButton, "number"),
        competitor_question_status: cell("competitor_question_status", competitorQuestionCell),
        customer_voice_claim_count: cell("customer_voice_claim_count", claimsButton, "number"),
        product_card: cell("product_card", `<button type="button" class="seo-cell-icon-btn icon-only" data-seo-product-card="${esc(row.sku)}" data-seo-product="${esc(product)}" aria-label="Открыть характеристики SKU ${esc(row.sku)}" title="Полная карточка товара"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3h8l3 3v11H5zM13 3v4h4M8 10h5M8 13h5"/></svg></button>`, "number"),
      };
      return `<tr>${tableColumns("project").map((column) => cells[column.key] || cell(column.key, "—")).join("")}</tr>`;
    }).join("") || `<tr><td colspan="${tableColumns("project").length}">Под текущие фильтры в группе проекта SKU нет</td></tr>`;
    bindProjectRowActions();
    const meta = seoState.root.querySelector("[data-seo-project-meta]");
    if (meta) meta.textContent = `Найдено ${num(data.total)} · страница ${num(data.page)} из ${num(data.total_pages)}`;
    applyMetricSourceText(data.metric_sources);
    populateProjectFilterOptions(data);
    const prev = seoState.root.querySelector("[data-seo-project-prev]");
    const next = seoState.root.querySelector("[data-seo-project-next]");
    if (prev) prev.disabled = data.page <= 1;
    if (next) next.disabled = data.page >= data.total_pages;
  }

  function populateProjectFilterOptions(data) {
    if (!data) return;
    const allSubcategories = [...new Set(Object.values(data.subcategories_by_category || {}).flat())].sort();
    const options = { subcategory: allSubcategories, ...(data.filter_options || {}) };
    options.catalog_category = data.category_values || [];
    const labels = { catalog_category: "Все категории", subcategory: "Все подкатегории", assortment_bia: "Весь ассортимент", tg: "Все товарные группы", tg_plus: "Все ТГ+", cg: "Все ЦГ", season: "Все сезоны", brand: "Все бренды", gender: "Все значения", age: "Все возрасты", collection: "Все коллекции", style: "Все стили", color: "Все цвета", material: "Все материалы", material_composition: "Любой состав", russian_size: "Все размеры", manufacturer_size: "Все размеры", target_audience: "Все аудитории", availability: "Любой остаток" };
    Object.entries(options).forEach(([name, values]) => {
      const field = seoState.root.querySelector(`select[name="${name}"]`);
      if (field) field.innerHTML = optionList(values, seoState.projectFilters[name] || field.value, labels[name] || "Все");
    });
    const availability = seoState.root.querySelector('select[name="availability"]');
    if (availability) {
      availability.querySelector('option[value="in_stock"]')?.replaceChildren("В наличии");
      availability.querySelector('option[value="out_of_stock"]')?.replaceChildren("Нет остатка");
    }
  }

  function closeSeoDataModal() {
    seoState.root.querySelector("[data-seo-data-modal]")?.remove();
  }

  function showSeoDataModal(title, subtitle, content) {
    closeSeoDataModal();
    seoState.root.insertAdjacentHTML("beforeend", `<div class="seo-data-modal" data-seo-data-modal><section class="seo-data-dialog" role="dialog" aria-modal="true" aria-labelledby="seoDataModalTitle"><header><div><h2 id="seoDataModalTitle">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button type="button" class="seo-data-close" data-seo-data-close aria-label="Закрыть" title="Закрыть"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5 5 15"/></svg></button></header><div class="seo-data-content" data-seo-data-content>${content}</div></section></div>`);
    const modal = seoState.root.querySelector("[data-seo-data-modal]");
    modal?.querySelector("[data-seo-data-close]")?.addEventListener("click", closeSeoDataModal);
    modal?.addEventListener("click", (event) => { if (event.target === modal) closeSeoDataModal(); });
    modal?.querySelector("[data-seo-data-close]")?.focus();
  }

  async function openAiSettings() {
    showSeoDataModal("Настройки ИИ", "Основная модель используется первой; резервная — после трёх неудачных попыток основной.", '<div class="seo-data-loading">Загружаю настройки…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const data = await json(`/api/seo-ai-settings?client=${encodeURIComponent(seoState.client)}&dashboard=seoMonitoring`);
      const providersById = new Map((data.providers || []).map((provider) => [provider.id, provider]));
      const providerOptions = (selected) => (data.providers || []).map((provider) => `<option value="${esc(provider.id)}" ${provider.id === selected ? "selected" : ""}>${esc(provider.label)}${provider.available ? "" : " · недоступен"}</option>`).join("");
      const modelOptions = (providerId, selected = "") => {
        const models = providersById.get(providerId)?.models || [];
        const active = models.includes(selected) ? selected : (models[0] || selected || "");
        if (!models.length) return `<option value="${esc(active)}">${esc(active || "Модели не найдены")}</option>`;
        return models.map((model) => `<option value="${esc(model)}" ${model === active ? "selected" : ""}>${esc(model)}</option>`).join("");
      };
      content.innerHTML = `<form class="seo-ai-settings-form" data-seo-ai-settings-form><table class="seo-project-table"><thead><tr><th>AI-сценарий</th><th>Основной провайдер</th><th>Основная модель</th><th>Резервный провайдер</th><th>Резервная модель</th></tr></thead><tbody>${(data.rows || []).map((row) => `<tr data-script-key="${esc(row.script_key)}"><td><strong>${esc(row.label)}</strong></td><td><select name="primary_provider" aria-label="Основной провайдер: ${esc(row.label)}">${providerOptions(row.primary_provider)}</select></td><td><select name="primary_model" aria-label="Основная модель: ${esc(row.label)}">${modelOptions(row.primary_provider, row.primary_model)}</select></td><td><select name="fallback_provider" aria-label="Резервный провайдер: ${esc(row.label)}">${providerOptions(row.fallback_provider)}</select></td><td><select name="fallback_model" aria-label="Резервная модель: ${esc(row.label)}">${modelOptions(row.fallback_provider, row.fallback_model)}</select></td></tr>`).join("")}</tbody></table><div class="seo-ai-provider-status">${(data.providers || []).map((provider) => `<span class="${provider.available ? "available" : "unavailable"}" title="${esc(provider.message || "Провайдер доступен")}">${esc(provider.label)} · ${provider.available ? "доступен" : "не подключён"}</span>`).join("")}</div><p class="seo-project-source-note">Список моделей зависит от выбранного провайдера. Недоступный провайдер можно сохранить заранее; при запуске он даст явную ошибку и система перейдёт к резервному.</p><div class="seo-ai-settings-actions"><span data-seo-ai-settings-status></span><button type="submit" class="seo-project-btn primary">Сохранить настройки</button></div></form>`;
      content.querySelectorAll("[data-script-key]").forEach((row) => {
        [["primary_provider", "primary_model"], ["fallback_provider", "fallback_model"]].forEach(([providerName, modelName]) => {
          const providerField = row.querySelector(`[name="${providerName}"]`);
          const modelField = row.querySelector(`[name="${modelName}"]`);
          providerField?.addEventListener("change", () => { modelField.innerHTML = modelOptions(providerField.value); });
        });
      });
      content.querySelector("[data-seo-ai-settings-form]")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const status = form.querySelector("[data-seo-ai-settings-status]");
        const button = form.querySelector('button[type="submit"]');
        const rows = [...form.querySelectorAll("[data-script-key]")].map((row) => ({
          script_key: row.dataset.scriptKey,
          primary_provider: row.querySelector('[name="primary_provider"]').value,
          primary_model: row.querySelector('[name="primary_model"]').value.trim(),
          fallback_provider: row.querySelector('[name="fallback_provider"]').value,
          fallback_model: row.querySelector('[name="fallback_model"]').value.trim(),
        }));
        button.disabled = true;
        status.textContent = "Сохраняю…";
        try {
          await post("/api/seo-ai-settings?dashboard=seoMonitoring", { client: seoState.client, rows });
          status.textContent = "Настройки сохранены";
        } catch (error) {
          status.textContent = `Не удалось сохранить: ${error.message}`;
        } finally {
          button.disabled = false;
        }
      });
    } catch (error) {
      content.innerHTML = `<div class="seo-data-error">Настройки не загружены: ${esc(error.message)}</div>`;
    }
  }

  function modalValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return esc(JSON.stringify(value, null, 2));
    return esc(value);
  }

  function semanticHighlightSpec(draft) {
    const exact = new Map();
    const bases = new Map();
    (draft?.keyword_usage_audit || []).forEach((row) => {
      const query = String(row?.query || "").trim();
      if (row?.exact_required && query) exact.set(query.toLocaleLowerCase("ru"), { term: query, query });
      if (row?.exact_required) return;
      Object.entries(row?.matched_forms || {}).forEach(([base, values]) => (values || []).forEach((rawForm) => {
        const form = String(rawForm || "").trim();
        if (form.length < 3 || !/[\p{L}\p{N}]/u.test(form)) return;
        const key = form.toLocaleLowerCase("ru");
        const item = bases.get(key) || { term: form, bases: new Set(), queries: new Set() };
        item.bases.add(base);
        if (query) item.queries.add(query);
        bases.set(key, item);
      }));
    });
    if (!exact.size && draft?.primary_intent) {
      const term = String(draft.primary_intent).trim();
      exact.set(term.toLocaleLowerCase("ru"), { term, query: term });
    }
    return {
      exact: [...exact.values()].sort((a, b) => b.term.length - a.term.length),
      bases: [...bases.values()].map((row) => ({ ...row, bases: [...row.bases], queries: [...row.queries] })).sort((a, b) => b.term.length - a.term.length),
    };
  }

  function highlightSemanticText(value, spec) {
    const source = String(value ?? "");
    if (!source) return { html: "—", exactOccurrences: 0, baseOccurrences: 0, matchedExact: [], matchedBases: [] };
    const ranges = [];
    const matchedExact = new Set();
    const matchedBases = new Set();
    const isWordChar = (char) => Boolean(char) && /[\p{L}\p{N}_]/u.test(char);
    const addTerms = (items, type) => (items || []).forEach((item) => {
      const words = String(item.term || "").trim().split(/\s+/).filter(Boolean);
      if (!words.length) return;
      const pattern = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
      const matcher = new RegExp(pattern, "giu");
      let match;
      while ((match = matcher.exec(source)) !== null) {
        const start = match.index;
        const end = start + match[0].length;
        const startsWithWord = isWordChar(match[0][0]);
        const endsWithWord = isWordChar(match[0][match[0].length - 1]);
        if ((startsWithWord && isWordChar(source[start - 1])) || (endsWithWord && isWordChar(source[end]))) continue;
        if (ranges.some((range) => start < range.end && end > range.start)) continue;
        ranges.push({ start, end, type, item });
        if (type === "exact") matchedExact.add(item.query || item.term);
        else (item.bases || []).forEach((base) => matchedBases.add(base));
      }
    });
    addTerms(spec?.exact, "exact");
    addTerms(spec?.bases, "base");
    ranges.sort((left, right) => left.start - right.start || right.end - left.end);
    let cursor = 0;
    const html = [];
    ranges.forEach((range) => {
      html.push(esc(source.slice(cursor, range.start)));
      const title = range.type === "exact"
        ? `Точное вхождение: ${range.item.query || range.item.term}`
        : `Смысловая словоформа основы «${(range.item.bases || []).join(", ")}» · ключи: ${(range.item.queries || []).join("; ")}`;
      html.push(`<strong class="seo-semantic-hit seo-semantic-${range.type}" title="${esc(title)}">${esc(source.slice(range.start, range.end))}</strong>`);
      cursor = range.end;
    });
    html.push(esc(source.slice(cursor)));
    return {
      html: html.join(""),
      exactOccurrences: ranges.filter((row) => row.type === "exact").length,
      baseOccurrences: ranges.filter((row) => row.type === "base").length,
      matchedExact: [...matchedExact],
      matchedBases: [...matchedBases],
    };
  }

  const seoKeywordModalColumns = [
    { key: "snapshot_date", label: "Дата", type: "date" },
    { key: "search_query", label: "Запрос", type: "text" },
    { key: "relevance_decision", label: "Релевантность", type: "enum", options: [["keep", "Релевантен"], ["reject", "Отклонён"], ["review", "Проверить"]] },
    { key: "frequency_class", label: "ВЧ/СЧ/НЧ", type: "enum", options: [["high", "ВЧ"], ["mid", "СЧ"], ["low", "НЧ"]] },
    { key: "priority_score", label: "Приоритет", type: "number" },
    { key: "average_position", label: "Позиция", type: "number" },
    { key: "search_demand", label: "Частотность", type: "number" },
    { key: "traffic", label: "Трафик", type: "number" },
    { key: "orders", label: "Заказы", type: "number" },
    { key: "revenue_rub", label: "GMV", type: "number" },
    { key: "source", label: "Источник", type: "text" },
  ];

  function seoKeywordModalFilterFields(column, filter) {
    if (column.type === "date") return `<label>Дата<input type="date" data-seo-modal-filter-value value="${esc(filter?.value || "")}"></label>`;
    if (column.type === "source") return `<label>Источник<select data-seo-modal-filter-value><option value="">Все</option><option value="seller_api" ${filter?.value === "seller_api" ? "selected" : ""}>Seller API</option><option value="mpstats" ${filter?.value === "mpstats" ? "selected" : ""}>MPStats</option></select></label>`;
    if (column.type === "enum") return `<label>Значение<select data-seo-modal-filter-value><option value="">Все</option>${(column.options || []).map(([value, label]) => `<option value="${esc(value)}" ${filter?.value === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>`;
    const operators = column.type === "number" ? numberFilterOperators : textFilterOperators;
    const defaultOp = column.type === "number" ? "eq" : "contains";
    return `<label>Условие<select data-seo-modal-filter-op>${operators.map(([value, label]) => `<option value="${value}" ${(filter?.op || defaultOp) === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>Значение<input data-seo-modal-filter-value type="${column.type === "number" ? "number" : "text"}" ${column.type === "number" ? 'step="any" inputmode="decimal"' : ""} value="${esc(filter?.value || "")}" placeholder="Значение"></label>`;
  }

  function seoKeywordModalHead(sortCol, sortDir, filters) {
    return `<thead><tr>${seoKeywordModalColumns.map((column) => {
      const active = sortCol === column.key;
      const filter = filters[column.key];
      const hasFilter = filter?.value !== undefined && filter.value !== null && filter.value !== "";
      const ariaSort = active ? (sortDir === "asc" ? "ascending" : "descending") : "none";
      const indicator = active ? (sortDir === "asc" ? "↑" : "↓") : "↕";
      return `<th class="seo-table-head-cell ${column.type === "number" ? "number" : ""} ${active ? "is-sorted" : ""} ${hasFilter ? "is-filtered" : ""}" aria-sort="${ariaSort}"><div class="seo-table-head-control"><button type="button" class="seo-table-sort seo-data-sort" data-seo-modal-keywords-sort="${column.key}" title="Сортировать по «${esc(column.label)}»"><span>${esc(column.label)}</span><span class="seo-data-sort-indicator" aria-hidden="true">${indicator}</span></button><button type="button" class="seo-table-filter-trigger ${hasFilter ? "active" : ""}" data-seo-modal-filter-trigger="${column.key}" aria-label="Фильтр по столбцу ${esc(column.label)}" title="Фильтр столбца" aria-expanded="false"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12L9.5 8v4l-3 1V8z"/></svg></button><div class="seo-table-filter-popover" data-seo-modal-filter="${column.key}" hidden><strong>${esc(column.label)}</strong>${seoKeywordModalFilterFields(column, filter)}<div><button type="button" class="seo-project-btn ghost" data-seo-modal-filter-clear="${column.key}">Сбросить</button><button type="button" class="seo-project-btn primary" data-seo-modal-filter-apply="${column.key}">Применить</button></div></div></div></th>`;
    }).join("")}</tr></thead>`;
  }

  async function openSkuKeywords(sku, product, sourceGroup, page = 1, sortCol = "search_demand", sortDir = "desc", filters = {}) {
    const sourceLabel = sourceGroup === "mpstats" ? "MPStats" : "API маркетплейса";
    if (page === 1) showSeoDataModal(`${sourceLabel}: ключи SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю реальные строки сборки…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    if (!content) return;
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, source_group: sourceGroup, page: String(page), limit: "100", sort_col: sortCol, sort_dir: sortDir, dashboard: "seoMonitoring" });
      if (!filters.relevance_decision) params.set("relevance", "clean");
      if (Object.keys(filters).length) params.set("column_filters", JSON.stringify(filters));
      const data = await json(`/api/seo-project-keywords?${params}`);
      const rows = data.rows || [];
      const hasFilters = Object.values(filters).some((item) => item?.value !== undefined && item.value !== null && item.value !== "");
      content.innerHTML = `<div class="seo-data-summary"><strong>${num(data.total)} чистых ключей</strong><span>${hasFilters ? '<button type="button" class="seo-data-reset-filters" data-seo-modal-filter-reset-all>Сбросить фильтры</button>' : ""} страница ${num(data.page)} из ${num(data.total_pages)}</span></div><div class="seo-data-table-wrap"><table class="seo-project-table seo-data-keywords">${seoKeywordModalHead(sortCol, sortDir, filters)}<tbody>${rows.map((row) => `<tr><td>${esc(ruDate(row.snapshot_date))}</td><td title="${esc(row.search_query)}">${esc(row.clean_query || row.search_query)}</td><td title="${esc(row.rationale || (row.relevance_decision ? "" : "Анализ ещё не выполнен"))}"><span class="seo-relevance-chip ${esc(row.relevance_decision || "raw")}">${esc(({keep:"Релевантен",reject:"Отклонён",review:"Проверить"})[row.relevance_decision] || "Не проверен")}</span></td><td><span class="seo-freq-chip ${esc(row.frequency)}">${esc(({high:"ВЧ",mid:"СЧ",low:"НЧ"})[row.frequency] || "—")}</span></td><td class="number"><span class="seo-keyword-priority ${esc(row.priority_label || "")}" title="${esc(row.rationale || "")}">${row.priority_score === null || row.priority_score === undefined ? "—" : num(row.priority_score)}</span></td><td class="number">${num(row.average_position, 1)}</td><td class="number">${num(row.search_demand)}</td><td class="number">${num(row.traffic)}</td><td class="number">${num(row.orders)}</td><td class="number">${rub(row.revenue_rub)}</td><td>${esc(keywordSourceLabel(row.source))}</td></tr>`).join("") || '<tr><td colspan="11">Чистых ключей нет. Запустите очистку или откройте фильтр «Релевантность» для просмотра отклонённых.</td></tr>'}</tbody></table></div><footer class="seo-data-pagination"><button type="button" class="seo-project-btn ghost" data-seo-modal-keywords-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Назад</button><button type="button" class="seo-project-btn ghost" data-seo-modal-keywords-page="${page + 1}" ${page >= data.total_pages ? "disabled" : ""}>Вперёд</button></footer>`;
      content.querySelectorAll("[data-seo-modal-keywords-page]").forEach((button) => button.addEventListener("click", () => openSkuKeywords(sku, product, sourceGroup, Number(button.dataset.seoModalKeywordsPage), sortCol, sortDir, filters)));
      content.querySelectorAll("[data-seo-modal-keywords-sort]").forEach((button) => button.addEventListener("click", () => {
        const nextCol = button.dataset.seoModalKeywordsSort;
        const nextDir = nextCol === sortCol ? (sortDir === "asc" ? "desc" : "asc") : "asc";
        openSkuKeywords(sku, product, sourceGroup, 1, nextCol, nextDir, filters);
      }));
      content.querySelectorAll("[data-seo-modal-filter-trigger]").forEach((button) => button.addEventListener("click", () => {
        const key = button.dataset.seoModalFilterTrigger;
        content.querySelectorAll("[data-seo-modal-filter]").forEach((panel) => { if (panel.dataset.seoModalFilter !== key) panel.hidden = true; });
        const panel = content.querySelector(`[data-seo-modal-filter="${key}"]`);
        if (panel) panel.hidden = !panel.hidden;
        button.setAttribute("aria-expanded", String(panel ? !panel.hidden : false));
      }));
      content.querySelectorAll("[data-seo-modal-filter-apply]").forEach((button) => button.addEventListener("click", () => {
        const key = button.dataset.seoModalFilterApply;
        const column = seoKeywordModalColumns.find((item) => item.key === key);
        const panel = button.closest("[data-seo-modal-filter]");
        const value = panel?.querySelector("[data-seo-modal-filter-value]")?.value || "";
        const nextFilters = { ...filters };
        if (value) nextFilters[key] = { op: ["date", "source", "enum"].includes(column?.type) ? "eq" : (panel?.querySelector("[data-seo-modal-filter-op]")?.value || "contains"), value };
        else delete nextFilters[key];
        openSkuKeywords(sku, product, sourceGroup, 1, sortCol, sortDir, nextFilters);
      }));
      content.querySelectorAll("[data-seo-modal-filter-clear]").forEach((button) => button.addEventListener("click", () => {
        const nextFilters = { ...filters };
        delete nextFilters[button.dataset.seoModalFilterClear];
        openSkuKeywords(sku, product, sourceGroup, 1, sortCol, sortDir, nextFilters);
      }));
      content.querySelector("[data-seo-modal-filter-reset-all]")?.addEventListener("click", () => openSkuKeywords(sku, product, sourceGroup, 1, sortCol, sortDir, {}));
    } catch (error) {
      content.innerHTML = `<div class="seo-data-error">Не удалось загрузить ключи: ${esc(error.message)}</div>`;
    }
  }

  async function openSeoProductCard(sku, product) {
    showSeoDataModal(`Характеристики SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю полную карточку товара из БД…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, marketplace: seoState.detail?.project?.marketplace || seoState.marketplace, sku, date_from: seoState.detail?.project?.date_from || "", date_to: seoState.detail?.project?.date_to || "" });
      const payload = await json(`/api/sku-card?${params}`);
      if (!content) return;
      const media = (payload.media || []).filter((item) => item.url);
      content.innerHTML = `<div class="seo-card-kpis">${(payload.cards || []).map((card) => `<article><span>${esc(card.label)}</span><strong>${modalValue(card.value)}</strong></article>`).join("")}</div>${media.length ? `<section class="seo-card-section"><h3>Медиа карточки</h3><div class="seo-card-media">${media.map((item) => item.kind === "photo" ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(item.thumbnail_url || item.url)}" alt="${esc(item.label || "Фото товара")}" loading="lazy"><span>${esc(item.label || "Фото")}</span></a>` : `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.label || "Видео")}</a>`).join("")}</div></section>` : ""}<section class="seo-card-section"><div class="seo-data-summary"><h3>Все характеристики</h3><span>заполнено ${(payload.attributes || []).filter((row) => row.filled).length} из ${(payload.attributes || []).length}</span></div><div class="seo-data-table-wrap"><table class="seo-project-table seo-card-attributes"><thead><tr><th>Характеристика</th><th>Группа</th><th>Значение</th><th>Состояние</th><th>Оценка</th></tr></thead><tbody>${(payload.attributes || []).map((row) => `<tr class="${row.filled ? "" : "is-empty"}"><td>${esc(row.attribute_name)}</td><td>${esc(row.attribute_kind || "—")}</td><td class="seo-data-value">${modalValue(row.value)}</td><td>${esc(row.state || (row.filled ? "заполнено" : "нет"))}</td><td class="number">${num(row.importance_score, 1)}</td></tr>`).join("") || '<tr><td colspan="5">В базе нет характеристик для этой карточки.</td></tr>'}</tbody></table></div></section>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить карточку: ${esc(error.message)}</div>`;
    }
  }

  async function openNicheCompetitors(sku, product) {
    const marketplaceLabel = projectMarketplace() === "wb" ? "WB" : "Ozon";
    showSeoDataModal(`Конкуренты по интенту · SKU ${sku}`, product, `<div class="seo-data-loading">Загружаю MPStats ${marketplaceLabel} «Товары в поиске»…</div>`);
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, dashboard: "seoMonitoring" });
      const payload = await json(`/api/seo-project-niche-competitors?${params}`);
      if (!content) return;
      const rows = payload.rows || [];
      const source = payload.source_report === "mpstats_ozon_products_in_search"
        ? "MPStats Ozon · Товары в поиске"
        : payload.source_report === "mpstats_wb_products_in_search"
          ? "MPStats WB · Товары в поиске"
          : (payload.source_report || `MPStats ${marketplaceLabel}`);
      content.innerHTML = `<div class="seo-data-summary"><div><strong>Интент: ${esc(payload.intent || "—")}</strong><span>${esc(source)} · ${esc(payload.date_from || "—")} — ${esc(payload.date_to || "—")}</span></div><span>${num(payload.count)} конкурентов</span></div><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>#</th><th>Товар</th><th>Бренд / продавец</th><th>Ср. позиция</th><th>Последняя</th><th>Цена</th><th>Заказы, шт.</th><th>Заказы, ₽</th><th>Рейтинг / отзывов на карточке</th><th>Собрано отзывов</th><th>Ниша</th></tr></thead><tbody>${rows.map((row) => {
        const safeUrl = /^https?:\/\//i.test(row.product_url || "") ? row.product_url : "";
        const safeImage = /^https?:\/\//i.test(row.image_url || "") ? row.image_url : "";
        const title = `<strong>${esc(row.product_name || row.competitor_sku)}</strong><span>SKU ${esc(row.competitor_sku)}</span>`;
        const collected = !row.review_status ? "—" : `<button type="button" class="seo-cell-icon-btn ${row.review_status === "ok" ? "" : "has-error"}" data-seo-competitor-review-open="${esc(row.competitor_sku)}" data-seo-own-sku="${esc(sku)}" data-seo-product="${esc(product)}" title="${esc(row.review_status === "ok" ? `Отзывы конкурента · ${num(row.review_period_days)} дней` : (row.review_error || "Ошибка сбора"))}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v9H9l-4 3v-3H4z"/></svg><span>${row.review_status === "ok" ? num(row.collected_review_count) : "!"}</span></button>`;
        return `<tr><td class="number">${num(row.competitor_rank)}</td><td class="seo-builder-product">${safeUrl ? `<a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${safeImage ? `<img src="${esc(safeImage)}" alt="" loading="lazy" width="36" height="48">` : ""}${title}</a>` : title}</td><td>${esc(row.brand || "—")}<br><span>${esc(row.seller || "—")}</span></td><td class="number">${num(row.average_position, 1)}</td><td class="number">${num(row.latest_position, 0)}</td><td class="number">${rub(row.price)}</td><td class="number">${num(row.sales)}</td><td class="number">${rub(row.revenue)}</td><td class="number">${num(row.rating, 1)} / ${num(row.reviews_count)}</td><td class="number">${collected}</td><td title="${esc(row.niche_name || "")}">${esc(row.niche_name || "—")}</td></tr>`;
      }).join("") || '<tr><td colspan="11">Конкуренты ещё не собраны для этого SKU.</td></tr>'}</tbody></table></div>`;
      content.querySelectorAll("[data-seo-competitor-review-open]").forEach((button) => button.addEventListener("click", () => openCompetitorReviews(button.dataset.seoOwnSku, button.dataset.seoProduct, button.dataset.seoCompetitorReviewOpen)));
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить конкурентов: ${esc(error.message)}</div>`;
    }
  }

  async function openCompetitorKeywords(sku, product) {
    showSeoDataModal(`Ключи конкурентов · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю ключи товаров-конкурентов…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku });
      const payload = await json(`/api/seo-project-competitor-keywords?${params}`), rows = payload.rows || [];
      if (!content) return;
      content.innerHTML = `<div class="seo-data-summary"><strong>${num(payload.count)} уникальных ключей</strong><span>MPStats · топ-10 конкурентов</span></div><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Запрос</th><th>Спрос</th><th>Лучшая позиция</th><th>Конкурентов</th><th>SKU конкурентов</th><th>Релевантность</th><th>ВЧ/СЧ/НЧ</th><th>Приоритет</th><th>Причина</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.clean_query || row.search_query)}</td><td class="number">${num(row.search_demand)}</td><td class="number">${num(row.best_position, 1)}</td><td class="number">${num(row.competitor_coverage)}</td><td title="${esc(row.competitor_skus || '')}">${esc(row.competitor_skus || '—')}</td><td>${esc(row.relevance_decision || 'не проверено')}</td><td>${esc(row.frequency_class || '—')}</td><td class="number">${row.priority_score == null ? '—' : `${num(row.priority_score)} · ${esc(row.priority_label || '')}`}</td><td title="${esc(row.relevance_reason || '')}">${esc(row.relevance_reason || '—')}</td></tr>`).join("") || '<tr><td colspan="9">Ключи конкурентов ещё не собраны.</td></tr>'}</tbody></table></div>`;
    } catch (error) { if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить ключи конкурентов: ${esc(error.message)}</div>`; }
  }

  async function openCustomerMessages(sku, product, messageType) {
    const isReview = messageType === "review";
    const label = isReview ? "Отзывы" : "Вопросы";
    showSeoDataModal(`${label} · SKU ${sku}`, product, `<div class="seo-data-loading">Загружаю ${isReview ? "отзывы MPStats" : "вопросы Ozon"}…</div>`);
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
        sku, message_type: messageType, dashboard: "seoMonitoring",
      });
      const payload = await json(`/api/seo-project-customer-messages?${params}`);
      if (!content) return;
      const rows = payload.rows || [];
      const status = (payload.statuses || []).find((item) => item.message_type === messageType);
      const statusLabel = !status ? "ещё не собирались" : status.status === "ok" ? "источник доступен" : status.status === "unavailable" ? "источник недоступен" : "ошибка источника";
      const sourceLabel = isReview ? "MPStats Ozon" : "Ozon Seller API";
      const statusError = status?.last_error ? `<div class="seo-data-error">${esc(status.last_error)}</div>` : "";
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${status?.status === "ok" ? `${num(payload.count)} ${isReview ? "отзывов" : "вопросов"}` : esc(statusLabel)}</strong><span>${esc(sourceLabel)}${status?.period_days ? ` · последние ${num(status.period_days)} дней` : ""}${status?.collected_at ? ` · обновлено ${esc(dateText(status.collected_at))}` : ""}</span></div>${payload.truncated ? '<span>Показаны первые 500 строк</span>' : ""}</div>${statusError}<div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Дата</th>${isReview ? "<th>Оценка</th>" : ""}<th>${isReview ? "Отзыв" : "Вопрос"}</th>${isReview ? "<th>Плюсы</th><th>Минусы</th>" : ""}<th>Ответ / статус</th><th>Источник</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(ruDate(row.message_date))}</td>${isReview ? `<td class="number">${row.rating === null || row.rating === undefined ? "—" : num(row.rating, 1)}</td>` : ""}<td class="seo-data-value">${esc(row.message_text || "—")}</td>${isReview ? `<td class="seo-data-value">${esc(row.pros || "—")}</td><td class="seo-data-value">${esc(row.cons || "—")}</td>` : ""}<td class="seo-data-value">${esc(row.answer_text || (row.answered ? "Есть ответ" : "Без ответа"))}</td><td>${esc(sourceLabel)}</td></tr>`).join("") || `<tr><td colspan="${isReview ? 7 : 4}">${status?.status === "ok" ? `За выбранные ${num(status.period_days)} дней данных нет.` : "Сначала запустите сбор или проверьте доступность источника."}</td></tr>`}</tbody></table></div>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить ${isReview ? "отзывы" : "вопросы"}: ${esc(error.message)}</div>`;
    }
  }

  async function openCompetitorReviews(sku, product, competitorSku = "") {
    const suffix = competitorSku ? ` · конкурент ${competitorSku}` : " · топ-10 конкурентов";
    showSeoDataModal(`Отзывы конкурентов · SKU ${sku}`, `${product}${suffix}`, '<div class="seo-data-loading">Загружаю отзывы конкурентов из MPStats…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({
        client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId,
        sku, dashboard: "seoMonitoring",
      });
      if (competitorSku) params.set("competitor_sku", competitorSku);
      const payload = await json(`/api/seo-project-competitor-reviews?${params}`);
      if (!content) return;
      const rows = payload.rows || [], statuses = payload.statuses || [];
      const failed = statuses.filter((item) => item.status !== "ok");
      const period = statuses.find((item) => item.status === "ok")?.period_days;
      const errorHtml = failed.length ? `<div class="seo-data-error">${failed.map((item) => `SKU ${esc(item.competitor_sku)}: ${esc(item.last_error || "источник недоступен")}`).join("<br>")}</div>` : "";
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${num(payload.count)} отзывов конкурентов</strong><span>MPStats Ozon${period ? ` · последние ${num(period)} дней` : ""} · конкурентов со статусом ${num(statuses.length)}</span></div>${payload.truncated ? '<span>Показаны первые 500 строк</span>' : ""}</div>${errorHtml}<div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>#</th><th>Конкурент</th><th>Дата</th><th>Оценка</th><th>Отзыв</th><th>Плюсы</th><th>Минусы</th><th>Ответ / статус</th></tr></thead><tbody>${rows.map((row) => `<tr><td class="number">${num(row.competitor_rank)}</td><td><strong>${esc(row.competitor_name || row.competitor_sku)}</strong><br><span>SKU ${esc(row.competitor_sku)}</span></td><td>${esc(ruDate(row.message_date))}</td><td class="number">${row.rating === null || row.rating === undefined ? "—" : num(row.rating, 1)}</td><td class="seo-data-value">${esc(row.message_text || "—")}</td><td class="seo-data-value">${esc(row.pros || "—")}</td><td class="seo-data-value">${esc(row.cons || "—")}</td><td class="seo-data-value">${esc(row.answer_text || (row.answered ? "Есть ответ" : "Без ответа"))}</td></tr>`).join("") || `<tr><td colspan="8">${statuses.length ? "За выбранный период отзывов нет." : "Сначала запустите «Отзывы + вопросы + конкуренты»."}</td></tr>`}</tbody></table></div>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить отзывы конкурентов: ${esc(error.message)}</div>`;
    }
  }

  async function openCustomerVoiceClaims(sku, product) {
    showSeoDataModal(`SEO-клеймы · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю клеймы и подтверждения…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({
        client: seoState.client, project_kind: seoState.projectKind,
        project_id: seoState.projectId, sku, dashboard: "seoMonitoring",
      });
      const payload = await json(`/api/seo-project-customer-voice/claims?${params}`);
      if (!content) return;
      const rows = payload.rows || [], status = payload.status;
      const statusError = status?.last_error ? `<div class="seo-data-error">${esc(status.last_error)}</div>` : "";
      const targetLabels = { title: "Название", description: "Описание", characteristics: "Характеристики", faq: "FAQ" };
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${status?.status === "ok" ? `${num(payload.count)} SEO-клеймов` : "Анализ ещё не выполнен"}</strong><span>${status ? `${num(status.message_count)} сообщений · ${esc(status.model || "модель не указана")} · ${esc(dateText(status.analyzed_at))}` : "Соберите отзывы и вопросы, затем запустите «Клеймы из отзывов»"}</span></div><span>Автопереноса в карточку нет</span></div>${statusError}<div class="seo-data-table-wrap"><table class="seo-project-table seo-customer-voice-claims"><thead><tr><th>Статус</th><th>Клейм для SEO</th><th>Куда</th><th>Свои отзывы</th><th>Вопросы</th><th>Конкуренты</th><th>Уверенность</th><th>Подтверждения</th><th>Действие</th></tr></thead><tbody>${rows.map((row) => {
        const evidence = Array.isArray(row.evidence_json) ? row.evidence_json : [];
        const evidenceHtml = evidence.map((item) => `<li><strong>${esc(({own_review:"Свой отзыв",own_question:"Свой вопрос",competitor_review:"Отзыв конкурента"})[item.source_type] || item.source_type)}</strong>${item.competitor_sku ? ` · SKU ${esc(item.competitor_sku)}` : ""}<br>${esc(item.text || "—")}</li>`).join("");
        return `<tr><td><span class="seo-claim-status ${esc(row.verification_status)}">${row.verification_status === "safe" ? "Можно использовать" : "Проверить"}</span></td><td class="seo-data-value"><strong>${esc(row.claim_text)}</strong><br><span>${esc(row.rationale || "—")}</span></td><td>${esc(targetLabels[row.seo_target] || row.seo_target || "Описание")}</td><td class="number">${num(row.own_review_mentions)}</td><td class="number">${num(row.own_question_mentions)}</td><td class="number">${num(row.competitor_review_mentions)}</td><td class="number">${row.confidence === null || row.confidence === undefined ? "—" : `${num(Number(row.confidence) * 100, 0)}%`}</td><td><details class="seo-claim-evidence"><summary>${num(row.evidence_count)} источн.</summary><ul>${evidenceHtml}</ul></details></td><td><button type="button" class="seo-cell-icon-btn icon-only" data-seo-copy-claim="${esc(row.claim_text)}" aria-label="Копировать клейм" title="Копировать клейм"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 6h9v11H7zM4 3h9v3M4 3v11h3"/></svg></button></td></tr>`;
      }).join("") || '<tr><td colspan="9">Клеймов нет. Сначала соберите customer voice и запустите анализ.</td></tr>'}</tbody></table></div>`;
      content.querySelectorAll("[data-seo-copy-claim]").forEach((button) => button.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(button.dataset.seoCopyClaim || "");
          button.title = "Скопировано";
        } catch (_) {
          button.title = "Не удалось скопировать";
        }
      }));
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Не удалось загрузить SEO-клеймы: ${esc(error.message)}</div>`;
    }
  }

  function semanticKeywordRows(rows) {
    return (rows || []).map((row) => `<tr><td>${esc(row.query || "—")}</td><td>${esc((row.sources || []).join(", ") || "—")}</td><td>${esc(row.frequency_class || "—")}</td><td>${esc(row.priority_label || "—")}</td><td class="number">${num(row.priority_score)}</td><td class="number">${row.search_demand == null ? "—" : num(row.search_demand)}</td><td class="number">${row.best_position == null ? "—" : num(row.best_position, 1)}</td></tr>`).join("") || '<tr><td colspan="7">Отобранных ключей нет</td></tr>';
  }

  async function openSemanticContext(sku, product) {
    showSeoDataModal(`SEO-контекст · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю подготовленный пакет…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, dashboard: "seoMonitoring" });
      const payload = await json(`/api/seo-project-semantic-context?${params}`);
      if (!content) return;
      if (!payload.available) {
        content.innerHTML = '<div class="seo-data-error">SEO-контекст ещё не подготовлен. Запусти этап «SEO-контекст» для этого SKU.</div>';
        return;
      }
      const context = payload.context_json || {}, productData = context.product || {}, coverage = context.coverage || {};
      const attributes = (productData.seo_characteristics || []).map((row) => `<tr><th>${esc(row.name)}</th><td>${modalValue(row.value)}</td></tr>`).join("") || '<tr><th>Характеристики</th><td>Нет данных</td></tr>';
      const claims = (context.customer_voice_claims || []).map((row) => `<tr><td>${esc(row.claim || "—")}</td><td>${esc(row.target || "—")}</td><td>${esc(row.verification_status || "—")}</td><td class="number">${row.confidence == null ? "—" : `${num(Number(row.confidence) * 100, 0)}%`}</td><td class="number">${num(row.evidence_count)}</td></tr>`).join("") || '<tr><td colspan="5">Клеймов нет</td></tr>';
      const warnings = (context.warnings || []).length ? `<div class="seo-data-error">Неполное покрытие: ${(context.warnings || []).map(esc).join(" · ")}</div>` : "";
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${payload.status === "ok" ? "Контекст готов" : "Контекст собран частично"}</strong><span>Версия ${esc(context.context_version || "—")} · ${esc(dateText(payload.prepared_at))}</span></div><span>Хэш ${esc(String(payload.context_hash || "").slice(0, 12))}</span></div>${warnings}
        <table class="seo-workspace-summary-table"><tbody><tr class="seo-workspace-table-section"><th colspan="2">Товар и интент</th></tr><tr><th>Название</th><td>${modalValue(productData.title)}</td></tr><tr><th>Описание</th><td>${modalValue(productData.description)}</td></tr><tr><th>Категория / тип</th><td>${modalValue([productData.category, productData.product_type].filter(Boolean).join(" · "))}</td></tr><tr><th>Интент</th><td>${modalValue(productData.intent)}</td></tr><tr class="seo-workspace-table-section"><th colspan="2">SEO-чувствительные характеристики (${num(coverage.seo_characteristics)} из ${num(coverage.all_characteristics)})</th></tr>${attributes}</tbody></table>
        <h4>Свои ключи · отобрано ${num(coverage.own_keywords_selected)} из ${num(coverage.own_keywords_available)}</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Запрос</th><th>Источник</th><th>Частотность</th><th>Приоритет</th><th>Балл</th><th>Спрос</th><th>Позиция</th></tr></thead><tbody>${semanticKeywordRows(context.own_keywords)}</tbody></table></div>
        <h4>Ключи конкурентов · отобрано ${num(coverage.competitor_keywords_selected)} из ${num(coverage.competitor_keywords_available)}</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Запрос</th><th>Источник</th><th>Частотность</th><th>Приоритет</th><th>Балл</th><th>Спрос</th><th>Позиция</th></tr></thead><tbody>${semanticKeywordRows(context.competitor_keywords)}</tbody></table></div>
        <h4>SEO-клеймы · отобрано ${num(coverage.claims_selected)} из ${num(coverage.claims_available)}</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Клейм</th><th>Размещение</th><th>Статус</th><th>Уверенность</th><th>Источники</th></tr></thead><tbody>${claims}</tbody></table></div>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">SEO-контекст недоступен: ${esc(error.message)}</div>`;
    }
  }

  async function openContentAllocation(sku, product) {
    showSeoDataModal(`SEO-разметка · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю выбранные ключи, характеристики и клеймы…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, dashboard: "seoMonitoring" });
      const payload = await json(`/api/seo-project-content-allocation?${params}`);
      if (!content) return;
      if (!payload.available) {
        content.innerHTML = '<div class="seo-data-error">SEO-разметка ещё не выполнена. Запусти этот этап после подготовки SEO-контекста.</div>';
        return;
      }
      const allocation = payload.allocation_json || {};
      const allocationRows = (rows) => (rows || []).map((row) => `<tr><td><strong>${esc(row.query)}</strong></td><td>${esc(row.role || "—")}</td><td>${esc((row.sources || []).join(", ") || "—")}</td><td class="number">${num(row.priority_score)}</td><td class="number">${row.search_demand == null ? "—" : num(row.search_demand)}</td><td class="number">${row.best_position == null ? "—" : num(row.best_position, 1)}</td><td>${esc(row.reason || "—")}</td></tr>`).join("") || '<tr><td colspan="7">Не выбрано</td></tr>';
      const characteristicRows = (rows) => (rows || []).map((row) => `<tr><td><strong>${esc(row.name || "—")}</strong></td><td>${modalValue(row.value)}</td><td>${esc(row.reason || "—")}</td></tr>`).join("") || '<tr><td colspan="3">Не выбрано</td></tr>';
      const claimRows = (allocation.description_claims || []).map((row) => `<tr><td><strong>${esc(row.claim)}</strong></td><td>${esc(row.verification_status === "safe" ? "Можно использовать" : "Нужна проверка")}</td><td class="number">${row.confidence == null ? "—" : `${num(Number(row.confidence) * 100, 0)}%`}</td><td class="number">${num(row.evidence_count)}</td><td>${esc(row.reason || "—")}</td></tr>`).join("") || '<tr><td colspan="5">Подтверждённых клеймов нет</td></tr>';
      const monitorRows = (payload.monitoring_keywords || []).map((row) => `<tr><td>${esc(row.search_query)}</td><td>${esc(row.placement === "title" ? "Название" : "Описание")}</td><td>${esc(row.role)}</td><td class="number">${num(row.priority_score)}</td><td class="number">${row.baseline_position == null ? "—" : num(row.baseline_position, 1)}</td><td class="number">${row.baseline_search_demand == null ? "—" : num(row.baseline_search_demand)}</td><td><span class="seo-claim-status safe">Мониторить</span></td></tr>`).join("") || '<tr><td colspan="7">Фразы мониторинга не отмечены</td></tr>';
      const warnings = (allocation.warnings || []).length ? `<div class="seo-data-error">Проверить: ${(allocation.warnings || []).map(esc).join(" · ")}</div>` : "";
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${payload.status === "ok" ? "SEO-разметка готова" : "SEO-разметка готова частично"}</strong><span>${esc(payload.model || "—")} · ${esc(dateText(payload.generated_at))}</span></div><span>${esc(payload.rules_version || "—")}</span></div>${warnings}
        <table class="seo-workspace-summary-table"><tbody><tr class="seo-workspace-table-section"><th colspan="2">Каркас полей — без автопубликации</th></tr><tr><th>Название</th><td>${modalValue(allocation.title_outline)}</td></tr><tr><th>Описание</th><td>${modalValue(allocation.description_outline)}</td></tr></tbody></table>
        <h4>SEO-характеристики · выбрано ${num(allocation.characteristics_selected)} из ${num(allocation.characteristics_available)}</h4>
        <div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>В название</th><th>Значение</th><th>Почему</th></tr></thead><tbody>${characteristicRows(allocation.title_characteristics)}</tbody></table></div>
        <div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>В описание</th><th>Значение</th><th>Почему</th></tr></thead><tbody>${characteristicRows(allocation.description_characteristics)}</tbody></table></div>
        <h4>В название</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Ключ</th><th>Роль</th><th>Источник</th><th>Балл</th><th>Спрос</th><th>Позиция</th><th>Почему</th></tr></thead><tbody>${allocationRows(allocation.title_keywords)}</tbody></table></div>
        <h4>В описание</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Ключ</th><th>Роль</th><th>Источник</th><th>Балл</th><th>Спрос</th><th>Позиция</th><th>Почему</th></tr></thead><tbody>${allocationRows(allocation.description_keywords)}</tbody></table></div>
        <h4>Клеймы в описание</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Клейм</th><th>Статус</th><th>Уверенность</th><th>Источники</th><th>Почему</th></tr></thead><tbody>${claimRows}</tbody></table></div>
        <h4>Фразы для последующего мониторинга</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Запрос</th><th>Поле</th><th>Роль</th><th>Балл</th><th>Базовая позиция</th><th>Спрос</th><th>Статус</th></tr></thead><tbody>${monitorRows}</tbody></table></div>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">SEO-разметка недоступна: ${esc(error.message)}</div>`;
    }
  }

  async function openContentDraft(sku, product) {
    showSeoDataModal(`SEO-текст · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю сохранённый черновик названия, описания и хештегов…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, dashboard: "seoMonitoring" });
      const payload = await json(`/api/seo-project-content-draft?${params}`);
      if (!content) return;
      if (!payload.available) {
        content.innerHTML = '<div class="seo-data-error">SEO-текст ещё не сформирован. Сначала выполни SEO-разметку, затем запусти этап «SEO-текст».</div>';
        return;
      }
      const draft = payload.draft_json || {};
      const semanticSpec = semanticHighlightSpec(draft);
      const highlightedTitle = highlightSemanticText(draft.title, semanticSpec);
      const highlightedDescription = highlightSemanticText(draft.description, semanticSpec);
      const exactOccurrences = highlightedTitle.exactOccurrences + highlightedDescription.exactOccurrences;
      const baseOccurrences = highlightedTitle.baseOccurrences + highlightedDescription.baseOccurrences;
      const matchedBases = new Set([...highlightedTitle.matchedBases, ...highlightedDescription.matchedBases]);
      const semanticLegend = `<div class="seo-semantic-legend"><strong>Семантическое ядро</strong><span><b class="seo-semantic-legend-exact">Точная фраза</b> ${num(exactOccurrences)} · <b class="seo-semantic-legend-base">Смысловые словоформы</b> ${num(baseOccurrences)} · основ ${num(matchedBases.size)}</span></div>`;
      const keywordCoverageLabels = { exact: "Точно", morphological: "По основам", partial: "Частично", absent: "Нет" };
      const keywordFieldLabels = { title: "Название", description: "Описание", hashtags: "Хештеги" };
      const keywordAuditRows = (draft.keyword_usage_audit || []).map((row) => {
        const bases = (row.morphological_bases || []).join(" · ") || "—";
        const links = (row.semantic_links || []).map((pair) => pair.join(" + ")).join("; ") || "—";
        const forms = Object.entries(row.matched_forms || {}).map(([base, values]) => `${base}: ${(values || []).join(", ")}`).join("; ") || "—";
        const locations = (row.locations || []).map((item) => `${keywordFieldLabels[item.field] || item.field}: ${item.type === "exact" ? "точно" : "по основам"}${item.count > 1 ? ` ×${item.count}` : ""}`).join("; ") || "Не найдено";
        const statusClass = row.passed ? "safe" : "review";
        return `<tr><td><strong>${esc(row.query || "—")}</strong><small>${row.exact_required ? "Главный · точное вхождение" : "Поддерживающий · словоформы"}</small></td><td>${esc(row.role || "—")}</td><td>${esc(bases)}</td><td>${esc(links)}</td><td>${esc(forms)}</td><td>${esc(locations)}</td><td class="number">${row.base_coverage == null ? "—" : `${num(Number(row.base_coverage) * 100, 0)}%`}</td><td><span class="seo-claim-status ${statusClass}">${esc(keywordCoverageLabels[row.coverage_type] || row.coverage_type || "—")}</span></td></tr>`;
      }).join("") || '<tr><td colspan="8">Поключевой аудит появится после повторной генерации этого SKU</td></tr>';
      const characteristicRows = (draft.used_characteristics || []).map((row) => `<tr><td><strong>${esc(row.name || "—")}</strong></td><td>${modalValue(row.value)}</td><td>${esc(row.placement === "title" ? "Название" : "Описание")}</td></tr>`).join("") || '<tr><td colspan="3">Характеристики не указаны</td></tr>';
      const claimRows = (draft.used_claims || []).map((row) => `<tr><td><strong>${esc(row.claim || "—")}</strong></td><td>${esc(row.verification_status || "—")}</td><td class="number">${row.confidence == null ? "—" : `${num(Number(row.confidence) * 100, 0)}%`}</td></tr>`).join("") || '<tr><td colspan="3">Проверенные клеймы не использованы</td></tr>';
      const hashtagDetails = new Map((draft.hashtag_details || []).map((row) => [String(row.hashtag || "").toLowerCase(), row]));
      const hashtags = (draft.hashtags || []).map((tag) => { const detail = hashtagDetails.get(String(tag).toLowerCase()) || {}; const source = detail.source_query ? `Ключ: ${detail.source_query}${detail.role ? ` · ${detail.role}` : ""}` : "Проверенная лексика модели"; return `<span class="seo-content-hashtag" title="${esc(source)}">${esc(tag)}</span>`; }).join("") || '<span class="seo-workspace-missing">Хештеги не сформированы</span>';
      const warnings = (draft.warnings || []).length ? `<div class="seo-data-error">Проверить: ${(draft.warnings || []).map(esc).join(" · ")}</div>` : "";
      const sourceWarranties = (draft.required_source_warranty_claims || []).map((claim) => `<div>${esc(claim)}</div>`).join("") || "—";
      const sectionRows = (draft.description_sections || []).map((row, index) => `<tr><td class="number">${index + 1}</td><td><strong>${esc(row.label || row.kind || "Без типа")}</strong><small>Внутренняя разметка · в текст заголовок не попадает</small></td><td class="number">${num(row.char_count == null ? String(row.text || "").length : row.char_count)}</td><td class="seo-semantic-copy">${highlightSemanticText(row.text, semanticSpec).html}</td></tr>`).join("") || '<tr><td colspan="4">Структура не сохранена: это черновик старого формата</td></tr>';
      const titleStructureRows = (draft.title_structure?.components || []).map((row, index) => `<tr><td class="number">${index + 1}</td><td><strong>${esc(row.label || row.kind || "—")}</strong></td><td>${esc(row.source || "—")}</td><td>${esc(row.value || "—")}</td><td>${row.found ? `<span class="seo-claim-status safe">В названии${row.position == null ? "" : ` · ${num(row.position + 1)} знак`}</span>` : '<span class="seo-claim-status review">Не найдено</span>'}</td></tr>`).join("") || '<tr><td colspan="5">Структура названия появится после повторной генерации этого SKU</td></tr>';
      const seoChecks = draft.seo_writing_checks || {};
      const titleCheck = seoChecks.primary_title_keyword_near_start || {};
      const openingCheck = seoChecks.description_opening_keyword || {};
      const usageCheck = seoChecks.keyword_usage || {};
      const stuffingCheck = seoChecks.keyword_stuffing || {};
      const coverageCheck = seoChecks.keyword_coverage || {};
      const checkBadge = (passed) => passed == null ? '<span class="seo-claim-status review">Нет проверки</span>' : `<span class="seo-claim-status ${passed ? "safe" : "review"}">${passed ? "Пройдено" : "Проверить"}</span>`;
      const seoCheckRows = `<tr><td><strong>Основной интент в начале названия</strong><small>Одно точное естественное вхождение</small></td><td>${esc(coverageCheck.main_intent || titleCheck.query || "—")}</td><td>${titleCheck.token_coverage == null ? "—" : `${num(Number(titleCheck.token_coverage) * 100, 0)}%`}</td><td>${checkBadge(titleCheck.passed && (coverageCheck.exact_passed || 0) === 1)}</td></tr><tr><td><strong>Точное вхождение интента</strong><small>Только основной интент; остальные запросы — смысловая поддержка</small></td><td>${num(coverageCheck.exact_passed || 0)} из ${num(coverageCheck.exact_required || 0)}</td><td>100%</td><td>${checkBadge((coverageCheck.exact_passed || 0) === (coverageCheck.exact_required || 0))}</td></tr><tr><td><strong>Морфологические основы</strong><small>Уникальные основы остальных релевантных запросов</small></td><td>${num(coverageCheck.unique_bases_used || 0)} из ${num(coverageCheck.unique_bases || 0)}</td><td>${coverageCheck.base_coverage == null ? "—" : `${num(Number(coverageCheck.base_coverage) * 100, 0)}%`}</td><td>${checkBadge(coverageCheck.base_coverage == null ? null : Number(coverageCheck.base_coverage) >= 0.8)}</td></tr><tr><td><strong>Поддерживающие запросы</strong><small>Покрыты основами и смысловыми связками</small></td><td>${num(coverageCheck.support_covered || 0)} из ${num(coverageCheck.support_queries || 0)}</td><td>без требования exact</td><td>${checkBadge(coverageCheck.passed)}</td></tr><tr><td><strong>Плотность точных фраз</strong><small>Без повторов; не более 6 вхождений на 1000 знаков</small></td><td>${num(stuffingCheck.exact_occurrences || 0)} вхождений</td><td>${stuffingCheck.exact_occurrences_per_1000_chars == null ? "—" : `${num(stuffingCheck.exact_occurrences_per_1000_chars, 2)} / 1000`}</td><td>${checkBadge(stuffingCheck.passed)}</td></tr>`;
      const audit = payload.audit_json || {};
      const auditHtml = audit.system_prompt ? `<section class="seo-content-ai-audit">
        <h4>Что физически пришло в модель и вернулось назад</h4>
        <table class="seo-workspace-summary-table"><tbody><tr><th>Провайдер</th><td>${esc(audit.provider || "—")}</td></tr><tr><th>Модель</th><td>${esc(audit.model || "—")}</td></tr><tr><th>Раздумывание</th><td>${esc(audit.reasoning_effort || "не применяется")}</td></tr><tr><th>Версия промта</th><td>${esc(audit.prompt_version || payload.prompt_version || "—")}</td></tr><tr><th>Статус проверки</th><td>${esc(audit.validation_status || payload.status || "—")}</td></tr></tbody></table>
        <details open><summary>Входной контекст модели</summary><pre>${esc(JSON.stringify(audit.input_context || {}, null, 2))}</pre></details>
        <details><summary>Системный промт</summary><pre>${esc(audit.system_prompt || "—")}</pre></details>
        <details><summary>Пользовательский промт</summary><pre>${esc(audit.user_prompt || "—")}</pre></details>
        <details><summary>Сырой ответ модели</summary><pre>${esc(audit.raw_response || "—")}</pre></details>
        <details><summary>Ответ после проверки и сохранения</summary><pre>${esc(JSON.stringify(audit.validated_result || draft, null, 2))}</pre></details>
      </section>` : '<div class="seo-data-error seo-content-audit-missing">У этого старого запуска AI-аудит ещё не сохранялся. Запусти этап «SEO-текст» повторно для этого SKU — массовый ран не требуется.</div>';
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${payload.status === "ok" ? "SEO-текст готов" : "SEO-текст готов частично"}</strong><span>${esc(payload.model || "—")} · ${esc(dateText(payload.generated_at))}</span></div><span>Черновик · без публикации</span></div>${warnings}${semanticLegend}
        <table class="seo-workspace-summary-table seo-content-draft-table"><tbody><tr class="seo-workspace-table-section"><th colspan="2">Сгенерированный контент</th></tr><tr><th>Название</th><td><div class="seo-semantic-copy seo-semantic-title">${highlightedTitle.html}</div><small>${num(String(draft.title || "").length)} символов</small></td></tr><tr><th>Модель в конце названия</th><td><strong>${esc(draft.required_source_model || "—")}</strong><small>Обязательное точное значение из исходной карточки</small></td></tr><tr><th>Описание</th><td class="seo-workspace-description"><div class="seo-semantic-copy">${highlightedDescription.html}</div><small>${num(draft.description_char_count == null ? String(draft.description || "").length : draft.description_char_count)} символов · цель 1300–1800</small></td></tr><tr><th>Гарантия из исходного описания</th><td>${sourceWarranties}<small>Сохраняется дословно; при отсутствии в исходнике не добавляется</small></td></tr><tr><th>Хештеги</th><td><div class="seo-content-hashtags">${hashtags}</div><small>${num((draft.hashtags || []).length)} тегов · из точных разрешённых ключей; наведи на тег, чтобы увидеть источник</small></td></tr></tbody></table>
        <h4>Динамическая структура названия</h4><p class="seo-project-source-note">${esc(draft.title_structure?.policy || "Основной интент, затем только уместные для товара различающие компоненты.")}</p><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>#</th><th>Компонент</th><th>Источник</th><th>Значение</th><th>Факт</th></tr></thead><tbody>${titleStructureRows}</tbody></table></div>
        <h4>Динамическая структура описания</h4><p class="seo-project-source-note">Набор и порядок блоков выбираются по категории и фактам конкретного товара; названия блоков служат только для аудита и не публикуются.</p><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>#</th><th>Роль блока</th><th>Знаков</th><th>Текст</th></tr></thead><tbody>${sectionRows}</tbody></table></div>
        <h4>Поключевой разбор и фактические вхождения</h4><div class="seo-data-table-wrap"><table class="seo-project-table seo-keyword-usage-audit"><thead><tr><th>Исходный ключ</th><th>Роль</th><th>Морф. основы</th><th>Смысловые связки</th><th>Найденные словоформы</th><th>Где встречается</th><th>Покрытие</th><th>Статус</th></tr></thead><tbody>${keywordAuditRows}</tbody></table></div>
        <h4>Проверка SEO-написания</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Правило</th><th>Фраза / результат</th><th>Покрытие</th><th>Статус</th></tr></thead><tbody>${seoCheckRows}</tbody></table></div><p class="seo-project-source-note">${esc(seoChecks.policy_note || "Это редакционная проверка качества, а не гарантия позиции в поиске Ozon.")}</p>
        <h4>Характеристики, использованные в тексте</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Характеристика</th><th>Значение</th><th>Поле</th></tr></thead><tbody>${characteristicRows}</tbody></table></div>
        <h4>Подтверждённые клеймы</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Клейм</th><th>Статус</th><th>Уверенность</th></tr></thead><tbody>${claimRows}</tbody></table></div>
        <p class="seo-project-source-note">Фразы мониторинга остаются привязаны к SEO-разметке этого SKU. Черновик не отправлялся в Ozon.</p>${auditHtml}`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">SEO-текст недоступен: ${esc(error.message)}</div>`;
    }
  }

  async function openContentReview(sku, product) {
    showSeoDataModal(`Экспертная оценка · SKU ${sku}`, product, '<div class="seo-data-loading">Загружаю независимую проверку SEO-текста…</div>');
    const content = seoState.root.querySelector("[data-seo-data-content]");
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku, dashboard: "seoMonitoring" });
      const payload = await json(`/api/seo-project-content-review?${params}`);
      if (!content) return;
      if (!payload.available) {
        content.innerHTML = '<div class="seo-data-error">Экспертная оценка ещё не выполнялась. Сначала сформируй SEO-текст, затем запусти этап «Экспертная оценка».</div>';
        return;
      }
      const review = payload.review_json || {}, checks = review.checks || {};
      const checkLabels = { russian_language: "Русский язык", phrase_logic: "Логика и связность", prohibited_information: "Запрещённая информация", gender: "Пол", age_audience: "Возраст и аудитория", material: "Материал и состав", color: "Цвет и оттенок", other_characteristics: "Остальные характеристики" };
      const statusLabels = { pass: "Пройдено", review: "Проверить", fail: "Ошибка", not_applicable: "Не применимо" };
      const checkRows = Object.entries(checkLabels).map(([key, label]) => {
        const row = checks[key] || {}, status = row.status || "review";
        const evidence = (row.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
        return `<tr><td><strong>${esc(label)}</strong></td><td><span class="seo-claim-status ${status === "pass" ? "safe" : "review"}">${esc(statusLabels[status] || status)}</span></td><td>${esc(row.summary || "—")}${evidence ? `<ul>${evidence}</ul>` : ""}</td></tr>`;
      }).join("");
      const issueRows = (review.issues || []).map((row) => `<tr><td><span class="seo-claim-status ${row.severity === "minor" ? "safe" : "review"}">${esc(row.severity || "—")}</span></td><td>${esc(row.field || "—")}</td><td>${esc(row.category || "—")}</td><td>${esc(row.fragment || "—")}</td><td>${esc(row.message || "—")}</td><td>${esc(row.suggestion || "—")}</td></tr>`).join("") || '<tr><td colspan="6">Замечаний нет</td></tr>';
      const factRows = (review.source_facts || []).map((row) => `<tr><td><strong>${esc(row.name || "—")}</strong></td><td>${modalValue(row.value)}</td></tr>`).join("") || '<tr><td colspan="2">Подтверждённые характеристики отсутствуют</td></tr>';
      const changes = (review.recommended_changes || []).map((item) => `<li>${esc(item)}</li>`).join("") || '<li>Изменения не требуются</li>';
      const audit = payload.audit_json || {};
      const remediation = audit.remediation || {};
      const editRows = (remediation.applied_edits || []).map((row) => `<tr><td>${esc(row.field || "—")}</td><td>${esc(row.find || "—")}</td><td>${esc(row.replace || "—")}</td><td>${esc(row.reason || "—")}</td></tr>`).join("");
      const remediationHtml = remediation.attempted ? `<h4>Точечная редактура</h4><div class="seo-data-summary"><div><strong>${remediation.accepted ? "Исправления применены и перепроверены" : "Исправления не сохранены"}</strong><span>До: ${esc(remediation.initial_verdict || "—")} · ${num(remediation.initial_score)}; после: ${esc(remediation.followup_verdict || "—")} · ${num(remediation.followup_score)}</span></div><span>${num((remediation.applied_edits || []).length)} правок</span></div>${editRows ? `<div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Поле</th><th>Было</th><th>Стало</th><th>Причина</th></tr></thead><tbody>${editRows}</tbody></table></div>` : '<p class="seo-project-source-note">Безопасных уникальных замен не найдено.</p>'}` : "";
      const stale = payload.stale ? '<div class="seo-data-error">SEO-текст изменился после этой проверки. Вердикт устарел — запусти экспертную оценку повторно.</div>' : "";
      const allowed = review.publication_allowed && !payload.stale;
      content.innerHTML = `<div class="seo-data-summary"><div><strong>${allowed ? "Экспертный шлюз пройден" : "Черновик заблокирован"}</strong><span>${esc(payload.model || "—")} · reasoning ${esc(audit.reasoning_effort || "—")} · ${esc(dateText(payload.reviewed_at))}</span></div><span>${num(review.score)} / 100</span></div>${stale}
        <table class="seo-workspace-summary-table"><tbody><tr class="seo-workspace-table-section"><th colspan="2">Вердикт</th></tr><tr><th>Статус</th><td><strong>${esc(review.verdict || "—")}</strong></td></tr><tr><th>Итог</th><td>${modalValue(review.summary)}</td></tr><tr><th>Готовность</th><td>${allowed ? '<span class="seo-claim-status safe">Можно считать проверенным черновиком</span>' : '<span class="seo-claim-status review">Нельзя считать готовым</span>'}<small>Автопубликация в Ozon отсутствует</small></td></tr></tbody></table>
        <h4>Обязательные проверки</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Проверка</th><th>Статус</th><th>Вывод и доказательства</th></tr></thead><tbody>${checkRows}</tbody></table></div>
        <h4>Замечания</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Важность</th><th>Поле</th><th>Категория</th><th>Фрагмент</th><th>Проблема</th><th>Как исправить</th></tr></thead><tbody>${issueRows}</tbody></table></div>
        ${remediationHtml}<h4>Рекомендованные изменения</h4><ul>${changes}</ul>
        <h4>Эталонные характеристики из карточки</h4><div class="seo-data-table-wrap"><table class="seo-project-table"><thead><tr><th>Характеристика</th><th>Подтверждённое значение</th></tr></thead><tbody>${factRows}</tbody></table></div>
        <section class="seo-content-ai-audit"><h4>Протокол экспертной модели</h4><table class="seo-workspace-summary-table"><tbody><tr><th>Провайдер</th><td>${esc(audit.provider || "—")}</td></tr><tr><th>Модель</th><td>${esc(audit.model || "—")}</td></tr><tr><th>Раздумывание</th><td>${esc(audit.reasoning_effort || "—")}</td></tr><tr><th>Хэш черновика</th><td>${esc(String(payload.draft_hash || "").slice(0, 16))}</td></tr></tbody></table><details><summary>Входной контекст</summary><pre>${esc(JSON.stringify(audit.input_context || {}, null, 2))}</pre></details><details><summary>Системный промт</summary><pre>${esc(audit.system_prompt || "—")}</pre></details><details><summary>Пользовательский промт</summary><pre>${esc(audit.user_prompt || "—")}</pre></details><details><summary>Сырой ответ модели</summary><pre>${esc(audit.raw_response || "—")}</pre></details></section>`;
    } catch (error) {
      if (content) content.innerHTML = `<div class="seo-data-error">Экспертная оценка недоступна: ${esc(error.message)}</div>`;
    }
  }

  function closeSkuWorkspace() {
    seoState.root?.querySelector("[data-seo-sku-workspace-layer]")?.remove();
    const returnFocus = seoState.skuWorkspaceReturnFocus;
    seoState.skuWorkspaceReturnFocus = null;
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function openOwnKeywordsOverview(row) {
    showSeoDataModal(`Свои ключи · SKU ${row.sku}`, row.product_name || "Без названия", `<div class="seo-workspace-source-choice"><button type="button" class="seo-workspace-data-btn" data-seo-own-keyword-source="api"><span>Seller API</span><strong>${num(row.api_keyword_count)}</strong><small>Собранные и очищенные ключи API</small></button><button type="button" class="seo-workspace-data-btn" data-seo-own-keyword-source="mpstats"><span>MPStats</span><strong>${num(row.mpstats_keyword_count)}</strong><small>Собранные и очищенные ключи MPStats</small></button></div>`);
    seoState.root.querySelectorAll("[data-seo-own-keyword-source]").forEach((button) => button.addEventListener("click", () => openSkuKeywords(row.sku, row.product_name || "Без названия", button.dataset.seoOwnKeywordSource)));
  }

  function openIntentOverview(row) {
    showSeoDataModal(`Интент · SKU ${row.sku}`, row.product_name || "Без названия", `<section class="seo-workspace-intent"><span>Основная поисковая сущность</span><strong>${esc(row.search_intent || "Интент ещё не определён")}</strong><p>Ниша каталога: ${esc(row.niche_name || "—")}</p></section>`);
  }

  function openUnavailableWorkspaceData(title, product, source, reason) {
    showSeoDataModal(title, product, `<div class="seo-data-summary"><div><strong>Источник недоступен</strong><span>${esc(source)}</span></div><span>Данные не подменяются нулями</span></div><div class="seo-data-error">${esc(reason)}</div>`);
  }

  function skuWorkspaceMetric(value, status, unavailable = false) {
    if (unavailable) return '<strong class="is-unavailable">Недоступно</strong>';
    if (status && status !== "ok") return `<strong class="is-error">${status === "unavailable" ? "Недоступно" : "Ошибка"}</strong>`;
    return `<strong>${value === null || value === undefined ? "—" : num(value)}</strong>`;
  }

  function skuWorkspaceStages(row) {
    return [
      { data: "characteristics", label: "Все характеристики", value: null, note: "Полная карточка товара", loading: true },
      { data: "api_keywords", run: "api", label: "Ключи Seller API", value: row.api_keyword_count, note: "Поисковые запросы Ozon" },
      { data: "mpstats_keywords", run: "mpstats", label: "Ключи MPStats", value: row.mpstats_keyword_count, note: "Карточные ключи MPStats" },
      { data: "own_keywords", run: "clean", label: "Очистка своих ключей", value: Number(row.api_keyword_count || 0) + Number(row.mpstats_keyword_count || 0), note: "Релевантность, ВЧ/СЧ/НЧ, приоритет" },
      { data: "intent", run: "intent", label: "Интент", value: row.search_intent ? 1 : null, note: row.search_intent || "Ещё не определён" },
      { data: "competitors", run: "competitors", label: "Конкуренты", value: row.niche_competitor_count, note: "Топ-10 товаров по интенту" },
      { data: "competitor_keywords", run: "competitor_keywords", label: "Ключи конкурентов", value: row.competitor_keyword_count, note: "Запросы топ-конкурентов" },
      { data: "competitor_keywords", run: "clean_competitor_keywords", label: "Очистка ключей конкурентов", value: row.competitor_keyword_count, note: "Релевантность и приоритет" },
      { data: "own_reviews", run: "reviews", label: "Свои отзывы", value: row.review_count, note: row.review_error || "Отзывы своих SKU", status: row.review_status },
      { data: "own_questions", run: "questions", label: "Свои вопросы", value: row.question_count, note: row.question_error || "Вопросы своих SKU", status: row.question_status },
      { data: "competitor_reviews", run: "reviews", label: "Отзывы конкурентов", value: row.competitor_review_count, note: row.competitor_review_error || "Отзывы топ-10 конкурентов", status: row.competitor_review_status },
      { data: "competitor_questions", label: "Вопросы конкурентов", value: null, note: "Источник MPStats Ozon FAQ недоступен", status: "unavailable", unavailable: true },
      { data: "claims", run: "claims", label: "SEO-клеймы", value: row.customer_voice_claim_count, note: row.customer_voice_error || "Клеймы из отзывов и вопросов", status: row.customer_voice_status },
      { data: "seo_context", run: "context", label: "SEO-контекст", value: null, note: "Отобранный пакет для семантического ядра", loading: true },
      { data: "content_allocation", run: "allocation", label: "SEO-разметка", value: null, note: "Ключи названия, описания, клеймы и мониторинг", loading: true },
      { data: "content_draft", run: "content_draft", label: "SEO-текст", value: null, note: "Название, описание и отдельные хештеги", loading: true },
      { data: "content_review", run: "content_review", label: "Экспертная оценка", value: null, note: "Язык, логика, запреты и факты карточки", loading: true },
    ];
  }

  function skuWorkspaceStageRows(row) {
    return skuWorkspaceStages(row).map((stage) => {
      const initialStatus = stage.unavailable ? "Недоступно" : stage.loading ? "Проверяется" : stage.status === "error" ? "Ошибка" : stage.status === "ok" || Number(stage.value || 0) > 0 ? "Данные есть" : "Не запускался";
      const dataControl = `<button type="button" class="seo-workspace-stage-link" data-seo-workspace-data="${esc(stage.data)}" title="${esc(stage.note)}">${esc(stage.label)}</button>`;
      const actions = stage.run ? `<div class="seo-workspace-stage-actions"><button type="button" class="seo-workspace-stage-action start" data-seo-workspace-run="${esc(stage.run)}">Запустить</button><button type="button" class="seo-workspace-stage-action stop" data-seo-workspace-stop="${esc(stage.run)}" hidden>Остановить</button><button type="button" class="seo-workspace-stage-action resume" data-seo-workspace-resume="${esc(stage.run)}" hidden>Возобновить</button></div>` : '<span class="seo-workspace-stage-no-run">Просмотр</span>';
      return `<tr data-seo-workspace-stage="${esc(stage.run || stage.data)}"><td>${dataControl}</td><td>${skuWorkspaceMetric(stage.value, stage.status, stage.unavailable)}<small>${esc(stage.note)}</small></td><td><span class="seo-workspace-stage-state ${stage.unavailable ? "unavailable" : ""}" data-seo-workspace-state>${esc(initialStatus)}</span></td><td>${actions}</td></tr>`;
    }).join("");
  }

  function bindSkuWorkspaceActions(row) {
    const panel = seoState.root.querySelector("[data-seo-sku-workspace-panel]");
    const product = row.product_name || "Без названия";
    const dataActions = {
      characteristics: () => openSeoProductCard(row.sku, product),
      api_keywords: () => openSkuKeywords(row.sku, product, "api"),
      mpstats_keywords: () => openSkuKeywords(row.sku, product, "mpstats"),
      own_keywords: () => openOwnKeywordsOverview(row),
      intent: () => openIntentOverview(row),
      competitors: () => openNicheCompetitors(row.sku, product),
      competitor_keywords: () => openCompetitorKeywords(row.sku, product),
      own_reviews: () => openCustomerMessages(row.sku, product, "review"),
      own_questions: () => openCustomerMessages(row.sku, product, "question"),
      competitor_reviews: () => openCompetitorReviews(row.sku, product),
      competitor_questions: () => openUnavailableWorkspaceData(`Вопросы конкурентов · SKU ${row.sku}`, product, "MPStats Ozon", "MPStats Ozon не предоставляет доступный источник FAQ по товарам-конкурентам. Этап остаётся недоступным, а отсутствие данных не считается нулём."),
      claims: () => openCustomerVoiceClaims(row.sku, product),
      seo_context: () => openSemanticContext(row.sku, product),
      content_allocation: () => openContentAllocation(row.sku, product),
      content_draft: () => openContentDraft(row.sku, product),
      content_review: () => openContentReview(row.sku, product),
    };
    panel?.querySelectorAll("[data-seo-workspace-data]").forEach((button) => button.addEventListener("click", () => dataActions[button.dataset.seoWorkspaceData]?.()));

    const setRunControls = (activeAction, state, message) => {
      panel?.querySelectorAll("[data-seo-workspace-run]").forEach((button) => {
        button.disabled = state === "running";
        button.textContent = state === "completed" && button.dataset.seoWorkspaceRun === activeAction ? "Запустить повторно" : "Запустить";
      });
      panel?.querySelectorAll("[data-seo-workspace-stop]").forEach((button) => { button.hidden = state !== "running" || button.dataset.seoWorkspaceStop !== activeAction; });
      panel?.querySelectorAll("[data-seo-workspace-resume]").forEach((button) => { button.hidden = state !== "stopped" || button.dataset.seoWorkspaceResume !== activeAction; });
      panel?.querySelectorAll(`[data-seo-workspace-stage="${activeAction}"] [data-seo-workspace-state]`).forEach((node) => {
        node.className = `seo-workspace-stage-state ${state}`;
        node.textContent = message;
      });
    };

    const executeStage = async (action, resumed = false) => {
      if (seoState.skuWorkspaceRun && !seoState.skuWorkspaceRun.finished) return;
      const options = collectParams();
      const runActions = {
        api: () => refreshSkus([row.sku], options),
        mpstats: () => refreshMpstatsSkus([row.sku], options),
        clean: () => analyzeKeywordSkus([row.sku]),
        intent: () => generateIntentSkus([row.sku]),
        competitors: () => collectCompetitorSkus([row.sku], options),
        competitor_keywords: () => collectCompetitorKeywordSkus([row.sku], options),
        clean_competitor_keywords: () => analyzeCompetitorKeywordSkus([row.sku]),
        reviews: () => collectCustomerMessageSkus([row.sku], options),
        questions: () => collectQuestionSkus([row.sku], options),
        claims: () => analyzeCustomerVoiceSkus([row.sku]),
        context: () => prepareSemanticContextSkus([row.sku]),
        allocation: () => generateContentAllocationSku(row.sku),
        content_draft: () => generateContentDraftSku(row.sku),
        content_review: () => reviewContentDraftSku(row.sku),
      };
      const runner = runActions[action];
      if (!runner) return;
      const controller = new AbortController();
      const context = { action, sku: String(row.sku), controller, stopRequested: false, finished: false, jobId: "" };
      seoState.skuWorkspaceRun = context;
      const terminal = panel?.querySelector("[data-seo-workspace-terminal]");
      const progress = panel?.querySelector("[data-seo-workspace-progress]");
      if (terminal) {
        terminal.hidden = false;
        terminal.classList.add("is-active");
      }
      if (progress) { progress.className = "seo-project-progress"; progress.textContent = `${resumed ? "ВОЗОБНОВЛЕНИЕ" : "ЗАПУСК"}: SKU ${row.sku} · этап ${action}\n`; }
      setRunControls(action, "running", resumed ? "Возобновлён" : "Выполняется");
      try {
        await runner();
        setRunControls(action, context.stopRequested ? "stopped" : "completed", context.stopRequested ? "Остановлен" : "Готово");
      } catch (error) {
        const stopped = context.stopRequested || error?.name === "AbortError";
        const box = panel?.querySelector("[data-seo-workspace-progress]");
        if (box) {
          box.className = `seo-project-progress${stopped ? "" : " error"}`;
          box.textContent += stopped ? "ОСТАНОВЛЕНО: можно возобновить этап.\n" : `ОШИБКА: ${error.message}\n`;
        }
        setRunControls(action, stopped ? "stopped" : "error", stopped ? "Остановлен" : "Ошибка");
      } finally {
        context.finished = true;
        if (seoState.skuWorkspaceRun === context) seoState.skuWorkspaceRun = null;
      }
    };

    panel?.querySelectorAll("[data-seo-workspace-run]").forEach((button) => button.addEventListener("click", () => executeStage(button.dataset.seoWorkspaceRun)));
    panel?.querySelectorAll("[data-seo-workspace-resume]").forEach((button) => button.addEventListener("click", () => executeStage(button.dataset.seoWorkspaceResume, true)));
    panel?.querySelectorAll("[data-seo-workspace-stop]").forEach((button) => button.addEventListener("click", async () => {
      const context = seoState.skuWorkspaceRun;
      if (!context || context.action !== button.dataset.seoWorkspaceStop) return;
      context.stopRequested = true;
      setRunControls(context.action, "running", "Останавливается");
      if (context.jobId && context.action === "clean_competitor_keywords") {
        try {
          await post("/api/seo-project-competitor-keywords/analyze-job/stop?dashboard=seoMonitoring", {
            client: seoState.client, project_id: seoState.projectId, job_id: context.jobId,
          });
        } catch (_) {}
      }
      context.controller.abort();
    }));
  }

  function skuWorkspaceSummary(payload, fallbackTitle) {
    const attributes = Array.isArray(payload?.attributes) ? payload.attributes : [];
    const findAttribute = (...names) => attributes.find((row) => names.some((name) => String(row.attribute_name || "").trim().toLowerCase() === name));
    const title = findAttribute("наименование", "название")?.value || fallbackTitle || "—";
    const description = findAttribute("описание", "аннотация")?.value || "—";
    const importantPattern = /(пол|гендер|возраст|аудитор|категор|тип товара|вид товара|предмет|назначен|материал|состав|цвет|оттенок|сезон|стиль|узор|рисунок|принт|посадк|силуэт|рукав|воротник|длина|рост|размер)/i;
    const technicalPattern = /(фото|изображ|медиа|картин|ссылк|url|rich|контент|json|jison|widget|таблиц.*размер|размерн.*таблиц|дополнительн.*фото|упаков|габарит|вес (?:товара|брутто|нетто)|штрихкод|артикул|идентификатор)/i;
    const sensitive = attributes.filter((row) => {
      const name = String(row.attribute_name || "");
      const value = String(row.value ?? "").trim();
      if (!row.filled || !importantPattern.test(name) || technicalPattern.test(name)) return false;
      if (!value || /^https?:\/\//i.test(value) || /^[\[{]/.test(value) || value.length > 500) return false;
      return true;
    }).slice(0, 18);
    const sensitiveRows = sensitive.length
      ? sensitive.map((item) => `<tr><th>${esc(item.attribute_name)}</th><td>${modalValue(item.value)}</td></tr>`).join("")
      : '<tr><th>SEO-характеристики</th><td><span class="seo-workspace-missing">Значимые признаки не найдены</span></td></tr>';
    return `<table class="seo-workspace-summary-table"><tbody><tr class="seo-workspace-table-section"><th colspan="2">Текущий контент</th></tr><tr><th>Название</th><td>${modalValue(title)}</td></tr><tr><th>Описание</th><td class="seo-workspace-description">${modalValue(description)}</td></tr><tr class="seo-workspace-table-section"><th colspan="2">SEO-чувствительные характеристики</th></tr>${sensitiveRows}</tbody></table>`;
  }

  async function openSkuWorkspace(row, trigger = null) {
    closeSkuWorkspace();
    seoState.skuWorkspaceReturnFocus = trigger || document.activeElement;
    seoState.root.insertAdjacentHTML("beforeend", `<div class="seo-sku-workspace-layer" data-seo-sku-workspace-layer><button type="button" class="seo-sku-workspace-backdrop" data-seo-sku-workspace-close aria-label="Закрыть карточку SKU"></button><aside class="seo-sku-workspace" data-seo-sku-workspace-panel role="dialog" aria-modal="true" aria-labelledby="seoSkuWorkspaceTitle"><header><div><span>Рабочая карточка SKU</span><h2 id="seoSkuWorkspaceTitle">${esc(row.product_name || "Без названия")}</h2><code>${esc(row.sku)}</code></div><button type="button" class="seo-data-close" data-seo-sku-workspace-close aria-label="Закрыть" title="Закрыть"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5 5 15"/></svg></button></header><div class="seo-sku-workspace-scroll"><section><div data-seo-sku-workspace-summary><div class="seo-data-loading">Загружаю название, описание и SEO-характеристики…</div></div></section><section><table class="seo-workspace-stage-table"><thead><tr><th>Этап / данные</th><th>Результат</th><th>Статус</th><th>Управление</th></tr></thead><tbody>${skuWorkspaceStageRows(row)}</tbody></table></section></div><section class="seo-workspace-terminal" data-seo-workspace-terminal><header><strong>Терминал SKU ${esc(row.sku)}</strong><span data-seo-workspace-terminal-status>Ожидание</span></header><pre class="seo-project-progress" data-seo-workspace-progress aria-live="polite">Терминал готов.\nВыберите этап и нажмите «Запустить».</pre></section></aside></div>`);
    const layer = seoState.root.querySelector("[data-seo-sku-workspace-layer]");
    layer?.querySelectorAll("[data-seo-sku-workspace-close]").forEach((button) => button.addEventListener("click", closeSkuWorkspace));
    layer?.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSkuWorkspace(); });
    layer?.querySelector(".seo-data-close")?.focus();
    bindSkuWorkspaceActions(row);
    try {
      const params = new URLSearchParams({ client: seoState.client, marketplace: seoState.detail?.project?.marketplace || seoState.marketplace, sku: row.sku, date_from: seoState.detail?.project?.date_from || "", date_to: seoState.detail?.project?.date_to || "" });
      const payload = await json(`/api/sku-card?${params}`);
      const summary = layer?.querySelector("[data-seo-sku-workspace-summary]");
      if (summary) summary.innerHTML = skuWorkspaceSummary(payload, row.product_name);
      const characteristicStage = layer?.querySelector('[data-seo-workspace-stage="characteristics"]');
      const characteristicCount = Array.isArray(payload?.attributes) ? payload.attributes.length : null;
      const characteristicResult = characteristicStage?.querySelector("td:nth-child(2) strong");
      const characteristicState = characteristicStage?.querySelector("[data-seo-workspace-state]");
      if (characteristicResult) characteristicResult.textContent = characteristicCount == null ? "—" : num(characteristicCount);
      if (characteristicState) characteristicState.textContent = characteristicCount == null ? "Недоступно" : "Данные есть";
    } catch (error) {
      const summary = layer?.querySelector("[data-seo-sku-workspace-summary]");
      if (summary) summary.innerHTML = `<div class="seo-data-error">Карточка недоступна: ${esc(error.message)}</div>`;
      const state = layer?.querySelector('[data-seo-workspace-stage="characteristics"] [data-seo-workspace-state]');
      if (state) { state.className = "seo-workspace-stage-state error"; state.textContent = "Ошибка"; }
    }
    try {
      const contextParams = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku: row.sku, dashboard: "seoMonitoring" });
      const contextPayload = await json(`/api/seo-project-semantic-context?${contextParams}`);
      const contextStage = layer?.querySelector('[data-seo-workspace-stage="context"]');
      const contextResult = contextStage?.querySelector("td:nth-child(2) strong");
      const contextState = contextStage?.querySelector("[data-seo-workspace-state]");
      if (contextPayload.available) {
        const coverage = contextPayload.context_json?.coverage || {};
        const selected = Number(coverage.own_keywords_selected || 0) + Number(coverage.competitor_keywords_selected || 0) + Number(coverage.claims_selected || 0);
        if (contextResult) contextResult.textContent = num(selected);
        if (contextState) { contextState.className = `seo-workspace-stage-state ${contextPayload.status || "ok"}`; contextState.textContent = contextPayload.status === "ok" ? "Данные есть" : "Частично"; }
      } else if (contextState) {
        contextState.textContent = "Не запускался";
      }
    } catch (_) {
      const contextState = layer?.querySelector('[data-seo-workspace-stage="context"] [data-seo-workspace-state]');
      if (contextState) { contextState.className = "seo-workspace-stage-state error"; contextState.textContent = "Ошибка"; }
    }
    for (const stage of [
      { key: "allocation", data: "content_allocation", endpoint: "/api/seo-project-content-allocation", value: (payload) => (payload.monitoring_keywords || []).length },
      { key: "content_draft", data: "content_draft", endpoint: "/api/seo-project-content-draft", value: (payload) => String(payload.draft_json?.title || "").length },
      { key: "content_review", data: "content_review", endpoint: "/api/seo-project-content-review", value: (payload) => payload.review_json?.score },
    ]) {
      try {
        const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, sku: row.sku, dashboard: "seoMonitoring" });
        const payload = await json(`${stage.endpoint}?${params}`);
        const stageRow = layer?.querySelector(`[data-seo-workspace-stage="${stage.key}"]`);
        const result = stageRow?.querySelector("td:nth-child(2) strong");
        const state = stageRow?.querySelector("[data-seo-workspace-state]");
        if (payload.available) {
          if (result) result.textContent = num(stage.value(payload));
          if (state) { state.className = `seo-workspace-stage-state ${payload.status || "ok"}`; state.textContent = payload.status === "ok" ? "Данные есть" : "Частично"; }
        } else if (state) {
          state.className = "seo-workspace-stage-state";
          state.textContent = "Не запускался";
        }
      } catch (_) {
        const state = layer?.querySelector(`[data-seo-workspace-stage="${stage.key}"] [data-seo-workspace-state]`);
        if (state) { state.className = "seo-workspace-stage-state error"; state.textContent = "Ошибка"; }
      }
    }
  }

  function bindProjectRowActions() {
    const body = seoState.root.querySelector("[data-seo-project-rows]");
    body?.querySelectorAll("[data-seo-keywords-open]").forEach((button) => button.addEventListener("click", () => openSkuKeywords(button.dataset.seoSku, button.dataset.seoProduct, button.dataset.seoKeywordsOpen)));
    body?.querySelectorAll("[data-seo-competitors-open]").forEach((button) => button.addEventListener("click", () => openNicheCompetitors(button.dataset.seoCompetitorsOpen, button.dataset.seoProduct)));
    body?.querySelectorAll("[data-seo-competitor-keywords-open]").forEach((button) => button.addEventListener("click", () => openCompetitorKeywords(button.dataset.seoCompetitorKeywordsOpen, button.dataset.seoProduct)));
    body?.querySelectorAll("[data-seo-customer-messages-open]").forEach((button) => button.addEventListener("click", () => openCustomerMessages(button.dataset.seoSku, button.dataset.seoProduct, button.dataset.seoCustomerMessagesOpen)));
    body?.querySelectorAll("[data-seo-competitor-reviews-open]").forEach((button) => button.addEventListener("click", () => openCompetitorReviews(button.dataset.seoCompetitorReviewsOpen, button.dataset.seoProduct)));
    body?.querySelectorAll("[data-seo-customer-voice-open]").forEach((button) => button.addEventListener("click", () => openCustomerVoiceClaims(button.dataset.seoCustomerVoiceOpen, button.dataset.seoProduct)));
    body?.querySelectorAll("[data-seo-product-card]").forEach((button) => button.addEventListener("click", () => openSeoProductCard(button.dataset.seoProductCard, button.dataset.seoProduct)));
    body?.querySelectorAll("[data-seo-sku-workspace]").forEach((button) => button.addEventListener("click", () => {
      const row = (seoState.projectCandidates?.rows || []).find((item) => String(item.sku) === String(button.dataset.seoSkuWorkspace));
      if (row) openSkuWorkspace(row, button);
    }));
  }

  function bindProjectTablePagination() {
    seoState.root.querySelector("[data-seo-project-prev]")?.addEventListener("click", () => loadProjectSkus(seoState.projectPage - 1));
    seoState.root.querySelector("[data-seo-project-next]")?.addEventListener("click", () => loadProjectSkus(seoState.projectPage + 1));
  }

  const dashState = { from: "", to: "", segment: "", frequency: "", query: "", sku: "" };
  const segmentLabelsFull = { priority: "Приоритет", core: "Ядро", tail: "Хвост" };
  const frequencyLabels = { high: "ВЧ", mid: "СЧ", low: "НЧ" };
  const semanticLabels = { brand: "Брендовый", category: "Категорийный", product: "Товарный", attribute: "По характеристике", audience: "Аудиторный", use_case: "Сценарий", problem: "Проблемный", competitor: "Конкурентный", other: "Другой" };
  const modifierLabels = { broad: "Широкий", specific: "Конкретный", qualifying: "Уточняющий" };
  const roleLabels = { head: "Главный", core: "Ядро", support: "Поддержка", tail: "Хвост" };
  const priorityLabels = { high: "Высокий", medium: "Средний", low: "Низкий" };

  function dashParams(extra = {}) {
    return new URLSearchParams({
      client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, dashboard: "seoMonitoring",
      date_from: dashState.from, date_to: dashState.to, segment: dashState.segment,
      frequency: dashState.frequency, query: dashState.query, sku: dashState.sku, ...extra,
    });
  }

  async function loadDashboard() {
    const tiles = seoState.root.querySelector("[data-seo-dash-tiles]");
    if (!tiles) return;
    tiles.innerHTML = '<div class="seo-dash-tile"><span>Считаю…</span></div>';
    try {
      renderDashboard(await json(`/api/seo-project-keyword-stats?${dashParams()}`));
    } catch (error) {
      tiles.innerHTML = `<div class="seo-dash-tile"><span>Не удалось посчитать: ${esc(error.message)}</span></div>`;
    }
  }

  function barList(rows, valueKey, labelKey, formatter) {
    const max = Math.max(...rows.map((row) => Number(row[valueKey]) || 0), 1);
    return `<div class="seo-dash-bars">${rows.map((row) => `<div class="seo-dash-bar" title="${esc(String(row[labelKey]))}"><span class="seo-dash-bar-label">${esc(String(row[labelKey]))}</span><span class="seo-dash-bar-track"><i style="width:${Math.max(2, Math.round((Number(row[valueKey]) || 0) * 100 / max))}%"></i></span><span class="seo-dash-bar-value">${formatter(row[valueKey])}</span></div>`).join("")}</div>`;
  }

  function renderDashboard(data) {
    const summary = data.summary || {};
    const tiles = seoState.root.querySelector("[data-seo-dash-tiles]");
    if (tiles) {
      tiles.innerHTML = [
        ["Ключей", num(summary.query_count)],
        ["Трафик", num(summary.traffic)],
        ["Искали", num(summary.demand)],
        ["Заказы", num(summary.orders)],
        ["GMV", rub(summary.gmv)],
        ["Конверсия", summary.conversion_pct === null || summary.conversion_pct === undefined ? "—" : pct(summary.conversion_pct)],
        ["Ср. позиция", num(summary.average_position, 1)],
        ["Дней в периоде", num((data.series || []).length)],
      ].map(([label, value]) => `<div class="seo-dash-tile"><span>${esc(label)}</span><strong>${esc(String(value))}</strong></div>`).join("");
    }
    const series = seoState.root.querySelector("[data-seo-dash-series]");
    if (series) {
      const rows = data.series || [];
      series.innerHTML = rows.length
        ? barList(rows.map((row) => ({ label: ruDate(row.day), traffic: row.traffic })), "traffic", "label", (value) => num(value))
        : '<p class="seo-project-source-note">Нет дневных снимков в этом периоде</p>';
    }
    const classes = seoState.root.querySelector("[data-seo-dash-classes]");
    if (classes) {
      const segments = (data.segments || []).map((row) => ({ label: segmentLabelsFull[row.segment] || row.segment, ...row }));
      const frequency = (data.frequency || []).map((row) => ({ label: frequencyLabels[row.frequency] || row.frequency, ...row }));
      const table = (rows) => `<table class="seo-project-table seo-dash-table"><thead><tr><th>Класс</th><th>Ключей</th><th>Трафик</th><th>Заказы</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.label)}</td><td class="number">${num(row.query_count)}</td><td class="number">${num(row.traffic)}</td><td class="number">${num(row.orders)}</td></tr>`).join("")}</tbody></table>`;
      classes.innerHTML = (segments.length ? table(segments) : "") + (frequency.length ? table(frequency) : "")
        || '<p class="seo-project-source-note">Нет данных</p>';
    }
    const top = seoState.root.querySelector("[data-seo-dash-top]");
    if (top) {
      const rows = data.top || [];
      top.innerHTML = rows.length
        ? `<table class="seo-project-table seo-dash-table"><thead><tr><th>Запрос</th><th>Класс</th><th>Трафик</th><th>Заказы</th><th>Конверсия</th></tr></thead><tbody>${rows.map((row) => `<tr><td title="${esc(row.search_query)}">${esc(row.search_query)}</td><td><span class="seo-keyword-segment ${esc(row.segment)}">${esc(segmentLabelsFull[row.segment] || row.segment)}</span> <span class="seo-freq-chip ${esc(row.frequency)}">${esc(frequencyLabels[row.frequency] || row.frequency)}</span></td><td class="number">${num(row.traffic)}</td><td class="number">${num(row.orders)}</td><td class="number">${row.conversion_pct === null || row.conversion_pct === undefined ? "—" : pct(row.conversion_pct)}</td></tr>`).join("")}</tbody></table>`
        : '<p class="seo-project-source-note">Нет ключей под текущими фильтрами</p>';
    }
  }

  function bindDashboard() {
    const root = seoState.root;
    root.querySelector("[data-seo-dash-apply]")?.addEventListener("click", () => {
      dashState.from = root.querySelector("[data-seo-dash-from]")?.value || "";
      dashState.to = root.querySelector("[data-seo-dash-to]")?.value || "";
      dashState.segment = root.querySelector("[data-seo-dash-segment]")?.value || "";
      dashState.frequency = root.querySelector("[data-seo-dash-frequency]")?.value || "";
      dashState.query = root.querySelector("[data-seo-dash-query]")?.value.trim() || "";
      dashState.sku = root.querySelector("[data-seo-dash-sku]")?.value.trim() || "";
      loadDashboard();
      loadKeywords(1);
    });
    root.querySelector("[data-seo-dash-reset]")?.addEventListener("click", () => {
      ["[data-seo-dash-from]", "[data-seo-dash-to]", "[data-seo-dash-query]", "[data-seo-dash-sku]"].forEach((selector) => {
        const field = root.querySelector(selector);
        if (field) field.value = "";
      });
      const rangeToggle = root.querySelector('[data-wb-range-prefix="seo_dash"] [data-wb-range-toggle]');
      if (rangeToggle) rangeToggle.textContent = "Весь период";
      const segment = root.querySelector("[data-seo-dash-segment]");
      const frequency = root.querySelector("[data-seo-dash-frequency]");
      if (segment) segment.value = "";
      if (frequency) frequency.value = "";
      Object.assign(dashState, { from: "", to: "", segment: "", frequency: "", query: "", sku: "" });
      loadDashboard();
      loadKeywords(1);
    });
  }

  const keywordsState = { page: 1, snapshot: "", query: "", sku: "" };

  async function loadKeywords(page = 1) {
    keywordsState.page = Math.max(1, page);
    const target = seoState.root.querySelector("[data-seo-keywords-rows]");
    if (!target) return;
    target.innerHTML = '<tr><td colspan="16">Загружаю ключи…</td></tr>';
    const params = dashParams({ page: String(keywordsState.page), limit: "50" });
    try {
      const data = await json(`/api/seo-project-keywords?${params}`);
      renderKeywords(data);
    } catch (error) {
      target.innerHTML = `<tr><td colspan="16">Не удалось загрузить ключи: ${esc(error.message)}</td></tr>`;
    }
  }

  function keywordSourceLabel(source) {
    const value = String(source || "").toLowerCase();
    if (value.startsWith("mpstats_")) return "MPStats";
    if (value.includes("seller") || value.includes("ozon_search")) return "Seller API";
    if (value.includes("wb") && value.includes("report")) return "WB отчёт";
    return source || "—";
  }

  function renderKeywords(data) {
    const target = seoState.root.querySelector("[data-seo-keywords-rows]");
    if (!target) return;
    target.innerHTML = (data.rows || []).map((row) => `<tr><td>${esc(ruDate(row.snapshot_date))}</td><td>${esc(keywordSourceLabel(row.source))}</td><td class="seo-builder-sku" title="${esc(row.sku)}">${esc(row.sku)}</td><td title="${esc(row.product_name || "")}">${esc(row.product_name || "—")}</td><td title="${esc(row.search_query)}">${esc(row.search_query)}</td><td>${esc(semanticLabels[row.semantic_type] || "—")}</td><td>${esc(modifierLabels[row.modifier_type] || "—")}</td><td><span class="seo-freq-chip ${esc(row.frequency)}">${esc(frequencyLabels[row.frequency] || "—")}</span></td><td><span class="seo-keyword-segment ${esc(row.segment)}" title="${esc(row.segment_reason || "")}">${esc(segmentLabelsFull[row.segment] || row.segment)}</span></td><td>${esc(roleLabels[row.core_role] || "—")}</td><td title="${esc(row.rationale || "Запустите AI-анализ")}"><span class="seo-keyword-priority ${esc(row.priority_label || "")}">${esc(priorityLabels[row.priority_label] || "—")}${row.priority_score === null || row.priority_score === undefined ? "" : ` · ${num(row.priority_score)}`}</span></td><td class="number">${num(row.average_position, 1)}</td><td class="number">${num(row.search_demand)}</td><td class="number">${num(row.traffic)}</td><td class="number">${num(row.orders)}</td><td class="number">${rub(row.revenue_rub)}</td></tr>`).join("")
      || '<tr><td colspan="16">Ключей за этот день ещё нет — запустите сбор поисковых запросов.</td></tr>';
    const meta = seoState.root.querySelector("[data-seo-keywords-meta]");
    if (meta) meta.textContent = `Строк ${num(data.total)} · страница ${num(data.page)} из ${num(data.total_pages)}`;
    const scope = seoState.root.querySelector("[data-seo-keywords-scope]");
    if (scope) scope.textContent = `${dashState.from || "весь период"}${dashState.to ? ` — ${dashState.to}` : ""} · снимков ${num((data.snapshots || []).length)}`;
    const prev = seoState.root.querySelector("[data-seo-keywords-prev]");
    const next = seoState.root.querySelector("[data-seo-keywords-next]");
    if (prev) prev.disabled = data.page <= 1;
    if (next) next.disabled = data.page >= data.total_pages;
  }

  function bindKeywordsPanel() {
    const root = seoState.root;
    root.querySelector("[data-seo-keywords-snapshot]")?.addEventListener("change", (event) => {
      keywordsState.snapshot = event.target.value;
      loadKeywords(1);
    });
    root.querySelector("[data-seo-keywords-prev]")?.addEventListener("click", () => loadKeywords(keywordsState.page - 1));
    root.querySelector("[data-seo-keywords-next]")?.addEventListener("click", () => loadKeywords(keywordsState.page + 1));
  }

  async function loadDetail() {
    clearFullRunPoll();
    seoState.fullRunJobId = "";
    seoState.root.innerHTML = '<div class="seo-project-empty"><p>Загружаю SEO-проект…</p></div>';
    try {
      const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, project_id: seoState.projectId, dashboard: "seoMonitoring" });
      const data = await json(`/api/seo-project?${params}`);
      seoState.detail = data;
      applyProjectFilterState(data.project?.sku_filters);
      seoState.root.innerHTML = detailHtml(data);
      const collectPanel = seoState.root.querySelector("[data-seo-collect]");
      const catalogPanel = seoState.root.querySelector("[data-seo-project-catalog]");
      if (collectPanel && catalogPanel) {
        catalogPanel.before(collectPanel);
      }
      seoState.root.querySelector("[data-seo-back]")?.addEventListener("click", async () => { setProjectParam(""); await loadList(); });
      seoState.root.querySelector("[data-seo-ai-settings]")?.addEventListener("click", openAiSettings);
      seoState.root.querySelector("[data-seo-edit-skus]")?.addEventListener("click", () => { clearBuilderUrlState(); openCreateWorkspace(false, seoState.detail); });
      seoState.root.querySelector("[data-seo-rename]")?.addEventListener("dblclick", (event) => beginProjectRename(event.currentTarget, async () => loadDetail()));
      seoState.root.querySelector("[data-seo-export-content]")?.addEventListener("click", (event) => downloadProjectContentExport(event.currentTarget));
      seoState.root.querySelector("[data-seo-full-run]")?.addEventListener("click", (event) => {
        startFullRun(event.currentTarget).catch((error) => {
          event.currentTarget.disabled = false;
          showCollectionFailure("Полный ран · ошибка запуска", error.message);
        });
      });
      seoState.root.querySelector("[data-seo-full-run-stop]")?.addEventListener("click", (event) => {
        stopFullRun(event.currentTarget).catch((error) => {
          event.currentTarget.disabled = false;
          showCollectionFailure("Полный ран · остановка", error.message);
        });
      });
      seoState.root.querySelector("[data-seo-refresh-all]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const options = collectParams();
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          if (!options.date_from || !options.date_to) {
            if (plan) plan.textContent = "Укажите период сбора";
            return;
          }
          const weekWarning = collectPeriodWarning(options, seoState.detail?.project?.marketplace);
          if (weekWarning) {
            if (plan) plan.textContent = weekWarning;
            return;
          }
          if (plan) plan.textContent = "Определяю список SKU…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await refreshSkus(skus, options);
        } catch (error) {
          const message = `Не удалось запустить сбор: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("Seller API · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-collect-mpstats]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const options = collectParams();
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          if (!options.date_from || !options.date_to) {
            if (plan) plan.textContent = "Укажите период сбора для MPStats";
            return;
          }
          if (options.date_from > options.date_to) {
            if (plan) plan.textContent = "Дата начала периода MPStats должна быть не позже даты окончания";
            return;
          }
          if (plan) plan.textContent = "Определяю список SKU для MPStats…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await refreshMpstatsSkus(skus, options);
        } catch (error) {
          const message = `Не удалось запустить сбор MPStats: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("MPStats · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-collect-customer-messages]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = `Определяю SKU для отзывов и вопросов за ${num(options.feedback_days)} дней…`;
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await collectCustomerMessageSkus(skus, options);
        } catch (error) {
          const message = `Отзывы и вопросы не собраны: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("Customer Voice · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-collect-questions]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = `Определяю SKU для отдельного сбора вопросов за ${num(options.feedback_days)} дней…`;
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await collectQuestionSkus(skus, options);
        } catch (error) {
          const message = `Вопросы не собраны: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("Ozon Seller API · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-analyze-customer-voice]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для анализа отзывов и вопросов…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await analyzeCustomerVoiceSkus(skus);
        } catch (error) {
          const message = `SEO-клеймы не сформированы: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("AI · Customer Voice · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-prepare-semantic-context]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для подготовки SEO-контекста…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await prepareSemanticContextSkus(skus);
        } catch (error) {
          const message = `SEO-контекст не подготовлен: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("SEO-контекст · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-generate-content-allocation]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget, plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для SEO-разметки…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) { if (plan) plan.textContent = "Под текущие фильтры SKU не попали"; return; }
          await generateContentAllocationSkus(skus);
        } catch (error) {
          const message = `SEO-разметка не выполнена: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("SEO-разметка · ошибка запуска", message);
        } finally { button.disabled = false; }
      });
      seoState.root.querySelector("[data-seo-generate-content-drafts]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget, plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для генерации SEO-текстов…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) { if (plan) plan.textContent = "Под текущие фильтры SKU не попали"; return; }
          await generateContentDraftSkus(skus);
        } catch (error) {
          const message = `SEO-тексты не сформированы: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("SEO-тексты · ошибка запуска", message);
        } finally { button.disabled = false; }
      });
      seoState.root.querySelector("[data-seo-review-content-drafts]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget, plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для экспертной оценки SEO-текстов…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) { if (plan) plan.textContent = "Под текущие фильтры SKU не попали"; return; }
          await reviewContentDraftSkus(skus);
        } catch (error) {
          const message = `Экспертная оценка не выполнена: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("Экспертная оценка · ошибка запуска", message);
        } finally { button.disabled = false; }
      });
      seoState.root.querySelector("[data-seo-analyze-keywords]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для очистки и ранжирования…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await analyzeKeywordSkus(skus);
        } catch (error) {
          const message = `Очистка и ранжирование не выполнены: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("AI · ключи · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-generate-intents]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (plan) plan.textContent = "Определяю SKU для построения интентов…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали — расширьте отбор или выберите всю группу";
            return;
          }
          await generateIntentSkus(skus);
        } catch (error) {
          const message = `Интенты не сформированы: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("AI · интенты · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-collect-competitors]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        button.disabled = true;
        try {
          const options = collectParams();
          if (!options.date_from || !options.date_to || options.date_from > options.date_to) {
            if (plan) plan.textContent = "Проверьте период сбора конкурентов";
            return;
          }
          if (plan) plan.textContent = "Определяю SKU и проверяю наличие интентов…";
          const skus = await collectScopeSkus(options.scope);
          if (!skus.length) {
            if (plan) plan.textContent = "Под текущие фильтры SKU не попали";
            return;
          }
          await collectCompetitorSkus(skus, options);
        } catch (error) {
          const message = `Конкуренты не собраны: ${error.message}`;
          if (plan) plan.textContent = message;
          showCollectionFailure("MPStats · конкуренты · ошибка запуска", message);
        } finally {
          button.disabled = false;
        }
      });
      seoState.root.querySelector("[data-seo-collect-competitor-keywords]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget, plan = seoState.root.querySelector("[data-seo-collect-plan]"); button.disabled = true;
        try {
          const options = collectParams();
          if (!options.date_from || !options.date_to || options.date_from > options.date_to) { if (plan) plan.textContent = "Проверьте период ключей конкурентов"; return; }
          if (plan) plan.textContent = "Проверяю конкурентов и формирую план ключей…";
          const skus = await collectScopeSkus(options.scope); if (!skus.length) { if (plan) plan.textContent = "Под текущие фильтры SKU не попали"; return; }
          await collectCompetitorKeywordSkus(skus, options);
        } catch (error) { const message = `Ключи конкурентов не собраны: ${error.message}`; if (plan) plan.textContent = message; showCollectionFailure("MPStats · ключи конкурентов · ошибка запуска", message); } finally { button.disabled = false; }
      });
      seoState.root.querySelector("[data-seo-analyze-competitor-keywords]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget, plan = seoState.root.querySelector("[data-seo-collect-plan]"); button.disabled = true;
        try {
          const options = collectParams(); if (plan) plan.textContent = "Формирую отдельный AI-план ключей конкурентов…";
          const skus = await collectScopeSkus(options.scope); if (!skus.length) { if (plan) plan.textContent = "Под текущие фильтры SKU не попали"; return; }
          await analyzeCompetitorKeywordSkus(skus);
        } catch (error) { const message = `AI ключей конкурентов не выполнен: ${error.message}`; if (plan) plan.textContent = message; showCollectionFailure("AI · ключи конкурентов · ошибка запуска", message); } finally { button.disabled = false; }
      });
      seoState.root.querySelector("[data-seo-stop-competitor-ai]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const jobId = button.dataset.jobId;
        if (!jobId) return;
        button.disabled = true;
        try {
          await post("/api/seo-project-competitor-keywords/analyze-job/stop?dashboard=seoMonitoring", {
            client: seoState.client, project_id: seoState.projectId, job_id: jobId,
          });
        } catch (error) {
          const box = showCollectionTerminal("AI · ключи конкурентов");
          box.textContent += `ОШИБКА ОСТАНОВКИ: ${error.message}\n`;
          button.disabled = false;
        }
      });
      bindBuilderTableHeader("project");
      bindProjectTablePagination();
      await loadProjectSkus(1);
      resumeCompetitorAnalysisJob().catch((error) => {
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        if (plan) plan.textContent = `Статус AI ключей конкурентов недоступен: ${error.message}`;
      });
      pollFullRunJob().catch((error) => {
        const plan = seoState.root.querySelector("[data-seo-collect-plan]");
        if (plan) plan.textContent = `Статус полного рана недоступен: ${error.message}`;
      });
    } catch (error) {
      seoState.root.innerHTML = `<div class="seo-project-empty"><h3>Не удалось открыть проект</h3><p>${esc(error.message)}</p><button class="seo-project-btn" data-seo-back>← К списку</button></div>`;
      seoState.root.querySelector("[data-seo-back]")?.addEventListener("click", async () => { setProjectParam(""); await loadList(); });
    }
  }

  async function loadList() {
    setCreateParam(false);
    seoState.root.innerHTML = '<div class="seo-project-empty"><p>Загружаю список проектов…</p></div>';
    const params = new URLSearchParams({ client: seoState.client, project_kind: seoState.projectKind, dashboard: "seoMonitoring" });
    try { renderList((await json(`/api/seo-projects?${params}`)).rows || []); }
    catch (error) { seoState.root.innerHTML = `<div class="seo-project-empty"><h3>SEO-проекты недоступны</h3><p>${esc(error.message)}</p></div>`; }
  }

  async function mount(context) {
    seoState.client = context.client;
    seoState.marketplaces = [...new Set((Array.isArray(context.marketplaces) ? context.marketplaces : [context.marketplace]).filter((marketplace) => ["ozon", "wb", "yandex_market"].includes(marketplace)))];
    const scopedClient = new URL(window.location.href).searchParams.get("client") || context.client;
    if (["toptop", "lera_nena"].includes(scopedClient) && !seoState.marketplaces.includes("yandex_market")) seoState.marketplaces.push("yandex_market");
    if (!seoState.marketplaces.length) seoState.marketplaces = ["ozon"];
    seoState.marketplace = seoState.marketplaces.includes(context.marketplace) ? context.marketplace : seoState.marketplaces[0];
    seoState.projectKind = context.projectKind === "generation" ? "generation" : "monitoring";
    const params = new URLSearchParams(window.location.search);
    seoState.projectId = params.get("seo_project") || "";
    const createMode = params.get("seo_new") === "1";
    const createMarketplace = params.get("seo_marketplace");
    if (seoState.marketplaces.includes(createMarketplace)) seoState.marketplace = createMarketplace;
    const root = ensureRoot();
    root.classList.remove("hidden");
    if (createMode && seoState.projectKind === "generation" && !seoState.marketplaces.includes(createMarketplace)) openMarketplaceChooser();
    else if (createMode) openCreateWorkspace(true);
    else if (seoState.projectId) await loadDetail();
    else await loadList();
  }

  function unmount() { if (seoState.root) seoState.root.classList.add("hidden"); }
  window.SeoProjectsApp = { mount, unmount };
})();
