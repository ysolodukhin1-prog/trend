const APP_ROUTE_MATCH = window.location.pathname.match(/^\/(glory|sportmaster|konstex)(?:\/|$)/);
const APP_ROUTE_PREFIX = APP_ROUTE_MATCH ? `/${APP_ROUTE_MATCH[1]}` : "";

function isSharedDashboardRoute() {
  return APP_ROUTE_PREFIX === "/glory";
}

function resolveAppUrl(url) {
  if (typeof url === "string") {
    const normalizedApiUrl = url.replace(/^\/(?:glory\/)*api(?=\/|$)/, "/api");
    if (normalizedApiUrl.startsWith("/" + "api" + "/")) {
      return `${APP_ROUTE_PREFIX}${normalizedApiUrl}`;
    }
    const normalizedReactUrl = url.replace(/^\/(?:(?:glory|sportmaster|konstex)\/)*react(?=\/|$)/, "/react");
    if (APP_ROUTE_PREFIX && normalizedReactUrl.startsWith("/react/")) {
      return `${APP_ROUTE_PREFIX}${normalizedReactUrl}`;
    }
  }
  return url;
}

function applyInterfaceUrlState(url) {
  url.searchParams.set("client", currentClient());
  url.searchParams.set("dashboard", state.dashboard);
  const values = {
    marketplace: qs("marketplace")?.value || "",
    date_from: qs("date_from")?.value || "",
    date_to: qs("date_to")?.value || "",
    product: qs("product")?.value || "",
    article: qs("article")?.value || "",
    period_group: qs("period_group")?.value || "",
  };
  Object.entries(values).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
  });
  url.searchParams.delete("categories");
  selectedValues("category").forEach((category) => url.searchParams.append("categories", category));
  return url;
}

function reactInterfaceUrl() {
  const url = new URL(window.location.href);
  const path = url.pathname.replace(/\/+$/, "");
  if (!path.endsWith("/react")) url.pathname = `${path || ""}/react/`;
  else url.pathname = `${path}/`;
  return applyInterfaceUrlState(url).toString();
}

function legacyInterfaceUrl() {
  const url = new URL(window.location.href);
  const path = url.pathname.replace(/\/+$/, "");
  const legacyPath = path.endsWith("/react") ? path.slice(0, -"/react".length) : path;
  url.pathname = legacyPath ? `${legacyPath}/` : "/";
  return applyInterfaceUrlState(url).toString();
}

function isReactInterfacePath() {
  return window.location.pathname.replace(/\/+$/, "").endsWith("/react");
}

function oppositeInterfaceUrl() {
  return isReactInterfacePath() ? legacyInterfaceUrl() : reactInterfaceUrl();
}

function syncInterfaceToggle() {
  const link = qs("navReactUi");
  if (!link) return;
  const targetLegacy = isReactInterfacePath();
  const label = targetLegacy ? "Легаси" : "React";
  const title = targetLegacy ? "Перейти в легаси-интерфейс" : "Перейти в React-интерфейс";
  link.textContent = label;
  link.href = targetLegacy ? legacyInterfaceUrl() : reactInterfaceUrl();
  link.dataset.targetUi = targetLegacy ? "legacy" : "react";
  link.dataset.compactLabel = title;
  link.setAttribute("title", title);
  link.setAttribute("aria-label", title);
}

async function appFetch(url, options) {
  const resolvedUrl = resolveAppUrl(url);
  const response = await window.fetch(resolvedUrl, options);
  const isProtectedAdminRequest = typeof resolvedUrl === "string"
    && resolvedUrl.includes("/api/admin/")
    && !resolvedUrl.includes("/api/admin/auth/");
  if (response.status === 401 && isProtectedAdminRequest) {
    state.adminAuthenticated = false;
    state.adminLoginRequested = state.dashboard === "admin";
    showAdminLogin("Сессия завершена. Войдите снова.");
  }
  return response;
}

const state = {
  client: "toptop",
  clientLocked: false,
  clients: [
    { key: "toptop", label: "TOPTOP", status: "active", reports: ["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics", "inventoryHistory", "planfact", "salesPlanning", "mediaPlan", "profitLoss", "unitEconomics", "seoMonitoring", "wbSearchQueries", "wbAdSearchQueries", "reviews", "commercialRadar"] },
    { key: "lera_nena", label: "LERA NENA", status: "active", reports: ["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics", "inventoryHistory", "planfact", "salesPlanning", "mediaPlan", "profitLoss", "unitEconomics", "seoMonitoring", "wbSearchQueries", "wbAdSearchQueries", "reviews", "commercialRadar", "wbEntrance"] },
  ],
  navigationReportsByClient: {},
  rows: [],
  columns: [],
  page: 1,
  totalPages: 1,
  total: 0,
  sortCol: "total_stock_qty",
  sortDir: "desc",
  primaryChartMetric: "stock",
  secondaryChartMetric: "stock",
  columnFilters: {},
  columnOrders: {},
  dashboard: "planfact",
  drillCategory: "",
  categoryNames: [],
  selectedCategories: [],
  collectionStatusValues: [],
  selectedCollectionStatuses: [],
  seoStatusValues: [],
  selectedSeoStatuses: [],
  sportmasterFilterValues: {},
  boironBrandValues: [],
  productNames: [],
  availableDateFrom: "",
  availableDateTo: "",
  pendingAdvCampaignId: "",
  planfactMonths: [],
  abcDateRangeInitialized: false,
  advDateRangeInitialized: false,
  mediaAdvDateRangeInitialized: false,
  skuDateRangeInitialized: false,
  funnelDateRangeInitialized: false,
  weeklyDynamicsDateRangeInitialized: false,
  inventoryHistoryDateRangeInitialized: false,
  seoMonitoringDateRangeInitialized: false,
  seoMonitoringSelectedProducts: [],
  seoMonitoringValuationNote: "",
  seoProjectsNavSource: "monitoring",
  planfactDateRangeInitialized: false,
  wbSearchSelectedMetrics: ["search_demand", "card_visits", "cart_adds", "ordered_units", "visibility_pct", "average_position"],
  wbSearchMetricAxes: {},
  wbSearchMetricTypes: { search_demand: "bar" },
  wbEntranceSelectedMetrics: ["impressions", "card_visits", "cart_adds", "ordered_units", "ctr_pct", "visit_to_order_pct"],
  wbEntranceMetricAxes: {},
  wbEntranceMetricTypes: { impressions: "bar" },
  funnelSelectedMetrics: ["impressions_total", "adv_impressions", "organic_impressions", "card_visit_to_order_pct"],
  funnelAvailableMetrics: null,
  funnelMetricAxes: {},
  funnelMetricTypes: {},
  advSelectedMetrics: ["orders_amount_rub", "expense_rub", "drr_pct", "total_drr_pct"],
  advMetricAxes: { drr_pct: "right", total_drr_pct: "right" },
  advMetricTypes: { orders_amount_rub: "bar" },
  mediaAdvSelectedMetrics: ["attributed_revenue_rub", "post_view_revenue_rub", "expense_rub", "drr_attributed_pct"],
  mediaAdvMetricAxes: { drr_attributed_pct: "right" },
  mediaAdvMetricTypes: { attributed_revenue_rub: "bar" },
  planfactRevenueSelectedMetrics: ["orders_rub", "sales_rub", "sales_plan_daily_rub", "ad_spend_rub"],
  planfactRevenueMetricAxes: { orders_rub: "right", ad_spend_rub: "right" },
  planfactRevenueMetricTypes: { sales_rub: "bar" },
  planfactExpenseSelectedMetrics: ["ad_spend_rub", "ad_spend_plan_daily_rub"],
  planfactExpenseMetricAxes: {},
  planfactExpenseMetricTypes: {},
  adminImportMode: "manual",
  adminClient: "toptop",
  adminClientRegistry: null,
  adminUsersRegistry: null,
  adminDatabaseOverview: null,
  adminDatabaseFilters: { client: "", db: "", schema: "", type: "", report: "", search: "" },
  adminEditingUserId: 0,
  adminOnboardingMarketplace: "ozon",
  adminHistoryMarketplace: "",
  adminWbStockHistoryRanges: {},
  adminHistoryOverwrite: false,
  adminHistoryStartConfirmation: null,
  adminHistoryInspection: null,
  adminHistoryInspectionKey: "",
  adminHistoryInspectionLoading: false,
  adminHistoryInspectionError: "",
  adminOnboardingClientKey: "",
  adminOnboardingDraftName: "",
  adminOnboardingPollToken: 0,
  adminApiDateFrom: "",
  adminApiDateTo: "",
  adminImportStatuses: {},
  adminImportRunning: false,
  adminCurrentImportKey: "",
  adminStopRequested: false,
  adminTerminalLines: [],
  adminLiveProgress: null,
  adminAllClientsDaily: null,
  adminAllClientsAssortment: null,
  adminAllClientsPollTimer: 0,
  adminAllClientsAssortmentPollTimer: 0,
  adminAllClientsTaskSelection: {},
  adminAuthenticated: null,
  adminAuthConfigured: null,
  adminAllowedSections: null,
  adminFullAccess: false,
  adminLoginRequested: false,
  apiTerminalLines: [],
  apiExportMarketplace: "wb",
  apiExportMode: "advertising",
  wbApiRunningMethod: "",
  wbApiProgressTimers: {},
  wbApiLastProgressMessages: {},
  wbApiProgressSnapshots: {},
  wbApiLastPromotionCountResultFile: "",
  wbApiLastPromotionAdvertsResultFile: "",
  wbApiLastPromotionStatsResultFile: "",
  wbApiLastContentCategoriesResultFile: "",
  wbApiLastContentCardsResultFile: "",
  wbApiLastContentCharacteristicsResultFile: "",
  wbApiLastLogFiles: {},
  ozonSeoLastLogFile: "",
  ozonSeoLastResponse: null,
  filtersDirty: false,
  mode: window.location.protocol === "file:" && window.DASHBOARD_DATA ? "static" : "api",
};

const metricLabels = {
  stock: "по остаткам",
  sku: "по SKU",
  orders: "по заказам, шт",
  sales: "по заказам, руб",
  attributes: "по характеристикам",
};

const metricSelectLabels = {
  stock: "Остатки",
  sku: "SKU",
  orders: "Заказы, шт",
  sales: "Заказы, руб",
  attributes: "Характеристики",
};

const metricFields = {
  stock: "total_stock_qty",
  sku: "sku_count",
  orders: "zakazano_sht",
  sales: "zakazano_rub",
  attributes: "category_attribute_count",
};

function selectedMetricKey() {
  return qs("sort")?.value || "stock";
}

function selectedMetricField() {
  return metricFields[selectedMetricKey()] || "total_stock_qty";
}

function selectedMetricLabel() {
  return metricLabels[selectedMetricKey()] || "по остаткам";
}

function normalizeChartMetricKey(key) {
  return Object.prototype.hasOwnProperty.call(metricFields, key) ? key : "stock";
}

function chartMetricKey(chart) {
  return normalizeChartMetricKey(chart === "secondary" ? state.secondaryChartMetric : state.primaryChartMetric);
}

function chartMetricField(chart) {
  return metricFields[chartMetricKey(chart)] || "total_stock_qty";
}

function chartMetricLabel(chart) {
  return metricLabels[chartMetricKey(chart)] || "по остаткам";
}

const categoryLevelLabels = {
  category: "Категория",
  subcategory: "Подкатегория",
  brand: "Бренд",
  model: "Модель",
  gender: "Пол",
  age: "Возраст",
  collection: "Коллекция",
  season_sm: "Сезон",
  sport: "Назначение / спорт",
};

const categoryLevelNouns = {
  category: "категорий",
  subcategory: "подкатегорий",
  brand: "брендов",
  model: "моделей",
  gender: "значений пола",
  age: "возрастных групп",
  collection: "коллекций",
  season_sm: "сезонов",
  sport: "назначений",
};

const sportmasterCategoryLevelValues = new Set(Object.keys(categoryLevelLabels));

function selectedCategoryLevel() {
  const value = qs("category_level")?.value || "category";
  if (currentClient() === "sportmaster" && sportmasterCategoryLevelValues.has(value)) return value;
  return value === "subcategory" && currentClient() === "sportmaster" ? "subcategory" : "category";
}

function categoryLevelNoun() {
  return categoryLevelNouns[selectedCategoryLevel()] || "категорий";
}

function updateCategoryLevelOptions() {
  const select = qs("category_level");
  if (!select) return;
  const allowSportmasterLevels = currentClient() === "sportmaster";
  Array.from(select.options).forEach((option) => {
    if (option.dataset.sportmasterOnly === "true") option.hidden = !allowSportmasterLevels;
  });
  if (!allowSportmasterLevels && select.selectedOptions[0]?.dataset.sportmasterOnly === "true") {
    select.value = "category";
  }
}

const defaultColumns = [
  { key: "category_name", label: "Категория", type: "text" },
  { key: "sku_count", label: "SKU", type: "number" },
  { key: "abc_combined", label: "ABC итог", type: "text" },
  { key: "zakazano_rub", label: "Заказано, руб", type: "number" },
  { key: "avg_price_rub", label: "Средняя цена, руб", type: "number" },
  { key: "zakazano_sht", label: "Заказано, шт", type: "number" },
  { key: "total_stock_qty", label: "Остаток, шт", type: "number" },
  { key: "category_attribute_count", label: "Характеристик", type: "number" },
  { key: "orders_share_pct", label: "Доля заказов, %", type: "number" },
  { key: "orders_cumulative_pct", label: "Накоп. доля заказов, %", type: "number" },
  { key: "abc_orders", label: "ABC заказов", type: "text" },
  { key: "sales_share_pct", label: "Доля продаж, %", type: "number" },
  { key: "sales_cumulative_pct", label: "Накоп. доля продаж, %", type: "number" },
  { key: "abc_sales", label: "ABC продаж", type: "text" },
  { key: "stock_share_pct", label: "Доля остатков, %", type: "number" },
  { key: "stock_cumulative_pct", label: "Накоп. доля остатков, %", type: "number" },
  { key: "abc_stock", label: "ABC остатков", type: "text" },
];

const fixedTableColumnOrders = {
  abc: [
    "category_name",
    "sku_count",
    "abc_combined",
    "zakazano_rub",
    "avg_price_rub",
    "zakazano_sht",
    "total_stock_qty",
    "category_attribute_count",
    "orders_share_pct",
    "orders_cumulative_pct",
    "abc_orders",
    "sales_share_pct",
    "sales_cumulative_pct",
    "abc_sales",
    "stock_share_pct",
    "stock_cumulative_pct",
    "abc_stock",
  ],
  product: [
    "category_name",
    "sku_wb",
    "sku_ozon",
    "naimenovanie",
    "abc_combined",
    "zakazano_rub",
    "avg_price_rub",
    "zakazano_sht",
    "total_stock_qty",
    "priority_collection",
    "seo_status",
    "adv_impressions",
    "adv_clicks",
    "adv_sales_rub",
    "adv_acos_pct",
    "adv_tacos_pct",
    "orders_share_pct",
    "orders_cumulative_pct",
    "abc_orders",
    "sales_share_pct",
    "sales_cumulative_pct",
    "abc_sales",
    "stock_share_pct",
    "stock_cumulative_pct",
    "abc_stock",
    "reyting_kartochki",
    "reyting_po_otzyvam",
  ],
};

const mappingFilterIds = ["gj_model", "assortment_bia", "tg", "tg_plus", "cg", "season"];
const ozonProductAttributeFilterIds = [
  "ozon_gender",
  "ozon_collection",
  "ozon_season",
  "ozon_style",
  "ozon_color",
  "ozon_material",
  "ozon_material_composition",
  "ozon_russian_size",
  "ozon_manufacturer_size",
  "ozon_target_audience",
];
const sportmasterFilterIds = ["sm_subcategory", "sm_brand", "sm_model", "sm_gender", "sm_age", "sm_collection", "sm_season", "sm_sport"];
const boironBrandFilterIds = ["boiron_brand"];
const wbEntranceFilterIds = ["wb_entrance_section", "wb_entrance_point"];
const wbQueryClassificationFilterIds = [
  "query_brand_class",
  "query_brand_name",
  "query_type",
  "query_audience",
  "query_scenario",
  "query_specificity",
  "frequency_tier",
];
const COLLECTION_NO_TAG = "__no_tag__";
const COLLECTION_PRIORITY = "priority_collection";
const PRIORITY_COLLECTION_SOURCE_TAG = "GJ лето 2026";
const PRIORITY_COLLECTION_DISPLAY_TAG = "Glory Jeans Лето 2026";
const BACK_TO_SCHOOL_COLLECTION_TAG = "школа";
const NEAR_SCHOOL_COLLECTION_TAG = "околошкола";
const collectionStatusLabels = {
  [COLLECTION_PRIORITY]: PRIORITY_COLLECTION_DISPLAY_TAG,
  [PRIORITY_COLLECTION_SOURCE_TAG]: PRIORITY_COLLECTION_DISPLAY_TAG,
  [BACK_TO_SCHOOL_COLLECTION_TAG]: BACK_TO_SCHOOL_COLLECTION_TAG,
  [NEAR_SCHOOL_COLLECTION_TAG]: NEAR_SCHOOL_COLLECTION_TAG,
  [COLLECTION_NO_TAG]: "Без тега",
};
const SEO_STATUS_NO_TAG = "__no_tag__";
const wbPromotionAdvertStatusOptions = [
  { value: "-1", label: "-1 · удалена" },
  { value: "4", label: "4 · готова" },
  { value: "7", label: "7 · завершена" },
  { value: "8", label: "8 · отменена" },
  { value: "9", label: "9 · активна" },
  { value: "11", label: "11 · пауза" },
];
const WB_PROMOTION_ADVERTS_DEFAULT_STATUSES = ["9", "11", "7"];

function renderWbApiStopButton(methodKey) {
  return `
    <button type="button" class="ghost wb-api-stop-button" data-stop-wb-api="${escapeHtml(methodKey)}" title="Остановить скрипт" aria-label="Остановить скрипт" disabled>
      <span aria-hidden="true">&#9632;</span>
    </button>
  `;
}

function renderApiExportLogButton(methodKey, logPath = "") {
  return `
    <button type="button" class="ghost" data-open-api-export-log="${escapeHtml(methodKey)}" ${logPath ? "" : "disabled"}>Лог</button>
  `;
}

const mediaAdvFilterIds = ["media_status", "media_segment", "media_campaign_id", "media_group_id", "media_creative_id"];
const advCampaignFilterIds = ["adv_campaign_id"];
const filterIds = ["marketplace", "category", "category_level", "media_level", ...mediaAdvFilterIds, ...advCampaignFilterIds, ...mappingFilterIds, ...ozonProductAttributeFilterIds, ...sportmasterFilterIds, ...boironBrandFilterIds, ...wbQueryClassificationFilterIds, ...wbEntranceFilterIds, "collection_status", "seo_status", "abc_orders", "abc_sales", "abc_stock", "abc_combined", "sort", "limit", "date_from", "date_to", "period_group", "article", "product"];
const dataFilterIds = ["category", "category_level", "media_level", ...mediaAdvFilterIds, ...advCampaignFilterIds, ...mappingFilterIds, ...ozonProductAttributeFilterIds, ...sportmasterFilterIds, ...boironBrandFilterIds, ...wbQueryClassificationFilterIds, ...wbEntranceFilterIds, "collection_status", "seo_status", "abc_orders", "abc_sales", "abc_stock", "abc_combined", "limit", "date_from", "date_to", "article", "product"];
const productContextDashboards = ["abc", "product", "sku", "adv", "funnel", "weeklyDynamics", "inventoryHistory", "seoMonitoring", "wbSearchQueries", "wbEntrance"];
const assortmentDashboards = ["abc", "product", "sku", "adv", "funnel", "weeklyDynamics", "inventoryHistory", "seoMonitoring", "wbSearchQueries", "wbEntrance"];
const productNameDashboards = [...productContextDashboards, "mediaAdv"];
const articleDashboards = [...productContextDashboards, "mediaAdv"];
const abcControlDashboards = ["abc", "product", "sku"];
const dateRangeDashboards = ["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics", "inventoryHistory", "planfact", "seoMonitoring", "wbSearchQueries", "wbEntrance"];
const avitoDashboards = new Set(["avitoOverview", "avitoCampaigns", "avitoGroups", "avitoCreatives", "avitoDaily"]);
const avitoDashboardTitles = {
  avitoOverview: "Avito Ads · Обзор",
  avitoCampaigns: "Avito Ads · Кампании",
  avitoGroups: "Avito Ads · Группы",
  avitoCreatives: "Avito Ads · Креативы",
  avitoDaily: "Avito Ads · Статистика по дням",
};
dateRangeDashboards.push(...avitoDashboards);
const yandexDashboards = new Set(["yandexOverview", "yandexFunnel", "yandexFinance", "yandexPromotion", "yandexInventory"]);
const yandexDashboardTitles = {
  yandexOverview: "Яндекс Маркет · Обзор",
  yandexFunnel: "Яндекс Маркет · Воронка",
  yandexFinance: "Яндекс Маркет · Финансы",
  yandexPromotion: "Яндекс Маркет · Продвижение",
  yandexInventory: "Яндекс Маркет · Остатки",
};
const yandexSelectedStoreByClient = {};
dateRangeDashboards.push(...yandexDashboards);
const filterGroupOrder = {
  core: 10,
  assortment: 30,
  merch: 50,
  query_semantics: 60,
  period: 70,
  analytics: 90,
  actions: 110,
};
const filterGroupTitles = {
  core: "Товар и маркетплейс",
  assortment: "Ассортимент",
  merch: "Коллекции и SEO",
  query_semantics: "Семантика запроса",
  period: "Период",
  analytics: "Показатели",
  actions: "Действия",
};
const STORAGE_KEY = "kokoc_bi_dashboard_state_v1";
const DEFAULT_RECENT_DAYS = 28;
let productOptionsRequestId = 0;
let datePickerLeftMonth = null;
let draftDateFrom = "";
let draftDateTo = "";
let draftDateClickStep = 0;
let lastWbSearchDailyRows = [];
let lastFunnelDailyRows = [];
let lastInventoryHistoryDailyRows = [];
let lastInventoryHistoryProductsPayload = {};
let lastWeeklyDynamicsRankings = {};
let lastAdvDailyRows = [];
let lastAdvWaterfalls = {};
let lastMediaAdvDailyRows = [];
let lastMediaAdvWaterfalls = {};
let lastPlanFactDailyRows = [];
let lastPlanFactMonthlyRows = [];
let lastPlanFactScorecardRows = [];
let lastAdminPayload = null;

function adminAccessEnabled() {
  return !state.clientLocked;
}

const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

const funnelMetricConfigs = [
  { key: "impressions_total", label: "Показы всего", type: "number", color: "#1f9fe5" },
  { key: "impressions_search_catalog", label: "Показы поиск/каталог", type: "number", color: "#147fc2" },
  { key: "impressions_card", label: "Показы карточки", type: "number", color: "#2563eb" },
  { key: "sessions_total", label: "Сессии всего", type: "number", color: "#0f766e" },
  { key: "sessions_search_catalog", label: "Сессии поиск/каталог", type: "number", color: "#14b8a6" },
  { key: "sessions_card", label: "Сессии карточки", type: "number", color: "#0d9488" },
  { key: "card_visits", label: "Переходы в карточку", type: "number", color: "#008c8c" },
  { key: "cart_adds", label: "Корзины", type: "number", color: "#d97706" },
  { key: "cart_adds_search_catalog", label: "Корзины поиск/каталог", type: "number", color: "#f59e0b" },
  { key: "cart_adds_card", label: "Корзины карточки", type: "number", color: "#ea580c" },
  { key: "returned_units", label: "Возвращено, шт", type: "number", color: "#e11d48" },
  { key: "delivered_units", label: "Доставлено, шт", type: "number", color: "#16a34a" },
  { key: "ordered_units", label: "Заказы, шт", type: "number", color: "#b7791f" },
  { key: "ordered_amount_rub", label: "Заказы, руб", type: "number", color: "#5b6ee1" },
  { key: "cohort_bought_units", label: "Выкуплено по дате заказа, шт", type: "number", color: "#15803d" },
  { key: "cohort_bought_amount_rub", label: "Выкуплено по дате заказа, руб", type: "number", color: "#15803d" },
  { key: "bought_units", label: "Выкуплено, шт", type: "number", color: "#16a34a" },
  { key: "bought_amount_rub", label: "Выкупы минус возвраты, руб", type: "number", color: "#0f766e" },
  { key: "favorites_adds", label: "Добавили в отложенные", type: "number", color: "#84cc16" },
  { key: "cancelled_units", label: "Отменено, шт", type: "number", color: "#f97316" },
  { key: "cancelled_amount_rub", label: "Отменено, руб", type: "number", color: "#dc2626" },
  { key: "wb_club_ordered_units", label: "Заказы WB Клуб, шт", type: "number", color: "#a855f7" },
  { key: "wb_club_bought_units", label: "Выкуплено WB Клуб, шт", type: "number", color: "#7e22ce" },
  { key: "wb_club_ordered_amount_rub", label: "Заказы WB Клуб, руб", type: "number", color: "#c026d3" },
  { key: "wb_club_bought_amount_rub", label: "Выкуплено WB Клуб, руб", type: "number", color: "#86198f" },
  { key: "favorite_to_card_visit_pct", label: "Карточка -> отложенные, %", type: "pct", color: "#4d7c0f", digits: 2, suffix: "%" },
  { key: "buyout_pct", label: "Заказ -> выкуп, %", type: "pct", color: "#15803d", digits: 2, suffix: "%" },
  { key: "cancellation_pct", label: "Заказ -> отмена, %", type: "pct", color: "#c2410c", digits: 2, suffix: "%" },
  { key: "wb_club_order_share_pct", label: "Доля заказов WB Клуб, %", type: "pct", color: "#9333ea", digits: 2, suffix: "%" },
  { key: "adv_impressions", label: "Рекламные показы", type: "number", color: "#38bdf8" },
  { key: "adv_clicks", label: "Рекламные клики", type: "number", color: "#0891b2" },
  { key: "adv_cart_adds", label: "Рекламные корзины", type: "number", color: "#f59e0b" },
  { key: "adv_orders", label: "Рекламные заказы", type: "number", color: "#92400e" },
  { key: "adv_expense_rub", label: "Расходы на рекламу", type: "number", color: "#ef4444" },
  { key: "adv_ctr_pct", label: "Рекламный CTR, %", type: "pct", color: "#0369a1", digits: 2, suffix: "%" },
  { key: "adv_click_to_cart_pct", label: "Реклама: клик -> корзина, %", type: "pct", color: "#b45309", digits: 2, suffix: "%" },
  { key: "adv_cart_to_order_pct", label: "Реклама: корзина -> заказ, %", type: "pct", color: "#9f1239", digits: 2, suffix: "%" },
  { key: "adv_click_to_order_pct", label: "Реклама: клик -> заказ, %", type: "pct", color: "#6b21a8", digits: 2, suffix: "%" },
  { key: "adv_cpc_rub", label: "Рекламный CPC, руб", type: "number", color: "#475569", digits: 2 },
  { key: "adv_cpa_rub", label: "Рекламный CPA, руб", type: "number", color: "#64748b", digits: 2 },
  { key: "adv_cpm_rub", label: "Рекламный CPM, руб", type: "number", color: "#334155", digits: 2 },
  { key: "organic_impressions", label: "Органические показы", type: "number", color: "#22c55e" },
  { key: "organic_card_visits", label: "Органические переходы", type: "number", color: "#16a34a" },
  { key: "organic_cart_adds", label: "Органические корзины", type: "number", color: "#65a30d" },
  { key: "organic_orders", label: "Органические заказы", type: "number", color: "#15803d" },
  { key: "search_to_card_visit_pct", label: "Поиск -> карточка, %", type: "pct", color: "#0f766e", digits: 2, suffix: "%" },
  { key: "total_impression_to_card_visit_pct", label: "Показы -> карточка, %", type: "pct", color: "#0e7490", digits: 2, suffix: "%" },
  { key: "card_visit_to_cart_pct", label: "Карточка -> корзина, %", type: "pct", color: "#dc2626", digits: 2, suffix: "%" },
  { key: "cart_to_order_pct", label: "Корзина -> заказ, %", type: "pct", color: "#7c3aed", digits: 2, suffix: "%" },
  { key: "card_visit_to_order_pct", label: "Карточка -> заказ, %", type: "pct", color: "#be185d", digits: 2, suffix: "%" },
  { key: "ordered_amount_per_unit_rub", label: "Средний заказ, руб/шт", type: "number", color: "#475569", digits: 2 },
  { key: "acos_pct", label: "ACOS, %", type: "pct", color: "#ea580c", digits: 2, suffix: "%" },
  { key: "tacos_pct", label: "TACOS, %", type: "pct", color: "#9333ea", digits: 2, suffix: "%" },
];

const wbOnlyFunnelMetricKeys = new Set([
  "bought_units", "bought_amount_rub", "cohort_bought_units", "cohort_bought_amount_rub", "favorites_adds", "cancelled_amount_rub",
  "wb_club_ordered_units", "wb_club_bought_units", "wb_club_ordered_amount_rub", "wb_club_bought_amount_rub",
  "favorite_to_card_visit_pct", "buyout_pct", "wb_club_order_share_pct",
]);

const ozonOnlyFunnelMetricKeys = new Set([
  "impressions_card", "sessions_total", "sessions_search_catalog", "sessions_card",
  "cart_adds_search_catalog", "cart_adds_card", "returned_units", "delivered_units",
]);

const funnelMetricKeysByMarketplace = {
  ozon: funnelMetricConfigs.map((metric) => metric.key).filter((key) => !wbOnlyFunnelMetricKeys.has(key)),
  wb: funnelMetricConfigs.map((metric) => metric.key).filter((key) => !ozonOnlyFunnelMetricKeys.has(key)),
};

const funnelDefaultMetricsByMarketplace = {
  ozon: ["impressions_total", "adv_impressions", "organic_impressions", "card_visit_to_order_pct"],
  wb: ["impressions_total", "card_visits", "ordered_units", "bought_units", "adv_expense_rub", "card_visit_to_order_pct"],
};

const inventoryHistoryFunnelMetricKeys = new Set([
  "impressions_total", "card_visits", "cart_adds", "ordered_units",
  "adv_impressions", "adv_clicks", "adv_cart_adds", "adv_orders",
  "organic_impressions", "organic_card_visits", "organic_cart_adds", "organic_orders",
]);

const inventoryHistoryMetricConfigs = [
  ...funnelMetricConfigs.filter((metric) => inventoryHistoryFunnelMetricKeys.has(metric.key)),
  { key: "stock_available_qty", label: "Остаток на дату", type: "number", color: "#2563eb" },
  { key: "stock_inflow_qty", label: "Рост остатка к предыдущему снимку", type: "number", color: "#16a34a" },
  { key: "stock_outflow_qty", label: "Снижение остатка к предыдущему снимку", type: "number", color: "#dc2626" },
  { key: "stock_preparing_qty", label: "Готовится к продаже", type: "number", color: "#0ea5e9" },
  { key: "stock_reserved_qty", label: "Зарезервировано", type: "number", color: "#7c3aed" },
  { key: "in_supply_orders_qty", label: "В заявках на поставку", type: "number", color: "#0891b2" },
  { key: "in_transit_supply_qty", label: "Товар в пути", type: "number", color: "#f59e0b" },
  { key: "to_customer_qty", label: "В пути к клиенту", type: "number", color: "#d97706" },
  { key: "from_customer_qty", label: "В пути от клиента", type: "number", color: "#c2410c" },
  { key: "returning_from_customers_qty", label: "Возвраты от клиентов", type: "number", color: "#a855f7" },
  { key: "checking_qty", label: "На проверке", type: "number", color: "#64748b" },
  { key: "defective_qty", label: "Брак", type: "number", color: "#be123c" },
  { key: "lost_orders_qty", label: "Потерянные заказы", type: "number", color: "#e11d48" },
  { key: "turnover_days", label: "Оборачиваемость, дней", type: "number", color: "#475569", digits: 1 },
  { key: "days_to_stockout", label: "Запаса, дней", type: "number", color: "#334155", digits: 1 },
];

const inventoryHistoryDefaultMetrics = ["stock_available_qty", "ordered_units", "adv_orders", "organic_orders"];

const funnelMetricDependencies = {
  search_to_card_visit_pct: ["impressions_search_catalog", "card_visits"],
  total_impression_to_card_visit_pct: ["impressions_total", "card_visits"],
  card_visit_to_cart_pct: ["card_visits", "cart_adds"],
  cart_to_order_pct: ["cart_adds", "ordered_units"],
  card_visit_to_order_pct: ["card_visits", "ordered_units"],
  ordered_amount_per_unit_rub: ["ordered_units", "ordered_amount_rub"],
  favorite_to_card_visit_pct: ["favorites_adds", "card_visits"],
  buyout_pct: ["cohort_bought_units", "ordered_units"],
  cancellation_pct: ["cancelled_units", "ordered_units"],
  wb_club_order_share_pct: ["wb_club_ordered_units", "ordered_units"],
  adv_ctr_pct: ["adv_impressions", "adv_clicks"],
  adv_click_to_cart_pct: ["adv_clicks", "adv_cart_adds"],
  adv_cart_to_order_pct: ["adv_cart_adds", "adv_orders"],
  adv_click_to_order_pct: ["adv_clicks", "adv_orders"],
  adv_cpc_rub: ["adv_expense_rub", "adv_clicks"],
  adv_cpa_rub: ["adv_expense_rub", "adv_orders"],
  adv_cpm_rub: ["adv_expense_rub", "adv_impressions"],
  organic_impressions: ["impressions_total", "adv_impressions"],
  organic_card_visits: ["card_visits", "adv_clicks"],
  organic_cart_adds: ["cart_adds", "adv_cart_adds"],
  organic_orders: ["ordered_units", "adv_orders"],
  acos_pct: ["adv_expense_rub", "adv_orders_amount_rub"],
  tacos_pct: ["adv_expense_rub", "ordered_amount_rub"],
};

function updateFunnelAvailableMetrics(summary) {
  const allowed = new Set(funnelMetricKeysByMarketplace[currentMarketplace()] || funnelMetricKeysByMarketplace.ozon);
  const hasData = (key) => Math.abs(Number(summary?.[key] || 0)) > 0;
  state.funnelAvailableMetrics = funnelMetricConfigs
    .filter((metric) => allowed.has(metric.key))
    .filter((metric) => (funnelMetricDependencies[metric.key] || [metric.key]).every(hasData))
    .map((metric) => metric.key);
}

function funnelMetricIsAvailable(key) {
  return !Array.isArray(state.funnelAvailableMetrics) || state.funnelAvailableMetrics.includes(key);
}

const advMetricConfigs = [
  { key: "orders_amount_rub", label: "Продажи с рекламы", type: "number", color: "#1f9fe5" },
  { key: "direct_orders_amount_rub", label: "Продажи — прямая атрибуция", type: "number", color: "#2563eb" },
  { key: "indirect_orders_amount_rub", label: "Продажи — косвенная атрибуция", type: "number", color: "#8b5cf6" },
  { key: "expense_rub", label: "Расходы", type: "number", color: "#0f766e" },
  { key: "orders_qty", label: "Заказы с рекламы", type: "number", color: "#16a34a" },
  { key: "direct_orders_qty", label: "Заказы — прямая атрибуция", type: "number", color: "#15803d" },
  { key: "indirect_orders_qty", label: "Заказы — косвенная атрибуция", type: "number", color: "#65a30d" },
  { key: "total_orders_qty", label: "Заказы итого", type: "number", color: "#22c55e" },
  { key: "total_orders_amount_rub", label: "Продажи итого", type: "number", color: "#5b6ee1" },
  { key: "impressions", label: "Показы", type: "number", color: "#38bdf8" },
  { key: "promoted_sku_count", label: "Товаров в продвижении", type: "number", color: "#14b8a6" },
  { key: "ordered_sku_count", label: "Товаров с заказами", type: "number", color: "#8b5cf6" },
  { key: "total_sku_count", label: "Товаров итого", type: "number", color: "#2563eb" },
  { key: "clicks", label: "Клики", type: "number", color: "#0891b2" },
  { key: "added_to_cart", label: "Корзины", type: "number", color: "#f59e0b" },
  { key: "adv_orders_to_total_orders_pct", label: "Рекл. заказы / итого", type: "pct", color: "#0e7490", digits: 2, suffix: "%" },
  { key: "drr_pct", label: "ДРР рекламный", type: "pct", color: "#d97706", digits: 2, suffix: "%" },
  { key: "direct_drr_pct", label: "ДРР — прямая атрибуция", type: "pct", color: "#ea580c", digits: 2, suffix: "%" },
  { key: "indirect_drr_pct", label: "ДРР — косвенная атрибуция", type: "pct", color: "#c2410c", digits: 2, suffix: "%" },
  { key: "total_drr_pct", label: "ДРР общий", type: "pct", color: "#b7791f", digits: 2, suffix: "%" },
  { key: "ctr_calc_pct", label: "CTR, %", type: "pct", color: "#0f766e", digits: 2, suffix: "%" },
  { key: "click_to_cart_pct", label: "Клик -> корзина, %", type: "pct", color: "#7c3aed", digits: 2, suffix: "%" },
  { key: "cart_to_order_pct", label: "Корзина -> заказ, %", type: "pct", color: "#be185d", digits: 2, suffix: "%" },
  { key: "click_to_order_pct", label: "Клик -> заказ, %", type: "pct", color: "#9333ea", digits: 2, suffix: "%" },
  { key: "cpc_calc_rub", label: "CPC, руб", type: "number", color: "#475569", digits: 2 },
  { key: "cpa_calc_rub", label: "CPA, руб", type: "number", color: "#64748b", digits: 2 },
  { key: "cpm_calc_rub", label: "CPM, руб", type: "number", color: "#334155", digits: 2 },
];

const mediaAdvMetricConfigs = [
  { key: "attributed_revenue_rub", label: "Атриб. выручка", type: "number", color: "#1f9fe5" },
  { key: "post_view_revenue_rub", label: "Post-view выручка", type: "number", color: "#0f766e" },
  { key: "orders_amount_rub", label: "Прямая выручка", type: "number", color: "#5b6ee1" },
  { key: "expense_rub", label: "Расходы", type: "number", color: "#d97706" },
  { key: "impressions", label: "Показы", type: "number", color: "#38bdf8" },
  { key: "clicks", label: "Клики", type: "number", color: "#0891b2" },
  { key: "attributed_orders_qty", label: "Атриб. заказы", type: "number", color: "#16a34a" },
  { key: "post_view_orders_qty", label: "Post-view заказы", type: "number", color: "#22c55e" },
  { key: "orders_qty", label: "Прямые заказы", type: "number", color: "#92400e" },
  { key: "ctr_calc_pct", label: "CTR, %", type: "pct", color: "#0e7490", digits: 2, suffix: "%" },
  { key: "click_to_order_pct", label: "Клик -> заказ, %", type: "pct", color: "#7c3aed", digits: 2, suffix: "%" },
  { key: "drr_direct_pct", label: "ДРР прямой, %", type: "pct", color: "#ea580c", digits: 2, suffix: "%" },
  { key: "drr_attributed_pct", label: "ДРР post-view, %", type: "pct", color: "#b7791f", digits: 2, suffix: "%" },
  { key: "attributed_roas", label: "ROAS post-view", type: "number", color: "#be185d", digits: 2 },
  { key: "post_view_revenue_share_pct", label: "Доля post-view, %", type: "pct", color: "#9333ea", digits: 2, suffix: "%" },
  { key: "cpc_calc_rub", label: "CPC, руб", type: "number", color: "#475569", digits: 2 },
  { key: "cpm_calc_rub", label: "CPM, руб", type: "number", color: "#64748b", digits: 2 },
];

const planfactChartMetrics = [
  { key: "orders_rub", label: "Заказы", type: "number", color: "#2b7fff" },
  { key: "sales_rub", label: "Факт продаж", type: "number", color: "#ff4747" },
  { key: "sales_plan_daily_rub", label: "План продаж день", type: "number", color: "#a3a3a3" },
  { key: "sales_cum_rub", label: "Факт продаж накоп.", type: "number", color: "#e03535" },
  { key: "sales_plan_elapsed_rub", label: "План продаж накоп.", type: "number", color: "#d4d4d4" },
  { key: "ad_spend_rub", label: "Расходы", type: "number", color: "#d61f3c" },
  { key: "ad_spend_plan_daily_rub", label: "Бюджет день", type: "number", color: "#a3a3a3" },
  { key: "ad_spend_cum_rub", label: "Расход накоп.", type: "number", color: "#b82626" },
  { key: "ad_spend_plan_elapsed_rub", label: "Бюджет накоп.", type: "number", color: "#d4d4d4" },
  { key: "sales_month_plan_fact_pct", label: "Продажи план/факт, %", type: "pct", color: "#049a6b", digits: 2, suffix: "%" },
  { key: "sales_elapsed_plan_fact_pct", label: "Продажи RR, %", type: "pct", color: "#22c55e", digits: 2, suffix: "%" },
  { key: "ad_spend_budget_used_pct", label: "Расходы план/факт, %", type: "pct", color: "#dc2626", digits: 2, suffix: "%" },
  { key: "ad_spend_elapsed_budget_pct", label: "Расходы RR, %", type: "pct", color: "#ef4444", digits: 2, suffix: "%" },
  { key: "tacos_pct", label: "TACOS, %", type: "pct", color: "#9333ea", digits: 2, suffix: "%" },
  { key: "tacos_cum_pct", label: "TACOS накоп., %", type: "pct", color: "#7c3aed", digits: 2, suffix: "%" },
];

const planfactRevenueMetricKeys = new Set([
  "orders_rub",
  "sales_rub",
  "ad_spend_rub",
  "sales_plan_daily_rub",
  "sales_cum_rub",
  "sales_plan_elapsed_rub",
  "sales_month_plan_fact_pct",
  "sales_elapsed_plan_fact_pct",
]);

const planfactExpenseMetricKeys = new Set([
  "ad_spend_rub",
  "ad_spend_plan_daily_rub",
  "ad_spend_cum_rub",
  "ad_spend_plan_elapsed_rub",
  "ad_spend_budget_used_pct",
  "ad_spend_elapsed_budget_pct",
  "tacos_pct",
  "tacos_cum_pct",
]);

const planfactRevenueMetrics = planfactChartMetrics.filter((item) => planfactRevenueMetricKeys.has(item.key));
const planfactExpenseMetrics = planfactChartMetrics.filter((item) => planfactExpenseMetricKeys.has(item.key));

function qs(id) {
  return document.getElementById(id);
}

function setAdminLoginMessage(message = "", isError = false) {
  const target = qs("adminLoginMessage");
  if (!target) return;
  target.textContent = message;
  target.classList.toggle("is-error", Boolean(isError && message));
}

function showAdminLogin(message = "") {
  const modal = qs("adminLoginModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  document.body.classList.add("admin-auth-open");
  setAdminLoginMessage(message);
  window.setTimeout(() => {
    const username = qs("adminLoginUsername");
    const password = qs("adminLoginPassword");
    (username?.value ? password : username)?.focus();
  }, 0);
}

function hideAdminLogin() {
  qs("adminLoginModal")?.classList.add("hidden");
  document.body.classList.remove("admin-auth-open");
  const password = qs("adminLoginPassword");
  if (password) password.value = "";
  setAdminLoginMessage("");
}

async function checkAdminAuth() {
  try {
    const payload = await getJson("/api/admin/auth/status");
    state.adminAuthenticated = Boolean(payload.authenticated);
    state.adminAuthConfigured = Boolean(payload.configured);
    // Legacy auth/status payload from a not-yet-restarted backend has no section fields.
    const hasSectionPayload = Object.prototype.hasOwnProperty.call(payload, "full_admin")
      || Array.isArray(payload.allowed_admin_sections);
    state.adminFullAccess = Boolean(payload.full_admin || (state.adminAuthenticated && !hasSectionPayload));
    state.adminAllowedSections = state.adminFullAccess
      ? adminSectionKeys()
      : (Array.isArray(payload.allowed_admin_sections) ? payload.allowed_admin_sections : []);
    return state.adminAuthenticated;
  } catch (error) {
    state.adminAuthenticated = false;
    state.adminAuthConfigured = null;
    state.adminAllowedSections = [];
    state.adminFullAccess = false;
    return false;
  }
}

function enterAdminDashboard() {
  state.dashboard = "admin";
  state.client = "";
  renderClientSelector();
  state.drillCategory = "";
  state.page = 1;
  resetColumnFilters();
  loadData().catch((error) => {
    setStatusError(error);
  });
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const submitButton = qs("adminLoginSubmit");
  const username = qs("adminLoginUsername")?.value || "";
  const password = qs("adminLoginPassword")?.value || "";
  if (submitButton) submitButton.disabled = true;
  setAdminLoginMessage("Проверяем данные...");
  try {
    await postJson("/api/admin/auth/login", { username, password });
    const openDashboard = state.adminLoginRequested;
    state.adminAuthenticated = true;
    state.adminAuthConfigured = true;
    state.adminLoginRequested = false;
    hideAdminLogin();
    if (openDashboard) enterAdminDashboard();
  } catch (error) {
    state.adminAuthenticated = false;
    setAdminLoginMessage(error.message || "Не удалось войти", true);
    qs("adminLoginPassword")?.select();
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

async function logoutAdmin() {
  try {
    await postJson("/api/admin/auth/logout", {});
  } finally {
    state.adminAuthenticated = false;
    state.adminLoginRequested = false;
    lastAdminPayload = null;
    openAbcDashboard();
  }
}

function cssEscape(value) {
  if (typeof window !== "undefined" && window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || value === "") return "";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(num);
}

function formatCompactNumber(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  const abs = Math.abs(num);
  if (abs >= 1000000000) return `${formatNumber(num / 1000000000, digits)} млрд`;
  if (abs >= 1000000) return `${formatNumber(num / 1000000, digits)} млн`;
  if (abs >= 10000) return `${formatNumber(num / 1000, digits)} тыс`;
  return formatNumber(num);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${formatNumber(size, unitIndex > 1 ? 1 : 0)} ${units[unitIndex]}`;
}

function formatAxisValue(value, digits = 0) {
  const num = Number(value || 0);
  if (Math.abs(num) >= 1000000) return `${formatNumber(num / 1000000, 1)} млн`;
  if (Math.abs(num) >= 1000) return `${formatNumber(num / 1000, digits ? 1 : 0)} тыс`;
  return formatNumber(num, digits);
}

function toIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftDate(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00`);
  date.setDate(date.getDate() + days);
  return toIsoDate(date);
}

function isoDateRange(from, to) {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  if (!start || !end || start > end) return [];
  const out = [];
  const current = new Date(start);
  while (current <= end) {
    out.push(toIsoDate(current));
    current.setDate(current.getDate() + 1);
  }
  return out;
}

function parseIsoDate(isoDate) {
  if (!isoDate) return null;
  const date = new Date(`${isoDate}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRuDate(isoDate) {
  const date = parseIsoDate(isoDate);
  return date ? date.toLocaleDateString("ru-RU") : "";
}

function formatMonthName(isoDate) {
  const date = parseIsoDate(isoDate);
  if (!date) return "";
  return date.toLocaleDateString("ru-RU", { month: "long", year: "numeric" }).replace(/^./, (char) => char.toUpperCase());
}

function monthEndIso(isoDate) {
  const date = parseIsoDate(isoDate);
  if (!date) return "";
  return toIsoDate(new Date(date.getFullYear(), date.getMonth() + 1, 0));
}

function formatShortDate(isoDate) {
  if (!isoDate) return "";
  const value = String(isoDate);
  if (/^\d{4}-\d{2}$/.test(value)) return `${value.slice(5, 7)}.${value.slice(2, 4)}`;
  const date = parseIsoDate(value);
  return date ? `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}` : value;
}

function formatWeeklyDynamicsPeriodLabel(isoDate) {
  if (state.dashboard !== "weeklyDynamics" || periodGroupMode() !== "week") return formatShortDate(isoDate);
  const start = parseIsoDate(isoDate);
  if (!start) return formatShortDate(isoDate);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return `${formatShortDate(isoDate)}–${formatShortDate(toIsoDate(end))}`;
}

function periodGroupMode() {
  const value = qs("period_group")?.value || "day";
  return ["day", "week", "month"].includes(value) ? value : "day";
}

function periodGroupLabel() {
  return { day: "дням", week: "неделям", month: "месяцам" }[periodGroupMode()] || "дням";
}

function periodBucketKey(rawDate, mode = periodGroupMode()) {
  const value = String(rawDate || "");
  if (mode === "day") return value;
  const date = parseIsoDate(value);
  if (!date) return value;
  if (mode === "month") {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  }
  const monday = new Date(date);
  monday.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  return toIsoDate(monday);
}

function aggregateRowsForPeriod(rows, summedFields, maxFields = []) {
  const mode = periodGroupMode();
  if (mode === "day") return rows;
  const buckets = new Map();
  rows.forEach((row) => {
    const key = periodBucketKey(row.report_date, mode);
    if (!key) return;
    const bucket = buckets.get(key) || { report_date: key };
    summedFields.forEach((field) => {
      bucket[field] = Number(bucket[field] || 0) + Number(row[field] || 0);
    });
    maxFields.forEach((field) => {
      bucket[field] = Math.max(Number(bucket[field] || 0), Number(row[field] || 0));
    });
    buckets.set(key, bucket);
  });
  return [...buckets.values()].sort((a, b) => String(a.report_date).localeCompare(String(b.report_date)));
}

function updateDateRangeToggle() {
  const from = qs("date_from")?.value || "";
  const to = qs("date_to")?.value || "";
  if (state.dashboard === "planfact") {
    const picked = state.planfactMonths.find((item) => item.date_from === from && item.date_to === to)
      || state.planfactMonths.find((item) => to && to >= item.date_from && to <= item.date_to)
      || state.planfactMonths.find((item) => from && from >= item.date_from && from <= item.date_to)
      || state.planfactMonths.find((item) => item.month === `${to.slice(0, 7)}-01`)
      || state.planfactMonths.find((item) => item.month === `${from.slice(0, 7)}-01`);
    qs("dateRangeToggle").textContent = picked ? formatMonthName(picked.month) : "Выберите месяц";
    return;
  }
  qs("dateRangeToggle").textContent = from && to ? `${formatRuDate(from)} - ${formatRuDate(to)}` : "Выберите период";
}

function applyDefaultAdvDateRange(filters) {
  if (state.advDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.advDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultMediaAdvDateRange(filters) {
  if (state.mediaAdvDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.mediaAdvDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultAbcDateRange(filters) {
  if (state.abcDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.abcDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultSkuDateRange(filters) {
  if (state.skuDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.skuDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultFunnelDateRange(filters) {
  if (state.funnelDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.funnelDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultWeeklyDynamicsDateRange(filters) {
  if (state.weeklyDynamicsDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.weeklyDynamicsDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultSeoMonitoringDateRange(filters) {
  if (state.seoMonitoringDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.seoMonitoringDateRangeInitialized = true;
  updateDateRangeToggle();
}

function applyDefaultInventoryHistoryDateRange(filters) {
  if (state.inventoryHistoryDateRangeInitialized || !filters.date_to) return;
  const defaultFrom = defaultRecentDateFrom(filters.date_to);
  qs("date_to").value = filters.date_to;
  qs("date_from").value = filters.date_from && defaultFrom < filters.date_from ? filters.date_from : defaultFrom;
  state.inventoryHistoryDateRangeInitialized = true;
  updateDateRangeToggle();
}

function planFactSelectableMonths(months = []) {
  const historyStart = currentClient() === "gloria_jeans" ? "2026-04-01" : "";
  return months.filter((item) => {
    const from = parseIsoDate(item.date_from);
    const to = parseIsoDate(item.date_to);
    return from
      && to
      && (!historyStart || String(item.month || item.date_from || "") >= historyStart)
      && from.getFullYear() === to.getFullYear()
      && from.getMonth() === to.getMonth()
      && !String(item.label || "").toLowerCase().includes("год");
  });
}

function currentCalendarMonthStart(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return `${year}-${month}-01`;
}

function preferredPlanFactMonth(months = [], now = new Date()) {
  const selectable = planFactSelectableMonths(months);
  const currentMonth = currentCalendarMonthStart(now);
  return selectable.find((item) => item.month === currentMonth) || selectable.at(-1);
}

function planFactShouldUseDefaultMonth(urlParams, dashboard = state.dashboard) {
  return dashboard === "planfact"
    && !urlParams.has("date_from")
    && !urlParams.has("date_to");
}

function applyDefaultPlanFactDateRange(filters) {
  const months = planFactSelectableMonths(filters.months || state.planfactMonths || []);
  if (state.planfactDateRangeInitialized || !filters.date_to) {
    normalizePlanFactMonthRange(months);
    return;
  }
  const defaultMonth = preferredPlanFactMonth(months);
  qs("date_from").value = defaultMonth?.date_from || filters.date_from || filters.date_to;
  qs("date_to").value = defaultMonth?.date_to || filters.date_to;
  state.planfactDateRangeInitialized = true;
  updateDateRangeToggle();
}

function normalizePlanFactMonthRange(months) {
  const selectable = planFactSelectableMonths(months);
  if (state.dashboard !== "planfact" || !selectable.length) return;
  const from = qs("date_from").value || "";
  const to = qs("date_to").value || "";
  const month = selectable.find((item) => item.date_from === from && item.date_to === to)
    || preferredPlanFactMonth(selectable);
  if (!month) return;
  qs("date_from").value = month.date_from;
  qs("date_to").value = month.date_to;
  updateDateRangeToggle();
}
function numberDigits(key) {
  return key.includes("pct") || key.includes("share") || key.includes("cumulative") ? 2 : 0;
}

function currentMarketplace() {
  if (currentClient() === "boiron" && state.dashboard === "adv") return "ozon";
  return qs("marketplace")?.value || "ozon";
}

function clientMarketplaceIds(clientKey = currentClient()) {
  const client = state.clients.find((item) => item.key === clientKey);
  const marketplaces = Array.isArray(client?.marketplaces) ? client.marketplaces : [];
  const supported = marketplaces.filter((marketplace) => ["ozon", "wb"].includes(marketplace));
  if (supported.length) return supported;
  const hydrated = [...(qs("marketplace")?.options || [])]
    .map((option) => option.value)
    .filter((marketplace) => ["ozon", "wb"].includes(marketplace));
  return hydrated.length ? [...new Set(hydrated)] : ["ozon"];
}

function clientHasMarketplace(marketplace, clientKey = currentClient()) {
  const client = state.clients.find((item) => item.key === clientKey);
  return Array.isArray(client?.marketplaces) && client.marketplaces.includes(marketplace);
}

function isAvitoDashboard(dashboard = state.dashboard) {
  return avitoDashboards.has(dashboard);
}

function isYandexDashboard(dashboard = state.dashboard) {
  return yandexDashboards.has(dashboard);
}

function isAvitoOnlyClient(clientKey = currentClient()) {
  const client = state.clients.find((item) => item.key === clientKey);
  const marketplaces = new Set(client?.marketplaces || []);
  return marketplaces.size === 1 && marketplaces.has("avito");
}

function setMarketplaceValue(value) {
  qs("marketplace").value = value;
}

function currentClient() {
  const select = qs("clientSelect");
  if (!select) return state.client || "";
  const stateClient = state.client || "";
  const selectHasStateClient = Boolean(stateClient)
    && [...select.options].some((option) => option.value === stateClient);
  if (stateClient && !selectHasStateClient) return stateClient;
  return select.value || stateClient || "";
}

function sportmasterFiltersEnabled() {
  return currentClient() === "sportmaster" && ["abc", "product", "sku", "seoMonitoring", "wbSearchQueries", "wbEntrance"].includes(state.dashboard);
}

function boironBrandFiltersEnabled() {
  return currentClient() === "boiron" && state.dashboard === "adv";
}

function filterReportCapabilities() {
  const dashboard = state.dashboard;
  const hasProductContext = productContextDashboards.includes(dashboard);
  const isGloria = currentClient() === "gloria_jeans";
  const isBoiron = currentClient() === "boiron";
  const isWbMediaAdv = dashboard === "mediaAdv" && currentMarketplace() === "wb";
  const mediaLevel = qs("media_level")?.value || "campaign";
  return {
    category: (hasProductContext || isWbMediaAdv || ["wbSearchQueries", "wbEntrance"].includes(dashboard)) && !isBoiron,
    categoryLevel: ["abc", "product"].includes(dashboard),
    productName: productNameDashboards.includes(dashboard) && !(dashboard === "mediaAdv" && currentMarketplace() === "wb"),
    article: articleDashboards.includes(dashboard),
    dateRange: dateRangeDashboards.includes(dashboard),
    periodGroup: ["adv", "mediaAdv", "funnel", "weeklyDynamics"].includes(dashboard),
    assortment: isGloria && assortmentDashboards.includes(dashboard),
    ozonProductAttributes: currentMarketplace() === "ozon" && assortmentDashboards.includes(dashboard),
    sportmasterAssortment: sportmasterFiltersEnabled(),
    boironBrand: boironBrandFiltersEnabled(),
    collection: isGloria && hasProductContext,
    seo: isGloria && hasProductContext,
    abcClass: abcControlDashboards.includes(dashboard),
    sort: abcControlDashboards.includes(dashboard),
    limit: abcControlDashboards.includes(dashboard) || ["wbSearchQueries", "wbEntrance"].includes(dashboard),
    mediaLevel: dashboard === "mediaAdv" && currentMarketplace() === "wb",
    mediaFilters: dashboard === "mediaAdv" && currentMarketplace() === "wb",
    mediaGroupFilter: isWbMediaAdv && (mediaLevel === "group" || mediaLevel === "creative"),
    mediaCreativeFilter: isWbMediaAdv && mediaLevel === "creative",
    marketplaceLocked: (isBoiron && dashboard === "adv") || ["wbSearchQueries", "wbEntrance"].includes(dashboard),
    admin: dashboard === "admin",
  };
}

function categoryFilterEnabled() {
  return filterReportCapabilities().category;
}

function categoryLevelFilterEnabled() {
  return filterReportCapabilities().categoryLevel;
}

function productNameFilterEnabled() {
  return filterReportCapabilities().productName;
}

function articleFilterEnabled() {
  return filterReportCapabilities().article;
}

function gloriaAssortmentFiltersEnabled() {
  return filterReportCapabilities().assortment;
}

function ozonProductAttributeFiltersEnabled() {
  return filterReportCapabilities().ozonProductAttributes;
}

function abcClassificationFiltersEnabled() {
  return filterReportCapabilities().abcClass;
}

function clientLabel(clientKey = currentClient()) {
  if (!clientKey) return "Аккаунт не выбран";
  const client = state.clients.find((item) => item.key === clientKey);
  return client?.label || "TOPTOP";
}

function rememberNavigationEntitlements(clients) {
  (clients || []).forEach((client) => {
    if (client?.key && Array.isArray(client.reports)) {
      state.navigationReportsByClient[client.key] = [...client.reports];
    }
  });
}

function clientSupportsReport(report, clientKey = currentClient()) {
  if (isAvitoOnlyClient(clientKey) && !avitoDashboards.has(report)) return false;
  const stableReports = state.navigationReportsByClient[clientKey];
  if (Array.isArray(stableReports)) return stableReports.includes(report);
  const client = state.clients.find((item) => item.key === clientKey);
  return !client?.reports || client.reports.includes(report);
}

function firstSupportedDashboard(clientKey = currentClient()) {
  if (isAvitoOnlyClient(clientKey)) return "avitoOverview";
  const client = state.clients.find((item) => item.key === clientKey);
  const reports = client?.reports || [];
  return reports.includes("planfact") ? "planfact" : (reports.includes("avitoOverview") ? "avitoOverview" : (reports.includes("adv") ? "adv" : (reports[0] || "abc")));
}

function ensureDashboardSupportedForClient(clientKey = currentClient()) {
  if (state.dashboard === "admin") return;
  if (!clientSupportsReport(state.dashboard, clientKey)) {
    state.dashboard = firstSupportedDashboard(clientKey);
    resetClientScopedState();
  }
}

function renderClientSelector() {
  const select = qs("clientSelect");
  if (!select) return;
  const current = state.client || "";
  select.innerHTML = `<option value="" disabled>Выберите аккаунт</option>` + state.clients
    .map((client) => `<option value="${escapeHtml(client.key)}">${escapeHtml(client.label)}</option>`)
    .join("");
  const fallback = state.clients[0]?.key || "toptop";
  select.value = current === "" ? "" : (state.clients.some((client) => client.key === current) ? current : fallback);
  const switcher = select.closest(".client-switcher");
  switcher?.classList.toggle("hidden", state.clientLocked);
}

function hasExplicitDashboardContext() {
  const params = new URLSearchParams(window.location.search);
  return params.has("client") || params.has("dashboard");
}

function applyLockedClientUi() {
  const adminActive = state.dashboard === "admin";
  document.body.classList.toggle("client-locked", state.clientLocked);
  document.body.classList.toggle("admin-shell-active", adminActive);
  document.querySelector(".client-switcher")?.classList.toggle("hidden", state.clientLocked || adminActive);
  qs("navPinnedTitle")?.classList.toggle("hidden", state.clientLocked);
  qs("navClientReview")?.classList.add("hidden");
  qs("navPortfolio")?.classList.toggle("hidden", state.clientLocked);
  qs("navAdmin")?.classList.toggle("hidden", state.clientLocked || !adminAccessEnabled());
  if (!adminActive) clearAdminTopbarTabs();
}

function resetClientScopedState() {
  state.drillCategory = "";
  state.page = 1;
  state.columnFilters = {};
  state.categoryNames = [];
  state.selectedCategories = [];
  state.productNames = [];
  state.availableDateFrom = "";
  state.availableDateTo = "";
  state.planfactMonths = [];
  state.abcDateRangeInitialized = false;
  state.advDateRangeInitialized = false;
  state.mediaAdvDateRangeInitialized = false;
  state.skuDateRangeInitialized = false;
  state.funnelDateRangeInitialized = false;
  state.weeklyDynamicsDateRangeInitialized = false;
  state.inventoryHistoryDateRangeInitialized = false;
  state.seoMonitoringDateRangeInitialized = false;
  state.seoMonitoringSelectedProducts = [];
  state.seoMonitoringValuationNote = "";
  state.planfactDateRangeInitialized = false;
  if (qs("product")) qs("product").value = "";
  if (qs("productSearch")) qs("productSearch").value = "";
  if (qs("productToggle")) qs("productToggle").textContent = "Все товары";
  if (qs("article")) qs("article").value = "";
  if (qs("date_from")) qs("date_from").value = "";
  if (qs("date_to")) qs("date_to").value = "";
  setSelectedCollectionStatuses([]);
  setSelectedSeoStatuses([]);
  mappingFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
  ozonProductAttributeFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
  resetSportmasterFilters();
  resetBoironBrandFilters();
  resetMediaAdvFilters();
  clearAdvCampaignFilter();
  resetPanelChartMetricsToSort();
}

function resetSportmasterFilters() {
  sportmasterFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
    renderSportmasterFilterOptions(id);
    updateSportmasterFilterToggle(id);
  });
}

function resetBoironBrandFilters() {
  boironBrandFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
    renderBoironBrandFilterOptions(id);
    updateBoironBrandFilterToggle(id);
  });
}

function resetMediaAdvFilters() {
  mediaAdvFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
}

function syncPendingAdvCampaignFilter() {
  state.pendingAdvCampaignId = qs("adv_campaign_id")?.value || "";
}

function clearAdvCampaignFilter() {
  const select = qs("adv_campaign_id");
  if (select) select.value = "";
  state.pendingAdvCampaignId = "";
}

function normalizeMultiValuePayload(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  if (typeof value === "string" && value) {
    const trimmed = value.trim();
    if (trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) return parsed.map((item) => String(item).trim()).filter(Boolean);
      } catch (_error) {
        return [];
      }
    }
    return [trimmed];
  }
  return [];
}

function selectedSportmasterValues(id) {
  return normalizeMultiValuePayload(qs(id)?.value || "");
}

function selectedBoironBrandValues(id = "boiron_brand") {
  return normalizeMultiValuePayload(qs(id)?.value || "");
}

function normalizeWbPromotionStatusValues(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || "")
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function selectedWbPromotionAdvertStatuses() {
  const selected = normalizeWbPromotionStatusValues(qs("wbPromotionAdvertsStatuses")?.value || "");
  return selected.filter((value) => wbPromotionAdvertStatusOptions.some((option) => option.value === value));
}

function updateWbPromotionAdvertStatusesToggle() {
  const toggle = qs("wbPromotionAdvertsStatusesToggle");
  if (!toggle) return;
  const selected = selectedWbPromotionAdvertStatuses();
  const allValues = wbPromotionAdvertStatusOptions.map((option) => option.value);
  if (selected.length === allValues.length) {
    toggle.textContent = "Все статусы";
  } else if (!selected.length) {
    toggle.textContent = "Не выбрано";
  } else if (selected.join(",") === WB_PROMOTION_ADVERTS_DEFAULT_STATUSES.join(",")) {
    toggle.textContent = "Активные + завершенные: 9, 11, 7";
  } else {
    toggle.textContent = `Выбрано: ${selected.length} (${selected.join(", ")})`;
  }
}

function renderWbPromotionAdvertStatusMenu() {
  const menu = qs("wbPromotionAdvertsStatusesMenu");
  if (!menu) return;
  const selected = new Set(selectedWbPromotionAdvertStatuses());
  const allSelected = selected.size === wbPromotionAdvertStatusOptions.length;
  menu.innerHTML = `
    <label class="dropdown-option">
      <input type="checkbox" value="" data-all="true" ${allSelected ? "checked" : ""} />
      <span>Все статусы</span>
    </label>
    ${wbPromotionAdvertStatusOptions.map((option) => `
      <label class="dropdown-option">
        <input type="checkbox" value="${escapeHtml(option.value)}" ${selected.has(option.value) ? "checked" : ""} />
        <span>${escapeHtml(option.label)}</span>
      </label>
    `).join("")}
  `;
}

function setWbPromotionAdvertStatuses(values) {
  const allowed = new Set(wbPromotionAdvertStatusOptions.map((option) => option.value));
  const normalized = [];
  normalizeWbPromotionStatusValues(values).forEach((value) => {
    if (!allowed.has(value) || normalized.includes(value)) return;
    normalized.push(value);
  });
  const input = qs("wbPromotionAdvertsStatuses");
  if (input) input.value = normalized.join(",");
  updateWbPromotionAdvertStatusesToggle();
  renderWbPromotionAdvertStatusMenu();
}

function setSelectedSportmasterValues(id, values, keepUnknown = true) {
  const available = new Set(state.sportmasterFilterValues[id] || []);
  const selected = [];
  normalizeMultiValuePayload(values).forEach((value) => {
    if (!keepUnknown && available.size && !available.has(value)) return;
    if (!selected.includes(value)) selected.push(value);
  });
  if (qs(id)) qs(id).value = JSON.stringify(selected);
  renderSportmasterFilterOptions(id, qs(`${id}Search`)?.value || "");
  updateSportmasterFilterToggle(id);
}

function setSelectedBoironBrandValues(id, values, keepUnknown = true) {
  const available = new Set(state.boironBrandValues || []);
  const selected = [];
  normalizeMultiValuePayload(values).forEach((value) => {
    if (!keepUnknown && available.size && !available.has(value)) return;
    if (!selected.includes(value)) selected.push(value);
  });
  if (qs(id)) qs(id).value = JSON.stringify(selected);
  renderBoironBrandFilterOptions(id, qs(`${id}Search`)?.value || "");
  updateBoironBrandFilterToggle(id);
}

function setSportmasterFilterOptions(id, values, selected = selectedSportmasterValues(id)) {
  state.sportmasterFilterValues[id] = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  setSelectedSportmasterValues(id, selected, false);
}

function setBoironBrandFilterOptions(values, selected = selectedBoironBrandValues()) {
  state.boironBrandValues = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  setSelectedBoironBrandValues("boiron_brand", selected, false);
}

function renderSportmasterFilterOptions(id, filterText = "") {
  const menu = qs(`${id}Menu`);
  if (!menu) return;
  const selected = new Set(selectedSportmasterValues(id));
  const query = filterText.trim().toLowerCase();
  const values = (state.sportmasterFilterValues[id] || [])
    .filter((value) => !query || String(value).toLowerCase().includes(query));
  menu.innerHTML = `
    <input id="${id}Search" class="dropdown-search" type="search" placeholder="Поиск" />
    <div>
      <label class="dropdown-option">
        <input type="checkbox" value="" data-all="true" ${selected.size ? "" : "checked"} />
        <span>Все</span>
      </label>
      ${values.map((value) => `
        <label class="dropdown-option">
          <input type="checkbox" value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""} />
          <span>${escapeHtml(value)}</span>
        </label>
      `).join("")}
    </div>
  `;
  const search = qs(`${id}Search`);
  if (search) search.value = filterText;
}

function renderBoironBrandFilterOptions(id = "boiron_brand", filterText = "") {
  const menu = qs(`${id}Menu`);
  if (!menu) return;
  const selected = new Set(selectedBoironBrandValues(id));
  const query = filterText.trim().toLowerCase();
  const values = (state.boironBrandValues || [])
    .filter((value) => !query || String(value).toLowerCase().includes(query));
  menu.innerHTML = `
    <input id="${id}Search" class="dropdown-search" type="search" placeholder="Поиск" />
    <div>
      <label class="dropdown-option">
        <input type="checkbox" value="" data-all="true" ${selected.size ? "" : "checked"} />
        <span>Все</span>
      </label>
      ${values.map((value) => `
        <label class="dropdown-option">
          <input type="checkbox" value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""} />
          <span>${escapeHtml(value)}</span>
        </label>
      `).join("")}
    </div>
  `;
  const search = qs(`${id}Search`);
  if (search) search.value = filterText;
}

function updateSportmasterFilterToggle(id) {
  const toggle = qs(`${id}Toggle`);
  if (!toggle) return;
  const selected = selectedSportmasterValues(id);
  if (!selected.length) {
    toggle.textContent = "Все";
  } else if (selected.length === 1) {
    toggle.textContent = selected[0];
  } else {
    toggle.textContent = `Выбрано: ${selected.length}`;
  }
}

function updateBoironBrandFilterToggle(id = "boiron_brand") {
  const toggle = qs(`${id}Toggle`);
  if (!toggle) return;
  const selected = selectedBoironBrandValues(id);
  if (!selected.length) {
    toggle.textContent = "Все";
  } else if (selected.length === 1) {
    toggle.textContent = selected[0];
  } else {
    toggle.textContent = `Выбрано: ${selected.length}`;
  }
}

function appendSportmasterFilterParams(params) {
  if (!sportmasterFiltersEnabled()) return;
  sportmasterFilterIds.forEach((id) => {
    selectedSportmasterValues(id).forEach((value) => params.append(id, value));
  });
}

function appendBoironBrandFilterParams(params) {
  if (!boironBrandFiltersEnabled()) return;
  selectedBoironBrandValues().forEach((value) => params.append("boiron_brand", value));
}

function filterDropdownMenuIds() {
  return [
    "categoryMenu",
    "productMenu",
    "collectionStatusMenu",
    "seoStatusMenu",
    "dateRangeMenu",
    "wbPromotionAdvertsStatusesMenu",
    ...sportmasterFilterIds.map((id) => `${id}Menu`),
    ...boironBrandFilterIds.map((id) => `${id}Menu`),
  ];
}

function closeFilterDropdowns(exceptMenuId = "") {
  filterDropdownMenuIds().forEach((menuId) => {
    if (menuId === exceptMenuId) return;
    qs(menuId)?.classList.add("hidden");
  });
}

function toggleSingleDropdown(menuId, focusId = "") {
  const menu = qs(menuId);
  if (!menu) return;
  const shouldOpen = menu.classList.contains("hidden");
  closeFilterDropdowns(menuId);
  menu.classList.toggle("hidden", !shouldOpen);
  if (shouldOpen && focusId) qs(focusId)?.focus();
}

function markFiltersDirty(dirty = true) {
  state.filtersDirty = dirty;
  const button = qs("applyFilters");
  const label = qs("filterDirtyStatus");
  if (button) button.disabled = !dirty;
  if (label) label.textContent = dirty ? "Есть непримененные фильтры" : "Фильтры применены";
}

const refreshFilterOptionsAfterDraftChange = debounce(() => {
  if (state.dashboard === "admin") return;
  loadFilters()
    .catch((error) => {
      setStatusError(error);
    });
}, 250);

async function applyPendingFilters() {
  closeFilterDropdowns();
  state.page = 1;
  syncPendingAdvCampaignFilter();
  if (state.dashboard === "sku" || state.dashboard === "product") {
    state.drillCategory = "";
  }
  await loadFilters();
  await loadData();
}

async function resetAllFilters() {
  if (state.dashboard === "admin") return;
  closeFilterDropdowns();
  state.page = 1;
  state.drillCategory = "";
  resetColumnFilters();
  clearCategorySelection();
  resetSportmasterFilters();
  resetMediaAdvFilters();
  clearAdvCampaignFilter();
  setSelectedCollectionStatuses([]);
  setSelectedSeoStatuses([]);
  mappingFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
  ozonProductAttributeFilterIds.forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
  ["abc_orders", "abc_sales", "abc_stock", "abc_combined"].forEach((id) => {
    if (qs(id)) qs(id).value = "";
  });
  if (qs("article")) qs("article").value = "";
  setProductFilter("");
  if (qs("date_from")) qs("date_from").value = "";
  if (qs("date_to")) qs("date_to").value = "";
  draftDateFrom = "";
  draftDateTo = "";
  draftDateClickStep = 0;
  datePickerLeftMonth = null;
  state.abcDateRangeInitialized = false;
  state.advDateRangeInitialized = false;
  state.mediaAdvDateRangeInitialized = false;
  state.skuDateRangeInitialized = false;
  state.funnelDateRangeInitialized = false;
  state.weeklyDynamicsDateRangeInitialized = false;
  state.seoMonitoringDateRangeInitialized = false;
  state.seoMonitoringSelectedProducts = [];
  state.seoMonitoringValuationNote = "";
  state.planfactDateRangeInitialized = false;
  if (qs("category_level")) qs("category_level").value = "category";
  if (qs("sort")) qs("sort").value = "stock";
  if (qs("limit")) qs("limit").value = "50";
  if (qs("period_group")) qs("period_group").value = state.dashboard === "weeklyDynamics" ? "week" : "day";
  if (state.dashboard === "seoMonitoring") {
    state.sortCol = "zakazano_rub";
    state.sortDir = "desc";
  } else {
    const chronological = state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "planfact";
    state.sortCol = chronological ? "report_date" : selectedMetricField();
    state.sortDir = chronological ? "asc" : "desc";
  }
  resetPanelChartMetricsToSort();
  markFiltersDirty(false);
  await loadFilters({ preserveCategories: false });
  await loadData();
}

function resetPanelChartMetricsToSort() {
  const metric = selectedMetricKey();
  state.primaryChartMetric = metric;
  state.secondaryChartMetric = metric;
}

function panelMetricControlsEnabled() {
  return state.dashboard === "abc" || state.dashboard === "product";
}

function fillChartMetricSelect(selectId, selectedKey) {
  const select = qs(selectId);
  if (!select) return;
  select.innerHTML = Object.entries(metricSelectLabels)
    .map(([key, label]) => `<option value="${key}" ${key === selectedKey ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
  select.value = normalizeChartMetricKey(selectedKey);
}

function updateChartMetricControls() {
  const enabled = panelMetricControlsEnabled();
  fillChartMetricSelect("primaryChartMetricSelect", chartMetricKey("primary"));
  fillChartMetricSelect("secondaryChartMetricSelect", chartMetricKey("secondary"));
  qs("primaryChartMetricSelect")?.classList.toggle("hidden", !enabled);
  qs("secondaryChartMetricSelect")?.classList.toggle("hidden", !enabled);
}

function funnelMetricConfigsForMarketplace(marketplace = currentMarketplace()) {
  if (state.dashboard === "inventoryHistory") return inventoryHistoryMetricConfigs;
  const allowed = new Set(funnelMetricKeysByMarketplace[marketplace] || funnelMetricKeysByMarketplace.ozon);
  return funnelMetricConfigs.filter((metric) => allowed.has(metric.key) && funnelMetricIsAvailable(metric.key));
}

function defaultFunnelMetricKeys(marketplace = currentMarketplace()) {
  const available = new Set(funnelMetricConfigsForMarketplace(marketplace).map((metric) => metric.key));
  const preferred = state.dashboard === "inventoryHistory"
    ? inventoryHistoryDefaultMetrics
    : (funnelDefaultMetricsByMarketplace[marketplace] || funnelDefaultMetricsByMarketplace.ozon);
  const selected = preferred.filter((key) => available.has(key));
  return selected.length ? selected : [...available].slice(0, 4);
}

function normalizedFunnelSelectedMetrics(marketplace = currentMarketplace()) {
  const allowed = new Set(funnelMetricConfigsForMarketplace(marketplace).map((metric) => metric.key));
  const current = state.funnelSelectedMetrics.filter(Boolean);
  const selected = current.filter((key) => allowed.has(key));
  if (current.length && selected.length !== current.length) {
    return defaultFunnelMetricKeys(marketplace);
  }
  return selected.length ? selected : defaultFunnelMetricKeys(marketplace);
}

function currentQuery() {
  const params = new URLSearchParams();
  params.set("client", currentClient());
  filterIds.forEach((id) => {
    if (id === "category") {
      if (!categoryFilterEnabled()) return;
      selectedValues("category").forEach((value) => params.append("categories", value));
      return;
    }
    if (id === "category_level" && !categoryLevelFilterEnabled()) return;
    if (id === "seo_status") {
      if (!seoFilterEnabled()) return;
      selectedSeoStatuses().forEach((value) => params.append("seo_status", value));
      return;
    }
    if (id === "collection_status") {
      if (!collectionFilterEnabled()) return;
      selectedCollectionStatuses().forEach((value) => params.append("collection_status", value));
      return;
    }
    if (sportmasterFilterIds.includes(id)) {
      if (!sportmasterFiltersEnabled()) return;
      selectedSportmasterValues(id).forEach((value) => params.append(id, value));
      return;
    }
    if (boironBrandFilterIds.includes(id)) {
      if (!boironBrandFiltersEnabled()) return;
      selectedBoironBrandValues(id).forEach((value) => params.append(id, value));
      return;
    }
    if (id === "media_level") {
      if (!filterReportCapabilities().mediaLevel) return;
      params.set("media_level", qs("media_level")?.value || "campaign");
      return;
    }
    if (id === "adv_campaign_id" && (state.dashboard !== "adv" || currentMarketplace() !== "ozon")) return;
    if (id === "media_status" || id === "media_segment" || id === "media_campaign_id") {
      if (!filterReportCapabilities().mediaFilters) return;
    }
    if (id === "media_group_id" && !filterReportCapabilities().mediaGroupFilter) return;
    if (id === "media_creative_id" && !filterReportCapabilities().mediaCreativeFilter) return;
    if (wbQueryClassificationFilterIds.includes(id) && state.dashboard !== "wbSearchQueries") return;
    if (wbEntranceFilterIds.includes(id) && state.dashboard !== "wbEntrance") return;
    if (ozonProductAttributeFilterIds.includes(id) && !ozonProductAttributeFiltersEnabled()) return;
    if (mappingFilterIds.includes(id) && !gloriaAssortmentFiltersEnabled()) return;
    if (["abc_orders", "abc_sales", "abc_stock", "abc_combined"].includes(id) && !abcClassificationFiltersEnabled()) return;
    if (id === "sort" && !filterReportCapabilities().sort) return;
    if (id === "limit" && !filterReportCapabilities().limit) return;
    if (id === "period_group" && !filterReportCapabilities().periodGroup) return;
    if (id === "article" && !articleFilterEnabled()) return;
    if (id === "product" && !productNameFilterEnabled()) return;
    if ((id === "date_from" || id === "date_to") && !filterReportCapabilities().dateRange) return;
    if (state.dashboard === "admin") return;
    if (!qs(id)) return;
    const value = qs(id).value.trim();
    if (value) params.set(id, value);
  });
  params.set("page", String(state.page));
  params.set("sort_col", state.sortCol);
  params.set("sort_dir", state.sortDir);
  const activeColumnFilters = Object.entries(state.columnFilters || {})
    .filter(([, config]) => config && config.op && String(config.value || "").trim())
    .map(([column, config]) => ({ column, op: config.op, value: String(config.value).trim() }));
  if (activeColumnFilters.length) {
    params.set("column_filters", JSON.stringify(activeColumnFilters));
  }
  return params.toString();
}

function chartExportDashboards() {
  return new Set(["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics"]);
}

const chartExportSelectorPrimary = '[data-chart-export="primary"]';
const chartExportSelectorSecondary = '[data-chart-export="secondary"]';

function setChartExportButtonsVisible(visible) {
  document.querySelectorAll(`${chartExportSelectorPrimary}, ${chartExportSelectorSecondary}`).forEach((button) => {
    button.classList.toggle("hidden", !visible || (state.dashboard === "weeklyDynamics" && button.dataset.chartExport === "secondary"));
  });
}

function chartExportPrimaryMetrics() {
  if (state.dashboard === "abc" || state.dashboard === "product") return [chartMetricField("primary")];
  if (state.dashboard === "sku") return ["total_stock_qty"];
  if (state.dashboard === "adv") return normalizedAdvSelectedMetrics();
  if (state.dashboard === "mediaAdv") return normalizedMediaAdvSelectedMetrics();
  if (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") return normalizedFunnelSelectedMetrics();
  return [];
}

function chartExportSecondaryMetrics() {
  if (state.dashboard === "abc" || state.dashboard === "product") return [chartMetricField("secondary")];
  if (state.dashboard === "sku") return ["zakazano_sht"];
  if (state.dashboard === "adv") {
    return [
      "orders_qty",
      "direct_orders_qty",
      "indirect_orders_qty",
      "orders_amount_rub",
      "direct_orders_amount_rub",
      "indirect_orders_amount_rub",
      "direct_drr_pct",
      "indirect_drr_pct",
      "impressions",
      "promoted_sku_count",
      "ordered_sku_count",
      "total_sku_count",
      "clicks",
      "added_to_cart",
      "expense_rub",
      "adv_orders_to_total_orders_pct",
      "ctr_calc_pct",
      "click_to_cart_pct",
      "cart_to_order_pct",
      "impression_to_order_pct",
      "click_to_order_pct",
      "cpc_calc_rub",
      "cpa_calc_rub",
      "cpm_calc_rub",
    ];
  }
  if (state.dashboard === "mediaAdv") {
    return [
      "attributed_revenue_rub",
      "post_view_revenue_rub",
      "expense_rub",
      "impressions",
      "clicks",
      "attributed_orders_qty",
      "ctr_calc_pct",
      "click_to_order_pct",
      "drr_direct_pct",
      "drr_attributed_pct",
      "attributed_roas",
      "post_view_revenue_share_pct",
      "cpc_calc_rub",
      "cpm_calc_rub",
      "post_view_orders_per_1000_impressions",
    ];
  }
  if (state.dashboard === "funnel") {
    return [
      "impressions_total",
      "impressions_search_catalog",
      "card_visits",
      "cart_adds",
      "ordered_units",
      "ordered_amount_rub",
      ...(currentMarketplace() === "wb" ? ["bought_units", "bought_amount_rub", "cohort_bought_units", "cohort_bought_amount_rub"] : []),
      "search_to_card_visit_pct",
      "total_impression_to_card_visit_pct",
      "card_visit_to_cart_pct",
      "cart_to_order_pct",
      "card_visit_to_order_pct",
      "ordered_amount_per_unit_rub",
    ];
  }
  return [];
}

function chartExportMetricOptions(chart) {
  if (chart === "secondary") {
    const metrics = chartExportSecondaryMetrics();
    if (state.dashboard === "abc" || state.dashboard === "product" || state.dashboard === "sku") {
      return { metrics, axes: {}, types: Object.fromEntries(metrics.map((metric) => [metric, "bar"])) };
    }
    return { metrics, axes: {}, types: {} };
  }
  if (state.dashboard === "abc" || state.dashboard === "product" || state.dashboard === "sku") {
    const metrics = chartExportPrimaryMetrics();
    return { metrics, axes: {}, types: Object.fromEntries(metrics.map((metric) => [metric, "bar"])) };
  }
  if (state.dashboard === "adv") {
    return { metrics: chartExportPrimaryMetrics(), axes: state.advMetricAxes, types: state.advMetricTypes };
  }
  if (state.dashboard === "mediaAdv") {
    return { metrics: chartExportPrimaryMetrics(), axes: state.mediaAdvMetricAxes, types: state.mediaAdvMetricTypes };
  }
  if (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") {
    return { metrics: chartExportPrimaryMetrics(), axes: state.funnelMetricAxes, types: state.funnelMetricTypes };
  }
  return { metrics: [], axes: {}, types: {} };
}

function chartExportQuery(chart, metricKey = "", source = "", title = "") {
  const params = new URLSearchParams(currentQuery());
  const exportChart = metricKey || source ? "secondary" : chart;
  const options = metricKey
    ? { metrics: [metricKey], axes: {}, types: { [metricKey]: "line" } }
    : (source ? { metrics: [], axes: {}, types: {} } : chartExportMetricOptions(exportChart));
  params.set("dashboard", state.dashboard);
  params.set("chart", exportChart);
  params.set("chart_metrics", options.metrics.join(","));
  params.set("chart_axes", JSON.stringify(options.axes || {}));
  params.set("chart_types", JSON.stringify(options.types || {}));
  if (source) params.set("chart_source", source);
  if (title) params.set("chart_title", title);
  return params.toString();
}

function chartExportElement(chart) {
  const button = document.querySelector(`[data-chart-export="${chart}"]`);
  return button?.closest(".panel") || (chart === "secondary" ? qs("abcChart")?.closest(".panel") : qs("barChart")?.closest(".panel"));
}

function chartExportButtonIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 15v4h14v-4" /></svg>';
}

function collectSnapshotStyles() {
  const styles = [];
  document.querySelectorAll("style").forEach((node) => styles.push(node.textContent || ""));
  Array.from(document.styleSheets).forEach((sheet) => {
    try {
      if (!sheet.cssRules) return;
      styles.push(
        Array.from(sheet.cssRules)
          .map((rule) => rule.cssText)
          .filter((text) => !String(text).trim().toLowerCase().startsWith("@import"))
          .join("\n")
      );
    } catch (error) {
      // Cross-origin stylesheets are skipped; local dashboard CSS is enough for the chart snapshot.
    }
  });
  styles.push(`
    * { box-sizing: border-box; }
    body, .snapshot-root { margin: 0; background: #ffffff; font-family: Montserrat, Arial, sans-serif; color: #142327; }
    .snapshot-root .chart-export-btn,
    .snapshot-root .mini-export-btn { display: none !important; }
    .snapshot-root .panel { width: 100%; margin: 0; box-shadow: none; }
  `);
  return styles.join("\n");
}

function chartSnapshotClone(element) {
  const clone = element.cloneNode(true);
  clone.querySelectorAll(".chart-export-btn, .mini-export-btn").forEach((node) => node.remove());
  clone.querySelectorAll("select").forEach((select) => {
    const replacement = document.createElement("span");
    replacement.className = "chart-metric-control snapshot-select";
    replacement.textContent = select.selectedOptions?.[0]?.textContent || select.value || "";
    select.replaceWith(replacement);
  });
  clone.querySelectorAll("input, textarea").forEach((input) => {
    const replacement = document.createElement("span");
    replacement.className = input.className || "snapshot-field";
    replacement.textContent = input.value || input.getAttribute("placeholder") || "";
    input.replaceWith(replacement);
  });
  return clone;
}

async function captureChartElementAsPng(element) {
  if (!element) return "";
  const rect = element.getBoundingClientRect();
  const width = Math.max(1, Math.ceil(rect.width));
  const height = Math.max(1, Math.ceil(rect.height));
  const clone = chartSnapshotClone(element);
  const html = new XMLSerializer().serializeToString(clone);
  const css = collectSnapshotStyles().replaceAll("]]>", "]]]]><![CDATA[>");
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <foreignObject width="100%" height="100%">
        <div xmlns="http://www.w3.org/1999/xhtml" class="snapshot-root">
          <style><![CDATA[${css}]]></style>
          ${html}
        </div>
      </foreignObject>
    </svg>
  `;
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Не удалось подготовить снимок диаграммы"));
      img.src = url;
    });
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * scale);
    canvas.height = Math.ceil(height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(scale, scale);
    ctx.drawImage(image, 0, 0, width, height);
    return canvas.toDataURL("image/png");
  } catch (error) {
    console.warn("Chart snapshot export fallback:", error);
    return "";
  } finally {
    URL.revokeObjectURL(url);
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "chart.xlsx";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportWeeklyDynamicsToExcel(kind = "trend") {
  const response = await appFetch("/api/weekly-dynamics-export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: chartExportQuery("primary"),
      kind,
    }),
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (error) {
      // Successful responses are XLSX files; failed binary responses keep the HTTP message.
    }
    throw new Error(message);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  downloadBlob(await response.blob(), filenameMatch ? filenameMatch[1] : `weekly_dynamics_${kind}.xlsx`);
}

async function exportChartToExcel(chart, button = null) {
  if (!chartExportDashboards().has(state.dashboard)) return;
  if (state.dashboard === "weeklyDynamics") {
    await exportWeeklyDynamicsToExcel("trend");
    return;
  }
  const metricKey = button?.dataset.chartMetric || "";
  const source = button?.dataset.chartSource || "";
  const title = button?.dataset.chartTitle || "";
  const query = chartExportQuery(chart, metricKey, source, title);
  const element = metricKey || source ? button.closest(".mini-chart, .waterfall-card") : chartExportElement(chart);
  const chartImageDataUrl = await captureChartElementAsPng(element);
  const response = await appFetch("/api/chart-export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      chart_image_data_url: chartImageDataUrl,
    }),
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (error) {
      // File endpoints return binary bodies on success; failed binary responses keep the HTTP message.
    }
    throw new Error(message);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const detailKey = metricKey || source;
  const filename = detailKey
    ? `${state.dashboard}_${detailKey}_chart.xlsx`
    : (filenameMatch ? filenameMatch[1] : `${state.dashboard}_${chart}_chart.xlsx`);
  downloadBlob(await response.blob(), filename);
}

async function exportReportToExcel(button = null) {
  if (!chartExportDashboards().has(state.dashboard)) {
    exportTableToExcel();
    return;
  }
  if (state.dashboard === "weeklyDynamics") {
    await exportWeeklyDynamicsToExcel("trend");
    return;
  }
  const query = chartExportQuery("primary");
  const previousDisabled = button?.disabled;
  if (button) button.disabled = true;
  try {
    const response = await appFetch("/api/report-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload.error || message;
      } catch (error) {
        // File endpoints return binary bodies on success; failed binary responses keep the HTTP message.
      }
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
    downloadBlob(await response.blob(), filenameMatch ? filenameMatch[1] : `${state.dashboard}_report.xlsx`);
  } finally {
    if (button) button.disabled = Boolean(previousDisabled);
  }
}

function exportTableToExcel() {
  if (isAvitoDashboard()) {
    const query = new URLSearchParams(currentQuery());
    const ui = lastAvitoPayload ? avitoUi(lastAvitoPayload) : null;
    query.set("dashboard", state.dashboard);
    if (ui?.q) query.set("q", ui.q);
    Object.entries(ui?.filters || {}).forEach(([key, value]) => { if (value) query.set(key, value); });
    if (ui?.metrics?.length) query.set("metrics", ui.metrics.join(","));
    window.location.href = resolveAppUrl(`/api/export?${query.toString()}`);
    return;
  }
  if (state.dashboard === "weeklyDynamics") {
    exportWeeklyDynamicsToExcel("trend").catch((error) => { setStatusError(error); });
    return;
  }
  if (state.mode === "static") {
    qs("status").textContent = "Экспорт Excel доступен в live-режиме";
    return;
  }
  const query = new URLSearchParams(currentQuery());
  query.set("dashboard", state.dashboard);
  window.location.href = resolveAppUrl(`/api/export?${query.toString()}`);
}

async function getJson(url) {
  const response = await appFetch(url, { cache: "no-store" });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error(`API returned ${contentType || "an unknown format"} instead of JSON: ${response.url}`);
  }
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function getJsonAllowBusinessError(url) {
  const response = await appFetch(url, { cache: "no-store" });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error(`API returned ${contentType || "an unknown format"} instead of JSON: ${response.url}`);
  }
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function postJson(url, payload) {
  const response = await appFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function postWbPromotionFullstatsStream(payload) {
  const response = await appFetch("/api/admin/wb-api/promotion/fullstats-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  if (!response.body?.getReader) {
    throw new Error("Браузер не отдал поток WB fullstats");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalResult = null;

  const handleEvent = (event) => {
    if (!event || typeof event !== "object") return;
    if (event.event === "start") {
      appendApiTerminalLine(event.message || "Поток WB fullstats открыт", "meta");
      return;
    }
    if (event.event === "progress") {
      const progressPayload = {
        running: true,
        method: event.method || "promotion_fullstats",
        progress: event.progress || {},
      };
      if (event.log_file) setApiExportLogFile("promotion_fullstats", event.log_file);
      updateWbApiProgressView("promotion_fullstats", progressPayload);
      appendWbApiProgressLine("promotion_fullstats", progressPayload);
      return;
    }
    if (event.event === "result") {
      finalResult = event.result || {};
      return;
    }
    if (event.event === "error") {
      if (event.log_file) setApiExportLogFile("promotion_fullstats", event.log_file);
      throw new Error(event.error || "Ошибка WB fullstats stream");
    }
  };

  const handleLine = (line) => {
    const trimmed = String(line || "").trim();
    if (!trimmed) return;
    handleEvent(JSON.parse(trimmed));
  };

  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      lines.forEach(handleLine);
    }
    if (done) break;
  }
  if (buffer.trim()) handleLine(buffer);
  if (!finalResult) throw new Error("WB fullstats stream завершился без итогового результата");
  return finalResult;
}

function setWbApiRunControls(methodKey, runButton, isRunning, runText = "") {
  if (runButton) {
    runButton.disabled = isRunning;
    if (runText) runButton.textContent = runText;
  }
  const stopButton = document.querySelector(`[data-stop-wb-api="${methodKey}"]`);
  if (stopButton) {
    stopButton.disabled = !isRunning;
    stopButton.classList.toggle("is-active", isRunning);
    stopButton.classList.remove("is-stopping");
    stopButton.innerHTML = "<span aria-hidden=\"true\">&#9632;</span>";
  }
  if (isRunning) {
    state.wbApiRunningMethod = methodKey;
  } else if (state.wbApiRunningMethod === methodKey) {
    state.wbApiRunningMethod = "";
  }
}

function setApiExportLogFile(methodKey, filePath) {
  const cleanPath = String(filePath || "");
  if (!methodKey || !cleanPath) return;
  state.wbApiLastLogFiles[methodKey] = cleanPath;
  if (methodKey === "ozon_seo_details") state.ozonSeoLastLogFile = cleanPath;
  const button = document.querySelector(`[data-open-api-export-log="${methodKey}"]`);
  if (button) button.disabled = false;
}

function wbApiResultState(result) {
  return result?.stopped ? "stopped" : "done";
}

function wbApiResultPrefix(result) {
  return result?.stopped ? "Остановлено: " : "Готово: ";
}

function appendWbApiStoppedLine(result) {
  if (result?.stopped) {
    appendApiTerminalLine("Остановлено пользователем: новые WB-запросы не отправляются, сохранен частичный результат.", "warn");
  }
}

function appendWbApiProgressLine(methodKey, payload) {
  if (!payload?.running) return;
  const progress = payload.progress || {};
  const message = progress.message || payload.label || methodKey;
  if (!message) return;
  const signature = `${progress.phase || ""}|${progress.current || 0}|${progress.total || 0}|${message}`;
  if (state.wbApiLastProgressMessages[methodKey] === signature) return;
  state.wbApiLastProgressMessages[methodKey] = signature;
  const type = progress.phase === "wait" || progress.phase === "plan" ? "meta" : "output";
  const counter = progress.total && !message.startsWith("ПРОГРЕСС:")
    ? ` [${formatNumber(progress.current || 0)}/${formatNumber(progress.total)}]`
    : "";
  appendApiTerminalLine(`${message}${counter}`, type);
}

function wbApiProgressElementId(methodKey) {
  const elementIds = {
    promotion_fullstats: "wbPromotionStatsProgress",
    content_categories: "wbContentCategoriesProgress",
    content_cards: "wbContentCardsProgress",
    content_characteristics: "wbContentCharacteristicsProgress",
  };
  return elementIds[methodKey] || "";
}

function updateWbApiProgressView(methodKey, payload) {
  const elementId = wbApiProgressElementId(methodKey);
  if (!elementId) return;
  const element = qs(elementId);
  if (!element) return;
  const progress = payload?.progress || {};
  const total = Number(progress.total || 0);
  const current = Number(progress.current || 0);
  const running = Boolean(payload?.running);
  const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : running ? 8 : 0;
  const message = progress.message || (running ? "В работе" : "Ожидание запуска");
  const counter = total > 0 ? `${formatNumber(current)} / ${formatNumber(total)}` : "";
  element.className = `wb-api-progress ${running ? "is-active" : "is-idle"}`;
  element.style.setProperty("--progress", `${running ? Math.max(pct, 6) : pct}%`);
  element.innerHTML = `
    <div class="wb-api-progress-main">
      <div class="wb-api-progress-bar"><i></i></div>
      <span class="wb-api-progress-counter">${escapeHtml(counter || (running ? "В работе" : ""))}</span>
    </div>
    <div class="wb-api-progress-message">${escapeHtml(message)}</div>
  `;
  element.classList.toggle("hidden", !running && !message);
}

function completeWbApiProgressView(methodKey, message, stateName = "done") {
  const elementId = wbApiProgressElementId(methodKey);
  if (!elementId) return;
  const element = qs(elementId);
  if (!element) return;
  element.className = `wb-api-progress is-${stateName}`;
  element.style.setProperty("--progress", stateName === "error" ? "100%" : "100%");
  element.innerHTML = `
    <div class="wb-api-progress-main">
      <div class="wb-api-progress-bar"><i></i></div>
      <span class="wb-api-progress-counter">${stateName === "error" ? "Ошибка" : "100%"}</span>
    </div>
    <div class="wb-api-progress-message">${escapeHtml(message || "")}</div>
  `;
}

async function pollWbApiProgress(methodKey) {
  try {
    const payload = await getJson(`/api/admin/wb-api/progress?method=${encodeURIComponent(methodKey)}`);
    state.wbApiProgressSnapshots[methodKey] = payload;
    if (payload.log_file) setApiExportLogFile(methodKey, payload.log_file);
    updateWbApiProgressView(methodKey, payload);
    appendWbApiProgressLine(methodKey, payload);
    if (!payload.running) stopWbApiProgressPolling(methodKey);
  } catch (error) {
    appendApiTerminalLine(`Не удалось получить прогресс WB API: ${error.message}`, "warn");
    stopWbApiProgressPolling(methodKey);
  }
}

function startWbApiProgressPolling(methodKey) {
  stopWbApiProgressPolling(methodKey);
  state.wbApiLastProgressMessages[methodKey] = "";
  pollWbApiProgress(methodKey);
  state.wbApiProgressTimers[methodKey] = window.setInterval(() => {
    pollWbApiProgress(methodKey);
  }, 2000);
}

function stopWbApiProgressPolling(methodKey) {
  const timer = state.wbApiProgressTimers[methodKey];
  if (timer) window.clearInterval(timer);
  delete state.wbApiProgressTimers[methodKey];
}

async function stopWbApiScriptFromUi(button) {
  const method = button?.dataset?.stopWbApi || "";
  if (!method) return;
  const oldHtml = button.innerHTML;
  button.disabled = true;
  button.classList.add("is-stopping");
  button.innerHTML = "<span aria-hidden=\"true\">...</span>";
  appendApiTerminalLine("Запрошена остановка WB API-скрипта", "warn");
  try {
    const payload = await postJson("/api/admin/wb-api/stop", { method });
    appendApiTerminalLine(`Остановка принята: ${payload.label || method}`, "warn");
    qs("status").textContent = "Остановка WB API-скрипта запрошена";
  } catch (error) {
    appendApiTerminalLine(`Не удалось остановить: ${error.message}`, "error");
    setStatusError(error);
    if (state.wbApiRunningMethod === method) {
      button.disabled = false;
      button.classList.remove("is-stopping");
      button.innerHTML = oldHtml;
    }
  }
}

function explicitUrlParamOrSaved(urlParams, key, savedValue = "") {
  return urlParams.has(key) ? (urlParams.get(key) || "") : (savedValue || "");
}

function savedDashboardFilterContextMatches(payload, client, dashboard, marketplace) {
  const savedMarketplace = ["ozon", "wb"].includes(payload.marketplace) ? payload.marketplace : "ozon";
  return payload.client === client && payload.dashboard === dashboard && savedMarketplace === marketplace;
}

function syncDashboardUrlParams(currentUrl, payload) {
  if (payload.client) currentUrl.searchParams.set("client", payload.client);
  else currentUrl.searchParams.delete("client");
  currentUrl.searchParams.set("dashboard", payload.dashboard || "abc");
  if (payload.dashboard === "admin" && payload.adminImportMode) {
    currentUrl.searchParams.set("admin_mode", payload.adminImportMode);
  } else {
    currentUrl.searchParams.delete("admin_mode");
  }
  currentUrl.searchParams.set("marketplace", payload.marketplace || "ozon");
  if (["ozon", "wb", "avito", "yandex_market"].includes(payload.adminHistoryMarketplace)) {
    currentUrl.searchParams.set("history_marketplace", payload.adminHistoryMarketplace);
  } else {
    currentUrl.searchParams.delete("history_marketplace");
  }
  [
    ["date_from", payload.dateFrom],
    ["date_to", payload.dateTo],
    ["product", payload.product],
    ["article", payload.article],
    ["adv_campaign_id", payload.advCampaignId],
  ].forEach(([key, value]) => {
    if (value) currentUrl.searchParams.set(key, value);
    else currentUrl.searchParams.delete(key);
  });
  currentUrl.searchParams.delete("categories");
  (payload.selectedCategories || []).forEach((category) => currentUrl.searchParams.append("categories", category));
  return currentUrl;
}

function dateRangeFullyOutsideSource(currentFrom, currentTo, sourceFrom, sourceTo) {
  return Boolean(
    (currentTo && sourceFrom && currentTo < sourceFrom)
    || (currentFrom && sourceTo && currentFrom > sourceTo)
  );
}

function persistDashboardState() {
  if (state.mode !== "api") return;
  const payload = {
    dashboard: state.dashboard,
    marketplace: qs("marketplace")?.value || "ozon",
    selectedCategories: selectedValues("category"),
    page: state.page,
    sortCol: state.sortCol,
    sortDir: state.sortDir,
    columnFilters: state.columnFilters,
    columnOrders: state.columnOrders,
    dateFrom: qs("date_from")?.value || "",
    dateTo: qs("date_to")?.value || "",
    article: qs("article")?.value || "",
    product: qs("product")?.value || "",
    advCampaignId: qs("adv_campaign_id")?.value || "",
    seoMonitoringSelectedProducts: state.seoMonitoringSelectedProducts || [],
    collection_status: selectedCollectionStatuses(),
    seo_status: selectedSeoStatuses(),
    mappingFilters: Object.fromEntries(mappingFilterIds.map((id) => [id, qs(id)?.value || ""])),
    ozonProductAttributeFilters: Object.fromEntries(ozonProductAttributeFilterIds.map((id) => [id, qs(id)?.value || ""])),
    sportmasterFilters: Object.fromEntries(sportmasterFilterIds.map((id) => [id, selectedSportmasterValues(id)])),
    boironBrands: selectedBoironBrandValues(),
    productSearch: qs("productSearch")?.value || "",
    limit: qs("limit")?.value || "50",
    periodGroup: qs("period_group")?.value || "day",
    categoryLevel: selectedCategoryLevel(),
    sort: qs("sort")?.value || "stock",
    primaryChartMetric: state.primaryChartMetric,
    secondaryChartMetric: state.secondaryChartMetric,
    wbSearchSelectedMetrics: state.wbSearchSelectedMetrics,
    wbSearchMetricAxes: state.wbSearchMetricAxes,
    wbSearchMetricTypes: state.wbSearchMetricTypes,
    wbEntranceSelectedMetrics: state.wbEntranceSelectedMetrics,
    wbEntranceMetricAxes: state.wbEntranceMetricAxes,
    wbEntranceMetricTypes: state.wbEntranceMetricTypes,
    funnelSelectedMetrics: state.funnelSelectedMetrics,
    funnelMetricAxes: state.funnelMetricAxes,
    funnelMetricTypes: state.funnelMetricTypes,
    advSelectedMetrics: state.advSelectedMetrics,
    advMetricAxes: state.advMetricAxes,
    advMetricTypes: state.advMetricTypes,
    mediaAdvSelectedMetrics: state.mediaAdvSelectedMetrics,
    mediaAdvMetricAxes: state.mediaAdvMetricAxes,
    mediaAdvMetricTypes: state.mediaAdvMetricTypes,
    planfactRevenueSelectedMetrics: state.planfactRevenueSelectedMetrics,
    planfactRevenueMetricAxes: state.planfactRevenueMetricAxes,
    planfactRevenueMetricTypes: state.planfactRevenueMetricTypes,
    planfactExpenseSelectedMetrics: state.planfactExpenseSelectedMetrics,
    planfactExpenseMetricAxes: state.planfactExpenseMetricAxes,
    planfactExpenseMetricTypes: state.planfactExpenseMetricTypes,
    client: currentClient(),
    adminClient: state.adminClient,
    adminImportMode: state.adminImportMode,
    adminOnboardingClientKey: state.adminOnboardingClientKey,
    adminHistoryMarketplace: state.adminHistoryMarketplace,
    adminWbStockHistoryRanges: state.adminWbStockHistoryRanges,
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  const currentUrl = syncDashboardUrlParams(new URL(window.location.href), payload);
  window.history.replaceState(null, "", currentUrl);
}

function restorePlanFactGroupState(payload, group, metrics) {
  const cap = group === "expense" ? "Expense" : "Revenue";
  const selectedKey = `planfact${cap}SelectedMetrics`;
  const axesKey = `planfact${cap}MetricAxes`;
  const typesKey = `planfact${cap}MetricTypes`;
  const allowed = new Set(metrics.map((item) => item.key));
  if (Array.isArray(payload[selectedKey]) && payload[selectedKey].length) {
    state[selectedKey] = payload[selectedKey].filter((key) => allowed.has(key));
  }
  if (payload[axesKey] && typeof payload[axesKey] === "object") {
    state[axesKey] = Object.fromEntries(
      Object.entries(payload[axesKey])
        .filter(([key, axis]) => allowed.has(key) && ["left", "right"].includes(axis))
    );
  }
  if (payload[typesKey] && typeof payload[typesKey] === "object") {
    state[typesKey] = Object.fromEntries(
      Object.entries(payload[typesKey])
        .filter(([key, type]) => allowed.has(key) && ["line", "bar"].includes(type))
    );
  }
}

function resetPlanFactDefaultChartState() {
  state.planfactRevenueSelectedMetrics = ["orders_rub", "sales_rub", "sales_plan_daily_rub", "ad_spend_rub"];
  state.planfactRevenueMetricAxes = { orders_rub: "right", ad_spend_rub: "right" };
  state.planfactRevenueMetricTypes = { sales_rub: "bar" };
  state.planfactExpenseSelectedMetrics = ["ad_spend_rub", "ad_spend_plan_daily_rub"];
  state.planfactExpenseMetricAxes = {};
  state.planfactExpenseMetricTypes = {};
}

function restoreWbChartState(payload, selectedKey, axesKey, typesKey, configs) {
  const allowed = new Set(configs.map((item) => item.field));
  if (Array.isArray(payload[selectedKey]) && payload[selectedKey].length) {
    const selected = payload[selectedKey].filter((key) => allowed.has(key));
    if (selected.length) state[selectedKey] = selected;
  }
  if (payload[axesKey] && typeof payload[axesKey] === "object") {
    state[axesKey] = Object.fromEntries(
      Object.entries(payload[axesKey])
        .filter(([key, axis]) => allowed.has(key) && ["left", "right"].includes(axis))
    );
  }
  if (payload[typesKey] && typeof payload[typesKey] === "object") {
    state[typesKey] = Object.fromEntries(
      Object.entries(payload[typesKey])
        .filter(([key, type]) => allowed.has(key) && ["line", "bar"].includes(type))
    );
  }
}

function restoreDashboardState() {
  if (state.mode !== "api") return;
  let payload;
  try {
    payload = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    payload = {};
  }
  const dashboards = new Set(["abc", "product", "adv", "mediaAdv", "funnel", "weeklyDynamics", "inventoryHistory", "planfact", "commercialRadar", "salesPlanning", "mediaPlan", "profitLoss", "unitEconomics", "seoMonitoring", "wbSearchQueries", "wbEntrance", "sku", "admin", ...avitoDashboards, ...yandexDashboards]);
  if (dashboards.has(payload.dashboard)) state.dashboard = payload.dashboard === "admin" && !adminAccessEnabled() ? "abc" : payload.dashboard;
  if (/^[a-z][a-z0-9_]{1,47}$/.test(String(payload.client || ""))) state.client = String(payload.client);
  const urlParams = new URLSearchParams(window.location.search);
  const urlDashboard = urlParams.get("dashboard");
  const urlClient = urlParams.get("client");
  const urlAdminMode = urlParams.get("admin_mode");
  state.seoProjectsNavSource = urlParams.get("seo_workspace") === "generation" ? "semantics" : "monitoring";
  if (dashboards.has(urlDashboard)) state.dashboard = urlDashboard;
  if (/^[a-z][a-z0-9_]{1,47}$/.test(String(urlClient || ""))) state.client = urlClient;
  if (urlDashboard && !urlParams.has("client")) state.client = "";
  if (urlClient && !urlDashboard) state.dashboard = "planfact";
  const savedMarketplace = ["ozon", "wb"].includes(payload.marketplace) ? payload.marketplace : "ozon";
  const defaultMarketplace = ["ozon", "wb"].includes(qs("marketplace")?.value) ? qs("marketplace").value : "ozon";
  const savedClientDashboardMatches = payload.client === state.client && payload.dashboard === state.dashboard;
  const requestedMarketplace = explicitUrlParamOrSaved(
    urlParams,
    "marketplace",
    savedClientDashboardMatches ? savedMarketplace : defaultMarketplace,
  );
  const restoredMarketplace = ["ozon", "wb"].includes(requestedMarketplace) ? requestedMarketplace : defaultMarketplace;
  const savedFilterContextMatches = savedDashboardFilterContextMatches(payload, state.client, state.dashboard, restoredMarketplace);
  const filterPayload = savedFilterContextMatches ? payload : {};
  const useDefaultPlanFactMonth = planFactShouldUseDefaultMonth(urlParams);
  const restoredDateFrom = useDefaultPlanFactMonth
    ? ""
    : explicitUrlParamOrSaved(urlParams, "date_from", filterPayload.dateFrom || "");
  const restoredDateTo = useDefaultPlanFactMonth
    ? ""
    : explicitUrlParamOrSaved(urlParams, "date_to", filterPayload.dateTo || "");
  const restoredProduct = explicitUrlParamOrSaved(urlParams, "product", filterPayload.product || "");
  const restoredArticle = explicitUrlParamOrSaved(urlParams, "article", filterPayload.article || "");
  state.pendingAdvCampaignId = explicitUrlParamOrSaved(urlParams, "adv_campaign_id", filterPayload.advCampaignId || "");
  const restoredCategories = urlParams.has("categories")
    ? urlParams.getAll("categories").map((value) => value.trim()).filter(Boolean)
    : (Array.isArray(filterPayload.selectedCategories) ? filterPayload.selectedCategories.filter(Boolean) : []);
  state.selectedCategories = restoredCategories;
  state.page = Number(filterPayload.page) > 0 ? Number(filterPayload.page) : 1;
  state.sortCol = filterPayload.sortCol || (state.dashboard === "wbEntrance" ? "ordered_units" : (state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "planfact" || state.dashboard === "seoMonitoring" || state.dashboard === "wbSearchQueries" ? "report_date" : state.sortCol));
  state.sortDir = filterPayload.sortDir === "asc" ? "asc" : "desc";
  state.columnFilters = filterPayload.columnFilters && typeof filterPayload.columnFilters === "object" ? filterPayload.columnFilters : {};
  state.columnOrders = filterPayload.columnOrders && typeof filterPayload.columnOrders === "object" ? filterPayload.columnOrders : {};
  state.seoMonitoringSelectedProducts = Array.isArray(filterPayload.seoMonitoringSelectedProducts)
    ? filterPayload.seoMonitoringSelectedProducts.map(String).filter(Boolean)
    : [];
  if (state.clients.some((client) => client.key === payload.adminClient)) state.adminClient = payload.adminClient;
  if (["manual", "daily", "allDaily", "apiDaily", "api", "client", "clientOnboarding", "database", "integrations", "users"].includes(payload.adminImportMode)) state.adminImportMode = payload.adminImportMode;
  if (["manual", "daily", "allDaily", "apiDaily", "api", "client", "clientOnboarding", "database", "integrations", "users"].includes(urlAdminMode)) state.adminImportMode = urlAdminMode;
  if (/^[a-z][a-z0-9_]{1,47}$/.test(String(payload.adminOnboardingClientKey || ""))) {
    state.adminOnboardingClientKey = String(payload.adminOnboardingClientKey);
  }
  const savedHistoryMarketplace = ["ozon", "wb", "avito", "yandex_market"].includes(payload.adminHistoryMarketplace)
    ? payload.adminHistoryMarketplace
    : "";
  const requestedHistoryMarketplace = explicitUrlParamOrSaved(
    urlParams,
    "history_marketplace",
    savedHistoryMarketplace,
  );
  if (["ozon", "wb", "avito", "yandex_market"].includes(requestedHistoryMarketplace)) {
    state.adminHistoryMarketplace = requestedHistoryMarketplace;
  }
  if (payload.adminWbStockHistoryRanges && typeof payload.adminWbStockHistoryRanges === "object") {
    state.adminWbStockHistoryRanges = Object.fromEntries(
      Object.entries(payload.adminWbStockHistoryRanges)
        .filter(([key, range]) => /^[a-z][a-z0-9_]{1,47}$/.test(key)
          && range && typeof range === "object"
          && /^\d{4}-\d{2}-\d{2}$/.test(String(range.dateFrom || ""))
          && /^\d{4}-\d{2}-\d{2}$/.test(String(range.dateTo || "")))
        .map(([key, range]) => [key, {dateFrom: String(range.dateFrom), dateTo: String(range.dateTo)}])
    );
  }
  if (qs("marketplace")) qs("marketplace").value = restoredMarketplace;
  if (qs("clientSelect")) qs("clientSelect").value = state.client;
  if (qs("date_from")) qs("date_from").value = restoredDateFrom;
  if (qs("date_to")) qs("date_to").value = restoredDateTo;
  if (qs("article")) qs("article").value = restoredArticle;
  if (qs("product")) qs("product").value = restoredProduct;
  if (state.dashboard === "inventoryHistory") {
    state.inventoryHistoryDateRangeInitialized = Boolean(restoredDateFrom || restoredDateTo);
  }
  setSelectedCollectionStatuses(normalizeCollectionStatusPayload(filterPayload.collection_status));
  setSelectedSeoStatuses(normalizeSeoStatusPayload(filterPayload.seo_status));
  if (filterPayload.mappingFilters && typeof filterPayload.mappingFilters === "object") {
    mappingFilterIds.forEach((id) => {
      if (qs(id)) qs(id).value = filterPayload.mappingFilters[id] || "";
    });
  }
  if (filterPayload.ozonProductAttributeFilters && typeof filterPayload.ozonProductAttributeFilters === "object") {
    ozonProductAttributeFilterIds.forEach((id) => {
      if (qs(id)) qs(id).value = filterPayload.ozonProductAttributeFilters[id] || "";
    });
  }
  if (filterPayload.sportmasterFilters && typeof filterPayload.sportmasterFilters === "object") {
    sportmasterFilterIds.forEach((id) => {
      setSelectedSportmasterValues(id, filterPayload.sportmasterFilters[id] || [], true);
    });
  }
  setSelectedBoironBrandValues("boiron_brand", filterPayload.boironBrands || [], true);
  if (qs("productSearch")) qs("productSearch").value = restoredProduct || filterPayload.productSearch || "";
  if (qs("productToggle")) qs("productToggle").textContent = restoredProduct || "Все товары";
  if (qs("limit") && filterPayload.limit) qs("limit").value = filterPayload.limit;
  if (qs("period_group") && filterPayload.periodGroup) qs("period_group").value = filterPayload.periodGroup;
  if (qs("category_level") && sportmasterCategoryLevelValues.has(filterPayload.categoryLevel)) qs("category_level").value = filterPayload.categoryLevel;
  if (qs("sort") && filterPayload.sort) qs("sort").value = filterPayload.sort;
  if (payload.primaryChartMetric) state.primaryChartMetric = normalizeChartMetricKey(payload.primaryChartMetric);
  if (payload.secondaryChartMetric) state.secondaryChartMetric = normalizeChartMetricKey(payload.secondaryChartMetric);
  restoreWbChartState(
    payload,
    "wbSearchSelectedMetrics",
    "wbSearchMetricAxes",
    "wbSearchMetricTypes",
    wbSearchChartMetricConfigs,
  );
  restoreWbChartState(
    payload,
    "wbEntranceSelectedMetrics",
    "wbEntranceMetricAxes",
    "wbEntranceMetricTypes",
    wbEntranceChartMetricConfigs,
  );
  if (state.dashboard === "inventoryHistory") {
    state.funnelSelectedMetrics = [...inventoryHistoryDefaultMetrics];
    state.funnelMetricAxes = { ordered_units: "right", adv_orders: "right", organic_orders: "right" };
    state.funnelMetricTypes = { stock_inflow_qty: "bar", stock_outflow_qty: "bar" };
  } else if (Array.isArray(payload.funnelSelectedMetrics) && payload.funnelSelectedMetrics.length) {
    state.funnelSelectedMetrics = payload.funnelSelectedMetrics.filter((key) => funnelMetricConfigs.some((item) => item.key === key));
  }
  if (state.dashboard !== "inventoryHistory" && payload.funnelMetricAxes && typeof payload.funnelMetricAxes === "object") {
    const allowed = new Set(funnelMetricConfigs.map((item) => item.key));
    state.funnelMetricAxes = Object.fromEntries(
      Object.entries(payload.funnelMetricAxes)
        .filter(([key, axis]) => allowed.has(key) && ["left", "right"].includes(axis))
    );
  }
  if (state.dashboard !== "inventoryHistory" && payload.funnelMetricTypes && typeof payload.funnelMetricTypes === "object") {
    const allowed = new Set(funnelMetricConfigs.map((item) => item.key));
    state.funnelMetricTypes = Object.fromEntries(
      Object.entries(payload.funnelMetricTypes)
        .filter(([key, type]) => allowed.has(key) && ["line", "bar"].includes(type))
    );
  }
  if (Array.isArray(payload.advSelectedMetrics) && payload.advSelectedMetrics.length) {
    state.advSelectedMetrics = payload.advSelectedMetrics.filter((key) => advMetricConfigs.some((item) => item.key === key));
  }
  if (payload.advMetricAxes && typeof payload.advMetricAxes === "object") {
    const allowed = new Set(advMetricConfigs.map((item) => item.key));
    state.advMetricAxes = Object.fromEntries(
      Object.entries(payload.advMetricAxes)
        .filter(([key, axis]) => allowed.has(key) && ["left", "right"].includes(axis))
    );
  }
  if (payload.advMetricTypes && typeof payload.advMetricTypes === "object") {
    const allowed = new Set(advMetricConfigs.map((item) => item.key));
    state.advMetricTypes = Object.fromEntries(
      Object.entries(payload.advMetricTypes)
        .filter(([key, type]) => allowed.has(key) && ["line", "bar"].includes(type))
    );
  }
  if (Array.isArray(payload.mediaAdvSelectedMetrics) && payload.mediaAdvSelectedMetrics.length) {
    state.mediaAdvSelectedMetrics = payload.mediaAdvSelectedMetrics.filter((key) => mediaAdvMetricConfigs.some((item) => item.key === key));
  }
  if (payload.mediaAdvMetricAxes && typeof payload.mediaAdvMetricAxes === "object") {
    const allowed = new Set(mediaAdvMetricConfigs.map((item) => item.key));
    state.mediaAdvMetricAxes = Object.fromEntries(
      Object.entries(payload.mediaAdvMetricAxes)
        .filter(([key, axis]) => allowed.has(key) && ["left", "right"].includes(axis))
    );
  }
  if (payload.mediaAdvMetricTypes && typeof payload.mediaAdvMetricTypes === "object") {
    const allowed = new Set(mediaAdvMetricConfigs.map((item) => item.key));
    state.mediaAdvMetricTypes = Object.fromEntries(
      Object.entries(payload.mediaAdvMetricTypes)
        .filter(([key, type]) => allowed.has(key) && ["line", "bar"].includes(type))
    );
  }
  resetPlanFactDefaultChartState();
  if (state.dashboard === "adv") state.advDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "mediaAdv") state.mediaAdvDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "sku") state.skuDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "funnel") state.funnelDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "weeklyDynamics") state.weeklyDynamicsDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "inventoryHistory") state.inventoryHistoryDateRangeInitialized = Boolean(restoredDateFrom || restoredDateTo);
  if (state.dashboard === "seoMonitoring") state.seoMonitoringDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  if (state.dashboard === "planfact") state.planfactDateRangeInitialized = Boolean(restoredDateFrom || restoredDateTo);
  if (state.dashboard === "abc" || state.dashboard === "product") state.abcDateRangeInitialized = Boolean(payload.dateFrom || payload.dateTo);
  updateDateRangeToggle();
}

function fillSelect(id, values) {
  const select = qs(id);
  select.innerHTML = '<option value="">Все</option>';
  [...new Set(values || [])].filter(Boolean).sort().forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
}

function fillLabeledSelect(id, values, labels = {}, selectedValue = "") {
  const select = qs(id);
  if (!select) return;
  select.innerHTML = '<option value="">Все</option>';
  [...new Set(values || [])].filter(Boolean).forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels?.[value] || value;
    select.appendChild(option);
  });
  if (selectedValue && [...select.options].some((option) => option.value === selectedValue)) select.value = selectedValue;
}

function restoreFilterValue(id, value) {
  const element = qs(id);
  if (!element || !value) return;
  if (sportmasterFilterIds.includes(id)) {
    setSelectedSportmasterValues(id, value, true);
    return;
  }
  if (boironBrandFilterIds.includes(id)) {
    setSelectedBoironBrandValues(id, value, true);
    return;
  }
  if (id === "seo_status") {
    setSelectedSeoStatuses(normalizeSeoStatusPayload(value), true);
    return;
  }
  if (id === "collection_status") {
    setSelectedCollectionStatuses(normalizeCollectionStatusPayload(value), true);
    return;
  }
  if (element.tagName === "SELECT") {
    if (Array.from(element.options).some((option) => option.value === value)) {
      element.value = value;
    }
    return;
  }
  element.value = value;
}

function normalizeSeoStatusPayload(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (typeof value === "string" && value) return value.split(",").map((item) => item.trim()).filter(Boolean);
  return [];
}

function normalizeCollectionStatusPayload(value) {
  const rawValues = Array.isArray(value)
    ? value.filter(Boolean)
    : (typeof value === "string" && value ? value.split(",").map((item) => item.trim()).filter(Boolean) : []);
  return rawValues.map((item) => (item === COLLECTION_PRIORITY || item === PRIORITY_COLLECTION_DISPLAY_TAG ? PRIORITY_COLLECTION_SOURCE_TAG : item));
}

function seoFilterEnabled() {
  return filterReportCapabilities().seo;
}

function collectionFilterEnabled() {
  return filterReportCapabilities().collection;
}

function selectedCollectionStatuses() {
  return [...state.selectedCollectionStatuses];
}

function selectedSeoStatuses() {
  return [...state.selectedSeoStatuses];
}

function updateCollectionStatusHiddenValue() {
  const input = qs("collection_status");
  if (input) input.value = state.selectedCollectionStatuses.join(",");
}

function updateSeoStatusHiddenValue() {
  const input = qs("seo_status");
  if (input) input.value = state.selectedSeoStatuses.join(",");
}

function updateCollectionStatusToggle() {
  const selected = selectedCollectionStatuses();
  const toggle = qs("collectionStatusToggle");
  if (!toggle) return;
  if (!selected.length) {
    toggle.textContent = "Все";
  } else if (selected.length === 1) {
    toggle.textContent = collectionStatusLabels[selected[0]] || selected[0];
  } else {
    toggle.textContent = `Выбрано: ${selected.length}`;
  }
  updateCollectionStatusHiddenValue();
}

function updateSeoStatusToggle() {
  const selected = selectedSeoStatuses();
  const toggle = qs("seoStatusToggle");
  if (!toggle) return;
  if (!selected.length) {
    toggle.textContent = "Все";
  } else if (selected.length === 1) {
    toggle.textContent = selected[0] === SEO_STATUS_NO_TAG ? "Без тега" : selected[0];
  } else {
    toggle.textContent = `Выбрано тегов: ${selected.length}`;
  }
  updateSeoStatusHiddenValue();
}

function setSelectedCollectionStatuses(values, keepUnknown = state.collectionStatusValues.length === 0) {
  const available = new Set([COLLECTION_NO_TAG, ...state.collectionStatusValues]);
  const selected = [];
  normalizeCollectionStatusPayload(values).forEach((value) => {
    if (!available.has(value) && !keepUnknown) return;
    if (!selected.includes(value)) selected.push(value);
  });
  state.selectedCollectionStatuses = selected;
  renderCollectionStatusOptions();
  updateCollectionStatusToggle();
}

function setSelectedSeoStatuses(values, keepUnknown = state.seoStatusValues.length === 0) {
  const available = new Set([SEO_STATUS_NO_TAG, ...state.seoStatusValues]);
  const selected = [];
  normalizeSeoStatusPayload(values).forEach((value) => {
    if (!available.has(value) && !keepUnknown) return;
    if (!selected.includes(value)) selected.push(value);
  });
  state.selectedSeoStatuses = selected;
  renderSeoStatusOptions();
  updateSeoStatusToggle();
}

function setCollectionStatusOptions(values, selected = selectedCollectionStatuses()) {
  state.collectionStatusValues = [...new Set(values || [])].filter(Boolean);
  setSelectedCollectionStatuses(selected, false);
}

function setCollectionStatusLabels(labels) {
  if (!labels || typeof labels !== "object") return;
  Object.entries(labels).forEach(([value, label]) => {
    if (value && label) collectionStatusLabels[value] = label;
  });
}

function setSeoStatusOptions(values, selected = selectedSeoStatuses()) {
  state.seoStatusValues = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  setSelectedSeoStatuses(selected, false);
}

function renderCollectionStatusOptions() {
  const menu = qs("collectionStatusMenu");
  if (!menu) return;
  const selected = new Set(selectedCollectionStatuses());
  const options = [
    { value: COLLECTION_NO_TAG, label: "Без тега" },
    ...state.collectionStatusValues.map((value) => ({ value, label: collectionStatusLabels[value] || value })),
  ];
  menu.innerHTML = `
    <label class="dropdown-option">
      <input type="checkbox" value="" data-all="true" ${selected.size ? "" : "checked"} />
      <span>Все</span>
    </label>
    ${options.map((option) => `
      <label class="dropdown-option">
        <input type="checkbox" value="${escapeHtml(option.value)}" ${selected.has(option.value) ? "checked" : ""} />
        <span>${escapeHtml(option.label)}</span>
      </label>
    `).join("")}
  `;
}

function renderSeoStatusOptions() {
  const menu = qs("seoStatusMenu");
  if (!menu) return;
  const selected = new Set(selectedSeoStatuses());
  const options = [
    { value: SEO_STATUS_NO_TAG, label: "Без тега" },
    ...state.seoStatusValues.map((value) => ({ value, label: value })),
  ];
  menu.innerHTML = `
    <label class="dropdown-option">
      <input type="checkbox" value="" data-all="true" ${selected.size ? "" : "checked"} />
      <span>Все</span>
    </label>
    ${options.map((option) => `
      <label class="dropdown-option">
        <input type="checkbox" value="${escapeHtml(option.value)}" ${selected.has(option.value) ? "checked" : ""} />
        <span>${escapeHtml(option.label)}</span>
      </label>
    `).join("")}
  `;
}

function renderCategoryOptions(filterText = "") {
  const selected = new Set(state.selectedCategories);
  const query = filterText.trim().toLowerCase();
  const options = state.categoryNames
    .filter((value) => !query || String(value).toLowerCase().includes(query));
  const menu = qs("categoryOptions");
  if (!menu) return;
  const allLabel = state.dashboard === "mediaAdv" ? "Все форматы" : "Все категории";
  menu.innerHTML = `
    <label class="dropdown-option">
      <input type="checkbox" value="" data-all="true" ${selected.size ? "" : "checked"} />
      <span>${allLabel}</span>
    </label>
    ${options
      .map(
        (value) => `
          <label class="dropdown-option">
            <input type="checkbox" value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""} />
            <span>${escapeHtml(value)}</span>
          </label>
        `
      )
      .join("")}
  `;
}

function fillCategorySelect(values) {
  const menu = qs("categoryMenu");
  state.categoryNames = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  state.selectedCategories = state.selectedCategories.filter((value) => state.categoryNames.includes(value));
  menu.innerHTML = `
    <input id="categorySearch" class="dropdown-search" type="search" placeholder="Поиск категории" />
    <div id="categoryOptions"></div>
  `;
  renderCategoryOptions("");
  menu.scrollTop = 0;
}

function renderProductOptions(filterText = "") {
  const query = filterText.trim().toLowerCase();
  const options = state.productNames
    .filter((value) => !query || String(value).toLowerCase().includes(query))
    .slice(0, 120);
  qs("productOptions").innerHTML = `
    <button class="product-option" type="button" data-value="">Все товары</button>
    ${options.map((value) => `<button class="product-option" type="button" data-value="${escapeHtml(value)}">${escapeHtml(value)}</button>`).join("")}
  `;
}

async function loadFunnelProductOptions(filterText = "") {
  const requestId = ++productOptionsRequestId;
  const params = new URLSearchParams();
  params.set("client", currentClient());
  params.set("dashboard", state.dashboard);
  params.set("marketplace", qs("marketplace").value || "ozon");
  selectedValues("category").forEach((value) => params.append("categories", value));
  mappingFilterIds.forEach((id) => {
    const value = qs(id)?.value?.trim();
    if (value) params.append(id, value);
  });
  ozonProductAttributeFilterIds.forEach((id) => {
    const value = qs(id)?.value?.trim();
    if (value) params.append(id, value);
  });
  selectedCollectionStatuses().forEach((value) => params.append("collection_status", value));
  selectedSeoStatuses().forEach((value) => params.append("seo_status", value));
  appendSportmasterFilterParams(params);
  wbEntranceFilterIds.forEach((id) => { if (qs(id)?.value) params.set(id, qs(id).value); });
  if (["wbSearchQueries", "wbEntrance", "inventoryHistory"].includes(state.dashboard)) {
    if (qs("date_from")?.value) params.set("date_from", qs("date_from").value);
    if (qs("date_to")?.value) params.set("date_to", qs("date_to").value);
  }
  let query = filterText.trim();
  if (state.dashboard === "wbSearchQueries") {
    const selectedPrefix = query.split("·", 1)[0].trim();
    if (/^WB \d+$/.test(selectedPrefix)) query = selectedPrefix.slice(3);
  }
  if (query) params.set("q", query);
  const endpoint = state.dashboard === "inventoryHistory" ? "/api/inventory-history-products" : state.dashboard === "wbSearchQueries" ? "/api/wb-search-query-products" : state.dashboard === "wbEntrance" ? "/api/wb-entrance-products" : "/api/funnel-products";
  const payload = await getJson(`${endpoint}?${params.toString()}`);
  if (requestId !== productOptionsRequestId) return;
  state.productNames = payload.product_names || [];
  renderProductOptions("");
}

function fillProductSelect(values) {
  state.productNames = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  renderProductOptions(qs("productSearch").value || "");
}

function fillMediaAdvFilterSelect(id, values) {
  const element = qs(id);
  if (!element) return;
  const current = element.value || "";
  const options = [...new Set(values || [])].filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "ru"));
  const labels = {
    media_status: "Все статусы",
    media_segment: "Все сегменты",
    media_campaign_id: "Все кампании",
    media_group_id: "Все группы",
    media_creative_id: "Все креативы",
  };
  element.innerHTML = `<option value="">${escapeHtml(labels[id] || "Все")}</option>`
    + options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  element.value = options.includes(current) ? current : "";
}

function setProductFilter(value) {
  qs("product").value = value || "";
  qs("productSearch").value = value || "";
  qs("productToggle").textContent = value || "Все товары";
}

function addMonths(date, count) {
  return new Date(date.getFullYear(), date.getMonth() + count, 1);
}

function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

function monthSelectHtml(date, side) {
  const monthOptions = monthNames.map((name, index) => `<option value="${index}" ${index === date.getMonth() ? "selected" : ""}>${name}</option>`).join("");
  const year = date.getFullYear();
  const yearOptions = Array.from({ length: 9 }, (_, index) => year - 4 + index)
    .map((item) => `<option value="${item}" ${item === year ? "selected" : ""}>${item}</option>`)
    .join("");
  return `
    <div class="calendar-head">
      <select class="calendar-month" data-side="${side}">${monthOptions}</select>
      <select class="calendar-year" data-side="${side}">${yearOptions}</select>
    </div>
  `;
}

function renderMonth(date, side) {
  const year = date.getFullYear();
  const month = date.getMonth();
  const firstDay = (new Date(year, month, 1).getDay() + 6) % 7;
  const selectedFrom = draftDateFrom;
  const selectedTo = draftDateTo;
  const cells = [];
  for (let i = 0; i < firstDay; i += 1) cells.push('<span class="calendar-empty"></span>');
  for (let day = 1; day <= daysInMonth(year, month); day += 1) {
    const iso = toIsoDate(new Date(year, month, day));
    const inRange = selectedFrom && selectedTo && iso >= selectedFrom && iso <= selectedTo;
    const isEdge = iso === selectedFrom || iso === selectedTo;
    cells.push(`<button class="calendar-day ${inRange ? "in-range" : ""} ${isEdge ? "range-edge" : ""}" type="button" data-date="${iso}">${day}</button>`);
  }
  return `
    <div class="calendar-month-panel">
      ${monthSelectHtml(date, side)}
      <div class="calendar-weekdays"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Вс</span></div>
      <div class="calendar-grid">${cells.join("")}</div>
    </div>
  `;
}

function setDateRange(from, to, shouldLoad = true) {
  qs("date_from").value = from || "";
  qs("date_to").value = to || "";
  draftDateClickStep = 0;
  updateDateRangeToggle();
  renderDateRangePicker();
  if (shouldLoad) {
    markFiltersDirty();
    refreshFilterOptionsAfterDraftChange();
  }
}

function applyDatePreset(key) {
  const today = parseIsoDate(state.availableDateTo) || new Date();
  const todayIso = toIsoDate(today);
  const yesterdayIso = shiftDate(todayIso, -1);
  if (key === "today") setDraftDateRange(todayIso, todayIso);
  if (key === "yesterday") setDraftDateRange(yesterdayIso, yesterdayIso);
  if (key === "week") setDraftDateRange(shiftDate(todayIso, -6), todayIso);
  if (key === "month") setDraftDateRange(toIsoDate(new Date(today.getFullYear(), today.getMonth(), 1)), todayIso);
  if (key === "prev_month") setDraftDateRange(toIsoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1)), toIsoDate(new Date(today.getFullYear(), today.getMonth(), 0)));
  if (key === "days28" || key === "days30") setDraftDateRange(defaultRecentDateFrom(todayIso), todayIso);
  if (key === "days90") setDraftDateRange(shiftDate(todayIso, -89), todayIso);
}

function setDraftDateRange(from, to) {
  draftDateFrom = from || "";
  draftDateTo = to || "";
  draftDateClickStep = draftDateFrom && draftDateTo ? 2 : (draftDateFrom ? 1 : 0);
  renderDateRangePicker();
}

function renderDateRangePicker() {
  qs("dateRangeMenu").classList.remove("planfact-month-menu");
  const fromDate = parseIsoDate(qs("date_from").value);
  const toDate = parseIsoDate(qs("date_to").value);
  datePickerLeftMonth = datePickerLeftMonth || new Date((fromDate || toDate || new Date()).getFullYear(), (fromDate || toDate || new Date()).getMonth(), 1);
  qs("dateRangeMenu").innerHTML = `
    <div class="date-presets">
      <button type="button" data-preset="today">Сегодня</button>
      <button type="button" data-preset="yesterday">Вчера</button>
      <button type="button" data-preset="week">Неделя</button>
      <button type="button" data-preset="month">Месяц</button>
      <button type="button" data-preset="prev_month">Прошлый месяц</button>
      <button type="button" data-preset="days28">28 дней</button>
      <button type="button" data-preset="days90">90 дней</button>
    </div>
    <div class="calendar-panels">
      ${renderMonth(datePickerLeftMonth, "left")}
      ${renderMonth(addMonths(datePickerLeftMonth, 1), "right")}
    </div>
    <div class="date-range-actions">
      <span>${draftDateFrom && draftDateTo ? `${formatRuDate(draftDateFrom)} - ${formatRuDate(draftDateTo)}` : "Выберите дату начала и дату окончания"}</span>
      <button id="dateRangeApply" type="button" ${draftDateFrom && draftDateTo ? "" : "disabled"}>Применить</button>
    </div>
  `;
}

function renderPlanFactMonthPicker() {
  qs("dateRangeMenu").classList.add("planfact-month-menu");
  const from = qs("date_from").value;
  const months = state.planfactMonths.length
    ? state.planfactMonths
    : [{ month: from || state.availableDateTo, date_from: from || state.availableDateTo, date_to: qs("date_to").value || monthEndIso(from || state.availableDateTo) }];
  qs("dateRangeMenu").innerHTML = `
    <div class="planfact-month-picker">
      ${months.map((item) => {
        const active = item.date_from === qs("date_from").value && item.date_to === qs("date_to").value;
        return `<button type="button" class="${active ? "active" : ""}" data-planfact-month="${escapeHtml(item.month)}" data-from="${escapeHtml(item.date_from)}" data-to="${escapeHtml(item.date_to)}">${escapeHtml(formatMonthName(item.month))}</button>`;
      }).join("")}
    </div>
  `;
}

function handleCalendarDateClick(isoDate) {
  if (draftDateClickStep !== 1) {
    draftDateFrom = isoDate;
    draftDateTo = "";
    draftDateClickStep = 1;
    renderDateRangePicker();
    return;
  }
  draftDateTo = isoDate;
  if (draftDateTo < draftDateFrom) {
    [draftDateFrom, draftDateTo] = [draftDateTo, draftDateFrom];
  }
  draftDateClickStep = 2;
  renderDateRangePicker();
}

function selectedValues(id) {
  if (id === "category") {
    return [...state.selectedCategories];
  }
  return Array.from(qs(id).selectedOptions)
    .map((option) => option.value)
    .filter(Boolean);
}

function setSelectedValues(id, values) {
  if (id === "category") {
    const wanted = new Set(values.filter(Boolean));
    state.selectedCategories = state.categoryNames.filter((value) => wanted.has(value));
    const inputs = Array.from(qs("categoryMenu").querySelectorAll('input[type="checkbox"]'));
    inputs.forEach((input) => {
      input.checked = input.dataset.all === "true" ? wanted.size === 0 : wanted.has(input.value);
    });
    renderCategoryOptions(qs("categorySearch")?.value || "");
    updateCategoryToggle();
    return;
  }
  const wanted = new Set(values.filter(Boolean));
  Array.from(qs(id).options).forEach((option) => {
    option.selected = wanted.has(option.value);
  });
}

function updateCategoryToggle() {
  const selected = selectedValues("category");
  const marketplaceLabel = qs("marketplace").selectedOptions[0]?.textContent || "";
  if (!selected.length) {
    qs("categoryToggle").textContent = "Все категории";
  } else if (selected.length === 1) {
    qs("categoryToggle").textContent = selected[0];
  } else {
    qs("categoryToggle").textContent = `Выбрано категорий: ${selected.length}`;
  }
}

function clearCategorySelection() {
  const menu = qs("categoryMenu");
  if (!menu) return;
  Array.from(menu.querySelectorAll('input[type="checkbox"]')).forEach((input) => {
    input.checked = input.dataset.all === "true";
  });
  state.selectedCategories = [];
  qs("categoryToggle").textContent = "Все категории";
}

function resetCategoryMenu() {
  state.selectedCategories = [];
  qs("categoryMenu").innerHTML = `
    <input id="categorySearch" class="dropdown-search" type="search" placeholder="Поиск категории" />
    <div id="categoryOptions">
    <label class="dropdown-option">
      <input type="checkbox" value="" data-all="true" checked />
      <span>Все категории</span>
    </label>
    </div>
  `;
  qs("categoryMenu").classList.add("hidden");
  qs("categoryToggle").textContent = "Все категории";
}

const originalFillCategorySelect = fillCategorySelect;
fillCategorySelect = function fillCategorySelectPatched(values) {
  originalFillCategorySelect(values);
  qs("categoryMenu").scrollTop = 0;
};

const originalUpdateCategoryToggle = updateCategoryToggle;
updateCategoryToggle = function updateCategoryTogglePatched() {
  originalUpdateCategoryToggle();
  if (!selectedValues("category").length) {
    if (state.dashboard === "mediaAdv") {
      qs("categoryToggle").textContent = "Все форматы";
      return;
    }
    const marketplaceLabel = qs("marketplace").selectedOptions[0]?.textContent || "";
    if (marketplaceLabel) {
      qs("categoryToggle").textContent = `Все категории (${marketplaceLabel})`;
    }
  }
};

const originalClearCategorySelection = clearCategorySelection;
clearCategorySelection = function clearCategorySelectionPatched() {
  originalClearCategorySelection();
  qs("categoryMenu").scrollTop = 0;
  updateCategoryToggle();
};

const originalResetCategoryMenu = resetCategoryMenu;
resetCategoryMenu = function resetCategoryMenuPatched() {
  originalResetCategoryMenu();
  qs("categoryMenu").scrollTop = 0;
  updateCategoryToggle();
};

function staticRows() {
  return (window.DASHBOARD_DATA && window.DASHBOARD_DATA.rows) || [];
}

function filteredStaticRows() {
  const categories = selectedValues("category");
  const abcOrders = qs("abc_orders").value;
  const abcSales = qs("abc_sales").value;
  const abcStock = qs("abc_stock").value;
  const abcCombined = qs("abc_combined").value;

  return staticRows().filter((row) => {
    const categoryName = String(row.category_name || "").toLowerCase();
    return (
      (!categories.length || categories.includes(row.category_name)) &&
      (!abcOrders || row.abc_orders === abcOrders) &&
      (!abcSales || row.abc_sales === abcSales) &&
      (!abcStock || row.abc_stock === abcStock) &&
      (!abcCombined || row.abc_combined === abcCombined)
    );
  });
}

function sortedRows(rows) {
  const col = state.sortCol;
  const dir = state.sortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = a[col];
    const bv = b[col];
    if (typeof av === "number" || typeof bv === "number") {
      return ((Number(av || 0) - Number(bv || 0)) || String(a.category_name || "").localeCompare(String(b.category_name || ""), "ru")) * dir;
    }
    return String(av || "").localeCompare(String(bv || ""), "ru") * dir;
  });
}

function pagedStaticRows(rows) {
  const pageSize = Number(qs("limit").value || 50);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  state.page = Math.min(state.page, totalPages);
  state.totalPages = totalPages;
  state.total = rows.length;
  const start = (state.page - 1) * pageSize;
  return sortedRows(rows).slice(start, start + pageSize);
}

function summarizeRows(rows) {
  const summary = rows.reduce(
    (acc, row) => {
      acc.categories += 1;
      acc.sku_count += Number(row.sku_count || 0);
      acc.total_stock_qty += Number(row.total_stock_qty || 0);
      acc.category_attribute_count += Number(row.category_attribute_count || 0);
      acc.zakazano_sht += Number(row.zakazano_sht || 0);
      acc.zakazano_rub += Number(row.zakazano_rub || 0);
      return acc;
    },
    {
      categories: 0,
      sku_count: 0,
      total_stock_qty: 0,
      category_attribute_count: 0,
      zakazano_sht: 0,
      zakazano_rub: 0,
    }
  );
  return summary;
}

async function loadFilters(options = {}) {
  if (state.dashboard === "admin") return;
  if (!currentClient()) return;
  if (["salesPlanning", "mediaPlan", "profitLoss", "unitEconomics", "commercialRadar"].includes(state.dashboard) || isAvitoDashboard()) return;
  if (currentClient() === "boiron" && state.dashboard === "adv") setMarketplaceValue("ozon");
  if (["wbSearchQueries", "wbEntrance"].includes(state.dashboard)) setMarketplaceValue("wb");
  const preserveCategories = options.preserveCategories ?? true;
  const selectedCategories = preserveCategories
    ? ((state.dashboard === "sku" || state.dashboard === "product") && state.drillCategory
        ? [state.drillCategory]
        : selectedValues("category"))
    : [];
  const selectedMappingFilters = Object.fromEntries(mappingFilterIds.map((id) => [id, qs(id)?.value || ""]));
  const selectedOzonProductAttributeFilters = Object.fromEntries(ozonProductAttributeFilterIds.map((id) => [id, qs(id)?.value || ""]));
  const selectedAdvCampaign = qs("adv_campaign_id")?.value || state.pendingAdvCampaignId || "";
  const selectedSportmasterFilters = Object.fromEntries(sportmasterFilterIds.map((id) => [id, selectedSportmasterValues(id)]));
  const selectedBoironBrands = selectedBoironBrandValues();
  const selectedCollectionStatus = selectedCollectionStatuses();
  const selectedSeoStatus = selectedSeoStatuses();
  const selectedQueryClassifications = Object.fromEntries(wbQueryClassificationFilterIds.map((id) => [id, qs(id)?.value || ""]));
  const selectedEntranceFilters = Object.fromEntries(wbEntranceFilterIds.map((id) => [id, qs(id)?.value || ""]));

  if (state.mode === "static") {
    const rows = staticRows();
    qs("marketplace").innerHTML = '<option value="ozon">Ozon</option>';
    fillCategorySelect(rows.map((row) => row.category_name));
    setSelectedValues("category", selectedCategories);
    fillSelect("abc_orders", rows.map((row) => row.abc_orders));
    fillSelect("abc_sales", rows.map((row) => row.abc_sales));
    fillSelect("abc_stock", rows.map((row) => row.abc_stock));
    fillSelect("abc_combined", rows.map((row) => row.abc_combined));
    setCollectionStatusOptions([]);
    setSeoStatusOptions([]);
    ozonProductAttributeFilterIds.forEach((id) => {
      fillSelect(id, []);
    });
    fillSelect("adv_campaign_id", []);
    sportmasterFilterIds.forEach((id) => {
      setSportmasterFilterOptions(id, []);
    });
    setBoironBrandFilterOptions([]);
    return;
  }

  const filterParams = new URLSearchParams({
    client: currentClient(),
    marketplace: qs("marketplace").value || "ozon",
    dashboard: state.dashboard,
  });
  if (options.includeCategoryLevel === true && (state.dashboard === "abc" || state.dashboard === "product")) {
    filterParams.set("category_level", selectedCategoryLevel());
  }
  if (state.dashboard === "mediaAdv" && (qs("marketplace").value || "ozon") === "wb") {
    filterParams.set("media_level", qs("media_level")?.value || "campaign");
  }
  if (state.dashboard === "abc" || state.dashboard === "product" || state.dashboard === "sku" || state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "seoMonitoring" || state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    selectedCategories.forEach((value) => filterParams.append("categories", value));
  }
  wbEntranceFilterIds.forEach((id) => { if (qs(id)?.value) filterParams.set(id, qs(id).value); });
  appendSportmasterFilterParams(filterParams);
  appendBoironBrandFilterParams(filterParams);
  const filters = await getJson(`/api/filters?${filterParams.toString()}`);
  if (filters.marketplaces) {
    const current = qs("marketplace").value || filters.marketplace || "ozon";
    qs("marketplace").innerHTML = filters.marketplaces
      .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`)
      .join("");
    qs("marketplace").value = current;
    if (!qs("marketplace").value) qs("marketplace").value = filters.marketplace || filters.marketplaces[0]?.id || "ozon";
  }
  if (state.dashboard === "abc" || state.dashboard === "product") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    applyDefaultAbcDateRange(filters);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillProductSelect([]);
  } else if (state.dashboard === "sku") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    applyDefaultSkuDateRange(filters);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillProductSelect([]);
  } else if (state.dashboard === "adv") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    applyDefaultAdvDateRange(filters);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillProductSelect(filters.product_names || []);
    fillSelect("adv_campaign_id", filters.adv_campaign_id_values || filters.adv_campaign_ids || []);
    restoreFilterValue("adv_campaign_id", selectedAdvCampaign);
    state.pendingAdvCampaignId = qs("adv_campaign_id")?.value || "";
  } else if (state.dashboard === "mediaAdv") {
    if (filters.media_level && qs("media_level")) qs("media_level").value = filters.media_level;
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    applyDefaultMediaAdvDateRange(filters);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillCategorySelect(filters.category_names || []);
    setSelectedValues("category", selectedCategories);
    fillMediaAdvFilterSelect("media_status", filters.media_statuses || []);
    fillMediaAdvFilterSelect("media_segment", filters.media_segments || []);
    fillMediaAdvFilterSelect("media_campaign_id", filters.media_campaign_ids || []);
    fillMediaAdvFilterSelect("media_group_id", filters.media_group_ids || []);
    fillMediaAdvFilterSelect("media_creative_id", filters.media_creative_ids || []);
    fillProductSelect(currentMarketplace() === "wb" ? [] : (filters.product_names || []));
  } else if (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    if (state.dashboard === "weeklyDynamics") applyDefaultWeeklyDynamicsDateRange(filters);
    else if (state.dashboard === "funnel") applyDefaultFunnelDateRange(filters);
    else {
      const currentDateFrom = qs("date_from").value || "";
      const currentDateTo = qs("date_to").value || "";
      const rangeIsOutsideSource = dateRangeFullyOutsideSource(
        currentDateFrom,
        currentDateTo,
        filters.date_from || "",
        filters.date_to || "",
      );
      if (rangeIsOutsideSource || (!currentDateFrom && !currentDateTo)) {
        if (rangeIsOutsideSource) {
          qs("date_from").value = "";
          qs("date_to").value = "";
        }
        state.inventoryHistoryDateRangeInitialized = false;
        applyDefaultInventoryHistoryDateRange(filters);
      } else {
        qs("date_from").value = currentDateFrom || filters.date_from || "";
        qs("date_to").value = currentDateTo || filters.date_to || "";
        state.inventoryHistoryDateRangeInitialized = true;
      }
    }
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillCategorySelect(filters.category_names || []);
    setSelectedValues("category", selectedCategories);
    fillProductSelect(filters.product_names || []);
  } else if (state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    const currentDateFrom = qs("date_from").value || "";
    const currentDateTo = qs("date_to").value || "";
    const rangeIsOutsideSource = Boolean(
      (currentDateTo && filters.date_from && currentDateTo < filters.date_from)
      || (currentDateFrom && filters.date_to && currentDateFrom > filters.date_to)
    );
    qs("date_from").value = rangeIsOutsideSource ? (filters.date_from || "") : (currentDateFrom || filters.date_from || "");
    qs("date_to").value = rangeIsOutsideSource ? (filters.date_to || "") : (currentDateTo || filters.date_to || "");
    fillProductSelect(filters.product_names || []);
    setMarketplaceValue("wb");
    wbEntranceFilterIds.forEach((id) => {
      fillSelect(id, filters[`${id}_values`] || []);
      if (selectedEntranceFilters[id] && qs(id)) qs(id).value = selectedEntranceFilters[id];
    });
    wbQueryClassificationFilterIds.forEach((id) => {
      fillLabeledSelect(
        id,
        filters[`${id}_values`] || [],
        filters[`${id}_labels`] || {},
        selectedQueryClassifications[id],
      );
    });
  } else if (state.dashboard === "seoMonitoring") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    applyDefaultSeoMonitoringDateRange(filters);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillProductSelect(filters.product_names || []);
  } else if (state.dashboard === "planfact") {
    state.availableDateFrom = filters.date_from || "";
    state.availableDateTo = filters.date_to || "";
    state.planfactMonths = planFactSelectableMonths(filters.months || []);
    applyDefaultPlanFactDateRange(filters);
    normalizePlanFactMonthRange(state.planfactMonths);
    qs("date_from").value = qs("date_from").value || filters.date_from || "";
    qs("date_to").value = qs("date_to").value || filters.date_to || "";
    fillProductSelect([]);
  }
  if (collectionFilterEnabled()) {
    setCollectionStatusLabels(filters.collection_status_labels);
    setCollectionStatusOptions(filters.collection_status_values || [], selectedCollectionStatus);
  } else {
    setCollectionStatusOptions([]);
  }
  if (seoFilterEnabled()) {
    setSeoStatusOptions(filters.seo_status_values || [], selectedSeoStatus);
  } else {
    setSeoStatusOptions([]);
  }
  sportmasterFilterIds.forEach((id) => {
    setSportmasterFilterOptions(id, filters[`${id}_values`] || [], selectedSportmasterFilters[id]);
  });
  setBoironBrandFilterOptions(filters.brand_names || [], selectedBoironBrands);
  updateDateRangeToggle();
  fillCategorySelect(filters.category_names);
  setSelectedValues("category", selectedCategories);
  fillSelect("abc_orders", filters.abc_orders);
  fillSelect("abc_sales", filters.abc_sales);
  fillSelect("abc_stock", filters.abc_stock);
  fillSelect("abc_combined", filters.abc_combined);
  mappingFilterIds.forEach((id) => {
    const element = qs(id);
    if (!element) return;
    if (element.tagName === "SELECT") {
      fillSelect(id, filters[`${id}_values`] || []);
    }
    restoreFilterValue(id, selectedMappingFilters[id]);
  });
  ozonProductAttributeFilterIds.forEach((id) => {
    const element = qs(id);
    if (!element) return;
    fillSelect(id, filters[`${id}_values`] || []);
    restoreFilterValue(id, selectedOzonProductAttributeFilters[id]);
  });
  updateFilterVisibility();
}

function setKpiItems(items) {
  const container = document.querySelector(".kpis");
  container.classList.remove("funnel-kpis");
  container.classList.remove("wb-search-kpis");
  container.classList.toggle("planfact-kpis", state.dashboard === "planfact");
  container.classList.toggle("media-adv-kpis", state.dashboard === "mediaAdv");
  container.innerHTML = items
    .map((item) => `
      <article>
        <span>${escapeHtml(item.label)}</span>
        <strong>${escapeHtml(item.value)}</strong>
      </article>
    `)
    .join("");
  container.classList.toggle("adv-kpis", state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "planfact" || state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance");
}

function setFunnelKpiGroups(numberItems, pctItems) {
  const container = document.querySelector(".kpis");
  container.classList.remove("adv-kpis");
  container.classList.remove("planfact-kpis");
  container.classList.remove("media-adv-kpis");
  container.classList.remove("wb-search-kpis");
  container.classList.add("funnel-kpis");
  const renderTile = (item) => `
    <article>
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </article>
  `;
  container.innerHTML = `
    <section class="kpi-group kpi-group-numbers">
      <div class="kpi-tile-grid">${numberItems.map(renderTile).join("")}</div>
    </section>
    <section class="kpi-group kpi-group-pct">
      <div class="kpi-tile-grid">${pctItems.map(renderTile).join("")}</div>
    </section>
  `;
}

function formatWbMetricValue(item, rawValue) {
  const value = Number(rawValue || 0);
  const digits = Number(item.digits || 0);
  if (item.format === "compact") return formatCompactNumber(value);
  if (item.format === "pct") return `${formatNumber(value, digits)}%`;
  if (item.format === "rub") return `${formatNumber(value, digits)} ₽`;
  if (item.format === "rating") return `${formatNumber(value, digits)}${item.suffix || ""}`;
  return formatNumber(value, digits);
}

function formatWbMetricChange(item) {
  if (item.change_value === null || item.change_value === undefined) return "динамика —";
  const value = Number(item.change_value || 0);
  const prefix = value > 0 ? "+" : "";
  return `Δ ${prefix}${formatNumber(value, 1)}${item.change_suffix || ""}`;
}

function setWbSearchKpiGroups(groups) {
  const container = document.querySelector(".kpis");
  container.classList.remove("adv-kpis");
  container.classList.remove("planfact-kpis");
  container.classList.remove("media-adv-kpis");
  container.classList.remove("funnel-kpis");
  container.classList.add("wb-search-kpis");
  container.innerHTML = (groups || []).map((group) => `
    <section class="wb-kpi-section wb-kpi-section-${escapeHtml(group.key || "group")}">
      <h2>${escapeHtml(group.title || "Метрики")}</h2>
      <div class="wb-kpi-grid" style="--wb-kpi-columns:${Math.min(Math.max((group.items || []).length, 1), 8)}">
        ${(group.items || []).map((item) => {
          const hasPrevious = Object.prototype.hasOwnProperty.call(item, "previous");
          const changeClass = Number(item.change_value || 0) > 0
            ? "is-positive"
            : (Number(item.change_value || 0) < 0 ? "is-negative" : "is-neutral");
          return `
            <article>
              <span>${escapeHtml(item.label || item.key || "Метрика")}</span>
              <strong>${escapeHtml(formatWbMetricValue(item, item.value))}</strong>
              ${hasPrevious ? `<small><b>Пред.</b> ${escapeHtml(formatWbMetricValue(item, item.previous))}<em class="${changeClass}">${escapeHtml(formatWbMetricChange(item))}</em></small>` : ""}
            </article>
          `;
        }).join("")}
      </div>
    </section>
  `).join("");
}

function setPlanFactKpiGroups(sections) {
  const container = document.querySelector(".kpis");
  container.classList.remove("adv-kpis");
  container.classList.remove("funnel-kpis");
  container.classList.remove("wb-search-kpis");
  container.classList.remove("media-adv-kpis");
  container.classList.add("planfact-kpis");
  const renderTile = (item) => `
    <article tabindex="0" aria-label="${escapeHtml(item.label)}: ${escapeHtml(item.value)}" data-tooltip="${escapeHtml(item.label)}">
      <span>${escapeHtml(item.shortLabel || item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </article>
  `;
  const renderGroup = (group) => {
    const tileCount = Math.max(1, Math.min(4, group.items.length));
    return `
      <section class="planfact-runrate-group" aria-label="${escapeHtml(group.title)}">
        <h3 title="${escapeHtml(group.title)}">${escapeHtml(group.shortTitle || group.title)}</h3>
        <div class="planfact-runrate-tiles" style="grid-template-columns: repeat(${tileCount}, minmax(0, 1fr))">${group.items.map(renderTile).join("")}</div>
      </section>
    `;
  };
  container.innerHTML = `
    ${sections.map((section) => `
      <section class="planfact-kpi-section">
        <h2>${escapeHtml(section.title)}</h2>
        <div class="planfact-runrate-row">
          ${section.groups.map(renderGroup).join("")}
        </div>
      </section>
    `).join("")}
  `;
}

function renderKpis(summary, dashboardPayload = {}) {
  if (state.dashboard === "seoMonitoring") {
    const selectedCategories = selectedValues("category");
    setKpiItems([
      { label: "Маркетплейс", value: currentMarketplaceLabel() },
      { label: "Категорий выбрано", value: formatNumber(selectedCategories.length) },
      { label: "Товар", value: qs("product")?.value || "Все товары" },
      { label: "Период", value: qs("date_from")?.value && qs("date_to")?.value ? `${qs("date_from").value} - ${qs("date_to").value}` : "Не выбран" },
      { label: "Статус", value: "Фильтры готовы" },
    ]);
  } else if (state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    if (dashboardPayload.metric_groups?.length) {
      setWbSearchKpiGroups(dashboardPayload.metric_groups);
    } else {
      setKpiItems([{ label: "Метрики", value: "Нет данных" }]);
    }
  } else if (state.dashboard === "inventoryHistory") {
    setKpiItems([
      { label: "Остаток на дату", value: formatNumber(summary.total_stock_qty) },
      { label: `Δ к прошлому снимку${summary.snapshot_gap_days ? ` · ${formatNumber(summary.snapshot_gap_days)} дн.` : ""}`, value: formatNumber(summary.stock_change_qty) },
      { label: "Покрытие спроса", value: summary.days_cover == null ? "—" : `${formatNumber(summary.days_cover, 1)} дн.` },
      { label: "Готовится к продаже", value: formatNumber(summary.stock_preparing_qty) },
      { label: "Заявки на поставку", value: summary.in_supply_orders_qty == null ? "нет данных" : formatNumber(summary.in_supply_orders_qty) },
      { label: "В пути", value: summary.in_transit_supply_qty == null ? "нет данных" : formatNumber(summary.in_transit_supply_qty) },
      { label: "OOS всего", value: formatNumber(summary.oos_sku_count) },
      { label: "OOS: снимок / расчётно", value: `${formatNumber(summary.confirmed_oos_sku_count)} / ${formatNumber(summary.inferred_oos_sku_count)}` },
      { label: "Дефицит <14 дней", value: formatNumber(summary.shortage_sku_count) },
      { label: "Излишек >60 дней", value: formatNumber(summary.excess_sku_count) },
      { label: "Без продаж", value: formatNumber(summary.no_sales_sku_count) },
      { label: "SKU: мониторинг / API-снимок", value: `${formatNumber(summary.monitoring_sku_count)} / ${formatNumber(summary.snapshot_sku_count)}` },
    ]);
  } else if (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") {
    const numberItems = [
      { key: "impressions_total", label: "Показы всего" },
      { key: "impressions_search_catalog", label: "Показы поиск/каталог" },
      { key: "card_visits", label: "Переходы в карточку" },
      { key: "cart_adds", label: "Корзины" },
      { key: "ordered_units", label: "Заказано, шт" },
      { key: "ordered_amount_rub", label: "Заказано, руб" },
      { key: "bought_units", label: "Выкуплено, шт" },
      { key: "bought_amount_rub", label: "Выкупы минус возвраты, руб" },
      { key: "favorites_adds", label: "В отложенные" },
      { key: "cancelled_units", label: "Отменено, шт" },
      { key: "cancelled_amount_rub", label: "Отменено, руб" },
      { key: "wb_club_ordered_units", label: "Заказы WB Клуб, шт" },
      { key: "wb_club_bought_units", label: "Выкуплено WB Клуб, шт" },
      { key: "adv_impressions", label: "Рекламные показы" },
      { key: "adv_clicks", label: "Рекламные клики" },
      { key: "adv_cart_adds", label: "Рекламные корзины" },
      { key: "adv_orders", label: "Рекламные заказы" },
      { key: "adv_expense_rub", label: "Расходы" },
      { key: "organic_impressions", label: "Орг. показы" },
      { key: "organic_card_visits", label: "Орг. переходы" },
      { key: "organic_cart_adds", label: "Орг. корзины" },
      { key: "organic_orders", label: "Орг. заказы" },
    ].filter((item) => funnelMetricIsAvailable(item.key))
      .map((item) => ({ label: item.label, value: formatCompactNumber(summary[item.key]) }));
    const pctItems = [
      { key: "search_to_card_visit_pct", label: "Поиск -> карточка" },
      { key: "total_impression_to_card_visit_pct", label: "Показы -> карточка" },
      { key: "card_visit_to_cart_pct", label: "Карточка -> корзина" },
      { key: "cart_to_order_pct", label: "Корзина -> заказ" },
      { key: "card_visit_to_order_pct", label: "Карточка -> заказ" },
      { key: "favorite_to_card_visit_pct", label: "Карточка -> отложенные" },
      { key: "buyout_pct", label: "Заказ -> выкуп" },
      { key: "cancellation_pct", label: "Заказ -> отмена" },
      { key: "wb_club_order_share_pct", label: "Доля заказов WB Клуб" },
      { key: "adv_ctr_pct", label: "Рекламный CTR" },
      { key: "adv_click_to_order_pct", label: "Реклама: клик -> заказ" },
      { key: "acos_pct", label: "ACOS" },
      { key: "tacos_pct", label: "TACOS" },
    ].filter((item) => funnelMetricIsAvailable(item.key))
      .map((item) => ({ label: item.label, value: `${formatNumber(summary[item.key], 1)}%` }));
    setFunnelKpiGroups(numberItems, pctItems);
  } else if (state.dashboard === "planfact") {
    renderPlanFactKpis(summary);
  } else if (state.dashboard === "adv") {
    const attributionItems = currentMarketplace() === "ozon" && summary.attribution_breakdown_available !== false ? [
      { label: "Продажи — прямая атрибуция, руб", value: formatNumber(summary.direct_orders_amount_rub) },
      { label: "Продажи — косвенная атрибуция, руб", value: formatNumber(summary.indirect_orders_amount_rub) },
      { label: "Заказы — прямая атрибуция, шт", value: formatNumber(summary.direct_orders_qty) },
      { label: "Заказы — косвенная атрибуция, шт", value: formatNumber(summary.indirect_orders_qty) },
      { label: "ДРР — прямая атрибуция", value: `${formatNumber(summary.direct_drr_pct, 1)}%` },
      { label: "ДРР — косвенная атрибуция", value: `${formatNumber(summary.indirect_drr_pct, 1)}%` },
    ] : [];
    setKpiItems([
      { label: "Продажи итого, руб", value: formatNumber(summary.total_orders_amount_rub) },
      { label: "Продажи с рекламы, руб", value: formatNumber(summary.orders_amount_rub) },
      ...attributionItems,
      { label: "Расходы на рекламу, руб", value: formatNumber(summary.expense_rub) },
      { label: "Заказы с рекламы, шт", value: formatNumber(summary.orders_qty) },
      { label: "Заказы итого, шт", value: formatNumber(summary.total_orders_qty) },
      { label: "Рекламные заказы / итого", value: `${formatNumber(summary.adv_orders_to_total_orders_pct, 1)}%` },
      { label: "ДРР рекламный", value: `${formatNumber(summary.drr_pct, 1)}%` },
      { label: "ДРР общий", value: `${formatNumber(summary.total_drr_pct, 1)}%` },
    ]);
  } else if (state.dashboard === "mediaAdv") {
    setKpiItems([
      { label: "Атриб. выручка, руб", value: formatNumber(summary.attributed_revenue_rub) },
      { label: "Выручка post-view, руб", value: formatNumber(summary.post_view_revenue_rub) },
      { label: "Прямая выручка, руб", value: formatNumber(summary.orders_amount_rub) },
      { label: "Расходы, руб", value: formatNumber(summary.expense_rub) },
      { label: "Показы", value: formatCompactNumber(summary.impressions) },
      { label: "Клики", value: formatCompactNumber(summary.clicks) },
      { label: "CTR", value: `${formatNumber(summary.ctr_calc_pct, 2)}%` },
      { label: "ДРР с post-view", value: `${formatNumber(summary.drr_attributed_pct, 1)}%` },
      { label: "ROAS с post-view", value: formatNumber(summary.attributed_roas, 2) },
    ]);
  } else {
    setKpiItems([
      { label: "Категорий", value: formatNumber(summary.categories) },
      { label: "SKU", value: formatNumber(summary.sku_count) },
      { label: "Остаток, шт", value: formatNumber(summary.total_stock_qty) },
      { label: "Заказано, руб", value: formatNumber(summary.zakazano_rub) },
      { label: "Заказано, шт", value: formatNumber(summary.zakazano_sht) },
    ]);
  }
}

function renderSeoMonitoringShell() {
  const selectedCategories = selectedValues("category");
  const marketplace = currentMarketplaceLabel();
  const product = qs("product")?.value || "Все товары";
  const period = qs("date_from")?.value && qs("date_to")?.value
    ? `${qs("date_from").value} - ${qs("date_to").value}`
    : "Не выбран";
  const categoryText = selectedCategories.length ? selectedCategories.join(", ") : "Все категории";
  qs("primaryChartTitle").textContent = "Мониторинг SEO";
  qs("chartMetric").textContent = "выбранный товарный контекст";
  qs("barChart").classList.remove("bar-chart");
  qs("barChart").innerHTML = `
    <div class="empty-chart">
      <h3>Фильтры для SEO-мониторинга готовы</h3>
      <p>Маркетплейс: ${escapeHtml(marketplace)}</p>
      <p>Категории: ${escapeHtml(categoryText)}</p>
      <p>Товар: ${escapeHtml(product)}</p>
      <p>Период: ${escapeHtml(period)}</p>
    </div>
  `;
  qs("secondaryChartTitle").textContent = "Следующий шаг";
  qs("secondaryChartMetric").textContent = "подключение источников SEO";
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    <div class="empty-chart">
      <p>Здесь появятся блоки сбора и проверки SEO-данных по выбранным товарам.</p>
      <p>Как только будут понятны источники и метрики, добавим загрузку данных в этот экран.</p>
    </div>
  `;
}

function seoMonitoringSelectedSet() {
  return new Set((state.seoMonitoringSelectedProducts || []).map((value) => String(value)));
}

function updateSeoMonitoringSelectionMeta() {
  const meta = qs("tableContextMeta");
  if (!meta) return;
  meta.textContent = `Найдено: ${formatNumber(state.total)} · Выбрано: ${formatNumber(seoMonitoringSelectedSet().size)}`;
}

function renderSeoMonitoringTable(columns, rows) {
  const displayColumns = orderedTableColumns(columns);
  state.columns = displayColumns;
  const selectedProducts = seoMonitoringSelectedSet();
  let draggedColumnKey = null;
  let suppressHeaderClick = false;
  qs("tableContext")?.classList.remove("hidden");
  if (qs("tableContextTitle")) qs("tableContextTitle").textContent = "Товары";
  if (qs("tableContextNote")) {
    qs("tableContextNote").textContent = state.seoMonitoringValuationNote || "Остатки в рублях рассчитаны по последней доступной средней цене.";
  }
  qs("exportTableExcel")?.classList.add("hidden");
  updateSeoMonitoringSelectionMeta();

  qs("tableHead").innerHTML = `
    <tr>
      ${displayColumns.map((column) => {
        if (column.key === "selected") {
          return '<th class="seo-select-column">Выбрать</th>';
        }
        const active = column.key === state.sortCol;
        const arrow = active ? (state.sortDir === "asc" ? " ↑" : " ↓") : "";
        const filterActive = state.columnFilters[column.key]?.value;
        return `
          <th class="draggable-column ${active ? "sorted" : ""} ${filterActive ? "filtered" : ""}" draggable="true" data-column="${escapeHtml(column.key)}" title="Перетащите, чтобы поменять порядок столбцов">
            <span class="table-header-label">${escapeHtml(column.label)}${arrow}</span>
            <button class="column-filter-btn ${filterActive ? "active" : ""}" type="button" title="Фильтр по полю ${escapeHtml(column.label)}" aria-label="Фильтр по полю ${escapeHtml(column.label)}" data-filter-column="${escapeHtml(column.key)}"></button>
          </th>
        `;
      }).join("")}
    </tr>
  `;

  qs("tableHead").querySelectorAll("th[data-column]").forEach((th) => {
    th.addEventListener("dragstart", (event) => {
      if (event.target.closest(".column-filter-btn")) {
        event.preventDefault();
        return;
      }
      draggedColumnKey = th.dataset.column;
      th.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedColumnKey);
      }
    });
    th.addEventListener("dragover", (event) => {
      if (!draggedColumnKey || draggedColumnKey === th.dataset.column) return;
      event.preventDefault();
      th.classList.add("drag-over");
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });
    th.addEventListener("dragleave", () => {
      th.classList.remove("drag-over");
    });
    th.addEventListener("drop", (event) => {
      event.preventDefault();
      th.classList.remove("drag-over");
      const fromKey = event.dataTransfer?.getData("text/plain") || draggedColumnKey;
      const toKey = th.dataset.column;
      if (!fromKey || !toKey || fromKey === toKey) return;
      const nextColumns = [...displayColumns];
      const fromIndex = nextColumns.findIndex((column) => column.key === fromKey);
      const toIndex = nextColumns.findIndex((column) => column.key === toKey);
      if (fromIndex < 0 || toIndex < 0) return;
      const [moved] = nextColumns.splice(fromIndex, 1);
      nextColumns.splice(toIndex, 0, moved);
      suppressHeaderClick = true;
      saveTableColumnOrder(nextColumns);
      renderSeoMonitoringTable(columns, rows);
      setTimeout(() => {
        suppressHeaderClick = false;
      }, 0);
    });
    th.addEventListener("dragend", () => {
      draggedColumnKey = null;
      qs("tableHead").querySelectorAll("th").forEach((item) => item.classList.remove("dragging", "drag-over"));
    });
    th.addEventListener("click", (event) => {
      if (suppressHeaderClick || event.target.closest(".column-filter-btn")) return;
      const key = th.dataset.column;
      if (state.sortCol === key) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortCol = key;
        const column = displayColumns.find((item) => item.key === key);
        state.sortDir = column?.type === "text" ? "asc" : "desc";
      }
      state.page = 1;
      loadData().catch((error) => {
        setStatusError(error);
      });
    });
  });
  qs("tableHead").querySelectorAll(".column-filter-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const column = displayColumns.find((item) => item.key === button.dataset.filterColumn);
      if (column) renderColumnFilterPopover(column, button);
    });
  });

  const body = qs("rows");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="${displayColumns.length}">Нет товаров под выбранные фильтры</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => {
    const sku = String(row.artikul_wb || "");
    const name = row.naimenovanie || "Без названия";
    const priceTitle = row.price_date
      ? `Средняя цена ${formatNumber(row.current_avg_price_rub, 2)} руб на ${row.price_date}`
      : `Средняя цена периода ${formatNumber(row.current_avg_price_rub, 2)} руб`;
    return `
      <tr>
        ${displayColumns.map((column) => {
          if (column.key === "selected") {
            return `
              <td class="seo-select-column">
                <input
                  class="seo-product-checkbox"
                  type="checkbox"
                  data-seo-product-select="${escapeHtml(sku)}"
                  aria-label="Выбрать товар ${escapeHtml(name)}"
                  ${selectedProducts.has(sku) ? "checked" : ""}
                />
              </td>
            `;
          }
          const value = row[column.key];
          if (column.key === "naimenovanie") {
            return `<td class="seo-product-name">${escapeHtml(value || "Без названия")}</td>`;
          }
          const content = column.type === "number" ? formatNumber(value, numberDigits(column.key)) : escapeHtml(value ?? "");
          if (column.key === "stock_value_rub") {
            return `<td class="num" title="${escapeHtml(priceTitle)}">${content}</td>`;
          }
          return `<td class="${column.type === "number" ? "num" : ""}">${content}</td>`;
        }).join("")}
      </tr>
    `;
  }).join("");

  body.querySelectorAll("[data-seo-product-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const selected = seoMonitoringSelectedSet();
      if (checkbox.checked) selected.add(String(checkbox.dataset.seoProductSelect || ""));
      else selected.delete(String(checkbox.dataset.seoProductSelect || ""));
      selected.delete("");
      state.seoMonitoringSelectedProducts = [...selected];
      updateSeoMonitoringSelectionMeta();
      persistDashboardState();
    });
  });
}
function renderHorizontalBars(containerId, rows, field, labelField, valueDigits = 0, limit = 8) {
  const top = [...rows]
    .sort((a, b) => Number(b[field] || 0) - Number(a[field] || 0))
    .slice(0, limit);
  const max = Math.max(...top.map((row) => Number(row[field] || 0)), 1);

  qs(containerId).innerHTML = top
    .map((row) => {
      const value = Number(row[field] || 0);
      const width = Math.max((value / max) * 100, 1.5);
      const label = row[labelField] || row.category_name || row.artikul_wb || "-";
      return `
        <div class="bar-row" title="${escapeHtml(label)}">
          <div class="bar-label">${escapeHtml(label)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
          <div class="bar-value">${formatNumber(value, valueDigits)}</div>
        </div>
      `;
    })
    .join("");
}

function renderBars(rows) {
  const field = chartMetricField("primary");
  qs("primaryChartTitle").textContent = `Топ ${categoryLevelNoun()}`;
  qs("chartMetric").textContent = chartMetricLabel("primary");
  renderHorizontalBars("barChart", rows, field, "category_name", 0, 8);
}

function renderAbc(rows) {
  qs("secondaryChartTitle").textContent = "ABC итог";
  qs("secondaryChartMetric").textContent = chartMetricLabel("secondary");
  const field = chartMetricField("secondary");
  const totals = rows.reduce((acc, row) => {
    const key = row.abc_combined || "Без ABC";
    acc[key] = (acc[key] || 0) + Number(row[field] || 0);
    return acc;
  }, {});
  const entries = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const max = Math.max(...entries.map(([, value]) => value), 1);
  qs("abcChart").innerHTML = entries
    .map(([label, value]) => {
      const width = Math.max((value / max) * 100, 2);
      return `
        <div class="abc-item">
          <div class="abc-line"><strong>${escapeHtml(label)}</strong><span>${formatNumber(value)}</span></div>
          <div class="abc-pill"><span style="width:${width}%"></span></div>
        </div>
      `;
    })
    .join("");
}

function renderSkuCharts(rows) {
  qs("primaryChartTitle").textContent = "Топ наименований";
  qs("chartMetric").textContent = "по остаткам, шт";
  renderHorizontalBars("barChart", rows, "total_stock_qty", "naimenovanie");

  qs("secondaryChartTitle").textContent = "Топ наименований";
  qs("secondaryChartMetric").textContent = "по заказам, шт";
  qs("abcChart").classList.add("bar-chart");
  renderHorizontalBars("abcChart", rows, "zakazano_sht", "naimenovanie");
}

function renderProductCharts(rows) {
  const field = chartMetricField("primary");
  qs("primaryChartTitle").textContent = "Топ продуктов";
  qs("chartMetric").textContent = chartMetricLabel("primary");
  renderHorizontalBars("barChart", rows, field, "naimenovanie", 0, 8);

  qs("secondaryChartTitle").textContent = "ABC итог";
  qs("secondaryChartMetric").textContent = chartMetricLabel("secondary");
  qs("abcChart").classList.remove("bar-chart");
  renderAbc(rows);
}

function renderCurrentAbcCharts() {
  if (state.dashboard === "product") {
    renderProductCharts(state.rows || []);
  } else if (state.dashboard === "abc") {
    qs("abcChart").classList.remove("bar-chart");
    renderBars(state.rows || []);
    renderAbc(state.rows || []);
  }
  updateChartMetricControls();
}

function advDailyTotals(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.expense += Number(row.expense_rub || 0);
      acc.revenue += Number(row.orders_amount_rub || 0);
      acc.impressions += Number(row.impressions || 0);
      acc.clicks += Number(row.clicks || 0);
      acc.carts += Number(row.added_to_cart || 0);
      acc.orders += Number(row.orders_qty || 0);
      acc.totalOrders += Number(row.total_orders_qty || 0);
      return acc;
    },
    { expense: 0, revenue: 0, impressions: 0, clicks: 0, carts: 0, orders: 0, totalOrders: 0 }
  );
}

function ratioPct(numerator, denominator) {
  return denominator ? (numerator / denominator) * 100 : 0;
}

function aggregateAdvMetric(rows, config) {
  if (config.aggregateRows) return config.aggregateRows(rows);
  if (config.aggregate) return config.aggregate(advDailyTotals(rows));
  return rows.reduce((sum, row) => sum + Number(row[config.field] || 0), 0);
}

function miniChartDotRadius(pointCount, plotW) {
  if (pointCount <= 1) return 4;
  const step = plotW / Math.max(pointCount - 1, 1);
  return Math.max(1.4, Math.min(4, step * 0.18));
}

function miniChartDateTicks(rows, maxTicks = 7) {
  const data = (rows || []).map((row, index) => ({
    index,
    date: row?.report_date || "",
  }));
  if (!data.length) return [];
  const tickCount = Math.max(2, Math.min(maxTicks, data.length));
  if (data.length <= tickCount) return data;
  const indexes = new Set();
  for (let tick = 0; tick < tickCount; tick += 1) {
    indexes.add(Math.round((tick / (tickCount - 1)) * (data.length - 1)));
  }
  return [...indexes].sort((a, b) => a - b).map((index) => data[index]);
}

function renderMiniLineChart(rows, config) {
  const values = rows.map((row) => Number(row[config.field] || 0));
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);
  const total = aggregateAdvMetric(rows, config);
  const latest = values.at(-1) || 0;
  const width = 420;
  const height = 120;
  const pad = { left: 42, right: 8, top: 8, bottom: 18 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const dotR = miniChartDotRadius(values.length, plotW);
  const dotHoverR = Math.min(dotR + 1.1, 4.8);
  const x = (index) => pad.left + (values.length === 1 ? plotW / 2 : (index / (values.length - 1)) * plotW);
  const y = (value) => pad.top + (1 - (value - min) / range) * plotH;
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const mid = min + range / 2;
  const ticks = [
    { value: max, y: y(max), label: formatAxisValue(max, config.digits || 0) },
    { value: mid, y: y(mid), label: formatAxisValue(mid, config.digits || 0) },
    { value: min, y: y(min), label: formatAxisValue(min, config.digits || 0) },
  ];
  const dateTicks = miniChartDateTicks(rows, 7);
  return `
    <article class="mini-chart">
      <div class="mini-head">
        <strong>${escapeHtml(config.title)}</strong>
        <span>${formatNumber(total, config.digits || 0)}${config.suffix || ""}</span>
        <button type="button" class="mini-export-btn" data-chart-export="metric" data-chart-metric="${escapeHtml(config.field)}" data-chart-title="${escapeHtml(config.title)}" aria-label="Скачать ${escapeHtml(config.title)} в Excel" title="Скачать в Excel">${chartExportButtonIcon()}</button>
      </div>
      <div class="mini-line" title="Последний период: ${formatNumber(latest, config.digits || 0)}${config.suffix || ""}">
        <svg class="mini-line-svg" viewBox="0 0 ${width} ${height}">
          ${dateTicks.map((tick) => `
            <line x1="${x(tick.index)}" y1="${pad.top}" x2="${x(tick.index)}" y2="${height - pad.bottom}" class="mini-grid-line mini-grid-line-vertical" />
          `).join("")}
          ${ticks.map((tick) => `
            <line x1="${pad.left}" y1="${tick.y}" x2="${width - pad.right}" y2="${tick.y}" class="mini-grid-line" />
            <text x="${pad.left - 8}" y="${tick.y + 4}" class="mini-axis-label" text-anchor="end">${escapeHtml(tick.label)}</text>
          `).join("")}
          <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="mini-axis" />
          <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="mini-axis" />
          <polyline points="${points}" class="mini-polyline" />
          ${values.map((value, index) => {
          const date = rows[index]?.report_date || "";
          return `<circle cx="${x(index)}" cy="${y(value)}" r="${dotR.toFixed(2)}" class="mini-dot" style="--mini-dot-hover-r:${dotHoverR.toFixed(2)}px">
            <title>${escapeHtml(date)}: ${formatNumber(value, config.digits || 0)}${config.suffix || ""}</title>
          </circle>`;
        }).join("")}
          ${dateTicks.map((tick, index) => `
            <text x="${x(tick.index)}" y="${height - 4}" class="mini-axis-label mini-axis-date-label" text-anchor="${index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle")}">${escapeHtml(formatShortDate(tick.date))}</text>
          `).join("")}
        </svg>
      </div>
    </article>
  `;
}

function renderWaterfallList(title, rows, unit = "руб") {
  const items = (rows || []).slice(0, 10);
  const max = Math.max(...items.map((row) => Number(row.value || 0)), 1);
  if (!items.length) {
    return `<article class="waterfall-card"><h3>${escapeHtml(title)}</h3><div class="empty">Нет данных</div></article>`;
  }
  return `
    <article class="waterfall-card">
      <h3>${escapeHtml(title)}</h3>
      <div class="waterfall-list">
        ${items.map((row, index) => {
          const value = Number(row.value || 0);
          const width = Math.max((value / max) * 100, value > 0 ? 2 : 0);
          const label = row.label || "-";
          return `
            <div class="waterfall-row" title="${escapeHtml(label)}: ${formatNumber(value)} ${escapeHtml(unit)}">
              <span class="waterfall-rank">${index + 1}</span>
              <span class="waterfall-label">${escapeHtml(label)}</span>
              <span class="waterfall-track"><i style="width:${width}%"></i></span>
              <strong>${formatNumber(value)}</strong>
            </div>
          `;
        }).join("")}
      </div>
    </article>
  `;
}

function averageMetric(rows, field) {
  const values = rows.map((row) => Number(row[field])).filter(Number.isFinite);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function latestMetric(rows, field) {
  return Number(rows.at(-1)?.[field] || 0);
}

function maximumMetric(rows, field) {
  return Math.max(...rows.map((row) => Number(row[field] || 0)), 0);
}

function minimumPositiveMetric(rows, field) {
  const values = rows.map((row) => Number(row[field])).filter((value) => Number.isFinite(value) && value > 0);
  return values.length ? Math.min(...values) : 0;
}

function compactChartDateTicks(data, maxTicks = 10) {
  if (!data.length) return [];
  if (data.length <= maxTicks) return data;
  const step = Math.ceil((data.length - 1) / Math.max(maxTicks - 1, 1));
  const ticks = data.filter((_, index) => index === 0 || index === data.length - 1 || index % step === 0);
  if (ticks.at(-1) !== data.at(-1)) ticks.push(data.at(-1));
  return ticks;
}

function chartMetricUnit(metric) {
  const label = String(metric?.label || metric?.title || "").toLowerCase();
  const suffix = String(metric?.suffix || "").toLowerCase();
  if (metric?.suffix === "%") return "%";
  if (suffix.includes("₽") || suffix.includes("руб")) return "руб";
  if (label.includes("%")) return "%";
  if (label.includes("руб") || label.includes("cpc") || label.includes("cpa") || label.includes("cpm")) return "руб";
  if (label.includes("шт") || label.includes("заказ") || label.includes("показ") || label.includes("клик") || label.includes("корзин") || label.includes("товар") || label.includes("sku") || label.includes("остат") || label.includes("запрос") || label.includes("переход")) return "шт";
  return "значения";
}

function chartAxisCaption(metrics, fallback = "значения") {
  const units = [...new Set((metrics || []).map(chartMetricUnit).filter(Boolean))];
  if (!units.length) return fallback;
  return units.length === 1 ? `ось: ${units[0]}` : `ось: ${units.join(" / ")}`;
}

function groupMetricsByAxisUnit(metrics) {
  const groups = [];
  const byUnit = new Map();
  (metrics || []).forEach((metric) => {
    const unit = chartMetricUnit(metric) || "значения";
    let group = byUnit.get(unit);
    if (!group) {
      group = { key: unit, unit, metrics: [] };
      byUnit.set(unit, group);
      groups.push(group);
    }
    group.metrics.push(metric);
  });
  return groups;
}

function compactMetricLabel(label, maxLength = 18) {
  const text = String(label || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function compactRightAxisLayout(rightAxisCount, options = {}) {
  const spacing = options.spacing || (rightAxisCount > 4 ? 18 : rightAxisCount > 2 ? 22 : 26);
  const padRight = rightAxisCount
    ? (options.baseRight || 8) + rightAxisCount * spacing + (options.tail || 2)
    : (options.emptyRight || 14);
  return {
    spacing,
    padRight,
    valueOffset: options.valueOffset ?? 3,
    titleOffset: options.titleOffset ?? 4,
    valueFontSize: options.valueFontSize || (rightAxisCount > 4 ? 7 : rightAxisCount > 2 ? 7.4 : 8),
  };
}

function rightAxisGroupTitle(group) {
  const unit = group?.unit || "значения";
  return unit === "значения" ? "доп. ось" : `доп. ${unit}`;
}

function rightAxisGroupDigits(group, maxValue, axisCount = 1) {
  if (group?.unit === "%") {
    if (axisCount > 1 && maxValue >= 10) return 0;
    if (maxValue >= 1) return 1;
    return 2;
  }
  const digits = Math.max(...(group?.metrics || []).map((metric) => Number(metric.digits || 0)), 0);
  return axisCount > 1 ? Math.min(digits, 1) : digits;
}

function rightAxisGroupValue(group, value, axisCount = 1, maxValue = value) {
  const suffix = group?.unit === "%" ? "%" : "";
  return `${formatAxisValue(value, rightAxisGroupDigits(group, maxValue, axisCount))}${suffix}`;
}

function rightAxisGroupByMetric(groups, metric) {
  const unit = chartMetricUnit(metric) || "значения";
  return groups.find((group) => group.key === unit) || groups[0];
}

function rightAxisMaxByGroup(data, groups, valueForMetric) {
  return new Map(groups.map((group) => {
    const values = data.flatMap((row) =>
      group.metrics.map((metric) => valueForMetric(row, metric)).filter((value) => value !== null && Number.isFinite(value))
    );
    return [group.key, Math.max(...values, 1)];
  }));
}

function chartDateLabelParts(label) {
  const text = String(label || "");
  const range = text.match(/^(\d{2}\.\d{2})\s*[–-]\s*(\d{2}\.\d{2})$/);
  if (range) return [range[1], range[2]];
  return [text];
}

function renderChartDateTick(label, x, y, anchor = "middle") {
  const parts = chartDateLabelParts(label);
  if (parts.length === 1) {
    return `<text x="${x}" y="${y}" class="axis-label axis-date-label" text-anchor="${anchor}">${escapeHtml(parts[0])}</text>`;
  }
  return `
    <text x="${x}" y="${y - 7}" class="axis-label axis-date-label" text-anchor="${anchor}">
      <tspan x="${x}" dy="0">${escapeHtml(parts[0])}</tspan>
      <tspan x="${x}" dy="12">${escapeHtml(parts[1])}</tspan>
    </text>
  `;
}

function wbSearchMovementRows(rows) {
  return (rows || []).map((row) => ({
    label: `${row.search_query || "-"} · ${formatNumber(row.first_position, 1)} → ${formatNumber(row.last_position, 1)}`,
    value: Math.abs(Number(row.position_change || 0)),
  }));
}

const wbSearchChartMetricGroups = [
  { key: "coverage", title: "Охват и спрос", metrics: [
    { field: "search_demand", title: "Количество запросов (частотность)", color: "#ff5a36", render: "bar" },
    { field: "search_query_count", title: "Уникальные запросы", color: "#0891b2", aggregateRows: (rows) => latestMetric(rows, "search_query_count") },
    { field: "high_frequency_query_count", title: "Уникальные ВЧ-запросы", color: "#16a34a", aggregateRows: (rows) => latestMetric(rows, "high_frequency_query_count") },
    { field: "medium_frequency_query_count", title: "Уникальные СЧ-запросы", color: "#f59e0b", aggregateRows: (rows) => latestMetric(rows, "medium_frequency_query_count") },
    { field: "low_frequency_query_count", title: "Уникальные НЧ-запросы", color: "#64748b", aggregateRows: (rows) => latestMetric(rows, "low_frequency_query_count") },
    { field: "sku_count", title: "SKU в отчёте", color: "#0f766e", aggregateRows: (rows) => latestMetric(rows, "sku_count") },
    { field: "product_query_pairs", title: "Связки SKU × запрос", color: "#14b8a6" },
    { field: "avg_queries_per_sku", title: "Ключей на SKU, среднее", color: "#2dd4bf", digits: 1, aggregateRows: (rows) => averageMetric(rows, "avg_queries_per_sku") },
    { field: "visible_pairs", title: "Видимые связки", color: "#06b6d4" },
    { field: "visible_pairs_pct", title: "Доля видимых связок", color: "#0284c7", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "visible_pairs_pct") },
    { field: "top20_pairs", title: "Связки в топ-20", color: "#6366f1" },
    { field: "top20_pairs_pct", title: "Доля связок в топ-20", color: "#4f46e5", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "top20_pairs_pct") },
  ]},
  { key: "placement", title: "Видимость и позиции", metrics: [
    { field: "visibility_pct", title: "Видимость", color: "#2563eb", digits: 1, suffix: "%", dash: "6 5", aggregateRows: (rows) => averageMetric(rows, "visibility_pct") },
    { field: "average_position", title: "Средняя позиция (ниже лучше)", color: "#64748b", digits: 1, dash: "2 6", aggregateRows: (rows) => averageMetric(rows, "average_position") },
    { field: "median_position", title: "Медианная позиция (ниже лучше)", color: "#475569", digits: 1, dash: "8 5", aggregateRows: (rows) => averageMetric(rows, "median_position") },
    { field: "best_position", title: "Лучшая позиция (ниже лучше)", color: "#0f172a", digits: 1, dash: "4 4", aggregateRows: (rows) => minimumPositiveMetric(rows, "best_position") },
  ]},
  { key: "funnel", title: "Воронка из поиска", metrics: [
    { field: "card_visits", title: "Переходы в карточку", color: "#0f8b8d" },
    { field: "cart_adds", title: "Добавления в корзину", color: "#d97706", dash: "10 6" },
    { field: "card_to_cart_pct", title: "Конверсия в корзину", color: "#ea580c", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "card_to_cart_pct") },
    { field: "ordered_units", title: "Заказы из поиска", color: "#7c3aed" },
    { field: "cart_to_order_pct", title: "Конверсия в заказ", color: "#9333ea", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "cart_to_order_pct") },
  ]},
  { key: "competition", title: "Сравнение с конкурентами", metrics: [
    { field: "card_visits_competitor_percentile", title: "Переходы: лучше карточек", color: "#059669", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "card_visits_competitor_percentile") },
    { field: "cart_adds_competitor_percentile", title: "Корзины: лучше карточек", color: "#16a34a", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "cart_adds_competitor_percentile") },
    { field: "card_to_cart_competitor_percentile", title: "Конверсия в корзину: лучше", color: "#65a30d", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "card_to_cart_competitor_percentile") },
    { field: "ordered_units_competitor_percentile", title: "Заказы: лучше карточек", color: "#15803d", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "ordered_units_competitor_percentile") },
    { field: "cart_to_order_competitor_percentile", title: "Конверсия в заказ: лучше", color: "#3f6212", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "cart_to_order_competitor_percentile") },
  ]},
  { key: "product", title: "Карточка и цена", metrics: [
    { field: "card_rating", title: "Рейтинг карточки", color: "#e11d48", digits: 1, suffix: "/10", aggregateRows: (rows) => latestMetric(rows, "card_rating") },
    { field: "review_rating", title: "Рейтинг по отзывам", color: "#db2777", digits: 1, suffix: "/5", aggregateRows: (rows) => latestMetric(rows, "review_rating") },
    { field: "min_price_rub", title: "Минимальная цена", color: "#a21caf", suffix: " ₽", aggregateRows: (rows) => minimumPositiveMetric(rows, "min_price_rub") },
    { field: "max_price_rub", title: "Максимальная цена", color: "#86198f", suffix: " ₽", aggregateRows: (rows) => maximumMetric(rows, "max_price_rub") },
  ]},
];

const wbSearchChartMetricConfigs = wbSearchChartMetricGroups.flatMap((group) =>
  group.metrics.map((metric) => ({ ...metric, group: group.key }))
);

const wbEntranceChartMetricGroups = [
  { key: "coverage", title: "Охват", metrics: [
    { field: "impressions", title: "Показы", color: "#0f8b8d", render: "bar" },
    { field: "card_visits", title: "Переходы в карточку", color: "#0284c7" },
    { field: "sku_count", title: "SKU в отчёте", color: "#0f766e", aggregateRows: (rows) => latestMetric(rows, "sku_count") },
  ]},
  { key: "engagement", title: "Интерес и корзина", metrics: [
    { field: "ctr_pct", title: "CTR", color: "#2563eb", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "ctr_pct") },
    { field: "cart_adds", title: "Добавления в корзину", color: "#d97706" },
    { field: "card_to_cart_pct", title: "Конверсия в корзину", color: "#ea580c", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "card_to_cart_pct") },
  ]},
  { key: "conversion", title: "Заказы", metrics: [
    { field: "ordered_units", title: "Заказы", color: "#7c3aed" },
    { field: "cart_to_order_pct", title: "Корзина → заказ", color: "#9333ea", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "cart_to_order_pct") },
    { field: "visit_to_order_pct", title: "Переход → заказ", color: "#a21caf", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "visit_to_order_pct") },
  ]},
  { key: "search_share", title: "Доля поиска", metrics: [
    { field: "search_visit_share_pct", title: "Доля переходов из поиска", color: "#059669", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "search_visit_share_pct") },
    { field: "search_order_share_pct", title: "Доля заказов из поиска", color: "#16a34a", digits: 1, suffix: "%", aggregateRows: (rows) => averageMetric(rows, "search_order_share_pct") },
  ]},
];

const wbEntranceChartMetricConfigs = wbEntranceChartMetricGroups.flatMap((group) =>
  group.metrics.map((metric) => ({ ...metric, group: group.key }))
);

const wbSearchDefaultSelectedMetrics = ["search_demand", "card_visits", "cart_adds", "ordered_units", "visibility_pct", "average_position"];
const wbEntranceDefaultSelectedMetrics = ["impressions", "card_visits", "cart_adds", "ordered_units", "ctr_pct", "visit_to_order_pct"];

function wbChartStateConfig() {
  return state.dashboard === "wbEntrance"
    ? {
        selectedKey: "wbEntranceSelectedMetrics",
        axesKey: "wbEntranceMetricAxes",
        typesKey: "wbEntranceMetricTypes",
        defaults: wbEntranceDefaultSelectedMetrics,
        defaultTypes: { impressions: "bar" },
      }
    : {
        selectedKey: "wbSearchSelectedMetrics",
        axesKey: "wbSearchMetricAxes",
        typesKey: "wbSearchMetricTypes",
        defaults: wbSearchDefaultSelectedMetrics,
        defaultTypes: { search_demand: "bar" },
      };
}

function normalizedWbSearchSelectedMetrics(configs = wbSearchChartMetricConfigs) {
  const chartState = wbChartStateConfig();
  const allowed = new Set(configs.map((config) => config.field));
  const selected = (state[chartState.selectedKey] || []).filter((key) => allowed.has(key));
  const fallback = chartState.defaults.filter((key) => allowed.has(key));
  return selected.length ? selected : fallback.length ? fallback : [configs[0]?.field].filter(Boolean);
}

function renderWbSearchCombinedChart(rows, configs, groups = wbSearchChartMetricGroups, chartLabel = "Единый график метрик поисковых запросов WB по дням") {
  if (!rows.length) return '<div class="empty-chart">Нет данных для графика</div>';
  const chartState = wbChartStateConfig();
  const selected = normalizedWbSearchSelectedMetrics(configs);
  state[chartState.selectedKey] = selected;
  const activeConfigs = configs.filter((config) => selected.includes(config.field));
  const metricAxes = state[chartState.axesKey];
  const metricTypes = state[chartState.typesKey];
  const metricAxis = (config) => metricAxes[config.field] || (config.suffix === "%" ? "right" : "left");
  const metricChartType = (config) => metricTypes[config.field] || (config.render === "bar" ? "bar" : "line");
  const rightConfigs = activeConfigs.filter((config) => metricAxis(config) === "right");
  const leftConfigs = activeConfigs.filter((config) => metricAxis(config) !== "right");
  const data = rows.map((row, index) => ({ ...row, index, date: String(row.report_date || row.date || "") }));
  const width = 1500;
  const height = 430;
  const rightAxisGroups = groupMetricsByAxisUnit(rightConfigs);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 10, emptyRight: 16, tail: 4 });
  const pad = { left: 82, right: rightAxisLayout.padRight, top: 28, bottom: 56 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (index) => data.length <= 1 ? pad.left + plotW / 2 : pad.left + (plotW * index) / (data.length - 1);
  const leftMax = Math.max(...data.flatMap((row) => leftConfigs.map((config) => Number(row[config.field] || 0))), 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, config) => Number(row[config.field] || 0));
  const yLeft = (value) => pad.top + plotH * (1 - Number(value || 0) / leftMax);
  const yRight = (config, value) => {
    const group = rightAxisGroupByMetric(rightAxisGroups, config);
    return pad.top + plotH * (1 - Number(value || 0) / (rightMaxByGroup.get(group?.key) || 1));
  };
  const yFor = (config, value) => metricAxis(config) === "right" ? yRight(config, value) : yLeft(value);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const maxDateTicks = data.length <= 16 ? data.length : 12;
  const dateTickStep = Math.max(1, Math.ceil(data.length / maxDateTicks));
  const dateTicks = data.filter((row, index) => index === 0 || index === data.length - 1 || index % dateTickStep === 0);
  const barConfigs = activeConfigs.filter((config) => metricChartType(config) === "bar");
  const lineConfigs = activeConfigs.filter((config) => metricChartType(config) !== "bar");
  const barStep = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const barGroupWidth = Math.min(48, Math.max(10, barStep * 0.7));
  const barWidth = Math.max(3, barGroupWidth / Math.max(barConfigs.length, 1) - 3);

  return `
    <div class="wb-search-combined-chart">
      <div class="wb-search-combined-legend" aria-label="Метрики графика по группам">
        ${groups.map((group, index) => {
          const groupConfigs = group.metrics.filter((metric) => configs.some((config) => config.field === metric.field));
          const activeCount = groupConfigs.filter((metric) => selected.includes(metric.field)).length;
          return `
            <section class="wb-search-metric-group ${index === 0 ? "is-wide" : ""}">
              <div class="wb-search-metric-group-head">
                <strong>${escapeHtml(group.title)}</strong>
                <span>${activeCount} из ${groupConfigs.length}</span>
              </div>
              <div class="wb-search-metric-group-items">
                ${groupConfigs.map((config) => `
                  <label class="wb-search-legend-item" style="--metric-color:${config.color}" title="Добавить или скрыть ряд: ${escapeHtml(config.title)}">
                    <input type="checkbox" data-wb-search-metric="${config.field}" ${selected.includes(config.field) ? "checked" : ""} />
                    <i class="wb-search-legend-swatch ${metricChartType(config) === "bar" ? "bar" : "line"}" style="color:${config.color}"></i>
                    <span>${escapeHtml(config.title)}</span>
                  </label>
                `).join("")}
              </div>
            </section>
          `;
        }).join("")}
      </div>
      <div class="wb-search-chart-shell">
        <svg class="combo-svg wb-search-combo-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chartLabel)}">
          <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" rx="6" class="chart-plot-bg" />
          ${ticks.map((tick) => {
            const y = pad.top + plotH * tick;
            const value = leftMax * (1 - tick);
            return `
              <line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" class="chart-grid-line" />
              <text x="${pad.left - 10}" y="${y + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(value))}</text>
            `;
          }).join("")}
          <text x="${pad.left}" y="18" class="axis-label">основная ось · фактические значения</text>
          <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
          <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis" />
          ${rightAxisGroups.map((group, axisIndex) => {
            const axisX = width - pad.right + axisIndex * rightAxisLayout.spacing;
            const maxValue = rightMaxByGroup.get(group.key) || 1;
            const color = group.metrics[0]?.color || "#0f8b8d";
            return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height - pad.bottom}" class="axis" style="stroke:${color}" />
              <text x="${axisX + rightAxisLayout.titleOffset}" y="18" class="axis-label" style="fill:${color}">${escapeHtml(rightAxisGroupTitle(group))}</text>
              ${[0, 0.5, 1].map((tick) => `<text x="${axisX + rightAxisLayout.valueOffset}" y="${pad.top + plotH * tick + 4}" class="axis-label" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px">${escapeHtml(rightAxisGroupValue(group, maxValue * (1 - tick), rightAxisGroups.length, maxValue))}</text>`).join("")}`;
          }).join("")}
          ${dateTicks.map((row, index) => `
            <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical" />
            <text x="${x(row.index)}" y="${height - 28}" class="axis-label" text-anchor="${index === 0 ? "start" : index === dateTicks.length - 1 ? "end" : "middle"}">${escapeHtml(formatShortDate(row.date))}</text>
          `).join("")}
          ${barConfigs.map((config) => data.map((row) => {
            const value = Number(row[config.field] || 0);
            const y = yFor(config, value);
            const metricIndex = barConfigs.indexOf(config);
            return `<rect x="${x(row.index) - barGroupWidth / 2 + metricIndex * (barWidth + 3)}" y="${y}" width="${barWidth}" height="${Math.max(0, height - pad.bottom - y)}" rx="3" class="funnel-metric-bar" style="fill:${config.color};opacity:.42">
              <title>${escapeHtml(row.date)} | ${escapeHtml(config.title)}: ${formatNumber(value, config.digits || 0)}${config.suffix || ""}</title>
            </rect>`;
          }).join("")).join("")}
          ${lineConfigs.map((config) => {
            const points = data.map((row) => `${x(row.index)},${yFor(config, Number(row[config.field] || 0))}`).join(" ");
            return `<polyline points="${points}" class="funnel-metric-line" style="stroke:${config.color};${config.dash ? `stroke-dasharray:${config.dash}` : ""}" />`;
          }).join("")}
          ${lineConfigs.map((config) => data.map((row) => {
            const value = Number(row[config.field] || 0);
            return `<circle cx="${x(row.index)}" cy="${yFor(config, value)}" r="4" class="funnel-metric-dot" style="fill:${config.color}">
              <title>${escapeHtml(row.date)} | ${escapeHtml(config.title)}: ${formatNumber(value, config.digits || 0)}${config.suffix || ""}</title>
            </circle>`;
          }).join("")).join("")}
        </svg>
      </div>
      <div class="legend funnel-legend wb-search-series-controls" aria-label="Настройки выбранных рядов">
        <button type="button" class="ghost" data-wb-search-chart-reset>Сбросить график</button>
        ${activeConfigs.map((config) => `
          <span class="legend-metric" data-wb-search-legend="${config.field}" title="Клик: линия / столбцы">
            <i class="${metricChartType(config) === "bar" ? "legend-bar-key" : ""}" style="background:${config.color}"></i>${escapeHtml(config.title)}
            <select class="axis-select" data-wb-search-axis-select="${config.field}" aria-label="Ось для ${escapeHtml(config.title)}">
              <option value="left" ${metricAxis(config) === "left" ? "selected" : ""}>осн.</option>
              <option value="right" ${metricAxis(config) === "right" ? "selected" : ""}>доп.</option>
            </select>
            <select class="axis-select" data-wb-search-type-select="${config.field}" aria-label="Тип графика для ${escapeHtml(config.title)}">
              <option value="line" ${metricChartType(config) === "line" ? "selected" : ""}>линия</option>
              <option value="bar" ${metricChartType(config) === "bar" ? "selected" : ""}>столбцы</option>
            </select>
          </span>
        `).join("")}
      </div>
      <p class="wb-search-scale-note">Сверху подключаются метрики. Под графиком выбранным рядам можно назначить основную или дополнительную ось и переключить линию/столбцы кликом по ряду.</p>
    </div>
  `;
}

function renderWbSearchQueriesMainChart() {
  qs("barChart").innerHTML = renderWbSearchCombinedChart(lastWbSearchDailyRows, wbSearchChartMetricConfigs);
}

function renderWbSearchQueriesCharts(dailyRows, payload = {}) {
  lastWbSearchDailyRows = dailyRows;
  qs("primaryChartTitle").textContent = "Динамика поисковых запросов WB";
  qs("chartMetric").textContent = "единый график спроса, видимости, позиций и воронки по дням";
  qs("barChart").classList.remove("bar-chart");
  renderWbSearchQueriesMainChart();

  qs("secondaryChartTitle").textContent = "Лидеры и изменение позиций";
  qs("secondaryChartMetric").textContent = "топ-10 за выбранный период";
  qs("abcChart").classList.remove("bar-chart");
  const topQueries = (payload.top_queries || []).map((row) => ({ label: row.search_query || "-", value: row.search_demand || 0 }));
  const topProducts = (payload.top_products || []).map((row) => ({ label: row.product_name || `SKU ${row.wb_sku || "-"}`, value: row.ordered_units || 0 }));
  qs("abcChart").innerHTML = `
    <div class="waterfall-grid">
      ${renderWaterfallList("Топ поисковых запросов", topQueries, "запр.")}
      ${renderWaterfallList("Топ товаров по заказам", topProducts, "шт")}
      ${renderWaterfallList("Рост позиций", wbSearchMovementRows(payload.position_growth), "поз.")}
      ${renderWaterfallList("Падение позиций", wbSearchMovementRows(payload.position_decline), "поз.")}
    </div>
    <p class="table-context-note">${escapeHtml(payload.data_note || "Спрос дедуплицирован на уровне дата × запрос.")}</p>
  `;
}

function renderWbEntranceMainChart() {
  qs("barChart").innerHTML = renderWbSearchCombinedChart(
    lastWbSearchDailyRows,
    wbEntranceChartMetricConfigs,
    wbEntranceChartMetricGroups,
    "Динамика эффективности точек входа WB по дням",
  );
}

function renderWbEntranceCharts(dailyRows, payload = {}) {
  lastWbSearchDailyRows = dailyRows;
  qs("primaryChartTitle").textContent = "Динамика точек входа WB";
  qs("chartMetric").textContent = "показы, переходы, корзины, заказы и конверсии по дням";
  qs("barChart").classList.remove("bar-chart");
  renderWbEntranceMainChart();

  qs("secondaryChartTitle").textContent = "Лидеры точек входа и товаров";
  qs("secondaryChartMetric").textContent = "топ за выбранный период";
  qs("abcChart").classList.remove("bar-chart");
  const topEntryPoints = (payload.top_entry_points || []).map((row) => ({
    label: `${row.section_name || "Без раздела"} · ${row.entry_point || "Без точки входа"}`,
    value: row.ordered_units || 0,
  }));
  const topEntryVisits = (payload.top_entry_points || []).map((row) => ({
    label: `${row.section_name || "Без раздела"} · ${row.entry_point || "Без точки входа"}`,
    value: row.card_visits || 0,
  }));
  const topProducts = (payload.top_products || []).map((row) => ({
    label: row.product_name || `WB ${row.wb_sku || "-"}`,
    value: row.ordered_units || 0,
  }));
  const topProductVisits = (payload.top_products || []).map((row) => ({
    label: row.product_name || `WB ${row.wb_sku || "-"}`,
    value: row.card_visits || 0,
  }));
  qs("abcChart").innerHTML = `
    <div class="waterfall-grid">
      ${renderWaterfallList("Точки входа по заказам", topEntryPoints, "шт")}
      ${renderWaterfallList("Точки входа по переходам", topEntryVisits, "пер.")}
      ${renderWaterfallList("Товары по заказам", topProducts, "шт")}
      ${renderWaterfallList("Товары по переходам", topProductVisits, "пер.")}
    </div>
    <p class="table-context-note">${escapeHtml(payload.data_note || "Метрики рассчитаны из детальных строк WB.")}</p>
  `;
}

function renderComboChart(rows) {
  if (!rows.length) return '<div class="empty">Нет данных под выбранные фильтры</div>';
  const hasAttributionBreakdown = rows.some((row) => row.attribution_breakdown_available !== false);
  const visibleMetricConfigs = hasAttributionBreakdown
    ? advMetricConfigs
    : advMetricConfigs.filter((metric) => ![
      "direct_orders_qty", "indirect_orders_qty",
      "direct_orders_amount_rub", "indirect_orders_amount_rub",
      "direct_drr_pct", "indirect_drr_pct",
    ].includes(metric.key));
  const selected = normalizedAdvSelectedMetrics(visibleMetricConfigs, hasAttributionBreakdown);
  state.advSelectedMetrics = selected;
  const metrics = visibleMetricConfigs.filter((item) => selected.includes(item.key));
  const width = 1500;
  const height = 380;
  const metricAxis = (metric) => state.advMetricAxes[metric.key] || (metric.type === "pct" ? "right" : "left");
  const rightMetrics = metrics.filter((item) => metricAxis(item) === "right");
  const leftMetrics = metrics.filter((item) => metricAxis(item) !== "right");
  const data = rows.map((row, index) => ({ ...row, index, date: row.report_date }));
  const rightAxisGroups = groupMetricsByAxisUnit(rightMetrics);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 6, emptyRight: 12, tail: 0 });
  const rightAxisSpacing = rightAxisLayout.spacing;
  const pad = { left: 76, right: rightAxisLayout.padRight, top: 20, bottom: 46 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const leftMax = Math.max(...data.flatMap((row) => leftMetrics.map((metric) => metricNumber(row, metric.key)).filter((value) => value !== null)), 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, metric) => metricNumber(row, metric.key));
  const yLeft = (value) => pad.top + (1 - value / leftMax) * plotH;
  const yRight = (metric, value) => {
    const group = rightAxisGroupByMetric(rightAxisGroups, metric);
    return pad.top + (1 - value / (rightMaxByGroup.get(group?.key) || 1)) * plotH;
  };
  const yFor = (metric, value) => metricAxis(metric) === "right" ? yRight(metric, value) : yLeft(value);
  const metricChartType = (metric) => state.advMetricTypes[metric.key] === "bar" ? "bar" : "line";
  const ticks = [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1];
  const majorTicks = new Set([0, 0.25, 0.5, 0.75, 1]);
  const rightTicks = rightAxisGroups.length > 2 ? [0, 0.25, 0.5, 0.75, 1] : ticks;
  const dateTicks = compactChartDateTicks(data, 11);
  const labeledDateTickIndexes = new Set(dateTicks.map((row) => row.index));
  const verticalGridStep = Math.max(1, Math.ceil(data.length / 28));
  const verticalGridRows = data.filter((row, index) =>
    index === 0 || index === data.length - 1 || index % verticalGridStep === 0
  );
  const barMetrics = metrics.filter((metric) => metricChartType(metric) === "bar");
  const lineMetrics = metrics.filter((metric) => metricChartType(metric) !== "bar");
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const groupW = Math.min(46, Math.max(10, step * 0.7));
  const barW = Math.max(3, groupW / Math.max(barMetrics.length, 1) - 3);
  const edgeInset = barMetrics.length ? groupW / 2 + 3 : 8;
  const x = (index) => pad.left + edgeInset + (data.length === 1 ? (plotW - edgeInset * 2) / 2 : (index / (data.length - 1)) * Math.max(plotW - edgeInset * 2, 1));
  return `
    <div class="chart-controls funnel-chart-controls">
      ${visibleMetricConfigs.map((metric) => `
        <label class="metric-toggle" style="--metric-color:${metric.color}">
          <input type="checkbox" data-adv-metric="${metric.key}" ${selected.includes(metric.key) ? "checked" : ""} />
          <span>${escapeHtml(metric.label)}</span>
        </label>
      `).join("")}
    </div>
    <svg class="combo-svg adv-combo-svg" style="width:100%;max-width:none;height:auto" viewBox="0 0 ${width} ${height}" role="img" aria-label="Продажи, расходы и общий ДРР по дням">
      <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" rx="6" class="chart-plot-bg" fill="rgba(224, 248, 248, 0.32)" />
      ${ticks.map((tick) => `
        <line x1="${pad.left}" y1="${yLeft(leftMax * tick)}" x2="${width - pad.right}" y2="${yLeft(leftMax * tick)}" class="chart-grid-line ${majorTicks.has(tick) ? "" : "chart-grid-line-minor"}" />
        <text x="${pad.left - 10}" y="${yLeft(leftMax * tick) + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(leftMax * tick))}</text>
      `).join("")}
      ${rightAxisGroups.length ? rightTicks.map((tick) => `
        ${rightAxisGroups.map((group, axisIndex) => {
          const axisX = width - pad.right + rightAxisLayout.valueOffset + axisIndex * rightAxisSpacing;
          const maxValue = rightMaxByGroup.get(group.key) || 1;
          const color = group.metrics[0]?.color || "#0f8b8d";
          return `<text x="${axisX}" y="${pad.top + plotH * (1 - tick) + 4}" class="axis-label adv-axis-value" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px;font-weight:750">${escapeHtml(rightAxisGroupValue(group, maxValue * tick, rightAxisGroups.length, maxValue))}</text>`;
        }).join("")}
      `).join("") : ""}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis" />
      ${rightAxisGroups.map((group, axisIndex) => {
        const axisX = width - pad.right + axisIndex * rightAxisSpacing;
        const color = group.metrics[0]?.color || "#0f8b8d";
        return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height - pad.bottom}" class="axis" style="stroke:${color}">
          <title>${escapeHtml(group.metrics.map((metric) => metric.label).join(" / "))}</title>
        </line>`;
      }).join("")}
      ${verticalGridRows.map((row) => labeledDateTickIndexes.has(row.index) ? "" : `
        <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical chart-grid-line-minor" />
      `).join("")}
      ${dateTicks.map((row, index) => `
        <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical" />
        ${renderChartDateTick(formatShortDate(row.date), x(row.index), height - 24, index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle"))}
      `).join("")}
      ${barMetrics.map((metric, metricIndex) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        const barH = Math.max(0, height - pad.bottom - yFor(metric, value));
        const barX = x(row.index) - groupW / 2 + metricIndex * (barW + 3);
        return `<rect x="${barX}" y="${yFor(metric, value)}" width="${barW}" height="${barH}" rx="2" class="funnel-metric-bar" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </rect>`;
      }).join("")).join("")}
      ${lineMetrics.map((metric) => linePointSegments(data, metric, x, yFor).map((points) =>
        `<polyline points="${points.join(" ")}" class="funnel-metric-line" style="stroke:${metric.color}" />`
      ).join("")).join("")}
      ${lineMetrics.map((metric) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        return `<circle cx="${x(row.index)}" cy="${yFor(metric, value)}" r="4" class="funnel-metric-dot" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </circle>`;
      }).join("")).join("")}
      <text x="${pad.left}" y="16" class="axis-label axis-title">${escapeHtml(chartAxisCaption(leftMetrics))}</text>
    </svg>
    <div class="legend funnel-legend adv-legend">
      ${metrics.map((metric) => `
        <span class="legend-metric" data-adv-legend="${metric.key}" title="Клик: линия / столбцы">
          <i class="${metricChartType(metric) === "bar" ? "legend-bar-key" : ""}" style="background:${metric.color}"></i>
          <span class="legend-label">${escapeHtml(metric.label)}</span>
          <select class="axis-select" data-adv-axis-select="${metric.key}" aria-label="Ось для ${escapeHtml(metric.label)}">
            <option value="left" ${metricAxis(metric) === "left" ? "selected" : ""}>осн.</option>
            <option value="right" ${metricAxis(metric) === "right" ? "selected" : ""}>доп.</option>
          </select>
        </span>
      `).join("")}
    </div>
  `;
}

function defaultAdvMetricKeys(hasAttributionBreakdown = true) {
  return hasAttributionBreakdown
    ? ["direct_orders_amount_rub", "indirect_orders_amount_rub", "expense_rub", "drr_pct"]
    : ["orders_amount_rub", "expense_rub", "impressions", "drr_pct"];
}

function normalizedAdvSelectedMetrics(metricConfigs = advMetricConfigs, hasAttributionBreakdown = true) {
  const allowed = new Set(metricConfigs.map((metric) => metric.key));
  const selected = state.advSelectedMetrics.filter((key) => allowed.has(key));
  return selected.length ? selected : defaultAdvMetricKeys(hasAttributionBreakdown);
}

function enrichAdvDailyRows(rows) {
  return rows.map((row) => {
    const impressions = Number(row.impressions || 0);
    const clicks = Number(row.clicks || 0);
    const carts = Number(row.added_to_cart || 0);
    const orders = Number(row.orders_qty || 0);
    const totalOrders = Number(row.total_orders_qty || 0);
    const expense = Number(row.expense_rub || 0);
    const revenue = Number(row.orders_amount_rub || 0);
    const totalRevenue = Number(row.total_orders_amount_rub || 0);
    return {
      ...row,
      adv_sales_to_total_sales_pct: totalRevenue ? (revenue / totalRevenue) * 100 : 0,
      adv_orders_to_total_orders_pct: totalOrders ? (orders / totalOrders) * 100 : 0,
      ctr_calc_pct: impressions ? (clicks / impressions) * 100 : 0,
      click_to_cart_pct: clicks ? (carts / clicks) * 100 : 0,
      cart_to_order_pct: carts ? (orders / carts) * 100 : 0,
      impression_to_order_pct: impressions ? (orders / impressions) * 100 : 0,
      click_to_order_pct: clicks ? (orders / clicks) * 100 : 0,
      cpc_calc_rub: clicks ? expense / clicks : 0,
      cpa_calc_rub: orders ? expense / orders : 0,
      cpm_calc_rub: impressions ? (expense / impressions) * 1000 : 0,
      avg_ad_order_value_rub: orders ? revenue / orders : 0,
      drr_pct: revenue ? (expense / revenue) * 100 : 0,
      total_drr_pct: totalRevenue ? (expense / totalRevenue) * 100 : 0,
    };
  });
}

function aggregateAdvRows(rows) {
  const addedToCartFlags = rows.filter((row) => typeof row.added_to_cart_available === "boolean");
  const attributionFlags = rows.filter((row) => typeof row.attribution_breakdown_available === "boolean");
  const addedToCartAvailable = addedToCartFlags.length
    ? addedToCartFlags.some((row) => row.added_to_cart_available)
    : true;
  const attributionBreakdownAvailable = attributionFlags.length
    ? attributionFlags.some((row) => row.attribution_breakdown_available)
    : true;
  const baseRows = aggregateRowsForPeriod(rows, [
    "expense_rub",
    "orders_amount_rub",
    "total_orders_amount_rub",
    "impressions",
    "promoted_sku_count",
    "ordered_sku_count",
    "clicks",
    "added_to_cart",
    "orders_qty",
    "total_orders_qty",
  ], ["total_sku_count"]);
  return enrichAdvDailyRows(baseRows).map((row) => ({
    ...row,
    added_to_cart_available: addedToCartAvailable,
    attribution_breakdown_available: attributionBreakdownAvailable,
  }));
}

function renderAdvCharts(dailyRows, waterfalls = {}) {
  lastAdvDailyRows = dailyRows;
  lastAdvWaterfalls = waterfalls || {};
  const rows = aggregateAdvRows(dailyRows);
  const periodLabel = periodGroupLabel();
  const hasCartMetrics = rows.some((row) => row.added_to_cart_available !== false);
  const hasAttributionBreakdown = rows.some((row) => row.attribution_breakdown_available !== false);
  qs("primaryChartTitle").textContent = `Продажи, расходы и общий ДРР по ${periodLabel}`;
  qs("chartMetric").textContent = "по выбранным фильтрам";
  qs("barChart").innerHTML = `
    ${renderComboChart(rows)}
  `;

  qs("secondaryChartTitle").textContent = `Показатели по ${periodLabel}`;
  qs("secondaryChartMetric").textContent = "количества, проценты и стоимость";
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    ${hasAttributionBreakdown ? `<div class="waterfall-grid">
      ${renderWaterfallList("Категории по продажам с рекламы", lastAdvWaterfalls.category_sales)}
      ${renderWaterfallList("Категории по расходам", lastAdvWaterfalls.category_expense)}
      ${renderWaterfallList("Товары по продажам с рекламы", lastAdvWaterfalls.product_sales)}
      ${renderWaterfallList("Товары по расходам", lastAdvWaterfalls.product_expense)}
    </div>` : '<p class="table-context-note">Доступны итоги по рекламным кампаниям. Распределение по SKU и категориям в источнике не загружено.</p>'}
    <div class="adv-chart-columns">
      <div>
        <h3>Количественные показатели</h3>
        <div class="mini-grid quantitative">
          ${[
            { title: "Заказы, шт", field: "orders_qty" },
            { title: "Продажи, руб", field: "orders_amount_rub" },
            { title: "Средний чек, руб", field: "avg_ad_order_value_rub", aggregate: (t) => (t.orders ? t.revenue / t.orders : 0) },
            { title: "Показы", field: "impressions" },
            { title: "Товаров в продвижении", field: "promoted_sku_count" },
            { title: "Товаров с заказами", field: "ordered_sku_count" },
            { title: "Товаров итого", field: "total_sku_count" },
            { title: "Клики", field: "clicks" },
            ...(hasCartMetrics ? [{ title: "Корзины", field: "added_to_cart" }] : []),
            { title: "Расход, руб", field: "expense_rub" },
          ].map((config) => renderMiniLineChart(rows, config)).join("")}
        </div>
      </div>
      <div>
        <h3>Метрики эффективности</h3>
        <div class="mini-grid metrics">
          ${[
            { title: "Рекламные заказы / общие", field: "adv_orders_to_total_orders_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.totalOrders) },
            { title: "CTR", field: "ctr_calc_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.clicks, t.impressions) },
            ...(hasCartMetrics ? [
              { title: "Клики -> корзины", field: "click_to_cart_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.carts, t.clicks) },
              { title: "Корзины -> заказы", field: "cart_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.carts) },
            ] : []),
            { title: "Показы -> заказы", field: "impression_to_order_pct", digits: 3, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.impressions) },
            { title: "Клики -> заказы", field: "click_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.clicks) },
          ].map((config) => renderMiniLineChart(rows, config)).join("")}
        </div>
      </div>
    </div>
    <div class="cost-chart-grid">
      ${[
        { title: "CPC, руб", field: "cpc_calc_rub", digits: 2, aggregate: (t) => (t.clicks ? t.expense / t.clicks : 0) },
        { title: "CPA, руб", field: "cpa_calc_rub", digits: 2, aggregate: (t) => (t.orders ? t.expense / t.orders : 0) },
        { title: "CPM, руб", field: "cpm_calc_rub", digits: 2, aggregate: (t) => (t.impressions ? (t.expense / t.impressions) * 1000 : 0) },
      ].map((config) => renderMiniLineChart(rows, config)).join("")}
    </div>
  `;
}

function formatRatioPercent(value, digits = 1) {
  return `${formatNumber(Number(value || 0) * 100, digits)}%`;
}

function boironPlanFactMetricCards(summary = {}) {
  const cards = [
    { label: "Бюджет PF", value: formatRatioPercent(summary.budget_plan_fact_pct) },
    { label: "Заказы PF", value: formatRatioPercent(summary.orders_plan_fact_pct) },
    { label: "Выручка PF", value: formatRatioPercent(summary.revenue_plan_fact_pct) },
    { label: "ДРР факт", value: formatRatioPercent(summary.drr_fact_pct) },
    { label: "CTR факт", value: formatRatioPercent(summary.ctr_fact_pct, 2) },
    { label: "CPC факт", value: `${formatNumber(summary.cpc_fact_rub, 2)} руб` },
  ];
  return cards.map((card) => `
    <article class="boiron-planfact-card">
      <span>${escapeHtml(card.label)}</span>
      <strong>${escapeHtml(card.value)}</strong>
    </article>
  `).join("");
}

function renderBoironPlanFactTable(rows = []) {
  if (!rows.length) return '<div class="empty">PF еще не импортирован</div>';
  const displayRows = rows.filter((row) => String(row.brand_name || "").toLowerCase() !== "общий итог");
  const sourceRows = displayRows.length ? displayRows : rows;
  return `
    <div class="boiron-planfact-table-wrap">
      <table class="boiron-planfact-table">
        <thead>
          <tr>
            <th>Бренд</th>
            <th>Бюджет</th>
            <th>Расход</th>
            <th>ПФ бюджет</th>
            <th>Заказы</th>
            <th>ПФ заказы</th>
            <th>Выручка</th>
            <th>ПФ выручка</th>
            <th>ДРР факт</th>
          </tr>
        </thead>
        <tbody>
          ${sourceRows.map((row) => `
            <tr>
              <td>${escapeHtml(row.brand_name || "")}</td>
              <td>${formatNumber(row.budget_plan_rub)}</td>
              <td>${formatNumber(row.expense_fact_rub)}</td>
              <td>${formatRatioPercent(row.budget_plan_fact_pct)}</td>
              <td>${formatNumber(row.orders_fact_qty)} / ${formatNumber(row.orders_plan_qty)}</td>
              <td>${formatRatioPercent(row.orders_plan_fact_pct)}</td>
              <td>${formatNumber(row.revenue_fact_rub)} / ${formatNumber(row.revenue_plan_rub)}</td>
              <td>${formatRatioPercent(row.revenue_plan_fact_pct)}</td>
              <td>${formatRatioPercent(row.drr_fact_pct)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderBoironAnalysisNote(analysis = {}) {
  const lines = analysis.note || [];
  if (!lines.length) return '<p class="muted">Аналитическая записка появится после импорта данных.</p>';
  return lines.map((line) => `<p>${escapeHtml(line)}</p>`).join("");
}

function renderBoironAdvExtras(extras) {
  const planfact = extras?.planfact || { rows: [], summary: {}, available: false };
  const analysis = extras?.analysis || {};
  const query = extras?.query || currentQuery();
  qs("abcChart").insertAdjacentHTML("beforeend", `
    <section class="boiron-planfact" aria-label="Boiron PF">
      <div class="boiron-section-head">
        <div>
          <h3>План/факт PF</h3>
          <span>лист PF из файла анализа рекламных кампаний</span>
        </div>
      </div>
      <div class="boiron-planfact-grid">
        ${boironPlanFactMetricCards(planfact.summary || {})}
      </div>
      ${renderBoironPlanFactTable(planfact.rows || [])}
    </section>
    <section class="boiron-analysis" aria-label="Boiron analytical note">
      <div class="boiron-section-head">
        <div>
          <h3>Аналитическая записка</h3>
          <span>основные метрики товарной рекламы и PF</span>
        </div>
        <button type="button" data-boiron-generate-note data-query="${escapeHtml(query)}">Генерация через LM Studio</button>
      </div>
      <div id="boironBaseNote" class="boiron-note">
        ${renderBoironAnalysisNote(analysis)}
      </div>
      <div id="boironLmNote" class="boiron-note boiron-lm-note hidden"></div>
    </section>
  `);
}

async function generateBoironAnalysisNote(button) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Генерация...";
  const target = qs("boironLmNote");
  target.classList.remove("hidden");
  target.textContent = "Ждем ответ LM Studio...";
  try {
    const result = await postJson("/api/boiron-adv-analysis/generate", { query: button.dataset.query || currentQuery() });
    target.textContent = result.text || "LM Studio вернула пустой ответ";
    qs("status").textContent = "Аналитическая записка Boiron сгенерирована";
  } catch (error) {
    target.textContent = `Ошибка LM Studio: ${error.message}`;
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function mediaAdvDailyTotals(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.expense += Number(row.expense_rub || 0);
      acc.impressions += Number(row.impressions || 0);
      acc.clicks += Number(row.clicks || 0);
      acc.orders += Number(row.orders_qty || 0);
      acc.revenue += Number(row.orders_amount_rub || 0);
      acc.postViewOrders += Number(row.post_view_orders_qty || 0);
      acc.postViewRevenue += Number(row.post_view_revenue_rub || 0);
      acc.attributedOrders += Number(row.attributed_orders_qty || 0);
      acc.attributedRevenue += Number(row.attributed_revenue_rub || 0);
      return acc;
    },
    { expense: 0, impressions: 0, clicks: 0, orders: 0, revenue: 0, postViewOrders: 0, postViewRevenue: 0, attributedOrders: 0, attributedRevenue: 0 }
  );
}

function aggregateMediaAdvMetric(rows, config) {
  if (config.aggregate) return config.aggregate(mediaAdvDailyTotals(rows));
  return rows.reduce((sum, row) => sum + Number(row[config.field] || 0), 0);
}

function enrichMediaAdvDailyRows(rows) {
  return rows.map((row) => {
    const expense = Number(row.expense_rub || 0);
    const impressions = Number(row.impressions || 0);
    const clicks = Number(row.clicks || 0);
    const orders = Number(row.orders_qty || 0);
    const directRevenue = Number(row.orders_amount_rub || 0);
    const attributedRevenue = Number(row.attributed_revenue_rub || 0);
    const postViewRevenue = Number(row.post_view_revenue_rub || 0);
    const postViewOrders = Number(row.post_view_orders_qty || 0);
    return {
      ...row,
      ctr_calc_pct: impressions ? (clicks / impressions) * 100 : 0,
      click_to_order_pct: clicks ? (orders / clicks) * 100 : 0,
      drr_direct_pct: directRevenue ? (expense / directRevenue) * 100 : 0,
      drr_attributed_pct: attributedRevenue ? (expense / attributedRevenue) * 100 : 0,
      attributed_roas: expense ? attributedRevenue / expense : 0,
      post_view_revenue_share_pct: attributedRevenue ? (postViewRevenue / attributedRevenue) * 100 : 0,
      cpc_calc_rub: clicks ? expense / clicks : 0,
      cpm_calc_rub: impressions ? (expense / impressions) * 1000 : 0,
      post_view_orders_per_1000_impressions: impressions ? (postViewOrders / impressions) * 1000 : 0,
    };
  });
}

function aggregateMediaAdvRows(rows) {
  const baseRows = aggregateRowsForPeriod(rows, [
    "expense_rub",
    "impressions",
    "clicks",
    "orders_qty",
    "orders_amount_rub",
    "post_view_orders_qty",
    "post_view_revenue_rub",
    "attributed_orders_qty",
    "attributed_revenue_rub",
  ]);
  return enrichMediaAdvDailyRows(baseRows);
}

function defaultMediaAdvMetricKeys() {
  return ["attributed_revenue_rub", "post_view_revenue_rub", "expense_rub", "drr_attributed_pct"];
}

function normalizedMediaAdvSelectedMetrics() {
  const allowed = new Set(mediaAdvMetricConfigs.map((metric) => metric.key));
  const selected = state.mediaAdvSelectedMetrics.filter((key) => allowed.has(key));
  return selected.length ? selected : defaultMediaAdvMetricKeys();
}

function renderMediaAdvMiniLineChart(rows, config) {
  const values = rows.map((row) => Number(row[config.field] || 0));
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);
  const total = aggregateMediaAdvMetric(rows, config);
  const latest = values.at(-1) || 0;
  const width = 420;
  const height = 120;
  const pad = { left: 42, right: 8, top: 8, bottom: 18 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const dotR = miniChartDotRadius(values.length, plotW);
  const dotHoverR = Math.min(dotR + 1.1, 4.8);
  const x = (index) => pad.left + (values.length === 1 ? plotW / 2 : (index / (values.length - 1)) * plotW);
  const y = (value) => pad.top + (1 - (value - min) / range) * plotH;
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const mid = min + range / 2;
  const ticks = [
    { value: max, y: y(max), label: formatAxisValue(max, config.digits || 0) },
    { value: mid, y: y(mid), label: formatAxisValue(mid, config.digits || 0) },
    { value: min, y: y(min), label: formatAxisValue(min, config.digits || 0) },
  ];
  const dateTicks = miniChartDateTicks(rows, 7);
  return `
    <article class="mini-chart">
      <div class="mini-head">
        <strong>${escapeHtml(config.title)}</strong>
        <span>${formatNumber(total, config.digits || 0)}${config.suffix || ""}</span>
      </div>
      <div class="mini-line" title="Последний период: ${formatNumber(latest, config.digits || 0)}${config.suffix || ""}">
        <svg class="mini-line-svg" viewBox="0 0 ${width} ${height}">
          ${dateTicks.map((tick) => `
            <line x1="${x(tick.index)}" y1="${pad.top}" x2="${x(tick.index)}" y2="${height - pad.bottom}" class="mini-grid-line mini-grid-line-vertical" />
          `).join("")}
          ${ticks.map((tick) => `
            <line x1="${pad.left}" y1="${tick.y}" x2="${width - pad.right}" y2="${tick.y}" class="mini-grid-line" />
            <text x="${pad.left - 8}" y="${tick.y + 4}" class="mini-axis-label" text-anchor="end">${escapeHtml(tick.label)}</text>
          `).join("")}
          <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="mini-axis" />
          <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="mini-axis" />
          <polyline points="${points}" class="mini-polyline" />
          ${values.map((value, index) => `<circle cx="${x(index)}" cy="${y(value)}" r="${dotR.toFixed(2)}" class="mini-dot" style="--mini-dot-hover-r:${dotHoverR.toFixed(2)}px">
            <title>${escapeHtml(rows[index]?.report_date || "")}: ${formatNumber(value, config.digits || 0)}${config.suffix || ""}</title>
          </circle>`).join("")}
          ${dateTicks.map((tick, index) => `
            <text x="${x(tick.index)}" y="${height - 4}" class="mini-axis-label mini-axis-date-label" text-anchor="${index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle")}">${escapeHtml(formatShortDate(tick.date))}</text>
          `).join("")}
        </svg>
      </div>
    </article>
  `;
}

function renderMediaAdvComboChart(rows) {
  if (!rows.length) return '<div class="empty">Нет данных под выбранные фильтры</div>';
  const selected = normalizedMediaAdvSelectedMetrics();
  state.mediaAdvSelectedMetrics = selected;
  const metrics = mediaAdvMetricConfigs.filter((item) => selected.includes(item.key));
  const width = 1500;
  const height = 390;
  const metricAxis = (metric) => state.mediaAdvMetricAxes[metric.key] || (metric.type === "pct" ? "right" : "left");
  const rightMetrics = metrics.filter((item) => metricAxis(item) === "right");
  const leftMetrics = metrics.filter((item) => metricAxis(item) !== "right");
  const data = rows.map((row, index) => ({ ...row, index, date: row.report_date }));
  const rightAxisGroups = groupMetricsByAxisUnit(rightMetrics);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 10, emptyRight: 16, tail: 4 });
  const pad = { left: 80, right: rightAxisLayout.padRight, top: 24, bottom: 50 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const leftMax = Math.max(...data.flatMap((row) => leftMetrics.map((metric) => metricNumber(row, metric.key)).filter((value) => value !== null)), 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, metric) => metricNumber(row, metric.key));
  const x = (index) => pad.left + (data.length === 1 ? plotW / 2 : (index / (data.length - 1)) * plotW);
  const yLeft = (value) => pad.top + (1 - value / leftMax) * plotH;
  const yRight = (metric, value) => {
    const group = rightAxisGroupByMetric(rightAxisGroups, metric);
    return pad.top + (1 - value / (rightMaxByGroup.get(group?.key) || 1)) * plotH;
  };
  const yFor = (metric, value) => metricAxis(metric) === "right" ? yRight(metric, value) : yLeft(value);
  const metricChartType = (metric) => state.mediaAdvMetricTypes[metric.key] === "bar" ? "bar" : "line";
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const dateTicks = compactChartDateTicks(data, 11);
  const barMetrics = metrics.filter((metric) => metricChartType(metric) === "bar");
  const lineMetrics = metrics.filter((metric) => metricChartType(metric) !== "bar");
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const groupW = Math.min(46, Math.max(10, step * 0.7));
  const barW = Math.max(3, groupW / Math.max(barMetrics.length, 1) - 3);
  return `
    <div class="chart-controls funnel-chart-controls">
      ${mediaAdvMetricConfigs.map((metric) => `
        <label class="metric-toggle" style="--metric-color:${metric.color}">
          <input type="checkbox" data-media-adv-metric="${metric.key}" ${selected.includes(metric.key) ? "checked" : ""} />
          <span>${escapeHtml(metric.label)}</span>
        </label>
      `).join("")}
    </div>
    <svg class="combo-svg media-adv-combo-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Медийная реклама по дням">
      <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" rx="6" class="chart-plot-bg" />
      ${ticks.map((tick) => `
        <line x1="${pad.left}" y1="${yLeft(leftMax * tick)}" x2="${width - pad.right}" y2="${yLeft(leftMax * tick)}" class="chart-grid-line" />
        <text x="${pad.left - 10}" y="${yLeft(leftMax * tick) + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(leftMax * tick))}</text>
      `).join("")}
      ${rightAxisGroups.length ? ticks.map((tick) => `
        ${rightAxisGroups.map((group, axisIndex) => {
          const axisX = width - pad.right + rightAxisLayout.valueOffset + axisIndex * rightAxisLayout.spacing;
          const maxValue = rightMaxByGroup.get(group.key) || 1;
          const color = group.metrics[0]?.color || "#0f8b8d";
          return `<text x="${axisX}" y="${pad.top + plotH * (1 - tick) + 4}" class="axis-label" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px">${escapeHtml(rightAxisGroupValue(group, maxValue * tick, rightAxisGroups.length, maxValue))}</text>`;
        }).join("")}
      `).join("") : ""}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis" />
      ${rightAxisGroups.map((group, axisIndex) => {
        const axisX = width - pad.right + axisIndex * rightAxisLayout.spacing;
        const color = group.metrics[0]?.color || "#0f8b8d";
        return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height - pad.bottom}" class="axis" style="stroke:${color}" />
          <text x="${axisX + rightAxisLayout.titleOffset}" y="16" class="axis-label axis-title" style="fill:${color}">
            <title>${escapeHtml(group.metrics.map((metric) => metric.label).join(" / "))}</title>${escapeHtml(rightAxisGroupTitle(group))}
          </text>`;
      }).join("")}
      ${dateTicks.map((row, index) => `
        <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical" />
        ${renderChartDateTick(formatShortDate(row.date), x(row.index), height - 24, index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle"))}
      `).join("")}
      ${barMetrics.map((metric, metricIndex) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        const barH = Math.max(0, height - pad.bottom - yFor(metric, value));
        const barX = x(row.index) - groupW / 2 + metricIndex * (barW + 3);
        return `<rect x="${barX}" y="${yFor(metric, value)}" width="${barW}" height="${barH}" rx="2" class="funnel-metric-bar" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </rect>`;
      }).join("")).join("")}
      ${lineMetrics.map((metric) => linePointSegments(data, metric, x, yFor).map((points) =>
        `<polyline points="${points.join(" ")}" class="funnel-metric-line" style="stroke:${metric.color}" />`
      ).join("")).join("")}
      ${lineMetrics.map((metric) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        return `<circle cx="${x(row.index)}" cy="${yFor(metric, value)}" r="4" class="funnel-metric-dot" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </circle>`;
      }).join("")).join("")}
      <text x="${pad.left}" y="16" class="axis-label axis-title">${escapeHtml(chartAxisCaption(leftMetrics))}</text>
    </svg>
    <div class="legend funnel-legend">
      ${metrics.map((metric) => `
        <span class="legend-metric" data-media-adv-legend="${metric.key}" title="Клик: линия / столбцы">
          <i class="${metricChartType(metric) === "bar" ? "legend-bar-key" : ""}" style="background:${metric.color}"></i>${escapeHtml(metric.label)}
          <select class="axis-select" data-media-adv-axis-select="${metric.key}" aria-label="Ось для ${escapeHtml(metric.label)}">
            <option value="left" ${metricAxis(metric) === "left" ? "selected" : ""}>осн.</option>
            <option value="right" ${metricAxis(metric) === "right" ? "selected" : ""}>доп.</option>
          </select>
        </span>
      `).join("")}
    </div>
  `;
}

function renderMediaAdvCharts(dailyRows, waterfalls = {}) {
  lastMediaAdvDailyRows = dailyRows;
  lastMediaAdvWaterfalls = waterfalls || {};
  const rows = aggregateMediaAdvRows(dailyRows);
  const periodLabel = periodGroupLabel();
  const hasCartMetrics = rows.some((row) => row.added_to_cart_available !== false);
  qs("primaryChartTitle").textContent = `Медийная реклама по ${periodLabel}`;
  qs("chartMetric").textContent = "выручка, расходы и ДРР";
  qs("barChart").innerHTML = renderMediaAdvComboChart(rows);

  qs("secondaryChartTitle").textContent = "Показатели медийной рекламы";
  qs("secondaryChartMetric").textContent = `по ${periodLabel}`;
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    <div class="waterfall-grid">
      ${renderWaterfallList("Форматы по атриб. выручке", lastMediaAdvWaterfalls.format_revenue)}
      ${renderWaterfallList("Форматы по расходам", lastMediaAdvWaterfalls.format_expense)}
      ${renderWaterfallList("Кампании по атриб. выручке", lastMediaAdvWaterfalls.campaign_revenue)}
      ${renderWaterfallList("Кампании по расходам", lastMediaAdvWaterfalls.campaign_expense)}
    </div>
    <div class="adv-chart-columns">
      <div>
        <h3>Количественные показатели</h3>
        <div class="mini-grid quantitative">
          ${[
            { title: "Атриб. выручка, руб", field: "attributed_revenue_rub" },
            { title: "Post-view выручка, руб", field: "post_view_revenue_rub" },
            { title: "Расход, руб", field: "expense_rub" },
            { title: "Показы", field: "impressions" },
            { title: "Клики", field: "clicks" },
            { title: "Атриб. заказы, шт", field: "attributed_orders_qty" },
          ].map((config) => renderMediaAdvMiniLineChart(rows, config)).join("")}
        </div>
      </div>
      <div>
        <h3>Метрики эффективности</h3>
        <div class="mini-grid metrics">
          ${[
            { title: "CTR", field: "ctr_calc_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.clicks, t.impressions) },
            { title: "Клики -> заказы", field: "click_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.clicks) },
            { title: "ДРР прямой", field: "drr_direct_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.expense, t.revenue) },
            { title: "ДРР с post-view", field: "drr_attributed_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.expense, t.attributedRevenue) },
            { title: "ROAS с post-view", field: "attributed_roas", digits: 2, aggregate: (t) => (t.expense ? t.attributedRevenue / t.expense : 0) },
            { title: "Доля post-view", field: "post_view_revenue_share_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.postViewRevenue, t.attributedRevenue) },
          ].map((config) => renderMediaAdvMiniLineChart(rows, config)).join("")}
        </div>
      </div>
    </div>
    <div class="cost-chart-grid">
      ${[
        { title: "CPC, руб", field: "cpc_calc_rub", digits: 2, aggregate: (t) => (t.clicks ? t.expense / t.clicks : 0) },
        { title: "CPM, руб", field: "cpm_calc_rub", digits: 2, aggregate: (t) => (t.impressions ? (t.expense / t.impressions) * 1000 : 0) },
        { title: "Post-view заказы / 1000 показов", field: "post_view_orders_per_1000_impressions", digits: 3, aggregate: (t) => (t.impressions ? (t.postViewOrders / t.impressions) * 1000 : 0) },
      ].map((config) => renderMediaAdvMiniLineChart(rows, config)).join("")}
    </div>
  `;
}

function funnelDailyTotals(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.impressionsTotal += Number(row.impressions_total || 0);
      acc.impressionsSearch += Number(row.impressions_search_catalog || 0);
      acc.visits += Number(row.card_visits || 0);
      acc.carts += Number(row.cart_adds || 0);
      acc.orders += Number(row.ordered_units || 0);
      acc.amount += Number(row.ordered_amount_rub || 0);
      acc.boughtUnits += Number(row.bought_units || 0);
      acc.cohortBoughtUnits = (acc.cohortBoughtUnits || 0) + Number(row.cohort_bought_units || 0);
      acc.boughtAmount += Number(row.bought_amount_rub || 0);
      acc.favorites += Number(row.favorites_adds || 0);
      acc.cancelledUnits += Number(row.cancelled_units || 0);
      acc.cancelledAmount += Number(row.cancelled_amount_rub || 0);
      acc.wbClubOrders += Number(row.wb_club_ordered_units || 0);
      acc.wbClubBought += Number(row.wb_club_bought_units || 0);
      acc.wbClubOrderAmount += Number(row.wb_club_ordered_amount_rub || 0);
      acc.wbClubBoughtAmount += Number(row.wb_club_bought_amount_rub || 0);
      acc.advImpressions += Number(row.adv_impressions || 0);
      acc.advClicks += Number(row.adv_clicks || 0);
      acc.advCarts += Number(row.adv_cart_adds || 0);
      acc.advOrders += Number(row.adv_orders || 0);
      acc.advAmount += Number(row.adv_orders_amount_rub || 0);
      acc.advExpense += Number(row.adv_expense_rub || 0);
      acc.organicImpressions += Number(row.organic_impressions || 0);
      acc.organicVisits += Number(row.organic_card_visits || 0);
      acc.organicCarts += Number(row.organic_cart_adds || 0);
      acc.organicOrders += Number(row.organic_orders || 0);
      return acc;
    },
    {
      impressionsTotal: 0, impressionsSearch: 0, visits: 0, carts: 0, orders: 0, amount: 0, boughtUnits: 0, boughtAmount: 0,
      favorites: 0, cancelledUnits: 0, cancelledAmount: 0, wbClubOrders: 0, wbClubBought: 0, wbClubOrderAmount: 0, wbClubBoughtAmount: 0,
      advImpressions: 0, advClicks: 0, advCarts: 0, advOrders: 0, advAmount: 0, advExpense: 0,
      organicImpressions: 0, organicVisits: 0, organicCarts: 0, organicOrders: 0,
    }
  );
}

function aggregateFunnelMetric(rows, config) {
  if (config.aggregate) return config.aggregate(funnelDailyTotals(rows));
  return rows.reduce((sum, row) => sum + Number(row[config.field] || 0), 0);
}

function renderFunnelComboChart(rows) {
  if (!rows.length) return '<div class="empty-chart">Нет данных для графика</div>';
  const availableMetrics = funnelMetricConfigsForMarketplace();
  const selected = normalizedFunnelSelectedMetrics();
  state.funnelSelectedMetrics = selected;
  const metrics = availableMetrics.filter((item) => selected.includes(item.key));
  const width = 1500;
  const height = 390;
  const metricAxis = (metric) => state.funnelMetricAxes[metric.key] || (metric.type === "pct" ? "right" : "left");
  const rightMetrics = metrics.filter((item) => metricAxis(item) === "right");
  const leftMetrics = metrics.filter((item) => metricAxis(item) !== "right");
  const data = rows.map((row, index) => ({ ...row, index, date: row.report_date }));
  const rightAxisGroups = groupMetricsByAxisUnit(rightMetrics);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 10, emptyRight: 16, tail: 4 });
  const rightAxisSpacing = rightAxisLayout.spacing;
  const pad = { left: 82, right: rightAxisLayout.padRight, top: 26, bottom: 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const leftMax = Math.max(...data.flatMap((row) => leftMetrics.map((metric) => metricNumber(row, metric.key)).filter((value) => value !== null)), 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, metric) => metricNumber(row, metric.key));
  const x = (index) => pad.left + (data.length === 1 ? plotW / 2 : (index / (data.length - 1)) * plotW);
  const yLeft = (value) => pad.top + (1 - value / leftMax) * plotH;
  const yRight = (metric, value) => {
    const group = rightAxisGroupByMetric(rightAxisGroups, metric);
    return pad.top + (1 - value / (rightMaxByGroup.get(group?.key) || 1)) * plotH;
  };
  const yFor = (metric, value) => metricAxis(metric) === "right" ? yRight(metric, value) : yLeft(value);
  const metricChartType = (metric) => state.funnelMetricTypes[metric.key] === "bar" ? "bar" : "line";
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const rightTicks = rightAxisGroups.length > 2 ? [0, 0.5, 1] : ticks;
  const dateTicks = compactChartDateTicks(data, 11);
  const barMetrics = metrics.filter((metric) => metricChartType(metric) === "bar");
  const lineMetrics = metrics.filter((metric) => metricChartType(metric) !== "bar");
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const groupW = Math.min(46, Math.max(10, step * 0.7));
  const barW = Math.max(3, groupW / Math.max(barMetrics.length, 1) - 3);
  return `
    <div class="chart-controls funnel-chart-controls">
      ${availableMetrics.map((metric) => `
        <label class="metric-toggle" style="--metric-color:${metric.color}">
          <input type="checkbox" data-funnel-metric="${metric.key}" ${selected.includes(metric.key) ? "checked" : ""} />
          <span>${escapeHtml(metric.label)}</span>
        </label>
      `).join("")}
    </div>
    <svg class="combo-svg funnel-combo-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Воронка по выбранным метрикам">
      <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" rx="6" class="chart-plot-bg" />
      ${ticks.map((tick) => `
        <line x1="${pad.left}" y1="${yLeft(leftMax * tick)}" x2="${width - pad.right}" y2="${yLeft(leftMax * tick)}" class="chart-grid-line" />
        <text x="${pad.left - 10}" y="${yLeft(leftMax * tick) + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(leftMax * tick))}</text>
      `).join("")}
      ${rightAxisGroups.length ? rightTicks.map((tick) => `
        ${rightAxisGroups.map((group, axisIndex) => {
          const axisX = width - pad.right + rightAxisLayout.valueOffset + axisIndex * rightAxisSpacing;
          const maxValue = rightMaxByGroup.get(group.key) || 1;
          const color = group.metrics[0]?.color || "#0f8b8d";
          return `<text x="${axisX}" y="${pad.top + (1 - tick) * plotH + 4}" class="axis-label funnel-axis-value" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px">
            <title>${escapeHtml(group.metrics.map((metric) => metric.label).join(" / "))}</title>${escapeHtml(rightAxisGroupValue(group, maxValue * tick, rightAxisGroups.length, maxValue))}
          </text>`;
        }).join("")}
      `).join("") : ""}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis" />
      ${rightAxisGroups.map((group, axisIndex) => {
        const axisX = width - pad.right + axisIndex * rightAxisSpacing;
        const color = group.metrics[0]?.color || "#0f8b8d";
        return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height - pad.bottom}" class="axis" style="stroke:${color}" />
          <text x="${axisX + rightAxisLayout.titleOffset}" y="18" class="axis-label axis-title funnel-axis-title" style="fill:${color}">
            <title>${escapeHtml(group.metrics.map((metric) => metric.label).join(" / "))}</title>${escapeHtml(rightAxisGroupTitle(group))}
          </text>`;
      }).join("")}
      ${dateTicks.map((row, index) => `
        <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical" />
        ${renderChartDateTick(formatWeeklyDynamicsPeriodLabel(row.date), x(row.index), height - 27, index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle"))}
      `).join("")}
      ${barMetrics.map((metric, metricIndex) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        const barH = Math.max(0, height - pad.bottom - yFor(metric, value));
        const barX = x(row.index) - groupW / 2 + metricIndex * (barW + 3);
        return `<rect x="${barX}" y="${yFor(metric, value)}" width="${barW}" height="${barH}" rx="2" class="funnel-metric-bar" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </rect>`;
      }).join("")).join("")}
      ${lineMetrics.map((metric) => linePointSegments(data, metric, x, yFor).map((points) =>
        `<polyline points="${points.join(" ")}" class="funnel-metric-line" style="stroke:${metric.color}" />`
      ).join("")).join("")}
      ${lineMetrics.map((metric) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        return `<circle cx="${x(row.index)}" cy="${yFor(metric, value)}" r="4" class="funnel-metric-dot" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </circle>`;
      }).join("")).join("")}
      ${state.dashboard === "weeklyDynamics" && periodGroupMode() === "week" ? metrics.slice(0, 4).map((metric, metricIndex) => data.slice(1).map((row, rowIndex) => {
        const previousRow = data[rowIndex];
        const previousValue = Number(previousRow[metric.key] || 0);
        const currentValue = Number(row[metric.key] || 0);
        if (!previousValue) return "";
        const changePct = ((currentValue - previousValue) / Math.abs(previousValue)) * 100;
        const badgeX = (x(previousRow.index) + x(row.index)) / 2;
        const midpointY = (yFor(metric, previousValue) + yFor(metric, currentValue)) / 2;
        const badgeY = Math.max(pad.top + 12, Math.min(height - pad.bottom - 10, midpointY + (metricIndex - (Math.min(metrics.length, 4) - 1) / 2) * 20));
        const directionClass = changePct >= 0 ? "weekly-delta-growth" : "weekly-delta-decline";
        const label = `${changePct >= 0 ? "+" : ""}${formatNumber(changePct, 1)}%`;
        return `
          <g class="weekly-delta-badge ${directionClass}">
            <rect x="${badgeX - 30}" y="${badgeY - 11}" width="60" height="19" rx="9.5" style="--weekly-metric-color:${metric.color}"></rect>
            <text x="${badgeX}" y="${badgeY + 3}" text-anchor="middle">${escapeHtml(label)}</text>
            <title>${escapeHtml(metric.label)} · ${escapeHtml(previousRow.date)} → ${escapeHtml(row.date)}: ${escapeHtml(label)}</title>
          </g>
        `;
      }).join("")).join("") : ""}
      <text x="${pad.left}" y="18" class="axis-label axis-title">${escapeHtml(chartAxisCaption(leftMetrics))}</text>
    </svg>
    <div class="legend funnel-legend">
      ${metrics.map((metric) => `
        <span class="legend-metric" data-funnel-legend="${metric.key}" title="Клик: линия / столбцы">
          <i class="${metricChartType(metric) === "bar" ? "legend-bar-key" : ""}" style="background:${metric.color}"></i>
          <span class="legend-label">${escapeHtml(metric.label)}</span>
          <select class="axis-select" data-funnel-axis-select="${metric.key}" aria-label="Ось для ${escapeHtml(metric.label)}">
            <option value="left" ${metricAxis(metric) === "left" ? "selected" : ""}>осн.</option>
            <option value="right" ${metricAxis(metric) === "right" ? "selected" : ""}>доп.</option>
          </select>
        </span>
      `).join("")}
    </div>
  `;
}

function aggregateFunnelRows(rows) {
  const mode = periodGroupMode();
  if (mode === "day") return rows;
  const buckets = new Map();
  rows.forEach((row) => {
    const key = periodBucketKey(row.report_date, mode);
    if (!key) return;
    const bucket = buckets.get(key) || { report_date: key };
    [
      "impressions_total", "impressions_search_catalog", "impressions_card", "sessions_total", "sessions_search_catalog", "sessions_card", "card_visits", "cart_adds", "cart_adds_search_catalog", "cart_adds_card", "returned_units", "delivered_units", "ordered_units", "ordered_amount_rub", "bought_units", "bought_amount_rub", "cohort_bought_units", "cohort_bought_amount_rub",
      "favorites_adds", "cancelled_units", "cancelled_amount_rub", "wb_club_ordered_units", "wb_club_bought_units", "wb_club_ordered_amount_rub", "wb_club_bought_amount_rub",
      "adv_impressions", "adv_clicks", "adv_cart_adds", "adv_orders", "adv_orders_amount_rub", "adv_expense_rub",
      "organic_impressions", "organic_card_visits", "organic_cart_adds", "organic_orders",
    ].forEach((field) => {
      bucket[field] = Number(bucket[field] || 0) + Number(row[field] || 0);
    });
    buckets.set(key, bucket);
  });
  return [...buckets.values()].sort((a, b) => String(a.report_date).localeCompare(String(b.report_date))).map((row) => ({
    ...row,
    search_to_card_visit_pct: ratioPct(row.card_visits, row.impressions_search_catalog),
    total_impression_to_card_visit_pct: ratioPct(row.card_visits, row.impressions_total),
    card_visit_to_cart_pct: ratioPct(row.cart_adds, row.card_visits),
    cart_to_order_pct: ratioPct(row.ordered_units, row.cart_adds),
    card_visit_to_order_pct: ratioPct(row.ordered_units, row.card_visits),
    ordered_amount_per_unit_rub: row.ordered_units ? row.ordered_amount_rub / row.ordered_units : 0,
    favorite_to_card_visit_pct: ratioPct(row.favorites_adds, row.card_visits),
    buyout_pct: ratioPct(row.cohort_bought_units, row.ordered_units),
    cancellation_pct: ratioPct(row.cancelled_units, row.ordered_units),
    wb_club_order_share_pct: ratioPct(row.wb_club_ordered_units, row.ordered_units),
    adv_ctr_pct: ratioPct(row.adv_clicks, row.adv_impressions),
    adv_click_to_cart_pct: ratioPct(row.adv_cart_adds, row.adv_clicks),
    adv_cart_to_order_pct: ratioPct(row.adv_orders, row.adv_cart_adds),
    adv_click_to_order_pct: ratioPct(row.adv_orders, row.adv_clicks),
    adv_cpc_rub: row.adv_clicks ? row.adv_expense_rub / row.adv_clicks : 0,
    adv_cpa_rub: row.adv_orders ? row.adv_expense_rub / row.adv_orders : 0,
    adv_cpm_rub: row.adv_impressions ? (row.adv_expense_rub / row.adv_impressions) * 1000 : 0,
    acos_pct: ratioPct(row.adv_expense_rub, row.adv_orders_amount_rub),
    tacos_pct: ratioPct(row.adv_expense_rub, row.ordered_amount_rub),
  }));
}

function renderFunnelMainChart() {
  const rows = state.dashboard === "inventoryHistory"
    ? lastInventoryHistoryDailyRows
    : aggregateFunnelRows(lastFunnelDailyRows);
  qs("barChart").innerHTML = renderFunnelComboChart(rows);
}

function renderFunnelMiniLineChart(rows, config) {
  const values = rows.map((row) => metricNumber(row, config.field));
  const presentValues = values.filter((value) => value !== null);
  const max = Math.max(...presentValues, 1);
  const min = Math.min(...presentValues, 0);
  const range = Math.max(max - min, 1);
  const total = config.aggregate ? config.aggregate(funnelDailyTotals(rows)) : presentValues.reduce((sum, value) => sum + value, 0);
  const latest = [...values].reverse().find((value) => value !== null);
  const width = 420;
  const height = 120;
  const pad = { left: 42, right: 8, top: 8, bottom: 18 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const dotR = miniChartDotRadius(values.length, plotW);
  const dotHoverR = Math.min(dotR + 1.1, 4.8);
  const x = (index) => pad.left + (values.length === 1 ? plotW / 2 : (index / Math.max(values.length - 1, 1)) * plotW);
  const y = (value) => pad.top + (1 - ((value - min) / range)) * plotH;
  const segments = [];
  let segment = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (segment.length) segments.push(segment);
      segment = [];
      return;
    }
    segment.push(`${x(index)},${y(value)}`);
  });
  if (segment.length) segments.push(segment);
  const ticks = [
    { value: max, y: y(max), label: formatAxisValue(max, config.digits || 0) },
    { value: min + range / 2, y: y(min + range / 2), label: formatAxisValue(min + range / 2, config.digits || 0) },
    { value: min, y: y(min), label: formatAxisValue(min, config.digits || 0) },
  ];
  const dates = rows.map((row) => row.report_date || "");
  const dateTicks = miniChartDateTicks(rows, 7);
  const headline = config.snapshotSeries
    ? `наблюдений: ${presentValues.length}`
    : `${formatNumber(total, config.digits || 0)}${config.suffix || ""}`;
  const latestLabel = config.snapshotSeries ? "последний снимок" : "последний период";
  return `
    <article class="mini-chart">
      <div class="mini-head">
        <strong>${escapeHtml(config.title)}</strong>
        <span>${escapeHtml(headline)}</span>
        <small>${latestLabel}: ${latest == null ? "—" : `${formatNumber(latest, config.digits || 0)}${config.suffix || ""}`}</small>
      </div>
      <div class="mini-line">
        <svg viewBox="0 0 ${width} ${height}" class="mini-line-svg" role="img" aria-label="${escapeHtml(config.title)}">
          ${dateTicks.map((tick) => `
            <line x1="${x(tick.index)}" y1="${pad.top}" x2="${x(tick.index)}" y2="${height - pad.bottom}" class="mini-grid-line mini-grid-line-vertical" />
          `).join("")}
          ${ticks.map((tick) => `
            <line x1="${pad.left}" y1="${tick.y}" x2="${width - pad.right}" y2="${tick.y}" class="mini-grid-line" />
            <text x="${pad.left - 8}" y="${tick.y + 4}" class="mini-axis-label" text-anchor="end">${escapeHtml(tick.label)}</text>
          `).join("")}
          <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="mini-axis" />
          <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="mini-axis" />
          ${segments.map((points) => `<polyline points="${points.join(" ")}" class="mini-polyline" />`).join("")}
          ${values.map((value, index) => value === null ? "" : `
            <circle cx="${x(index)}" cy="${y(value)}" r="${dotR.toFixed(2)}" class="mini-dot" style="--mini-dot-hover-r:${dotHoverR.toFixed(2)}px">
              <title>${escapeHtml(dates[index] || "")} | ${escapeHtml(config.title)}: ${formatNumber(value, config.digits || 0)}${config.suffix || ""}</title>
            </circle>
          `).join("")}
          ${dateTicks.map((tick, index) => `
            <text x="${x(tick.index)}" y="${height - 4}" class="mini-axis-label mini-axis-date-label" text-anchor="${index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle")}">${escapeHtml(formatShortDate(tick.date))}</text>
          `).join("")}
        </svg>
      </div>
    </article>
  `;
}

function renderFunnelCharts(dailyRows) {
  lastFunnelDailyRows = dailyRows;
  const rows = aggregateFunnelRows(dailyRows);
  const periodLabel = periodGroupLabel();
  const hasCartMetrics = rows.some((row) => row.added_to_cart_available !== false);
  qs("primaryChartTitle").textContent = `Воронка по ${periodLabel}`;
  qs("chartMetric").textContent = "показы, карточки, корзины и заказы";
  qs("barChart").innerHTML = renderFunnelComboChart(rows);

  qs("secondaryChartTitle").textContent = "Показатели воронки";
  qs("secondaryChartMetric").textContent = `по ${periodLabel}`;
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    <div class="adv-chart-columns">
      <div>
        <h3>Количественные показатели</h3>
        <div class="mini-grid quantitative">
          ${[
            { title: "Показы всего", field: "impressions_total" },
            { title: "Показы поиск/каталог", field: "impressions_search_catalog" },
            { title: "Посещения карточки", field: "card_visits" },
            { title: "Корзины", field: "cart_adds" },
            { title: "Заказы, шт", field: "ordered_units" },
            { title: "Заказы, руб", field: "ordered_amount_rub" },
            ...(currentMarketplace() === "wb" ? [
              { title: "Выкуплено, шт", field: "bought_units" },
              { title: "Выкупы минус возвраты, руб", field: "bought_amount_rub" },
              { title: "Выкуплено по дате заказа, шт", field: "cohort_bought_units" },
              { title: "Выкуплено по дате заказа, руб", field: "cohort_bought_amount_rub" },
              { title: "Добавили в отложенные", field: "favorites_adds" },
              { title: "Отменено, шт", field: "cancelled_units" },
              { title: "Отменено, руб", field: "cancelled_amount_rub" },
              { title: "Заказы WB Клуб, шт", field: "wb_club_ordered_units" },
              { title: "Выкуплено WB Клуб, шт", field: "wb_club_bought_units" },
            ] : []),
            { title: "Рекламные показы", field: "adv_impressions" },
            { title: "Рекламные клики", field: "adv_clicks" },
            { title: "Рекламные корзины", field: "adv_cart_adds" },
            { title: "Рекламные заказы", field: "adv_orders" },
            { title: "Расходы на рекламу", field: "adv_expense_rub" },
          ].filter((config) => funnelMetricIsAvailable(config.field)).map((config) => renderFunnelMiniLineChart(rows, config)).join("")}
        </div>
      </div>
      <div>
        <h3>Конверсии</h3>
        <div class="mini-grid metrics">
          ${[
            { title: "Поиск -> карточка", field: "search_to_card_visit_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.visits, t.impressionsSearch) },
            { title: "Показы -> карточка", field: "total_impression_to_card_visit_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.visits, t.impressionsTotal) },
            { title: "Карточка -> корзина", field: "card_visit_to_cart_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.carts, t.visits) },
            { title: "Корзина -> заказ", field: "cart_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.carts) },
            { title: "Карточка -> заказ", field: "card_visit_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.orders, t.visits) },
            { title: "Средний заказ, руб/шт", field: "ordered_amount_per_unit_rub", digits: 2, aggregate: (t) => (t.orders ? t.amount / t.orders : 0) },
            ...(currentMarketplace() === "wb" ? [
              { title: "Карточка -> отложенные", field: "favorite_to_card_visit_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.favorites, t.visits) },
              { title: "Заказ -> выкуп", field: "buyout_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.cohortBoughtUnits, t.orders) },
              { title: "Заказ -> отмена", field: "cancellation_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.cancelledUnits, t.orders) },
              { title: "Доля заказов WB Клуб", field: "wb_club_order_share_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.wbClubOrders, t.orders) },
            ] : []),
            { title: "Рекламный CTR", field: "adv_ctr_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advClicks, t.advImpressions) },
            { title: "Реклама: клик -> корзина", field: "adv_click_to_cart_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advCarts, t.advClicks) },
            { title: "Реклама: корзина -> заказ", field: "adv_cart_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advOrders, t.advCarts) },
            { title: "Реклама: клик -> заказ", field: "adv_click_to_order_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advOrders, t.advClicks) },
            { title: "ACOS", field: "acos_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advExpense, t.advAmount) },
            { title: "TACOS", field: "tacos_pct", digits: 2, suffix: "%", aggregate: (t) => ratioPct(t.advExpense, t.amount) },
            { title: "Рекламный CPC, руб", field: "adv_cpc_rub", digits: 2, aggregate: (t) => (t.advClicks ? t.advExpense / t.advClicks : 0) },
            { title: "Рекламный CPA, руб", field: "adv_cpa_rub", digits: 2, aggregate: (t) => (t.advOrders ? t.advExpense / t.advOrders : 0) },
            { title: "Рекламный CPM, руб", field: "adv_cpm_rub", digits: 2, aggregate: (t) => (t.advImpressions ? (t.advExpense / t.advImpressions) * 1000 : 0) },
          ].filter((config) => funnelMetricIsAvailable(config.field)).map((config) => renderFunnelMiniLineChart(rows, config)).join("")}
        </div>
      </div>
    </div>
  `;
}


function renderInventoryHistoryCharts(dailyRows, productsPayload = {}) {
  lastInventoryHistoryDailyRows = dailyRows;
  qs("primaryChartTitle").textContent = "История запасов и воронка";
  qs("chartMetric").textContent = "остатки, логистика, реклама и органика по дням";
  qs("barChart").classList.remove("bar-chart");
  qs("barChart").innerHTML = renderFunnelComboChart(dailyRows);

  const stockRows = dailyRows.filter((row) => row.stock_available_qty !== null && row.stock_available_qty !== undefined);
  const snapshotGapDays = productsPayload?.summary?.snapshot_gap_days;
  qs("secondaryChartTitle").textContent = "Складские показатели";
  qs("secondaryChartMetric").textContent = "по сохранённым снимкам";
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    <div class="inventory-history-note">
      <strong>Как читать дельту:</strong>
      рост и снижение показывают изменение доступного остатка к предыдущему сохранённому снимку.
      Интервал между двумя последними снимками — ${snapshotGapDays == null ? "не определён" : `${formatNumber(snapshotGapDays)} дн.`}.
      Это не движение по складским документам. Дни без снимка не интерполируются.
    </div>
    <div class="mini-grid quantitative">
      ${[
        { title: "Остаток на дату", field: "stock_available_qty" },
        { title: "Рост остатка к предыдущему снимку", field: "stock_inflow_qty" },
        { title: "Снижение остатка к предыдущему снимку", field: "stock_outflow_qty" },
        { title: currentMarketplace() === "wb" ? "В пути к клиенту" : "Товар в пути", field: currentMarketplace() === "wb" ? "to_customer_qty" : "in_transit_supply_qty" },
        { title: currentMarketplace() === "wb" ? "В пути от клиента" : "Готовится к продаже", field: currentMarketplace() === "wb" ? "from_customer_qty" : "stock_preparing_qty" },
        { title: "Зарезервировано", field: "stock_reserved_qty" },
        { title: "Возвраты от клиентов", field: "returning_from_customers_qty" },
        { title: "Оборачиваемость, дней", field: "turnover_days", digits: 1 },
      ].map((config) => renderFunnelMiniLineChart(dailyRows, { ...config, snapshotSeries: true })).join("")}
    </div>
    ${typeof renderInventoryStockMonitor === "function" ? renderInventoryStockMonitor(productsPayload) : ""}
  `;
}

function renderWeeklyRankingChart(kind, title, rows, metricKey, direction, entityType, options = {}) {
  const maxValue = Math.max(...rows.map((row) => Math.abs(Number(row[metricKey] || 0))), 1);
  const isUnits = metricKey.includes("units");
  const valueSuffix = options.suffix ?? (isUnits ? " шт" : (metricKey === "change_pct" ? "%" : " руб"));
  const valueDigits = options.digits ?? (metricKey === "change_pct" ? 1 : 0);
  const previousKey = options.previousKey ?? (isUnits ? "previous_units" : "previous_amount_rub");
  const currentKey = options.currentKey ?? (isUnits ? "current_units" : "current_amount_rub");
  const subtitle = options.subtitle ?? (isUnits ? "абсолютное изменение заказов" : (metricKey === "change_pct" ? "к предыдущей неделе" : "абсолютное изменение заказов"));
  const showExport = options.showExport !== false;
  const showPlus = options.showPlus !== false;
  return `
    <section class="weekly-ranking-chart">
      <header>
        <div><h4>${escapeHtml(title)}</h4><span>${escapeHtml(subtitle)}</span></div>
        ${showExport ? `<button type="button" class="chart-export-btn" data-weekly-export="${escapeHtml(kind)}">Excel</button>` : ""}
      </header>
      <div class="weekly-ranking-list">
        ${rows.length ? rows.map((row, index) => {
          const rawValue = Number(row[metricKey] || 0);
          const widthPct = Math.max(3, Math.abs(rawValue) / maxValue * 100);
          const entityLabel = row.entity_label || row.entity_key || "Без названия";
          const entityKey = row.entity_key || "";
          const showKey = entityType === "sku" && entityKey && entityKey !== entityLabel;
          const formattedValue = `${showPlus && rawValue > 0 ? "+" : ""}${formatNumber(rawValue, valueDigits)}${valueSuffix}`;
          return `
            <article class="weekly-ranking-row">
              <span class="weekly-ranking-index">${index + 1}</span>
              <div class="weekly-ranking-entity" title="${escapeHtml(entityLabel)}${showKey ? ` · ${escapeHtml(entityKey)}` : ""}">
                <strong>${escapeHtml(entityLabel)}</strong>
                ${showKey ? `<small>SKU ${escapeHtml(entityKey)}</small>` : ""}
              </div>
              <div class="weekly-ranking-bar" aria-hidden="true"><i class="${direction === "growth" ? "is-growth" : "is-decline"}" style="width:${widthPct.toFixed(2)}%"></i></div>
              <div class="weekly-ranking-value ${direction === "growth" ? "is-growth" : "is-decline"}">
                <strong>${escapeHtml(formattedValue)}</strong>
                <small>${formatCompactNumber(row[previousKey])} → ${formatCompactNumber(row[currentKey])}</small>
              </div>
            </article>
          `;
        }).join("") : '<div class="empty-chart">Нет сопоставимых данных за две недели</div>'}
      </div>
    </section>
  `;
}

function renderWeeklyRankingDashboard(title, entityType, direction, payload) {
  const prefix = `${entityType}_${direction}`;
  return `
    <article class="weekly-ranking-dashboard ${direction === "growth" ? "is-growth" : "is-decline"}">
      <div class="weekly-ranking-dashboard-head"><div>
        <h3>${escapeHtml(title)}</h3>
        <span>Сравнение ${escapeHtml(formatShortDate(payload.previous_week || ""))} → ${escapeHtml(formatShortDate(payload.current_week || ""))}</span>
      </div></div>
      <div class="weekly-ranking-pair">
        ${renderWeeklyRankingChart(`${prefix}_qty`, "Топ в штуках", payload[`${prefix}_qty`] || [], "change_units", direction, entityType)}
        ${renderWeeklyRankingChart(`${prefix}_amount`, "По изменению заказов, руб", payload[`${prefix}_amount`] || [], "change_amount_rub", direction, entityType)}
      </div>
    </article>
  `;
}

function renderWeeklyOosImpact(payload) {
  const oos = payload.oos_impact || {};
  const stockDate = oos.stock_date ? formatShortDate(oos.stock_date) : "нет даты";
  return `
    <article class="weekly-ranking-dashboard weekly-oos-dashboard">
      <div class="weekly-ranking-dashboard-head"><div>
        <h3>5. Влияние OOS</h3>
        <span>Продажи были на предыдущей неделе, подтвержденный текущий остаток = 0 · снимок остатков ${escapeHtml(stockDate)}</span>
      </div></div>
      <div class="weekly-oos-kpis">
        <div><span>SKU в OOS</span><strong>${formatNumber(oos.sku_count || 0, 0)}</strong></div>
        <div><span>Продажи пред. недели, шт</span><strong>${formatNumber(oos.previous_units || 0, 0)}</strong></div>
        <div><span>Продажи пред. недели, руб</span><strong>${formatCompactNumber(oos.previous_amount_rub || 0)}</strong></div>
        <div><span>Заказы текущей недели, шт</span><strong>${formatNumber(oos.current_units || 0, 0)}</strong></div>
      </div>
      <div class="weekly-ranking-pair">
        ${renderWeeklyRankingChart("", "Предыдущие продажи, шт", oos.rows_by_units || [], "previous_units", "decline", "sku", {
          subtitle: "SKU сейчас OOS; рейтинг по продажам прошлой недели", previousKey: "previous_units", currentKey: "current_units", showExport: false, showPlus: false,
        })}
        ${renderWeeklyRankingChart("", "Предыдущие продажи, руб", oos.rows_by_amount || [], "previous_amount_rub", "decline", "sku", {
          subtitle: "потенциальный недельный объем под риском", previousKey: "previous_amount_rub", currentKey: "current_amount_rub", showExport: false, showPlus: false,
        })}
      </div>
    </article>
  `;
}

function renderWeeklyDynamicsCharts(dailyRows, rankings = {}) {
  lastFunnelDailyRows = dailyRows;
  const rows = aggregateFunnelRows(dailyRows);
  qs("primaryChartTitle").textContent = `Еженедельная динамика по ${periodGroupLabel()}`;
  qs("chartMetric").textContent = "изменение к предыдущей неделе показано между точками";
  qs("barChart").innerHTML = renderFunnelComboChart(rows);
  qs("secondaryChartTitle").textContent = "Лидеры и аутсайдеры недели";
  qs("secondaryChartMetric").textContent = rankings.previous_week && rankings.current_week
    ? `${formatShortDate(rankings.previous_week)} → ${formatShortDate(rankings.current_week)}`
    : "нужны данные минимум за две недели";
  document.querySelector('[data-chart-export="secondary"]')?.classList.add("hidden");
  qs("abcChart").classList.remove("bar-chart");
  qs("abcChart").innerHTML = `
    <div class="weekly-ranking-grid">
      ${renderWeeklyRankingDashboard("1. Топ по росту категорий", "category", "growth", rankings)}
      ${renderWeeklyRankingDashboard("2. Топ по падению категорий", "category", "decline", rankings)}
      ${renderWeeklyRankingDashboard("3. Топ по росту SKU", "sku", "growth", rankings)}
      ${renderWeeklyRankingDashboard("4. Топ по падению SKU", "sku", "decline", rankings)}
      ${renderWeeklyOosImpact(rankings)}
    </div>
  `;
}

function planfactGroupState(group) {
  const isExpense = group === "expense";
  return {
    selectedKey: isExpense ? "planfactExpenseSelectedMetrics" : "planfactRevenueSelectedMetrics",
    axesKey: isExpense ? "planfactExpenseMetricAxes" : "planfactRevenueMetricAxes",
    typesKey: isExpense ? "planfactExpenseMetricTypes" : "planfactRevenueMetricTypes",
    metrics: isExpense ? planfactExpenseMetrics : planfactRevenueMetrics,
    fallback: isExpense ? "ad_spend_cum_rub" : "sales_cum_rub",
    aria: isExpense ? "Расходы план факт по дням" : "Доходы план факт по дням",
  };
}

function completePlanFactRows(rows) {
  const dateFrom = qs("date_from")?.value || rows[0]?.report_date || "";
  const dateTo = qs("date_to")?.value || rows.at(-1)?.report_date || "";
  const byDate = new Map(rows.map((row) => [row.report_date, row]));
  const days = isoDateRange(dateFrom, dateTo);
  return (days.length ? days : rows.map((row) => row.report_date)).map((date) => ({
    ...(byDate.get(date) || {}),
    report_date: date,
  }));
}

function metricNumber(row, key) {
  if (!Object.prototype.hasOwnProperty.call(row, key) || row[key] === null || row[key] === undefined || row[key] === "") return null;
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : null;
}

function linePointSegments(data, metric, x, yFor) {
  const segments = [];
  let current = [];
  data.forEach((row) => {
    const value = metricNumber(row, metric.key);
    if (value === null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push(`${x(row.index)},${yFor(metric, value)}`);
  });
  if (current.length) segments.push(current);
  return segments;
}

function renderPlanFactChart(rows, group = "revenue") {
  if (!rows.length && !(qs("date_from")?.value && qs("date_to")?.value)) return '<div class="empty-chart">Нет данных для графика</div>';
  const groupState = planfactGroupState(group);
  const selected = state[groupState.selectedKey].length ? state[groupState.selectedKey] : [groupState.fallback];
  const metrics = groupState.metrics.filter((item) => selected.includes(item.key));
  const width = 1800;
  const height = 430;
  const metricAxis = (metric) => state[groupState.axesKey][metric.key] || (metric.type === "pct" ? "right" : "left");
  const metricChartType = (metric) => state[groupState.typesKey][metric.key] || "line";
  const rightMetrics = metrics.filter((metric) => metricAxis(metric) === "right");
  const leftMetrics = metrics.filter((metric) => metricAxis(metric) !== "right");
  const data = completePlanFactRows(rows).map((row, index) => ({ ...row, index, date: row.report_date }));
  const rightAxisGroups = groupMetricsByAxisUnit(rightMetrics);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 10, emptyRight: 16, tail: 4 });
  const pad = { left: 86, right: rightAxisLayout.padRight, top: 28, bottom: 60 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const leftValues = data.flatMap((row) => leftMetrics.map((metric) => metricNumber(row, metric.key))).filter((value) => value !== null);
  const leftMax = Math.max(...leftValues, 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, metric) => metricNumber(row, metric.key));
  const x = (index) => pad.left + (data.length === 1 ? plotW / 2 : (index / (data.length - 1)) * plotW);
  const yLeft = (value) => pad.top + (1 - value / leftMax) * plotH;
  const yRight = (metric, value) => {
    const group = rightAxisGroupByMetric(rightAxisGroups, metric);
    return pad.top + (1 - value / (rightMaxByGroup.get(group?.key) || 1)) * plotH;
  };
  const yFor = (metric, value) => metricAxis(metric) === "right" ? yRight(metric, value) : yLeft(value);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const dateTicks = data.length <= 31
    ? data
    : data.filter((_, index) => index === 0 || index === data.length - 1 || index % Math.ceil(data.length / 12) === 0);
  const barMetrics = metrics.filter((metric) => metricChartType(metric) === "bar");
  const lineMetrics = metrics.filter((metric) => metricChartType(metric) !== "bar");
  const slotW = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const barW = Math.max(3, Math.min(18, (slotW * 0.68) / Math.max(barMetrics.length, 1)));
  const barStart = (count) => -((count - 1) * barW) / 2;
  return `
    <div class="chart-controls funnel-chart-controls">
      ${groupState.metrics.map((metric) => `
        <label class="metric-toggle" style="--metric-color:${metric.color}">
          <input type="checkbox" data-planfact-metric="${metric.key}" data-planfact-group="${group}" ${selected.includes(metric.key) ? "checked" : ""} />
          <span>${escapeHtml(metric.label)}</span>
        </label>
      `).join("")}
    </div>
    <svg class="combo-svg planfact-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(groupState.aria)}">
      ${ticks.map((tick) => `
        <line x1="${pad.left}" y1="${yLeft(leftMax * tick)}" x2="${width - pad.right}" y2="${yLeft(leftMax * tick)}" class="chart-grid-line" />
        <text x="${pad.left - 10}" y="${yLeft(leftMax * tick) + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(leftMax * tick))}</text>
      `).join("")}
      ${rightAxisGroups.length ? ticks.map((tick) => `
        ${rightAxisGroups.map((group, axisIndex) => {
          const axisX = width - pad.right + rightAxisLayout.valueOffset + axisIndex * rightAxisLayout.spacing;
          const maxValue = rightMaxByGroup.get(group.key) || 1;
          const color = group.metrics[0]?.color || "#0f8b8d";
          return `<text x="${axisX}" y="${pad.top + (1 - tick) * plotH + 4}" class="axis-label" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px">${escapeHtml(rightAxisGroupValue(group, maxValue * tick, rightAxisGroups.length, maxValue))}</text>`;
        }).join("")}
      `).join("") : ""}
      ${dateTicks.map((row, index) => `
        <line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height - pad.bottom}" class="chart-grid-line vertical" />
        <text x="${x(row.index)}" y="${height - 24}" class="axis-label" text-anchor="${index === 0 ? "start" : (index === dateTicks.length - 1 ? "end" : "middle")}">${escapeHtml(formatShortDate(row.date))}</text>
      `).join("")}
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="axis" />
      ${rightAxisGroups.map((group, axisIndex) => {
        const axisX = width - pad.right + axisIndex * rightAxisLayout.spacing;
        const color = group.metrics[0]?.color || "#0f8b8d";
        return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height - pad.bottom}" class="axis" style="stroke:${color}" />
          <text x="${axisX + rightAxisLayout.titleOffset}" y="18" class="axis-label" style="fill:${color}">
            <title>${escapeHtml(group.metrics.map((metric) => metric.label).join(" / "))}</title>${escapeHtml(rightAxisGroupTitle(group))}
          </text>`;
      }).join("")}
      ${barMetrics.map((metric, metricIndex) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        const zeroY = yFor(metric, 0);
        const valueY = yFor(metric, value);
        const rectY = Math.min(zeroY, valueY);
        const rectH = Math.max(Math.abs(zeroY - valueY), 1);
        const rectX = x(row.index) + barStart(barMetrics.length) + metricIndex * barW - barW / 2;
        return `<rect x="${rectX}" y="${rectY}" width="${barW}" height="${rectH}" class="planfact-metric-bar" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </rect>`;
      }).join("")).join("")}
      ${lineMetrics.map((metric) => {
        const segments = linePointSegments(data, metric, x, yFor);
        return segments.map((points) => `<polyline points="${points.join(" ")}" class="funnel-metric-line" style="stroke:${metric.color}" />`).join("");
      }).join("")}
      ${lineMetrics.map((metric) => data.map((row) => {
        const value = metricNumber(row, metric.key);
        if (value === null) return "";
        return `<circle cx="${x(row.index)}" cy="${yFor(metric, value)}" r="3" class="funnel-metric-dot" style="fill:${metric.color}">
          <title>${escapeHtml(row.date)} | ${escapeHtml(metric.label)}: ${formatNumber(value, metric.digits || 0)}${metric.suffix || ""}</title>
        </circle>`;
      }).join("")).join("")}
      <text x="${pad.left}" y="18" class="axis-label">значения</text>
    </svg>
    <div class="legend funnel-legend">
      ${metrics.map((metric) => `
        <span class="legend-metric planfact-legend-metric" data-planfact-legend="${metric.key}" data-planfact-group="${group}" title="Клик: линия / столбцы">
          <i class="${metricChartType(metric) === "bar" ? "legend-bar-key" : ""}" style="background:${metric.color}"></i>${escapeHtml(metric.label)}
          <select class="axis-select" data-planfact-axis-select="${metric.key}" data-planfact-group="${group}" aria-label="Ось для ${escapeHtml(metric.label)}">
            <option value="left" ${metricAxis(metric) === "left" ? "selected" : ""}>осн.</option>
            <option value="right" ${metricAxis(metric) === "right" ? "selected" : ""}>доп.</option>
          </select>
        </span>
      `).join("")}
    </div>
  `;
}

function renderPlanFactMonthCards(rows) {
  if (!rows.length) return '<div class="empty-chart">Нет данных по месяцам</div>';
  return `
    <div class="planfact-month-grid">
      ${rows.map((row) => {
        const salesPct = Math.max(0, Math.min(Number(row.sales_plan_fact_pct || 0), 140));
        const budgetPct = Math.max(0, Math.min(Number(row.ad_spend_budget_used_pct || 0), 140));
        return `
          <article class="planfact-month-card">
            <div class="month-card-head">
              <strong>${escapeHtml(row.marketplace_label || row.marketplace)} · ${escapeHtml(formatShortDate(row.plan_month || ""))}</strong>
              <span>${escapeHtml(row.days_with_fact || 0)} дн.</span>
            </div>
            <div class="month-card-line"><span>Продажи</span><b>${formatCompactNumber(row.sales_rub)} / ${formatCompactNumber(row.sales_plan_rub)}</b></div>
            <div class="progress"><i style="width:${Math.min(salesPct, 100)}%"></i></div>
            <div class="month-card-line"><span>План/факт</span><b>${formatNumber(row.sales_plan_fact_pct, 1)}%</b></div>
            <div class="month-card-line"><span>Расходы</span><b>${formatCompactNumber(row.ad_spend_rub)} / ${formatCompactNumber(row.ad_spend_plan_rub)}</b></div>
            <div class="progress warn"><i style="width:${Math.min(budgetPct, 100)}%"></i></div>
            <div class="month-card-line"><span>TACOS факт / план</span><b>${formatNumber(row.tacos_fact_pct, 1)}% / ${formatNumber(row.tacos_plan_pct, 1)}%</b></div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function planFactMonthName(row) {
  const date = parseIsoDate(row?.plan_month || qs("date_from")?.value || "");
  return date ? monthNames[date.getMonth()] : "";
}

function renderPlanFactMiniBars(config) {
  const values = config.bars.map((bar) => Number(bar.value || 0));
  const maxValue = Math.max(...values, 1);
  const width = 520;
  const height = 260;
  const pad = { left: 82, right: 24, top: 38, bottom: 46 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const barW = Math.min(70, plotW / Math.max(config.bars.length * 1.8, 1));
  const gap = (plotW - barW * config.bars.length) / Math.max(config.bars.length + 1, 1);
  const y = (value) => pad.top + (1 - value / maxValue) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return `
    <article class="planfact-mini-card">
      <h3>${escapeHtml(config.title)}</h3>
      <svg class="planfact-mini-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(config.title)}">
        ${ticks.map((tick) => `
          <line x1="${pad.left}" y1="${y(maxValue * tick)}" x2="${width - pad.right}" y2="${y(maxValue * tick)}" class="chart-grid-line" />
          <text x="${pad.left - 10}" y="${y(maxValue * tick) + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatNumber(maxValue * tick))}</text>
        `).join("")}
        <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="axis" />
        ${config.bars.map((bar, index) => {
          const x = pad.left + gap + index * (barW + gap);
          const valueY = y(Number(bar.value || 0));
          const barH = Math.max(height - pad.bottom - valueY, 1);
          return `
            <rect x="${x}" y="${valueY}" width="${barW}" height="${barH}" rx="0" class="planfact-mini-bar" style="fill:${bar.color}">
              <title>${escapeHtml(bar.label)}: ${formatNumber(bar.value)}</title>
            </rect>
            <text x="${x + barW / 2}" y="${height - 16}" class="axis-label" text-anchor="middle">${escapeHtml(bar.label)}</text>
          `;
        }).join("")}
        ${config.badges.map((badge) => `
          <g>
            <rect x="${badge.x}" y="${badge.y}" width="64" height="34" rx="0" class="planfact-mini-badge" />
            <text x="${badge.x + 32}" y="${badge.y + 22}" class="planfact-mini-badge-text" text-anchor="middle">${escapeHtml(pctText(badge.value))}</text>
          </g>
        `).join("")}
      </svg>
    </article>
  `;
}

function renderPlanFactMonthSummaryCharts(rows, group = "revenue") {
  const row = rows?.[0] || {};
  const isRevenue = group === "revenue";
  const plan = isRevenue ? row.sales_plan_rub : row.ad_spend_plan_rub;
  const fact = isRevenue ? row.sales_rub : row.ad_spend_rub;
  const runrate = isRevenue ? row.sales_runrate_rub : row.ad_spend_runrate_rub;
  const recentRunrate = isRevenue ? row.sales_recent_runrate_rub : row.ad_spend_recent_runrate_rub;
  const planFactPct = isRevenue ? row.sales_plan_fact_pct : row.ad_spend_plan_fact_pct;
  const runratePct = isRevenue ? row.sales_runrate_pct : row.ad_spend_runrate_pct;
  const recentRunratePct = isRevenue ? row.sales_recent_runrate_pct : row.ad_spend_recent_runrate_pct;
  const planLabel = isRevenue ? "План продаж" : "План расходов";
  const factLabel = isRevenue ? "Факт продаж" : "Факт расходов";
  const runrateTitle = `Run-rate ${planFactMonthName(row) || "месяц"}`;
  const planColor = "#a3a3a3";
  const factColor = isRevenue ? "#ff4747" : "#d61f3c";
  const runrateColor = "#ffb3d2";
  const recentColor = "#06e19b";
  return `
    <div class="planfact-mini-grid">
      ${renderPlanFactMiniBars({
        title: "План/факт",
        bars: [
          { label: planLabel, value: plan, color: planColor },
          { label: factLabel, value: fact, color: factColor },
        ],
        badges: [{ value: planFactPct, x: 298, y: 106 }],
      })}
      ${renderPlanFactMiniBars({
        title: runrateTitle,
        bars: [
          { label: planLabel, value: plan, color: planColor },
          { label: "Run-rate 1-" + (row.last_day || ""), value: runrate, color: runrateColor },
          { label: "Run-rate " + (row.recent_start_day || "") + "-" + (row.last_day || ""), value: recentRunrate, color: recentColor },
        ],
        badges: [
          { value: runratePct, x: 274, y: 112 },
          { value: recentRunratePct, x: 408, y: 90 },
        ],
      })}
    </div>
  `;
}

function pctText(value) {
  return `${formatNumber(value, 0)}%`;
}

function renderPlanFactKpis(row = {}) {
  setPlanFactKpiGroups([
    {
      title: "Доходы",
      groups: [
        {
          title: "План/факт",
          items: [
            { shortLabel: "План", label: "План продаж на месяц", value: formatNumber(row.sales_plan_rub) },
            { shortLabel: "Заказы", label: "Заказы за период, руб.", value: formatNumber(row.orders_rub) },
            { shortLabel: "Продажи", label: "Факт продаж за период, руб.", value: formatNumber(row.sales_rub) },
            { shortLabel: "План/факт", label: "Выполнение плана продаж, %", value: pctText(row.sales_plan_fact_pct) },
          ],
        },
        {
          title: "Ран рейт продаж (мес)",
          shortTitle: "RR продаж · мес.",
          items: [
            { shortLabel: "План", label: "План продаж на месяц", value: formatNumber(row.sales_plan_rub) },
            { shortLabel: "RR месяца", label: "Run Rate продаж с начала месяца", value: formatNumber(row.sales_runrate_rub) },
            { shortLabel: "План/RR", label: "Прогноз выполнения плана продаж по Run Rate месяца", value: pctText(row.sales_runrate_pct) },
          ],
        },
        {
          title: "Ран рейт продаж (нед)",
          shortTitle: "RR продаж · 5 дн.",
          items: [
            { shortLabel: "План", label: "План продаж на месяц", value: formatNumber(row.sales_plan_rub) },
            { shortLabel: "RR 5 дней", label: "Run Rate продаж за последние 5 дней", value: formatNumber(row.sales_recent_runrate_rub) },
            { shortLabel: "План/RR", label: "Прогноз выполнения плана продаж по Run Rate последних 5 дней", value: pctText(row.sales_recent_runrate_pct) },
          ],
        },
      ],
    },
    {
      title: "Расходы",
      groups: [
        {
          title: "План/факт",
          items: [
            { shortLabel: "План", label: "План расходов на месяц", value: formatNumber(row.ad_spend_plan_rub) },
            { shortLabel: "Расходы", label: "Факт расходов за период, руб.", value: formatNumber(row.ad_spend_rub) },
            { shortLabel: "План/факт", label: "Выполнение плана расходов, %", value: pctText(row.ad_spend_plan_fact_pct) },
            { shortLabel: "TACoS", label: "Фактический TACoS, %", value: row.sales_rub ? `${formatNumber((Number(row.ad_spend_rub || 0) / Number(row.sales_rub || 1)) * 100, 1)}%` : "0%" },
          ],
        },
        {
          title: "Ран рейт расходов (мес)",
          shortTitle: "RR расходов · мес.",
          items: [
            { shortLabel: "План", label: "План расходов на месяц", value: formatNumber(row.ad_spend_plan_rub) },
            { shortLabel: "RR месяца", label: "Run Rate расходов с начала месяца", value: formatNumber(row.ad_spend_runrate_rub) },
            { shortLabel: "План/RR", label: "Прогноз выполнения бюджета по Run Rate месяца", value: pctText(row.ad_spend_runrate_pct) },
          ],
        },
        {
          title: "Ран рейт расходов (нед)",
          shortTitle: "RR расходов · 5 дн.",
          items: [
            { shortLabel: "План", label: "План расходов на месяц", value: formatNumber(row.ad_spend_plan_rub) },
            { shortLabel: "RR 5 дней", label: "Run Rate расходов за последние 5 дней", value: formatNumber(row.ad_spend_recent_runrate_rub) },
            { shortLabel: "План/RR", label: "Прогноз выполнения бюджета по Run Rate последних 5 дней", value: pctText(row.ad_spend_recent_runrate_pct) },
          ],
        },
      ],
    }
  ]);
}

function renderPlanFactMetricRows(row, type) {
  const isSales = type === "sales";
  const plan = isSales ? row.sales_plan_rub : row.ad_spend_plan_rub;
  const fact = isSales ? row.sales_rub : row.ad_spend_rub;
  const runrate = isSales ? row.sales_runrate_rub : row.ad_spend_runrate_rub;
  const runratePct = isSales ? row.sales_runrate_pct : row.ad_spend_runrate_pct;
  const recentRunrate = isSales ? row.sales_recent_runrate_rub : row.ad_spend_recent_runrate_rub;
  const recentRunratePct = isSales ? row.sales_recent_runrate_pct : row.ad_spend_recent_runrate_pct;
  const planFactPct = isSales ? row.sales_plan_fact_pct : row.ad_spend_plan_fact_pct;
  const firstLabel = isSales ? "Заказы:" : "Бюджет на маркетинг:";
  const planLabel = isSales ? "План продаж" : "Факт расходов:";
  const factLabel = isSales ? "Факт продаж" : "План/факт:";
  const firstValue = isSales ? row.orders_rub : plan;
  const secondValue = isSales ? plan : fact;
  const thirdValue = isSales ? fact : planFactPct;
  const thirdIsPct = !isSales;
  const lastDay = Number(row.last_day || 0);
  const recentStart = Number(row.recent_start_day || 0);
  return `
    <div class="pf-row"><span>${escapeHtml(firstLabel)}</span><b>${formatNumber(firstValue)}</b></div>
    <div class="pf-row"><span>${escapeHtml(planLabel)}</span><b>${formatNumber(secondValue)}</b></div>
    <div class="pf-row emph"><span>${escapeHtml(factLabel)}</span><b>${thirdIsPct ? pctText(thirdValue) : formatNumber(thirdValue)}</b></div>
    ${isSales ? `<div class="pf-row emph"><span>План/факт:</span><b>${pctText(planFactPct)}</b></div>` : ""}
    <div class="pf-row"><span>Run-Rate 1-${lastDay}</span><b>${formatNumber(runrate)}</b></div>
    <div class="pf-row emph"><span>%</span><b>${pctText(runratePct)}</b></div>
    <div class="pf-row"><span>Run-rate ${recentStart}-${lastDay}</span><b>${formatNumber(recentRunrate)}</b></div>
    <div class="pf-row emph"><span>%</span><b>${pctText(recentRunratePct)}</b></div>
  `;
}

function renderPlanFactScorecard(rows) {
  if (!rows.length) return '<div class="empty-chart">Нет данных для план-факт сводки</div>';
  return `
    <div class="planfact-scorecard">
      <div class="pf-title">${escapeHtml(clientLabel())}</div>
      <div class="pf-market-grid">
        ${rows.map((row) => `
          <section class="pf-market">
            <h3>${escapeHtml(row.marketplace_label || row.marketplace)}</h3>
            <div class="pf-block">
              <h4>Продажи:</h4>
              <div class="pf-head"><span>Статья:</span><span>Значение:</span></div>
              ${renderPlanFactMetricRows(row, "sales")}
            </div>
            <div class="pf-block">
              <h4>Продвижение:</h4>
              <div class="pf-head"><span>Статья:</span><span>Значение:</span></div>
              ${renderPlanFactMetricRows(row, "promo")}
            </div>
          </section>
        `).join("")}
      </div>
    </div>
  `;
}

let kmSalesPlanPayload = null;

function kmSalesPlanMoney(value) {
  return `${formatNumber(Number(value || 0), 0)} ₽`;
}

function kmSalesPlanPct(value) {
  return `${formatNumber(Number(value || 0), 1)}%`;
}

function kmSalesPlanInput(value) {
  const number = Number(value);
  return Number.isFinite(number) && number !== 0 ? String(number) : "";
}

function kmSalesPlanMonthLabel(value, short = false) {
  const parsed = parseIsoDate(value);
  if (!parsed) return value || "";
  return parsed.toLocaleDateString("ru-RU", { month: short ? "short" : "long" });
}

function renderKmSalesPlanMonthlyChart(rows) {
  const width = 1040;
  const height = 330;
  const pad = { left: 76, right: 24, top: 24, bottom: 58 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxValue = Math.max(...rows.flatMap((row) => [Number(row.plan_revenue || 0), Number(row.actual_revenue || 0)]), 1);
  const slot = plotW / Math.max(rows.length, 1);
  const barWidth = Math.min(28, slot * 0.3);
  const y = (value) => pad.top + (1 - Number(value || 0) / maxValue) * plotH;
  return `
    <svg class="km-sales-plan-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="План и факт продаж KM Trade по месяцам">
      ${[0, .25, .5, .75, 1].map((tick) => {
        const value = maxValue * tick;
        const tickY = y(value);
        return `<line x1="${pad.left}" y1="${tickY}" x2="${width - pad.right}" y2="${tickY}" class="chart-grid-line" />
          <text x="${pad.left - 10}" y="${tickY + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatCompactNumber(value))}</text>`;
      }).join("")}
      ${rows.map((row, index) => {
        const center = pad.left + slot * index + slot / 2;
        const planY = y(row.plan_revenue);
        const factY = y(row.actual_revenue);
        return `
          <rect x="${center - barWidth - 2}" y="${planY}" width="${barWidth}" height="${Math.max(1, height - pad.bottom - planY)}" fill="#c45cf4"><title>План: ${escapeHtml(kmSalesPlanMoney(row.plan_revenue))}</title></rect>
          <rect x="${center + 2}" y="${factY}" width="${barWidth}" height="${Math.max(1, height - pad.bottom - factY)}" fill="#70ad47"><title>Факт: ${escapeHtml(kmSalesPlanMoney(row.actual_revenue))}</title></rect>
          <text x="${center}" y="${height - 30}" class="axis-label" text-anchor="middle">${escapeHtml(kmSalesPlanMonthLabel(row.month_start, true))}</text>`;
      }).join("")}
      <rect x="420" y="${height - 14}" width="14" height="8" fill="#c45cf4" /><text x="440" y="${height - 7}" class="axis-label">План</text>
      <rect x="520" y="${height - 14}" width="14" height="8" fill="#70ad47" /><text x="540" y="${height - 7}" class="axis-label">Факт</text>
    </svg>`;
}

function renderKmSalesPlanCumulativeChart(rows) {
  const width = 1040;
  const height = 330;
  const pad = { left: 76, right: 24, top: 24, bottom: 58 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxValue = Math.max(...rows.flatMap((row) => [Number(row.plan_revenue_cum || 0), Number(row.actual_revenue_cum || 0)]), 1);
  const x = (index) => pad.left + (rows.length === 1 ? plotW / 2 : plotW * index / Math.max(rows.length - 1, 1));
  const y = (value) => pad.top + (1 - Number(value || 0) / maxValue) * plotH;
  const planPoints = rows.map((row, index) => `${x(index)},${y(row.plan_revenue_cum)}`).join(" ");
  const factPoints = rows.map((row, index) => `${x(index)},${y(row.actual_revenue_cum)}`).join(" ");
  return `
    <svg class="km-sales-plan-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Накопительный план и факт KM Trade">
      ${[0, .25, .5, .75, 1].map((tick) => {
        const value = maxValue * tick;
        const tickY = y(value);
        return `<line x1="${pad.left}" y1="${tickY}" x2="${width - pad.right}" y2="${tickY}" class="chart-grid-line" />
          <text x="${pad.left - 10}" y="${tickY + 4}" class="axis-label" text-anchor="end">${escapeHtml(formatCompactNumber(value))}</text>`;
      }).join("")}
      <polyline points="${planPoints}" fill="none" stroke="#c45cf4" stroke-width="4" />
      <polyline points="${factPoints}" fill="none" stroke="#70ad47" stroke-width="4" />
      ${rows.map((row, index) => `
        <circle cx="${x(index)}" cy="${y(row.plan_revenue_cum)}" r="4" fill="#c45cf4"><title>План накопительно: ${escapeHtml(kmSalesPlanMoney(row.plan_revenue_cum))}</title></circle>
        <circle cx="${x(index)}" cy="${y(row.actual_revenue_cum)}" r="4" fill="#70ad47"><title>Факт накопительно: ${escapeHtml(kmSalesPlanMoney(row.actual_revenue_cum))}</title></circle>
        <text x="${x(index)}" y="${height - 30}" class="axis-label" text-anchor="middle">${escapeHtml(kmSalesPlanMonthLabel(row.month_start, true))}</text>
      `).join("")}
      <rect x="420" y="${height - 14}" width="14" height="8" fill="#c45cf4" /><text x="440" y="${height - 7}" class="axis-label">План</text>
      <rect x="520" y="${height - 14}" width="14" height="8" fill="#70ad47" /><text x="540" y="${height - 7}" class="axis-label">Факт</text>
    </svg>`;
}

function renderKmSalesPlanPayload(payload) {
  kmSalesPlanPayload = payload;
  const root = qs("kmSalesPlanSection");
  if (!root) return;
  const totals = payload.totals || {};
  const rows = payload.months || [];
  const products = payload.products || [];
  root.innerHTML = `
    <article class="km-sales-plan-card">
      <header class="km-sales-plan-head">
        <div><h2>План продаж</h2><p>План вводится вручную; факт — точные продажи и возвраты Ozon плюс расходы Performance API.</p></div>
        <div class="km-sales-plan-actions"><span>${escapeHtml(String(payload.period?.date_from || "").slice(0, 4))} год</span><button type="button" data-km-sales-plan-save>Сохранить план</button></div>
      </header>
      <div class="km-sales-plan-kpis">
        ${[
          ["План продаж, ₽", kmSalesPlanMoney(totals.plan_revenue)],
          ["Факт продаж, ₽", kmSalesPlanMoney(totals.actual_revenue)],
          ["Выполнение", kmSalesPlanPct(totals.plan_fact_pct)],
          ["План, шт", formatNumber(totals.plan_units, 0)],
          ["Факт, шт", formatNumber(totals.actual_units, 0)],
        ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
      </div>
    </article>
    <div class="km-sales-plan-chart-grid">
      <article class="km-sales-plan-card"><h3>По месяцам</h3><p>План и факт продаж в деньгах</p>${renderKmSalesPlanMonthlyChart(rows)}</article>
      <article class="km-sales-plan-card"><h3>Накопительно</h3><p>Темп выполнения годового плана</p>${renderKmSalesPlanCumulativeChart(rows)}</article>
    </div>
    <article class="km-sales-plan-card">
      <header class="km-sales-plan-head"><div><h3>Месячный план/факт</h3><p>Редактируются план продаж, план в штуках и рекламный бюджет.</p></div><button type="button" data-km-sales-plan-save>Сохранить месяцы</button></header>
      <div class="finance-table-wrap"><table class="finance-table km-sales-plan-table">
        <thead><tr><th>Месяц</th><th>План, шт</th><th>Факт, шт</th><th>План, ₽</th><th>Факт, ₽</th><th>П/Ф</th><th>Реклама план, ₽</th><th>Реклама факт, ₽</th><th>План накоп., ₽</th><th>Факт накоп., ₽</th><th>П/Ф накоп.</th></tr></thead>
        <tbody>${rows.map((row) => `<tr data-km-sales-month="${escapeHtml(row.month_start)}">
          <th>${escapeHtml(kmSalesPlanMonthLabel(row.month_start))}</th>
          <td><input class="finance-input" data-km-sales-field="plan_units" type="number" min="0" step="1" value="${escapeHtml(kmSalesPlanInput(row.plan_units))}" placeholder="0" /></td>
          <td class="num">${formatNumber(row.actual_units, 0)}</td>
          <td><input class="finance-input" data-km-sales-field="plan_revenue" type="number" min="0" step="1000" value="${escapeHtml(kmSalesPlanInput(row.plan_revenue))}" placeholder="0" /></td>
          <td class="num">${kmSalesPlanMoney(row.actual_revenue)}</td>
          <td class="num">${kmSalesPlanPct(row.plan_fact_pct)}</td>
          <td><input class="finance-input" data-km-sales-field="ad_spend_plan" type="number" min="0" step="1000" value="${escapeHtml(kmSalesPlanInput(row.ad_spend_plan))}" placeholder="0" /></td>
          <td class="num">${kmSalesPlanMoney(row.actual_ad_spend)}</td>
          <td class="num">${kmSalesPlanMoney(row.plan_revenue_cum)}</td>
          <td class="num">${kmSalesPlanMoney(row.actual_revenue_cum)}</td>
          <td class="num">${kmSalesPlanPct(row.plan_fact_cum_pct)}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </article>
    <article class="km-sales-plan-card">
      <header class="km-sales-plan-head"><div><h3>План по товарам</h3><p>Годовая раскладка по SKU. Итог годового графика берётся из месячного плана выше.</p></div><button type="button" data-km-sales-plan-save>Сохранить товары</button></header>
      <div class="finance-table-wrap"><table class="finance-table km-sales-plan-product-table">
        <thead><tr><th>SKU / товар</th><th>Цена, ₽</th><th>План, шт</th><th>Факт, шт</th><th>План, ₽</th><th>Факт, ₽</th><th>П/Ф</th></tr></thead>
        <tbody>${products.map((row) => `<tr data-km-sales-product="${escapeHtml(row.sku)}" data-article="${escapeHtml(row.article || "")}" data-product-name="${escapeHtml(row.product_name || "")}">
          <td class="finance-product finance-sticky-product"><strong>${escapeHtml(row.article || row.sku)}</strong><span>${escapeHtml(row.product_name || "")}</span><small>SKU ${escapeHtml(row.sku)}</small></td>
          <td><input class="finance-input" data-km-product-field="price_rub" type="number" min="0" step="1" value="${escapeHtml(kmSalesPlanInput(row.price_rub))}" /></td>
          <td><input class="finance-input" data-km-product-field="plan_units" type="number" min="0" step="1" value="${escapeHtml(kmSalesPlanInput(row.plan_units))}" placeholder="0" /></td>
          <td class="num">${formatNumber(row.actual_units, 0)}</td>
          <td><input class="finance-input" data-km-product-field="plan_revenue" type="number" min="0" step="1000" value="${escapeHtml(kmSalesPlanInput(row.plan_revenue))}" placeholder="0" /></td>
          <td class="num">${kmSalesPlanMoney(row.actual_revenue)}</td>
          <td class="num">${kmSalesPlanPct(row.plan_fact_pct)}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </article>
    <p class="km-sales-plan-note">${escapeHtml(payload.methodology || "")}</p>
  `;
}

async function loadKmSalesPlanSection() {
  const root = qs("kmSalesPlanSection");
  if (!root || currentClient() !== "km_trade" || state.dashboard !== "planfact") return;
  const selected = qs("date_from")?.value || new Date().toISOString().slice(0, 10);
  const year = selected.slice(0, 4);
  root.innerHTML = '<div class="km-sales-plan-loading">Загрузка годового плана KM Trade…</div>';
  try {
    const payload = await getJson(`/api/km-trade/sales-plan?client=${encodeURIComponent(currentClient())}&date_from=${encodeURIComponent(`${year}-01-01`)}&date_to=${encodeURIComponent(`${year}-12-31`)}`);
    if (currentClient() === "km_trade" && state.dashboard === "planfact") renderKmSalesPlanPayload(payload);
  } catch (error) {
    root.innerHTML = `<div class="finance-alert finance-alert-warning">Ошибка плана продаж: ${escapeHtml(error.message)}</div>`;
  }
}

function kmSalesPlanNumeric(input) {
  if (!input || input.value.trim() === "") return 0;
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < 0) throw new Error(`Некорректное плановое значение: ${input.value}`);
  return value;
}

async function saveKmSalesPlan() {
  if (!kmSalesPlanPayload) return;
  const root = qs("kmSalesPlanSection");
  const months = [...root.querySelectorAll("[data-km-sales-month]")].map((tr) => {
    const row = { month_start: tr.dataset.kmSalesMonth };
    tr.querySelectorAll("[data-km-sales-field]").forEach((input) => {
      row[input.dataset.kmSalesField] = kmSalesPlanNumeric(input);
    });
    return row;
  });
  const products = [...root.querySelectorAll("[data-km-sales-product]")].map((tr) => {
    const row = {
      sku: tr.dataset.kmSalesProduct,
      article: tr.dataset.article || "",
      product_name: tr.dataset.productName || "",
    };
    tr.querySelectorAll("[data-km-product-field]").forEach((input) => {
      row[input.dataset.kmProductField] = kmSalesPlanNumeric(input);
    });
    return row;
  });
  const year = Number(String(kmSalesPlanPayload.period?.date_from || "").slice(0, 4));
  await postJson("/api/km-trade/sales-plan", { client: currentClient(), year, months, products });
  await loadData();
}

qs("financeDashboard")?.addEventListener("change", (event) => {
  const input = event.target.closest("[data-finance-cogs-file]");
  if (!input) return;
  const file = input.files?.[0];
  input.value = "";
  importFinanceCogs(file).catch((error) => {
    setStatusError(error);
  });
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-km-sales-plan-save]");
  if (!button) return;
  button.disabled = true;
  saveKmSalesPlan()
    .catch((error) => {
      setStatusError(error);
    })
    .finally(() => {
      if (button.isConnected) button.disabled = false;
    });
});

function renderPlanFactCharts(dailyRows, monthlyRows, scorecardRows = []) {
  lastPlanFactDailyRows = dailyRows;
  lastPlanFactMonthlyRows = monthlyRows;
  lastPlanFactScorecardRows = scorecardRows;
  qs("primaryChartTitle").textContent = "Доходы";
  qs("chartMetric").textContent = "продажи, план и прогноз выполнения";
  qs("barChart").classList.remove("bar-chart");
  qs("barChart").innerHTML = `${renderPlanFactMonthSummaryCharts(scorecardRows, "revenue")}${renderPlanFactChart(dailyRows, "revenue")}`;

  qs("secondaryChartTitle").textContent = "Расходы";
  qs("secondaryChartMetric").textContent = "факт расходов и дневной бюджет";
  qs("abcChart").innerHTML = `${renderPlanFactMonthSummaryCharts(scorecardRows, "expense")}${renderPlanFactChart(dailyRows, "expense")}`;
  qs("abcChart").closest(".panel").classList.remove("hidden");
}

function adminRowsForMode(payload) {
  const rows = payload.rows || [];
  if (state.adminImportMode === "apiDaily") {
    return rows
      .filter((row) => row.api_daily)
      .sort((a, b) => Number(a.api_daily_order ?? 999) - Number(b.api_daily_order ?? 999));
  }
  if (state.adminImportMode !== "daily") return rows;
  return rows
    .filter((row) => row.daily)
    .sort((a, b) => Number(a.daily_order ?? 999) - Number(b.daily_order ?? 999));
}

function adminDailyMarketplaceLabel(payload) {
  const supported = new Set();
  (payload.rows || [])
    .filter((row) => row.daily)
    .forEach((row) => {
      (Array.isArray(row.marketplaces) ? row.marketplaces : []).forEach((marketplace) => supported.add(marketplace));
    });
  const labels = [
    ["ozon", "Ozon"],
    ["wb", "WB"],
  ].filter(([marketplace]) => supported.has(marketplace)).map(([, label]) => label);
  return labels.length ? labels.join(" + ") : "подключённые площадки";
}

function adminDailyStage(row) {
  return row.daily_stage === "views" ? "views" : "imports";
}

function adminDailyGroups(payload) {
  const rows = adminRowsForMode(payload);
  return {
    imports: rows.filter((row) => adminDailyStage(row) === "imports"),
    views: rows.filter((row) => adminDailyStage(row) === "views"),
  };
}

function adminStatusFor(row) {
  return state.adminImportStatuses[row.key] || { state: "idle", title: "Ожидает запуска", detail: "В очереди не стоит" };
}

function boundedProgressPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function adminProgressFromLine(line) {
  const text = String(line || "");
  const percentMatch = text.match(/(?:^|\s|\()((?:100|[1-9]?\d)(?:[.,]\d+)?)%\)?\|?/);
  const progress = {};
  if (percentMatch) {
    const progressPct = boundedProgressPct(String(percentMatch[1]).replace(",", "."));
    progress.progressPct = progressPct;
    progress.progressText = `${progressPct}%`;
  }
  const fractionMatch = text.match(/([0-9][0-9\s,]*)\s*\/\s*([0-9][0-9\s,]*)/);
  if (fractionMatch) {
    const current = Number(fractionMatch[1].replace(/\D/g, ""));
    const total = Number(fractionMatch[2].replace(/\D/g, ""));
    if (total > 0) {
      progress.progressPct = progress.progressPct ?? boundedProgressPct((current / total) * 100);
      progress.progressText = `${current.toLocaleString("ru-RU")} / ${total.toLocaleString("ru-RU")}`;
    }
  }
  const loadedRowsMatch = text.match(/(?:строк(?:и)?|rows)\D{0,18}([0-9][0-9\s,]*)\s*\/\s*([0-9][0-9\s,]*)/i);
  if (loadedRowsMatch) {
    const rows = Number(loadedRowsMatch[1].replace(/\D/g, ""));
    const totalRows = Number(loadedRowsMatch[2].replace(/\D/g, ""));
    if (rows >= 0 && totalRows > 0) {
      progress.rowsText = `строки ${rows.toLocaleString("ru-RU")} / ${totalRows.toLocaleString("ru-RU")}`;
    }
  }
  const rowsMatch = text.match(/([0-9][0-9\s,]*)\s+rows\b/i);
  if (rowsMatch) {
    const rows = Number(rowsMatch[1].replace(/\D/g, ""));
    if (rows > 0) {
      progress.rowsText = `строки ${rows.toLocaleString("ru-RU")}`;
      progress.progressText = progress.progressText || `${rows.toLocaleString("ru-RU")} строк`;
    }
  }
  const elapsedMatch = text.match(/(?:прошло|elapsed|time)\D{0,12}([0-9]+(?::[0-9]{2}){1,2}|[0-9.]+\s*s)/i);
  if (elapsedMatch) progress.elapsedText = `прошло ${elapsedMatch[1]}`;
  if (text.includes("ПРОГРЕСС")) progress.line = text;
  return Object.keys(progress).length ? progress : null;
}

function renderAdminStatus(status) {
  const title = status.title || "";
  const detail = status.detail || "";
  return `
    <div class="admin-run-status is-${escapeHtml(status.state || "idle")}">
      <i></i>
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
    </div>
  `;
}

function renderAdminLog(status) {
  const log = status.log || (status.state === "idle" ? "Запуска еще не было" : status.detail || "");
  return `<div class="admin-short-log is-${escapeHtml(status.state || "idle")}">${escapeHtml(log)}</div>`;
}

function renderAdminProgress(status) {
  const stateName = status.state || "idle";
  const explicitPct = boundedProgressPct(status.progressPct);
  const fallbackPct = stateName === "done" ? 100 : stateName === "queued" || stateName === "idle" ? 0 : null;
  const progressPct = explicitPct ?? fallbackPct ?? 12;
  const progressText = status.progressText || (stateName === "done" ? "100%" : stateName === "queued" ? "В очереди" : stateName === "idle" ? "0%" : "В работе");
  return `
    <div class="admin-row-progress is-${escapeHtml(stateName)}">
      <div class="admin-progress-bar" style="--progress:${progressPct}%"><i></i></div>
      <span class="admin-progress-text">${escapeHtml(progressText)}</span>
    </div>
  `;
}

function renderAdminLiveProgress() {
  const progress = state.adminLiveProgress;
  if (!progress) {
    return `
      <div class="admin-live-progress is-idle">
        <div class="admin-live-progress-main">
          <strong>Процесс не запущен</strong>
          <span>строки 0 / 0</span>
          <span>прошло 00:00:00</span>
        </div>
        <div class="admin-progress-bar" style="--progress:0%"><i></i></div>
        <code>Ожидает запуска импорта</code>
      </div>
    `;
  }
  const stateName = progress.state || "active";
  const progressPct = boundedProgressPct(progress.progressPct) ?? (stateName === "done" ? 100 : 8);
  return `
    <div class="admin-live-progress is-${escapeHtml(stateName)}" id="adminLiveProgress">
      <div class="admin-live-progress-main">
        <strong>${escapeHtml(progress.title || "Импорт")}</strong>
        <span>${escapeHtml(progress.rowsText || progress.progressText || "строки 0 / ?")}</span>
        <span>${escapeHtml(progress.elapsedText || "прошло 00:00:00")}</span>
      </div>
      <div class="admin-progress-bar" style="--progress:${progressPct}%"><i></i></div>
      <code>${escapeHtml(progress.line || progress.progressText || "В работе")}</code>
    </div>
  `;
}

function updateAdminLiveProgressElement() {
  const slot = qs("adminLiveProgressSlot");
  if (!slot) return;
  slot.innerHTML = renderAdminLiveProgress();
}

function updateAdminLiveProgress(row, line, parsed = null, stateName = "active") {
  const current = state.adminLiveProgress || {};
  const progress = parsed || adminProgressFromLine(line) || {};
  state.adminLiveProgress = {
    ...current,
    state: stateName,
    title: row?.report || current.title || "Импорт",
    progressPct: progress.progressPct ?? current.progressPct,
    progressText: progress.progressText || current.progressText || "В работе",
    rowsText: progress.rowsText || current.rowsText || "строки 0 / ?",
    elapsedText: progress.elapsedText || current.elapsedText || "прошло 00:00:00",
    line: String(line || progress.line || current.line || "").trim() || current.line || "В работе",
  };
  updateAdminLiveProgressElement();
}

function renderAdminTimeline(rows) {
  if (!["daily", "apiDaily"].includes(state.adminImportMode)) return "";
  return `
    <div class="admin-import-timeline">
      <div class="admin-timeline-title">Статус ежедневного процесса</div>
      ${rows.map((row, index) => {
        const status = adminStatusFor(row);
        return `
          <div class="admin-timeline-item is-${escapeHtml(status.state || "idle")}">
            <div class="admin-timeline-marker">${status.state === "done" ? "✓" : status.state === "stopped" ? "!" : index + 1}</div>
            <div>
              <strong>${escapeHtml(row.report)}</strong>
              <span>${escapeHtml(status.detail || status.title || "Ожидает запуска")}</span>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function terminalTime() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function appendAdminTerminalLine(text, type = "output") {
  state.adminTerminalLines.push({ time: terminalTime(), text: String(text || ""), type });
  if (state.adminTerminalLines.length > 600) {
    state.adminTerminalLines = state.adminTerminalLines.slice(-600);
  }
  updateAdminTerminal();
}

function updateAdminTerminal() {
  const body = qs("adminTerminalBody");
  if (!body) return;
  body.innerHTML = state.adminTerminalLines.length
    ? state.adminTerminalLines.map((line) => `
      <div class="admin-terminal-line is-${escapeHtml(line.type)}">
        <span>${escapeHtml(line.time)}</span>
        <code>${escapeHtml(line.text)}</code>
      </div>
    `).join("")
    : '<div class="admin-terminal-empty">Терминал готов. Запусти импорт, и здесь появится live-лог скриптов.</div>';
  body.scrollTop = body.scrollHeight;
}

function updateApiTerminal() {
  const body = qs("apiExportTerminalBody");
  if (!body) return;
  body.innerHTML = state.apiTerminalLines.length
    ? state.apiTerminalLines.map((line) => `
      <div class="api-terminal-line is-${escapeHtml(line.type || "output")}">
        <span>${escapeHtml(line.time)}</span>
        <code>${escapeHtml(line.text)}</code>
      </div>
    `).join("")
    : '<div class="api-terminal-empty">Терминал готов. Запусти метод, и здесь появится ход выгрузки.</div>';
  body.scrollTop = body.scrollHeight;
}

function appendApiTerminalLine(text, type = "output") {
  state.apiTerminalLines.push({ time: terminalTime(), text: String(text || ""), type });
  if (state.apiTerminalLines.length > 300) {
    state.apiTerminalLines = state.apiTerminalLines.slice(-300);
  }
  updateApiTerminal();
}

const ADMIN_ACTION_ICONS = {
  manual: '<path d="M12 4v10m0 0 4-4m-4 4-4-4"/><path d="M5 15v4h14v-4"/>',
  daily: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4m8-4v4M4 9h16"/><path d="M9 14l2 2 4-4"/>',
  allDaily: '<path d="M20 7h-5V2"/><path d="M20 2l-5 5a8 8 0 1 0 2.3 5.7"/><circle cx="7" cy="8" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="16" cy="9" r="2"/>',
  apiDaily: '<path d="M7 17H6a4 4 0 0 1-.4-8A6 6 0 0 1 17 7.6 4.5 4.5 0 0 1 18 16h-1"/><path d="M12 11v9m0 0 3-3m-3 3-3-3"/>',
  api: '<path d="M8 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2M16 5h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-2"/><path d="m10 8-3 4 3 4m4-8 3 4-3 4"/>',
  client: '<circle cx="9" cy="8" r="3"/><path d="M4 20v-2a5 5 0 0 1 10 0v2m4-10v6m-3-3h6"/>',
  logout: '<path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4"/><path d="M14 8l4 4-4 4m4-4H9"/>',
  users: '<circle cx="8" cy="8" r="3"/><circle cx="16" cy="9" r="2.5"/><path d="M3 20v-2a5 5 0 0 1 10 0v2m1-5a4 4 0 0 1 7 3v2"/>',
  database: '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v10c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 10c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
  historyDownload: '<path d="M12 3v11m0 0 4-4m-4 4-4-4"/><path d="M5 16v4h14v-4"/>',
  historyData: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v8c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M12 11v7m0 0 3-3m-3 3-3-3"/>',
  assortment: '<path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h5"/>',
  funnelStep: '<path d="M4 5h16l-6 7v5l-4 2v-7z"/>',
  stock: '<path d="M4 8l8-4 8 4-8 4z"/><path d="M4 8v8l8 4 8-4V8"/><path d="M12 12v8"/>',
  advertising: '<path d="M4 13l12-6v10L4 13z"/><path d="M4 13v4a2 2 0 0 0 2 2h1"/><path d="M18 9v6"/>',
  finance: '<path d="M4 18h16"/><path d="M7 14v4m5-9v9m5-12v12"/>',
  marketplace: '<path d="M4 7h16M6 7l1-3h10l1 3M6 7v11h12V7"/><path d="M9 11h6"/>',
  full: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
  imports: '<path d="M12 3v11m0 0 4-4m-4 4-4-4"/><path d="M5 16v3h14v-3"/>',
  views: '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 11v6c0 1.7 3.1 3 7 3 2.1 0 4-.4 5.3-1.2"/><path d="m18 15 2 2-2 2"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  resume: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6"/><path d="M4 4v4.6h4.6"/><path d="m10 8 6 4-6 4z"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.7 2.7L16.5 9"/>',
  error: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
  clear: '<path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/>',
  run: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
  validate: '<path d="M5 4h10l4 4v12H5z"/><path d="M15 4v4h4m-10 5 2 2 4-4"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6"/><path d="M4 4v4.6h4.6M12 8v4l3 2"/>',
  expand: '<path d="M6 9l6 6 6-6"/>',
  collapse: '<path d="M18 15l-6-6-6 6"/>',
  save: '<path d="M5 4h12l2 2v14H5z"/><path d="M8 4v6h8V4M8 20v-6h8v6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>',
  eyeOff: '<path d="m3 3 18 18"/><path d="M10.6 6.1A11.8 11.8 0 0 1 12 6c6.5 0 10 6 10 6a17 17 0 0 1-3 3.7M6.2 6.2C3.5 8 2 12 2 12s3.5 6 10 6a10.8 10.8 0 0 0 3.2-.5M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
};

function adminActionIcon(name) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${ADMIN_ACTION_ICONS[name] || ADMIN_ACTION_ICONS.run}</svg>`;
}

function adminIconButton(label, icon, attributes = "", classes = "", disabled = false) {
  return `<button type="button" class="admin-icon-button ${classes}" ${attributes} title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}" ${disabled ? "disabled" : ""}>${adminActionIcon(icon)}</button>`;
}

function withAdminTopbarCaption(buttonHtml, caption) {
  return buttonHtml.replace("</button>", `<span>${escapeHtml(caption)}</span></button>`);
}

const ADMIN_SECTION_CONFIGS = [
  { key: "manual", icon: "manual", label: "Ручные загрузки", caption: "Ручные" },
  { key: "daily", icon: "daily", label: "Ежедневный импорт выгрузок", caption: "Ежедн." },
  { key: "allDaily", icon: "allDaily", label: "Обновление всех аккаунтов", caption: "Все" },
  { key: "apiDaily", icon: "apiDaily", label: "Ежедневная выгрузка API", caption: "API день" },
  { key: "api", icon: "api", label: "Выгрузка API", caption: "API" },
  { key: "client", icon: "client", label: "Клиенты", caption: "Клиенты" },
  { key: "clientOnboarding", icon: "marketplace", label: "Добавление магазина", caption: "Добавление магазина" },
  { key: "database", icon: "database", label: "Структура БД", caption: "БД" },
  { key: "integrations", icon: "api", label: "Интеграции", caption: "Интеграции" },
  { key: "users", icon: "users", label: "Пользователи и доступы", caption: "Права" },
];
const ADMIN_SECTION_ATTRS = {
  manual: `data-admin-mode="manual"`,
  daily: `data-admin-mode="daily"`,
  allDaily: `data-admin-mode="allDaily"`,
  apiDaily: `data-admin-mode="apiDaily"`,
  api: `data-admin-mode="api"`,
  client: `data-admin-mode="client"`,
  clientOnboarding: `data-admin-mode="clientOnboarding"`,
  database: `data-admin-mode="database"`,
  integrations: `data-admin-mode="integrations"`,
  users: `data-admin-mode="users"`,
};

function adminSectionKeys() {
  return ADMIN_SECTION_CONFIGS.map((section) => section.key);
}

function adminAllowedModeSet(payload = {}) {
  let source;
  if (payload.full_admin || state.adminFullAccess) source = adminSectionKeys();
  else if (Array.isArray(payload.allowed_admin_sections)) source = payload.allowed_admin_sections;
  else if (Array.isArray(state.adminAllowedSections)) source = state.adminAllowedSections;
  else source = adminSectionKeys();
  const allowed = new Set(source);
  if (allowed.has("client")) allowed.add("clientOnboarding");
  if (payload.api_daily_enabled === false) allowed.delete("apiDaily");
  return allowed;
}

function normalizeAdminModeForAccess(payload = {}) {
  const allowed = adminAllowedModeSet(payload);
  if (allowed.has(state.adminImportMode)) return state.adminImportMode;
  const fallback = ADMIN_SECTION_CONFIGS.find((section) => allowed.has(section.key));
  return fallback?.key || "manual";
}

function adminTopbarTabsMarkup(payload = {}) {
  const mode = state.adminImportMode;
  const allowed = adminAllowedModeSet(payload);
  const isDaily = mode === "daily";
  const isAllDaily = mode === "allDaily";
  const isApiDaily = mode === "apiDaily";
  const isApi = mode === "api";
  const isClients = mode === "client";
  const isClientOnboarding = mode === "clientOnboarding";
  const isDatabase = mode === "database";
  const isIntegrations = mode === "integrations";
  const isUsers = mode === "users";
  const isManual = !isDaily && !isAllDaily && !isApiDaily && !isApi && !isClients && !isClientOnboarding && !isDatabase && !isIntegrations && !isUsers;
  return `
    <nav class="admin-tabs admin-topbar-tabs" aria-label="Разделы админки">
      ${allowed.has("manual") ? withAdminTopbarCaption(adminIconButton("Ручные загрузки", "manual", ADMIN_SECTION_ATTRS.manual, `admin-tab admin-topbar-tab ${isManual ? "active" : ""}`), "Ручные") : ""}
      ${allowed.has("daily") ? withAdminTopbarCaption(adminIconButton("Ежедневный импорт выгрузок", "daily", ADMIN_SECTION_ATTRS.daily, `admin-tab admin-topbar-tab ${isDaily ? "active" : ""}`), "Ежедн.") : ""}
      ${allowed.has("allDaily") ? withAdminTopbarCaption(adminIconButton("Обновление всех аккаунтов", "allDaily", ADMIN_SECTION_ATTRS.allDaily, `admin-tab admin-topbar-tab ${isAllDaily ? "active" : ""}`), "Все") : ""}
      ${payload.api_daily_enabled && allowed.has("apiDaily") ? withAdminTopbarCaption(adminIconButton("Ежедневная выгрузка API", "apiDaily", ADMIN_SECTION_ATTRS.apiDaily, `admin-tab admin-topbar-tab ${isApiDaily ? "active" : ""}`), "API день") : ""}
      ${allowed.has("api") ? withAdminTopbarCaption(adminIconButton("Выгрузка API", "api", ADMIN_SECTION_ATTRS.api, `admin-tab admin-topbar-tab ${isApi ? "active" : ""}`), "API") : ""}
      ${allowed.has("client") ? withAdminTopbarCaption(adminIconButton("Клиенты", "client", ADMIN_SECTION_ATTRS.client, `admin-tab admin-topbar-tab ${isClients ? "active" : ""}`), "Клиенты") : ""}
      ${allowed.has("clientOnboarding") ? withAdminTopbarCaption(adminIconButton("Добавление магазина", "marketplace", ADMIN_SECTION_ATTRS.clientOnboarding, `admin-tab admin-topbar-tab ${isClientOnboarding ? "active" : ""}`), "Добавление магазина") : ""}
      ${allowed.has("database") ? withAdminTopbarCaption(adminIconButton("Структура БД", "database", ADMIN_SECTION_ATTRS.database, `admin-tab admin-topbar-tab ${isDatabase ? "active" : ""}`), "БД") : ""}
      ${allowed.has("integrations") ? withAdminTopbarCaption(adminIconButton("Интеграции", "api", ADMIN_SECTION_ATTRS.integrations, `admin-tab admin-topbar-tab ${isIntegrations ? "active" : ""}`), "Интеграции") : ""}
      ${allowed.has("users") ? withAdminTopbarCaption(adminIconButton("Пользователи и доступы", "users", ADMIN_SECTION_ATTRS.users, `admin-tab admin-topbar-tab ${isUsers ? "active" : ""}`), "Права") : ""}
    </nav>
  `;
}

function clearAdminTopbarTabs() {
  document.querySelector(".admin-topbar-tabs")?.remove();
  document.body.classList.remove("admin-shell-active");
  document.querySelector(".client-switcher")?.classList.toggle("hidden", state.clientLocked || state.dashboard === "admin");
}

function syncAdminTopbarTabs(payload = {}) {
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  topbar.querySelector(".admin-topbar-tabs")?.remove();
  document.body.classList.toggle("admin-shell-active", state.dashboard === "admin");
  const switcher = topbar.querySelector(".client-switcher");
  if (state.dashboard !== "admin") {
    switcher?.classList.toggle("hidden", state.clientLocked);
    return;
  }
  switcher?.classList.add("hidden");
  const anchor = topbar.querySelector("#backToAbc") || switcher || topbar.lastElementChild;
  if (anchor) anchor.insertAdjacentHTML("beforebegin", adminTopbarTabsMarkup(payload));
  else topbar.insertAdjacentHTML("beforeend", adminTopbarTabsMarkup(payload));
}

function switchAdminModeFromUi(adminModeButton) {
  if (!adminModeButton || state.dashboard !== "admin") return;
  const requestedMode = adminModeButton.dataset.adminMode || "manual";
  if (!adminAllowedModeSet(lastAdminPayload || {}).has(requestedMode)) {
    qs("status").textContent = "Нет доступа к разделу админки";
    return;
  }
  state.adminImportMode = requestedMode;
  renderAdminDashboardContent(lastAdminPayload);
  scheduleAdminAllClientsDailyPoll();
  if (state.adminImportMode === "allDaily") {
    Promise.all([loadAdminAllClientsDaily(false), loadAdminAllClientsAssortment(false)])
      .then(() => renderAdminDashboardContent(lastAdminPayload))
      .catch((error) => { setStatusError(error); });
  }
  if (["client", "clientOnboarding"].includes(state.adminImportMode)) {
    const registryReady = state.adminClientRegistry
      ? Promise.resolve()
      : loadAdminClientRegistry();
    registryReady
      .then(() => {
        renderAdminDashboardContent(lastAdminPayload);
        if (state.adminImportMode === "clientOnboarding") return restoreAdminClientHistoryContext();
        if (state.adminImportMode === "client") return restoreAdminClientAssortmentContext();
        return null;
      })
      .catch((error) => { setStatusError(error); });
  }
  if (state.adminImportMode === "users") {
    const registryReady = state.adminUsersRegistry ? Promise.resolve() : loadAdminUsersRegistry();
    registryReady
      .then(() => renderAdminDashboardContent(lastAdminPayload))
      .catch((error) => { setStatusError(error); });
  }
  if (state.adminImportMode === "database") {
    const overviewReady = state.adminDatabaseOverview ? Promise.resolve() : loadAdminDatabaseOverview();
    overviewReady
      .then(() => renderAdminDashboardContent(lastAdminPayload))
      .catch((error) => { setStatusError(error); });
  }
  if (state.adminImportMode === "integrations") {
    const ready = state.adminIntegrations ? Promise.resolve() : loadAdminIntegrations();
    ready.then(() => renderAdminDashboardContent(lastAdminPayload))
      .catch((error) => { setStatusError(error); });
  }
  persistDashboardState();
}

function renderAdminTerminal() {
  return `
    <section class="admin-terminal">
      <header>
        <div>
          <h3>Терминал процесса</h3>
          <span>Live-вывод всех запускаемых скриптов</span>
        </div>
        ${adminIconButton("Очистить терминал", "clear", "data-clear-admin-terminal", "ghost")}
      </header>
      <div id="adminLiveProgressSlot">${renderAdminLiveProgress()}</div>
      <div id="adminTerminalBody" class="admin-terminal-body"></div>
    </section>
  `;
}

function renderAdminImportTable(rows, isDaily) {
  return `
    <div class="admin-import-table ${isDaily ? "daily-import-table" : ""}">
      <div class="admin-import-head">
        <span>Отчет</span>
        <span>Скрипт импорта</span>
        <span>Источник данных</span>
        <span>Куда импортирует</span>
        ${isDaily ? "<span>Статус обновления</span><span>Краткий лог</span><span>Запуск</span>" : "<span></span>"}
      </div>
      ${rows.map((row) => {
        const status = adminStatusFor(row);
        const buttonLabel = isDaily && adminDailyStage(row) === "views" ? "Обновить витрину" : "Запустить импорт";
        return `
          <div class="admin-import-row is-${escapeHtml(status.state || "idle")}">
            <div class="admin-import-report">
              <strong>${escapeHtml(row.report)}</strong>
              <span>${escapeHtml(row.description || "")}</span>
              <span class="admin-import-policy">${escapeHtml(row.policy || "")}</span>
            </div>
            <code title="${escapeHtml(row.script)}">${escapeHtml(row.script)}</code>
            <code title="${escapeHtml(row.source)}">${escapeHtml(row.source)}</code>
            <span>${escapeHtml(row.destination)}</span>
            ${isDaily ? `<div>${renderAdminStatus(status)}${renderAdminProgress(status)}</div>${renderAdminLog(status)}` : ""}
            <div class="admin-import-action">
              ${adminIconButton(buttonLabel, isDaily && adminDailyStage(row) === "views" ? "views" : "run", `data-run-import="${escapeHtml(row.key)}"`, "", !(row.script_exists && !state.adminImportRunning))}
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderAdminClientSelector(payload) {
  const clients = Array.isArray(payload?.clients) && payload.clients.length
    ? payload.clients
    : [{ key: "toptop", label: "TOPTOP", status: "active", description: "Рабочий клиент" }, { key: "lera_nena", label: "LERA NENA", status: "active", description: "Рабочий клиент" }];
  const current = payload?.client || state.adminClient || "toptop";
  const label = payload?.client_label || clients.find((client) => client.key === current)?.label || "TOPTOP";
  const status = payload?.client_status === "active" ? "Подключен" : "Подготовка";
  const description = payload?.client_description || "";
  return `
    <div class="admin-client-bar">
      <label>
        <span>Аккаунт</span>
        <select data-admin-client>
          ${clients.map((client) => `
            <option value="${escapeHtml(client.key)}" ${client.key === current ? "selected" : ""}>${escapeHtml(client.label)}</option>
          `).join("")}
        </select>
      </label>
      <div class="admin-client-meta">
        <strong>${escapeHtml(label)}</strong>
        <span class="admin-client-status is-${escapeHtml(payload?.client_status || "active")}">${escapeHtml(status)}</span>
        <span>${escapeHtml(description)}</span>
      </div>
      ${adminIconButton("Выйти из админки", "logout", "data-admin-logout", "ghost admin-auth-logout")}
    </div>
  `;
}

function canRunAdminClient(payload = lastAdminPayload) {
  return (payload?.client_status || "active") === "active";
}

function renderWbApiExportsBlock(payload) {
  const wbApi = payload?.wb_api || {};
  const tokenStatus = wbApi.token_saved ? "Ключ сохранен" : "Ключ не сохранен";
  return `
    <section class="wb-api-panel wb-api-modern">
      <header class="wb-api-header">
        <div>
          <h3>WB API</h3>
          <span>Read-only выгрузки обычной рекламы WB через Promotion API</span>
        </div>
        <span id="wbApiTokenBadge" class="wb-api-badge ${wbApi.token_saved ? "is-saved" : ""}">${escapeHtml(tokenStatus)}</span>
      </header>

      <div class="wb-api-token-row">
        <label>
          <span>WB API ключ</span>
          <input id="wbApiTokenInput" type="password" autocomplete="off" placeholder="${wbApi.token_saved ? "Новый ключ, если нужно заменить" : "Вставьте ключ категории Promotion"}" />
        </label>
        <button type="button" data-save-wb-api-token>Сохранить ключ</button>
        <div id="wbApiTokenStatus" class="wb-api-token-status">
          ENV: ${escapeHtml(wbApi.token_env || "WB_API_TOKEN")} · ${escapeHtml(wbApi.env_file || "")}
        </div>
      </div>

      <div class="wb-api-sources">
        <section class="wb-api-source wb-api-source-promotion">
          <header class="wb-api-source-header">
            <div>
              <strong>WB Promotion API · обычная реклама</strong>
              <span>advert-api.wildberries.ru · товарное продвижение, read-only режим</span>
            </div>
            <span class="wb-api-source-tag is-promotion">Официальный API</span>
          </header>

          <article class="wb-api-method-card">
            <div class="wb-api-method-copy">
              <strong>Список ID кампаний</strong>
              <span>GET /adv/v1/promotion/count · источник ID для detail-запросов</span>
              <code>${escapeHtml(wbApi.promotion_count_output_dir || "")}</code>
            </div>
            <div class="wb-api-card-actions">
              <button type="button" data-run-wb-promotion-count>Получить данные</button>
              ${renderWbApiStopButton("promotion_count")}
              ${renderApiExportLogButton("promotion_count", state.wbApiLastLogFiles.promotion_count)}
              <button type="button" class="ghost" data-open-wb-promotion-count-result ${state.wbApiLastPromotionCountResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbPromotionCountResult" class="wb-api-result">ID кампаний обычной рекламы еще не запрашивались.</div>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Карточки кампаний</strong>
              <span>GET /api/advert/v2/adverts · статусы, ставки, размещения, nmId; ID берутся из promotion/count пачками до 50</span>
              <code>${escapeHtml(wbApi.promotion_adverts_output_dir || "")}</code>
            </div>
            <div class="wb-api-filter-grid wb-api-filter-grid-short">
              <label>
                <span>Статусы</span>
                <div id="wbPromotionAdvertsStatusesDropdown" class="dropdown-multi wb-api-status-dropdown">
                  <button id="wbPromotionAdvertsStatusesToggle" class="dropdown-toggle" type="button" data-wb-promotion-status-toggle>Активные + завершенные: 9, 11, 7</button>
                  <div id="wbPromotionAdvertsStatusesMenu" class="dropdown-menu hidden"></div>
                </div>
                <input id="wbPromotionAdvertsStatuses" type="hidden" value="${escapeHtml(WB_PROMOTION_ADVERTS_DEFAULT_STATUSES.join(","))}" />
              </label>
              <button type="button" data-run-wb-promotion-adverts>Получить список</button>
              ${renderWbApiStopButton("promotion_adverts")}
              ${renderApiExportLogButton("promotion_adverts", state.wbApiLastLogFiles.promotion_adverts)}
              <button type="button" class="ghost" data-open-wb-promotion-adverts-result ${state.wbApiLastPromotionAdvertsResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbPromotionAdvertsResult" class="wb-api-result">Карточки кампаний обычной рекламы еще не запрашивались.</div>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Статистика по дням</strong>
              <span>GET /adv/v3/fullstats · до 50 кампаний и до 31 дня на запрос, пауза 20 сек.</span>
              <code>${escapeHtml(wbApi.promotion_stats_output_dir || "")}</code>
            </div>
            <div class="wb-api-stats-grid wb-api-stats-grid-promotion">
              <label>
                <span>ID кампаний</span>
                <textarea id="wbPromotionStatsCampaignIds" rows="2" placeholder="Авто: все ID из последней выгрузки карточек кампаний"></textarea>
              </label>
              <label>
                <span>Дата с</span>
                <input id="wbPromotionStatsDateFrom" type="date" />
              </label>
              <label>
                <span>Дата по</span>
                <input id="wbPromotionStatsDateTo" type="date" />
              </label>
              <label>
                <span>Авто-ID</span>
                <select id="wbPromotionStatsAutoMode">
                  <option value="active_period" selected>По интервалу кампании</option>
                  <option value="strict_started">Только с датой запуска</option>
                  <option value="all_status">Все 7/9/11 из карточек</option>
                </select>
              </label>
              <label>
                <span>Окно запроса</span>
                <select id="wbPromotionStatsWindowMode">
                  <option value="campaign_lifetime" selected>По кампаниям за срок жизни</option>
                  <option value="period">За период</option>
                  <option value="day">По дням</option>
                </select>
              </label>
              <label>
                <span>Батч</span>
                <select id="wbPromotionStatsBatchSize">
                  <option value="1">1</option>
                  <option value="5">5</option>
                  <option value="10">10</option>
                  <option value="20">20</option>
                  <option value="25" selected>25</option>
                  <option value="30">30</option>
                  <option value="35">35</option>
                  <option value="50">50</option>
                </select>
              </label>
              <label>
                <span>Результат</span>
                <select id="wbPromotionStatsResultMode">
                  <option value="campaign_totals" selected>Итого по кампаниям</option>
                  <option value="details">Полная детализация</option>
                </select>
              </label>
              <button type="button" data-run-wb-promotion-fullstats>Получить статистику</button>
              ${renderWbApiStopButton("promotion_fullstats")}
              ${renderApiExportLogButton("promotion_fullstats", state.wbApiLastLogFiles.promotion_fullstats)}
              <button type="button" class="ghost" data-open-wb-promotion-stats-result ${state.wbApiLastPromotionStatsResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbPromotionStatsProgress" class="wb-api-progress is-idle hidden" style="--progress:0%"></div>
          <div id="wbPromotionStatsResult" class="wb-api-result">Статистика обычной рекламы еще не запрашивалась.</div>
        </section>
      </div>
    </section>
  `;
}


function renderWbAssortmentExportsBlock(payload) {
  const wbApi = payload?.wb_api || {};
  const tokenStatus = wbApi.token_saved ? "Ключ сохранен" : "Ключ не сохранен";
  return `
    <section class="wb-api-panel wb-api-modern wb-content-panel">
      <header class="wb-api-header">
        <div>
          <h3>WB Content API</h3>
          <span>Read-only выгрузка категорий, карточек товаров и характеристик предметов</span>
        </div>
        <span id="wbApiTokenBadge" class="wb-api-badge ${wbApi.token_saved ? "is-saved" : ""}">${escapeHtml(tokenStatus)}</span>
      </header>

      <div class="wb-api-token-row">
        <label>
          <span>WB API ключ</span>
          <input id="wbApiTokenInput" type="password" autocomplete="off" placeholder="${wbApi.token_saved ? "Новый ключ, если нужно заменить" : "Вставьте ключ категории Content"}" />
        </label>
        <button type="button" data-save-wb-api-token>Сохранить ключ</button>
        <div id="wbApiTokenStatus" class="wb-api-token-status">
          ENV: ${escapeHtml(wbApi.token_env || "WB_API_TOKEN")} · ${escapeHtml(wbApi.env_file || "")}
        </div>
      </div>

      <div class="wb-api-sources">
        <section class="wb-api-source wb-api-source-content">
          <header class="wb-api-source-header">
            <div>
              <strong>WB Content API · товарный ассортимент</strong>
              <span>content-api.wildberries.ru · категории, карточки товаров, характеристики</span>
            </div>
            <span class="wb-api-source-tag is-promotion">Официальный API</span>
          </header>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Категории и предметы</strong>
              <span>GET /content/v2/object/parent/all + GET /content/v2/object/all · справочник категорий для отбора товаров</span>
              <code>${escapeHtml(wbApi.content_categories_output_dir || "")}</code>
            </div>
            <div class="wb-api-filter-grid wb-content-query-grid">
              <label>
                <span>Locale</span>
                <input id="wbContentCategoriesLocale" type="text" value="ru" />
              </label>
              <label>
                <span>Лимит страницы</span>
                <input id="wbContentCategoriesLimit" type="number" min="1" max="1000" value="1000" />
              </label>
              <button type="button" data-run-wb-content-categories>Получить категории</button>
              ${renderWbApiStopButton("content_categories")}
              ${renderApiExportLogButton("content_categories", state.wbApiLastLogFiles.content_categories)}
              <button type="button" class="ghost" data-open-wb-content-categories-result ${state.wbApiLastContentCategoriesResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbContentCategoriesProgress" class="wb-api-progress is-idle hidden" style="--progress:0%"></div>
          <div id="wbContentCategoriesResult" class="wb-api-result">Категории WB Content еще не запрашивались.</div>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Список товаров</strong>
              <span>POST /content/v2/get/cards/list · карточки товаров, SKU, штрихкоды, subjectID для характеристик</span>
              <code>${escapeHtml(wbApi.content_cards_output_dir || "")}</code>
            </div>
            <div class="wb-api-filter-grid wb-content-query-grid">
              <label>
                <span>Locale</span>
                <input id="wbContentCardsLocale" type="text" value="ru" />
              </label>
              <label>
                <span>Лимит страницы</span>
                <input id="wbContentCardsLimit" type="number" min="1" max="100" value="100" />
              </label>
              <label>
                <span>Макс. страниц</span>
                <input id="wbContentCardsMaxPages" type="number" min="0" value="0" />
              </label>
              <label>
                <span>subjectID</span>
                <textarea id="wbContentObjectIds" rows="2" placeholder="Пусто = все предметы; можно вставить ID через запятую"></textarea>
              </label>
              <label>
                <span>Поиск</span>
                <input id="wbContentTextSearch" type="text" placeholder="Опционально: часть названия или артикула" />
              </label>
              <label>
                <span>Фото</span>
                <select id="wbContentWithPhoto">
                  <option value="-1" selected>Все</option>
                  <option value="1">С фото</option>
                  <option value="0">Без фото</option>
                </select>
              </label>
              <label class="ozon-seo-check">
                <input id="wbContentAllowedCategoriesOnly" type="checkbox" checked />
                <span>Только разрешенные категории</span>
              </label>
              <button type="button" data-run-wb-content-cards>Получить товары</button>
              ${renderWbApiStopButton("content_cards")}
              ${renderApiExportLogButton("content_cards", state.wbApiLastLogFiles.content_cards)}
              <button type="button" class="ghost" data-open-wb-content-cards-result ${state.wbApiLastContentCardsResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbContentCardsProgress" class="wb-api-progress is-idle hidden" style="--progress:0%"></div>
          <div id="wbContentCardsResult" class="wb-api-result">Список товаров WB Content еще не запрашивался.</div>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Характеристики предметов</strong>
              <span>GET /content/v2/object/charcs/{subjectId} · отдельная выгрузка характеристик по subjectID</span>
              <code>${escapeHtml(wbApi.content_characteristics_output_dir || "")}</code>
            </div>
            <div class="wb-api-filter-grid wb-content-query-grid">
              <label>
                <span>Locale</span>
                <input id="wbContentCharacteristicsLocale" type="text" value="ru" />
              </label>
              <label>
                <span>subjectID</span>
                <textarea id="wbContentSubjectIds" rows="3" placeholder="Например: 11, 12, 13. После выгрузки товаров заполнится автоматически."></textarea>
              </label>
              <button type="button" data-run-wb-content-characteristics>Получить характеристики</button>
              ${renderWbApiStopButton("content_characteristics")}
              ${renderApiExportLogButton("content_characteristics", state.wbApiLastLogFiles.content_characteristics)}
              <button type="button" class="ghost" data-open-wb-content-characteristics-result ${state.wbApiLastContentCharacteristicsResultFile ? "" : "disabled"}>Файл результата</button>
            </div>
          </article>
          <div id="wbContentCharacteristicsProgress" class="wb-api-progress is-idle hidden" style="--progress:0%"></div>
          <div id="wbContentCharacteristicsResult" class="wb-api-result">Характеристики WB Content еще не запрашивались.</div>
        </section>
      </div>
    </section>
  `;
}

function renderApiExportTerminal() {
  return `
    <section class="api-export-terminal">
      <header>
        <div>
          <h3>Терминал выгрузки API</h3>
          <span>Лог запуска метода и сохранения результата</span>
        </div>
        ${adminIconButton("Очистить терминал API", "clear", "data-clear-api-terminal", "ghost")}
      </header>
      <div id="apiExportTerminalBody" class="api-export-terminal-body">
        ${state.apiTerminalLines.length
          ? state.apiTerminalLines.map((line) => `
            <div class="api-terminal-line is-${escapeHtml(line.type || "output")}">
              <span>${escapeHtml(line.time)}</span>
              <code>${escapeHtml(line.text)}</code>
            </div>
          `).join("")
          : '<div class="api-terminal-empty">Терминал готов. Запусти метод, и здесь появится ход выгрузки.</div>'}
      </div>
    </section>
  `;
}

function renderOzonSeoExportsBlock(payload) {
  const ozonSeo = payload?.ozon_seo || {};
  const credentialsSaved = ozonSeo.credentials_saved === true;
  const credentialStatus = credentialsSaved ? "Ключи сохранены" : "Ключи не сохранены";
  return `
    <section class="wb-api-panel wb-api-modern ozon-seo-panel">
      <header class="wb-api-header">
        <div>
          <h3>Ozon SEO API</h3>
          <span>Тестовая read-only выгрузка поисковых запросов и заказов по одному SKU</span>
        </div>
        <span id="ozonSeoTokenBadge" class="wb-api-badge ${credentialsSaved ? "is-saved" : ""}">${escapeHtml(credentialStatus)}</span>
      </header>

      <div class="wb-api-token-row ozon-seo-token-row">
        <label>
          <span>Client ID</span>
          <input id="ozonSeoClientIdInput" type="text" autocomplete="off" value="${escapeHtml(ozonSeo.client_id || "")}" placeholder="Ozon Seller Client-Id" />
        </label>
        <label>
          <span>API key</span>
          <input id="ozonSeoApiKeyInput" type="password" autocomplete="off" placeholder="${credentialsSaved ? "Ключ сохранен; введите новый для замены" : "Api-Key для Seller API"}" />
        </label>
        <button type="button" data-save-ozon-seo-credentials>Сохранить ключи</button>
        <div id="ozonSeoTokenStatus" class="wb-api-token-status">
          ${credentialsSaved
            ? `Сохранено на сервере: ${escapeHtml(ozonSeo.client_id_env || "OZON_SELLER_CLIENT_ID")} и ${escapeHtml(ozonSeo.api_key_env || "OZON_SELLER_API_KEY")}. API key не отображается.`
            : "Введите Client ID и API key, затем нажмите «Сохранить ключи»."}
        </div>
      </div>

      <div class="wb-api-sources">
        <section class="wb-api-source ozon-seo-source">
          <header class="wb-api-source-header">
            <div>
              <strong>Ozon Seller API · поисковые запросы товара</strong>
              <span>api-seller.ozon.ru · аналитика запросов, заказов и видимости по SKU</span>
            </div>
            <span class="wb-api-source-tag is-promotion">Официальный API</span>
          </header>

          <article class="wb-api-method-card wb-api-method-card-wide">
            <div class="wb-api-method-copy">
              <strong>Детализация поисковых фраз по SKU</strong>
              <span>POST /v1/analytics/product-queries/details · один SKU, период и лимит фраз по SKU</span>
              <code>Возвращает полный JSON Ozon; таблица ниже показывает найденные items, если они есть в ответе.</code>
            </div>
            <div class="wb-api-filter-grid ozon-seo-query-grid">
              <label>
                <span>SKU</span>
                <input id="ozonSeoSkuInput" type="text" inputmode="numeric" placeholder="Например 123456789" />
              </label>
              <label>
                <span>Дата с</span>
                <input id="ozonSeoDateFrom" type="date" />
              </label>
              <label>
                <span>Дата по</span>
                <input id="ozonSeoDateTo" type="date" />
              </label>
              <label>
                <span>Фраз на SKU</span>
                <input id="ozonSeoLimitBySku" type="number" min="1" max="15" value="15" />
              </label>
              <label>
                <span>Страница</span>
                <input id="ozonSeoPage" type="number" min="0" value="0" />
              </label>
              <label>
                <span>Размер страницы</span>
                <input id="ozonSeoPageSize" type="number" min="1" max="100" value="100" />
              </label>
              <label>
                <span>Сортировка</span>
                <select id="ozonSeoSortBy">
                  <option value="BY_SEARCHES">По показам/поискам</option>
                  <option value="BY_ORDERS">По заказам</option>
                  <option value="BY_GMV">По GMV</option>
                  <option value="BY_POSITION">По позиции</option>
                </select>
              </label>
              <label>
                <span>Направление</span>
                <select id="ozonSeoSortDir">
                  <option value="DESCENDING">По убыванию</option>
                  <option value="ASCENDING">По возрастанию</option>
                </select>
              </label>
              <label class="ozon-seo-check">
                <input id="ozonSeoShowAll" type="checkbox" checked />
                <span>Отобразить все данные ответа</span>
              </label>
              <button type="button" data-run-ozon-seo-details>Получить данные</button>
              ${renderApiExportLogButton("ozon_seo_details", state.ozonSeoLastLogFile || state.wbApiLastLogFiles.ozon_seo_details)}
            </div>
          </article>
          <div id="ozonSeoDetailsResult" class="wb-api-result">SEO-запрос Ozon еще не выполнялся.</div>
          <div id="ozonSeoDetailsTable" class="ozon-seo-preview"></div>
          <pre id="ozonSeoRawResult" class="ozon-seo-raw hidden">{}</pre>
        </section>
      </div>
    </section>
  `;
}

function wbAnalyticsDefaultPeriod() {
  const end = new Date();
  const start = new Date(end); start.setDate(start.getDate() - 6);
  const iso = (value) => value.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

function wbAnalyticsField(section, name) {
  return section?.querySelector(`[data-wb-field="${name}"]`);
}

function wbAnalyticsValue(section, name) {
  const field = wbAnalyticsField(section, name);
  if (!field) return "";
  return field.type === "checkbox" ? field.checked : field.value.trim();
}

function wbAnalyticsList(value, numeric = false) {
  const items = String(value || "").split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean);
  return numeric ? items.map((item) => Number(item)).filter((item) => Number.isInteger(item) && item >= 0) : items;
}

function wbAnalyticsPeriod(section, prefix, optional = false) {
  const start = wbAnalyticsValue(section, `${prefix}_start`);
  const end = wbAnalyticsValue(section, `${prefix}_end`);
  if (optional && !start && !end) return null;
  return { start, end };
}

function wbAnalyticsFilters(section, stocks = false) {
  const result = {};
  const nmIds = wbAnalyticsList(wbAnalyticsValue(section, "nm_ids"), true);
  const brandNames = wbAnalyticsList(wbAnalyticsValue(section, "brand_names"));
  const subjectIds = wbAnalyticsList(wbAnalyticsValue(section, "subject_ids"), true);
  const tagIds = wbAnalyticsList(wbAnalyticsValue(section, "tag_ids"), true);
  if (nmIds.length) result[stocks ? "nmIDs" : "nmIds"] = nmIds;
  if (brandNames.length) result.brandNames = brandNames;
  if (subjectIds.length) result[stocks ? "subjectIDs" : "subjectIds"] = subjectIds;
  if (tagIds.length) result[stocks ? "tagIDs" : "tagIds"] = tagIds;
  return result;
}

function wbAnalyticsOrder(section) {
  return { field: wbAnalyticsValue(section, "order_field"), mode: wbAnalyticsValue(section, "order_mode") };
}

function wbAnalyticsLimit(section) {
  return { limit: Number(wbAnalyticsValue(section, "limit") || 100), offset: Number(wbAnalyticsValue(section, "offset") || 0) };
}

function wbAnalyticsResult(section, message, payload = null, error = false) {
  const box = section?.querySelector("[data-wb-result]");
  if (!box) return;
  box.classList.toggle("is-error", error);
  box.innerHTML = `<strong>${escapeHtml(message)}</strong>${payload ? `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>` : ""}`;
}

const WB_ANALYTICS_FIELD_HELP = {
  "Выбранный период": "selectedPeriod.start/end — период, за который WB рассчитает метрики карточек. Доступны данные максимум за последние 365 дней.",
  "Текущий период": "currentPeriod.start/end — основной период расчёта отчёта. Все метрики и фильтры применяются к этим датам.",
  "Период": "Период расчёта метода. Для дневной воронки WB разрешает максимум последнюю неделю; история остатков использует ограничения выбранного разреза.",
  "Период отчёта": "params.startDate/endDate — период создаваемого CSV. Обычно до одного года, для отчётов об остатках — до трёх месяцев.",
  "Прошлый период": "pastPeriod.start/end — необязательный период сравнения. В отчёте карточек WB приводит его к длительности выбранного периода; в поиске число дней не должно превышать текущий период.",
  "Артикулы WB (nmID)": "nmIds/nmIDs — числовые ID карточек Wildberries. Пустое поле означает все карточки там, где метод это допускает; дневная история требует 1–20 nmID, поиск принимает до 50, сводка — до 1000.",
  "Бренды": "brandNames — точный список брендов для фильтрации. Пусто — без фильтра; вместе с предметами и ярлыками условия применяются одновременно.",
  "ID предметов": "subjectIds/subjectIDs — числовые ID предметов WB. Ограничивают состав карточек; в групповой истории произведение числа предметов, брендов и ярлыков не должно превышать 16.",
  "ID ярлыков": "tagIds/tagIDs — числовые ID пользовательских ярлыков карточек. Пусто — без фильтра; при нескольких фильтрах карточка должна соответствовать всем.",
  "Исключить удалённые карточки": "skipDeletedNm — если включено, удалённые карточки не попадут в результат.",
  "Сортировать по": "orderBy.field/topOrderBy — метрика, определяющая порядок строк. Она меняет порядок выдачи, но не пересчитывает сами показатели.",
  "Направление": "orderBy.mode — desc сортирует от большего к меньшему, asc — от меньшего к большему.",
  "Лимит (1–1000)": "limit — максимальное число элементов на одной странице ответа, не ограничение периода или общего объёма. Для следующей страницы увеличьте смещение.",
  "Смещение": "offset — сколько первых элементов пропустить при пагинации. Например, 100 при limit=100 запрашивает вторую страницу.",
  "Агрегация": "aggregationLevel — группировка временного ряда: day по дням или week по неделям. Меняет детализацию строк, а не выбранный период.",
  "Кластер позиции": "positionCluster — отбор товаров по средней позиции в поиске: все, 1–100, 101–200 или ниже 200.",
  "Включить подменные SKU": "includeSubstitutedSKUs — включает прямые поисковые запросы, где WB показал подменный артикул. Нельзя одновременно выключить это поле и поисковые фразы.",
  "Включить поисковые фразы": "includeSearchTexts — включает детализацию по текстам поисковых запросов. Нельзя одновременно выключить это поле и подменные SKU.",
  "Разрез отчёта": "Выбирает официальный метод остатков и гранулярность ответа: группы, товары, размеры или склады. Для размеров и складов требуется ровно один nmID.",
  "Размер (для складов)": "sizeName — размер товара для детализации остатков по складам. Используется только в разрезе «Склады».",
  "Тип остатков": "stockType — источник остатков: все, склады WB или склады продавца. Меняет состав складских остатков в ответе.",
  "Фильтр доступности": "availabilityFilters — категории состояния запасов WB: дефицитные, актуальные, сбалансированные, неактуальные, неликвидные или с некорректными данными.",
  "Тип отчёта": "reportType — схема создаваемого CSV: детальная/групповая воронка, поисковые запросы или история остатков. От типа зависят доступность Jam, период и колонки файла.",
  "Название отчёта": "userReportName — ваше имя задания в списке отчётов. На расчёт данных не влияет; если оставить пустым, WB сформирует название автоматически.",
  "Часовой пояс": "params.timezone — IANA-таймзона, в которой WB определяет границы дат отчёта. Для кабинета в РФ обычно Europe/Moscow.",
  "UUID отчётов": "filter[downloadIds] — необязательный список ID заданий. Пусто возвращает общий список, значения ограничивают ответ выбранными отчётами.",
  "UUID отчёта": "downloadId/id — ID задания генерации. По нему повторяют неудачный отчёт или скачивают готовый ZIP; готовый файл хранится 48 часов."
};

function attachWbAnalyticsFieldHelp(root) {
  root?.querySelectorAll(".wb-analytics-form label > span:first-child, .wb-analytics-period > legend, .wb-analytics-options > legend").forEach((node) => {
    const label = String(node.textContent || "").replace(/\s*(?:·\s*необязательно|\(необязательно\))\s*$/i, "").trim();
    const help = WB_ANALYTICS_FIELD_HELP[label];
    if (!help) return;
    node.classList.add("wb-analytics-help");
    node.dataset.wbHelp = help;
    node.tabIndex = 0;
    node.setAttribute("aria-label", `${label}. ${help}`);
  });
}

function wbAnalyticsCommonFields({ nm = true, groups = true } = {}) {
  return `${nm ? `<label><span>Артикулы WB (nmID)</span><textarea data-wb-field="nm_ids" rows="2" placeholder="123456, 789012"></textarea></label>` : ""}
    ${groups ? `<label><span>Бренды</span><textarea data-wb-field="brand_names" rows="2" placeholder="Через запятую"></textarea></label>
    <label><span>ID предметов</span><textarea data-wb-field="subject_ids" rows="2" placeholder="123, 456"></textarea></label>
    <label><span>ID ярлыков</span><textarea data-wb-field="tag_ids" rows="2" placeholder="10, 20"></textarea></label>` : ""}`;
}

const wbAnalyticsCalendarStates = new WeakMap();

function wbAnalyticsPeriodFields(prefix, title, dates, optional = false) {
  const start = optional ? "" : dates.start;
  const end = optional ? "" : dates.end;
  const label = start && end ? `${formatRuDate(start)} - ${formatRuDate(end)}` : "Выберите период";
  return `<fieldset class="wb-analytics-period" data-wb-range data-wb-range-prefix="${prefix}" data-wb-range-optional="${optional ? "1" : "0"}"><legend>${title}${optional ? " · необязательно" : ""}</legend>
    <input data-wb-field="${prefix}_start" type="hidden" value="${start}" />
    <input data-wb-field="${prefix}_end" type="hidden" value="${end}" />
    <button type="button" class="dropdown-toggle wb-analytics-range-toggle" data-wb-range-toggle>${label}</button>
    <div class="date-range-menu wb-analytics-date-menu hidden" data-wb-range-menu></div>
  </fieldset>`;
}

function wbAnalyticsRangeState(range) {
  if (!wbAnalyticsCalendarStates.has(range)) {
    const section = range.closest("[data-wb-method], [data-wb-csv], [data-seo-collect], [data-seo-dash]");
    const prefix = range.dataset.wbRangePrefix;
    const from = wbAnalyticsValue(section, `${prefix}_start`);
    const to = wbAnalyticsValue(section, `${prefix}_end`);
    const reference = parseIsoDate(from || to) || new Date();
    wbAnalyticsCalendarStates.set(range, { from, to, step: from && to ? 2 : (from ? 1 : 0), leftMonth: new Date(reference.getFullYear(), reference.getMonth(), 1) });
  }
  return wbAnalyticsCalendarStates.get(range);
}

function wbAnalyticsCalendarMonth(date, side, calendarState) {
  const year = date.getFullYear();
  const month = date.getMonth();
  const monthOptions = monthNames.map((name, index) => `<option value="${index}" ${index === month ? "selected" : ""}>${name}</option>`).join("");
  const yearOptions = Array.from({ length: 9 }, (_, index) => year - 4 + index).map((item) => `<option value="${item}" ${item === year ? "selected" : ""}>${item}</option>`).join("");
  const firstDay = (new Date(year, month, 1).getDay() + 6) % 7;
  const cells = Array.from({ length: firstDay }, () => '<span class="calendar-empty"></span>');
  for (let day = 1; day <= daysInMonth(year, month); day += 1) {
    const iso = toIsoDate(new Date(year, month, day));
    const inRange = calendarState.from && calendarState.to && iso >= calendarState.from && iso <= calendarState.to;
    const edge = iso === calendarState.from || iso === calendarState.to;
    cells.push(`<button class="calendar-day ${inRange ? "in-range" : ""} ${edge ? "range-edge" : ""}" type="button" data-wb-calendar-date="${iso}">${day}</button>`);
  }
  return `<div class="calendar-month-panel"><div class="calendar-head"><select data-wb-calendar-month data-side="${side}">${monthOptions}</select><select data-wb-calendar-year data-side="${side}">${yearOptions}</select></div><div class="calendar-weekdays"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Вс</span></div><div class="calendar-grid">${cells.join("")}</div></div>`;
}

function renderWbAnalyticsCalendar(range) {
  const calendarState = wbAnalyticsRangeState(range);
  const menu = range.querySelector("[data-wb-range-menu]");
  const optional = range.dataset.wbRangeOptional === "1";
  menu.innerHTML = `<div class="date-presets"><button type="button" data-wb-calendar-preset="today">Сегодня</button><button type="button" data-wb-calendar-preset="yesterday">Вчера</button><button type="button" data-wb-calendar-preset="week">Неделя</button><button type="button" data-wb-calendar-preset="month">Месяц</button><button type="button" data-wb-calendar-preset="prev_month">Прошлый месяц</button><button type="button" data-wb-calendar-preset="days28">28 дней</button><button type="button" data-wb-calendar-preset="days90">90 дней</button></div><div class="calendar-panels">${wbAnalyticsCalendarMonth(calendarState.leftMonth, "left", calendarState)}${wbAnalyticsCalendarMonth(addMonths(calendarState.leftMonth, 1), "right", calendarState)}</div><div class="date-range-actions"><span>${calendarState.from && calendarState.to ? `${formatRuDate(calendarState.from)} - ${formatRuDate(calendarState.to)}` : "Выберите дату начала и дату окончания"}</span><div>${optional ? '<button type="button" class="ghost" data-wb-calendar-clear>Очистить</button>' : ""}<button type="button" data-wb-calendar-apply ${calendarState.from && calendarState.to ? "" : "disabled"}>Применить</button></div></div>`;
}

function setWbAnalyticsCalendarPreset(range, preset) {
  const calendarState = wbAnalyticsRangeState(range);
  const today = new Date(); const todayIso = toIsoDate(today); const yesterday = shiftDate(todayIso, -1);
  if (preset === "today") [calendarState.from, calendarState.to] = [todayIso, todayIso];
  if (preset === "yesterday") [calendarState.from, calendarState.to] = [yesterday, yesterday];
  if (preset === "week") [calendarState.from, calendarState.to] = [shiftDate(todayIso, -6), todayIso];
  if (preset === "month") [calendarState.from, calendarState.to] = [toIsoDate(new Date(today.getFullYear(), today.getMonth(), 1)), todayIso];
  if (preset === "prev_month") [calendarState.from, calendarState.to] = [toIsoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1)), toIsoDate(new Date(today.getFullYear(), today.getMonth(), 0))];
  if (preset === "days28" || preset === "days30") [calendarState.from, calendarState.to] = [defaultRecentDateFrom(todayIso), todayIso];
  if (preset === "days90") [calendarState.from, calendarState.to] = [shiftDate(todayIso, -89), todayIso];
  calendarState.step = 2; calendarState.leftMonth = new Date(parseIsoDate(calendarState.from).getFullYear(), parseIsoDate(calendarState.from).getMonth(), 1); renderWbAnalyticsCalendar(range);
}

function handleWbAnalyticsCalendarClick(event) {
  const range = event.target.closest("[data-wb-range]");
  document.querySelectorAll("[data-wb-range-menu]").forEach((menu) => { if (!range || menu !== range.querySelector("[data-wb-range-menu]")) menu.classList.add("hidden"); });
  if (!range) return;
  const menu = range.querySelector("[data-wb-range-menu]");
  if (event.target.closest("[data-wb-range-toggle]")) { event.preventDefault(); renderWbAnalyticsCalendar(range); menu.classList.toggle("hidden"); return; }
  const preset = event.target.closest("[data-wb-calendar-preset]"); if (preset) { setWbAnalyticsCalendarPreset(range, preset.dataset.wbCalendarPreset); return; }
  const day = event.target.closest("[data-wb-calendar-date]"); if (day) { const state = wbAnalyticsRangeState(range); const iso = day.dataset.wbCalendarDate; if (state.step !== 1) { state.from = iso; state.to = ""; state.step = 1; } else { if (iso < state.from) { state.to = state.from; state.from = iso; } else state.to = iso; state.step = 2; } renderWbAnalyticsCalendar(range); return; }
  if (event.target.closest("[data-wb-calendar-clear]")) { const state = wbAnalyticsRangeState(range); state.from = ""; state.to = ""; state.step = 0; const section = range.closest("[data-wb-method], [data-wb-csv], [data-seo-collect], [data-seo-dash]"); const prefix = range.dataset.wbRangePrefix; wbAnalyticsField(section, `${prefix}_start`).value = ""; wbAnalyticsField(section, `${prefix}_end`).value = ""; range.querySelector("[data-wb-range-toggle]").textContent = "Выберите период"; menu.classList.add("hidden"); return; }
  if (event.target.closest("[data-wb-calendar-apply]")) { const state = wbAnalyticsRangeState(range); if (!state.from || !state.to) return; const section = range.closest("[data-wb-method], [data-wb-csv], [data-seo-collect], [data-seo-dash]"); const prefix = range.dataset.wbRangePrefix; wbAnalyticsField(section, `${prefix}_start`).value = state.from; wbAnalyticsField(section, `${prefix}_end`).value = state.to; range.querySelector("[data-wb-range-toggle]").textContent = `${formatRuDate(state.from)} - ${formatRuDate(state.to)}`; menu.classList.add("hidden"); }
}

function handleWbAnalyticsCalendarChange(event) {
  const select = event.target.closest("[data-wb-calendar-month], [data-wb-calendar-year]"); if (!select) return;
  const range = select.closest("[data-wb-range]"); const state = wbAnalyticsRangeState(range); const side = select.dataset.side; const panelDate = side === "right" ? addMonths(state.leftMonth, 1) : state.leftMonth; const panel = select.closest(".calendar-month-panel"); const month = Number(panel.querySelector("[data-wb-calendar-month]").value); const year = Number(panel.querySelector("[data-wb-calendar-year]").value); const picked = new Date(year, month, 1); state.leftMonth = side === "right" ? addMonths(picked, -1) : picked; renderWbAnalyticsCalendar(range);
}

document.addEventListener("click", handleWbAnalyticsCalendarClick);
document.addEventListener("change", handleWbAnalyticsCalendarChange);
function wbAnalyticsMethodSection({ key, title, path, note, fields, action = "Получить отчёт" }) {
  return `<section class="wb-analytics-method" data-wb-method="${key}">
    <header><div><h4>${title}</h4><code>POST ${path}</code></div><span class="wb-api-source-tag">Analytics</span></header>
    <p class="wb-analytics-note">${note}</p>
    <div class="wb-analytics-form">${fields}</div>
    <div class="wb-analytics-actions"><button type="button" class="ghost" data-download-wb-analytics-excel disabled>Скачать Excel</button><button type="button" data-run-wb-analytics-method>${action}</button></div>
    <div class="wb-api-result wb-analytics-result" data-wb-result>Запрос ещё не выполнялся.</div>
  </section>`;
}

function renderWbAnalyticsReportsBlock() {
  const dates = wbAnalyticsDefaultPeriod();
  const orderModes = `<label><span>Направление</span><select data-wb-field="order_mode"><option value="desc">По убыванию</option><option value="asc">По возрастанию</option></select></label>`;
  const paging = `<label><span>Лимит (1–1000)</span><input data-wb-field="limit" type="number" min="1" max="1000" value="100" /></label><label><span>Смещение</span><input data-wb-field="offset" type="number" min="0" value="0" /></label>`;
  const skipDeleted = `<label class="wb-analytics-check"><input data-wb-field="skip_deleted" type="checkbox" checked /><span>Исключить удалённые карточки</span></label>`;
  const aggregation = `<label><span>Агрегация</span><select data-wb-field="aggregation"><option value="day">По дням</option><option value="week">По неделям</option></select></label>`;
  const availability = `<fieldset class="wb-analytics-options"><legend>Фильтр доступности</legend>${[["deficient","Дефицит"],["actual","Актуальные"],["balanced","Сбалансированные"],["nonActual","Неактуальные"],["nonLiquid","Неликвид"],["invalidData","Некорректные данные"]].map(([value,label]) => `<label><input type="checkbox" data-wb-availability="${value}" /><span>${label}</span></label>`).join("")}</fieldset>`;

  const methods = [
    wbAnalyticsMethodSection({ key: "products", title: "1. Статистика карточек товаров за период", path: "/api/analytics/v3/sales-funnel/products", note: "Фильтры до 1000 карточек, пагинация и сравнение с прошлым периодом одинаковой длительности. Лимит WB: 3 запроса в минуту.", fields: `${wbAnalyticsPeriodFields("selected", "Выбранный период", dates)}${wbAnalyticsPeriodFields("past", "Прошлый период", dates, true)}${wbAnalyticsCommonFields()}${skipDeleted}<label><span>Сортировать по</span><select data-wb-field="order_field"><option value="openCard">Открытия карточки</option><option value="addToCart">Добавления в корзину</option><option value="orders">Заказы</option><option value="ordersSumRub">Сумма заказов</option><option value="buyouts">Выкупы</option><option value="buyoutPercent">Процент выкупа</option><option value="stockWbQty">Остаток WB</option><option value="stockMpQty">Остаток продавца</option></select></label>${orderModes}${paging}` }),
    wbAnalyticsMethodSection({ key: "products_history", title: "2. Статистика карточек товаров по дням", path: "/api/analytics/v3/sales-funnel/products/history", note: "Обязательны от 1 до 20 артикулов WB. Доступна агрегация по дням или неделям.", fields: `${wbAnalyticsPeriodFields("selected", "Период", dates)}${wbAnalyticsCommonFields({ groups: false })}${aggregation}${skipDeleted}` }),
    wbAnalyticsMethodSection({ key: "grouped_history", title: "3. Статистика групп карточек по дням", path: "/api/analytics/v3/sales-funnel/grouped/history", note: "Группировка по брендам, предметам и ярлыкам. Произведение количества выбранных значений не должно превышать 16.", fields: `${wbAnalyticsPeriodFields("selected", "Период", dates)}${wbAnalyticsCommonFields({ nm: false })}${aggregation}${skipDeleted}` }),
    wbAnalyticsMethodSection({ key: "search_report", title: "4. Поисковые запросы по вашим товарам", path: "/api/v2/search-report/report", note: "Текущий и необязательный прошлый период, кластер позиции, состав результата, сортировка и пагинация.", fields: `${wbAnalyticsPeriodFields("current", "Текущий период", dates)}${wbAnalyticsPeriodFields("past", "Прошлый период", dates, true)}${wbAnalyticsCommonFields()}<label><span>Кластер позиции</span><select data-wb-field="position_cluster"><option value="all">Все позиции</option><option value="firstHundred">1–100</option><option value="secondHundred">101–200</option><option value="below">Ниже 200</option></select></label><label><span>Сортировать по</span><select data-wb-field="order_field"><option value="avgPosition">Средняя позиция</option><option value="openCard">Переходы в карточку</option><option value="addToCart">Добавления в корзину</option><option value="orders">Заказы</option><option value="cartToOrder">Конверсия корзина → заказ</option></select></label>${orderModes}${paging}<label class="wb-analytics-check"><input data-wb-field="include_substituted" type="checkbox" checked /><span>Включить подменные SKU</span></label><label class="wb-analytics-check"><input data-wb-field="include_texts" type="checkbox" checked /><span>Включить поисковые фразы</span></label>` }),
    wbAnalyticsMethodSection({ key: "stocks", title: "5. История остатков", path: "/api/v2/stocks-report/products/{groups|products|sizes|offices}", note: "Выберите допустимый разрез метода: группы, товары, размеры или склады. Для размеров и складов нужен ровно один nmID.", fields: `${wbAnalyticsPeriodFields("current", "Период", dates)}<label><span>Разрез отчёта</span><select data-wb-field="stock_view"><option value="stocks_products">Товары</option><option value="stocks_groups">Группы</option><option value="stocks_sizes">Размеры</option><option value="stocks_offices">Склады</option></select></label>${wbAnalyticsCommonFields()}<label><span>Размер (для складов)</span><input data-wb-field="size_name" placeholder="Например 42" /></label><label><span>Тип остатков</span><select data-wb-field="stock_type"><option value="">Все</option><option value="wb">Склады WB</option><option value="mp">Склады продавца</option></select></label><label><span>Сортировать по</span><select data-wb-field="order_field"><option value="avgOrders">Средние заказы</option><option value="stockCount">Остаток</option><option value="stockTurnover">Оборачиваемость</option><option value="ordersCount">Заказы</option></select></label>${orderModes}${paging}${skipDeleted}${availability}` }),
  ].join("");

  const csv = `<section class="wb-analytics-method wb-analytics-csv" data-wb-csv>
    <header><div><h4>6. Аналитика продавца CSV · полный цикл</h4><code>POST /api/v2/nm-report/downloads · GET list · POST retry · GET file</code></div><span class="wb-api-source-tag">CSV / ZIP</span></header>
    <p class="wb-analytics-note">WB хранит сформированный файл 48 часов. Создание — отдельная операция; после неё обновите список, дождитесь статуса готовности и скачайте ZIP.</p>
    <div class="wb-analytics-csv-stage"><h5>6.1 Заказать отчёт</h5><div class="wb-analytics-form">
      <label><span>Тип отчёта</span><select data-wb-field="report_type"><option value="DETAIL_HISTORY_REPORT">Детальная история карточек</option><option value="GROUPED_HISTORY_REPORT">История групп карточек</option><option value="SEARCH_QUERIES_PREMIUM_REPORT_GROUP">Поисковые запросы · группы</option><option value="SEARCH_QUERIES_PREMIUM_REPORT_TEXT">Поисковые запросы · фразы</option><option value="STOCK_HISTORY_REPORT_CSV">История остатков</option><option value="STOCK_HISTORY_DAILY_CSV">Остатки по дням</option></select></label>
      <label><span>Название отчёта</span><input data-wb-field="user_report_name" maxlength="255" placeholder="Например WB июль" /></label>${wbAnalyticsPeriodFields("selected", "Период отчёта", dates)}${wbAnalyticsCommonFields()}${aggregation}${skipDeleted}<label><span>Часовой пояс</span><input data-wb-field="timezone" value="Europe/Moscow" /></label>
    </div><div class="wb-analytics-actions"><button type="button" data-wb-csv-action="create">Заказать CSV-отчёт</button></div></div>
    <div class="wb-analytics-csv-stage"><h5>6.2 Список и статусы отчётов</h5><div class="wb-analytics-form"><label class="wb-analytics-span"><span>UUID отчётов (необязательно)</span><textarea data-wb-field="download_ids" rows="2" placeholder="Пусто — запросить список; либо UUID через запятую"></textarea></label></div><div class="wb-analytics-actions"><button type="button" data-wb-csv-action="list">Обновить список и статусы</button></div><div data-wb-csv-table class="wb-analytics-csv-table"></div></div>
    <div class="wb-analytics-csv-stage"><h5>6.3 Повторить или скачать</h5><div class="wb-analytics-form"><label class="wb-analytics-span"><span>UUID отчёта</span><input data-wb-field="download_id" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></label></div><div class="wb-analytics-actions"><button type="button" data-wb-csv-action="retry" class="ghost">Повторить формирование</button><button type="button" data-wb-csv-action="download">Скачать ZIP</button></div></div>
    <div class="wb-api-result wb-analytics-result" data-wb-result>CSV-операции ещё не выполнялись.</div>
  </section>`;

  return `<section class="wb-api-panel wb-api-modern wb-analytics-workbench"><header class="wb-api-header"><div><h3>WB Analytics API · рабочее подключение отчётов</h3><span>Каждый метод имеет собственные допустимые параметры. Нужен token с категорией «Аналитика».</span></div><a class="wb-analytics-doc-link" href="https://dev.wildberries.ru/docs/openapi/analytics" target="_blank" rel="noreferrer">Документация WB ↗</a></header><div class="wb-analytics-stack">${methods}${csv}</div></section>`;
}

function buildWbAnalyticsDirectBody(section, method) {
  const skipDeletedNm = Boolean(wbAnalyticsValue(section, "skip_deleted"));
  if (method === "products") {
    const body = { selectedPeriod: wbAnalyticsPeriod(section, "selected"), ...wbAnalyticsFilters(section), skipDeletedNm, orderBy: wbAnalyticsOrder(section), ...wbAnalyticsLimit(section) };
    const past = wbAnalyticsPeriod(section, "past", true); if (past) body.pastPeriod = past; return body;
  }
  if (method === "products_history") return { selectedPeriod: wbAnalyticsPeriod(section, "selected"), nmIds: wbAnalyticsList(wbAnalyticsValue(section, "nm_ids"), true), skipDeletedNm, aggregationLevel: wbAnalyticsValue(section, "aggregation") };
  if (method === "grouped_history") return { selectedPeriod: wbAnalyticsPeriod(section, "selected"), ...wbAnalyticsFilters(section), skipDeletedNm, aggregationLevel: wbAnalyticsValue(section, "aggregation") };
  if (method === "search_report") {
    const body = { currentPeriod: wbAnalyticsPeriod(section, "current"), ...wbAnalyticsFilters(section), positionCluster: wbAnalyticsValue(section, "position_cluster"), orderBy: wbAnalyticsOrder(section), includeSubstitutedSKUs: Boolean(wbAnalyticsValue(section, "include_substituted")), includeSearchTexts: Boolean(wbAnalyticsValue(section, "include_texts")), ...wbAnalyticsLimit(section) };
    const past = wbAnalyticsPeriod(section, "past", true); if (past) body.pastPeriod = past; return body;
  }
  const view = wbAnalyticsValue(section, "stock_view");
  const filters = wbAnalyticsFilters(section, true);
  const body = { currentPeriod: wbAnalyticsPeriod(section, "current"), stockType: wbAnalyticsValue(section, "stock_type"), skipDeletedNm, orderBy: wbAnalyticsOrder(section), availabilityFilters: [...section.querySelectorAll("[data-wb-availability]:checked")].map((item) => item.dataset.wbAvailability), ...wbAnalyticsLimit(section) };
  if (view === "stocks_groups") { if (filters.subjectIDs) body.subjectIDs = filters.subjectIDs; if (filters.brandNames) body.brandNames = filters.brandNames; if (filters.tagIDs) body.tagIDs = filters.tagIDs; }
  else if (view === "stocks_products") { Object.assign(body, filters); if (body.subjectIDs) { body.subjectID = body.subjectIDs[0]; delete body.subjectIDs; } if (body.tagIDs) { body.tagID = body.tagIDs[0]; delete body.tagIDs; } if (body.brandNames) { body.brandName = body.brandNames[0]; delete body.brandNames; } }
  else { body.nmID = filters.nmIDs?.[0]; if (view === "stocks_offices" && wbAnalyticsValue(section, "size_name")) body.sizeName = wbAnalyticsValue(section, "size_name"); }
  return { method: view, body };
}

async function runWbAnalyticsReportFromUi(button) {
  const section = button.closest("[data-wb-method]");
  let method = section.dataset.wbMethod;
  let body = buildWbAnalyticsDirectBody(section, method);
  if (method === "stocks") { method = body.method; body = body.body; }
  const oldText = button.textContent; button.disabled = true; button.textContent = "Запрашиваю…";
  try {
    const result = await postJson("/api/admin/wb-api/analytics-report", { report: method, body });
    section.dataset.wbXlsxFile = result.xlsx || "";
    const excelButton = section.querySelector("[data-download-wb-analytics-excel]");
    if (excelButton) excelButton.disabled = !result.xlsx;
    wbAnalyticsResult(section, `Готово. JSON: ${result.file}. Excel: ${result.xlsx}`, { request: result.request, response: result.response });
    qs("status").textContent = "WB Analytics: отчёт получен, Excel подготовлен";
  } catch (error) { wbAnalyticsResult(section, `Ошибка: ${error.message}`, null, true); setStatusError(error); }
  finally { button.disabled = false; button.textContent = oldText; }
}

async function downloadWbAnalyticsExcel(button) {
  const section = button.closest("[data-wb-method]");
  const file = section?.dataset.wbXlsxFile || "";
  if (!file) throw new Error("Сначала получите отчёт");
  const oldText = button.textContent; button.disabled = true; button.textContent = "Скачиваю…";
  try {
    const response = await fetch("/api/admin/wb-api/analytics-excel/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file }) });
    if (!response.ok) { let message = `HTTP ${response.status}`; try { const data = await response.json(); message = data.error || data.message || message; } catch (_) {} throw new Error(message); }
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = file.split(/[\\/]/).pop() || "wb_analytics.xlsx"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    qs("status").textContent = "WB Analytics: Excel скачан";
  } finally { button.disabled = false; button.textContent = oldText; }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-download-wb-analytics-excel]");
  if (!button) return;
  try { await downloadWbAnalyticsExcel(button); }
  catch (error) { wbAnalyticsResult(button.closest("[data-wb-method]"), `Ошибка Excel: ${error.message}`, null, true); }
});
function wbAnalyticsCsvRows(response) {
  if (Array.isArray(response)) return response;
  for (const key of ["data", "downloads", "reports", "items"]) if (Array.isArray(response?.[key])) return response[key];
  return [];
}

function renderWbAnalyticsCsvTable(section, response) {
  const target = section.querySelector("[data-wb-csv-table]");
  const rows = wbAnalyticsCsvRows(response);
  if (!rows.length) { target.innerHTML = `<div class="wb-api-result">Список пуст или WB вернул объект без массива отчётов.</div>`; return; }
  target.innerHTML = `<table><thead><tr><th>UUID</th><th>Тип</th><th>Статус</th><th>Создан</th><th>Действия</th></tr></thead><tbody>${rows.map((row) => { const id = row.id || row.downloadId || row.downloadID || ""; return `<tr><td><code>${escapeHtml(id)}</code></td><td>${escapeHtml(row.reportType || row.type || "—")}</td><td>${escapeHtml(row.status || row.state || "—")}</td><td>${escapeHtml(row.createdAt || row.created || "—")}</td><td><button type="button" data-wb-csv-row="retry" data-download-id="${escapeHtml(id)}">Повторить</button><button type="button" data-wb-csv-row="download" data-download-id="${escapeHtml(id)}">ZIP</button></td></tr>`; }).join("")}</tbody></table>`;
}

async function downloadWbAnalyticsCsv(downloadId) {
  const response = await fetch("/api/admin/wb-api/analytics-csv/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ download_id: downloadId }) });
  if (!response.ok) { let message = `HTTP ${response.status}`; try { const data = await response.json(); message = data.error || data.message || message; } catch (_) {} throw new Error(message); }
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `wb_analytics_${downloadId}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function runWbAnalyticsCsvAction(button) {
  const section = button.closest("[data-wb-csv]");
  const action = button.dataset.wbCsvAction || button.dataset.wbCsvRow;
  const rowId = button.dataset.downloadId || "";
  const oldText = button.textContent; button.disabled = true; button.textContent = "Выполняю…";
  try {
    if (action === "create") {
      const params = { startDate: wbAnalyticsValue(section, "selected_start"), endDate: wbAnalyticsValue(section, "selected_end"), timezone: wbAnalyticsValue(section, "timezone"), aggregationLevel: wbAnalyticsValue(section, "aggregation"), skipDeletedNm: Boolean(wbAnalyticsValue(section, "skip_deleted")) };
      const filters = wbAnalyticsFilters(section, true); Object.assign(params, filters);
      const body = { reportType: wbAnalyticsValue(section, "report_type"), userReportName: wbAnalyticsValue(section, "user_report_name"), params };
      const result = await postJson("/api/admin/wb-api/analytics-csv/create", { body });
      wbAnalyticsField(section, "download_id").value = result.download_id; wbAnalyticsField(section, "download_ids").value = result.download_id;
      wbAnalyticsResult(section, `Отчёт заказан. UUID: ${result.download_id}`, { request: result.request, response: result.response });
    } else if (action === "list") {
      const result = await postJson("/api/admin/wb-api/analytics-csv/list", { download_ids: wbAnalyticsValue(section, "download_ids") }); renderWbAnalyticsCsvTable(section, result.response); wbAnalyticsResult(section, `Статусы обновлены. JSON: ${result.file}`, result.response);
    } else if (action === "retry") {
      const id = rowId || wbAnalyticsValue(section, "download_id"); const result = await postJson("/api/admin/wb-api/analytics-csv/retry", { download_id: id }); wbAnalyticsResult(section, `Повторное формирование запрошено: ${id}`, result.response);
    } else if (action === "download") {
      const id = rowId || wbAnalyticsValue(section, "download_id"); await downloadWbAnalyticsCsv(id); wbAnalyticsResult(section, `ZIP скачан: ${id}`);
    }
    qs("status").textContent = "WB Analytics CSV: операция выполнена";
  } catch (error) { wbAnalyticsResult(section, `Ошибка: ${error.message}`, null, true); setStatusError(error); }
  finally { button.disabled = false; button.textContent = oldText; }
}
function renderApiExportsDashboard(payload) {
  const marketplace = state.apiExportMarketplace === "ozon" ? "ozon" : "wb";
  const allowedModes = marketplace === "ozon" ? new Set(["seo"]) : new Set(["advertising", "assortment", "reports"]);
  const fallbackMode = marketplace === "ozon" ? "seo" : "advertising";
  const mode = allowedModes.has(state.apiExportMode) ? state.apiExportMode : fallbackMode;
  const modeMeta = {
    advertising: { title: "Выгрузка рекламы", description: "Read-only методы Wildberries API по обычной рекламе" },
    assortment: { title: "Выгрузка товарного ассортимента", description: "WB Content API: категории, товары и характеристики" },
    reports: { title: "Выгрузка отчётов", description: "WB Analytics API: воронка, поиск, остатки и CSV" },
    seo: { title: "Выгрузка SEO", description: "Ozon Seller API по поисковым фразам и заказам SKU" },
  };
  const lastLogs = payload?.wb_api?.last_logs || {};
  state.wbApiLastLogFiles = { ...state.wbApiLastLogFiles, ...lastLogs };
  if (lastLogs.ozon_seo_details) state.ozonSeoLastLogFile = lastLogs.ozon_seo_details;
  const body = mode === "seo" ? renderOzonSeoExportsBlock(payload) : mode === "assortment" ? renderWbAssortmentExportsBlock(payload) : mode === "reports" ? renderWbAnalyticsReportsBlock() : renderWbApiExportsBlock(payload);
  const marketplaceTabs = `
    <button type="button" class="admin-tab ${marketplace === "wb" ? "active" : ""}" data-api-export-marketplace="wb">Wildberries</button>
    <button type="button" class="admin-tab ${marketplace === "ozon" ? "active" : ""}" data-api-export-marketplace="ozon">Ozon</button>
  `;
  const sectionTabs = marketplace === "wb" ? `
    <button type="button" class="admin-tab ${mode === "advertising" ? "active" : ""}" data-api-export-mode="advertising">Выгрузка рекламы</button>
    <button type="button" class="admin-tab ${mode === "assortment" ? "active" : ""}" data-api-export-mode="assortment">Выгрузка товарного ассортимента</button>
    <button type="button" class="admin-tab ${mode === "reports" ? "active" : ""}" data-api-export-mode="reports">Выгрузка отчётов</button>
  ` : `<button type="button" class="admin-tab active" data-api-export-mode="seo">Выгрузка SEO</button>`;
  return `
    <div class="api-export-grid">
      <div class="api-export-marketplaces">${marketplaceTabs}</div>
      <div class="api-export-subtabs">${sectionTabs}</div>
      <section class="api-export-section">
        <header>
          <div><h3>${modeMeta[mode].title}</h3><span>${modeMeta[mode].description}</span></div>
          <span class="api-export-badge is-active">${marketplace === "wb" ? "Wildberries" : "Ozon"}</span>
        </header>
        ${body}
      </section>
      ${renderApiExportTerminal()}
    </div>
  `;
}

function clientCredentialSummary(client) {
  const credentials = client?.credentials || {};
  const labels = {
    wb_api_token: "WB token",
    wb_service_api_token: "WB Service token",
    ozon_client_id: "Ozon Client ID",
    ozon_api_key: "Ozon API key",
    ozon_performance_client_id: "Performance ID",
    ozon_performance_client_secret: "Performance secret",
    avito_ads_account_id: "Avito Account ID",
    avito_ads_client_id: "Avito Client ID",
    avito_ads_client_secret: "Avito Client Secret",
    lamoda_client_id: "Lamoda Client ID",
    lamoda_client_secret: "Lamoda Client Secret",
    yandex_market_api_key: "Яндекс Маркет API-Key",
  };
  const saved = Object.entries(credentials)
    .filter(([key, meta]) => meta?.saved && key !== "yandex_market_business_id" && key !== "yandex_market_campaign_id")
    .map(([key, meta]) => `${labels[key] || key} · ${meta.fingerprint || "сохранён"}`);
  return saved.length ? saved.join(" · ") : "Ключи ещё не сохранены";
}

function renderAdminClientCredentialField(client, credentialKey, label, emptyPlaceholder) {
  const meta = client?.credentials?.[credentialKey] || {};
  const status = meta.saved
    ? `Сохранён · ${meta.fingerprint || "защищён"}`
    : "Не сохранён";
  const placeholder = meta.saved ? "Новое значение, если нужно заменить" : emptyPlaceholder;
  return `
    <label class="admin-client-credential-field">
      <span>${escapeHtml(label)}</span>
      <span class="admin-client-secret-field">
        <input
          type="password"
          data-admin-client-credential="${escapeHtml(credentialKey)}"
          autocomplete="new-password"
          autocapitalize="none"
          spellcheck="false"
          placeholder="${escapeHtml(placeholder)}"
        />
        <button type="button" class="admin-client-secret-toggle" data-toggle-admin-client-secret title="Показать значение" aria-label="Показать значение" aria-pressed="false">${adminActionIcon("eye")}</button>
      </span>
      <small class="admin-client-credential-status ${meta.saved ? "is-saved" : ""}">${escapeHtml(status)}</small>
    </label>
  `;
}

function renderYandexMarketAccounts(client) {
  const accounts = client?.marketplace_accounts?.yandex_market || [];
  if (!accounts.length) {
    return '<p class="admin-yandex-empty">Магазины ещё не найдены. Проверьте API-ключ.</p>';
  }
  return `
    <div class="admin-yandex-accounts">
      ${accounts.map((account) => `
        <div class="admin-yandex-account">
          <header><strong>${escapeHtml(account.name || "Бизнес Яндекс Маркета")}</strong><code>Business ID ${escapeHtml(account.business_id || "—")}</code></header>
          <table>
            <thead><tr><th class="is-check">Импорт</th><th>Магазин</th><th>ID магазина</th><th>Статус</th></tr></thead>
            <tbody>
              ${(account.stores || []).map((store) => `<tr class="${store.is_accessible === false ? "is-unavailable" : ""}">
                <td class="is-check"><input type="checkbox" data-yandex-market-store value="${escapeHtml(store.campaign_id || "")}" ${store.import_enabled !== false && store.is_accessible !== false ? "checked" : ""} ${store.is_accessible === false ? "disabled" : ""} aria-label="Загружать магазин ${escapeHtml(store.name || store.campaign_id || "")}" /></td>
                <td><strong>${escapeHtml(store.name || store.domain || "Магазин")}</strong>${store.domain && store.domain !== store.name ? `<small>${escapeHtml(store.domain)}</small>` : ""}</td>
                <td><code>${escapeHtml(store.campaign_id || "—")}</code></td>
                <td>${store.is_accessible === false ? "Нет доступа" : "Доступен"}</td>
              </tr>`).join("") || '<tr><td colspan="4">Магазины не найдены</td></tr>'}
            </tbody>
          </table>
        </div>
      `).join("")}
    </div>
  `;
}

function renderAdminClientAssortmentUpdate(client) {
  const connected = new Set(client.marketplaces || []);
  const operation = client.operation === "assortment";
  const status = operation ? client.operation_status : "idle";
  const running = status === "running";
  const logs = operation && Array.isArray(client.operation_logs) ? client.operation_logs : [];
  const steps = operation && Array.isArray(client.operation_steps) && client.operation_steps.length
    ? client.operation_steps
    : [
        ...(connected.has("ozon") ? [{key: "ozon", label: "Ozon · ассортимент и характеристики", status: "pending"}] : []),
        ...(connected.has("wb") ? [{key: "wb", label: "WB · ассортимент и характеристики", status: "pending"}] : []),
      ];
  const statusLabels = {
    idle: "Готово к запуску",
    running: client.operation_stop_requested ? "Останавливается" : "Выполняется",
    completed: "Завершено",
    partial: "Завершено с ограничениями",
    failed: "Ошибка",
    stopped: "Остановлено",
  };
  const stepLabels = {pending: "Ожидает", running: "В работе", done: "Готово", failed: "Ошибка", stopped: "Остановлено"};
  const progress = operation ? Math.max(0, Math.min(100, Number(client.operation_progress || 0))) : 0;
  const terminalId = `clientAssortmentTerminal-${client.key}`;
  return `
    <fieldset class="admin-client-assortment-panel" data-admin-client-assortment>
      <legend>Обновление ассортимента через API</legend>
      <header>
        <div>
          <strong>Мягкий soft-update</strong>
          <span>Seller API только читается. В БД добавляются новые SKU и заполняются пустые поля/характеристики; строки не удаляются.</span>
        </div>
        <div class="admin-client-assortment-actions">
          <span class="admin-client-assortment-status is-${escapeHtml(status || "idle")}">${escapeHtml(statusLabels[status] || status)}</span>
          <button type="button" class="client-history-run-button primary" data-start-client-assortment ${running ? "disabled" : ""} title="Мягко обновить ассортимент" aria-label="Мягко обновить ассортимент">${adminActionIcon("api")}<span>Обновить</span></button>
          ${running ? `<button type="button" class="icon-button ghost danger" data-stop-client-assortment title="Остановить обновление ассортимента" aria-label="Остановить обновление ассортимента" ${client.operation_stop_requested ? "disabled" : ""}>${adminActionIcon("stop")}</button>` : ""}
        </div>
      </header>
      <div class="admin-client-assortment-marketplaces" role="group" aria-label="Маркетплейсы для обновления ассортимента">
        ${connected.has("ozon") ? `<label><input type="checkbox" name="adminAssortmentMarketplace" value="ozon" checked ${running ? "disabled" : ""}/><span>Ozon</span><small>Seller API · read-only</small></label>` : ""}
        ${connected.has("wb") ? `<label><input type="checkbox" name="adminAssortmentMarketplace" value="wb" checked ${running ? "disabled" : ""}/><span>WB</span><small>Content API · инкремент</small></label>` : ""}
      </div>
      ${operation ? `
        <div class="admin-client-assortment-progress-head"><span>${escapeHtml(client.operation_message || statusLabels[status] || "")}</span><b>${escapeHtml(String(progress))}%</b></div>
        <div class="admin-client-assortment-progress" role="progressbar" aria-label="Прогресс обновления ассортимента" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${escapeHtml(String(progress))}"><i style="width:${progress}%"></i></div>
        <div class="admin-client-assortment-steps">
          ${steps.map((step) => `<span class="is-${escapeHtml(step.status || "pending")}"><i aria-hidden="true"></i><b>${escapeHtml(step.label || step.key || "Этап")}</b><em>${escapeHtml(stepLabels[step.status] || "Ожидает")}</em></span>`).join("")}
        </div>
        <div id="${escapeHtml(terminalId)}" class="admin-client-assortment-terminal" role="log" aria-label="Терминал обновления ассортимента">
          ${logs.length ? logs.map((line) => `<div class="is-${escapeHtml(line.type || "output")}"><time>${escapeHtml(String(line.time || "").slice(11, 19) || "--:--:--")}</time><code>${escapeHtml(line.text || "")}</code></div>`).join("") : `<p>Подробный вывод появится после запуска.</p>`}
        </div>
        ${status === "failed" ? `<div class="client-onboarding-error">${escapeHtml(client.operation_error || "Soft-update завершился с ошибкой")}</div>` : ""}
      ` : `<p class="admin-client-assortment-note">Перед запуском сохраните ключи клиента. Для WB повторный полный проход не выполняется, если в БД уже есть контрольная точка.</p>`}
    </fieldset>
  `;
}

function renderAdminClientRegistryDetails(client, registry) {
  const reportSet = new Set(client.reports || []);
  const marketplaceSet = new Set(client.marketplaces || []);
  return `
    <form class="admin-client-registry-details" data-admin-client-form data-client-key="${escapeHtml(client.key)}" autocomplete="off">
      <div class="admin-client-detail-grid">
        <label><span>Статус</span><select name="status"><option value="active" ${client.status === "active" ? "selected" : ""}>Подключен</option><option value="paused" ${client.status !== "active" ? "selected" : ""}>Отключен</option></select></label>
        <label><span>Название</span><input name="label" value="${escapeHtml(client.label || "")}" maxlength="120" required /></label>
        <label><span>Системный ключ</span><input name="key" value="${escapeHtml(client.key)}" readonly aria-readonly="true" /></label>
        <label><span>База</span><input name="db_name" value="${escapeHtml(client.db_name || client.key)}" pattern="[a-z][a-z0-9_]{1,47}" required /></label>
        <label class="is-wide"><span>Папка</span><input name="root_path" value="${escapeHtml(client.root_path || "")}" required /></label>
        <div class="is-wide admin-client-marketplace-field">
          <span class="admin-client-registry-label">Маркетплейсы</span>
          <div class="admin-client-marketplace-options">
            ${Object.entries(CLIENT_ONBOARDING_MARKETPLACES).map(([id, item]) => `<label><input type="checkbox" name="adminClientMarketplace" value="${escapeHtml(id)}" ${marketplaceSet.has(id) ? "checked" : ""}/><span>${escapeHtml(item.label)}</span></label>`).join("")}
          </div>
        </div>
      </div>
      <fieldset class="admin-client-credentials-panel">
        <legend>Ключи маркетплейсов</legend>
        <p>Сохранённые значения не показываются. Введите только новый ключ для добавления или замены; пустое поле ничего не изменит.</p>
        <div class="admin-client-credential-grid">
          <section>
            <header><strong>Wildberries</strong><span>${escapeHtml(client?.credentials?.wb_api_token?.saved ? "Подключён" : "Нет ключа")}</span></header>
            ${renderAdminClientCredentialField(client, "wb_api_token", "WB API token", "Вставьте токен WB")}
            ${renderAdminClientCredentialField(client, "wb_service_api_token", "WB Service token для проверки Jam", "Необязательно: сервисный токен WB")}
          </section>
          <section>
            <header><strong>Ozon Seller API</strong><span>${escapeHtml(client?.credentials?.ozon_client_id?.saved && client?.credentials?.ozon_api_key?.saved ? "Подключён" : "Нет полной пары")}</span></header>
            <div class="admin-client-ozon-credential-grid">
              ${renderAdminClientCredentialField(client, "ozon_client_id", "Client-Id", "Введите Ozon Client-Id")}
              ${renderAdminClientCredentialField(client, "ozon_api_key", "Api-Key", "Вставьте Ozon Api-Key")}
            </div>
          </section>
          <section>
            <header><strong>Ozon Performance API</strong><span>${escapeHtml(client?.credentials?.ozon_performance_client_id?.saved && client?.credentials?.ozon_performance_client_secret?.saved ? "Подключён" : "Не подключён")}</span></header>
            <div class="admin-client-ozon-credential-grid">
              ${renderAdminClientCredentialField(client, "ozon_performance_client_id", "Client ID", "Введите Performance Client ID")}
              ${renderAdminClientCredentialField(client, "ozon_performance_client_secret", "Client Secret", "Введите Performance Client Secret")}
            </div>
          </section>
          <section>
            <header><strong>Avito Ads API</strong><span>${escapeHtml(client?.credentials?.avito_ads_account_id?.saved && client?.credentials?.avito_ads_client_id?.saved && client?.credentials?.avito_ads_client_secret?.saved ? "Подключён" : "Не подключён")}</span></header>
            <div class="admin-client-ozon-credential-grid">
              ${renderAdminClientCredentialField(client, "avito_ads_account_id", "Account ID", "Введите ID рекламного аккаунта")}
              ${renderAdminClientCredentialField(client, "avito_ads_client_id", "Client ID", "Введите Avito Client ID")}
              ${renderAdminClientCredentialField(client, "avito_ads_client_secret", "Client Secret", "Введите Avito Client Secret")}
            </div>
          </section>
          <section>
            <header><strong>Lamoda Seller API v2</strong><span>${escapeHtml(client?.credentials?.lamoda_client_id?.saved && client?.credentials?.lamoda_client_secret?.saved ? "Подключён" : "Не подключён")}</span></header>
            <div class="admin-client-ozon-credential-grid">
              ${renderAdminClientCredentialField(client, "lamoda_client_id", "Client ID", "Введите Lamoda Client ID")}
              ${renderAdminClientCredentialField(client, "lamoda_client_secret", "Client Secret", "Введите Lamoda Client Secret")}
            </div>
          </section>
          <section>
            <header><strong>Яндекс Маркет Partner API</strong><span>${escapeHtml(client?.credentials?.yandex_market_api_key?.saved ? "Ключ сохранён" : "Не подключён")}</span></header>
            <div class="admin-yandex-connect-row">
              ${renderAdminClientCredentialField(client, "yandex_market_api_key", "API-Key", "Введите API-Key Яндекс Маркета")}
              <button type="button" class="client-history-run-button" data-discover-yandex-market>${adminActionIcon("api")}<span>Проверить и найти магазины</span></button>
            </div>
            <small class="admin-yandex-status" data-yandex-market-status>Business ID и ID магазинов определяются автоматически.</small>
            <div data-yandex-market-accounts>${renderYandexMarketAccounts(client)}</div>
          </section>
        </div>
      </fieldset>
      ${renderAdminClientAssortmentUpdate(client)}
      <fieldset class="admin-client-report-grid">
        <legend>Доступные отчёты клиента</legend>
        ${(registry.reports || []).map((report) => `<label><input type="checkbox" name="adminClientReport" value="${escapeHtml(report.id)}" ${reportSet.has(report.id) ? "checked" : ""}/><span>${escapeHtml(report.label)}</span></label>`).join("")}
      </fieldset>
      <div class="admin-client-detail-actions">
        <span data-admin-client-save-status>Данные сохраняются в реестр и сразу обновляют селектор. Значения ключей остаются зашифрованными.</span>
        <button type="button" class="client-history-run-button primary" data-save-admin-client-access>${adminActionIcon("save")}<span>Сохранить клиента</span></button>
      </div>
    </form>
  `;
}

function renderAdminClientRegistryList(registry) {
  const rows = registry?.clients || [];
  if (!rows.length) return '<div class="admin-client-empty">Клиенты пока не зарегистрированы.</div>';
  return `
    <div class="admin-client-registry-list">
      ${rows.map((client) => {
        const expanded = client.key === state.adminOnboardingClientKey;
        return `
        <article class="admin-client-registry-row ${expanded ? "is-selected is-expanded" : ""}">
          <div class="admin-client-registry-name">
            <strong>${escapeHtml(client.label)}</strong>
            <code>${escapeHtml(client.key)}</code>
          </div>
          <div><span class="admin-client-registry-label">Маркетплейсы</span>${escapeHtml((client.marketplaces || []).join(" · ") || "—")}</div>
          <div><span class="admin-client-registry-label">Отчёты</span>${escapeHtml(String((client.reports || []).length))} доступно</div>
          <div><span class="admin-client-registry-label">База</span>${escapeHtml(client.db_name || "—")}</div>
          <div><span class="admin-client-registry-label">Ключи</span>${escapeHtml(clientCredentialSummary(client))}</div>
          <div class="admin-client-registry-actions">
            <span class="admin-client-status-light ${client.status === "active" ? "is-active" : "is-paused"}" title="${client.status === "active" ? "Подключен" : "Отключен"}" aria-label="${client.status === "active" ? "Подключен" : "Отключен"}"></span>
            ${adminIconButton(
              `${expanded ? "Свернуть" : "Развернуть"} · ${client.label}`,
              expanded ? "collapse" : "expand",
              `data-toggle-admin-client="${escapeHtml(client.key)}" aria-expanded="${expanded ? "true" : "false"}"`,
              "ghost admin-row-action",
            )}
            ${client.status === "active" ? adminIconButton(
              `Исторические данные · ${client.label}`,
              "historyData",
              `data-open-client-history="${escapeHtml(client.key)}"`,
              "ghost admin-row-action admin-row-action-history",
            ) : ""}
          </div>
          ${expanded ? renderAdminClientRegistryDetails(client, registry) : ""}
        </article>`;
      }).join("")}
    </div>
  `;
}

async function loadAdminClientRegistry() {
  state.adminClientRegistry = await getJson("/api/admin/clients");
  return state.adminClientRegistry;
}

async function loadAdminUsersRegistry() {
  state.adminUsersRegistry = await getJson("/api/admin/users");
  return state.adminUsersRegistry;
}

async function loadAdminDatabaseOverview(force = false) {
  if (!force && state.adminDatabaseOverview) return state.adminDatabaseOverview;
  state.adminDatabaseOverview = await getJson("/api/admin/database-overview");
  return state.adminDatabaseOverview;
}

const ADMIN_REPORT_ICONS = {
  abc: "assortment",
  product: "assortment",
  sku: "stock",
  adv: "advertising",
  mediaAdv: "advertising",
  funnel: "funnelStep",
  weeklyDynamics: "daily",
  inventoryHistory: "historyData",
  planfact: "finance",
  salesPlanning: "finance",
  mediaPlan: "advertising",
  profitLoss: "finance",
  unitEconomics: "finance",
  seoMonitoring: "views",
  wbSearchQueries: "api",
  wbAdSearchQueries: "api",
  wbEntrance: "funnelStep",
  commercialRadar: "validate",
};

function adminReportIcon(reportId) {
  return ADMIN_REPORT_ICONS[reportId] || "validate";
}

function adminUserSectionCatalog(registry) {
  if (Array.isArray(registry?.admin_sections) && registry.admin_sections.length) return registry.admin_sections;
  return ADMIN_SECTION_CONFIGS.map((section) => ({ id: section.key, label: section.label, caption: section.caption }));
}

function renderAdminPermissionOption(name, value, label, checked, icon, meta = "") {
  const searchText = `${label} ${meta} ${value}`.toLowerCase();
  return `
    <label class="admin-permission-option ${checked ? "is-on" : ""}" title="${escapeHtml(label)}" data-admin-permission-option data-search-text="${escapeHtml(searchText)}">
      <input type="checkbox" name="${escapeHtml(name)}" value="${escapeHtml(value)}" ${checked ? "checked" : ""} />
      <i>${adminActionIcon(icon)}</i>
      <span>${escapeHtml(label)}</span>
      ${meta ? `<em>${escapeHtml(meta)}</em>` : ""}
    </label>
  `;
}

function adminPermissionSummary(count, total) {
  if (!total) return "нет доступных";
  if (!count) return `0 из ${total}`;
  return `${count} из ${total}`;
}

function renderAdminPermissionGroup(title, summary, className, options, selectedCount = 0, totalCount = 0) {
  const allChecked = totalCount > 0 && selectedCount === totalCount;
  return `
    <details class="admin-permission-group admin-access-dropdown ${className}" data-admin-permission-dropdown>
      <summary>
        <span><strong>${escapeHtml(title)}</strong><em data-admin-permission-summary>${escapeHtml(summary)}</em></span>
        <i>${adminActionIcon("expand")}</i>
      </summary>
      <div class="admin-access-menu">
        <label class="admin-access-search">
          <span>Поиск</span>
          <input type="search" data-admin-permission-search placeholder="Найти..." autocomplete="off" />
        </label>
        <label class="admin-access-select-all">
          <input type="checkbox" data-admin-permission-all ${allChecked ? "checked" : ""} />
          <span>Выбрать все / снять</span>
        </label>
        <div class="admin-permission-grid">${options.join("")}</div>
        <p class="admin-access-empty" data-admin-permission-empty hidden>Ничего не найдено</p>
      </div>
    </details>
  `;
}

function adminLabelMap(items, keyField = "key") {
  return new Map((items || []).map((item) => [String(item[keyField] || item.id || item.key), item.label || item.caption || item.id || item.key]));
}

function renderAdminAccessPills(values, labels, emptyText) {
  const list = Array.isArray(values) ? values : [];
  if (!list.length) return `<span class="admin-user-access-empty">${escapeHtml(emptyText)}</span>`;
  const visible = list.slice(0, 3);
  const rest = list.length - visible.length;
  return `
    <div class="admin-user-access-pills">
      ${visible.map((value) => `<span>${escapeHtml(labels.get(String(value)) || value)}</span>`).join("")}
      ${rest > 0 ? `<em>+${rest}</em>` : ""}
    </div>
  `;
}

function focusAdminUserEditor() {
  window.requestAnimationFrame(() => {
    const form = document.querySelector("[data-admin-user-form]");
    const firstField = form?.querySelector('[name="display_name"]');
    form?.scrollIntoView({ behavior: "smooth", block: "start" });
    firstField?.focus({ preventScroll: true });
  });
}

function adminDbPills(values, emptyText = "—") {
  const list = Array.isArray(values) ? values.filter(Boolean) : [];
  if (!list.length) return `<span class="admin-db-muted">${escapeHtml(emptyText)}</span>`;
  return `<div class="admin-db-pills">${list.slice(0, 5).map((value) => `<span>${escapeHtml(value)}</span>`).join("")}${list.length > 5 ? `<em>+${list.length - 5}</em>` : ""}</div>`;
}

const ADMIN_DB_TYPE_LABELS = {
  table: "Таблица",
  partitioned_table: "Партиц. таблица",
  materialized_view: "Матвитрина",
  view: "View",
};
const ADMIN_DB_UNMAPPED_REPORT = "__unmapped__";

function adminDbObjectTypeLabel(type) {
  return ADMIN_DB_TYPE_LABELS[type] || type || "Объект";
}

function adminDbSortedUnique(values) {
  return [...new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "ru", { sensitivity: "base" }));
}

function adminDbSelectFilter(name, label, options, value, emptyLabel, valueLabel = (item) => item) {
  return `
    <label class="admin-db-filter">
      <span>${escapeHtml(label)}</span>
      <select data-admin-db-filter="${escapeHtml(name)}">
        <option value="">${escapeHtml(emptyLabel)}</option>
        ${options.map((option) => `<option value="${escapeHtml(option)}" ${String(value || "") === option ? "selected" : ""}>${escapeHtml(valueLabel(option))}</option>`).join("")}
      </select>
    </label>
  `;
}

function adminDbFilterOptions(objects) {
  const rows = Array.isArray(objects) ? objects : [];
  return {
    clients: adminDbSortedUnique(rows.flatMap((item) => item.client_labels || [])),
    databases: adminDbSortedUnique(rows.map((item) => item.db_name)),
    schemas: adminDbSortedUnique(rows.map((item) => item.schema)),
    types: adminDbSortedUnique(rows.map((item) => item.type)),
    reports: adminDbSortedUnique(rows.flatMap((item) => item.reports || [])),
    hasUnmappedReports: rows.some((item) => !(item.reports || []).length),
  };
}

function adminDbObjectSearchText(item) {
  const fields = (item.fields || []).flatMap((field) => [field.name, field.type, field.comment]);
  return [
    item.db_name,
    item.schema,
    item.name,
    item.type,
    adminDbObjectTypeLabel(item.type),
    item.comment,
    ...(item.client_labels || []),
    ...(item.reports || []),
    ...fields,
  ].filter(Boolean).join(" ").toLowerCase();
}

function adminDbFilteredObjects(objects) {
  const rows = Array.isArray(objects) ? objects : [];
  const filters = state.adminDatabaseFilters || {};
  const query = String(filters.search || "").trim().toLowerCase();
  return rows.filter((item) => {
    if (filters.client && !(item.client_labels || []).includes(filters.client)) return false;
    if (filters.db && item.db_name !== filters.db) return false;
    if (filters.schema && item.schema !== filters.schema) return false;
    if (filters.type && item.type !== filters.type) return false;
    if (filters.report === ADMIN_DB_UNMAPPED_REPORT && (item.reports || []).length) return false;
    if (filters.report && filters.report !== ADMIN_DB_UNMAPPED_REPORT && !(item.reports || []).includes(filters.report)) return false;
    if (query && !adminDbObjectSearchText(item).includes(query)) return false;
    return true;
  });
}

function renderAdminDbObjectFilters(objects, filteredObjects) {
  const filters = state.adminDatabaseFilters || {};
  const options = adminDbFilterOptions(objects);
  const reportOptions = options.hasUnmappedReports ? [...options.reports, ADMIN_DB_UNMAPPED_REPORT] : options.reports;
  const activeCount = ["client", "db", "schema", "type", "report", "search"]
    .filter((key) => String(filters[key] || "").trim()).length;
  return `
    <div class="admin-db-object-filters" data-admin-db-filters>
      <div class="admin-db-object-filter-grid">
        ${adminDbSelectFilter("client", "Клиент", options.clients, filters.client, "Все клиенты")}
        ${adminDbSelectFilter("db", "БД", options.databases, filters.db, "Все БД")}
        ${adminDbSelectFilter("schema", "Схема", options.schemas, filters.schema, "Все схемы")}
        ${adminDbSelectFilter("type", "Тип", options.types, filters.type, "Все типы", adminDbObjectTypeLabel)}
        ${adminDbSelectFilter("report", "Отчёт", reportOptions, filters.report, "Все отчёты", (value) => value === ADMIN_DB_UNMAPPED_REPORT ? "Не определено" : value)}
        <label class="admin-db-filter admin-db-filter-search">
          <span>Поиск</span>
          <input type="search" data-admin-db-filter="search" value="${escapeHtml(filters.search || "")}" placeholder="таблица, поле, комментарий" autocomplete="off" />
        </label>
      </div>
      <div class="admin-db-filter-status">
        <span>${formatNumber(filteredObjects.length)} из ${formatNumber((objects || []).length)} объектов${activeCount ? ` · фильтров ${activeCount}` : ""}</span>
        ${adminIconButton("Сбросить фильтры БД", "clear", "data-reset-admin-database-filters", "ghost admin-db-filter-reset", activeCount === 0)}
      </div>
    </div>
  `;
}

function updateAdminDatabaseFilter(name, value, restoreFocus = false, selectionStart = null, selectionEnd = null) {
  state.adminDatabaseFilters = {
    client: "",
    db: "",
    schema: "",
    type: "",
    report: "",
    search: "",
    ...(state.adminDatabaseFilters || {}),
    [name]: String(value || ""),
  };
  renderAdminDashboardContent(lastAdminPayload);
  if (restoreFocus) {
    window.requestAnimationFrame(() => {
      const field = document.querySelector(`[data-admin-db-filter="${name}"]`);
      field?.focus({ preventScroll: true });
      if (field instanceof HTMLInputElement && selectionStart !== null) {
        field.setSelectionRange(selectionStart, selectionEnd ?? selectionStart);
      }
    });
  }
}

function resetAdminDatabaseFilters() {
  state.adminDatabaseFilters = { client: "", db: "", schema: "", type: "", report: "", search: "" };
  renderAdminDashboardContent(lastAdminPayload);
}

function adminDbFieldsMarkup(fields) {
  const list = Array.isArray(fields) ? fields : [];
  if (!list.length) return `<span class="admin-db-muted">нет полей</span>`;
  return `
    <details class="admin-db-fields">
      <summary>${formatNumber(list.length)} полей</summary>
      <div>
        ${list.map((field) => `
          <span title="${escapeHtml(field.comment || field.default || "")}">
            <b>${escapeHtml(field.name)}</b>
            <em>${escapeHtml(field.type)}${field.nullable ? "" : " · NOT NULL"}</em>
          </span>
        `).join("")}
      </div>
    </details>
  `;
}

function renderAdminDatabaseDashboard() {
  const overview = state.adminDatabaseOverview;
  if (!overview) return `<section class="admin-db-panel"><p class="admin-db-empty">Загружаю структуру PostgreSQL...</p></section>`;
  const databases = overview.databases || [];
  const objects = overview.objects || [];
  const filteredObjects = adminDbFilteredObjects(objects);
  const totals = overview.totals || {};
  return `
    <section class="admin-db-panel">
      <header class="admin-db-header">
        <div>
          <h3>БД</h3>
          <span>${formatNumber(totals.databases || databases.length)} БД · ${formatNumber(totals.objects || objects.length)} объектов · ${formatNumber(totals.materialized_views || 0)} матвитрин · ${formatBytes(totals.size_bytes || 0)}</span>
        </div>
        ${adminIconButton("Обновить структуру БД", "database", "data-refresh-admin-database", "ghost")}
      </header>
      ${overview.catalog_error ? `<div class="admin-db-warning">Каталог PostgreSQL прочитан частично: ${escapeHtml(overview.catalog_error)}</div>` : ""}
      <section class="admin-db-section">
        <header><strong>Базы и клиенты</strong><span>${escapeHtml(overview.row_count_source || "оценка строк PostgreSQL")}</span></header>
        <div class="admin-db-table-wrap">
          <table class="admin-db-table admin-db-database-table">
            <thead><tr><th>БД</th><th>Клиенты</th><th>Отчёты</th><th>Таблицы</th><th>Матвитрины</th><th>Поля</th><th>Строки</th><th>Вес</th><th>Статус</th></tr></thead>
            <tbody>
              ${databases.map((db) => `<tr class="${db.error ? "is-warning" : ""}">
                <td><strong>${escapeHtml(db.db_name)}</strong></td>
                <td>${adminDbPills(db.client_labels, "не привязана")}</td>
                <td>${adminDbPills(db.reports, "нет отчётов")}</td>
                <td class="num">${formatNumber(db.table_count || 0)}</td>
                <td class="num">${formatNumber(db.materialized_view_count || 0)}</td>
                <td class="num">${formatNumber(db.field_count || 0)}</td>
                <td class="num">${formatCompactNumber(db.rows_estimate || 0)}</td>
                <td class="num">${formatBytes(db.size_bytes || 0)}</td>
                <td>${db.error ? `<span class="admin-db-status is-warning">${escapeHtml(db.error)}</span>` : `<span class="admin-db-status is-ok">Доступна</span>`}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </section>
      <section class="admin-db-section">
        <header><strong>Таблицы, поля и матвитрины</strong><span>${formatNumber(filteredObjects.length)} из ${formatNumber(objects.length)} объектов в клиентских БД</span></header>
        ${renderAdminDbObjectFilters(objects, filteredObjects)}
        <div class="admin-db-table-wrap">
          <table class="admin-db-table admin-db-object-table">
            <thead><tr><th>Клиент / БД</th><th>Объект</th><th>Тип</th><th>Что содержит</th><th>Поля</th><th>Строки</th><th>Вес</th><th>Для отчётов</th></tr></thead>
            <tbody>
              ${filteredObjects.length ? filteredObjects.map((item) => `<tr class="${item.type === "materialized_view" ? "is-matview" : ""}">
                <td>${adminDbPills(item.client_labels, "—")}<code>${escapeHtml(item.db_name)}</code></td>
                <td><strong>${escapeHtml(item.schema)}.${escapeHtml(item.name)}</strong></td>
                <td><span class="admin-db-type ${item.type === "materialized_view" ? "is-matview" : ""}">${escapeHtml(adminDbObjectTypeLabel(item.type))}</span></td>
                <td>${escapeHtml(item.comment || (item.fields || []).slice(0, 4).map((field) => field.name).join(", ") || "Комментарий не задан")}</td>
                <td>${adminDbFieldsMarkup(item.fields)}</td>
                <td class="num">${item.rows_estimate == null ? "—" : formatCompactNumber(item.rows_estimate)}</td>
                <td class="num">${formatBytes(item.total_bytes || 0)}</td>
                <td>${adminDbPills(item.reports, "не определено")}</td>
              </tr>`).join("") : `<tr><td colspan="8"><span class="admin-db-empty-inline">По выбранным фильтрам объектов нет</span></td></tr>`}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  `;
}

async function loadAdminIntegrations() {
  state.adminIntegrations = await getJson("/api/admin/integrations");
  return state.adminIntegrations;
}

function renderAdminIntegrationsDashboard() {
  const payload = state.adminIntegrations;
  if (!payload) return `<section class="admin-integrations-panel"><p class="admin-db-empty">Загружаю интеграции...</p></section>`;
  const services = Array.isArray(payload.services) ? payload.services : [];
  return `
    <section class="admin-integrations-panel">
      <header class="admin-integrations-head">
        <div><h3>Интеграции</h3><span>Общие API-ключи внешних сервисов для рабочих процессов PULSE</span></div>
        <span class="admin-integrations-security">PostgreSQL · AES-256 · ключи не отображаются</span>
      </header>
      <div class="admin-integrations-list">
        ${services.map((service) => `
          <article class="admin-integration-card" data-integration="${escapeHtml(service.key)}">
            <div class="admin-integration-meta">
              <strong>${escapeHtml(service.label)}</strong>
              <span>${escapeHtml(service.description || "")}</span>
              <small class="${service.saved ? "is-saved" : ""}">${service.saved ? "Подключение сохранено" : "Подключение не настроено"}</small>
            </div>
            <div class="admin-integration-fields">${(service.credentials || []).map((field) => `<label class="admin-integration-field">
              <span>${escapeHtml(field.label)}${field.saved ? ` · сохранён ${escapeHtml(field.fingerprint)}` : ""}</span>
              <input data-integration-credential="${escapeHtml(field.key)}" id="integration-${escapeHtml(service.key)}-${escapeHtml(field.key)}" type="password" autocomplete="new-password" placeholder="${escapeHtml(field.saved ? "Введите новое значение для замены" : field.placeholder)}" />
            </label>`).join("")}</div>
            <div class="admin-integration-actions">
              <button type="button" onclick="saveAdminIntegration('${escapeHtml(service.key)}')">${service.saved ? "Обновить подключение" : "Сохранить подключение"}</button>
              ${service.saved ? `<button type="button" class="secondary" onclick="deleteAdminIntegration('${escapeHtml(service.key)}')">Удалить</button>` : ""}
            </div>
            <div class="admin-integration-status" id="integration-${escapeHtml(service.key)}-status"></div>
          </article>
        `).join("") || `<p class="admin-db-empty">Интеграции пока не настроены.</p>`}
      </div>
    </section>
  `;
}

async function saveAdminIntegration(service) {
  const card = document.querySelector(`[data-integration="${CSS.escape(service)}"]`);
  const status = qs(`integration-${service}-status`);
  const credentials = {};
  card?.querySelectorAll("[data-integration-credential]").forEach((input) => {
    credentials[input.dataset.integrationCredential] = String(input.value || "").trim();
  });
  if (!Object.keys(credentials).length || Object.values(credentials).some((value) => !value)) { if (status) status.textContent = "Заполните все поля подключения"; return; }
  if (status) status.textContent = "Сохраняю...";
  try {
    state.adminIntegrations = await postJson("/api/admin/integrations", { service, credentials });
    renderAdminDashboardContent(lastAdminPayload);
  } catch (error) {
    if (status) status.textContent = `Ошибка: ${error.message || error}`;
  }
}

async function deleteAdminIntegration(service) {
  const status = qs(`integration-${service}-status`);
  if (status) status.textContent = "Удаляю...";
  try {
    state.adminIntegrations = await postJson("/api/admin/integrations", { service, action: "delete" });
    renderAdminDashboardContent(lastAdminPayload);
  } catch (error) {
    if (status) status.textContent = `Ошибка: ${error.message || error}`;
  }
}

function renderAdminUsersDashboard() {
  const registry = state.adminUsersRegistry;
  if (!registry) return `<section class="admin-users-panel"><p class="admin-users-empty">Загружаю реестр пользователей...</p></section>`;
  const users = registry.users || [];
  const current = users.find((user) => Number(user.user_id) === Number(state.adminEditingUserId)) || null;
  const clientSet = new Set(current?.clients || []);
  const reportSet = new Set(current?.reports || []);
  const adminSectionSet = new Set(current?.admin_sections || []);
  const adminSections = adminUserSectionCatalog(registry);
  const clients = registry.clients || [];
  const reports = registry.reports || [];
  const activeUsers = users.filter((user) => user.is_active !== false).length;
  const editorTitle = current ? "Редактирование пользователя" : "Добавление нового пользователя";
  const editorMeta = current ? `${current.display_name} · @${current.username}` : "логин, пароль, почта и права доступа";
  const resetEditorTitle = current ? "Отменить редактирование пользователя" : "Очистить форму пользователя";
  const adminLabels = adminLabelMap(adminSections, "id");
  const clientLabels = adminLabelMap(clients, "key");
  const reportLabels = adminLabelMap(reports, "id");
  const adminOptions = adminSections.map((section) => renderAdminPermissionOption(
    "adminUserAdminSection",
    section.id || section.key,
    section.label || section.caption || section.id,
    adminSectionSet.has(section.id || section.key),
    section.id || section.key,
    section.caption || "Админка",
  ));
  const clientOptions = clients.map((client) => renderAdminPermissionOption(
    "adminUserClient",
    client.key,
    client.label,
    clientSet.has(client.key),
    "client",
    client.key,
  ));
  const reportOptions = reports.map((report) => renderAdminPermissionOption(
    "adminUserReport",
    report.id,
    report.label,
    reportSet.has(report.id),
    adminReportIcon(report.id),
    report.id,
  ));
  return `
    <section class="admin-users-panel">
      <header class="admin-users-header">
        <div>
          <h3>Пользователи и доступы</h3>
          <span>${users.length} в реестре · активны ${activeUsers} · ${escapeHtml(registry.password_storage || "PBKDF2-SHA256")}</span>
        </div>
      </header>
      <section class="admin-user-create-block" aria-label="${escapeHtml(editorTitle)}">
        <form class="admin-user-form" data-admin-user-form>
          <input type="hidden" name="user_id" value="${current?.user_id || ""}" />
          <div class="admin-user-editor-head">
            <div><strong>${escapeHtml(editorTitle)}</strong><span>${escapeHtml(editorMeta)}</span></div>
            ${adminIconButton(resetEditorTitle, current ? "close" : "client", "data-new-admin-user", `ghost admin-users-new-card ${current ? "admin-user-cancel-edit" : ""}`)}
            <button type="submit" class="admin-icon-button admin-user-save" title="Сохранить пользователя" aria-label="Сохранить пользователя">${adminActionIcon("save")}</button>
          </div>
          <div class="admin-user-fields">
            <label><span>Имя</span><input name="display_name" value="${escapeHtml(current?.display_name || "")}" maxlength="120" required /></label>
            <label><span>Логин</span><input name="username" value="${escapeHtml(current?.username || "")}" pattern="[a-z][a-z0-9._\\-]{2,63}" autocomplete="off" required /></label>
            <label><span>Почта</span><input name="email" type="email" value="${escapeHtml(current?.email || "")}" autocomplete="email" placeholder="user@company.ru" /></label>
            <label><span>Пароль</span><span class="admin-user-password-field"><input name="password" type="password" minlength="10" autocomplete="new-password" placeholder="${current ? "Оставьте пустым, чтобы не менять" : "Не менее 10 символов"}" ${current ? "" : "required"} /><button type="button" class="admin-user-password-toggle" data-toggle-admin-user-password title="Показать пароль" aria-label="Показать пароль" aria-pressed="false">${adminActionIcon("eye")}</button></span></label>
          </div>
          <div class="admin-user-toggles">
            <label class="admin-user-active" title="Активен">
              <input name="is_active" type="checkbox" ${current?.is_active !== false ? "checked" : ""} />
              <span></span><strong>Активен</strong>
            </label>
            <label class="admin-user-active admin-user-email-verified" title="Почта подтверждена">
              <input name="email_verified" type="checkbox" ${current?.email_verified ? "checked" : ""} />
              <span></span><strong>Почта подтверждена</strong>
            </label>
          </div>
          <div class="admin-user-permissions">
            ${renderAdminPermissionGroup("Админка", adminPermissionSummary(adminSectionSet.size, adminSections.length), "admin-permission-admin", adminOptions, adminSectionSet.size, adminSections.length)}
            ${renderAdminPermissionGroup("Клиенты", adminPermissionSummary(clientSet.size, clients.length), "admin-permission-clients", clientOptions, clientSet.size, clients.length)}
            ${renderAdminPermissionGroup("Отчёты", adminPermissionSummary(reportSet.size, reports.length), "admin-permission-reports", reportOptions, reportSet.size, reports.length)}
          </div>
        </form>
      </section>
      <section class="admin-user-directory-block" aria-label="Действующие пользователи">
        <header>
          <div><strong>Действующие пользователи</strong><span>${activeUsers} активны · ${users.length} всего</span></div>
        </header>
        ${users.length ? `
          <div class="admin-user-table" role="table" aria-label="Список пользователей">
            <div class="admin-user-table-row admin-user-table-head" role="row">
              <span role="columnheader">Пользователь</span>
              <span role="columnheader">Почта</span>
              <span role="columnheader">Клиенты</span>
              <span role="columnheader">Отчёты</span>
              <span role="columnheader">Админка</span>
              <span role="columnheader">Статус</span>
              <span role="columnheader">Карточка</span>
            </div>
            ${users.map((user) => {
              const active = current && Number(current.user_id) === Number(user.user_id);
              const email = user.email || "";
              const emailStatus = email ? (user.email_verified ? "подтверждена" : "не подтверждена") : "почта не указана";
              return `<div class="admin-user-table-row ${active ? "is-editing" : ""}" role="row">
                <div class="admin-user-person" role="cell">
                  <i class="admin-user-state-light ${user.is_active ? "is-active" : "is-muted"}"></i>
                  <span><strong>${escapeHtml(user.display_name)}</strong><em>@${escapeHtml(user.username)}</em></span>
                </div>
                <div class="admin-user-email" role="cell"><span>${escapeHtml(email || "—")}</span><em class="${user.email_verified ? "is-ok" : ""}">${escapeHtml(emailStatus)}</em></div>
                <div role="cell">${renderAdminAccessPills(user.clients, clientLabels, "нет клиентов")}</div>
                <div role="cell">${renderAdminAccessPills(user.reports, reportLabels, "нет отчётов")}</div>
                <div role="cell">${renderAdminAccessPills(user.admin_sections, adminLabels, "без админки")}</div>
                <div role="cell"><span class="admin-user-status ${user.is_active ? "is-active" : "is-muted"}">${user.is_active ? "Активен" : "Отключен"}</span></div>
                <div role="cell">
                  <button type="button" class="admin-user-edit-button ${active ? "is-active" : ""}" data-edit-admin-user="${user.user_id}" title="${active ? "Перейти к форме редактирования" : "Редактировать карточку пользователя"}" aria-label="${active ? "Перейти к форме редактирования" : "Редактировать карточку пользователя"}" aria-pressed="${active ? "true" : "false"}">
                    ${adminActionIcon("users")}<span>${active ? "Редактируется" : "Редактировать"}</span>
                  </button>
                </div>
              </div>`;
            }).join("")}
          </div>
        ` : `<p class="admin-users-empty">Пользователей пока нет. Заполните верхнюю форму, выберите клиентов и отчёты, затем сохраните карточку.</p>`}
      </section>
    </section>`;
}

async function saveAdminUserFromUi(form) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const payload = {
      user_id: Number(form.elements.user_id.value || 0),
      display_name: form.elements.display_name.value.trim(),
      username: form.elements.username.value.trim(),
      email: form.elements.email?.value.trim() || "",
      email_verified: Boolean(form.elements.email_verified?.checked),
      password: form.elements.password.value,
      is_active: form.elements.is_active.checked,
      clients: [...form.querySelectorAll('[name="adminUserClient"]:checked')].map((input) => input.value),
      reports: [...form.querySelectorAll('[name="adminUserReport"]:checked')].map((input) => input.value),
      admin_sections: [...form.querySelectorAll('[name="adminUserAdminSection"]:checked')].map((input) => input.value),
    };
    state.adminUsersRegistry = await postJson("/api/admin/users", payload);
    state.adminEditingUserId = Number(state.adminUsersRegistry.saved_user?.user_id || payload.user_id || 0);
    renderAdminDashboardContent(lastAdminPayload);
    qs("status").textContent = "Пользователь и права сохранены";
  } finally {
    button.disabled = false;
  }
}

function activeClientsFromRegistry(registry) {
  return (registry?.clients || [])
    .filter((client) => client.status === "active")
    .map((client) => ({
      key: client.key,
      label: client.label,
      status: client.status,
      reports: client.reports || [],
      marketplaces: client.marketplaces || [],
    }));
}

function adminRegistryClientByKey(key) {
  return (state.adminClientRegistry?.clients || []).find((client) => client.key === key) || null;
}

function clearAdminClientSaveValidation(form) {
  if (!form) return;
  form.querySelectorAll('[data-admin-client-credential][aria-invalid="true"]').forEach((input) => {
    input.removeAttribute("aria-invalid");
    const field = input.closest(".admin-client-credential-field");
    field?.classList.remove("is-invalid");
    const status = field?.querySelector(".admin-client-credential-status");
    if (status?.dataset.validationOriginalStatus !== undefined) {
      status.textContent = status.dataset.validationOriginalStatus;
      delete status.dataset.validationOriginalStatus;
    }
  });
  form.querySelector("[data-admin-client-save-status]")?.classList.remove("is-error");
}

function throwAdminClientSaveValidation(form, message, missingCredentialKeys = []) {
  const statusNode = form?.querySelector("[data-admin-client-save-status]");
  if (statusNode) {
    statusNode.textContent = `Ошибка: ${message}`;
    statusNode.classList.add("is-error");
  }
  const credentialInputs = [...(form?.querySelectorAll("[data-admin-client-credential]") || [])];
  const missingInputs = missingCredentialKeys
    .map((key) => credentialInputs.find((input) => input.dataset.adminClientCredential === key))
    .filter(Boolean);
  missingInputs.forEach((input) => {
    input.setAttribute("aria-invalid", "true");
    const field = input.closest(".admin-client-credential-field");
    field?.classList.add("is-invalid");
    const status = field?.querySelector(".admin-client-credential-status");
    if (status) {
      if (status.dataset.validationOriginalStatus === undefined) {
        status.dataset.validationOriginalStatus = status.textContent || "";
      }
      status.textContent = "Обязательное поле не заполнено";
    }
  });
  const firstMissing = missingInputs[0];
  if (firstMissing) {
    firstMissing.focus({preventScroll: true});
    firstMissing.scrollIntoView({block: "center", behavior: "smooth"});
  }
  const error = new Error(message);
  error.isAdminClientValidation = true;
  throw error;
}

async function saveAdminClientAccessFromUi(button) {
  const form = button.closest("[data-admin-client-form]");
  const key = form?.dataset.clientKey || "";
  const client = adminRegistryClientByKey(key);
  if (!form || !client) throw new Error("Клиент не найден в реестре");
  clearAdminClientSaveValidation(form);
  const reports = [...form.querySelectorAll('[name="adminClientReport"]:checked')].map((input) => input.value);
  if (!reports.length) throwAdminClientSaveValidation(form, "Выберите хотя бы один отчёт");
  const marketplaces = [...form.querySelectorAll('[name="adminClientMarketplace"]:checked')].map((input) => input.value);
  if (!marketplaces.length) throwAdminClientSaveValidation(form, "У клиента не указан маркетплейс");
  const credentials = {};
  form.querySelectorAll("[data-admin-client-credential]").forEach((input) => {
    const credentialKey = input.dataset.adminClientCredential || "";
    const value = input.value.trim();
    if (credentialKey && value) credentials[credentialKey] = value;
  });
  const savedCredentials = client.credentials || {};
  const hasOzonClientId = Boolean(credentials.ozon_client_id || savedCredentials.ozon_client_id?.saved);
  const hasOzonApiKey = Boolean(credentials.ozon_api_key || savedCredentials.ozon_api_key?.saved);
  if ((credentials.ozon_client_id || credentials.ozon_api_key) && (!hasOzonClientId || !hasOzonApiKey)) {
    const missingKeys = ["ozon_client_id", "ozon_api_key"]
      .filter((credentialKey) => !(credentials[credentialKey] || savedCredentials[credentialKey]?.saved));
    throwAdminClientSaveValidation(form, "Для Ozon сохраните пару Client-Id и Api-Key", missingKeys);
  }
  const avitoCredentialKeys = ["avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret"];
  const hasAnyAvitoCredential = avitoCredentialKeys.some((key) => credentials[key] || savedCredentials[key]?.saved);
  const hasFullAvitoCredentials = avitoCredentialKeys.every((key) => credentials[key] || savedCredentials[key]?.saved);
  if (hasAnyAvitoCredential && !hasFullAvitoCredentials) {
    const missingKeys = avitoCredentialKeys
      .filter((credentialKey) => !(credentials[credentialKey] || savedCredentials[credentialKey]?.saved));
    throwAdminClientSaveValidation(form, "Для Avito Ads сохраните Account ID, Client ID и Client Secret", missingKeys);
  }
  const credentialGroups = [
    { label: "Lamoda", keys: ["lamoda_client_id", "lamoda_client_secret"] },
  ];
  credentialGroups.forEach(({ label, keys }) => {
    const hasAny = keys.some((credentialKey) => credentials[credentialKey] || savedCredentials[credentialKey]?.saved);
    const hasFull = keys.every((credentialKey) => credentials[credentialKey] || savedCredentials[credentialKey]?.saved);
    if (hasAny && !hasFull) {
      const missingKeys = keys
        .filter((credentialKey) => !(credentials[credentialKey] || savedCredentials[credentialKey]?.saved));
      throwAdminClientSaveValidation(form, `Для ${label} заполните все обязательные поля доступа`, missingKeys);
    }
  });
  if (marketplaces.includes("yandex_market") && !(credentials.yandex_market_api_key || savedCredentials.yandex_market_api_key?.saved)) {
    throwAdminClientSaveValidation(form, "Для Яндекс Маркета заполните API-ключ", ["yandex_market_api_key"]);
  }
  const yandexMarketAccounts = client?.marketplace_accounts?.yandex_market || [];
  const yandexMarketEnabledStoreIds = [...form.querySelectorAll("[data-yandex-market-store]:checked")]
    .map((input) => input.value).filter(Boolean);
  button.disabled = true;
  const statusNode = form.querySelector("[data-admin-client-save-status]");
  if (statusNode) statusNode.textContent = "Сохраняю изменения...";
  try {
    const result = await postJson("/api/admin/clients", {
      key: client.key,
      label: form.elements.label.value.trim(),
      db_name: form.elements.db_name.value.trim(),
      root_path: form.elements.root_path.value.trim(),
      status: form.elements.status.value,
      marketplaces,
      reports,
      credentials,
      yandex_market_accounts: yandexMarketAccounts,
      yandex_market_enabled_store_ids: yandexMarketAccounts.length ? yandexMarketEnabledStoreIds : undefined,
    });
    state.adminClientRegistry = result;
    const activeClients = activeClientsFromRegistry(result);
    if (activeClients.length) {
      state.clients = activeClients;
      rememberNavigationEntitlements(activeClients);
    }
    renderClientSelector();
    form.dataset.adminClientDraftDirty = "false";
    form.querySelectorAll("[data-admin-client-credential]").forEach((input) => { input.value = ""; });
    renderAdminDashboardContent(lastAdminPayload);
    qs("status").textContent = "Настройки клиента сохранены";
  } finally {
    button.disabled = false;
  }
}

async function discoverAdminYandexMarket(button) {
  const form = button.closest("[data-admin-client-form]");
  const clientKey = form?.dataset.clientKey || "";
  const client = adminRegistryClientByKey(clientKey);
  if (!form || !client) throw new Error("Клиент не найден в реестре");
  clearAdminClientSaveValidation(form);
  const input = form.querySelector('[data-admin-client-credential="yandex_market_api_key"]');
  const apiKey = input?.value.trim() || "";
  if (!apiKey && !client?.credentials?.yandex_market_api_key?.saved) {
    throwAdminClientSaveValidation(form, "Введите API-ключ Яндекс Маркета", ["yandex_market_api_key"]);
  }
  const status = form.querySelector("[data-yandex-market-status]");
  button.disabled = true;
  if (status) status.textContent = "Проверяю ключ и запрашиваю список магазинов...";
  try {
    const result = await postJson("/api/admin/yandex-market/discover", {client: clientKey, api_key: apiKey});
    const choices = new Map([...form.querySelectorAll("[data-yandex-market-store]")]
      .map((checkbox) => [checkbox.value, checkbox.checked]));
    const accounts = (result.accounts || []).map((account) => ({...account,
      stores: (account.stores || []).map((store) => ({...store,
        import_enabled: store.is_accessible !== false && (choices.has(store.campaign_id)
          ? choices.get(store.campaign_id) : store.import_enabled !== false),
      })),
    }));
    client.marketplace_accounts = {...(client.marketplace_accounts || {}), yandex_market: accounts};
    const container = form.querySelector("[data-yandex-market-accounts]");
    if (container) container.innerHTML = renderYandexMarketAccounts(client);
    if (status) status.textContent = `Найдено бизнесов: ${result.business_count || 0}, магазинов: ${result.store_count || 0}. Сохраните клиента.`;
    form.dataset.adminClientDraftDirty = "true";
  } catch (error) {
    if (status) status.textContent = error.message || "Не удалось проверить API-ключ. Повторите проверку.";
    throw error;
  } finally {
    button.disabled = false;
  }
}

function onboardingClientKey(name) {
  const map = {а:"a",б:"b",в:"v",г:"g",д:"d",е:"e",ё:"e",ж:"zh",з:"z",и:"i",й:"y",к:"k",л:"l",м:"m",н:"n",о:"o",п:"p",р:"r",с:"s",т:"t",у:"u",ф:"f",х:"h",ц:"ts",ч:"ch",ш:"sh",щ:"sch",ъ:"",ы:"y",ь:"",э:"e",ю:"yu",я:"ya"};
  return String(name || "").trim().toLowerCase().split("").map((char) => map[char] ?? char)
    .join("").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48);
}

function mergeOnboardingClient(client) {
  if (!client || !state.adminClientRegistry) return;
  const rows = [...(state.adminClientRegistry.clients || [])];
  const index = rows.findIndex((row) => row.key === client.key);
  if (index >= 0) rows[index] = client;
  else rows.push(client);
  state.adminClientRegistry = {...state.adminClientRegistry, clients: rows};
}

function refreshAdminClientAssortmentPanel(clientKey) {
  const client = adminRegistryClientByKey(clientKey);
  const form = [...document.querySelectorAll("[data-admin-client-form]")]
    .find((item) => item.dataset.clientKey === clientKey);
  const currentPanel = form?.querySelector("[data-admin-client-assortment]");
  if (!client || !currentPanel) return false;
  const template = document.createElement("template");
  template.innerHTML = renderAdminClientAssortmentUpdate(client).trim();
  const nextPanel = template.content.firstElementChild;
  if (!nextPanel) return false;
  currentPanel.replaceWith(nextPanel);
  window.requestAnimationFrame(() => scrollAdminClientAssortmentTerminal(clientKey));
  return true;
}

function captureAdminClientEditorDraft() {
  const form = [...document.querySelectorAll("[data-admin-client-form]")]
    .find((item) => item.dataset.adminClientDraftDirty !== "false");
  if (!form) return null;
  const controls = [...form.querySelectorAll("input, select, textarea")];
  const activeIndex = controls.indexOf(document.activeElement);
  const saveStatus = form.querySelector("[data-admin-client-save-status]");
  return {
    clientKey: form.dataset.clientKey || "",
    activeIndex,
    controls: controls.map((control) => ({
      value: control.value,
      checked: Boolean(control.checked),
      ariaInvalid: control.getAttribute("aria-invalid"),
      fieldInvalid: control.closest(".admin-client-credential-field")?.classList.contains("is-invalid") || false,
      fieldStatus: control.closest(".admin-client-credential-field")?.querySelector(".admin-client-credential-status")?.textContent || null,
    })),
    saveStatusText: saveStatus?.textContent || "",
    saveStatusError: saveStatus?.classList.contains("is-error") || false,
  };
}

function restoreAdminClientEditorDraft(draft) {
  if (!draft?.clientKey) return;
  const form = [...document.querySelectorAll("[data-admin-client-form]")]
    .find((item) => item.dataset.clientKey === draft.clientKey);
  if (!form) return;
  form.dataset.adminClientDraftDirty = "true";
  const controls = [...form.querySelectorAll("input, select, textarea")];
  draft.controls.forEach((saved, index) => {
    const control = controls[index];
    if (!control) return;
    if (control.type === "checkbox" || control.type === "radio") control.checked = saved.checked;
    else control.value = saved.value;
    if (saved.ariaInvalid === null) control.removeAttribute("aria-invalid");
    else control.setAttribute("aria-invalid", saved.ariaInvalid);
    const field = control.closest(".admin-client-credential-field");
    field?.classList.toggle("is-invalid", saved.fieldInvalid);
    const fieldStatus = field?.querySelector(".admin-client-credential-status");
    if (fieldStatus && saved.fieldStatus !== null) fieldStatus.textContent = saved.fieldStatus;
  });
  const saveStatus = form.querySelector("[data-admin-client-save-status]");
  if (saveStatus) {
    saveStatus.textContent = draft.saveStatusText;
    saveStatus.classList.toggle("is-error", draft.saveStatusError);
  }
  if (draft.activeIndex >= 0) controls[draft.activeIndex]?.focus({preventScroll: true});
}

async function pollAdminClientOnboarding(clientKey) {
  const token = ++state.adminOnboardingPollToken;
  for (let attempt = 0; attempt < 2400; attempt += 1) {
    if (token !== state.adminOnboardingPollToken || state.dashboard !== "admin") return;
    const result = await getJson(`/api/admin/client-onboarding?client=${encodeURIComponent(clientKey)}`);
    mergeOnboardingClient(result.client);
    if (state.adminImportMode === "clientOnboarding") {
      renderAdminDashboardContent(lastAdminPayload);
      window.requestAnimationFrame(() => {
        scrollClientHistoryTerminal();
      });
    } else if (state.adminImportMode === "client") {
      refreshAdminClientAssortmentPanel(clientKey);
    }
    const status = result.client?.operation_status;
    if (["completed", "failed", "stopped"].includes(status)) {
      await loadAdminClientRegistry();
      mergeOnboardingClient(result.client);
      const activeClients = activeClientsFromRegistry(state.adminClientRegistry);
      if (activeClients.length) {
        state.clients = activeClients;
        rememberNavigationEntitlements(activeClients);
      }
      renderClientSelector();
      if (state.adminImportMode === "clientOnboarding") renderAdminDashboardContent(lastAdminPayload);
      else if (state.adminImportMode === "client") refreshAdminClientAssortmentPanel(clientKey);
      if (state.adminOnboardingClientKey === clientKey && historySelectedMarketplace(result.client) === "wb") {
        loadAdminClientHistoryInspection({force: true}).catch((error) => setStatusError(error));
      }
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
  }
}

async function restoreAdminClientHistoryContext() {
  const clients = state.adminClientRegistry?.clients || [];
  const saved = clients.find((client) => client.key === state.adminOnboardingClientKey);
  const running = clients.find((client) => client.operation === "history" && client.operation_status === "running");
  const selected = running || saved;
  if (!selected || selected.operation !== "history") return;
  state.adminOnboardingClientKey = selected.key;
  const result = await getJson(`/api/admin/client-onboarding?client=${encodeURIComponent(selected.key)}`);
  mergeOnboardingClient(result.client);
  renderAdminDashboardContent(lastAdminPayload);
  window.requestAnimationFrame(scrollClientHistoryTerminal);
  persistDashboardState();
  if (result.client?.operation_status === "running") {
    pollAdminClientOnboarding(selected.key).catch((error) => {
      setStatusError(error, "Ошибка восстановления контроля истории");
    });
  }
}

function scrollAdminClientAssortmentTerminal(clientKey) {
  const terminal = document.getElementById(`clientAssortmentTerminal-${clientKey}`);
  if (terminal) terminal.scrollTop = terminal.scrollHeight;
}

async function startAdminClientAssortment(button) {
  const form = button.closest("[data-admin-client-form]");
  const client = form?.dataset.clientKey || "";
  if (!form || !client) throw new Error("Клиент не найден в реестре");
  const marketplaces = [...form.querySelectorAll('[name="adminAssortmentMarketplace"]:checked')]
    .map((input) => input.value);
  if (!marketplaces.length) throw new Error("Выберите Ozon и/или WB");
  button.disabled = true;
  try {
    const result = await postJson("/api/admin/client-assortment/start", {client, marketplaces});
    mergeOnboardingClient(result.client);
    refreshAdminClientAssortmentPanel(client);
    pollAdminClientOnboarding(client).catch((error) => setStatusError(error));
  } finally {
    button.disabled = false;
  }
}

async function stopAdminClientAssortment(button) {
  const form = button.closest("[data-admin-client-form]");
  const client = form?.dataset.clientKey || "";
  if (!client) throw new Error("Клиент не найден в реестре");
  button.disabled = true;
  const result = await postJson("/api/admin/client-assortment/stop", {client});
  mergeOnboardingClient(result.client);
  refreshAdminClientAssortmentPanel(client);
}

async function restoreAdminClientAssortmentContext() {
  const clients = state.adminClientRegistry?.clients || [];
  const running = clients.find((client) => client.operation === "assortment" && client.operation_status === "running");
  const selected = running || clients.find((client) => client.key === state.adminOnboardingClientKey && client.operation === "assortment");
  if (!selected) return;
  state.adminOnboardingClientKey = selected.key;
  const result = await getJson(`/api/admin/client-onboarding?client=${encodeURIComponent(selected.key)}`);
  mergeOnboardingClient(result.client);
  renderAdminDashboardContent(lastAdminPayload);
  window.requestAnimationFrame(() => scrollAdminClientAssortmentTerminal(selected.key));
  persistDashboardState();
  if (result.client?.operation_status === "running") {
    pollAdminClientOnboarding(selected.key).catch((error) => setStatusError(error, "Ошибка восстановления soft-update"));
  }
}

async function saveAdminClientFromUi(button) {
  const label = qs("newClientName")?.value?.trim() || "";
  const key = onboardingClientKey(label);
  if (!label || !key) throw new Error("Введите название клиента");
  state.adminOnboardingDraftName = label;
  const marketplace = state.adminOnboardingMarketplace || "ozon";
  const reports = [...document.querySelectorAll('[name="newClientReport"]:checked')].map((input) => input.value);
  if (!reports.length) throw new Error("Выберите хотя бы один отчёт для клиента");
  const payload = {
    label,
    key,
    db_name: key,
    marketplace,
    reports,
    credentials: marketplace === "wb" ? {
        wb_api_token: qs("newClientWbToken")?.value || "",
        wb_service_api_token: qs("newClientWbServiceToken")?.value || "",
      } : marketplace === "ozon" ? {
        ozon_client_id: qs("newClientOzonClientId")?.value || "",
        ozon_api_key: qs("newClientOzonApiKey")?.value || "",
        ozon_performance_client_id: qs("newClientOzonPerformanceId")?.value || "",
        ozon_performance_client_secret: qs("newClientOzonPerformanceSecret")?.value || "",
      } : marketplace === "avito" ? {
        avito_ads_account_id: qs("newClientAvitoAccountId")?.value || "",
        avito_ads_client_id: qs("newClientAvitoClientId")?.value || "",
        avito_ads_client_secret: qs("newClientAvitoClientSecret")?.value || "",
      } : marketplace === "lamoda" ? {
        lamoda_client_id: qs("newClientLamodaClientId")?.value || "",
        lamoda_client_secret: qs("newClientLamodaClientSecret")?.value || "",
      } : {
        yandex_market_api_key: qs("newClientYandexApiKey")?.value || "",
      },
  };
  button.disabled = true;
  try {
    const result = await postJson("/api/admin/client-onboarding/start", payload);
    state.adminOnboardingClientKey = result.client.key;
    state.adminOnboardingDraftName = "";
    mergeOnboardingClient(result.client);
    renderAdminDashboardContent(lastAdminPayload);
    pollAdminClientOnboarding(result.client.key).catch((error) => {
      setStatusError(error);
    });
  } finally {
    button.disabled = false;
  }
}

async function retryAdminClientOnboarding(button) {
  const key = button.dataset.retryClientOnboarding || "";
  const client = (state.adminClientRegistry?.clients || []).find((item) => item.key === key);
  if (!client) throw new Error("Клиент для повторного запуска не найден");
  button.disabled = true;
  try {
    const result = await postJson("/api/admin/client-onboarding/start", {
      label: client.label,
      key: client.key,
      db_name: client.db_name || client.key,
      marketplaces: client.marketplaces || [],
      reports: client.reports || [],
      credentials: {},
    });
    state.adminOnboardingClientKey = result.client.key;
    mergeOnboardingClient(result.client);
    renderAdminDashboardContent(lastAdminPayload);
    pollAdminClientOnboarding(result.client.key).catch((error) => {
      setStatusError(error);
    });
  } finally {
    button.disabled = false;
  }
}

function inferClientHistoryCompletedStepCount(progress, stepTotal) {
  if (!stepTotal) return 0;
  const value = Math.max(0, Math.min(99, Number.parseInt(progress || 0, 10) || 0));
  if (value <= 5) return 0;
  return Math.max(0, Math.min(stepTotal - 1, Math.floor(((value - 5) / 90) * stepTotal)));
}

function resumeAdminClientHistorySteps(client) {
  if (!client) return [];
  const sourceSteps = client.operation_steps?.length
    ? client.operation_steps
    : fallbackHistorySteps({...client, operation_status: "idle"});
  const historySteps = historyStepsForMarketplace(client, sourceSteps);
  const ordered = historySteps.map((step) => step.key).filter(Boolean);
  if (historySelectedMarketplace(client) === "yandex_market") {
    return ordered.length ? ordered : scopedAdminClientHistorySteps(client);
  }
  if (!ordered.length) return [];
  const runtimeSteps = Array.isArray(client.operation_steps)
    ? client.operation_steps.filter((step) => ordered.includes(step.key))
    : [];
  if (runtimeSteps.length) {
    const doneKeys = new Set(runtimeSteps.filter((step) => step.status === "done").map((step) => step.key));
    return ordered.filter((key) => !doneKeys.has(key));
  }
  const completed = inferClientHistoryCompletedStepCount(client.operation_progress, ordered.length);
  return ordered.slice(completed);
}

function scopedAdminClientHistorySteps(client) {
  if (!client) return [];
  // A completed or stopped run preserves only its own selected steps.  The
  // "load all" action must always rebuild the full plan for this marketplace.
  const sourceSteps = fallbackHistorySteps({...client, operation_status: "idle"});
  return historyStepsForMarketplace(client, sourceSteps).map((step) => step.key).filter(Boolean);
}

function canResumeAdminClientHistory(client, resumeSteps = null) {
  if (client?.operation === "history" && historySelectedMarketplace(client) === "yandex_market") {
    return ["failed", "stopped", "partial"].includes(client.operation_status);
  }
  return Boolean(
    client?.operation === "history"
      && ["failed", "stopped"].includes(client.operation_status)
      && (resumeSteps || resumeAdminClientHistorySteps(client)).length
  );
}

function buildAdminClientHistoryStartRequest(button) {
  const client = qs("[data-client-history-client]")?.value || state.adminOnboardingClientKey;
  if (!client) throw new Error("Клиент не выбран");
  const loadAll = button.hasAttribute("data-start-client-history-all");
  const resume = button.hasAttribute("data-resume-client-history");
  const overwrite = Boolean(qs("clientHistoryOverwrite")?.checked);
  const includeInactiveCampaigns = Boolean(qs("clientHistoryIncludeInactiveCampaigns")?.checked);
  if (resume && overwrite) throw new Error("Для перезаписи запустите новую загрузку, а не продолжение остановленной");
  const selectedClient = (state.adminClientRegistry?.clients || []).find((item) => item.key === client);
  const steps = resume
    ? resumeAdminClientHistorySteps(selectedClient)
    : (loadAll
      ? scopedAdminClientHistorySteps(selectedClient)
      : [...document.querySelectorAll('[name="clientHistoryStep"]:checked')].map((input) => input.value));
  if (resume && !resumeAdminClientHistorySteps(selectedClient).length) throw new Error("Нет оставшихся разделов для продолжения");
  if (!resume && !loadAll && !steps.length) throw new Error("Выберите хотя бы один раздел для загрузки");
  const availableSteps = resume && selectedClient?.operation_steps?.length
    ? selectedClient.operation_steps
    : fallbackHistorySteps({...selectedClient, operation_status: "idle"});
  const stepLabels = historyStepsForMarketplace(selectedClient, availableSteps)
    .filter((step) => steps.includes(step.key))
    .map((step) => step.label || step.key);
  const marketplace = historySelectedMarketplace(selectedClient);
  const includesWbStockHistory = marketplace === "wb" && steps.includes("wb_stock_history");
  const wbStockHistoryDateFrom = includesWbStockHistory ? (qs("clientHistoryWbStockDateFrom")?.value || "") : "";
  const wbStockHistoryDateTo = includesWbStockHistory ? (qs("clientHistoryWbStockDateTo")?.value || "") : "";
  if (includesWbStockHistory && (!wbStockHistoryDateFrom || !wbStockHistoryDateTo)) {
    throw new Error("Выберите отдельный период истории остатков WB");
  }
  if (includesWbStockHistory && wbStockHistoryDateFrom > wbStockHistoryDateTo) {
    throw new Error("В периоде истории остатков WB дата начала позже даты окончания");
  }
  return {
    client,
    clientLabel: selectedClient?.label || client,
    marketplace,
    dateFrom: qs("clientHistoryDateFrom")?.value || "",
    dateTo: qs("clientHistoryDateTo")?.value || "",
    wbStockHistoryDateFrom,
    wbStockHistoryDateTo,
    steps,
    stepLabels,
    loadAll,
    resume,
    overwrite,
    includeInactiveCampaigns,
  };
}

function historyStartConfirmationMarkup(request) {
  if (!request) return "";
  const actionLabel = request.resume ? "Продолжить загрузку" : (request.loadAll ? "Загрузить всё" : "Загрузить выбранное");
  const modeLabel = request.overwrite
    ? "Перезапись API-данных за выбранный период"
    : (request.resume ? "Продолжение с незавершённого этапа" : "Обычная загрузка без удаления данных");
  const sections = request.stepLabels.length ? request.stepLabels.join(", ") : "Нет разделов";
  const wbStockHistoryPeriod = request.marketplace === "wb" && request.steps.includes("wb_stock_history") ? `
          <div><dt>История остатков</dt><dd>${escapeHtml(formatRuDate(request.wbStockHistoryDateFrom))} - ${escapeHtml(formatRuDate(request.wbStockHistoryDateTo))}</dd></div>` : "";
  const overwriteWarning = request.overwrite ? `
    <p class="client-history-start-confirmation-warning" role="alert">
      Перезапись включена: API-данные по выбранным разделам за период ${escapeHtml(formatRuDate(request.dateFrom))} - ${escapeHtml(formatRuDate(request.dateTo))} будут заменены. Ручные файлы, настройки и данные вне периода сохранятся.
    </p>` : "";
  const inactiveCampaignsWarning = request.includeInactiveCampaigns ? `
    <p class="client-history-start-confirmation-warning" role="alert">
      Будут запрошены поисковые фразы не только активных, но и завершённых и приостановленных WB-кампаний за выбранный период.
    </p>` : "";
  return `
    <div class="client-history-start-confirmation" role="presentation">
      <section class="client-history-start-confirmation-card" role="dialog" aria-modal="true" aria-labelledby="clientHistoryStartConfirmationTitle">
        <header>
          <div>
            <h3 id="clientHistoryStartConfirmationTitle">Подтвердите запуск</h3>
            <span>${escapeHtml(actionLabel)}</span>
          </div>
        </header>
        <dl class="client-history-start-confirmation-params">
          <div><dt>Клиент</dt><dd>${escapeHtml(request.clientLabel)}</dd></div>
          <div><dt>Площадка</dt><dd>${escapeHtml((request.marketplace || "").toUpperCase())}</dd></div>
          <div><dt>Период</dt><dd>${escapeHtml(formatRuDate(request.dateFrom))} - ${escapeHtml(formatRuDate(request.dateTo))}</dd></div>
          ${wbStockHistoryPeriod}
          <div><dt>Режим</dt><dd>${escapeHtml(modeLabel)}</dd></div>
          <div class="is-wide"><dt>Разделы</dt><dd>${escapeHtml(sections)}</dd></div>
        </dl>
        ${overwriteWarning}
        ${inactiveCampaignsWarning}
        <p class="client-history-start-confirmation-note">${request.overwrite ? "После подтверждения загрузка начнётся с заменой данных выбранного периода." : "API-данные будут добавлены или обновлены по ключам. Для полного удаления и повторной загрузки используйте «Перезаписать»."}</p>
        <footer>
          <button type="button" class="client-history-run-button secondary" data-cancel-client-history-start>Отмена</button>
          <button type="button" class="client-history-run-button primary" data-confirm-client-history-start>${adminActionIcon("historyDownload")}<span>${escapeHtml(actionLabel)}</span></button>
        </footer>
      </section>
    </div>`;
}

async function startAdminClientHistory(request, button = null) {
  if (button) button.disabled = true;
  try {
    const result = await postJson("/api/admin/client-onboarding/history", {
      client: request.client,
      marketplace: request.marketplace,
      date_from: request.dateFrom,
      date_to: request.dateTo,
      wb_stock_history_date_from: request.wbStockHistoryDateFrom,
      wb_stock_history_date_to: request.wbStockHistoryDateTo,
      steps: request.steps,
      load_all: request.loadAll,
      resume: request.resume,
      overwrite: request.overwrite,
      include_inactive_campaigns: request.includeInactiveCampaigns,
    });
    state.adminHistoryStartConfirmation = null;
    state.adminHistoryInspectionKey = "";
    mergeOnboardingClient(result.client);
    renderAdminDashboardContent(lastAdminPayload);
    pollAdminClientOnboarding(request.client).catch((error) => {
      setStatusError(error);
    });
  } finally {
    if (button) button.disabled = false;
  }
}

async function requestAdminClientHistoryStart(button) {
  const request = buildAdminClientHistoryStartRequest(button);
  state.adminHistoryStartConfirmation = request;
  renderAdminDashboardContent(lastAdminPayload);
}

async function stopAdminClientHistory(button) {
  const client = state.adminOnboardingClientKey;
  if (!client) throw new Error("Клиент не выбран");
  button.disabled = true;
  const result = await postJson("/api/admin/client-onboarding/history/stop", {client});
  mergeOnboardingClient(result.client);
  renderAdminDashboardContent(lastAdminPayload);
  window.requestAnimationFrame(scrollClientHistoryTerminal);
}

function scrollClientHistoryTerminal() {
  const terminal = qs("clientHistoryTerminal");
  if (terminal) terminal.scrollTop = terminal.scrollHeight;
}

function openAdminClientHistory(clientKey) {
  const client = (state.adminClientRegistry?.clients || []).find((row) => row.key === clientKey);
  if (!client || client.status !== "active") return;
  state.adminOnboardingClientKey = client.key;
  state.adminHistoryMarketplace = historySelectedMarketplace(client);
  state.adminImportMode = "clientOnboarding";
  persistDashboardState();
  renderAdminDashboardContent(lastAdminPayload);
  window.requestAnimationFrame(() => {
    qs("clientHistoryPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  if (["wb", "avito", "yandex_market"].includes(state.adminHistoryMarketplace)) {
    loadAdminClientHistoryInspection().catch((error) => setStatusError(error));
  }
  if (client.operation_status === "running") {
    pollAdminClientOnboarding(client.key).catch((error) => {
      setStatusError(error);
    });
  } else if (client.operation === "history") {
    getJson(`/api/admin/client-onboarding?client=${encodeURIComponent(client.key)}`).then((result) => {
      mergeOnboardingClient(result.client);
      renderAdminDashboardContent(lastAdminPayload);
      window.requestAnimationFrame(scrollClientHistoryTerminal);
    }).catch((error) => {
      setStatusError(error);
    });
  }
}

function closeAdminClientHistory() {
  state.adminOnboardingPollToken += 1;
  state.adminOnboardingClientKey = "";
  persistDashboardState();
  renderAdminDashboardContent(lastAdminPayload);
}

async function loadAdminClientHistoryInspection({force = false} = {}) {
  const client = (state.adminClientRegistry?.clients || []).find((row) => row.key === state.adminOnboardingClientKey);
  const marketplace = client ? historySelectedMarketplace(client) : "";
  if (!client || !["wb", "avito", "yandex_market"].includes(marketplace)) return;
  const dateFrom = qs("clientHistoryDateFrom")?.value || client.history_date_from || "";
  const dateTo = qs("clientHistoryDateTo")?.value || client.history_date_to || "";
  const cacheKey = [client.key, marketplace, dateFrom, dateTo].join("|");
  if (!force && state.adminHistoryInspectionKey === cacheKey && state.adminHistoryInspection) return;
  if (state.adminHistoryInspectionLoading) return;
  state.adminHistoryInspectionLoading = true;
  state.adminHistoryInspectionError = "";
  renderAdminDashboardContent(lastAdminPayload);
  try {
    const params = new URLSearchParams({client: client.key, marketplace});
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const result = await getJson(`/api/admin/client-onboarding/inspection?${params}`);
    state.adminHistoryInspection = result;
    state.adminHistoryInspectionKey = cacheKey;
  } catch (error) {
    state.adminHistoryInspectionError = error?.message || String(error);
    throw error;
  } finally {
    state.adminHistoryInspectionLoading = false;
    renderAdminDashboardContent(lastAdminPayload);
  }
}


function historyOperationStatus(client) {
  const statuses = {
    running: client.operation_stop_requested ? "Останавливается" : "Выполняется",
    completed: "Завершено",
    partial: "Завершено с ограничениями",
    failed: "Ошибка",
    stopped: "Остановлено",
  };
  return statuses[client.operation_status] || "Готов к запуску";
}

function historyDuration(startedAt, finishedAt = "") {
  const start = Date.parse(startedAt || "");
  if (!Number.isFinite(start)) return "—";
  const finish = Date.parse(finishedAt || "") || Date.now();
  const total = Math.max(0, Math.floor((finish - start) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].filter((_, index) => index > 0 || hours > 0).map((value) => String(value).padStart(2, "0")).join(":");
}

function fallbackHistorySteps(client) {
  const marketplaces = new Set(client.marketplaces || []);
  const steps = [];
  if (marketplaces.has("ozon")) {
    steps.push(
      {key: "ozon_assortment", label: "Ozon · Ассортимент и характеристики", script: "sync_ozon_assortment.py"},
      {
        key: "ozon_funnel",
        label: "Ozon · Воронка и продажи",
        script: "sync_km_ozon_api.py --step funnel",
        description: "Автовыбор Standard или Advanced по фактическому ответу Seller API",
        substeps: [
          "Проверка Premium и доступного контракта",
          "Standard: заказы и выручка за доступные 3 месяца",
          "Advanced: показы, сессии, корзины, заказы и доставки по SKU × день",
          "Checkpoint и автоматическая пересборка витрин BI",
        ],
      },
      {key: "ozon_stock", label: "Ozon · Остатки", script: "sync_km_ozon_api.py --step stock"},
      {
        key: "ozon_advertising",
        label: "Ozon · Реклама по типам кампаний",
        script: "sync_km_ozon_api.py --step advertising",
        description: "Раздельные Performance API отчёты по реальному grain",
        substeps: [
          "Список и типы кампаний за период",
          "CPC / product: товарная статистика и корзины",
          "CPO / all-SKU promo: отдельный отчёт заказов",
          "Campaign daily: контрольные итоги и сверка",
        ],
      },
      {key: "ozon_finance", label: "Ozon · Финансы и цены", script: "sync_km_ozon_finance.py --source api"},
      {key: "ozon_views", label: "Ozon · Витрины BI", script: "sync_km_ozon_api.py --step views"},
    );
  }
  if (marketplaces.has("wb")) {
    steps.push(
      {key: "wb_catalog", label: "WB · Ассортимент и характеристики", script: "sync_km_wb_extended_api.py --step content", description: "Карточки товаров, бренды, категории, размеры и характеристики из Content API."},
      {key: "wb_orders_sales", label: "WB · Заказы и продажи", script: "sync_km_wb_extended_api.py --step statistics", description: "Заказы, продажи, возвраты и отмены из Statistics API за доступный WB период."},
      {key: "wb_stock_current", label: "WB · Текущие остатки", script: "sync_km_wb_api.py --step stock", description: "Текущий снимок остатков по товарам и складам."},
      {key: "wb_stock_history", label: "WB · История остатков", script: "sync_km_wb_extended_api.py --step stock_history", description: "Дневная история остатков за выбранный период через Seller Analytics CSV."},
      {key: "wb_funnel", label: "WB · Воронка продаж", script: "sync_km_wb_api.py --step funnel", description: "Показы карточек, корзины, заказы и выкупы; прямой API отдаёт максимум последние 7 дней."},
      {key: "wb_advertising", label: "WB · Рекламные кампании", script: "sync_km_wb_extended_api.py --step promotion", description: "Кампании, общая статистика и дневные поисковые фразы: показы, клики, расходы, корзины, заказы и позиции."},
      {
        key: "wb_search",
        label: "WB · Поисковая аналитика",
        script: "sync_km_wb_extended_api.py --step analytics",
        description: "Сводная видимость, позиции, переходы и товарная детализация за последние 7 дней; требуется подписка WB Jam.",
        substeps: [
          "POST /api/v2/search-report/report · сводный поисковый отчёт",
          "POST /api/v2/search-report/table/details · детализация по товарам",
        ],
      },
      {
        key: "wb_feedbacks_questions",
        label: "WB · Отзывы и вопросы",
        script: "sync_km_wb_extended_api.py --step communication",
        description: "Отзывы, ответы продавца и вопросы покупателей за выбранный период; требуется категория токена «Вопросы и отзывы».",
        substeps: [
          "GET /api/v1/new-feedbacks-questions · проверка новых событий",
          "GET /api/v1/feedbacks · обработанные и необработанные отзывы",
          "GET /api/v1/feedbacks/archive · архив обработанных отзывов",
          "GET /api/v1/questions · отвеченные и неотвеченные вопросы",
        ],
      },
      {key: "wb_finance", label: "WB · Финансовые отчёты", script: "sync_km_wb_extended_api.py --step finance --finance-period daily", description: "Дневные отчёты для ежедневной догрузки; недельные закрывающие отчёты запускаются отдельно."},
      {key: "wb_views", label: "WB · Витрины BI", script: "sync_km_wb_api.py --step views", description: "Пересборка зависимых таблиц и materialized views после загрузки данных."},
    );
  }
  if (marketplaces.has("avito")) {
    steps.push({
      key: "avito_advertising",
      label: "Avito · Рекламная статистика",
      script: "sync_avito_ads.py",
      description: "Кампании, группы, объявления, баланс и дневная рекламная статистика за выбранный период.",
      substeps: [
        "Аккаунт · GET /ads/v1/account/{accountID}",
        "Баланс · GET /ads/v1/account/{accountID}/balance",
        "Кампании · POST /ads/v1/account/{accountID}/campaigns",
        "Группы · POST /ads/v1/account/{accountID}/groups",
        "Объявления · POST /ads/v1/account/{accountID}/creatives",
        "Дневная статистика · POST /campaigns/{campaignID}/stats · окна до 100 дней",
      ],
    });
  }
  if (marketplaces.has("yandex_market")) {
    steps.push(
      {key: "yandex_orders", label: "Яндекс · Заказы", script: "sync_yandex_market.py --step yandex_orders", description: "По дате создания: статусы, товары, количество, цены и скидки."},
      {key: "yandex_order_stats", label: "Яндекс · Детализация заказов", script: "sync_yandex_market.py --step yandex_order_stats", description: "По дате создания заказа: товарные позиции, платежи, комиссии, субсидии. Не полный финансовый отчёт."},
      {key: "yandex_returns", label: "Яндекс · Невыкупы и возвраты", script: "sync_yandex_market.py --step yandex_returns", description: "По дате обновления: связь с заказом, товары, статусы денег и логистики."},
    );
  }
  return steps.map((step, index) => ({...step, status: index === 0 && client.operation_status === "running" ? "running" : "pending"}));
}

function historyMarketplacesForClient(client) {
  const items = Array.isArray(client?.marketplaces) ? client.marketplaces : [];
  return ["ozon", "wb", "avito", "yandex_market"].filter((marketplace) => items.includes(marketplace));
}

function historySelectedMarketplace(client) {
  const marketplaces = historyMarketplacesForClient(client);
  if (!marketplaces.length) return "";
  const runningHistory = client?.operation === "history" && client?.operation_status === "running";
  const runningMarketplaces = runningHistory && Array.isArray(client?.operation_steps)
    ? [...new Set(client.operation_steps.map(historyStepMarketplace).filter(Boolean))]
    : [];
  const pageMarketplace = qs("marketplace")?.value;
  const selected = runningMarketplaces.length === 1 && marketplaces.includes(runningMarketplaces[0])
    ? runningMarketplaces[0]
    : (marketplaces.includes(state.adminHistoryMarketplace)
      ? state.adminHistoryMarketplace
      : (marketplaces.includes(pageMarketplace) ? pageMarketplace : marketplaces[0]));
  state.adminHistoryMarketplace = selected;
  return selected;
}

function historyStepMarketplace(step) {
  const value = `${step?.key || ""} ${step?.label || ""} ${step?.script || ""} ${step?.command || ""}`.toLowerCase();
  if (/\bwb\b|wildberries|вайлдбер/.test(value)) return "wb";
  if (/\bozon\b|озон/.test(value)) return "ozon";
  if (/\bavito\b|авито/.test(value)) return "avito";
  if (/yandex|яндекс/.test(value)) return "yandex_market";
  return "";
}

function historyStepsForMarketplace(client, steps) {
  const marketplace = historySelectedMarketplace(client);
  if (!marketplace) return steps;
  return steps.filter((step) => {
    const stepMarketplace = historyStepMarketplace(step);
    return !stepMarketplace || stepMarketplace === marketplace;
  });
}

function historyStepShortLabel(step) {
  return String(step?.label || "Этап").replace(/^\s*(Ozon|WB|Wildberries|Avito|Яндекс)\s*·\s*/i, "");
}

function historyStepDetailsMarkup(step, compact = false) {
  const description = String(step?.description || "").trim();
  const substeps = Array.isArray(step?.substeps) ? step.substeps.filter(Boolean) : [];
  if (!description && !substeps.length) return "";
  return `<div class="client-history-step-details ${compact ? "is-compact" : ""}">
    ${description ? `<small>${escapeHtml(description)}</small>` : ""}
    ${substeps.length ? `<ol>${substeps.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>` : ""}
  </div>`;
}

const avitoHistoryMethodStages = [
  {key: "avito_account", label: "Аккаунт", script: "GET /ads/v1/account/{accountID}", description: "Карточка рекламного кабинета и параметры аккаунта."},
  {key: "avito_balance", label: "Баланс", script: "GET /ads/v1/account/{accountID}/balance", description: "Основной и бонусный баланс на дату снимка."},
  {key: "avito_campaigns", label: "Кампании", script: "POST /ads/v1/account/{accountID}/campaigns", description: "Список кампаний, статусы, типы, модели оплаты и бюджеты."},
  {key: "avito_groups", label: "Группы", script: "POST /ads/v1/account/{accountID}/groups", description: "Группы объявлений, связи с кампаниями, статусы, цены и бюджеты."},
  {key: "avito_creatives", label: "Объявления", script: "POST /ads/v1/account/{accountID}/creatives", description: "Объявления, связи с группами и кампаниями, статусы и preview."},
  {key: "avito_advertising", label: "Дневная рекламная статистика", script: "POST /campaigns/{campaignID}/stats", description: "Показы, клики, расходы и видео-метрики; окна не более 100 дней."},
];

function expandAvitoHistoryMethodSteps(steps, logs, operationStatus) {
  const markers = new Map();
  (logs || []).forEach((line) => {
    const text = String(line?.text || "");
    const match = text.match(/AVITO-МЕТОД\s+\d+\/\d+\s*\|\s*key=(avito_[a-z_]+).*?\|\s*status=(running|done|failed)(?:\s*\|\s*rows=(\d+))?/i);
    if (!match) return;
    markers.set(match[1].toLowerCase(), {status: match[2].toLowerCase(), rows: match[3] === undefined ? null : Number(match[3])});
  });
  return (steps || []).flatMap((step) => {
    if (step.key !== "avito_advertising") return [step];
    const parentStatus = operationStatus === "completed" ? "done"
      : (["failed", "stopped"].includes(operationStatus) ? operationStatus : (step.status || "pending"));
    const hasMarkers = markers.size > 0;
    const latestMethodIndex = avitoHistoryMethodStages.reduce(
      (latest, method, index) => markers.has(method.key) ? Math.max(latest, index) : latest,
      -1,
    );
    return avitoHistoryMethodStages.map((method, index) => {
      const marker = markers.get(method.key);
      let status = marker?.status || "pending";
      if (!marker && index < latestMethodIndex) status = "done";
      if (parentStatus === "done") status = "done";
      if (!hasMarkers && parentStatus === "running" && index === 0) status = "running";
      if (["failed", "stopped"].includes(parentStatus) && (status === "running" || (!hasMarkers && index === 0))) status = parentStatus;
      return {
        ...method,
        status,
        rows: marker?.rows,
        executable_key: step.key,
        virtual: true,
      };
    });
  });
}

function historyStepIcon(step) {
  const key = String(step?.key || step?.label || "").toLowerCase();
  if (key.includes("assort") || key.includes("характер")) return "assortment";
  if (key.includes("funnel") || key.includes("ворон") || key.includes("orders_sales")) return "funnelStep";
  if (key.includes("stock") || key.includes("остат")) return "stock";
  if (key.includes("advert") || key.includes("реклам")) return "advertising";
  if (key.includes("finance") || key.includes("финанс")) return "finance";
  if (key.includes("views") || key.includes("витрин")) return "views";
  return "marketplace";
}

function renderClientHistoryMarketplaceSwitch(client, selectedMarketplace, disabled) {
  const connected = new Set(historyMarketplacesForClient(client));
  const marketplaces = ["ozon", "wb", "avito", "yandex_market"];
  const labels = {ozon: "Ozon", wb: "WB", avito: "Avito", yandex_market: "Яндекс"};
  const icons = {ozon: "OZ", wb: "WB", avito: "A", yandex_market: "Я"};
  const selected = selectedMarketplace || historySelectedMarketplace(client) || "ozon";
  return `
    <div class="client-history-marketplace-switch" role="tablist" aria-label="Маркетплейс истории">
      ${marketplaces.map((marketplace) => {
        const label = labels[marketplace] || marketplace.toUpperCase();
        const isConnected = connected.has(marketplace);
        const isActive = marketplace === selected;
        const title = isConnected ? `Пайплайн ${label}` : `${label}: не подключен клиенту`;
        return `<button type="button" class="client-history-marketplace-tab ${isActive ? "is-active" : ""} ${isConnected ? "" : "is-disabled"}" data-client-history-marketplace="${escapeHtml(marketplace)}" aria-pressed="${isActive ? "true" : "false"}" title="${escapeHtml(title)}" ${disabled || !isConnected ? "disabled" : ""}><i>${escapeHtml(icons[marketplace] || marketplace.toUpperCase())}</i><span>${escapeHtml(label)}</span></button>`;
      }).join("")}
    </div>
  `;
}

function renderClientHistoryClientSelector(registry, selected) {
  const clients = (registry?.clients || []).filter((client) => client.status === "active");
  if (!clients.length) return "";
  return `
    <label class="client-history-client-select">Клиент
      <select data-client-history-client aria-label="Клиент для исторической загрузки">
        ${clients.map((client) => `<option value="${escapeHtml(client.key)}" ${client.key === selected?.key ? "selected" : ""}>${escapeHtml(client.label || client.key)}</option>`).join("")}
      </select>
    </label>
  `;
}

const clientHistoryCalendarStates = new WeakMap();

function clientHistoryRangeState(range) {
  if (!clientHistoryCalendarStates.has(range)) {
    const fromInputId = range.dataset.clientHistoryRangeFrom || "clientHistoryDateFrom";
    const toInputId = range.dataset.clientHistoryRangeTo || "clientHistoryDateTo";
    const min = range.dataset.clientHistoryRangeMin || "";
    const max = range.dataset.clientHistoryRangeMax || range.dataset.clientHistoryMax || "";
    const from = range.querySelector(`[id="${fromInputId}"]`)?.value || "";
    const to = range.querySelector(`[id="${toInputId}"]`)?.value || "";
    const reference = parseIsoDate(from || to || max) || new Date();
    clientHistoryCalendarStates.set(range, {
      from,
      to,
      min,
      max,
      step: from && to ? 2 : (from ? 1 : 0),
      leftMonth: new Date(reference.getFullYear(), reference.getMonth(), 1),
    });
  }
  return clientHistoryCalendarStates.get(range);
}

function clientHistoryCalendarMonth(date, side, calendarState) {
  const year = date.getFullYear();
  const month = date.getMonth();
  const monthOptions = monthNames.map((name, index) => `<option value="${index}" ${index === month ? "selected" : ""}>${name}</option>`).join("");
  const yearOptions = Array.from({ length: 9 }, (_, index) => year - 4 + index).map((item) => `<option value="${item}" ${item === year ? "selected" : ""}>${item}</option>`).join("");
  const firstDay = (new Date(year, month, 1).getDay() + 6) % 7;
  const cells = Array.from({ length: firstDay }, () => '<span class="calendar-empty"></span>');
  for (let day = 1; day <= daysInMonth(year, month); day += 1) {
    const iso = toIsoDate(new Date(year, month, day));
    const unavailable = Boolean((calendarState.min && iso < calendarState.min) || (calendarState.max && iso > calendarState.max));
    const inRange = calendarState.from && calendarState.to && iso >= calendarState.from && iso <= calendarState.to;
    const edge = iso === calendarState.from || iso === calendarState.to;
    cells.push(`<button class="calendar-day ${inRange ? "in-range" : ""} ${edge ? "range-edge" : ""}" type="button" data-client-history-calendar-date="${iso}" ${unavailable ? "disabled" : ""}>${day}</button>`);
  }
  return `<div class="calendar-month-panel"><div class="calendar-head"><select data-client-history-calendar-month data-side="${side}">${monthOptions}</select><select data-client-history-calendar-year data-side="${side}">${yearOptions}</select></div><div class="calendar-weekdays"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Вс</span></div><div class="calendar-grid">${cells.join("")}</div></div>`;
}

function renderClientHistoryCalendar(range) {
  const calendarState = clientHistoryRangeState(range);
  const menu = range.querySelector("[data-client-history-range-menu]");
  menu.innerHTML = `<div class="date-presets"><button type="button" data-client-history-calendar-preset="yesterday">Вчера</button><button type="button" data-client-history-calendar-preset="week">7 дней</button><button type="button" data-client-history-calendar-preset="days28">28 дней</button><button type="button" data-client-history-calendar-preset="days90">90 дней</button><button type="button" data-client-history-calendar-preset="month">Этот месяц</button><button type="button" data-client-history-calendar-preset="prev_month">Прошлый месяц</button></div><div class="calendar-panels">${clientHistoryCalendarMonth(calendarState.leftMonth, "left", calendarState)}${clientHistoryCalendarMonth(addMonths(calendarState.leftMonth, 1), "right", calendarState)}</div><div class="date-range-actions"><span>${calendarState.from && calendarState.to ? `${formatRuDate(calendarState.from)} - ${formatRuDate(calendarState.to)}` : "Выберите дату начала и дату окончания"}</span><button type="button" data-client-history-calendar-apply ${calendarState.from && calendarState.to ? "" : "disabled"}>Применить</button></div>`;
}

function setClientHistoryCalendarPreset(range, preset) {
  const calendarState = clientHistoryRangeState(range);
  const referenceIso = calendarState.max || shiftDate(toIsoDate(new Date()), -1);
  const reference = parseIsoDate(referenceIso);
  if (preset === "yesterday") [calendarState.from, calendarState.to] = [referenceIso, referenceIso];
  if (preset === "week") [calendarState.from, calendarState.to] = [shiftDate(referenceIso, -6), referenceIso];
  if (preset === "days28") [calendarState.from, calendarState.to] = [shiftDate(referenceIso, -27), referenceIso];
  if (preset === "days90") [calendarState.from, calendarState.to] = [shiftDate(referenceIso, -89), referenceIso];
  if (preset === "month") [calendarState.from, calendarState.to] = [toIsoDate(new Date(reference.getFullYear(), reference.getMonth(), 1)), referenceIso];
  if (preset === "prev_month") [calendarState.from, calendarState.to] = [toIsoDate(new Date(reference.getFullYear(), reference.getMonth() - 1, 1)), toIsoDate(new Date(reference.getFullYear(), reference.getMonth(), 0))];
  if (calendarState.min && calendarState.from < calendarState.min) calendarState.from = calendarState.min;
  if (calendarState.max && calendarState.to > calendarState.max) calendarState.to = calendarState.max;
  if (calendarState.min && calendarState.to < calendarState.min) [calendarState.from, calendarState.to] = [calendarState.min, calendarState.min];
  if (calendarState.max && calendarState.from > calendarState.max) [calendarState.from, calendarState.to] = [calendarState.max, calendarState.max];
  calendarState.step = 2;
  calendarState.leftMonth = new Date(parseIsoDate(calendarState.from).getFullYear(), parseIsoDate(calendarState.from).getMonth(), 1);
  renderClientHistoryCalendar(range);
}

function handleClientHistoryCalendarClick(event) {
  const range = event.target.closest("[data-client-history-range]");
  document.querySelectorAll("[data-client-history-range-menu]").forEach((menu) => {
    if (!range || menu !== range.querySelector("[data-client-history-range-menu]")) menu.classList.add("hidden");
  });
  if (!range) return;
  const menu = range.querySelector("[data-client-history-range-menu]");
  if (event.target.closest("[data-client-history-range-toggle]")) {
    event.preventDefault();
    renderClientHistoryCalendar(range);
    menu.classList.toggle("hidden");
    return;
  }
  const preset = event.target.closest("[data-client-history-calendar-preset]");
  if (preset) {
    setClientHistoryCalendarPreset(range, preset.dataset.clientHistoryCalendarPreset);
    return;
  }
  const day = event.target.closest("[data-client-history-calendar-date]");
  if (day) {
    const calendarState = clientHistoryRangeState(range);
    const iso = day.dataset.clientHistoryCalendarDate;
    if (calendarState.step !== 1) {
      calendarState.from = iso;
      calendarState.to = "";
      calendarState.step = 1;
    } else {
      if (iso < calendarState.from) [calendarState.from, calendarState.to] = [iso, calendarState.from];
      else calendarState.to = iso;
      calendarState.step = 2;
    }
    renderClientHistoryCalendar(range);
    return;
  }
  if (event.target.closest("[data-client-history-calendar-apply]")) {
    const calendarState = clientHistoryRangeState(range);
    if (!calendarState.from || !calendarState.to) return;
    const fromInputId = range.dataset.clientHistoryRangeFrom || "clientHistoryDateFrom";
    const toInputId = range.dataset.clientHistoryRangeTo || "clientHistoryDateTo";
    range.querySelector(`[id="${fromInputId}"]`).value = calendarState.from;
    range.querySelector(`[id="${toInputId}"]`).value = calendarState.to;
    range.querySelector("[data-client-history-range-toggle]").textContent = `${formatRuDate(calendarState.from)} - ${formatRuDate(calendarState.to)}`;
    menu.classList.add("hidden");
    if (range.dataset.clientHistoryRangeKind === "wb-stock-history") {
      rememberWbStockHistoryRange();
      persistDashboardState();
    } else if (state.dashboard === "admin" && ["wb", "avito", "yandex_market"].includes(state.adminHistoryMarketplace)) {
      const selected = (state.adminClientRegistry?.clients || []).find((row) => row.key === state.adminOnboardingClientKey);
      if (selected) {
        selected.history_date_from = calendarState.from;
        selected.history_date_to = calendarState.to;
      }
      state.adminHistoryInspectionKey = "";
      window.setTimeout(() => loadAdminClientHistoryInspection({force: true}).catch((error) => setStatusError(error)), 0);
    }
  }
}

function handleClientHistoryCalendarChange(event) {
  const select = event.target.closest("[data-client-history-calendar-month], [data-client-history-calendar-year]");
  if (!select) return;
  const range = select.closest("[data-client-history-range]");
  const calendarState = clientHistoryRangeState(range);
  const side = select.dataset.side;
  const panel = select.closest(".calendar-month-panel");
  const month = Number(panel.querySelector("[data-client-history-calendar-month]").value);
  const year = Number(panel.querySelector("[data-client-history-calendar-year]").value);
  const picked = new Date(year, month, 1);
  calendarState.leftMonth = side === "right" ? addMonths(picked, -1) : picked;
  renderClientHistoryCalendar(range);
}

document.addEventListener("click", handleClientHistoryCalendarClick);
document.addEventListener("change", handleClientHistoryCalendarChange);

const historyProgressMemory = new Map();

function historyProgressSnapshot(client, steps, progress) {
  const logs = client.operation_logs || [];
  const sources = client.operation_message
    ? [...logs, {text: client.operation_message}]
    : logs;
  const checkpoint = {page: null, rows: null, requests: null};
  let liveStepPercent = null;
  let liveText = "";
  let currentDate = "";
  let eta = "";
  let pauseRemaining = "";
  let speed = "";
  let elapsed = "";
  for (let index = sources.length - 1; index >= 0; index -= 1) {
    const text = String(sources[index]?.text || "");
    if (!liveText && text.startsWith("ПРОГРЕСС:")) liveText = text;
    if (liveStepPercent === null) {
      const liveMatch = text.match(/\bstep_pct=(\d{1,3}(?:\.\d+)?)\b/);
      if (liveMatch) liveStepPercent = Math.max(0, Math.min(100, Number(liveMatch[1])));
    }
    const pageMatch = checkpoint.page === null ? text.match(/страница\s+(\d+)/i) : null;
    const rowsMatch = checkpoint.rows === null ? text.match(/(?:обработано за запуск|загружено|сохранено строк)\s+([\d\s,.]+)/i) : null;
    const requestsMatch = checkpoint.requests === null ? text.match(/запросов\s+(\d+)/i) : null;
    if (pageMatch) checkpoint.page = Number(pageMatch[1] || 0);
    if (rowsMatch) checkpoint.rows = Number(String(rowsMatch[1] || "").replace(/\D/g, ""));
    if (requestsMatch) checkpoint.requests = Number(requestsMatch[1] || 0);
    if (!currentDate) {
      const dateRangeMatch = text.match(/текущие даты\s+(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})/i);
      currentDate = dateRangeMatch?.[2]
        || text.match(/(?:текущая дата|период до)\s+(\d{4}-\d{2}-\d{2})/i)?.[1]
        || "";
    }
    const remaining = text.match(/осталось\s*~?\s*([^|]+)/i)?.[1]?.trim() || "";
    if (!pauseRemaining && /пауза API/i.test(text)) pauseRemaining = remaining;
    if (!eta && remaining && /(?:checkpoint|скорость|\bETA\b)/i.test(text)) eta = remaining;
    if (!speed) speed = text.match(/скорость\s+([\d.,]+\s*стр\/с)/i)?.[1] || "";
    if (!elapsed) elapsed = text.match(/прошло\s+([^|]+)/i)?.[1]?.trim() || "";
  }
  const runningStep = Math.max(0, steps.findIndex((step) => step.status === "running"));
  const runKey = `${client.key || "client"}:${client.operation_started_at || "idle"}:${runningStep}`;
  const remembered = historyProgressMemory.get(runKey) || {};
  const current = {
    currentDate: currentDate || remembered.currentDate || "",
    eta: eta || remembered.eta || "",
    speed: speed || remembered.speed || "",
    elapsed: elapsed || remembered.elapsed || "",
    pauseRemaining,
    checkpoint: {
      page: checkpoint.page ?? remembered.checkpoint?.page ?? null,
      rows: checkpoint.rows ?? remembered.checkpoint?.rows ?? null,
      requests: checkpoint.requests ?? remembered.checkpoint?.requests ?? null,
    },
  };
  if (client.operation_status === "running") {
    historyProgressMemory.set(runKey, current);
    if (historyProgressMemory.size > 24) historyProgressMemory.delete(historyProgressMemory.keys().next().value);
  }
  currentDate = current.currentDate;
  eta = current.eta;
  speed = current.speed;
  elapsed = current.elapsed;
  checkpoint.page = current.checkpoint.page;
  checkpoint.rows = current.checkpoint.rows;
  checkpoint.requests = current.checkpoint.requests;
  if (liveStepPercent === null && currentDate && client.history_date_from && client.history_date_to) {
    const from = parseIsoDate(client.history_date_from);
    const to = parseIsoDate(client.history_date_to);
    const current = parseIsoDate(currentDate);
    const span = from && to ? to.getTime() - from.getTime() : 0;
    if (current && span > 0) liveStepPercent = Math.max(0, Math.min(100, ((current.getTime() - from.getTime()) / span) * 100));
  }
  const liveOverallProgress = liveStepPercent === null
    ? null
    : Math.min(95, 5 + Math.round(((runningStep + liveStepPercent / 100) / Math.max(steps.length, 1)) * 90));
  const displayedProgress = liveOverallProgress === null ? progress : Math.max(progress, liveOverallProgress);
  const unknown = client.operation_status === "running" && liveStepPercent === null && progress <= 5;
  return {
    checkpoint,
    unknown,
    progress: displayedProgress,
    label: unknown ? `Этап ${runningStep + 1}/${Math.max(steps.length, 1)}` : `${displayedProgress}%`,
    currentDate,
    eta: eta || "—",
    pauseRemaining: current.pauseRemaining || "",
    speed: speed || "—",
    elapsed: elapsed || "—",
    liveText,
    message: checkpoint.rows !== null
      ? `Сохранено ${checkpoint.rows.toLocaleString("ru-RU")} строк${checkpoint.page !== null ? ` · страница ${checkpoint.page}` : ""} · данные уже в базе`
      : (client.operation_message || "Ожидание запуска"),
  };
}

function historyTerminalPresentation(logs, snapshot) {
  const events = [];
  let live = null;
  logs.forEach((line) => {
    const text = String(line?.text || "");
    if (!text.startsWith("ПРОГРЕСС:")) {
      events.push(line);
      return;
    }
    const isFailureEvent = /(?:ошибка|failed|http\s*429|retry-after)/i.test(text)
      || /\berrors?\s*[=:]\s*(?!0\b)\d+/i.test(text);
    if (/^ПРОГРЕСС:\s*(?:запуск|capability|возобновление)/i.test(text)
      || isFailureEvent
      || /заверш[её]н/i.test(text)) {
      events.push(line);
      return;
    }
    live = line;
  });
  const progressParts = [];
  if (snapshot.currentDate) progressParts.push(`дата ${snapshot.currentDate}`);
  if (snapshot.checkpoint.rows !== null) progressParts.push(`строк ${snapshot.checkpoint.rows.toLocaleString("ru-RU")}`);
  if (snapshot.checkpoint.requests !== null) progressParts.push(`запросов ${snapshot.checkpoint.requests}`);
  if (snapshot.speed !== "—") progressParts.push(`скорость ${snapshot.speed}`);
  if (snapshot.eta !== "—") progressParts.push(`ETA ${snapshot.eta}`);
  if (snapshot.pauseRemaining) progressParts.push(`пауза API ${snapshot.pauseRemaining}`);
  if (live && progressParts.length) live = {...live, text: `ПРОГРЕСС: ${progressParts.join(" | ")}`};
  return {events, live};
}

function historyCapabilityStatus(client) {
  const sources = [
    ...(client.operation_logs || []).map((line) => String(line?.text || "")),
    String(client.operation_message || ""),
  ];
  for (let index = sources.length - 1; index >= 0; index -= 1) {
    const text = sources[index];
    if (/WB preflight.*Jam активен/i.test(text)) return {label: "Jam", value: "активен", tone: "positive"};
    if (/WB preflight.*Jam неактивен/i.test(text)) return {label: "Jam", value: "неактивен", tone: "neutral"};
    if (/WB preflight.*статус Jam не определ/i.test(text)) return {label: "Jam", value: "не определён", tone: "warning"};
    const ozon = text.match(/capability Ozon\s*\|\s*premium=(true|false)\s*\|\s*premium_plus=(true|false)\s*\|\s*режим=(advanced|standard)/i);
    if (ozon) {
      const mode = ozon[3].toLowerCase() === "advanced" ? "Advanced" : "Standard";
      const premium = ozon[2] === "true" ? "Premium Plus" : (ozon[1] === "true" ? "Premium" : "без Premium");
      return {label: "Ozon", value: `${premium} · ${mode}`, tone: mode === "Advanced" ? "positive" : "neutral"};
    }
    const cachedOzon = text.match(/capability Ozon.*подтверждённый режим=(advanced|standard)/i);
    if (cachedOzon) return {label: "Ozon", value: `кэш · ${cachedOzon[1] === "advanced" ? "Advanced" : "Standard"}`, tone: "warning"};
  }
  return null;
}

function renderClientHistoryStepTable(historySteps, historyRunning) {
  return `
    <div class="client-history-step-selector">
      <table class="client-history-step-table" aria-label="Разделы исторической загрузки">
        <thead>
          <tr><th>Раздел</th><th>Описание</th><th>Скрипт</th></tr>
        </thead>
        <tbody>
          ${historySteps.map((step) => `
            <tr class="${step.substeps?.length ? "has-details" : ""}">
              <td>
                <label class="client-history-step-check" title="${escapeHtml(step.script || step.command || step.label || "")}">
                  <input type="checkbox" name="clientHistoryStep" value="${escapeHtml(step.key)}" checked ${historyRunning ? "disabled" : ""}/>
                  <span><i>${adminActionIcon(historyStepIcon(step))}</i><b>${escapeHtml(historyStepShortLabel(step))}</b></span>
                </label>
              </td>
              <td>${historyStepDetailsMarkup(step, true) || `<span class="client-history-step-muted">Полная выгрузка раздела и обновление зависимых витрин.</span>`}</td>
              <td><code>${escapeHtml(step.script || step.command || "run_client_pipeline.py")}</code></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function clientHistoryInspectionPeriod(row) {
  if (Number(row.requested_days || 0) > 0) {
    const missing = Number(row.missing_days || 0);
    return `${Number(row.covered_days || 0)} из ${Number(row.requested_days)} дн.${missing ? ` · пробел ${missing} дн.` : ""}`;
  }
  if (row.date_from && row.date_to) return `${formatRuDate(row.date_from)} - ${formatRuDate(row.date_to)}`;
  if (row.date_from || row.date_to) return formatRuDate(row.date_from || row.date_to);
  if (row.objects_total != null) return `${Number(row.objects_present || 0)} из ${Number(row.objects_total || 0)} объектов`;
  return "—";
}

function clientHistoryInspectionRun(row) {
  const run = row.latest_run;
  if (!run) return `<span class="client-history-inspection-muted">Запусков нет</span>`;
  const period = run.date_from && run.date_to ? `${formatRuDate(run.date_from)} - ${formatRuDate(run.date_to)}` : "без периода";
  const timestamp = String(run.finished_at || run.started_at || "").slice(0, 16).replace("T", " ");
  return `<b>${escapeHtml(run.status || "—")}</b><span>${escapeHtml(period)} · ${escapeHtml(timestamp || "—")}</span><small>${escapeHtml(String(run.rows || 0))} строк · ${escapeHtml(String(run.requests || 0))} запросов</small>`;
}

function clientHistoryAvitoMethodDescription(row) {
  if (row?.description) return row.description;
  return {
    avito_account: "Карточка кабинета: Account ID и полный набор параметров аккаунта из ответа Avito Ads API.",
    avito_balance: "Основной и бонусный баланс рекламного кабинета на дату снимка.",
    avito_campaigns: "ID, название и статус кампании; рекламодатель, договор, тип, модель оплаты, бюджет и время обновления.",
    avito_groups: "ID и название группы, родительская кампания, статус, бюджет и установленная цена.",
    avito_creatives: "ID, название и статус объявления, связи с кампанией и группой, ссылка на предпросмотр.",
    avito_advertising: "По дням и уровням кампания / группа / объявление: показы, клики, CTR, расходы и бонусы, CPM, CPC, просмотры видео 25/50/75/100%, Q25/Q50/Q75 и VTR.",
  }[row?.key] || "—";
}

function renderClientHistoryInspection(client, marketplace) {
  if (!["wb", "avito", "yandex_market"].includes(marketplace)) return "";
  const showMethodDescription = ["avito", "yandex_market"].includes(marketplace);
  const inspection = state.adminHistoryInspection?.client?.key === client.key
    && state.adminHistoryInspection?.marketplace === marketplace
    ? state.adminHistoryInspection
    : null;
  const summary = inspection?.summary || {};
  const checkedAt = String(inspection?.checked_at || "").slice(0, 16).replace("T", " ");
  return `
    <section class="client-history-inspection" aria-label="Инспекция загруженных данных ${escapeHtml(marketplace === "avito" ? "Avito" : "WB")}">
      <header>
        <div>
          <strong>Что фактически загружено</strong>
          <span>${inspection ? `${escapeHtml(inspection.client.db_name)} · проверено ${escapeHtml(checkedAt)}` : "Проверка таблиц, строк, дат и журнала запусков"}</span>
        </div>
        <div class="client-history-inspection-summary">
          ${inspection ? `<span class="is-loaded">Загружено <b>${Number(summary.loaded || 0)}</b></span><span class="is-limited">Ограничено <b>${Number(summary.limited || 0)}</b></span><span class="is-empty">Пусто <b>${Number(summary.empty || 0)}</b></span><span class="is-missing">Нет запуска <b>${Number(summary.missing || 0)}</b></span>${summary.error ? `<span class="is-error">Ошибки <b>${Number(summary.error)}</b></span>` : ""}` : ""}
          <button type="button" class="admin-icon-button ghost" data-refresh-client-history-inspection title="Проверить данные заново" aria-label="Проверить данные заново" ${state.adminHistoryInspectionLoading ? "disabled" : ""}>${adminActionIcon("history")}</button>
        </div>
      </header>
      ${state.adminHistoryInspectionLoading ? `<div class="client-history-inspection-loading">Проверяю клиентскую БД…</div>` : ""}
      ${state.adminHistoryInspectionError ? `<div class="client-onboarding-error">${escapeHtml(state.adminHistoryInspectionError)}</div>` : ""}
      ${inspection?.coverage?.missing_windows?.length ? `<div class="client-history-inspection-gaps"><strong>Пробелы выбранного периода:</strong> ${inspection.coverage.missing_windows.map((window) => `${formatRuDate(window.date_from)} - ${formatRuDate(window.date_to)}`).join("; ")}</div>` : ""}
      ${inspection && !state.adminHistoryInspectionLoading ? `
        <div class="client-history-inspection-table-wrap">
          <table class="client-history-inspection-table ${showMethodDescription ? "has-method-description" : ""}">
            <thead><tr><th>Раздел</th>${showMethodDescription ? "<th>Что придёт</th>" : ""}<th>Статус</th><th>Строки</th><th>Данные в БД</th><th>Последний запуск</th></tr></thead>
            <tbody>
              ${(inspection.rows || []).map((row) => `
                <tr class="is-${escapeHtml(row.status || "missing")}">
                  <td><b>${escapeHtml(row.label)}</b><small>${escapeHtml(row.object || "")}</small></td>
                  ${showMethodDescription ? `<td class="client-history-inspection-description">${escapeHtml(clientHistoryAvitoMethodDescription(row))}</td>` : ""}
                  <td><span class="client-history-inspection-status">${escapeHtml(row.status_label || "—")}</span>${row.limit_note ? `<small>${escapeHtml(row.limit_note)}</small>` : ""}</td>
                  <td><strong>${row.rows == null ? "—" : Number(row.rows).toLocaleString("ru-RU")}</strong></td>
                  <td>${escapeHtml(clientHistoryInspectionPeriod(row))}</td>
                  <td><div class="client-history-inspection-run">${clientHistoryInspectionRun(row)}</div></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : ""}
    </section>
  `;
}

function historyActiveStepIndex(logs, steps) {
  const progress = [...logs].reverse()
    .map((line) => String(line?.text || "").match(/^ПРОГРЕСС:\s+(\d+)\/(\d+)\s+\([^)]*\)\s*\|\s*([^|]+)/i))
    .find(Boolean);
  if (!progress) return -1;
  const [, currentText, totalText, rawLabel] = progress;
  const label = rawLabel.replace(/\s+заверш[её]н\s*$/i, "").trim().toLowerCase();
  const scriptMatch = (step) => String(step.script || step.command || "").match(/--step\s+([a-z0-9_-]+)/i)?.[1]?.toLowerCase() === label;
  const labelMatch = (step) => {
    const stepLabel = String(step.label || "").toLowerCase();
    return stepLabel === label || stepLabel.endsWith(`· ${label}`);
  };
  const matchedIndex = steps.findIndex((step) => scriptMatch(step) || labelMatch(step));
  const completed = /заверш[её]н\s*$/i.test(rawLabel);
  if (matchedIndex >= 0) return completed ? matchedIndex + 1 : matchedIndex;
  if (Number(totalText) === steps.length) return Number(currentText) - 1;
  return -1;
}

function historyProcessMarkup(client) {
  const progress = Math.max(0, Math.min(100, Number(client.operation_progress || 0)));
  const status = client.operation_status || "idle";
  const logs = client.operation_logs?.length ? client.operation_logs : (client.operation_message ? [{time: client.operation_started_at, type: "output", text: client.operation_message}] : []);
  let steps = historyStepsForMarketplace(client, client.operation_steps?.length ? client.operation_steps : fallbackHistorySteps(client));
  if (status === "running") {
    let activeIndex = historyActiveStepIndex(logs, steps);
    if (activeIndex < 0) activeIndex = steps.findIndex((step) => step.status === "running");
    if (activeIndex < 0) activeIndex = Math.max(0, steps.map((step) => step.status).lastIndexOf("done"));
    if (steps.length) {
      activeIndex = Math.max(0, Math.min(steps.length - 1, activeIndex));
      steps = steps.map((step, index) => ({...step, status: step.status === "limited" ? "limited" : index < activeIndex ? "done" : index === activeIndex ? "running" : "pending"}));
    }
  }
  steps = expandAvitoHistoryMethodSteps(steps, logs, status);
  const snapshot = historyProgressSnapshot(client, steps, progress);
  const checkpoint = snapshot.checkpoint;
  const terminal = historyTerminalPresentation(logs, snapshot);
  const capability = historyCapabilityStatus(client);
  const stepLabels = {pending: "Ожидает", running: "Работает", done: "Готово", limited: "Есть ограничения", failed: "Ошибка", stopped: "Остановлен"};
  return `
    <section class="client-history-process is-${escapeHtml(status)}" aria-live="polite">
      <header class="client-history-process-head">
        <div><strong>${escapeHtml(historyOperationStatus(client))}</strong><span>${escapeHtml(snapshot.message)}</span></div>
        <div class="client-history-process-meta">
          <span>Время <b>${escapeHtml(historyDuration(client.operation_started_at, client.operation_finished_at))}</b></span>
          <span>Дата <b>${escapeHtml(snapshot.currentDate ? formatRuDate(snapshot.currentDate) : "—")}</b></span>
          ${checkpoint.rows !== null ? `<span>Строки <b>${escapeHtml(checkpoint.rows.toLocaleString("ru-RU"))}</b></span>` : ""}
          <span>Запросы <b>${escapeHtml(String(checkpoint.requests ?? client.operation_request_count ?? 0))}</b></span>
          ${capability ? `<span class="is-capability is-${escapeHtml(capability.tone)}">${escapeHtml(capability.label)} <b>${escapeHtml(capability.value)}</b></span>` : ""}
          ${snapshot.speed !== "—" ? `<span>Скорость <b>${escapeHtml(snapshot.speed)}</b></span>` : ""}
          <span>Осталось <b>${escapeHtml(snapshot.eta !== "—" ? snapshot.eta : (status === "running" ? "расчёт…" : "—"))}</b></span>
          <strong>${escapeHtml(snapshot.label)}</strong>
        </div>
      </header>
      <div class="client-history-progress-track${snapshot.unknown ? " is-indeterminate" : ""}" role="progressbar" aria-label="Прогресс исторической загрузки" aria-valuemin="0" aria-valuemax="100" ${snapshot.unknown ? `aria-valuetext="${escapeHtml(snapshot.message)}"` : `aria-valuenow="${escapeHtml(String(snapshot.progress))}"`}><i style="width:${snapshot.unknown ? 36 : snapshot.progress}%"></i></div>
      <div class="client-history-workspace">
        <section class="client-history-terminal-shell">
          <header><div><strong>Терминал процесса</strong><span>${terminal.events.length} событий · текущий прогресс обновляется одной строкой</span></div><code>PID ${escapeHtml(String(client.operation_pid || "—"))}</code></header>
          <div id="clientHistoryTerminal" class="client-history-terminal" role="log" aria-label="Терминал исторической загрузки">
            ${terminal.events.map((line) => `<div class="is-${escapeHtml(line.type || "output")}"><time>${escapeHtml(String(line.time || "").slice(11, 19) || "--:--:--")}</time><code>${escapeHtml(line.text || "")}</code></div>`).join("")}
            ${terminal.live ? `<div class="is-live"><time>${escapeHtml(String(terminal.live.time || "").slice(11, 19) || "--:--:--")}</time><code>${escapeHtml(terminal.live.text || "")}</code></div>` : ""}
            ${!terminal.events.length && !terminal.live ? `<p>Терминал готов. После запуска здесь появится вывод скриптов.</p>` : ""}
          </div>
        </section>
        <section class="client-history-scripts">
          <header><strong>Этапы выгрузки</strong><span>${steps.filter((step) => step.status === "done").length}/${steps.length} завершено</span></header>
          <div class="client-history-script-list">
            ${steps.map((step) => `<article class="is-${escapeHtml(step.status || "pending")}"><i aria-hidden="true"></i><div><strong>${escapeHtml(step.label || "Этап")}</strong><code>${escapeHtml(step.script || "")}${step.rows !== null && step.rows !== undefined ? ` · ${Number(step.rows).toLocaleString("ru-RU")} строк` : ""}</code>${historyStepDetailsMarkup(step)}</div><span>${escapeHtml(stepLabels[step.status] || "Ожидает")}</span></article>`).join("")}
          </div>
        </section>
      </div>
      ${status === "failed" ? `<div class="client-onboarding-error">${escapeHtml(client.operation_error || "Операция завершилась с ошибкой")}</div>` : ""}
    </section>`;
}

function onboardingProgressMarkup(client) {
  if (!client) return "";
  const stages = client.operation === "history" ? [
    ["queued", "Очередь"], ["history", "API-история"], ["history_ready", "Витрины"],
  ] : [
    ["queued", "Очередь"], ["credentials", "API"], ["folders", "Папки"], ["database", "БД"],
    ["schema", "Таблицы и витрины"], ["pipelines", "Pipelines"], ["ready", "Селектор"],
  ];
  const stageIndex = stages.findIndex(([key]) => key === client.operation_stage);
  const failed = client.operation_status === "failed";
  return `
    <section class="client-onboarding-progress ${failed ? "is-failed" : ""} ${client.operation === "history" ? "is-history" : ""}" aria-live="polite">
      <div class="client-onboarding-progress-head">
        <div><strong>${escapeHtml(client.label)}</strong><span>${escapeHtml(client.operation_message || "Подготовка подключения")}</span></div>
        <div class="client-onboarding-progress-actions">
          <b>${escapeHtml(String(client.operation_progress || 0))}%</b>
          ${failed && client.operation === "provision" ? `<button type="button" class="icon-button ghost" data-retry-client-onboarding="${escapeHtml(client.key)}" title="Повторить подключение" aria-label="Повторить подключение">↻</button>` : ""}
        </div>
      </div>
      <div class="client-onboarding-progress-track"><i style="width:${Math.max(0, Math.min(100, Number(client.operation_progress || 0)))}%"></i></div>
      <div class="client-onboarding-steps">
        ${stages.map(([key, label], index) => `<span class="${index < stageIndex || client.operation_status === "completed" ? "is-done" : index === stageIndex ? "is-current" : ""}"><i>${index < stageIndex || client.operation_status === "completed" ? "✓" : index + 1}</i>${escapeHtml(label)}</span>`).join("")}
      </div>
      ${failed ? `<div class="client-onboarding-error">${escapeHtml(client.operation_error || "Операция завершилась с ошибкой")}</div>` : ""}
    </section>`;
}

function renderAdminClientsDashboard() {
  const registry = state.adminClientRegistry;
  if (!registry) {
    return `<section class="add-client-panel admin-client-registry-panel"><header><div><h3>Клиенты</h3><span>Загружаю защищённый реестр…</span></div></header></section>`;
  }
  return `
    <section class="add-client-panel admin-client-registry-panel">
      <header><div><h3>Клиенты</h3><span>${escapeHtml(String((registry.clients || []).length))} в реестре · отчёты и доступы клиента</span></div></header>
      ${renderAdminClientRegistryList(registry)}
    </section>
  `;
}

function selectedClientOnboardingRow(registry) {
  const selected = (registry.clients || []).find((client) => client.key === state.adminOnboardingClientKey)
    || (registry.clients || []).find((client) => client.operation_status === "running");
  if (selected && !state.adminOnboardingClientKey) state.adminOnboardingClientKey = selected.key;
  return selected;
}

function localIsoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function wbStockHistoryEarliestDate() {
  const today = new Date();
  const firstOfTargetMonth = new Date(today.getFullYear(), today.getMonth() - 3, 1);
  const lastTargetDay = new Date(firstOfTargetMonth.getFullYear(), firstOfTargetMonth.getMonth() + 1, 0).getDate();
  firstOfTargetMonth.setDate(Math.min(today.getDate(), lastTargetDay));
  return localIsoDate(firstOfTargetMonth);
}

function wbStockHistoryRange(clientKey, generalFrom, generalTo, yesterday) {
  const earliest = wbStockHistoryEarliestDate();
  const saved = state.adminWbStockHistoryRanges?.[clientKey];
  if (saved?.dateFrom && saved?.dateTo) {
    const savedDateFrom = saved.dateFrom < earliest ? earliest : saved.dateFrom;
    const savedDateTo = saved.dateTo > yesterday ? yesterday : saved.dateTo;
    if (savedDateFrom <= savedDateTo) return {dateFrom: savedDateFrom, dateTo: savedDateTo, earliest};
  }
  let dateTo = generalTo && generalTo <= yesterday ? generalTo : yesterday;
  if (dateTo < earliest) dateTo = yesterday;
  let dateFrom = generalFrom && generalFrom >= earliest ? generalFrom : earliest;
  if (dateFrom > dateTo) dateFrom = earliest;
  return {dateFrom, dateTo, earliest};
}

function rememberWbStockHistoryRange(clientKey = state.adminOnboardingClientKey) {
  const dateFrom = qs("clientHistoryWbStockDateFrom")?.value || "";
  const dateTo = qs("clientHistoryWbStockDateTo")?.value || "";
  if (!clientKey || !dateFrom || !dateTo) return;
  state.adminWbStockHistoryRanges = {
    ...state.adminWbStockHistoryRanges,
    [clientKey]: {dateFrom, dateTo},
  };
}

function renderClientHistoryPanel(selected, registry) {
  if (selected?.status !== "active") return "";
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const defaultHistoryFromDate = new Date(Date.now() - 99 * 86400000);
  defaultHistoryFromDate.setDate(1);
  const defaultHistoryFrom = defaultHistoryFromDate.toISOString().slice(0, 10);
  const historyDateFrom = selected.history_date_from || defaultHistoryFrom;
  const historyDateTo = selected.history_date_to || yesterday;
  const historyRunning = selected?.operation === "history" && selected?.operation_status === "running";
  const historyOverwrite = state.adminHistoryOverwrite || Boolean(historyRunning && selected?.operation_overwrite);
  if (historyRunning && selected?.operation_overwrite) state.adminHistoryOverwrite = true;
  const historyMarketplace = selected ? historySelectedMarketplace(selected) : "";
  const stockHistoryRange = wbStockHistoryRange(selected.key, historyDateFrom, historyDateTo, yesterday);
  const historySteps = selected ? historyStepsForMarketplace(selected, fallbackHistorySteps({...selected, operation_status: "idle"})) : [];
  const historyProcessClient = selected.operation === "history"
    ? selected
    : {...selected, operation_status: "idle", operation_progress: 0, operation_message: "Ожидание запуска", operation_logs: [], operation_steps: historySteps, operation_pid: ""};
  const resumeSteps = resumeAdminClientHistorySteps(historyProcessClient);
  const historyCanResume = canResumeAdminClientHistory(historyProcessClient, resumeSteps);
  return `
    <section id="clientHistoryPanel" class="add-client-panel client-history-panel">
      <header class="client-history-header">
        <div class="client-history-header-main">
          <div class="client-history-title">
            <h3>Исторические данные · ${escapeHtml(selected.label)}</h3>
            <span>${historyMarketplace === "yandex_market" ? "Яндекс Маркет · период и разделы по магазинам" : `${escapeHtml((historyMarketplace || "").toUpperCase())} · период, разделы и обновление витрин BI`}</span>
          </div>
          ${renderClientHistoryClientSelector(registry, selected)}
          ${renderClientHistoryMarketplaceSwitch(selected, historyMarketplace, historyRunning)}
          <fieldset class="client-history-period date-range" data-client-history-range data-client-history-max="${yesterday}">
            <legend>Период</legend>
            <input id="clientHistoryDateFrom" type="hidden" value="${escapeHtml(historyDateFrom)}" />
            <input id="clientHistoryDateTo" type="hidden" value="${escapeHtml(historyDateTo)}" />
            <button type="button" class="dropdown-toggle client-history-range-toggle" data-client-history-range-toggle aria-label="Выбрать период исторической загрузки">${formatRuDate(historyDateFrom)} - ${formatRuDate(historyDateTo)}</button>
            <div class="date-range-menu client-history-date-menu hidden" data-client-history-range-menu></div>
          </fieldset>
          ${historyMarketplace === "wb" ? `
            <fieldset class="client-history-period client-history-stock-period date-range" data-client-history-range data-client-history-range-kind="wb-stock-history" data-client-history-range-from="clientHistoryWbStockDateFrom" data-client-history-range-to="clientHistoryWbStockDateTo" data-client-history-range-min="${escapeHtml(stockHistoryRange.earliest)}" data-client-history-range-max="${escapeHtml(yesterday)}">
              <legend>История остатков</legend>
              <input id="clientHistoryWbStockDateFrom" type="hidden" value="${escapeHtml(stockHistoryRange.dateFrom)}" />
              <input id="clientHistoryWbStockDateTo" type="hidden" value="${escapeHtml(stockHistoryRange.dateTo)}" />
              <button type="button" class="dropdown-toggle client-history-range-toggle" data-client-history-range-toggle aria-label="Выбрать период истории остатков WB" ${historyRunning ? "disabled" : ""}>${formatRuDate(stockHistoryRange.dateFrom)} - ${formatRuDate(stockHistoryRange.dateTo)}</button>
              <div class="date-range-menu client-history-date-menu hidden" data-client-history-range-menu></div>
            </fieldset>` : ""}
          <label class="client-history-overwrite-toggle" title="Заново скачать и заменить API-данные выбранного периода">
            <input id="clientHistoryOverwrite" type="checkbox" data-client-history-overwrite ${historyOverwrite ? "checked" : ""} ${historyRunning ? "disabled" : ""}/>
            <span>Перезаписать</span>
          </label>
          ${historyMarketplace === "wb" ? `
            <label class="client-history-overwrite-toggle" title="Догрузить поисковые фразы завершённых и приостановленных рекламных кампаний">
              <input id="clientHistoryIncludeInactiveCampaigns" type="checkbox" ${historyRunning ? "disabled" : ""}/>
              <span>Старые РК</span>
            </label>` : ""}
          <div class="client-history-run-actions">
            ${historyCanResume ? `<button type="button" class="client-history-run-button primary" data-resume-client-history title="Продолжить с незавершённого раздела" aria-label="Продолжить историческую загрузку">${adminActionIcon("history")}<span>Продолжить</span></button>` : ""}
            <button type="button" class="client-history-run-button primary" data-start-client-history title="Загрузить выбранное" aria-label="Загрузить выбранное" ${historyRunning ? "disabled" : ""}>${adminActionIcon("historyDownload")}<span>Выбранное</span></button>
            <button type="button" class="client-history-run-button secondary" data-start-client-history-all title="Загрузить всё" aria-label="Загрузить всё" ${historyRunning ? "disabled" : ""}>${adminActionIcon("historyData")}<span>Всё</span></button>
          </div>
        </div>
        <div class="client-history-header-actions">
          ${historyRunning && selected.operation_pid ? adminIconButton("Остановить историческую загрузку", "stop", "data-stop-client-history", "ghost danger", Boolean(selected.operation_stop_requested)) : ""}
          ${adminIconButton("Закрыть панель истории", "close", "data-close-client-history", "ghost")}
        </div>
      </header>
      <div class="client-history-overwrite-warning" data-client-history-overwrite-warning ${historyOverwrite ? "" : "hidden"} role="alert">
        Режим перезаписи включён: API-данные клиента ${escapeHtml(selected.label)} за указанный период будут удалены и загружены заново. Ручные файлы, настройки и данные вне периода сохранятся. При остановке процесса период может остаться загруженным частично.
      </div>
      ${historyMarketplace === "avito" ? `<div class="client-history-avito-note"><strong>Безопасная дозагрузка Avito Ads</strong><span>Обычный режим только добавляет или обновляет строки по ключам и не удаляет историю при пустом ответе. Запросы автоматически делятся на окна до 100 дней. «Продолжить» пропускает уже сохранённые campaign × window checkpoint-окна. Запись в рекламный кабинет запрещена.</span></div>` : ""}
      ${renderClientHistoryStepTable(historySteps, historyRunning)}
      ${historyMarketplace === "yandex_market" ? `<div class="client-history-avito-note"><strong>Яндекс Маркет · история по магазинам</strong><span>Загружаются магазины, включённые в настройках клиента: ${(selected.marketplace_accounts?.yandex_market || []).flatMap((account) => (account.stores || []).filter((store) => account.is_accessible !== false && store.is_accessible !== false && store.import_enabled !== false).map((store) => escapeHtml(`${store.name || store.campaign_id} · ${store.campaign_id}`))).join("; ") || "нет включённых магазинов — проверьте ключ в настройках клиента"}. Окна до 30 дней. «Продолжить» пропускает завершённые окна каждого магазина. Заказы выбираются по дате создания, возвраты — по дате обновления. Реклама, воронка и исторические остатки в эти три раздела не входят.</span></div>` : ""}
      ${renderClientHistoryInspection(selected, historyMarketplace)}
      ${historyProcessMarkup(historyProcessClient)}
      ${historyStartConfirmationMarkup(state.adminHistoryStartConfirmation)}
    </section>
  `;
}

const CLIENT_ONBOARDING_COMMON_REPORT_IDS = [
  "abc", "product", "sku", "weeklyDynamics", "planfact", "salesPlanning",
  "mediaPlan", "profitLoss", "unitEconomics", "commercialRadar",
];

const CLIENT_ONBOARDING_MARKETPLACE_REPORT_IDS = {
  ozon: ["adv", "mediaAdv", "funnel", "inventoryHistory", "seoMonitoring", "reviews"],
  wb: ["adv", "mediaAdv", "funnel", "inventoryHistory", "seoMonitoring", "wbSearchQueries", "wbAdSearchQueries", "wbEntrance", "reviews"],
  avito: ["adv"],
  lamoda: ["funnel", "inventoryHistory", "reviews"],
  yandex_market: ["adv", "funnel", "inventoryHistory", "seoMonitoring", "reviews"],
};

const CLIENT_ONBOARDING_MARKETPLACE_DEFAULT_IDS = {
  ozon: CLIENT_ONBOARDING_MARKETPLACE_REPORT_IDS.ozon,
  wb: CLIENT_ONBOARDING_MARKETPLACE_REPORT_IDS.wb,
  avito: ["adv"],
  lamoda: [],
  yandex_market: [],
};

const CLIENT_ONBOARDING_MARKETPLACE_REPORT_NOTES = {
  avito: "Только Avito Ads: кампании, группы, креативы, баланс, показы, клики и расходы",
};

const CLIENT_ONBOARDING_MARKETPLACE_REPORT_LABELS = {
  avito: { adv: "Рекламная статистика Avito Ads" },
};

function onboardingDefaultReportIds(marketplace) {
  return [
    ...CLIENT_ONBOARDING_COMMON_REPORT_IDS,
    ...(CLIENT_ONBOARDING_MARKETPLACE_DEFAULT_IDS[marketplace] || []),
  ];
}

const CLIENT_ONBOARDING_MARKETPLACES = {
  ozon: { label: "Ozon", badge: "O", source: "Seller API" },
  wb: { label: "Wildberries", badge: "WB", source: "Content / Analytics API" },
  avito: { label: "Авито", badge: "A", source: "API / Ads API" },
  lamoda: { label: "Lamoda", badge: "L", source: "Seller API v2" },
  yandex_market: { label: "Яндекс Маркет", badge: "Я", source: "Partner API" },
};

function renderClientOnboardingCredentialFields(marketplace) {
  if (marketplace === "wb") return `<label class="is-wide">WB API token<input id="newClientWbToken" type="password" autocomplete="new-password" placeholder="Токен доступа WB" /></label><label class="is-wide">WB Service token для проверки Jam<input id="newClientWbServiceToken" type="password" autocomplete="new-password" placeholder="Необязательно: сервисный токен WB" /></label>`;
  if (marketplace === "avito") return `<label>Avito Account ID<input id="newClientAvitoAccountId" inputmode="numeric" autocomplete="off" placeholder="ID рекламного аккаунта" /></label><label>Avito Client ID<input id="newClientAvitoClientId" autocomplete="off" placeholder="Client ID" /></label><label class="is-wide">Avito Client Secret<input id="newClientAvitoClientSecret" type="password" autocomplete="new-password" placeholder="Client Secret" /></label><small class="client-onboarding-access-note is-wide">Access token выпускается при запуске и не сохраняется.</small>`;
  if (marketplace === "lamoda") return `<label>Lamoda Client ID<input id="newClientLamodaClientId" autocomplete="off" placeholder="client_id" /></label><label>Lamoda Client Secret<input id="newClientLamodaClientSecret" type="password" autocomplete="new-password" placeholder="client_secret" /></label><small class="client-onboarding-access-note is-wide">OAuth access token короткоживущий и не сохраняется. Импорт Lamoda подключается отдельным адаптером.</small>`;
  if (marketplace === "yandex_market") return `<label class="is-wide">API-Key Яндекс Маркета<input id="newClientYandexApiKey" type="password" autocomplete="new-password" placeholder="API-Key" /></label><small class="client-onboarding-access-note is-wide">Business ID и доступные магазины будут определены автоматически при проверке API-ключа.</small>`;
  return `<label>Ozon Client ID<input id="newClientOzonClientId" inputmode="numeric" autocomplete="off" placeholder="Client ID" /></label><label>Ozon API key<input id="newClientOzonApiKey" type="password" autocomplete="new-password" placeholder="API key" /></label><details class="client-onboarding-optional is-wide"><summary>Ключи Performance API для рекламы — необязательно</summary><div><label>Performance Client ID<input id="newClientOzonPerformanceId" autocomplete="off" /></label><label>Performance Secret<input id="newClientOzonPerformanceSecret" type="password" autocomplete="new-password" /></label></div></details>`;
}

function renderClientOnboardingReportSelector(registry, marketplace) {
  const defaults = new Set(onboardingDefaultReportIds(marketplace));
  const reports = registry.reports || [];
  const reportById = new Map(reports.map((report) => [report.id, report]));
  const commonReports = CLIENT_ONBOARDING_COMMON_REPORT_IDS.map((id) => reportById.get(id)).filter(Boolean);
  const marketplaceReports = (CLIENT_ONBOARDING_MARKETPLACE_REPORT_IDS[marketplace] || []).map((id) => reportById.get(id)).filter(Boolean);
  const marketplaceLabel = CLIENT_ONBOARDING_MARKETPLACES[marketplace]?.label || marketplace;
  const marketplaceNote = CLIENT_ONBOARDING_MARKETPLACE_REPORT_NOTES[marketplace] || "Зависят от API и данных выбранного маркетплейса";
  const renderReports = (items, labels = {}) => items.map((report) => `<label class="${defaults.has(report.id) ? "is-default" : ""}"><input type="checkbox" name="newClientReport" value="${escapeHtml(report.id)}" ${defaults.has(report.id) ? "checked" : ""}/><span>${escapeHtml(labels[report.id] || report.label)}</span></label>`).join("");
  return `
    <div class="client-onboarding-section-title"><b>3</b><div><strong>Отчёты клиента</strong><span>Эти разделы будут включены у нового магазина по умолчанию</span></div></div>
    <div class="client-onboarding-report-groups">
      <section class="client-onboarding-report-group" aria-labelledby="clientCommonReportsTitle">
        <header><strong id="clientCommonReportsTitle">Общие отчёты</strong><span>Единый набор для всех маркетплейсов</span></header>
        <div class="client-onboarding-report-grid" role="group" aria-label="Общие отчёты нового клиента">${renderReports(commonReports)}</div>
      </section>
      <section class="client-onboarding-report-group is-marketplace" aria-labelledby="clientMarketplaceReportsTitle">
        <header><strong id="clientMarketplaceReportsTitle">Отчёты ${escapeHtml(marketplaceLabel)}</strong><span>${escapeHtml(marketplaceNote)}</span></header>
        <div class="client-onboarding-report-grid" role="group" aria-label="Отчёты ${escapeHtml(marketplaceLabel)}">${renderReports(marketplaceReports, CLIENT_ONBOARDING_MARKETPLACE_REPORT_LABELS[marketplace])}</div>
      </section>
    </div>
  `;
}

function renderAddClientDashboard() {
  const registry = state.adminClientRegistry;
  if (!registry) {
    return `<section class="add-client-panel"><header><div><h3>Подключение нового магазина</h3><span>Загружаю защищённый реестр…</span></div></header></section>`;
  }
  const marketplace = state.adminOnboardingMarketplace || "ozon";
  const selected = selectedClientOnboardingRow(registry);
  return `
    <section class="add-client-panel client-onboarding-wizard">
      <header>
        <div><h3>Подключение нового магазина</h3><span>Введите токены, создайте инфраструктуру и догрузите историю магазина.</span></div>
      </header>
      <div class="client-onboarding-layout">
        <div class="client-onboarding-form">
          <div class="client-onboarding-section-title"><b>1</b><div><strong>Маркетплейс</strong><span>Выберите источник данных</span></div></div>
          <div class="client-onboarding-marketplaces" role="radiogroup" aria-label="Маркетплейс">
            ${Object.entries(CLIENT_ONBOARDING_MARKETPLACES).map(([id, item]) => `<label class="marketplace-card--${id} ${marketplace === id ? "is-active" : ""}"><input type="radio" name="clientOnboardingMarketplace" value="${id}" ${marketplace === id ? "checked" : ""} /><i>${item.badge}</i><span><strong>${item.label}</strong><small>${item.source}</small></span></label>`).join("")}
          </div>
          <div class="client-onboarding-section-title"><b>2</b><div><strong>Магазин и доступ</strong><span>Название задаёт папку, ключ клиента и имя БД</span></div></div>
          <div class="client-onboarding-fields">
            <label>Название магазина<input id="newClientName" value="${escapeHtml(state.adminOnboardingDraftName || "")}" autocomplete="organization" placeholder="Например, Спортмастер" /></label>
            ${renderClientOnboardingCredentialFields(marketplace)}
          </div>
          ${renderClientOnboardingReportSelector(registry, marketplace)}
          <div class="client-onboarding-submit-row">
            <div><strong>Создать инфраструктуру</strong><span>Магазин появится в селекторе только после успешной проверки.</span></div>
            <button type="button" class="icon-button primary" data-save-admin-client title="Подключить магазин" aria-label="Подключить магазин">＋</button>
            <span id="newClientSaveStatus" class="client-save-status" role="status" aria-live="polite"></span>
          </div>
        </div>
        <aside class="client-onboarding-summary" aria-label="Что будет создано">
          <strong>Будет создано</strong>
          <ul><li>Папка в ${escapeHtml(registry.clients_root || "G:\\Общие диски\\Kokoc Marketplaces\\Clients")}</li><li>Отдельная база PostgreSQL</li><li>Таблицы и materialized views BI</li><li>API-импорт и обновление витрин</li><li>Ежедневный процесс в Админке</li><li>Магазин в общем селекторе</li></ul>
          <small>Токен используется для read-only проверки API и хранится только в зашифрованном виде.</small>
        </aside>
      </div>
      ${selected && selected.status !== "active" ? onboardingProgressMarkup(selected) : ""}
    </section>
    ${renderClientHistoryPanel(selected, registry)}
  `;
}

function adminAllClientsDuration(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  return [hours, minutes, secs].map((value) => String(value).padStart(2, "0")).join(":");
}

function adminAllClientsRowMarketplace(row = {}) {
  const value = `${row.id || ""} ${row.label || ""}`.toLowerCase();
  if (value.includes("avito") || value.includes("авито")) return "avito";
  if (value.includes("wb")) return "wb";
  if (value.includes("ozon")) return "ozon";
  return "";
}

function adminAllClientsClientMarketplaces(client = {}, tasks = []) {
  const configured = Array.isArray(client.marketplaces) ? client.marketplaces.filter(Boolean) : [];
  if (configured.length) return new Set(configured.map((value) => String(value).toLowerCase()));
  const inferred = new Set();
  tasks.filter((task) => task.client === client.key).forEach((task) => {
    const marketplace = adminAllClientsRowMarketplace({ id: task.row_id, label: task.row_label });
    if (marketplace) inferred.add(marketplace);
  });
  return inferred;
}

function adminAllClientsCell(taskOrTasks, context = {}) {
  const tasks = Array.isArray(taskOrTasks) ? taskOrTasks : (taskOrTasks ? [taskOrTasks] : []);
  if (!tasks.length) {
    const rowMarketplace = adminAllClientsRowMarketplace(context.row);
    const connected = context.marketplaces instanceof Set ? context.marketplaces : new Set();
    if (rowMarketplace && connected.size && !connected.has(rowMarketplace)) {
      const marketplaceLabels = { wb: "WB", ozon: "Ozon", avito: "Avito" };
      const marketplaceLabel = marketplaceLabels[rowMarketplace] || rowMarketplace;
      const connectedLabel = [...connected].map((value) => marketplaceLabels[value] || value).join(" + ");
      const title = `${marketplaceLabel} не подключён. Подключено: ${connectedLabel}`;
      return `<td class="all-clients-status-cell is-unavailable is-not-applicable"><span title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">Нет ${marketplaceLabel}</span></td>`;
    }
    if (context.row?.id === "imports:wb_market_search") {
      const title = "Отдельный ручной XLSX со спросом всей площадки WB не настроен. Поисковая аналитика по своим товарам из API отображается строкой «Поисковые запросы WB».";
      return `<td class="all-clients-status-cell is-unavailable is-source-manual"><span title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">Нет XLSX</span></td>`;
    }
    if (context.row?.id === "views:wb_market_search_views") {
      const title = "Витрина строится только из отдельного XLSX со спросом всей площадки WB. Поисковая аналитика по своим товарам из API относится к другой витрине.";
      return `<td class="all-clients-status-cell is-unavailable is-source-manual"><span title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">Нет XLSX</span></td>`;
    }
    return '<td class="all-clients-status-cell is-unavailable"><span title="Этап не подключён" aria-label="Этап не подключён">Не подключено</span></td>';
  }
  const task = tasks[0];
  const statuses = tasks.map((item) => item.status || "queued");
  const terminalStatuses = new Set(["ok", "limited", "skipped"]);
  let status = statuses.every((value) => value === "ok") ? "ok" : (statuses[0] || "queued");
  if (statuses.includes("error")) status = "error";
  else if (statuses.includes("running")) status = "running";
  else if (statuses.includes("stopped")) status = "stopped";
  else if (statuses.includes("limited")) status = "limited";
  else if (statuses.includes("skipped")) status = "skipped";
  else if (statuses.includes("queued")) status = "queued";
  else if (statuses.includes("idle")) status = "idle";
  const activeTask = tasks.find((item) => item.status === status) || task;
  const completedWindows = statuses.filter((value) => terminalStatuses.has(value)).length;
  const detail = activeTask.detail || activeTask.progress_text || "В очереди";
  const sourceKind = task.source_kind === "api" ? "api" : "manual";
  const sourceLabel = task.source_label || (sourceKind === "api" ? "API" : "Ручной файл");
  let icon = '<span class="all-clients-status-dot" aria-hidden="true"></span>';
  if (status === "running") icon = '<span class="all-clients-status-spinner" aria-hidden="true"></span>';
  else if (status === "ok") icon = adminActionIcon("check");
  else if (status === "error") icon = adminActionIcon("error");
  else if (status === "stopped") icon = adminActionIcon("stop");
  const coverage = task.coverage_label ? `Покрытие: ${task.coverage_label}` : "";
  const missing = task.missing_label && task.missing_label !== "нет" ? `Не хватает: ${task.missing_label}` : "";
  const expected = task.expected_from ? `Контроль: ${task.expected_from}—${task.expected_to || task.expected_from}` : "";
  const limit = task.retention_days ? `Окно API: ${task.retention_days} дн.` : "";
  const inspection = task.inspection_error ? `Проверка БД недоступна: ${task.inspection_error}` : "";
  const title = [sourceLabel, coverage, expected, missing, limit, inspection, detail].filter(Boolean).join(" · ");
  let cellText = activeTask.progress_text || task.cell_label || (status === "queued" ? "Ждёт" : detail);
  if (tasks.length > 1 && !statuses.every((value) => terminalStatuses.has(value))) cellText = `${completedWindows}/${tasks.length} окон`;
  const directTask = tasks.every((item) => item.row_id === context.row?.id);
  const selectable = Boolean(context.selectable && directTask);
  const selectionScope = context.selectionScope || "daily";
  const selectionKey = `${selectionScope}|${context.client?.key || task.client}|${context.row?.id || task.row_id}`;
  if (!(selectionKey in state.adminAllClientsTaskSelection)) {
    state.adminAllClientsTaskSelection[selectionKey] = !statuses.every((value) => terminalStatuses.has(value));
  }
  const taskIds = tasks.map((item) => item.id).filter(Boolean).join("|");
  const checkbox = selectable
    ? `<label class="all-clients-stage-select" title="Выбрать этап для запуска"><input type="checkbox" data-all-clients-task-select data-task-scope="${escapeHtml(selectionScope)}" data-selection-key="${escapeHtml(selectionKey)}" data-client="${escapeHtml(context.client?.key || task.client)}" data-task-ids="${escapeHtml(taskIds)}" aria-label="Выбрать ${escapeHtml(context.row?.label || task.row_label)} для ${escapeHtml(context.client?.label || task.client_label)}" ${state.adminAllClientsTaskSelection[selectionKey] ? "checked" : ""} ${context.selectionDisabled ? "disabled" : ""}></label>`
    : "";
  return `<td class="all-clients-status-cell is-${escapeHtml(status)} is-source-${sourceKind}"><span class="all-clients-cell-content">${checkbox}<span class="all-clients-status" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"><i class="all-clients-source-mark" aria-hidden="true"></i>${icon}<small>${escapeHtml(cellText)}</small></span></span></td>`;
}

function renderAdminAllClientsRunner(payload = {}, options = {}) {
  if (!payload || !Array.isArray(payload.clients)) {
    return `<section class="admin-all-clients-workspace is-${escapeHtml(options.kind || "daily")}"><div class="admin-all-clients-loading">Загружаем состояние процесса...</div></section>`;
  }
  const kind = options.kind || "daily";
  const actionSuffix = kind === "assortment"
    ? "all-clients-assortment"
    : kind === "avito"
      ? "all-clients-avito"
      : "all-clients-daily";
  const selectionScope = options.selectionScope || kind;
  const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
  const clientMarketplaces = new Map(payload.clients.map((client) => [
    client.key,
    adminAllClientsClientMarketplaces(client, tasks),
  ]));
  const taskByCell = new Map();
  tasks.forEach((task) => {
    const key = `${task.row_id}|${task.client}`;
    if (!taskByCell.has(key)) taskByCell.set(key, []);
    taskByCell.get(key).push(task);
  });
  const bundledViewParents = new Map([
    ["views:ozon_funnel_views", [{ id: "views:ozon_views", label: "Витрины BI Ozon" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:ozon_advertising_views", [{ id: "views:ozon_views", label: "Витрины BI Ozon" }, { id: "views:all_views", label: "Общие витрины BI" }, { id: "imports:ozon_advertising", label: "Импорт товарной рекламы Boiron", taskKey: "boiron_ozon_adv_daily" }]],
    ["views:ozon_media_advertising_views", [{ id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:ozon_abc_views", [{ id: "views:ozon_views", label: "Витрины BI Ozon" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:ozon_sku_views", [{ id: "views:ozon_views", label: "Витрины BI Ozon" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:wb_funnel_views", [{ id: "views:wb_views", label: "Витрины BI WB" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:wb_advertising_views", [{ id: "views:wb_views", label: "Витрины BI WB" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:wb_abc_views", [{ id: "views:wb_views", label: "Витрины BI WB" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["views:wb_sku_views", [{ id: "views:wb_views", label: "Витрины BI WB" }, { id: "views:all_views", label: "Общие витрины BI" }]],
    ["imports:avito_account", [{ id: "imports:avito_advertising", label: "Единый пакет Avito Ads" }]],
    ["imports:avito_balance", [{ id: "imports:avito_advertising", label: "Единый пакет Avito Ads" }]],
    ["imports:avito_campaigns", [{ id: "imports:avito_advertising", label: "Единый пакет Avito Ads" }]],
    ["imports:avito_groups", [{ id: "imports:avito_advertising", label: "Единый пакет Avito Ads" }]],
    ["imports:avito_creatives", [{ id: "imports:avito_advertising", label: "Единый пакет Avito Ads" }]],
  ]);
  const tasksForCell = (row, client) => {
    const direct = taskByCell.get(`${row.id}|${client.key}`);
    if (direct?.length) return direct;
    if (row.id === "imports:wb_stock") {
      const funnelTasks = taskByCell.get(`imports:wb_funnel|${client.key}`);
      if (!funnelTasks?.length) return direct;
      return funnelTasks.map((task) => ({
        ...task,
        source_label: `${task.source_label || "Файл"} · остатки в составе воронки WB`,
      }));
    }
    const parentCandidates = bundledViewParents.get(row.id) || [];
    const parentTasksFor = (candidate) => (taskByCell.get(`${candidate.id}|${client.key}`) || [])
      .filter((task) => !candidate.taskKey || task.key === candidate.taskKey);
    const bundledParent = parentCandidates.find((candidate) => parentTasksFor(candidate).length);
    if (!bundledParent) return direct;
    const parentTasks = parentTasksFor(bundledParent);
    return parentTasks.map((task) => ({
      ...task,
      source_label: `${task.source_label || "API"} · собирается в «${bundledParent.label}»`,
    }));
  };
  const status = payload.status || "idle";
  const current = payload.current || {};
  const progress = Math.max(0, Math.min(100, Number(payload.progress_pct || 0)));
  const sourceRows = Array.isArray(payload.rows) ? payload.rows : [];
  const rowsById = new Map(sourceRows.map((row) => [row.id, row]));
  if (kind === "daily") {
    [
      { id: "views:wb_funnel_views", label: "Витрина воронки WB", stage: "views", order: 315 },
      { id: "views:wb_abc_views", label: "Витрины ABC WB", stage: "views", order: 345 },
      { id: "views:wb_sku_views", label: "SKU-скоринг WB", stage: "views", order: 355 },
    ].forEach((row) => {
      if (!rowsById.has(row.id)) rowsById.set(row.id, row);
    });
  }
  if (kind === "avito" && rowsById.has("imports:avito_advertising")) {
    [
      { id: "imports:avito_account", label: "Аккаунт Avito", stage: "imports", order: 375 },
      { id: "imports:avito_balance", label: "Баланс Avito", stage: "imports", order: 376 },
      { id: "imports:avito_campaigns", label: "Кампании Avito", stage: "imports", order: 377 },
      { id: "imports:avito_groups", label: "Группы объявлений Avito", stage: "imports", order: 378 },
      { id: "imports:avito_creatives", label: "Объявления Avito", stage: "imports", order: 379 },
    ].forEach((row) => rowsById.set(row.id, row));
  }
  const rowLabelOverrides = new Map([
    ["imports:wb_market_search", "Спрос площадки WB · XLSX"],
    ["views:wb_market_search_views", "Витрина спроса площадки WB · XLSX"],
    ["imports:avito_advertising", "Дневная статистика Avito"],
  ]);
  const hiddenBundledRows = new Set(["views:ozon_views", "views:wb_views", "views:all_views"]);
  const rows = [...rowsById.values()]
    .filter((row) => !hiddenBundledRows.has(row.id))
    .map((row) => rowLabelOverrides.has(row.id) ? { ...row, label: rowLabelOverrides.get(row.id) } : row)
    .sort((left, right) => Number(left.order || 900) - Number(right.order || 900));
  const logs = Array.isArray(payload.logs) ? payload.logs : [];
  const terminal = logs.length
    ? logs.map((line) => `<div class="api-terminal-line is-${escapeHtml(line.type || "output")}"><span>${escapeHtml(line.time || "")}</span><code>${escapeHtml(line.text || "")}</code></div>`).join("")
    : `<div class="api-terminal-empty">${escapeHtml(options.emptyTerminal || "Терминал готов. Запустите процесс, чтобы увидеть ход обновления.")}</div>`;
  const message = current.client_label
    ? `${current.client_label} · ${current.report || ""}`
    : (payload.message || options.idleMessage || "Процесс ещё не запускался");
  const selectionLocked = ["running", "stopping"].includes(status);
  const renderSelectionMaster = (stageKey, client = "", label = "") => kind === "assortment"
    ? ""
    : `<label class="all-clients-master-select${client ? " is-client" : " is-matrix"}" title="${escapeHtml(client ? `Выбрать все доступные этапы ${label}` : "Выбрать все доступные этапы раздела")}">
        <input type="checkbox" data-all-clients-selection-master data-task-scope="${escapeHtml(selectionScope)}" data-master-stage="${escapeHtml(stageKey)}" ${client ? `data-master-client="${escapeHtml(client)}"` : ""} data-selection-locked="${selectionLocked ? "true" : "false"}" aria-label="${escapeHtml(client ? `Выбрать все доступные этапы ${label}` : "Выбрать все доступные этапы раздела")}" ${selectionLocked ? "disabled" : ""}>
      </label>`;
  const renderMatrix = (matrixRows, stageKey = "all") => `
    <div class="admin-all-clients-matrix-wrap" data-stage-matrix="${escapeHtml(stageKey)}">
      <table class="admin-all-clients-matrix">
        <thead><tr><th scope="col" class="admin-all-clients-row-master">${renderSelectionMaster(stageKey)}<span>Что обновляется</span></th>${payload.clients.map((client) => {
          const marketplaces = [...(clientMarketplaces.get(client.key) || [])];
          const marketplaceLabels = { wb: "WB", ozon: "Ozon", avito: "Avito" };
          const marketplaceLabel = marketplaces.map((value) => marketplaceLabels[value] || value).join(" + ") || "Нет источника";
          const controls = kind === "assortment" || options.showClientActions === false
            ? null
            : (payload.client_controls || {})[client.key];
          const clientActions = controls
            ? `<div class="admin-client-actions" data-client-actions="${escapeHtml(client.key)}">
                ${adminIconButton(`Запустить только ${client.label}`, "full", `data-start-client-daily="${escapeHtml(client.key)}"`, "is-mini", !controls.can_start)}
                ${adminIconButton(`Остановить ${client.label}`, "stop", `data-stop-client-daily="${escapeHtml(client.key)}"`, "is-mini danger", !controls.can_stop)}
                ${adminIconButton(`Возобновить ${client.label}`, "resume", `data-resume-client-daily="${escapeHtml(client.key)}"`, "is-mini", !controls.can_resume)}
              </div>`
            : "";
          const clientState = controls && controls.paused
            ? " · снят с прогона"
            : controls && controls.active
              ? " · выполняется"
              : "";
          return `<th scope="col" class="${controls && controls.paused ? "is-paused" : ""}" title="${escapeHtml(`${client.label} · ${marketplaceLabel}${clientState}`)}"><span>${escapeHtml(client.label)}</span><small>${escapeHtml(marketplaceLabel)}</small>${renderSelectionMaster(stageKey, client.key, client.label)}${clientActions}</th>`;
        }).join("")}</tr></thead>
        <tbody>${matrixRows.map((row) => `<tr><th scope="row"><span>${escapeHtml(row.label)}</span><small>${row.stage === "views" ? "Витрина" : "Данные"}</small></th>${payload.clients.map((client) => adminAllClientsCell(tasksForCell(row, client), { row, client, marketplaces: clientMarketplaces.get(client.key), selectable: kind !== "assortment", selectionScope, selectionDisabled: selectionLocked })).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
  const matrices = options.groupStages
    ? `
      <div class="admin-all-clients-stage-groups">
        <section class="admin-all-clients-stage-group is-imports" aria-label="Выгрузки и импорт">
          <header><strong>Выгрузки и импорт</strong><span>Каждый аккаунт начинает с первого доступного этапа и идёт сверху вниз независимо</span></header>
          ${renderMatrix(rows.filter((row) => row.stage !== "views"), "imports")}
        </section>
        <section class="admin-all-clients-stage-group is-views" aria-label="Витрины">
          <header><strong>Витрины</strong><span>Для аккаунта запускаются после его импортов; одновременно перестраиваются не более двух</span></header>
          ${renderMatrix(rows.filter((row) => row.stage === "views"), "views")}
        </section>
      </div>
    `
    : renderMatrix(rows);
  return `
    <section class="admin-all-clients-workspace is-${escapeHtml(kind)}" aria-label="${escapeHtml(options.title || "Обновление всех аккаунтов")}">
      <header class="admin-all-clients-toolbar">
        <div>
          <strong>${escapeHtml(options.title || "Обновление всех аккаунтов")}</strong>
          <span>${escapeHtml(options.description || "Процесс останавливается при первой ошибке")}</span>
          <div class="admin-all-clients-legend" aria-label="Тип источника">
            <span class="is-source-api"><i></i>API</span>
            <span class="is-source-manual"><i></i>Ручной файл</span>
            ${options.note ? `<span class="is-stock-history">${escapeHtml(options.note)}</span>` : ""}
          </div>
          ${options.capabilities ? `<div class="admin-all-clients-capabilities">${escapeHtml(options.capabilities)}</div>` : ""}
        </div>
        <div class="admin-daily-actions">
          ${adminIconButton("Запустить сначала", "full", `data-start-${actionSuffix}`, "", !payload.can_start)}
          ${adminIconButton("Остановить процесс", "stop", `data-stop-${actionSuffix}`, "danger", !payload.can_stop)}
          ${adminIconButton("Продолжить с ошибки", "resume", `data-resume-${actionSuffix}`, "", !payload.can_resume)}
        </div>
      </header>
      <div class="admin-all-clients-progress is-${escapeHtml(status)}">
        <div class="admin-all-clients-progress-copy">
          <strong>${escapeHtml(message)}</strong>
          <span>Этапы ${formatNumber(payload.completed || 0)} / ${formatNumber(payload.total || 0)}</span>
          <span>Время ${adminAllClientsDuration(payload.elapsed_seconds)}</span>
          <b>${formatNumber(progress, 1)}%</b>
        </div>
        <div class="admin-all-clients-progress-track" aria-label="Прогресс ${escapeHtml(String(progress))}%"><i style="width:${progress}%"></i></div>
      </div>
      ${matrices}
      ${options.hideTerminal ? "" : `<section class="api-export-terminal admin-all-clients-terminal" aria-label="${escapeHtml(options.terminalTitle || "Терминал процесса")}">
        <header><div><h3>${escapeHtml(options.terminalTitle || "Терминал процесса")}</h3><span>Live-вывод параллельных аккаунтов и этапов</span></div></header>
        <div class="api-export-terminal-body">${terminal}</div>
      </section>`}
    </section>
  `;
}

function adminAllClientsFilteredPayload(payload = {}, predicate = () => true) {
  if (!payload || !Array.isArray(payload.tasks)) return payload;
  const tasks = payload.tasks.filter(predicate);
  const taskIds = new Set(tasks.map((task) => task.id));
  const rowIds = new Set(tasks.map((task) => task.row_id));
  const clientIds = new Set(tasks.map((task) => task.client));
  const terminalStatuses = new Set(["ok", "limited", "skipped"]);
  const active = (payload.active || []).filter((item) => taskIds.has(item.task_id));
  const current = taskIds.has(payload.current?.task_id) ? payload.current : null;
  const completed = tasks.filter((task) => terminalStatuses.has(task.status)).length;
  const progressUnits = tasks.reduce((sum, task) => sum + (
    terminalStatuses.has(task.status) ? 1 : Math.max(0, Math.min(100, Number(task.progress_pct || 0))) / 100
  ), 0);
  const scopeIds = new Set(payload.scope_task_ids || []);
  const scopeTouchesSubset = !scopeIds.size || [...scopeIds].some((taskId) => taskIds.has(taskId));
  const subsetRunning = active.length > 0;
  const subsetStatus = subsetRunning
    ? payload.status
    : tasks.some((task) => task.status === "error")
      ? "error"
      : tasks.some((task) => task.status === "stopped")
        ? "stopped"
        : tasks.length && tasks.every((task) => terminalStatuses.has(task.status))
          ? "ok"
          : "idle";
  const hasRunHistory = tasks.some((task) => Number(task.attempts || 0) > 0);
  return {
    ...payload,
    clients: (payload.clients || []).filter((client) => clientIds.has(client.key)),
    rows: (payload.rows || []).filter((row) => rowIds.has(row.id)),
    tasks,
    active,
    current,
    total: tasks.length,
    completed,
    progress_pct: tasks.length ? Math.round((progressUnits / tasks.length) * 1000) / 10 : 0,
    message: current ? payload.message : "Готово к отдельному запуску",
    status: subsetStatus,
    elapsed_seconds: subsetRunning || hasRunHistory ? payload.elapsed_seconds : 0,
    can_start: Boolean(tasks.length && payload.can_start),
    can_stop: Boolean(payload.can_stop && subsetRunning),
    can_resume: Boolean(payload.can_resume && scopeTouchesSubset && tasks.some((task) => !terminalStatuses.has(task.status))),
  };
}

function renderAdminAllClientsDaily(payload = {}) {
  const mainPayload = adminAllClientsFilteredPayload(
    payload,
    (task) => task.row_id !== "imports:avito_advertising",
  );
  const avitoPayload = adminAllClientsFilteredPayload(
    payload,
    (task) => task.row_id === "imports:avito_advertising",
  );
  return `
    <div class="admin-all-clients-page">
      ${renderAdminAllClientsRunner(mainPayload, {
        kind: "daily",
        selectionScope: "daily",
        title: "Ежедневные данные",
        description: "Аккаунты обновляются параллельно; внутри каждого этапы идут сверху вниз",
        groupStages: true,
        note: "Остатки: новый снимок за день, история сохраняется",
        terminalTitle: "Терминал ежедневного процесса",
      })}
      ${renderAdminAllClientsRunner(avitoPayload, {
        kind: "avito",
        selectionScope: "avito",
        title: "Ежедневное обновление Avito Ads",
        description: "Только кабинеты с полной тройкой Account ID, Client ID и Client Secret",
        note: "Только чтение · окно статистики до 100 дней",
        capabilities: "6 методов одним запуском: аккаунт · баланс · кампании · группы · объявления · дневная статистика (показы, клики, CTR, расходы и бонусы, CPM, CPC, видео 25/50/75/100%, VTR)",
        idleMessage: "Avito Ads ещё не запускался отдельным прогоном",
        hideTerminal: true,
        showClientActions: false,
      })}
      ${renderAdminAllClientsRunner(state.adminAllClientsAssortment, {
        kind: "assortment",
        title: "Ассортимент и характеристики",
        description: "Отдельное тяжёлое обновление карточек Ozon и WB; не блокирует состав ежедневного плана",
        idleMessage: "Ассортимент ещё не обновлялся отдельным запуском",
        terminalTitle: "Терминал ассортимента",
      })}
    </div>
  `;
}

function scheduleAdminAllClientsDailyPoll() {
  window.clearTimeout(state.adminAllClientsPollTimer);
  state.adminAllClientsPollTimer = 0;
  if (state.dashboard !== "admin" || state.adminImportMode !== "allDaily") return;
  if (!["running", "stopping"].includes(state.adminAllClientsDaily?.status)) return;
  state.adminAllClientsPollTimer = window.setTimeout(() => {
    loadAdminAllClientsDaily(true).catch((error) => setStatusError(error));
  }, 1000);
}

async function loadAdminAllClientsDaily(rerender = false) {
  state.adminAllClientsDaily = await getJson("/api/admin/all-clients-daily");
  if (rerender && state.dashboard === "admin" && state.adminImportMode === "allDaily") {
    renderAdminDashboardContent(lastAdminPayload);
  }
  scheduleAdminAllClientsDailyPoll();
  return state.adminAllClientsDaily;
}

async function runAdminAllClientsDailyClient(action, client) {
  const taskIds = action === "stop" ? [] : selectedAdminAllClientsTaskIds(client, "daily");
  const payload = { client };
  // Пустой выбор в шапке аккаунта означает запуск всей его цепочки.
  // Если пользователь отметил чекбоксы, ограничиваем запуск выбранными этапами.
  if (taskIds.length) payload.task_ids = taskIds;
  state.adminAllClientsDaily = await postJson(`/api/admin/all-clients-daily/client/${action}`, payload);
  renderAdminDashboardContent(lastAdminPayload);
  scheduleAdminAllClientsDailyPoll();
}

async function runAdminAllClientsDaily(action) {
  const path = action === "resume" ? "resume" : action === "stop" ? "stop" : "start";
  const taskIds = action === "stop" ? [] : selectedAdminAllClientsTaskIds("", "daily");
  if (action !== "stop" && !taskIds.length) throw new Error("Выберите хотя бы один этап");
  state.adminAllClientsDaily = await postJson(`/api/admin/all-clients-daily/${path}`, { task_ids: taskIds });
  renderAdminDashboardContent(lastAdminPayload);
  scheduleAdminAllClientsDailyPoll();
}

async function runAdminAllClientsAvito(action) {
  const path = action === "resume" ? "resume" : action === "stop" ? "stop" : "start";
  const taskIds = action === "stop" ? [] : selectedAdminAllClientsTaskIds("", "avito");
  if (action !== "stop" && !taskIds.length) throw new Error("Выберите хотя бы один кабинет Avito");
  state.adminAllClientsDaily = await postJson(`/api/admin/all-clients-daily/${path}`, { task_ids: taskIds });
  renderAdminDashboardContent(lastAdminPayload);
  scheduleAdminAllClientsDailyPoll();
}

function selectedAdminAllClientsTaskIds(client = "", scope = "") {
  const ids = new Set();
  document.querySelectorAll("[data-all-clients-task-select]:checked").forEach((checkbox) => {
    if (client && checkbox.dataset.client !== client) return;
    if (scope && checkbox.dataset.taskScope !== scope) return;
    String(checkbox.dataset.taskIds || "").split("|").filter(Boolean).forEach((taskId) => ids.add(taskId));
  });
  return [...ids];
}

function adminAllClientsMasterTargets(master) {
  const matrix = master.closest("[data-stage-matrix]");
  if (!matrix) return [];
  const scope = master.dataset.taskScope || "";
  const client = master.dataset.masterClient || "";
  return [...matrix.querySelectorAll("[data-all-clients-task-select]")].filter((checkbox) => (
    !checkbox.disabled
    && (!scope || checkbox.dataset.taskScope === scope)
    && (!client || checkbox.dataset.client === client)
  ));
}

function syncAdminAllClientsSelectionMasters(root = document) {
  root.querySelectorAll("[data-all-clients-selection-master]").forEach((master) => {
    const targets = adminAllClientsMasterTargets(master);
    const checkedCount = targets.filter((checkbox) => checkbox.checked).length;
    master.checked = targets.length > 0 && checkedCount === targets.length;
    master.indeterminate = checkedCount > 0 && checkedCount < targets.length;
    master.disabled = master.dataset.selectionLocked === "true" || targets.length === 0;
  });
}

function scheduleAdminAllClientsAssortmentPoll() {
  window.clearTimeout(state.adminAllClientsAssortmentPollTimer);
  state.adminAllClientsAssortmentPollTimer = 0;
  if (state.dashboard !== "admin" || state.adminImportMode !== "allDaily") return;
  if (!["running", "stopping"].includes(state.adminAllClientsAssortment?.status)) return;
  state.adminAllClientsAssortmentPollTimer = window.setTimeout(() => {
    loadAdminAllClientsAssortment(true).catch((error) => setStatusError(error));
  }, 1000);
}

async function loadAdminAllClientsAssortment(rerender = false) {
  state.adminAllClientsAssortment = await getJson("/api/admin/all-clients-assortment");
  if (rerender && state.dashboard === "admin" && state.adminImportMode === "allDaily") {
    renderAdminDashboardContent(lastAdminPayload);
  }
  scheduleAdminAllClientsAssortmentPoll();
  return state.adminAllClientsAssortment;
}

async function runAdminAllClientsAssortment(action) {
  const path = action === "resume" ? "resume" : action === "stop" ? "stop" : "start";
  state.adminAllClientsAssortment = await postJson(`/api/admin/all-clients-assortment/${path}`, {});
  renderAdminDashboardContent(lastAdminPayload);
  scheduleAdminAllClientsAssortmentPoll();
}

function renderAdminDashboardContent(payload) {
  const clientEditorDraft = captureAdminClientEditorDraft();
  lastAdminPayload = payload;
  if (!state.adminApiDateFrom) state.adminApiDateFrom = payload.api_default_date_from || "";
  if (!state.adminApiDateTo) state.adminApiDateTo = payload.api_default_date_to || "";
  if (payload.full_admin || state.adminFullAccess) state.adminAllowedSections = adminSectionKeys();
  else if (Array.isArray(payload.allowed_admin_sections)) state.adminAllowedSections = payload.allowed_admin_sections;
  state.adminImportMode = normalizeAdminModeForAccess(payload);
  const rows = adminRowsForMode(payload);
  const isDaily = state.adminImportMode === "daily";
  const isAllDaily = state.adminImportMode === "allDaily";
  const isApiDaily = state.adminImportMode === "apiDaily";
  const isDailyLike = isDaily || isApiDaily;
  const isApi = state.adminImportMode === "api";
  const isClients = state.adminImportMode === "client";
  const isClientOnboarding = state.adminImportMode === "clientOnboarding";
  const isDatabase = state.adminImportMode === "database";
  const isIntegrations = state.adminImportMode === "integrations";
  const isUsers = state.adminImportMode === "users";
  const showAdminTerminal = !isAllDaily && !isApi && !isClients && !isClientOnboarding && !isDatabase && !isIntegrations && !isUsers;
  const dailyGroups = isDailyLike ? adminDailyGroups(payload) : null;
  const dailyMarketplaceLabel = isDaily ? adminDailyMarketplaceLabel(payload) : "";
  const dailyRunDisabled = state.adminImportRunning || !canRunAdminClient(payload);
  qs("primaryChartTitle").textContent = isUsers ? "Пользователи" : isIntegrations ? "Интеграции" : isDatabase ? "БД" : isClients ? "Клиенты" : isClientOnboarding ? "Добавление магазина" : isApi ? "Выгрузка API" : isApiDaily ? "Ежедневная выгрузка API" : isAllDaily ? "Обновление всех аккаунтов" : isDaily ? "Ежедневный импорт выгрузок" : "Каталог загрузок";
  qs("chartMetric").textContent = isUsers ? "логины и права доступа" : isIntegrations ? "общие API-ключи внешних сервисов" : isDatabase ? "структура PostgreSQL, таблицы, поля, строки, вес и матвитрины" : isClients ? "реестр клиентов, отчёты и доступы" : isClientOnboarding ? "токены, первичная настройка и исторические данные" : isApi ? "Ozon и WB API в отдельных подразделах" : isApiDaily ? "Ozon и WB: воронка, остатки, реклама и витрины KM Trade" : isAllDaily ? "матрица ежедневных процессов по всем подключённым аккаунтам" : isDaily ? "последовательный запуск ежедневных скриптов" : "скрипты, источники и витрины";
  syncAdminTopbarTabs(payload);
  if (isAllDaily) {
    qs("barChart").innerHTML = renderAdminAllClientsDaily(state.adminAllClientsDaily);
    syncAdminAllClientsSelectionMasters(qs("barChart"));
    qs("barChart").querySelectorAll(".admin-all-clients-terminal .api-export-terminal-body").forEach((terminalBody) => {
      terminalBody.scrollTop = terminalBody.scrollHeight;
    });
    scheduleAdminAllClientsDailyPoll();
    scheduleAdminAllClientsAssortmentPoll();
    return;
  }
  qs("barChart").innerHTML = `
    ${isClients || isClientOnboarding || isDatabase || isIntegrations || isUsers ? "" : renderAdminClientSelector(payload)}
    ${isDailyLike ? `
      <div class="admin-daily-head">
        <div>
          <strong>${isApiDaily ? "Цепочка ежедневного API-обновления KM Trade" : `Полный ежедневный пайплайн · ${dailyMarketplaceLabel}`}</strong>
          <span>${isApiDaily ? "Read-only API-запросы выполняются последовательно; после загрузки пересобираются только KM-витрины." : `Все подключённые площадки клиента: импорты выполняются в серверном порядке, затем пересобираются их витрины.`}</span>
        </div>
        <div class="admin-daily-actions">
          ${adminIconButton("Запустить полный процесс", "full", 'data-run-daily-chain="all"', "", dailyRunDisabled)}
          ${adminIconButton("Запустить только импорт", "imports", 'data-run-daily-chain="imports"', "ghost", dailyRunDisabled)}
          ${adminIconButton("Обновить витрины", "views", 'data-run-daily-chain="views"', "ghost", dailyRunDisabled)}
          ${adminIconButton("Остановить скрипты", "stop", "data-stop-admin-import", "ghost danger", !(state.adminImportRunning && state.adminCurrentImportKey && !state.adminStopRequested))}
        </div>
      </div>
      ${isApiDaily ? `
        <div class="admin-api-settings">
          <div class="admin-api-date-grid">
            <label>Дата с<input id="adminApiDateFrom" type="date" value="${escapeHtml(state.adminApiDateFrom)}" max="${escapeHtml(payload.api_default_date_to || "")}" /></label>
            <label>Дата по<input id="adminApiDateTo" type="date" value="${escapeHtml(state.adminApiDateTo)}" max="${escapeHtml(payload.api_default_date_to || "")}" /></label>
          </div>
          <div class="admin-api-credential-grid">
            <div>
              <strong>Seller API</strong>
              <span>${payload.ozon_seo?.credentials_saved ? "Client-Id и Api-Key сохранены" : "Ключи не сохранены"}</span>
            </div>
            <div>
              <strong>Performance API</strong>
              <span>${payload.ozon_performance?.credentials_saved ? "Client ID и Client Secret сохранены" : "Нужны отдельные ключи для рекламы"}</span>
            </div>
            <div>
              <strong>WB Analytics API</strong>
              <span>${payload.wb_api?.token_saved ? "Отдельный токен KM Trade сохранён" : "Нужен токен категории «Аналитика»"}</span>
            </div>
            <label>WB API token<input id="wbApiTokenInput" type="password" autocomplete="off" placeholder="${payload.wb_api?.token_saved ? "Токен сохранён; введите новый для замены" : "WB_API_TOKEN_KM_TRADE"}" /></label>
            <button type="button" data-save-wb-api-token>Сохранить WB токен</button>
            <div id="wbApiTokenStatus"></div>
            <span id="wbApiTokenBadge" class="wb-api-badge ${payload.wb_api?.token_saved ? "is-saved" : ""}">${payload.wb_api?.token_saved ? "Ключ сохранен" : "Ключ не сохранен"}</span>
            <label>Performance Client ID<input id="ozonPerformanceClientId" value="${escapeHtml(payload.ozon_performance?.client_id || "")}" autocomplete="off" /></label>
            <label>Performance Client Secret<input id="ozonPerformanceClientSecret" type="password" autocomplete="off" placeholder="${payload.ozon_performance?.client_secret_saved ? "Секрет сохранён; введите новый для замены" : "Client Secret"}" /></label>
            <button type="button" data-save-ozon-performance-credentials>Сохранить Performance ключи</button>
          </div>
        </div>
      ` : ""}
      ${renderAdminTimeline(rows)}
    ` : ""}
    ${showAdminTerminal ? renderAdminTerminal() : ""}
    ${isDailyLike ? `
      <section class="admin-daily-section">
        <header><h3>${isApiDaily ? "Загрузка через API" : "Импорт выгрузок"}</h3><span>${isApiDaily ? "Воронка, текущий снимок остатков и товарная реклама" : "Файлы и ежедневные отчеты из папок источников"}</span></header>
        ${renderAdminImportTable(dailyGroups.imports, true)}
      </section>
      <section class="admin-daily-section">
        <header><h3>Обновление витрин</h3><span>Пересборка materialized views и зависимых отчетов после импорта</span></header>
        ${renderAdminImportTable(dailyGroups.views, true)}
      </section>
    ` : isApi ? renderApiExportsDashboard(payload) : isClients ? renderAdminClientsDashboard() : isClientOnboarding ? renderAddClientDashboard() : isDatabase ? renderAdminDatabaseDashboard() : isIntegrations ? renderAdminIntegrationsDashboard() : isUsers ? renderAdminUsersDashboard() : renderAdminImportTable(rows, false)}
  `;
  restoreAdminClientEditorDraft(clientEditorDraft);
  updateAdminTerminal();
  updateApiTerminal();
  attachWbAnalyticsFieldHelp(qs("barChart"));
  syncAdminPermissionDropdowns(qs("barChart"));
}

async function renderAdminDashboard() {
  const payload = state.adminFullAccess
    ? await getJson(`/api/admin/imports?client=${encodeURIComponent(state.adminClient || "toptop")}`)
    : {
        full_admin: false,
        allowed_admin_sections: [...state.adminAllowedSections],
        api_daily_enabled: false,
        client: "",
      };
  state.adminClient = payload.client || state.adminClient || "toptop";
  qs("dashboardTitle").textContent = "Админка";
  qs("status").textContent = "Управление локальными импортами";
  qs("backToAbc").classList.add("hidden");
  qs("exportExcel").classList.add("hidden");
  document.querySelector(".filters").classList.add("hidden");
  document.querySelector(".kpis").classList.add("hidden");
  const visuals = document.querySelector(".visuals");
  visuals.classList.remove("hidden");
  visuals.classList.add("adv-dashboard", "admin-dashboard");
  qs("abcChart").closest(".panel").classList.add("hidden");
  qs("barChart").classList.remove("bar-chart");
  renderAdminDashboardContent(payload);
  if (state.adminImportMode === "allDaily") {
    try {
      await Promise.all([loadAdminAllClientsDaily(false), loadAdminAllClientsAssortment(false)]);
      renderAdminDashboardContent(payload);
    } catch (error) {
      setStatusError(error);
    }
  }
  if (["client", "clientOnboarding"].includes(state.adminImportMode) && !state.adminClientRegistry) {
    try {
      await loadAdminClientRegistry();
      renderAdminDashboardContent(payload);
      if (state.adminImportMode === "clientOnboarding") await restoreAdminClientHistoryContext();
      if (state.adminImportMode === "client") await restoreAdminClientAssortmentContext();
    } catch (error) {
      setStatusError(error);
    }
  }
  if (state.adminImportMode === "users" && !state.adminUsersRegistry) {
    try {
      await loadAdminUsersRegistry();
      renderAdminDashboardContent(payload);
    } catch (error) {
      setStatusError(error);
    }
  }
  if (state.adminImportMode === "database" && !state.adminDatabaseOverview) {
    try {
      await loadAdminDatabaseOverview();
      renderAdminDashboardContent(payload);
    } catch (error) {
      setStatusError(error);
    }
  }
  if (state.adminImportMode === "integrations" && !state.adminIntegrations) {
    try {
      await loadAdminIntegrations();
      renderAdminDashboardContent(payload);
    } catch (error) {
      setStatusError(error);
    }
  }
  setTableVisible(false);
}

function adminResultText(result) {
  const summary = result.summary || {};
  const parts = [];
  if (summary.date) parts.push(`дата ${summary.date}`);
  if (summary.rows !== null && summary.rows !== undefined) parts.push(`${formatNumber(summary.rows)} строк`);
  if (summary.errors !== null && summary.errors !== undefined) parts.push(`ошибок ${formatNumber(summary.errors)}`);
  if (!parts.length && result.ok) return "Готово, детали в выводе скрипта";
  if (!parts.length) return result.error || "Ошибка импорта";
  return `${result.ok ? "Загружено" : "Ошибка"}: ${parts.join(", ")}`;
}

function adminResultLog(result) {
  const summary = result.summary || {};
  const lines = String(summary.tail || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length) return lines.slice(-2).join(" | ").slice(0, 240);
  return result.ok ? "Скрипт завершился без подробного вывода" : (result.error || "Скрипт завершился с ошибкой");
}

async function streamAdminImport(row, options = {}) {
  const params = new URLSearchParams({
    client: state.adminClient || "toptop",
    key: row.key,
  });
  if (state.adminImportMode === "apiDaily") {
    if (state.adminApiDateFrom) params.set("date_from", state.adminApiDateFrom);
    if (state.adminApiDateTo) params.set("date_to", state.adminApiDateTo);
  }
  if (options.deferViews) {
    params.set("chain", "1");
    params.set("defer_views", "1");
  }
  const response = await appFetch(`/api/admin/run-import-stream?${params.toString()}`);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Браузер не отдал поток вывода скрипта");
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let outputCount = 0;
  let progressEventCount = 0;
  let finalResult = null;
  const renderStreamProgress = () => {
    if (!["manual", "daily", "apiDaily"].includes(state.adminImportMode)) return;
    renderAdminDashboardContent(lastAdminPayload);
  };

  const handleEvent = (event) => {
    if (!event || !event.event) return;
    if (event.event === "start") {
      appendAdminTerminalLine(`> ${event.report}`, "start");
      appendAdminTerminalLine(`client: ${event.client_label || event.client || ""} (${event.client || ""})`, "meta");
      appendAdminTerminalLine(`database: ${event.db_name || ""}`, "meta");
      appendAdminTerminalLine(`source: ${event.source || ""}`, "meta");
      appendAdminTerminalLine(`script: ${event.script}`, "meta");
      appendAdminTerminalLine(`updates: ${event.destination}`, "meta");
      appendAdminTerminalLine(`cwd: ${event.cwd || ""}`, "meta");
      appendAdminTerminalLine(`command: ${event.command || ""}`, "meta");
    }
    if (event.event === "output") {
      outputCount += 1;
      const progress = adminProgressFromLine(event.line || "");
      const currentStatus = state.adminImportStatuses[row.key] || {};
      if (progress && String(event.line || "").includes("ПРОГРЕСС")) {
        updateAdminLiveProgress(row, event.line || "", progress);
        state.adminImportStatuses[row.key] = {
          ...currentStatus,
          state: "active",
          log: String(event.line).trim().slice(0, 240),
          progressPct: progress.progressPct ?? currentStatus.progressPct,
          progressText: progress.progressText || currentStatus.progressText || "В работе",
        };
        renderStreamProgress();
        return;
      }
      appendAdminTerminalLine(event.line || "", "output");
      if ((event.line || "").trim()) {
        state.adminImportStatuses[row.key] = {
          ...currentStatus,
          state: "active",
          log: String(event.line).trim().slice(0, 240),
          progressPct: progress?.progressPct ?? currentStatus.progressPct,
          progressText: progress?.progressText || currentStatus.progressText || "В работе",
        };
        if (outputCount % 5 === 0) renderStreamProgress();
      }
    }
    if (event.event === "progress") {
      progressEventCount += 1;
      const progress = adminProgressFromLine(event.line || "");
      const currentStatus = state.adminImportStatuses[row.key] || {};
      updateAdminLiveProgress(row, event.line || "", progress);
      state.adminImportStatuses[row.key] = {
        ...currentStatus,
        state: "active",
        log: String(event.line || "").trim().slice(0, 240),
        progressPct: progress?.progressPct ?? currentStatus.progressPct,
        progressText: progress?.progressText || currentStatus.progressText || "В работе",
      };
      if (progressEventCount % 10 === 0) renderStreamProgress();
    }
    if (event.event === "heartbeat") {
      const minutes = Math.max(1, Math.floor(Number(event.elapsed_sec || 0) / 60));
      const currentStatus = state.adminImportStatuses[row.key] || {};
      const rawLog = String(currentStatus.log || "");
      const lastConcreteLog = rawLog.includes(" | Последнее: ")
        ? rawLog.split(" | Последнее: ").slice(1).join(" | Последнее: ")
        : rawLog && !rawLog.startsWith("Скрипт работает, ждем") && !rawLog.startsWith("Работает ")
          ? rawLog
          : "";
      const heartbeatLog = lastConcreteLog
        ? `Работает ${minutes} мин. | Последнее: ${lastConcreteLog}`
        : `Скрипт работает, ждем вывод первого статуса (${minutes} мин.)`;
      state.adminImportStatuses[row.key] = {
        ...currentStatus,
        state: "active",
        log: heartbeatLog,
        progressText: currentStatus.progressText || `Работает ${minutes} мин.`,
      };
      updateAdminLiveProgress(row, heartbeatLog, {
        progressPct: currentStatus.progressPct,
        progressText: currentStatus.progressText || `Работает ${minutes} мин.`,
        elapsedText: `прошло ${minutes} мин.`,
      });
      renderStreamProgress();
    }
    if (event.event === "done") {
      finalResult = event;
      updateAdminLiveProgress(row, event.ok ? "Готово" : event.stopped ? "Остановлено" : event.error || "Ошибка", {
        progressPct: event.ok ? 100 : state.adminLiveProgress?.progressPct,
        progressText: event.ok ? "100%" : event.stopped ? "Остановлено" : "Ошибка",
      }, event.ok ? "done" : event.stopped ? "stopped" : "error");
      appendAdminTerminalLine(
        event.ok ? `✓ ${event.report}: завершено` : event.stopped ? `Остановлено: ${event.report}` : `✕ ${event.report}: ${event.error || "ошибка"}`,
        event.ok ? "done" : event.stopped ? "meta" : "error",
      );
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    lines.forEach((line) => {
      if (!line.trim()) return;
      handleEvent(JSON.parse(line));
    });
  }
  buffer += decoder.decode();
  if (buffer.trim()) handleEvent(JSON.parse(buffer));
  if (!finalResult) throw new Error("Скрипт завершился без финального статуса");
  return finalResult;
}

async function runAdminImportInline(row, options = {}) {
  const isView = adminDailyStage(row) === "views";
  state.adminCurrentImportKey = row.key;
  state.adminLiveProgress = {
    state: "active",
    title: row.report,
    progressPct: 0,
    progressText: "0%",
    rowsText: "строки 0 / ?",
    elapsedText: "прошло 00:00:00",
    line: `Запущен скрипт: ${row.report}`,
  };
  state.adminImportStatuses[row.key] = {
    state: "active",
    title: isView ? "Обновляется витрина" : "Импортируется",
    detail: `Сейчас: ${row.report}`,
    log: `Запущен скрипт. Обновляет: ${row.destination}`,
    progressPct: 0,
    progressText: "0%",
  };
  renderAdminDashboardContent(lastAdminPayload);
  const result = await streamAdminImport(row, options);
  state.adminImportStatuses[row.key] = {
    state: result.ok ? "done" : result.stopped ? "stopped" : "error",
    title: result.ok ? "Готово" : result.stopped ? "Остановлено" : "Ошибка",
    detail: adminResultText(result),
    log: adminResultLog(result),
    progressPct: result.ok ? 100 : state.adminImportStatuses[row.key]?.progressPct,
    progressText: result.ok ? "100%" : result.stopped ? "Остановлено" : state.adminImportStatuses[row.key]?.progressText,
  };
  renderAdminDashboardContent(lastAdminPayload);
  return result;
}

async function stopAdminImport() {
  if (!state.adminImportRunning || !state.adminCurrentImportKey) return;
  const requestedKey = state.adminCurrentImportKey;
  state.adminStopRequested = true;
  appendAdminTerminalLine("Запрошена остановка текущего скрипта и всей оставшейся цепочки", "meta");
  renderAdminDashboardContent(lastAdminPayload);
  const result = await postJson("/api/admin/stop-import", {
    client: state.adminClient || "toptop",
    key: requestedKey,
  });
  const resolvedKey = result.key || requestedKey;
  if (result.ok) {
    const stopped = result.state === "stopped";
    state.adminImportStatuses[resolvedKey] = {
      ...(state.adminImportStatuses[resolvedKey] || {}),
      state: stopped ? "stopped" : "stopping",
      title: stopped ? "Остановлено" : "Останавливается",
      detail: result.message || (stopped ? "Процесс остановлен" : "Ожидаем завершения процесса"),
      log: `PID ${result.pid || "ожидается"}: ${result.message || "остановка запрошена"}`,
      progressText: stopped ? "Остановлено" : "Остановка...",
    };
    appendAdminTerminalLine(`${result.message || "Остановка отправлена"}: PID ${result.pid || "ожидается"}`, "meta");
  } else {
    appendAdminTerminalLine(`Не удалось завершить процесс: ${result.error || "нет активного процесса"}. Следующие скрипты запускаться не будут.`, "error");
  }
  renderAdminDashboardContent(lastAdminPayload);
  return result;
}

function captureAdminApiDates() {
  const fromInput = qs("adminApiDateFrom");
  const toInput = qs("adminApiDateTo");
  if (fromInput) state.adminApiDateFrom = fromInput.value || "";
  if (toInput) state.adminApiDateTo = toInput.value || "";
  if (state.adminImportMode === "apiDaily" && state.adminApiDateFrom && state.adminApiDateTo && state.adminApiDateFrom > state.adminApiDateTo) {
    throw new Error("Дата начала API-выгрузки позже даты окончания");
  }
}

async function saveOzonPerformanceCredentialsFromUi(button) {
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "Сохраняю...";
  try {
    const payload = await postJson("/api/admin/ozon-performance/credentials", {
      client: state.adminClient || "km_trade",
      client_id: qs("ozonPerformanceClientId")?.value || "",
      client_secret: qs("ozonPerformanceClientSecret")?.value || "",
    });
    if (lastAdminPayload) lastAdminPayload.ozon_performance = payload;
    qs("status").textContent = "Performance API ключи KM Trade сохранены";
    renderAdminDashboardContent(lastAdminPayload);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function dailyRowsForRun(scope = "all") {
  if (!lastAdminPayload) return [];
  const groups = adminDailyGroups(lastAdminPayload);
  if (scope === "imports") return groups.imports;
  if (scope === "views") return groups.views;
  return [...groups.imports, ...groups.views];
}

async function runDailyImportChain(scope = "all") {
  if (state.adminImportRunning || !lastAdminPayload) return;
  captureAdminApiDates();
  if (!canRunAdminClient(lastAdminPayload)) {
    qs("status").textContent = "Для выбранного клиента ежедневный импорт еще не подключен";
    return;
  }
  const rows = dailyRowsForRun(scope);
  state.adminImportRunning = true;
  state.adminCurrentImportKey = "";
  state.adminStopRequested = false;
  state.adminImportStatuses = {};
  state.adminTerminalLines = [];
  state.adminLiveProgress = null;
  appendAdminTerminalLine(scope === "views" ? "Запуск обновления витрин" : scope === "imports" ? "Запуск импорта выгрузок" : "Запуск полного ежедневного процесса", "start");
  rows.forEach((row) => {
    state.adminImportStatuses[row.key] = {
      state: "queued",
      title: "В очереди",
      detail: "Ждет предыдущие импорты",
      log: "Ожидает запуска по цепочке",
      progressPct: 0,
      progressText: "В очереди",
    };
  });
  renderAdminDashboardContent(lastAdminPayload);
  try {
    for (const row of rows) {
      if (state.adminStopRequested) break;
      const result = await runAdminImportInline(row, { deferViews: adminDailyStage(row) === "imports" });
      if (result.stopped) {
        state.adminStopRequested = true;
        qs("status").textContent = `Остановлено: ${row.report}`;
        break;
      }
      if (!result.ok) {
        state.adminStopRequested = true;
        qs("status").textContent = `Ошибка: ${row.report}`;
        break;
      }
    }
    if (!state.adminStopRequested) {
      qs("status").textContent = scope === "views" ? "Обновление витрин завершено" : scope === "imports" ? "Импорт выгрузок завершен" : "Ежедневный импорт и обновление витрин завершены";
    }
  } finally {
    state.adminImportRunning = false;
    state.adminCurrentImportKey = "";
    renderAdminDashboardContent(lastAdminPayload);
  }
}

function setWbPromotionCountResult(message, stateName = "idle") {
  const result = qs("wbPromotionCountResult");
  if (!result) return;
  result.className = `wb-api-result is-${stateName}`;
  result.textContent = message;
}

function setWbPromotionAdvertsResult(message, stateName = "idle") {
  const result = qs("wbPromotionAdvertsResult");
  if (!result) return;
  result.className = `wb-api-result is-${stateName}`;
  result.textContent = message;
}

function setWbPromotionStatsResult(message, stateName = "idle") {
  const result = qs("wbPromotionStatsResult");
  if (!result) return;
  result.className = `wb-api-result is-${stateName}`;
  result.textContent = message;
}


function setWbContentResult(elementId, message, stateName = "idle") {
  const result = qs(elementId);
  if (!result) return;
  result.className = `wb-api-result is-${stateName}`;
  result.textContent = message;
}

async function validateClientPathsFromUi(button) {
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "Проверяю...";
  const paths = Object.fromEntries(
    Array.from(document.querySelectorAll("[data-client-path]"))
      .map((input) => [input.dataset.clientPath, input.value || ""])
  );
  try {
    const payload = await postJson("/api/admin/client-paths/validate", { paths });
    const result = qs("clientPathValidationResult");
    result.innerHTML = `
      <strong>${payload.ok ? "Все пути найдены" : "Есть отсутствующие пути"}</strong>
      <div class="client-path-list">
        ${(payload.rows || []).map((row) => `
          <div class="${row.exists ? "is-ok" : "is-error"}">
            <span>${escapeHtml(row.key)}</span>
            <code>${escapeHtml(row.path)}</code>
          </div>
        `).join("")}
      </div>
    `;
    qs("status").textContent = payload.ok ? "Пути клиента проверены" : "Часть путей клиента не найдена";
  } catch (error) {
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function saveWbApiTokenFromUi(button) {
  const input = qs("wbApiTokenInput");
  const token = input?.value?.trim() || "";
  const status = qs("wbApiTokenStatus");
  if (!token) {
    if (status) status.textContent = "Вставьте WB API ключ перед сохранением";
    return;
  }
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Сохраняю...";
  try {
    const payload = await postJson("/api/admin/wb-api/token", { token, client: state.adminClient || "toptop" });
    input.value = "";
    if (lastAdminPayload?.wb_api) lastAdminPayload.wb_api.token_saved = true;
    qs("wbApiTokenBadge").textContent = "Ключ сохранен";
    qs("wbApiTokenBadge").classList.add("is-saved");
    if (status) status.textContent = `Сохранено в ${payload.token_env || "WB_API_TOKEN"}`;
    qs("status").textContent = "WB API ключ сохранен";
  } catch (error) {
    if (status) status.textContent = `Ошибка сохранения: ${error.message}`;
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function runWbPromotionCountFromUi(button) {
  const oldText = button.textContent;
  setWbApiRunControls("promotion_count", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastPromotionCountResultFile = "";
  appendApiTerminalLine("> WB Promotion Count: запуск GET /adv/v1/promotion/count", "start");
  appendApiTerminalLine("Read-only GET к advert-api.wildberries.ru", "meta");
  setWbPromotionCountResult("Запрашиваю ID кампаний обычной рекламы WB...", "active");
  try {
    const payload = await postJson("/api/admin/wb-api/promotion/count", {});
    const files = payload.files || {};
    setApiExportLogFile("promotion_count", payload.log_file || files.log);
    state.wbApiLastPromotionCountResultFile = files.xlsx || "";
    appendWbApiStoppedLine(payload);
    appendApiTerminalLine(`Получено кампаний: ${formatNumber(payload.count)}`, "done");
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (payload.log_file || files.log) appendApiTerminalLine(`LOG: ${payload.log_file || files.log}`, "meta");
    setWbPromotionCountResult(`${wbApiResultPrefix(payload)}${formatNumber(payload.count)} кампаний. JSON: ${files.json || ""} XLSX: ${files.xlsx || ""}`, wbApiResultState(payload));
    const openButton = document.querySelector("[data-open-wb-promotion-count-result]");
    if (openButton) openButton.disabled = !state.wbApiLastPromotionCountResultFile;
    qs("status").textContent = "WB Promotion count сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbPromotionCountResult(`Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    setWbApiRunControls("promotion_count", button, false, oldText);
  }
}

async function runWbPromotionAdvertsFromUi(button) {
  const statuses = qs("wbPromotionAdvertsStatuses")?.value?.trim() || "9,11,7";
  const oldText = button.textContent;
  setWbApiRunControls("promotion_adverts", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastPromotionAdvertsResultFile = "";
  appendApiTerminalLine("> WB Promotion Campaigns: count -> v2/adverts", "start");
  appendApiTerminalLine(`Статусы: ${statuses}; ID берутся из /adv/v1/promotion/count`, "meta");
  appendApiTerminalLine("Detail-запросы идут пачками до 50 ID; используются только GET endpoints", "meta");
  setWbPromotionAdvertsResult("Запрашиваю карточки кампаний обычной рекламы WB...", "active");
  try {
    const result = await postJson("/api/admin/wb-api/promotion/adverts", { statuses });
    const files = result.files || {};
    setApiExportLogFile("promotion_adverts", result.log_file || files.log);
    state.wbApiLastPromotionAdvertsResultFile = files.xlsx || "";
    appendWbApiStoppedLine(result);
    appendApiTerminalLine(`ID кампаний из count: ${formatNumber(result.campaign_ids_count || 0)}`, "done");
    appendApiTerminalLine(`Детальных кампаний сохранено: ${formatNumber(result.count || 0)}; запросов: ${formatNumber(result.requests || 0)}`, "meta");
    if (files.count_xlsx) appendApiTerminalLine(`COUNT XLSX: ${files.count_xlsx}`, "meta");
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (result.log_file || files.log) appendApiTerminalLine(`LOG: ${result.log_file || files.log}`, "meta");
    setWbPromotionAdvertsResult(
      `${wbApiResultPrefix(result)}${formatNumber(result.count || 0)} кампаний, ID из count: ${formatNumber(result.campaign_ids_count || 0)}, запросов: ${formatNumber(result.requests || 0)}. XLSX: ${files.xlsx || ""}`,
      wbApiResultState(result),
    );
    const openButton = document.querySelector("[data-open-wb-promotion-adverts-result]");
    if (openButton) openButton.disabled = !state.wbApiLastPromotionAdvertsResultFile;
    qs("status").textContent = "WB Promotion adverts сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbPromotionAdvertsResult(`Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    setWbApiRunControls("promotion_adverts", button, false, oldText);
  }
}

async function runWbPromotionFullstatsFromUi(button) {
  const campaignIds = qs("wbPromotionStatsCampaignIds")?.value?.trim() || "";
  const dateFrom = qs("wbPromotionStatsDateFrom")?.value || "";
  const dateTo = qs("wbPromotionStatsDateTo")?.value || "";
  const autoMode = qs("wbPromotionStatsAutoMode")?.value || "active_period";
  const windowMode = qs("wbPromotionStatsWindowMode")?.value || "period";
  const batchSize = qs("wbPromotionStatsBatchSize")?.value || "1";
  const resultMode = windowMode === "campaign_lifetime" ? "details" : (qs("wbPromotionStatsResultMode")?.value || "campaign_totals");
  if (windowMode !== "campaign_lifetime" && (!dateFrom || !dateTo)) {
    setWbPromotionStatsResult("Укажите период дат.", "error");
    return;
  }
  const oldText = button.textContent;
  setWbApiRunControls("promotion_fullstats", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastPromotionStatsResultFile = "";
  appendApiTerminalLine("> WB Promotion Fullstats: запуск GET /adv/v3/fullstats", "start");
  appendApiTerminalLine("Read-only GET: не передаются ставки, бюджеты или команды управления", "meta");
  appendApiTerminalLine(`Лимит WB fullstats: до 50 кампаний; окно запроса: ${windowMode === "campaign_lifetime" ? `батч до ${batchSize} кампаний с одинаковым окном дат, окна по 31 день от created до deleted` : windowMode === "day" ? "по 1 дню" : "за период до 31 дня"}; пауза 20 секунд между запросами`, "meta");
  appendApiTerminalLine(`Режим результата: ${windowMode === "campaign_lifetime" ? "полная детализация, файл на каждую кампанию ID.xlsx" : resultMode === "campaign_totals" ? "итого по кампаниям без days/apps/nms" : "полная детализация days/apps/nms"}`, "meta");
  appendApiTerminalLine("Транспорт: поток NDJSON, прогресс и лог пишутся во время запроса без ожидания финального JSON", "meta");
  if (windowMode === "campaign_lifetime") appendApiTerminalLine("Дата с/по в этом режиме не используется: берем created/deleted из карточек; deleted=2100-01-01 или пусто -> вчерашний день; существующий ID.xlsx пропускается; батч объединяет только кампании с одинаковым beginDate..endDate", "meta");
  if (!campaignIds) appendApiTerminalLine(`ID кампаний будут отобраны из последней выгрузки карточек обычной рекламы: ${autoMode === "all_status" ? "все статусы 7/9/11" : autoMode === "active_period" ? "по интервалу started/created -> deleted, без updated" : "только кампании с датой запуска в периоде"}`, "meta");
  setWbPromotionStatsResult("Запрашиваю статистику обычной рекламы WB...", "active");
  updateWbApiProgressView("promotion_fullstats", {
    running: true,
    progress: { phase: "plan", current: 0, total: 0, message: "Строю план запросов и отбираю кампании" },
  });
  state.wbApiLastProgressMessages["promotion_fullstats"] = "";
  try {
    const result = await postWbPromotionFullstatsStream({
      campaign_ids: campaignIds,
      date_from: dateFrom,
      date_to: dateTo,
      auto_mode: autoMode,
      window_mode: windowMode,
      campaign_batch_size: batchSize,
      result_mode: resultMode,
    });
    const files = result.files || {};
    setApiExportLogFile("promotion_fullstats", result.log_file || files.log);
    state.wbApiLastPromotionStatsResultFile = files.xlsx || "";
    appendWbApiStoppedLine(result);
    appendApiTerminalLine(`Получено блоков fullstats: ${formatNumber(result.count || 0)}`, "done");
    if (result.result_mode === "campaign_totals") appendApiTerminalLine("Сохранен режим итого по кампаниям: вложенные days/apps/nms не пишутся в результат", "meta");
    if (result.campaign_lifetime_mode) appendApiTerminalLine(`Режим по кампаниям: батч ${formatNumber(result.campaign_batch_size || 1)}, файлов кампаний в manifest: ${formatNumber((result.campaign_files || []).length)}, уже существующих пропущено: ${formatNumber(result.skipped_existing_count || 0)}`, "done");
    (result.campaign_files || []).forEach((row) => {
      appendApiTerminalLine(`Кампания ${row.advertId}: ${row.status || "ok"}, окон ${formatNumber(row.requests || 0)}, блоков ${formatNumber(row.count || 0)}, расход ${formatNumber(row.expense_sum || 0, 2)} руб. | XLSX: ${row.xlsx || ""}`, row.status === "partial_stopped" ? "warn" : "meta");
    });
    if (result.daily_save_mode) appendApiTerminalLine(`Дневной режим: сохранено файлов по дням: ${formatNumber((result.daily_files || []).length)}; итоговый manifest ниже`, "done");
    (result.daily_files || []).forEach((row) => {
      appendApiTerminalLine(`День ${row.date}: ${row.status || "ok"}, блоков ${formatNumber(row.count || 0)}, запросов ${formatNumber(row.requests || 0)}, расход ${formatNumber(row.expense_sum || 0, 2)} руб. | XLSX: ${row.xlsx || ""}`, row.status === "partial_stopped" ? "warn" : "meta");
    });
    appendApiTerminalLine(`Кампаний: ${formatNumber(result.campaigns_requested || 0)} из ${formatNumber(result.candidate_count || result.campaigns_requested || 0)}; периодов: ${formatNumber(result.periods_count || 0)}; запросов: ${formatNumber(result.requests || 0)}${result.planned_requests ? `, плановых окон: ${formatNumber(result.planned_requests)}` : ""}`, "meta");
    appendApiTerminalLine(`Расход по ответу WB: ${formatNumber(result.expense_sum || 0, 2)} руб.`, "meta");
    if (result.error_count) appendApiTerminalLine(`Ошибок WB после дробления: ${formatNumber(result.error_count)}; детали в JSON/XLSX`, "warn");
    if (result.skipped_count) appendApiTerminalLine(`Пропущено кампаний: ${formatNumber(result.skipped_count)}`, "meta");
    if (result.skipped_reasons && Object.keys(result.skipped_reasons).length) {
      const reasons = Object.entries(result.skipped_reasons).map(([key, value]) => `${key}: ${formatNumber(value)}`).join("; ");
      appendApiTerminalLine(`Причины пропуска: ${reasons}`, "meta");
    }
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (result.log_file || files.log) appendApiTerminalLine(`LOG: ${result.log_file || files.log}`, "meta");
    setWbPromotionStatsResult(
      `${wbApiResultPrefix(result)}${formatNumber(result.count || 0)} блоков, кампаний: ${formatNumber(result.campaigns_requested || 0)}, расход: ${formatNumber(result.expense_sum || 0, 2)} руб., запросов: ${formatNumber(result.requests || 0)}${result.daily_save_mode ? `, дневных файлов: ${formatNumber((result.daily_files || []).length)}` : ""}. XLSX: ${files.xlsx || ""}`,
      wbApiResultState(result),
    );
    completeWbApiProgressView("promotion_fullstats", result.stopped ? "Остановлено, частичный результат сохранен" : "Готово, результат сохранен", result.stopped ? "stopped" : "done");
    const openButton = document.querySelector("[data-open-wb-promotion-stats-result]");
    if (openButton) openButton.disabled = !state.wbApiLastPromotionStatsResultFile;
    qs("status").textContent = "WB Promotion fullstats сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbPromotionStatsResult(`Ошибка: ${error.message}`, "error");
    completeWbApiProgressView("promotion_fullstats", `Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    stopWbApiProgressPolling("promotion_fullstats");
    setWbApiRunControls("promotion_fullstats", button, false, oldText);
  }
}


function wbContentOpenButton(selector, enabled) {
  const button = document.querySelector(selector);
  if (button) button.disabled = !enabled;
}

async function runWbContentCategoriesFromUi(button) {
  const payload = {
    locale: qs("wbContentCategoriesLocale")?.value?.trim() || "ru",
    limit: Number(qs("wbContentCategoriesLimit")?.value || 1000),
  };
  const oldText = button.textContent;
  setWbApiRunControls("content_categories", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastContentCategoriesResultFile = "";
  appendApiTerminalLine("> WB Content Categories: parent/all -> object/all", "start");
  appendApiTerminalLine(`ПЛАН: locale ${payload.locale}; страницы предметов по ${payload.limit}; сохранение JSON/XLSX`, "meta");
  setWbContentResult("wbContentCategoriesResult", "Запрашиваю категории и предметы WB...", "active");
  updateWbApiProgressView("content_categories", { running: true, progress: { phase: "plan", current: 0, total: 0, message: "Готовлю выгрузку категорий" } });
  startWbApiProgressPolling("content_categories");
  try {
    const result = await postJson("/api/admin/wb-api/content/categories", payload);
    const files = result.files || {};
    setApiExportLogFile("content_categories", result.log_file || files.log);
    state.wbApiLastContentCategoriesResultFile = files.xlsx || "";
    appendWbApiStoppedLine(result);
    appendApiTerminalLine(`ИТОГ: родительских категорий ${formatNumber(result.parent_count || 0)}, предметов ${formatNumber(result.count || 0)}, запросов ${formatNumber(result.requests || 0)}`, "done");
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (result.log_file || files.log) appendApiTerminalLine(`LOG: ${result.log_file || files.log}`, "meta");
    setWbContentResult("wbContentCategoriesResult", `${wbApiResultPrefix(result)}${formatNumber(result.count || 0)} предметов, ${formatNumber(result.parent_count || 0)} родительских категорий. XLSX: ${files.xlsx || ""}`, wbApiResultState(result));
    wbContentOpenButton("[data-open-wb-content-categories-result]", state.wbApiLastContentCategoriesResultFile);
    completeWbApiProgressView("content_categories", result.stopped ? "Остановлено" : "Готово, результат сохранен", result.stopped ? "stopped" : "done");
    qs("status").textContent = "WB Content categories сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbContentResult("wbContentCategoriesResult", `Ошибка: ${error.message}`, "error");
    completeWbApiProgressView("content_categories", `Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    stopWbApiProgressPolling("content_categories");
    setWbApiRunControls("content_categories", button, false, oldText);
  }
}

async function runWbContentCardsFromUi(button) {
  const payload = {
    locale: qs("wbContentCardsLocale")?.value?.trim() || "ru",
    limit: Number(qs("wbContentCardsLimit")?.value || 100),
    max_pages: Number(qs("wbContentCardsMaxPages")?.value || 0),
    object_ids: qs("wbContentObjectIds")?.value?.trim() || "",
    text_search: qs("wbContentTextSearch")?.value?.trim() || "",
    with_photo: Number(qs("wbContentWithPhoto")?.value ?? -1),
    allowed_categories_only: qs("wbContentAllowedCategoriesOnly")?.checked !== false,
  };
  const oldText = button.textContent;
  setWbApiRunControls("content_cards", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastContentCardsResultFile = "";
  appendApiTerminalLine("> WB Content Cards: POST /content/v2/get/cards/list", "start");
  appendApiTerminalLine(`ПЛАН: limit ${payload.limit}; max_pages ${payload.max_pages || "до конца"}; subjectID фильтр: ${payload.object_ids || "все"}`, "meta");
  setWbContentResult("wbContentCardsResult", "Запрашиваю список товаров WB...", "active");
  updateWbApiProgressView("content_cards", { running: true, progress: { phase: "plan", current: 0, total: payload.max_pages || 0, message: "Готовлю выгрузку товаров" } });
  startWbApiProgressPolling("content_cards");
  try {
    const result = await postJson("/api/admin/wb-api/content/cards", payload);
    const files = result.files || {};
    setApiExportLogFile("content_cards", result.log_file || files.log);
    state.wbApiLastContentCardsResultFile = files.xlsx || "";
    appendWbApiStoppedLine(result);
    appendApiTerminalLine(`ИТОГ: товаров ${formatNumber(result.count || 0)}, subjectID ${formatNumber((result.subject_ids || []).length)}, запросов ${formatNumber(result.requests || 0)}`, "done");
    if (Array.isArray(result.subject_ids) && result.subject_ids.length) {
      const subjectInput = qs("wbContentSubjectIds");
      if (subjectInput) subjectInput.value = result.subject_ids.join(", ");
      appendApiTerminalLine(`subjectID для характеристик: ${result.subject_ids.join(", ")}`, "meta");
    }
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (result.log_file || files.log) appendApiTerminalLine(`LOG: ${result.log_file || files.log}`, "meta");
    setWbContentResult("wbContentCardsResult", `${wbApiResultPrefix(result)}${formatNumber(result.count || 0)} товаров, subjectID: ${formatNumber((result.subject_ids || []).length)}. XLSX: ${files.xlsx || ""}`, wbApiResultState(result));
    wbContentOpenButton("[data-open-wb-content-cards-result]", state.wbApiLastContentCardsResultFile);
    completeWbApiProgressView("content_cards", result.stopped ? "Остановлено" : "Готово, результат сохранен", result.stopped ? "stopped" : "done");
    qs("status").textContent = "WB Content cards сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbContentResult("wbContentCardsResult", `Ошибка: ${error.message}`, "error");
    completeWbApiProgressView("content_cards", `Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    stopWbApiProgressPolling("content_cards");
    setWbApiRunControls("content_cards", button, false, oldText);
  }
}

async function runWbContentCharacteristicsFromUi(button) {
  const payload = {
    locale: qs("wbContentCharacteristicsLocale")?.value?.trim() || "ru",
    subject_ids: qs("wbContentSubjectIds")?.value?.trim() || "",
  };
  if (!payload.subject_ids) {
    setWbContentResult("wbContentCharacteristicsResult", "Укажите subjectID или сначала выгрузите список товаров.", "error");
    return;
  }
  const oldText = button.textContent;
  setWbApiRunControls("content_characteristics", button, true, "Получаю...");
  state.apiTerminalLines = [];
  state.wbApiLastContentCharacteristicsResultFile = "";
  appendApiTerminalLine("> WB Content Characteristics: GET /content/v2/object/charcs/{subjectId}", "start");
  appendApiTerminalLine(`ПЛАН: subjectID ${payload.subject_ids}; сохранение JSON/XLSX`, "meta");
  setWbContentResult("wbContentCharacteristicsResult", "Запрашиваю характеристики предметов WB...", "active");
  updateWbApiProgressView("content_characteristics", { running: true, progress: { phase: "plan", current: 0, total: 0, message: "Готовлю выгрузку характеристик" } });
  startWbApiProgressPolling("content_characteristics");
  try {
    const result = await postJson("/api/admin/wb-api/content/characteristics", payload);
    const files = result.files || {};
    setApiExportLogFile("content_characteristics", result.log_file || files.log);
    state.wbApiLastContentCharacteristicsResultFile = files.xlsx || "";
    appendWbApiStoppedLine(result);
    appendApiTerminalLine(`ИТОГ: характеристик ${formatNumber(result.count || 0)}, subjectID ${formatNumber((result.subject_ids || []).length)}, ошибок ${formatNumber(result.error_count || 0)}`, result.error_count ? "warn" : "done");
    if (files.json) appendApiTerminalLine(`JSON: ${files.json}`, "meta");
    if (files.xlsx) appendApiTerminalLine(`XLSX: ${files.xlsx}`, "meta");
    if (result.log_file || files.log) appendApiTerminalLine(`LOG: ${result.log_file || files.log}`, "meta");
    setWbContentResult("wbContentCharacteristicsResult", `${wbApiResultPrefix(result)}${formatNumber(result.count || 0)} характеристик, ошибок: ${formatNumber(result.error_count || 0)}. XLSX: ${files.xlsx || ""}`, wbApiResultState(result));
    wbContentOpenButton("[data-open-wb-content-characteristics-result]", state.wbApiLastContentCharacteristicsResultFile);
    completeWbApiProgressView("content_characteristics", result.stopped ? "Остановлено" : "Готово, результат сохранен", result.stopped ? "stopped" : "done");
    qs("status").textContent = "WB Content characteristics сохранен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setWbContentResult("wbContentCharacteristicsResult", `Ошибка: ${error.message}`, "error");
    completeWbApiProgressView("content_characteristics", `Ошибка: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    stopWbApiProgressPolling("content_characteristics");
    setWbApiRunControls("content_characteristics", button, false, oldText);
  }
}

function extractOzonSeoItems(response) {
  if (!response || typeof response !== "object") return [];
  const result = response.result || response.data || response;
  if (Array.isArray(result.items)) return result.items;
  if (result.result && Array.isArray(result.result.items)) return result.result.items;
  if (Array.isArray(result)) return result;
  return [];
}

function renderOzonSeoPreview(items) {
  const target = qs("ozonSeoDetailsTable");
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="ozon-seo-preview-empty">В ответе не найден массив items для табличного предпросмотра.</div>';
    return;
  }
  target.innerHTML = `
    <div class="ozon-seo-preview-head">Предпросмотр items: ${formatNumber(items.length)} строк</div>
    <div class="ozon-seo-table-wrap">
      <table class="ozon-seo-table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Фраза</th>
            <th>Заказы</th>
            <th>GMV</th>
            <th>Позиция</th>
            <th>Поиски</th>
            <th>Показы</th>
            <th>Конверсия</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((item) => `
            <tr>
              <td>${escapeHtml(item.sku ?? item.skus ?? "")}</td>
              <td>${escapeHtml(item.query ?? item.search_query ?? item.phrase ?? "")}</td>
              <td class="number">${formatNumber(item.order_count ?? item.orders ?? "")}</td>
              <td class="number">${formatNumber(item.gmv ?? item.revenue ?? "")}</td>
              <td class="number">${formatNumber(item.position ?? "")}</td>
              <td class="number">${formatNumber(item.unique_search_users ?? item.searches ?? "")}</td>
              <td class="number">${formatNumber(item.unique_view_users ?? item.shows ?? item.views ?? "")}</td>
              <td class="number">${formatNumber(item.view_conversion ?? item.conversion ?? "")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function setOzonSeoResult(message, stateName = "idle", payload = null) {
  const result = qs("ozonSeoDetailsResult");
  if (result) {
    result.className = `wb-api-result is-${stateName}`;
    result.textContent = message;
  }
  const raw = qs("ozonSeoRawResult");
  if (raw) {
    const showAll = qs("ozonSeoShowAll")?.checked !== false;
    raw.classList.toggle("hidden", !payload || !showAll);
    raw.textContent = payload ? JSON.stringify(payload, null, 2) : "{}";
  }
}

async function saveOzonSeoCredentialsFromUi(button) {
  const clientIdInput = qs("ozonSeoClientIdInput");
  const apiKeyInput = qs("ozonSeoApiKeyInput");
  const clientId = clientIdInput?.value?.trim() || "";
  const apiKey = apiKeyInput?.value?.trim() || "";
  const status = qs("ozonSeoTokenStatus");
  if (!clientId) {
    if (status) status.textContent = "Введите Client ID из кабинета Ozon Seller.";
    return;
  }
  if (!apiKey) {
    if (status) status.textContent = "Введите API key перед сохранением.";
    return;
  }
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Сохраняю...";
  try {
    const payload = await postJson("/api/admin/ozon-seo/credentials", {
      client: state.adminClient || "toptop",
      client_id: clientId,
      api_key: apiKey,
    });
    apiKeyInput.value = "";
    apiKeyInput.placeholder = "Ключ сохранен; введите новый для замены";
    if (clientIdInput) clientIdInput.value = payload.client_id || clientId;
    if (lastAdminPayload) {
      lastAdminPayload.ozon_seo = {
        ...(lastAdminPayload.ozon_seo || {}),
        ...payload,
      };
    }
    const badge = qs("ozonSeoTokenBadge");
    if (badge) {
      badge.textContent = "Ключи сохранены";
      badge.classList.add("is-saved");
    }
    if (status) status.textContent = "Client ID и API key сохранены на сервере. API key не отображается.";
    qs("status").textContent = "Ключи Ozon Seller API сохранены";
  } catch (error) {
    if (status) status.textContent = `Ошибка сохранения: ${error.message}`;
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}


async function runOzonSeoDetailsFromUi(button) {
  const payload = {
    client: state.adminClient || "toptop",
    client_id: qs("ozonSeoClientIdInput")?.value?.trim() || "",
    api_key: qs("ozonSeoApiKeyInput")?.value?.trim() || "",
    sku: qs("ozonSeoSkuInput")?.value?.trim() || "",
    date_from: qs("ozonSeoDateFrom")?.value || "",
    date_to: qs("ozonSeoDateTo")?.value || "",
    limit_by_sku: Number(qs("ozonSeoLimitBySku")?.value || 15),
    page: Number(qs("ozonSeoPage")?.value || 0),
    page_size: Number(qs("ozonSeoPageSize")?.value || 100),
    sort_by: qs("ozonSeoSortBy")?.value || "BY_SEARCHES",
    sort_dir: qs("ozonSeoSortDir")?.value || "DESCENDING",
  };
  const credentialsSaved = lastAdminPayload?.ozon_seo?.credentials_saved === true;
  if (!payload.client_id || (!payload.api_key && !credentialsSaved) || !payload.sku || !payload.date_from || !payload.date_to) {
    setOzonSeoResult("Заполните или сохраните Client ID и API key, затем укажите SKU и период.", "error");
    return;
  }
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Получаю...";
  state.apiTerminalLines = [];
  appendApiTerminalLine("> Ozon SEO: POST /v1/analytics/product-queries/details", "start");
  appendApiTerminalLine(`ПРОГРЕСС: 1/1 (100%) | SKU ${payload.sku} | период ${payload.date_from}..${payload.date_to} | отправка запроса`, "meta");
  appendApiTerminalLine("API key не печатается в лог; сохраненные ключи читаются только на сервере.", "meta");
  setOzonSeoResult("Запрашиваю поисковые фразы Ozon по SKU...", "active");
  renderOzonSeoPreview([]);
  try {
    const data = await postJson("/api/admin/ozon-seo/product-queries/details", payload);
    setApiExportLogFile("ozon_seo_details", data.log_file || data.files?.log);
    state.ozonSeoLastResponse = data;
    const items = extractOzonSeoItems(data.response);
    renderOzonSeoPreview(items);
    setOzonSeoResult(`Готово: HTTP ${data.status || 200}, строк items: ${formatNumber(items.length)}. Полный ответ ${qs("ozonSeoShowAll")?.checked === false ? "скрыт настройкой" : "показан ниже"}.`, "done", data);
    appendApiTerminalLine(`ИТОГ: SKU ${payload.sku} | строк ${formatNumber(items.length)} | запрос выполнен`, "done");
    if (data.log_file || data.files?.log) appendApiTerminalLine(`LOG: ${data.log_file || data.files.log}`, "meta");
    qs("status").textContent = "Ozon SEO details получен";
  } catch (error) {
    appendApiTerminalLine(`Ошибка: ${error.message}`, "error");
    setOzonSeoResult(`Ошибка: ${error.message}`, "error");
    renderOzonSeoPreview([]);
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function openWbPromotionResultFileFromUi(button, filePath) {
  if (!filePath) {
    appendApiTerminalLine("Файл результата еще не создан", "error");
    return;
  }
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "Открываю...";
  appendApiTerminalLine(`Открываю файл: ${filePath}`, "output");
  try {
    await postJson("/api/admin/wb-api/open-file", { file: filePath });
    appendApiTerminalLine("Файл результата открыт", "done");
  } catch (error) {
    appendApiTerminalLine(`Не удалось открыть файл: ${error.message}`, "error");
    setStatusError(error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

const columnFilterOperators = [
  { value: "contains", label: "содержит" },
  { value: "not_contains", label: "не содержит" },
  { value: "eq", label: "равно" },
  { value: "neq", label: "не равно" },
  { value: "gt", label: "больше чем" },
  { value: "gte", label: "больше или равно" },
  { value: "lt", label: "меньше чем" },
  { value: "lte", label: "меньше или равно" },
];

function closeColumnFilterPopover() {
  document.querySelector(".column-filter-popover")?.remove();
}

function resetColumnFilters() {
  state.columnFilters = {};
  closeColumnFilterPopover();
}

function tableOrderKey() {
  const marketplace = qs("marketplace")?.value || "ozon";
  return `${state.dashboard}:${marketplace}`;
}

function applyColumnOrder(columns, order) {
  const byKey = new Map(columns.map((column) => [column.key, column]));
  const ordered = order.map((key) => byKey.get(key)).filter(Boolean);
  const missing = columns.filter((column) => !order.includes(column.key));
  return [...ordered, ...missing];
}

function orderedTableColumns(columns) {
  const fixedOrder = fixedTableColumnOrders[state.dashboard];
  if (fixedOrder) return applyColumnOrder(columns, fixedOrder);
  const order = state.columnOrders?.[tableOrderKey()];
  if (!Array.isArray(order) || !order.length) return columns;
  return applyColumnOrder(columns, order);
}

function saveTableColumnOrder(columns) {
  if (!state.columnOrders || typeof state.columnOrders !== "object") {
    state.columnOrders = {};
  }
  state.columnOrders[tableOrderKey()] = columns.map((column) => column.key);
  persistDashboardState();
}

function renderColumnFilterPopover(column, anchor) {
  closeColumnFilterPopover();
  const current = state.columnFilters[column.key] || {
    op: column.type === "number" ? "gt" : "contains",
    value: "",
  };
  const popover = document.createElement("div");
  popover.className = "column-filter-popover";
  popover.innerHTML = `
    <div class="column-filter-title">${escapeHtml(column.label)}</div>
    <select class="column-filter-op">
      ${columnFilterOperators.map((item) => `<option value="${item.value}" ${item.value === current.op ? "selected" : ""}>${item.label}</option>`).join("")}
    </select>
    <input class="column-filter-value" type="text" ${column.type === "number" ? 'inputmode="decimal"' : ""} value="${escapeHtml(current.value || "")}" placeholder="Значение" />
    <div class="column-filter-actions">
      <button type="button" class="column-filter-clear">Сбросить</button>
      <button type="button" class="column-filter-apply">Применить</button>
    </div>
  `;
  document.body.appendChild(popover);
  const rect = anchor.getBoundingClientRect();
  const left = Math.min(window.scrollX + rect.left, window.scrollX + window.innerWidth - popover.offsetWidth - 12);
  popover.style.left = `${Math.max(12, left)}px`;
  popover.style.top = `${window.scrollY + rect.bottom + 6}px`;
  const input = popover.querySelector(".column-filter-value");
  input.focus();
  input.select();

  const apply = () => {
    const value = input.value.trim();
    if (value) {
      state.columnFilters[column.key] = {
        op: popover.querySelector(".column-filter-op").value,
        value,
      };
    } else {
      delete state.columnFilters[column.key];
    }
    state.page = 1;
    closeColumnFilterPopover();
    loadData().catch((error) => {
      setStatusError(error);
    });
  };

  popover.querySelector(".column-filter-apply").addEventListener("click", apply);
  popover.querySelector(".column-filter-clear").addEventListener("click", () => {
    delete state.columnFilters[column.key];
    state.page = 1;
    closeColumnFilterPopover();
    loadData().catch((error) => {
      setStatusError(error);
    });
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") apply();
    if (event.key === "Escape") closeColumnFilterPopover();
  });
}

function renderSkuCardValue(value) {
  if (value === null || value === undefined || value === "") return '<span class="muted">нет</span>';
  if (typeof value === "number") return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  const text = String(value);
  const shortText = text.length > 900 ? `${text.slice(0, 900)}...` : text;
  return escapeHtml(shortText);
}

function renderSkuCardKpi(card) {
  return `
    <article>
      <span>${escapeHtml(card.label)}</span>
      <strong>${renderSkuCardValue(card.value)}</strong>
    </article>
  `;
}

function renderSkuCardAttributes(rows) {
  if (!rows.length) return '<div class="empty">Нет атрибутов по карточке</div>';
  return `
    <div class="sku-card-table-wrap">
      <table class="sku-card-table">
        <thead>
          <tr>
            <th>Атрибут</th>
            <th>Категория</th>
            <th>Значение</th>
            <th>Вес</th>
            <th>Состояние</th>
            <th>Оценка</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr class="${row.filled ? "" : "is-empty"}">
              <td>${escapeHtml(row.attribute_name || "")}</td>
              <td>${escapeHtml(row.attribute_kind || "")}</td>
              <td class="sku-card-value">${renderSkuCardValue(row.value)}</td>
              <td class="num">${formatNumber(row.weight || 0)}</td>
              <td>${escapeHtml(row.state || "")}</td>
              <td class="num">${formatNumber(row.importance_score || 0, Number.isInteger(Number(row.importance_score || 0)) ? 0 : 1)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderSkuCardMedia(media, source) {
  if (!media.length) return "";
  const photos = media.filter((item) => item.kind === "photo" && item.url);
  const videos = media.filter((item) => item.kind === "video" && item.url);
  return `
    <section class="sku-card-section sku-card-media-section">
      <div class="sku-card-section-head">
        <h3>Медиа карточки</h3>
        ${source ? `<span>${escapeHtml(source)}</span>` : ""}
      </div>
      <div class="sku-card-media" aria-label="Фотографии товара">
        ${photos.map((item) => `
          <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(item.label || "Открыть фото")}">
            <img src="${escapeHtml(item.thumbnail_url || item.url)}" alt="${escapeHtml(item.label || "Фото товара")}" loading="lazy" decoding="async">
            <span>${escapeHtml(item.label || "Фото")}</span>
          </a>
        `).join("")}
        ${videos.map((item) => `
          <a class="sku-card-video-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
            ${escapeHtml(item.label || "Открыть видео")}
          </a>
        `).join("")}
      </div>
    </section>
  `;
}

function currentMarketplaceLabel() {
  return qs("marketplace")?.selectedOptions?.[0]?.textContent || currentMarketplace();
}

function setSkuCardPageVisible(visible) {
  qs("skuCardPage")?.classList.toggle("hidden", !visible);
  document.querySelector(".filters")?.classList.toggle("hidden", visible);
  document.querySelector(".kpis")?.classList.toggle("hidden", visible);
  document.querySelector(".visuals")?.classList.toggle("hidden", visible);
  document.querySelector(".table-wrap")?.classList.toggle("hidden", visible);
  qs("advCampaignTableSection")?.classList.toggle("hidden", visible || state.dashboard !== "adv" || currentMarketplace() !== "ozon");
  document.querySelector(".pagination")?.classList.toggle("hidden", visible);
  qs("exportExcel")?.classList.toggle("hidden", visible);
}

function closeSkuCard() {
  setSkuCardPageVisible(false);
  qs("status").textContent = state.dashboard === "product" ? `${currentMarketplaceLabel()}: ABC по продуктам` : `${currentMarketplaceLabel()}: SKU-скоринг`;
}

function renderSkuCard(payload) {
  const summary = payload.summary || {};
  qs("skuCardTitle").textContent = `SKU ${payload.sku || summary.artikul_wb || ""}`;
  qs("skuCardSubtitle").textContent = summary.naimenovanie || summary.card_name || "";
  qs("skuCardContent").innerHTML = `
    <section class="sku-card-kpis">
      ${(payload.cards || []).map(renderSkuCardKpi).join("")}
    </section>
    ${renderSkuCardMedia(payload.media || [], payload.content_source || "")}
    <section class="sku-card-section">
      <h3>Скоринг карточки</h3>
      ${renderSkuCardAttributes(payload.attributes || [])}
    </section>
  `;
}

async function openSkuCard(sku) {
  if (!sku) return;
  setSkuCardPageVisible(true);
  if (qs("skuCardBack")) qs("skuCardBack").textContent = state.dashboard === "product" ? "Назад к продуктам" : "Назад к SKU";
  qs("skuCardTitle").textContent = `SKU ${sku}`;
  qs("skuCardSubtitle").textContent = "";
  qs("skuCardContent").innerHTML = '<div class="empty">Загрузка карточки...</div>';
  qs("status").textContent = `${currentMarketplaceLabel()}: карточка SKU ${sku}`;
  window.scrollTo({ top: 0, behavior: "smooth" });
  try {
    const params = new URLSearchParams({ client: currentClient(), marketplace: currentMarketplace(), sku });
    if (qs("date_from")?.value) params.set("date_from", qs("date_from").value);
    if (qs("date_to")?.value) params.set("date_to", qs("date_to").value);
    const payload = await getJson(`/api/sku-card?${params.toString()}`);
    renderSkuCard(payload);
  } catch (error) {
    qs("skuCardContent").innerHTML = `<div class="empty">Ошибка: ${escapeHtml(error.message)}</div>`;
  }
}

async function selectAdvCampaignFromTable(campaignId) {
  const normalizedId = String(campaignId || "").trim();
  const select = qs("adv_campaign_id");
  if (!normalizedId || !select) return;
  if (![...select.options].some((option) => option.value === normalizedId)) {
    const option = document.createElement("option");
    option.value = normalizedId;
    option.textContent = normalizedId;
    select.appendChild(option);
  }
  select.value = normalizedId;
  state.pendingAdvCampaignId = normalizedId;
  state.page = 1;
  state.columnFilters = {};
  markFiltersDirty(false);
  await loadData();
}

function renderAdvCampaignTable(payload) {
  const section = qs("advCampaignTableSection");
  const head = qs("advCampaignTableHead");
  const body = qs("advCampaignTableRows");
  const visible = state.dashboard === "adv" && currentMarketplace() === "ozon";
  if (!section || !head || !body) return;
  section.classList.toggle("hidden", !visible);
  if (!visible) { head.innerHTML = ""; body.innerHTML = ""; return; }
  const columns = payload?.columns || [];
  const rows = payload?.rows || [];
  const selectedCampaign = qs("adv_campaign_id")?.value || "";
  qs("advCampaignTableMeta").textContent = selectedCampaign
    ? `Выбрана кампания ${selectedCampaign}.`
    : `Кампаний: ${formatNumber(payload?.total || rows.length)}. Нажмите ID, чтобы применить фильтр ко всему дашборду.`;
  head.innerHTML = `<tr>${columns.map((column) => `<th class="${column.type === "number" ? "num" : ""}" data-type="${escapeHtml(column.type || "text")}">${escapeHtml(column.label || column.key)}</th>`).join("")}</tr>`;
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="${Math.max(1, columns.length)}">Нет кампаний под выбранные фильтры</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => {
    const campaignId = String(row.campaign_id || "");
    const selected = campaignId === selectedCampaign;
    return `<tr class="${selected ? "is-selected" : ""}" data-campaign-row="${escapeHtml(campaignId)}">${columns.map((column) => {
      const value = row[column.key];
      const content = column.type === "number" ? formatNumber(value, numberDigits(column.key)) : escapeHtml(value ?? "");
      if (column.key === "campaign_id") return `<td><button class="link-button campaign-id-button" type="button" data-adv-campaign-id="${escapeHtml(campaignId)}" aria-pressed="${selected ? "true" : "false"}" title="Показать весь дашборд только по кампании ${escapeHtml(campaignId)}">${content}</button></td>`;
      return `<td class="${column.type === "number" ? "num" : ""}">${content}</td>`;
    }).join("")}</tr>`;
  }).join("");
  body.querySelectorAll("[data-adv-campaign-id]").forEach((button) => {
    button.addEventListener("click", () => selectAdvCampaignFromTable(button.dataset.advCampaignId).catch((error) => { setStatusError(error); }));
  });
}

function tableColumnWidthStorageKey() {
  return `${STORAGE_KEY}:column-widths:${currentClient()}:${currentMarketplace()}:${state.dashboard}`;
}

function loadTableColumnWidths() {
  try {
    const parsed = JSON.parse(localStorage.getItem(tableColumnWidthStorageKey()) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveTableColumnWidths(widths) {
  try {
    localStorage.setItem(tableColumnWidthStorageKey(), JSON.stringify(widths));
  } catch (_error) {}
}

function tableColumnWidthStyle(columnKey) {
  const width = Number(loadTableColumnWidths()[columnKey]);
  if (!Number.isFinite(width) || width <= 0) return "";
  const safeWidth = Math.max(72, Math.min(Math.round(width), 760));
  return ` style="width:${safeWidth}px;min-width:${safeWidth}px;max-width:${safeWidth}px"`;
}

function setRenderedTableColumnWidth(columnKey, width) {
  const safeWidth = Math.max(72, Math.min(Math.round(width), 760));
  document.querySelectorAll(`#tableHead [data-column="${columnKey}"], #rows [data-column="${columnKey}"]`).forEach((cell) => {
    cell.style.width = `${safeWidth}px`;
    cell.style.minWidth = `${safeWidth}px`;
    cell.style.maxWidth = `${safeWidth}px`;
  });
  return safeWidth;
}

function attachTableColumnResizeHandlers() {
  qs("tableHead")?.querySelectorAll("[data-column-resize]").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const columnKey = handle.dataset.columnResize;
      const th = handle.closest("th");
      if (!columnKey || !th) return;
      const startX = event.clientX;
      const startWidth = th.getBoundingClientRect().width;
      const widths = loadTableColumnWidths();
      document.documentElement.classList.add("is-resizing-column");
      const onMove = (moveEvent) => {
        const nextWidth = setRenderedTableColumnWidth(columnKey, startWidth + moveEvent.clientX - startX);
        widths[columnKey] = nextWidth;
      };
      const onUp = () => {
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.documentElement.classList.remove("is-resizing-column");
        saveTableColumnWidths(widths);
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp, { once: true });
    });
  });
}

function productSkuCardValue(row, columnKey) {
  if (state.dashboard !== "product") return "";
  if (!["sku_ozon", "ozon_sku", "artikul_wb"].includes(columnKey)) return "";
  return String(row[columnKey] || row.sku_ozon || row.ozon_sku || row.artikul_wb || "").trim();
}

function renderTable(columns, rows) {
  qs("tableContext")?.classList.add("hidden");
  qs("exportTableExcel")?.classList.remove("hidden");
  const displayColumns = orderedTableColumns(columns);
  state.columns = displayColumns;
  let draggedColumnKey = null;
  let suppressHeaderClick = false;
  qs("tableHead").innerHTML = `
    <tr>
      ${displayColumns
        .map((column) => {
          const active = column.key === state.sortCol;
          const arrow = active ? (state.sortDir === "asc" ? " ↑" : " ↓") : "";
          const filterActive = state.columnFilters[column.key]?.value;
          return `
            <th class="draggable-column ${active ? "sorted" : ""} ${filterActive ? "filtered" : ""}" draggable="true" data-column="${escapeHtml(column.key)}"${tableColumnWidthStyle(column.key)} title="Перетащите, чтобы поменять порядок столбцов">
              <span class="table-header-label">${escapeHtml(column.label)}${arrow}</span>
              <button class="column-filter-btn ${filterActive ? "active" : ""}" type="button" title="Фильтр по полю ${escapeHtml(column.label)}" aria-label="Фильтр по полю ${escapeHtml(column.label)}" data-filter-column="${escapeHtml(column.key)}"></button>
              <span class="column-resize-handle" data-column-resize="${escapeHtml(column.key)}" title="Изменить ширину столбца"></span>
            </th>
          `;
        })
        .join("")}
    </tr>
  `;

  qs("tableHead").querySelectorAll("th").forEach((th) => {
    th.addEventListener("dragstart", (event) => {
      if (event.target.closest(".column-filter-btn, .column-resize-handle")) {
        event.preventDefault();
        return;
      }
      draggedColumnKey = th.dataset.column;
      th.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedColumnKey);
      }
    });
    th.addEventListener("dragover", (event) => {
      if (!draggedColumnKey || draggedColumnKey === th.dataset.column) return;
      event.preventDefault();
      th.classList.add("drag-over");
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });
    th.addEventListener("dragleave", () => {
      th.classList.remove("drag-over");
    });
    th.addEventListener("drop", (event) => {
      event.preventDefault();
      th.classList.remove("drag-over");
      const fromKey = event.dataTransfer?.getData("text/plain") || draggedColumnKey;
      const toKey = th.dataset.column;
      if (!fromKey || !toKey || fromKey === toKey) return;
      const nextColumns = [...displayColumns];
      const fromIndex = nextColumns.findIndex((column) => column.key === fromKey);
      const toIndex = nextColumns.findIndex((column) => column.key === toKey);
      if (fromIndex < 0 || toIndex < 0) return;
      const [moved] = nextColumns.splice(fromIndex, 1);
      nextColumns.splice(toIndex, 0, moved);
      suppressHeaderClick = true;
      saveTableColumnOrder(nextColumns);
      renderTable(columns, rows);
      setTimeout(() => {
        suppressHeaderClick = false;
      }, 0);
    });
    th.addEventListener("dragend", () => {
      draggedColumnKey = null;
      qs("tableHead").querySelectorAll("th").forEach((item) => item.classList.remove("dragging", "drag-over"));
    });
    th.addEventListener("click", (event) => {
      if (suppressHeaderClick) return;
      if (event.target.closest(".column-filter-btn, .column-resize-handle")) return;
      const key = th.dataset.column;
      if (state.sortCol === key) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortCol = key;
        state.sortDir = "desc";
      }
      state.page = 1;
      loadData().catch((error) => {
        setStatusError(error);
      });
    });
  });
  qs("tableHead").querySelectorAll(".column-filter-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const column = displayColumns.find((item) => item.key === button.dataset.filterColumn);
      if (column) renderColumnFilterPopover(column, button);
    });
  });
  attachTableColumnResizeHandlers();

  const body = qs("rows");
  if (!rows.length) {
    body.innerHTML = `<tr><td class="empty" colspan="${displayColumns.length}">Нет данных под выбранные фильтры</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map(
      (row) => `
        <tr>
          ${displayColumns
            .map((column) => {
              const value = row[column.key];
              const content = column.type === "number" ? formatNumber(value, numberDigits(column.key)) : escapeHtml(value ?? "");
              const cellAttrs = `data-column="${escapeHtml(column.key)}"${tableColumnWidthStyle(column.key)}`;
              if (state.dashboard === "wbSearchQueries" && column.key === "frequency_tier") {
                const tierClass = value === "ВЧ" ? "high" : (value === "СЧ" ? "middle" : "low");
                return `<td ${cellAttrs}><span class="wb-frequency-tier wb-frequency-tier-${tierClass}">${content}</span></td>`;
              }
              if (state.dashboard === "wbSearchQueries" && column.key === "category_rank") {
                const totalInCategory = formatNumber(row.category_query_count || 0);
                return `<td class="num" ${cellAttrs} title="Место ключа среди запросов категории">${content} / ${totalInCategory}</td>`;
              }
              if (state.dashboard === "abc" && column.key === "category_name") {
                return `<td ${cellAttrs}><button class="link-button" type="button" data-category="${escapeHtml(value ?? "")}">${content}</button></td>`;
              }
              if (state.dashboard === "sku" && column.key === "artikul_wb") {
                return `<td ${cellAttrs}><button class="link-button sku-link" type="button" data-sku-card="${escapeHtml(value ?? "")}">${content}</button></td>`;
              }
              const skuCardValue = productSkuCardValue(row, column.key);
              if (skuCardValue) {
                return `<td ${cellAttrs}><button class="link-button sku-link" type="button" data-sku-card="${escapeHtml(skuCardValue)}">${content}</button></td>`;
              }
              return `<td class="${column.type === "number" ? "num" : ""}" ${cellAttrs}>${content}</td>`;
            })
            .join("")}
        </tr>
      `
    )
    .join("");

  body.querySelectorAll(".link-button[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      openProductDashboard(button.dataset.category || "");
    });
  });
  body.querySelectorAll(".link-button[data-sku-card]").forEach((button) => {
    button.addEventListener("click", () => {
      openSkuCard(button.dataset.skuCard || "");
    });
  });
}

function renderPagination() {
  qs("pageInfo").textContent = `Страница ${state.page} из ${state.totalPages} · строк: ${formatNumber(state.total)}`;
  qs("prevPage").disabled = state.page <= 1;
  qs("nextPage").disabled = state.page >= state.totalPages;
}

function setTableVisible(visible) {
  document.querySelector(".table-wrap")?.classList.toggle("hidden", !visible);
  document.querySelector(".pagination")?.classList.toggle("hidden", !visible);
  if (!visible) {
    qs("tableContext")?.classList.add("hidden");
    qs("exportTableExcel")?.classList.remove("hidden");
    qs("tableHead").innerHTML = "";
    qs("rows").innerHTML = "";
  }
}


function financeMoney(value, digits = 0) {
  if (value === null || value === undefined || value === "") return "—";
  return `${formatNumber(value, digits)} ₽`;
}

function financePercent(value) {
  if (value === null || value === undefined || value === "") return "—";
  return `${formatNumber(value, 1)}%`;
}

function financeInputValue(value) {
  return value === null || value === undefined ? "" : escapeHtml(value);
}

function financeMetricCard(label, value, note = "", tone = "") {
  return `
    <article class="finance-kpi-card ${tone ? `finance-kpi-${tone}` : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
      ${note ? `<small>${escapeHtml(note)}</small>` : ""}
    </article>
  `;
}

function setFinanceShellVisible(visible) {
  const root = qs("financeDashboard");
  root?.classList.toggle("hidden", !visible);
  if (!visible) return;
  document.querySelector(".filters")?.classList.add("hidden");
  document.querySelector(".kpis")?.classList.add("hidden");
  document.querySelector(".visuals")?.classList.add("hidden");
  document.querySelector(".table-wrap")?.classList.add("hidden");
  qs("advCampaignTableSection")?.classList.add("hidden");
  document.querySelector(".pagination")?.classList.add("hidden");
  qs("exportExcel")?.classList.add("hidden");
  qs("backToAbc")?.classList.add("hidden");
  setChartExportButtonsVisible(false);
}

let financeCalendarMin = "";
let financeCalendarMax = "";
let financeCalendarDraftFrom = "";
let financeCalendarDraftTo = "";
let financeCalendarViewMonth = "";

const financeCalendarMonthNames = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];

function financeIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function financeDateIso(value) {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return "";
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function financeFormatRuDate(value) {
  const parsed = financeIsoDate(value);
  return parsed
    ? parsed.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" })
    : String(value || "—");
}

function financeShiftDate(value, days) {
  const parsed = financeIsoDate(value);
  if (!parsed) return "";
  parsed.setDate(parsed.getDate() + days);
  return financeDateIso(parsed);
}

function financeShiftMonth(value, months) {
  const parsed = financeIsoDate(value);
  if (!parsed) return "";
  return financeDateIso(new Date(parsed.getFullYear(), parsed.getMonth() + months, 1));
}

function financeMonthStart(value) {
  const parsed = financeIsoDate(value);
  return parsed ? financeDateIso(new Date(parsed.getFullYear(), parsed.getMonth(), 1)) : "";
}

function financeMonthEnd(value) {
  const parsed = financeIsoDate(value);
  return parsed ? financeDateIso(new Date(parsed.getFullYear(), parsed.getMonth() + 1, 0)) : "";
}

function financeBoundDate(value) {
  if (!value) return value;
  if (financeCalendarMin && value < financeCalendarMin) return financeCalendarMin;
  if (financeCalendarMax && value > financeCalendarMax) return financeCalendarMax;
  return value;
}

function financeCalendarMonthHtml(monthIso) {
  const month = financeIsoDate(monthIso);
  if (!month) return "";
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const firstWeekday = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
  const today = financeCalendarMax;
  const cells = [];
  for (let index = 0; index < 42; index += 1) {
    const day = index - firstWeekday + 1;
    if (day < 1 || day > daysInMonth) {
      cells.push('<span class="finance-calendar-empty"></span>');
      continue;
    }
    const iso = financeDateIso(new Date(year, monthIndex, day));
    const disabled = (financeCalendarMin && iso < financeCalendarMin) || (financeCalendarMax && iso > financeCalendarMax);
    const isStart = iso === financeCalendarDraftFrom;
    const isEnd = iso === financeCalendarDraftTo;
    const inRange = Boolean(financeCalendarDraftFrom && financeCalendarDraftTo && iso > financeCalendarDraftFrom && iso < financeCalendarDraftTo);
    const classes = [
      isStart ? "range-start" : "",
      isEnd ? "range-end" : "",
      inRange ? "in-range" : "",
      iso === today ? "is-today" : "",
    ].filter(Boolean).join(" ");
    cells.push(`<button type="button" class="${classes}" data-finance-calendar-action="day" data-date="${iso}" ${disabled ? "disabled" : ""} aria-label="${escapeHtml(financeFormatRuDate(iso))}">${day}</button>`);
  }
  return `
    <section class="finance-calendar-month">
      <h3>${escapeHtml(financeCalendarMonthNames[monthIndex])} ${year}</h3>
      <div class="finance-calendar-weekdays">${["пн", "вт", "ср", "чт", "пт", "сб", "вс"].map((day) => `<span>${day}</span>`).join("")}</div>
      <div class="finance-calendar-days">${cells.join("")}</div>
    </section>
  `;
}

function financeCalendarBodyHtml() {
  const secondMonth = financeShiftMonth(financeCalendarViewMonth, 1);
  const first = financeIsoDate(financeCalendarViewMonth);
  const second = financeIsoDate(secondMonth);
  const title = first && second
    ? `${financeCalendarMonthNames[first.getMonth()]} — ${financeCalendarMonthNames[second.getMonth()]} ${second.getFullYear()}`
    : "Выбор периода";
  const draftLabel = financeCalendarDraftFrom && financeCalendarDraftTo
    ? `${financeFormatRuDate(financeCalendarDraftFrom)} — ${financeFormatRuDate(financeCalendarDraftTo)}`
    : (financeCalendarDraftFrom ? `${financeFormatRuDate(financeCalendarDraftFrom)} — выберите окончание` : "Выберите начало и окончание периода");
  const presets = [
    ["all", "Весь период"],
    ["days7", "Последние 7 дней"],
    ["days28", "Последние 28 дней"],
    ["days90", "Последние 90 дней"],
    ["month", "Текущий месяц"],
    ["prev_month", "Прошлый месяц"],
  ];
  return `
    <header class="finance-calendar-head">
      <button type="button" class="ghost" data-finance-calendar-action="prev" aria-label="Предыдущий месяц">←</button>
      <strong>${escapeHtml(title)}</strong>
      <button type="button" class="ghost" data-finance-calendar-action="next" aria-label="Следующий месяц">→</button>
      <button type="button" class="ghost finance-calendar-close" data-finance-calendar-action="close" aria-label="Закрыть">×</button>
    </header>
    <div class="finance-calendar-layout">
      <nav class="finance-calendar-presets" aria-label="Быстрый выбор периода">
        <span>Быстрый выбор</span>
        ${presets.map(([key, label]) => `<button type="button" data-finance-calendar-action="preset" data-preset="${key}">${escapeHtml(label)}</button>`).join("")}
      </nav>
      <div class="finance-calendar-months">
        ${financeCalendarMonthHtml(financeCalendarViewMonth)}
        ${financeCalendarMonthHtml(secondMonth)}
      </div>
    </div>
    <footer class="finance-calendar-footer">
      <span>${escapeHtml(draftLabel)}</span>
      <div>
        <button type="button" class="ghost" data-finance-calendar-action="close">Отмена</button>
        <button type="button" data-finance-action="apply-period" ${financeCalendarDraftFrom && financeCalendarDraftTo ? "" : "disabled"}>Применить</button>
      </div>
    </footer>
  `;
}

function renderFinanceCalendarBody() {
  const popover = qs("financeCalendarPopover");
  if (popover) popover.innerHTML = financeCalendarBodyHtml();
}

function closeFinanceCalendar() {
  qs("financeCalendarPopover")?.classList.add("hidden");
  qs("financePeriodToggle")?.setAttribute("aria-expanded", "false");
}

function financeCalendarPreset(key) {
  const anchor = financeCalendarMax || financeDateIso(new Date());
  let from = anchor;
  let to = anchor;
  if (key === "all") {
    from = financeCalendarMin;
    to = financeCalendarMax;
  } else if (key === "days7") {
    from = financeBoundDate(financeShiftDate(anchor, -6));
  } else if (key === "days28" || key === "days30") {
    from = financeBoundDate(financeShiftDate(anchor, -(DEFAULT_RECENT_DAYS - 1)));
  } else if (key === "days90") {
    from = financeBoundDate(financeShiftDate(anchor, -89));
  } else if (key === "month") {
    from = financeBoundDate(financeMonthStart(anchor));
  } else if (key === "prev_month") {
    const previous = financeShiftMonth(financeMonthStart(anchor), -1);
    from = financeBoundDate(financeMonthStart(previous));
    to = financeBoundDate(financeMonthEnd(previous));
  }
  financeCalendarDraftFrom = from;
  financeCalendarDraftTo = to;
  financeCalendarViewMonth = financeShiftMonth(financeMonthStart(to || from), -1);
  renderFinanceCalendarBody();
}

function handleFinanceCalendarAction(button) {
  const action = button.dataset.financeCalendarAction;
  if (action === "toggle") {
    const popover = qs("financeCalendarPopover");
    if (!popover) return;
    const willOpen = popover.classList.contains("hidden");
    popover.classList.toggle("hidden", !willOpen);
    button.setAttribute("aria-expanded", String(willOpen));
    if (willOpen) renderFinanceCalendarBody();
  } else if (action === "close") {
    closeFinanceCalendar();
  } else if (action === "prev" || action === "next") {
    financeCalendarViewMonth = financeShiftMonth(financeCalendarViewMonth, action === "prev" ? -1 : 1);
    renderFinanceCalendarBody();
  } else if (action === "preset") {
    financeCalendarPreset(button.dataset.preset || "all");
  } else if (action === "day" && !button.disabled) {
    const selected = button.dataset.date || "";
    if (!financeCalendarDraftFrom || financeCalendarDraftTo) {
      financeCalendarDraftFrom = selected;
      financeCalendarDraftTo = "";
    } else if (selected < financeCalendarDraftFrom) {
      financeCalendarDraftTo = financeCalendarDraftFrom;
      financeCalendarDraftFrom = selected;
    } else {
      financeCalendarDraftTo = selected;
    }
    renderFinanceCalendarBody();
  }
}

function financeMarketplaceSwitch(payload) {
  const entries = Array.isArray(payload?.marketplaces) && payload.marketplaces.length
    ? payload.marketplaces
    : clientMarketplaceIds().map((id) => ({id, label: id === "wb" ? "WB" : "Ozon"}));
  const selected = payload?.marketplace || currentMarketplace();
  if (entries.length < 2) return "";
  return `
    <div class="finance-marketplace-switch" role="tablist" aria-label="Маркетплейс P&amp;L">
      ${entries.map((entry) => {
        const id = entry.id === "wb" ? "wb" : "ozon";
        const active = id === selected;
        return `<button type="button" role="tab" class="${active ? "is-active" : ""}" data-finance-marketplace="${id}" aria-selected="${active ? "true" : "false"}">${escapeHtml(entry.label || id.toUpperCase())}</button>`;
      }).join("")}
    </div>
  `;
}

function financeDateToolbar(payload) {
  const label = arguments[1] || "Период отчёта";
  financeCalendarMin = payload.available_date_from || "";
  financeCalendarMax = payload.available_date_to || "";
  financeCalendarDraftFrom = payload.date_from || financeCalendarMin;
  financeCalendarDraftTo = payload.date_to || financeCalendarMax;
  financeCalendarViewMonth = financeShiftMonth(financeMonthStart(financeCalendarDraftTo || financeCalendarMax), -1);
  const appliedLabel = `${financeFormatRuDate(financeCalendarDraftFrom)} — ${financeFormatRuDate(financeCalendarDraftTo)}`;
  return `
    <section class="finance-toolbar finance-period-toolbar">
      ${financeMarketplaceSwitch(payload)}
      <div class="finance-period-picker">
        <span class="finance-period-label">${escapeHtml(label)}</span>
        <button id="financePeriodToggle" type="button" class="finance-period-trigger" data-finance-calendar-action="toggle" aria-haspopup="dialog" aria-expanded="false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M7 3v4M17 3v4M3 10h18"></path></svg>
          <span>${escapeHtml(appliedLabel)}</span>
          <i aria-hidden="true">⌄</i>
        </button>
        <input id="financeDateFrom" type="hidden" value="${escapeHtml(financeCalendarDraftFrom)}" />
        <input id="financeDateTo" type="hidden" value="${escapeHtml(financeCalendarDraftTo)}" />
        <div id="financeCalendarPopover" class="finance-calendar-popover hidden" role="dialog" aria-label="Выбор периода">
          ${financeCalendarBodyHtml()}
        </div>
      </div>
      <span class="finance-period-availability">Доступны данные: <strong>${escapeHtml(financeFormatRuDate(financeCalendarMin))}</strong> — <strong>${escapeHtml(financeFormatRuDate(financeCalendarMax))}</strong></span>
    </section>
  `;
}

function financeMethodology(methodology) {
  return `
    <details class="finance-method">
      <summary>Как считается отчет</summary>
      ${Object.values(methodology || {}).map((text) => `<p>${escapeHtml(text)}</p>`).join("")}
    </details>
  `;
}

function renderFinanceStatementChart(rows) {
  const max = Math.max(...(rows || []).map((row) => Math.abs(Number(row.amount || 0))), 1);
  return `
    <div class="finance-bars">
      ${(rows || []).map((row) => {
        const amount = Number(row.amount || 0);
        const width = Math.max(2, Math.abs(amount) / max * 100);
        const tone = row.amount === null || row.amount === undefined ? "missing" : (amount < 0 ? "negative" : "positive");
        return `
          <div class="finance-bar-row">
            <span>${escapeHtml(row.label)}</span>
            <div class="finance-bar-track"><i class="${tone}" style="width:${width}%"></i></div>
            <strong>${financeMoney(row.amount)}</strong>
          </div>
        `;
      }).join("")}
    </div>
  `;
}


let financeUnitActiveTab = "actual";
let financeUnitPayload = null;
window.__financeUnitPage = window.__financeUnitPage || 1;

function captureFinanceUnitDraft() {
  const root = qs("financeDashboard");
  if (!root || !financeUnitPayload) return;
  financeUnitPayload.global_settings = financeUnitPayload.global_settings || {};
  root.querySelectorAll("[data-finance-global]").forEach((input) => {
    financeUnitPayload.global_settings[input.dataset.financeGlobal] = financeNumericValue(input);
  });
  const rowsBySku = new Map((financeUnitPayload.rows || []).map((row) => [String(row.sku), row]));
  root.querySelectorAll("[data-finance-sku-row]").forEach((tr) => {
    const row = rowsBySku.get(String(tr.dataset.financeSkuRow));
    if (!row) return;
    tr.querySelectorAll("[data-finance-field]").forEach((input) => {
      row[input.dataset.financeField] = financeNumericValue(input);
    });
  });
}

function renderUnitEconomicsDashboard(payload) {
  financeUnitPayload = payload;
  const root = qs("financeDashboard");
  const totals = payload.totals || {};
  const globals = payload.global_settings || {};
  const rows = payload.rows || [];
  const marketplace = payload.marketplace === "wb" ? "WB" : "Ozon";
  const marketplaceApi = `${marketplace} API`;
  const taxWarning = totals.tax_configured
    ? ""
    : '<div class="finance-alert finance-alert-warning">Налог с дохода не задан. Прибыль и коридор МРЦ–РРЦ не рассчитываются, пока ставка не будет сохранена явно.</div>';
  const cogsWarning = Number(totals.cogs_coverage_units_pct || 0) >= 100
    ? ""
    : `<div class="finance-alert finance-alert-warning">Себестоимость заполнена для ${financePercent(totals.cogs_coverage_units_pct)} проданных единиц. Полученные ответы ${marketplaceApi} не содержат это поле: внесите себестоимость в модели или загрузите отдельный файл. Пустые SKU не считаются нулевой себестоимостью.</div>`;
  const moneyCell = (value, tone = false) => `<td class="num ${tone && value != null && value < 0 ? "finance-negative" : ""}">${financeMoney(value, 2)}</td>`;
  const productCell = (row) => {
    const dimensionParts = [row.depth, row.width, row.height].filter((value) => value !== null && value !== undefined);
    const dimensions = dimensionParts.length === 3
      ? `${dimensionParts.map((value) => formatNumber(value, 0)).join("×")} ${escapeHtml(row.dimension_unit || "")}`
      : "габариты: нет";
    const weight = row.weight == null ? "" : ` · ${formatNumber(row.weight, 0)} ${escapeHtml(row.weight_unit || "")}`;
    return `<td class="finance-product finance-sticky-product"><strong>${escapeHtml(row.article || row.sku)}</strong><span>${escapeHtml(row.product_name || "")}</span><small>SKU ${escapeHtml(row.sku)} · ${escapeHtml(row.delivery_schema || "")} · ${dimensions}${weight}</small><small>${escapeHtml(row.dimension_source || marketplaceApi)}</small></td>`;
  };
  const modelMetaCell = (row) => `<td>${financePercent(row.commission_pct)} комиссия<small>${financePercent(row.model_acquiring_pct)} эквайринг · ${financePercent(row.return_rate_pct_effective)} возвраты</small><small>${marketplace === "Ozon" ? `<span class="finance-index finance-index-${escapeHtml(String(row.price_index_color || "unknown").toLowerCase())}">${escapeHtml(row.price_index_color || "нет индекса")}</span> ${row.ozon_price_index == null ? "" : formatNumber(row.ozon_price_index, 2)}` : "Тарифы и сценарий WB"}</small></td>`;
  const scenarioCells = (scenario) => {
    const item = scenario || {};
    return `
      ${moneyCell(item.price)}
      ${moneyCell(item.commission)}
      ${moneyCell(item.acquiring)}
      ${moneyCell(item.forward_logistics)}
      ${moneyCell(item.reverse_logistics)}
      ${moneyCell(item.tax)}
      ${moneyCell(item.vat)}
      ${moneyCell(item.advertising)}
      ${moneyCell(item.capital)}
      ${moneyCell(item.cogs)}
      ${moneyCell(item.seller_costs)}
      ${moneyCell(item.total_costs)}
      ${moneyCell(item.profit, true)}
      <td class="num ${item.margin_pct != null && item.margin_pct < 0 ? "finance-negative" : ""}">${financePercent(item.margin_pct)}</td>
    `;
  };
  const scenarioHead = `
    <th>Цена</th><th>Комиссия</th><th>Эквайринг</th><th>Прямая лог.</th>
    <th>Обратная лог.</th><th>Налог</th><th>НДС</th><th>Реклама</th>
    <th>Стоимость денег</th><th>Себестоимость</th><th>Расходы продавца</th>
    <th>Все расходы</th><th>Прибыль</th><th>Маржа</th>
  `;
  const actualTable = `
    <div class="finance-table-wrap">
      <table class="finance-table finance-unit-table finance-unit-table-actual">
        <thead><tr><th>SKU / товар</th><th>Модель ${marketplace}</th><th>Ср. цена</th><th>Шт.</th><th>Выручка</th><th>Комиссия / ед.</th><th>Эквайринг / ед.</th><th>Прямая лог. / ед.</th><th>Обратная лог. / ед.</th><th>Налог / ед.</th><th>НДС / ед.</th><th>Стоимость денег / ед.</th><th>Реклама / ед.</th><th>Прибыль / ед.</th><th>Маржа</th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr>
            ${productCell(row)}${modelMetaCell(row)}
            ${moneyCell(row.actual_average_price)}
            <td class="num">${formatNumber(row.actual_units, 0)}</td>
            ${moneyCell(row.actual_revenue)}
            <td class="num">${financeMoney(row.actual_commission_per_unit, 2)}<small>${financePercent(row.actual_commission_pct)}</small></td>
            <td class="num">${financeMoney(row.actual_acquiring_per_unit, 2)}<small>${financePercent(row.actual_acquiring_pct)}</small></td>
            ${moneyCell(row.actual_forward_logistics_per_unit)}
            ${moneyCell(row.actual_reverse_logistics_per_unit)}
            ${moneyCell(row.actual_tax_per_unit)}
            ${moneyCell(row.actual_vat_per_unit)}
            ${moneyCell(row.actual_capital_per_unit)}
            <td class="num">${financeMoney(row.actual_ad_per_unit, 2)}<small>${financePercent(row.actual_ad_pct)}</small></td>
            ${moneyCell(row.actual_profit_per_unit, true)}
            <td class="num ${row.actual_margin_pct != null && row.actual_margin_pct < 0 ? "finance-negative" : ""}">${financePercent(row.actual_margin_pct)}</td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  const currentTable = `
    <div class="finance-table-wrap">
      <table class="finance-table finance-unit-table finance-unit-table-scenario">
        <thead><tr><th>SKU / товар</th><th>Тарифы ${marketplace}</th>${scenarioHead}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${productCell(row)}${modelMetaCell(row)}${scenarioCells(row.current_scenario)}</tr>`).join("")}</tbody>
      </table>
    </div>`;
  const corridorCell = (row) => `<td class="num finance-corridor-cell"><strong>${financeMoney(row.mrc_price, 2)} — ${financeMoney(row.rrc_price, 2)}</strong><small>ширина ${financeMoney(row.price_corridor_width, 2)}</small></td>`;
  const plannedTable = `
    <div class="finance-table-wrap">
      <table class="finance-table finance-unit-table finance-unit-table-plan">
        <thead><tr><th>SKU / товар</th><th class="finance-editable">Себестоимость</th><th class="finance-editable">Длина</th><th class="finance-editable">Ширина</th><th class="finance-editable">Высота</th><th class="finance-editable">Вес</th><th class="finance-editable">Возвраты, %</th><th class="finance-editable">Фулфилмент</th><th class="finance-editable">Поставка</th><th class="finance-editable">Прочее</th><th>МРЦ</th><th>Маржа МРЦ</th><th>РРЦ</th><th>Маржа РРЦ</th><th>Ценовой коридор</th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr data-finance-sku-row="${escapeHtml(row.sku)}">
            ${productCell(row)}
            <td class="finance-editable"><input class="finance-input" data-finance-field="cogs_per_unit" type="number" step="0.01" min="0" value="${financeInputValue(row.cogs_per_unit)}" /></td>
            <td class="finance-editable"><input class="finance-input finance-dimension-input" data-finance-field="depth" type="number" step="0.01" min="0.01" value="${financeInputValue(row.depth)}" /><small>${escapeHtml(row.dimension_unit || "mm")}</small></td>
            <td class="finance-editable"><input class="finance-input finance-dimension-input" data-finance-field="width" type="number" step="0.01" min="0.01" value="${financeInputValue(row.width)}" /><small>${escapeHtml(row.dimension_unit || "mm")}</small></td>
            <td class="finance-editable"><input class="finance-input finance-dimension-input" data-finance-field="height" type="number" step="0.01" min="0.01" value="${financeInputValue(row.height)}" /><small>${escapeHtml(row.dimension_unit || "mm")}</small></td>
            <td class="finance-editable"><input class="finance-input finance-dimension-input" data-finance-field="weight" type="number" step="0.01" min="0.01" value="${financeInputValue(row.weight)}" /><small>${escapeHtml(row.weight_unit || "g")}</small></td>
            <td class="finance-editable"><input class="finance-input" data-finance-field="return_rate_pct" type="number" step="0.01" min="0" max="100" value="${financeInputValue(row.return_rate_pct)}" placeholder="${financeInputValue(row.return_rate_pct_effective)}" /><small>${escapeHtml(row.reverse_logistics_source || "")}</small></td>
            <td class="finance-editable"><input class="finance-input" data-finance-field="fulfillment_per_unit" type="number" step="0.01" min="0" value="${financeInputValue(row.fulfillment_per_unit)}" /></td>
            <td class="finance-editable"><input class="finance-input" data-finance-field="inbound_per_unit" type="number" step="0.01" min="0" value="${financeInputValue(row.inbound_per_unit)}" /></td>
            <td class="finance-editable"><input class="finance-input" data-finance-field="other_per_unit" type="number" step="0.01" min="0" value="${financeInputValue(row.other_per_unit)}" /></td>
            ${moneyCell(row.mrc_price)}
            <td class="num">${financePercent(row.mrc_scenario?.margin_pct)}</td>
            ${moneyCell(row.rrc_price)}
            <td class="num">${financePercent(row.rrc_scenario?.margin_pct)}</td>
            ${corridorCell(row)}
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  const forecastTable = `
    <div class="finance-table-wrap">
      <table class="finance-table finance-unit-table finance-unit-table-forecast">
        <thead><tr><th>SKU / товар</th><th>Тарифы ${marketplace}</th><th>Текущая цена</th><th>МРЦ</th><th>РРЦ</th><th>Ценовой коридор</th>${scenarioHead}</tr></thead>
        <tbody>${rows.map((row) => `
          <tr>
            ${productCell(row)}${modelMetaCell(row)}
            ${moneyCell(row.current_price)}
            ${moneyCell(row.mrc_price)}
            ${moneyCell(row.rrc_price)}
            ${corridorCell(row)}
            ${scenarioCells(row.rrc_scenario)}
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  const activeTable = {
    actual: actualTable,
    current: currentTable,
    planned: plannedTable,
    forecast: forecastTable,
  }[financeUnitActiveTab] || actualTable;
  root.innerHTML = `
    ${financeDateToolbar(payload)}
    <section class="finance-card finance-model-settings">
      <header class="finance-card-head">
        <div><h2>Параметры модели ${escapeHtml(clientLabel())}</h2><p>Единые допущения сверху, детализация и цены — по SKU. Тарифы комиссии и логистики берутся из ${marketplaceApi}.</p></div>
        <button type="button" data-finance-action="save-unit">Сохранить и пересчитать</button>
      </header>
      <div class="finance-settings-grid finance-settings-grid-model">
        <label>Налог с дохода, %<input class="finance-input" data-finance-global="tax_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.tax_pct)}" /></label>
        <label>НДС, %<input class="finance-input" data-finance-global="vat_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.vat_pct ?? 0)}" /></label>
        <label>Эквайринг для плана, %<input class="finance-input" data-finance-global="acquiring_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.acquiring_pct)}" placeholder="Текущий тариф ${marketplace}" /></label>
        <label>Срок капитала, дней<input class="finance-input" data-finance-global="capital_days" type="number" min="0" max="3650" step="1" value="${financeInputValue(globals.capital_days ?? 30)}" /></label>
        <label>Стоимость денег, % год<input class="finance-input" data-finance-global="capital_rate_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.capital_rate_pct ?? 20)}" /></label>
        <label>Реклама для плана, %<input class="finance-input" data-finance-global="advertising_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.advertising_pct)}" placeholder="Факт Performance по SKU" /></label>
        <label>Возвраты для плана, %<input class="finance-input" data-finance-global="return_rate_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.return_rate_pct)}" placeholder="Факт обратной логистики" /></label>
        <label>Цель по марже МРЦ, %<input class="finance-input" data-finance-global="mrc_margin_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.mrc_margin_pct ?? 0)}" /></label>
        <label>Цель по марже РРЦ, %<input class="finance-input" data-finance-global="rrc_margin_pct" type="number" min="0" max="100" step="0.01" value="${financeInputValue(globals.rrc_margin_pct ?? globals.target_margin_pct ?? 20)}" /></label>
      </div>
    </section>
    ${taxWarning}${cogsWarning}
    <section class="finance-kpi-grid">
      ${financeMetricCard("Продажи и возвраты", financeMoney(totals.revenue))}
      ${financeMetricCard(`Эквайринг ${marketplace}`, financeMoney(totals.acquiring))}
      ${financeMetricCard("Прямая логистика", financeMoney(totals.forward_logistics))}
      ${financeMetricCard("Обратная логистика", financeMoney(totals.reverse_logistics))}
      ${financeMetricCard("НДС модели", financeMoney(totals.vat), `ставка ${financePercent(globals.vat_pct ?? 0)}`)}
      ${financeMetricCard("Покрытие себестоимостью", financePercent(totals.cogs_coverage_units_pct), `${formatNumber(totals.sku_count || 0)} SKU`, Number(totals.cogs_coverage_units_pct || 0) < 100 ? "warning" : "good")}
    </section>
    <section class="finance-card finance-unit-workspace">
      <header class="finance-card-head">
        <div><h2>Юнит-экономика по SKU</h2><p>МРЦ и РРЦ рассчитываются автоматически под две цели маржи. Габариты модели можно скорректировать по SKU.</p></div>
        ${financeUnitActiveTab === "planned" ? '<button type="button" data-finance-action="save-unit">Сохранить план</button>' : ""}
      </header>
      <nav class="finance-unit-tabs" aria-label="Сценарии юнит-экономики">
        ${[
          ["actual", "Факт периода", `Точные начисления ${marketplace}`],
          ["current", "Текущая", "Цена и тарифы сейчас"],
          ["planned", "Модель", "Себестоимость, габариты и расходы"],
          ["forecast", "Коридор цен", "МРЦ и РРЦ под цели маржи"],
        ].map(([key, label, note]) => `<button type="button" class="${financeUnitActiveTab === key ? "active" : ""}" data-finance-unit-tab="${key}"><strong>${label}</strong><span>${note}</span></button>`).join("")}
      </nav>
      <div class="finance-unit-tab-panel" data-active-unit-tab="${financeUnitActiveTab}">
        ${activeTable}
      </div>
    </section>
    ${financeMethodology(payload.methodology)}
  `;
}

function renderProfitLossDashboard(payload) {
  const root = qs("financeDashboard");
  const totals = payload.totals || {};
  const ready = totals.net_profit !== null && totals.net_profit !== undefined;
  const rows = (payload.products || []).slice(0, 300);
  const taxWarning = totals.tax_configured ? "" : '<div class="finance-alert finance-alert-warning">Налог не задан. Чистая прибыль скрыта до явного сохранения ставки в «Юнит-экономике».</div>';
  const cogsWarning = Number(totals.cogs_coverage_units_pct || 0) >= 100 ? "" : `<div class="finance-alert finance-alert-warning">Покрытие себестоимостью ${financePercent(totals.cogs_coverage_units_pct)}. Неполная себестоимость не подменяется нулём, поэтому чистая прибыль скрыта.</div>`;
  const unallocated = Number(totals.unallocated_marketplace_net || 0);
  root.innerHTML = `
    ${financeDateToolbar(payload)}
    ${taxWarning}${cogsWarning}
    <section class="finance-kpi-grid">
      ${financeMetricCard("Продажи и возвраты", financeMoney(totals.revenue))}
      ${financeMetricCard("Итого начислено Ozon", financeMoney(totals.marketplace_net), `${formatNumber(totals.events || 0)} начислений`)}
      ${financeMetricCard("Себестоимость", Number(totals.cogs_coverage_units_pct || 0) >= 100 ? financeMoney(totals.cogs) : "неполная", `покрытие ${financePercent(totals.cogs_coverage_units_pct)}`, Number(totals.cogs_coverage_units_pct || 0) < 100 ? "warning" : "")}
      ${financeMetricCard("Чистая прибыль", ready ? financeMoney(totals.net_profit) : "не рассчитана", ready ? financePercent(totals.margin_pct) : "нужны налог и полная себестоимость", ready && totals.net_profit < 0 ? "bad" : (ready ? "good" : "warning"))}
      ${financeMetricCard("Не распределено по SKU", financeMoney(unallocated), "общие начисления Ozon показаны отдельно", Math.abs(unallocated) > 0.01 ? "warning" : "")}
      ${financeMetricCard("Источники", `${formatNumber(totals.xlsx_events || 0)} XLSX / ${formatNumber(totals.api_events || 0)} API`, `${payload.available_date_from} — ${payload.available_date_to}`)}
    </section>
    <section class="finance-grid finance-grid-2">
      <article class="finance-card">
        <header class="finance-card-head"><div><h2>P&amp;L периода</h2><p>Знаки сохранены как в начислениях Ozon.</p></div></header>
        ${renderFinanceStatementChart(payload.statement || [])}
      </article>
      <article class="finance-card">
        <header class="finance-card-head">
          <div><h2>Прочие расходы периода</h2><p>Задаются для выбранного диапазона и вычитаются из прибыли.</p></div>
          <button type="button" class="ghost" data-finance-action="add-expense">Добавить</button>
        </header>
        <div class="finance-expenses" id="financeExpenses">
          ${(payload.expenses || []).map((row) => `
            <div class="finance-expense-row">
              <input class="finance-input" data-expense-field="expense_name" placeholder="Статья расхода" value="${financeInputValue(row.expense_name)}" />
              <input class="finance-input" data-expense-field="amount" type="number" min="0" step="0.01" placeholder="Сумма" value="${financeInputValue(row.amount)}" />
              <input class="finance-input" data-expense-field="notes" placeholder="Комментарий" value="${financeInputValue(row.notes)}" />
              <button type="button" class="ghost" data-finance-action="remove-expense">×</button>
            </div>
          `).join("")}
        </div>
        <button type="button" data-finance-action="save-expenses">Сохранить расходы</button>
      </article>
    </section>
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>Динамика по месяцам</h2><p>Прибыль показывается только при полной себестоимости и заданном налоге.</p></div></header>
      <div class="finance-table-wrap">
        <table class="finance-table"><thead><tr><th>Месяц</th><th>Продажи</th><th>Единицы</th><th>Начислено Ozon</th><th>Себестоимость</th><th>Налог</th><th>Чистая прибыль</th><th>Маржа</th></tr></thead>
        <tbody>${(payload.monthly || []).map((row) => `<tr><td>${escapeHtml(row.month)}</td><td class="num">${financeMoney(row.revenue)}</td><td class="num">${formatNumber(row.units)}</td><td class="num">${financeMoney(row.marketplace_net)}</td><td class="num">${row.cogs_complete === false ? "неполная" : financeMoney(row.cogs)}</td><td class="num">${payload.totals.tax_configured ? financeMoney(row.tax) : "—"}</td><td class="num">${financeMoney(row.net_profit)}</td><td class="num">${financePercent(row.margin_pct)}</td></tr>`).join("")}</tbody></table>
      </div>
    </section>
    <section class="finance-card">
      <header class="finance-card-head"><div><h2>Вклад SKU</h2><p>Только начисления с точной привязкой к SKU; показаны первые ${formatNumber(rows.length)} строк.</p></div></header>
      <div class="finance-table-wrap">
        <table class="finance-table"><thead><tr><th>SKU / товар</th><th>Единицы</th><th>Продажи</th><th>Комиссия</th><th>Логистика</th><th>Прочее Ozon</th><th>Компонент Ozon</th><th>Себестоимость</th><th>Прибыль до общих расходов</th><th>Маржа</th></tr></thead>
        <tbody>${rows.map((row) => `<tr><td class="finance-product"><strong>${escapeHtml(row.article || row.sku)}</strong><span>${escapeHtml(row.product_name || "")}</span><small>SKU ${escapeHtml(row.sku)}</small></td><td class="num">${formatNumber(row.units)}</td><td class="num">${financeMoney(row.revenue)}</td><td class="num">${financeMoney(row.commission)}</td><td class="num">${financeMoney(row.logistics)}</td><td class="num">${financeMoney(row.other_ozon)}</td><td class="num">${financeMoney(row.marketplace_component_net)}</td><td class="num">${row.cogs_status === "missing" ? "не задана" : financeMoney(row.cogs)}</td><td class="num">${financeMoney(row.profit_before_common_costs)}</td><td class="num">${financePercent(row.margin_pct)}</td></tr>`).join("")}</tbody></table>
      </div>
    </section>
    ${financeMethodology(payload.methodology)}
  `;
}

function financeNumericValue(input) {
  if (!input || input.value.trim() === "") return null;
  const value = Number(input.value);
  if (!Number.isFinite(value)) throw new Error(`Некорректное число: ${input.value}`);
  return value;
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

async function importFinanceCogs(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".xlsx")) throw new Error("Выберите файл XLSX");
  if (file.size > 8 * 1024 * 1024) throw new Error("Размер XLSX не должен превышать 8 МБ");
  const result = await postJson("/api/km-trade/unit-cogs-import", {
    client: currentClient(),
    filename: file.name,
    file_base64: await fileAsBase64(file),
  });
  await renderKmFinanceDashboard();
  const settingsCard = qs("financeDashboard")?.querySelector(".kmue-settings-card");
  settingsCard?.insertAdjacentHTML(
    "afterend",
    `<div class="kmue-save-notice" role="status">Себестоимость загружена: ${formatNumber(result.saved_rows || 0)} SKU</div>`,
  );
}

async function saveFinanceUnitSettings() {
  captureFinanceUnitDraft();
  const source = financeUnitPayload || { rows: [], global_settings: {} };
  const rows = (source.rows || []).map((row) => ({
    sku: row.sku,
    cogs_per_unit: row.cogs_per_unit ?? null,
    return_rate_pct: row.return_rate_pct ?? null,
    target_advertising_pct: row.target_advertising_pct ?? null,
    fulfillment_per_unit: row.fulfillment_per_unit ?? 0,
    inbound_per_unit: row.inbound_per_unit ?? 0,
    other_per_unit: row.other_per_unit ?? 0,
    depth: row.depth ?? null,
    width: row.width ?? null,
    height: row.height ?? null,
    dimension_unit: row.dimension_unit || row.ozon_dimension_unit || "mm",
    weight: row.weight ?? null,
    weight_unit: row.weight_unit || row.ozon_weight_unit || "g",
    notes: row.notes || "",
  }));
  const result = await postJson("/api/km-trade/unit-settings", {
    client: currentClient(),
    rows,
    global_settings: source.global_settings || {},
  });
  await renderKmFinanceDashboard();
  const settingsCard = qs("financeDashboard")?.querySelector(".kmue-settings-card");
  settingsCard?.insertAdjacentHTML(
    "afterend",
    `<div class="kmue-save-notice" role="status">Настройки сохранены: ${formatNumber(result.saved_rows || 0)} SKU</div>`,
  );
}

function addFinanceExpenseRow() {
  const holder = qs("financeExpenses");
  if (!holder) return;
  holder.insertAdjacentHTML("beforeend", `
    <div class="finance-expense-row">
      <input class="finance-input" data-expense-field="expense_name" placeholder="Статья расхода" />
      <input class="finance-input" data-expense-field="amount" type="number" min="0" step="0.01" placeholder="Сумма" />
      <input class="finance-input" data-expense-field="notes" placeholder="Комментарий" />
      <button type="button" class="ghost" data-finance-action="remove-expense">×</button>
    </div>
  `);
}

async function saveFinanceExpenses() {
  const rows = [...qs("financeDashboard").querySelectorAll(".finance-expense-row")].filter(row=>row.querySelector('input[type="checkbox"]')?.checked !== false).map((row) => ({
    expense_name: row.querySelector('[data-expense-field="expense_name"]')?.value || "",
    amount: financeNumericValue(row.querySelector('[data-expense-field="amount"]')),
    notes: row.querySelector('[data-expense-field="notes"]')?.value || "",
    allocation_method: "period",
  }));
  await postJson("/api/km-trade/pl-expenses", {
    client: currentClient(),
    marketplace: currentMarketplace(),
    date_from: qs("financeDateFrom").value,
    date_to: qs("financeDateTo").value,
    rows,
  });
  await renderKmFinanceDashboard();
}

function financeRequestIsTransient(error) {
  const message = String(error?.message || error || "");
  return error instanceof TypeError
    || /failed to fetch|networkerror|load failed|http 50[234]/i.test(message);
}

async function getFinanceJsonWithRetry(url) {
  const delays = [0, 450, 1200];
  let lastError = null;
  for (let attempt = 0; attempt < delays.length; attempt += 1) {
    if (delays[attempt]) {
      qs("status").textContent = `Связь с BI прервалась · повтор ${attempt + 1}/${delays.length}...`;
      await new Promise((resolve) => window.setTimeout(resolve, delays[attempt]));
    }
    try {
      return await getJson(url);
    } catch (error) {
      lastError = error;
      if (!financeRequestIsTransient(error) || attempt === delays.length - 1) throw error;
    }
  }
  throw lastError || new Error("Не удалось загрузить финансовый отчёт");
}

async function renderKmFinanceDashboard() {
  setFinanceShellVisible(true);
  const reportName = state.dashboard === "profitLoss" ? "P&L" : "Юнит-экономика";
  const root = qs("financeDashboard");
  qs("dashboardTitle").textContent = reportName;
  qs("status").textContent = `${clientLabel()} · ${currentMarketplaceLabel()}: загрузка ${reportName}...`;
  root.innerHTML = `<section class="finance-card finance-loading-card" role="status">
    <h2>Загрузка: ${escapeHtml(reportName)}</h2>
    <p>Получаем расчёты ${escapeHtml(clientLabel())} из базы BI…</p>
  </section>`;
  const params = new URLSearchParams();
  if (qs("date_from")?.value) params.set("date_from", qs("date_from").value);
  if (qs("date_to")?.value) params.set("date_to", qs("date_to").value);
  params.set("client", currentClient());
  params.set("marketplace", currentMarketplace());
  if (state.dashboard === "unitEconomics") {
    params.set("page", String(window.__financeUnitPage || 1));
    params.set("limit", "100");
  }
  const endpoint = state.dashboard === "profitLoss" ? "/api/km-trade/pl" : "/api/km-trade/unit-economics";
  try {
    const payload = await getFinanceJsonWithRetry(`${endpoint}?${params.toString()}`);
    if (state.dashboard === "profitLoss" && payload.marketplace) setMarketplaceValue(payload.marketplace);
    qs("date_from").value = payload.date_from;
    qs("date_to").value = payload.date_to;
    if (state.dashboard === "profitLoss") renderProfitLossDashboard(payload);
    else renderUnitEconomicsDashboard(payload);
    qs("status").textContent = `${clientLabel()} · ${currentMarketplaceLabel()}: ${reportName} за ${payload.date_from} — ${payload.date_to} · обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
  } catch (error) {
    root.innerHTML = `<section class="finance-card finance-alert finance-alert-warning">
      <h2>${escapeHtml(reportName)} временно не загрузилась</h2>
      <p>${escapeHtml(error?.message || error)}</p>
      <button type="button" data-finance-action="retry-load">Повторить загрузку</button>
    </section>`;
    throw error;
  }
}

qs("financeDashboard")?.addEventListener("click", (event) => {
  const marketplaceButton = event.target.closest("[data-finance-marketplace]");
  if (marketplaceButton) {
    const marketplace = marketplaceButton.dataset.financeMarketplace;
    if (marketplace && marketplace !== currentMarketplace()) {
      setMarketplaceValue(marketplace);
      persistDashboardState();
      renderKmFinanceDashboard().catch((error) => setStatusError(error));
    }
    return;
  }
  const calendarButton = event.target.closest("[data-finance-calendar-action]");
  if (calendarButton) {
    handleFinanceCalendarAction(calendarButton);
    return;
  }
  const tabButton = event.target.closest("[data-finance-unit-tab]");
  if (tabButton) {
    captureFinanceUnitDraft();
    financeUnitActiveTab = tabButton.dataset.financeUnitTab || "actual";
    renderUnitEconomicsDashboard(financeUnitPayload);
    return;
  }
  const button = event.target.closest("[data-finance-action]");
  if (!button) return;
  const action = button.dataset.financeAction;
  button.disabled = true;
  Promise.resolve()
    .then(async () => {
      if (action === "retry-load") {
        await renderKmFinanceDashboard();
      } else if (action === "apply-period") {
        if (!financeCalendarDraftFrom || !financeCalendarDraftTo) return;
        qs("financeDateFrom").value = financeCalendarDraftFrom;
        qs("financeDateTo").value = financeCalendarDraftTo;
        qs("date_from").value = financeCalendarDraftFrom;
        qs("date_to").value = financeCalendarDraftTo;
        closeFinanceCalendar();
        await renderKmFinanceDashboard();
      } else if (action === "save-unit") {
        await saveFinanceUnitSettings();
      } else if (action === "import-cogs") {
        qs("financeDashboard")?.querySelector("[data-finance-cogs-file]")?.click();
      } else if (action === "add-expense") {
        addFinanceExpenseRow();
      } else if (action === "remove-expense") {
        button.closest(".finance-expense-row")?.remove();
      } else if (action === "save-expenses") {
        await saveFinanceExpenses();
      }
    })
    .catch((error) => {
      setStatusError(error);
    })
    .finally(() => {
      if (button.isConnected) button.disabled = false;
    });
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".finance-period-picker")) closeFinanceCalendar();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeFinanceCalendar();
});

function updateNavigation() {
  document.body.classList.toggle("admin-shell-active", state.dashboard === "admin");
  if (state.dashboard !== "admin") clearAdminTopbarTabs();
  qs("navPinnedTitle")?.classList.toggle("hidden", state.clientLocked);
  qs("navClientReview")?.classList.add("hidden");
  qs("navPortfolio")?.classList.toggle("hidden", state.clientLocked);
  qs("navAdmin").classList.toggle("hidden", state.clientLocked || !adminAccessEnabled());
  const avitoVisible = [...avitoDashboards].some((report) => clientSupportsReport(report));
  const yandexVisible = [...yandexDashboards].some((report) => clientSupportsReport(report));
  const avitoOnly = isAvitoOnlyClient();
  qs("navAvitoTitle")?.classList.toggle("hidden", !avitoVisible);
  qs("navYandexTitle")?.classList.toggle("hidden", !yandexVisible);
  qs("navReportsTitle")?.classList.toggle("hidden", avitoOnly);
  qs("navAdvertisingTitle")?.classList.toggle("hidden", avitoOnly);
  qs("navSalesTitle")?.classList.toggle("hidden", avitoOnly);
  qs("navSeoTitle")?.classList.toggle("hidden", avitoOnly);
  qs("navAbcTitle")?.classList.toggle("hidden", avitoOnly);
  qs("navAbc").classList.toggle("hidden", avitoOnly || !clientSupportsReport("abc"));
  qs("navProduct").classList.toggle("hidden", avitoOnly || !clientSupportsReport("product"));
  qs("navSku").classList.toggle("hidden", avitoOnly || !clientSupportsReport("sku"));
  qs("navAdv").classList.toggle("hidden", avitoOnly || !clientSupportsReport("adv"));
  qs("navMediaAdv").classList.toggle("hidden", avitoOnly || !clientSupportsReport("mediaAdv"));
  qs("navFunnel").classList.toggle("hidden", avitoOnly || !clientSupportsReport("funnel"));
  qs("navWeeklyDynamics").classList.toggle("hidden", avitoOnly || !clientSupportsReport("weeklyDynamics"));
  const inventoryHistoryVisible = clientSupportsReport("inventoryHistory");
  qs("navLogisticsTitle")?.classList.toggle("hidden", avitoOnly || !inventoryHistoryVisible);
  qs("navInventoryHistory")?.classList.toggle("hidden", avitoOnly || !inventoryHistoryVisible);
  qs("navPlanFact").classList.toggle("hidden", avitoOnly || !clientSupportsReport("planfact"));
  qs("navSalesPlanning")?.classList.toggle("hidden", avitoOnly || !clientSupportsReport("salesPlanning"));
  qs("navMediaPlan")?.classList.toggle("hidden", avitoOnly || !clientSupportsReport("mediaPlan"));
  const financeVisible = clientSupportsReport("profitLoss") || clientSupportsReport("unitEconomics");
  qs("navFinanceTitle").classList.toggle("hidden", avitoOnly || !financeVisible);
  qs("navProfitLoss").classList.toggle("hidden", avitoOnly || !clientSupportsReport("profitLoss"));
  qs("navUnitEconomics").classList.toggle("hidden", avitoOnly || !clientSupportsReport("unitEconomics"));
  const reviewsVisible = clientSupportsReport("reviews");
  qs("navClientsTitle")?.classList.toggle("hidden", avitoOnly || !reviewsVisible);
  qs("navReviews")?.classList.toggle("hidden", avitoOnly || !reviewsVisible);
  if (qs("navReviews")) qs("navReviews").href = resolveAppUrl(`/react/?client=${encodeURIComponent(currentClient())}&dashboard=reviews`);
  qs("navSeoMonitoring").classList.toggle("hidden", avitoOnly || !clientSupportsReport("seoMonitoring"));
  qs("navWbSearchQueries").classList.toggle("hidden", avitoOnly || !clientSupportsReport("wbSearchQueries"));
  qs("navWbEntrance").classList.toggle("hidden", avitoOnly || !clientSupportsReport("wbEntrance"));
  qs("navSeoBot")?.classList.toggle("hidden", avitoOnly || !clientSupportsReport("seoMonitoring"));
  [...avitoDashboards].forEach((report) => {
    const suffix = report.replace(/^avito/, "Avito");
    qs(`nav${suffix}`)?.classList.toggle("hidden", !clientSupportsReport(report));
    qs(`nav${suffix}`)?.classList.toggle("active", state.dashboard === report);
  });
  [...yandexDashboards].forEach((report) => {
    const suffix = report.replace(/^yandex/, "Yandex");
    qs(`nav${suffix}`)?.classList.toggle("hidden", !clientSupportsReport(report));
    qs(`nav${suffix}`)?.classList.toggle("active", state.dashboard === report);
  });
  qs("navAbc").classList.toggle("active", state.dashboard === "abc");
  qs("navProduct").classList.toggle("active", state.dashboard === "product");
  qs("navAdv").classList.toggle("active", state.dashboard === "adv");
  qs("navMediaAdv").classList.toggle("active", state.dashboard === "mediaAdv");
  qs("navFunnel").classList.toggle("active", state.dashboard === "funnel");
  qs("navWeeklyDynamics").classList.toggle("active", state.dashboard === "weeklyDynamics");
  qs("navInventoryHistory")?.classList.toggle("active", state.dashboard === "inventoryHistory");
  qs("navSeoMonitoring").classList.toggle("active", state.dashboard === "seoMonitoring" && state.seoProjectsNavSource !== "semantics");
  qs("navSeoBot")?.classList.toggle("active", state.dashboard === "seoMonitoring" && state.seoProjectsNavSource === "semantics");
  qs("navWbSearchQueries").classList.toggle("active", state.dashboard === "wbSearchQueries");
  qs("navWbEntrance").classList.toggle("active", state.dashboard === "wbEntrance");
  qs("navPlanFact").classList.toggle("active", state.dashboard === "planfact");
  qs("navSalesPlanning")?.classList.toggle("active", state.dashboard === "salesPlanning");
  qs("navMediaPlan")?.classList.toggle("active", state.dashboard === "mediaPlan");
  qs("navProfitLoss").classList.toggle("active", state.dashboard === "profitLoss");
  qs("navUnitEconomics").classList.toggle("active", state.dashboard === "unitEconomics");
  qs("navSku").classList.toggle("active", state.dashboard === "sku");
  qs("navAdmin").classList.toggle("active", state.dashboard === "admin");
}

function setFilterFieldVisible(id, visible) {
  const element = qs(id);
  const label = element?.closest("label");
  label?.classList.toggle("hidden", !visible);
}

function compactFilterPanelEnabled() {
  return state.dashboard !== "admin";
}

function applyFilterPanelLayout() {
  const panel = document.querySelector(".filters");
  if (!panel) return;
  panel.classList.toggle("compact-filters", compactFilterPanelEnabled());
  panel.querySelectorAll("[data-filter-section-title]").forEach((item) => item.remove());
  const visibleGroups = new Set();
  const groupOffsets = {};
  [...panel.children].forEach((item) => {
    if (item.dataset.filterSectionTitle !== undefined) return;
    const group = item.dataset.filterGroup || "core";
    const baseOrder = filterGroupOrder[group] || 100;
    groupOffsets[group] = (groupOffsets[group] || 0) + 1;
    item.style.order = String(baseOrder + groupOffsets[group]);
    if (!item.classList.contains("hidden")) visibleGroups.add(group);
  });
  [...visibleGroups]
    .sort((left, right) => (filterGroupOrder[left] || 100) - (filterGroupOrder[right] || 100))
    .forEach((group) => {
      const title = document.createElement("div");
      title.className = "filter-group-title";
      title.dataset.filterSectionTitle = group;
      title.textContent = filterGroupTitles[group] || group;
      title.style.order = String(filterGroupOrder[group] || 100);
      panel.appendChild(title);
    });
}

function updateFilterVisibility() {
  const isAbc = state.dashboard === "abc";
  const isProduct = state.dashboard === "product";
  const isSku = state.dashboard === "sku";
  const capabilities = filterReportCapabilities();
  const isFunnel = state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory";
  const isSeoMonitoring = state.dashboard === "seoMonitoring";
  const isWbSearchQueries = state.dashboard === "wbSearchQueries";
  const isWbEntrance = state.dashboard === "wbEntrance";
  const isAdv = state.dashboard === "adv";
  const isMediaAdv = state.dashboard === "mediaAdv";
  const isPlanFact = state.dashboard === "planfact";
  const isAvito = isAvitoDashboard();
  const isYandex = isYandexDashboard();
  document.querySelector(".filters").classList.toggle("hidden", capabilities.admin);
  document.querySelector(".filters").classList.toggle("adv-filters", isProduct || isAdv || isMediaAdv || isFunnel || isPlanFact || isSeoMonitoring || isWbSearchQueries || isWbEntrance || isAvito || isYandex);
  document.querySelector(".filters").classList.toggle("funnel-filters", isProduct || isFunnel || isWbSearchQueries || isWbEntrance);
  document.querySelector(".filters").classList.toggle("abc-date-filters", isAbc || isProduct || isSku);
  document.querySelector(".filters").classList.toggle("assortment-filters", capabilities.assortment || capabilities.sportmasterAssortment || capabilities.boironBrand);
  document.querySelectorAll(".date-range-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.dateRange));
  document.querySelectorAll(".mapping-filter").forEach((item) => item.classList.toggle("hidden", !gloriaAssortmentFiltersEnabled()));
  document.querySelectorAll(".ozon-product-attribute-filter").forEach((item) => {
    const select = item.querySelector("select");
    const hasOptions = (select?.options?.length || 0) > 1;
    item.classList.toggle("hidden", !ozonProductAttributeFiltersEnabled() || !hasOptions);
  });
  document.querySelectorAll(".sportmaster-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.sportmasterAssortment));
  document.querySelectorAll(".boiron-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.boironBrand));
  document.querySelectorAll(".period-group-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.periodGroup));
  document.querySelectorAll(".media-level-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.mediaLevel));
  document.querySelectorAll(".media-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.mediaFilters));
  document.querySelectorAll(".media-group-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.mediaGroupFilter));
  document.querySelectorAll(".media-creative-filter").forEach((item) => item.classList.toggle("hidden", !capabilities.mediaCreativeFilter));
  document.querySelectorAll(".adv-campaign-filter").forEach((item) => {
    const select = item.querySelector("select");
    const hasOptions = (select?.options?.length || 0) > 1;
    item.classList.toggle("hidden", !isAdv || currentMarketplace() !== "ozon" || !hasOptions);
  });
  document.querySelectorAll(".wb-query-classification-filter").forEach((item) => item.classList.toggle("hidden", !isWbSearchQueries));
  document.querySelectorAll(".wb-entrance-filter").forEach((item) => item.classList.toggle("hidden", !isWbEntrance));
  setFilterFieldVisible("collection_status", collectionFilterEnabled());
  setFilterFieldVisible("seo_status", seoFilterEnabled());
  qs("categoryDropdown").closest("label").classList.toggle("hidden", !categoryFilterEnabled());
  setFilterFieldVisible("category_level", categoryLevelFilterEnabled());
  ["abc_orders", "abc_sales", "abc_stock", "abc_combined"].forEach((id) => setFilterFieldVisible(id, abcClassificationFiltersEnabled()));
  setFilterFieldVisible("sort", capabilities.sort);
  setFilterFieldVisible("limit", capabilities.limit);
  setFilterFieldVisible("product", productNameFilterEnabled());
  setFilterFieldVisible("article", articleFilterEnabled());
  setFilterFieldVisible("marketplace", !isAvito && !isYandex);
  setFilterFieldVisible("media_status", capabilities.mediaFilters);
  if (qs("categoryFilterLabel")) {
    qs("categoryFilterLabel").textContent = isMediaAdv ? "Формат" : (currentClient() === "gloria_jeans" && capabilities.assortment ? "Предмет / категория" : "Категория");
  }
  if (qs("productFilterLabel")) qs("productFilterLabel").textContent = (isWbSearchQueries || isWbEntrance) ? "Товар (WB SKU / артикул)" : (isMediaAdv ? "Кампания" : "Товар");
  if (qs("productSearch")) qs("productSearch").placeholder = (isWbSearchQueries || isWbEntrance) ? "Название, WB SKU или артикул" : (isProduct ? "Название или артикул" : "Введите наименование");
  qs("marketplace").disabled = capabilities.marketplaceLocked;
  updateCategoryLevelOptions();
  applyFilterPanelLayout();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function defaultRecentDateFrom(dateTo) {
  return shiftDate(dateTo, -(DEFAULT_RECENT_DAYS - 1));
}

const FRIENDLY_ERROR_STATES = {
  preparing: "Витрина еще собирается · загляните чуть позже",
  empty: "Здесь пока тихо · выберите другой период",
  access: "Доступ к данным пока не подтвержден",
  throttled: "Источник взял паузу · повторите чуть позже",
  timeout: "Отчет не дождался ответа · попробуйте обновить",
  connection: "Связь с данными прервалась · скоро восстановим",
  filters: "Фильтры не договорились · проверьте период и выборки",
  unknown: "Отчет немного задумался · попробуйте обновить",
};

const REPORT_PLACEHOLDER_STATES = {
  account: {
    asset: "assets/empty-states/report-connection.png",
    hint: "Чтобы открыть отчёт, выберите аккаунт в верхней панели.",
  },
  preparing: {
    asset: "assets/empty-states/report-preparing.png",
    hint: "Цифры уже в пути. Осталось собрать их в один отчет.",
  },
  empty: {
    asset: "assets/empty-states/report-empty.png",
    hint: "Цифры спрятались. Попробуйте выбрать другой период.",
  },
  filters: {
    asset: "assets/empty-states/report-empty.png",
    hint: "Похоже, выбран слишком строгий набор условий.",
  },
  throttled: {
    asset: "assets/empty-states/report-paused.png",
    hint: "Источник сделал паузу на кофе. Скоро продолжим.",
  },
  timeout: {
    asset: "assets/empty-states/report-paused.png",
    hint: "Данные задержались в пути. Обновите отчет чуть позже.",
  },
  access: {
    asset: "assets/empty-states/report-connection.png",
    hint: "Проверьте доступ к данным или обратитесь к администратору.",
  },
  connection: {
    asset: "assets/empty-states/report-connection.png",
    hint: "Кабель почти на месте. Обновите отчет чуть позже.",
  },
  unknown: {
    asset: "assets/empty-states/report-connection.png",
    hint: "Связь с цифрами потерялась. Попробуйте обновить отчет.",
  },
};

function friendlyErrorState(error) {
  const message = String(error?.message || error || "").trim();
  const normalized = message.toLowerCase();
  if (/relation\s+["']?public\.|отношение\s+["']?public\.|undefinedtable|materialized view|does not exist|не существует/.test(normalized)) return "preparing";
  if (/no data|empty result|данных нет|нет данных|пустой результат/.test(normalized)) return "empty";
  if (/\b401\b|\b403\b|unauthorized|forbidden|permission|доступ|авторизац/.test(normalized)) return "access";
  if (/\b429\b|too many requests|rate.?limit|лимит/.test(normalized)) return "throttled";
  if (/timeout|timed out|deadline|превышено время|тайм.?аут/.test(normalized)) return "timeout";
  if (/failed to fetch|networkerror|network error|connection|api returned .* instead of json|\b50[234]\b|соединени|связь/.test(normalized)) return "connection";
  if (/invalidargument|invalid argument|invalid date|filter|некорректн|период/.test(normalized)) return "filters";
  return "unknown";
}

function friendlyErrorMessage(error) {
  return FRIENDLY_ERROR_STATES[friendlyErrorState(error)];
}

function applyReportPlaceholder(kind, labelText = "") {
  const placeholder = qs("reportLoadingState");
  const illustration = qs("reportLoadingIllustration");
  const label = qs("reportLoadingLabel");
  const hint = qs("reportLoadingHint");
  const state = REPORT_PLACEHOLDER_STATES[kind] || REPORT_PLACEHOLDER_STATES.unknown;
  if (placeholder) placeholder.dataset.placeholderKind = kind;
  if (illustration) illustration.src = state.asset;
  if (label && labelText) label.textContent = labelText;
  if (hint) hint.textContent = state.hint;
}

function showAccountRequiredPlaceholder() {
  document.body.classList.add("report-shell-loading", "report-shell-failed");
  applyReportPlaceholder("account", "Выберите аккаунт");
  updateNavigation();
  applyLockedClientUi();
  qs("status").textContent = "Аккаунт не выбран";
  persistDashboardState();
}

function clearAccountRequiredPlaceholder() {
  if (qs("reportLoadingState")?.dataset.placeholderKind !== "account") return;
  document.body.classList.remove("report-shell-loading", "report-shell-failed");
}

function setStatusError(error, context = "Загрузка отчета") {
  console.error(`[KOKOC BI] ${context}`, error);
  const status = qs("status");
  if (!status) return;
  status.textContent = friendlyErrorMessage(error);
  status.removeAttribute("title");
}

function finishInitialReportLoading(error = null) {
  if (!document.body.classList.contains("report-shell-loading")) return;
  if (!error && state.dashboard !== "admin" && !currentClient()) return;
  if (error) {
    document.body.classList.add("report-shell-failed");
    const kind = friendlyErrorState(error);
    applyReportPlaceholder(kind, FRIENDLY_ERROR_STATES[kind]);
    return;
  }
  document.body.classList.remove("report-shell-loading", "report-shell-failed");
}

function setAvitoShellVisible(visible) {
  document.body.classList.toggle("avito-shell-active", visible);
  qs("avitoDashboard")?.classList.toggle("hidden", !visible);
  document.querySelector(".kpis")?.classList.toggle("hidden", visible);
  document.querySelector(".visuals")?.classList.toggle("hidden", visible);
  document.querySelector(".table-wrap")?.classList.toggle("hidden", visible);
  document.querySelector(".pagination")?.classList.toggle("hidden", visible);
  qs("advCampaignTableSection")?.classList.add("hidden");
}

function setYandexShellVisible(visible) {
  document.body.classList.toggle("yandex-shell-active", visible);
  qs("yandexDashboard")?.classList.toggle("hidden", !visible);
  document.querySelector(".kpis")?.classList.toggle("hidden", visible);
  document.querySelector(".visuals")?.classList.toggle("hidden", visible);
  document.querySelector(".table-wrap")?.classList.toggle("hidden", visible);
  document.querySelector(".pagination")?.classList.toggle("hidden", visible);
  qs("advCampaignTableSection")?.classList.add("hidden");
}

function avitoMetric(value, { money = false, percent = false } = {}) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return escapeHtml(value);
  const formatted = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: money || percent ? 2 : 0,
    maximumFractionDigits: money || percent ? 2 : 0,
  }).format(number);
  return `${formatted}${money ? " ₽" : (percent ? "%" : "")}`;
}

const avitoMetricCatalog = {
  spend_rub: { label: "Расход", short: "Расход, ₽", kind: "money" },
  bonus_spend_rub: { label: "Бонусный расход", short: "Бонусы, ₽", kind: "money" },
  views: { label: "Показы", short: "Показы", kind: "number" },
  clicks: { label: "Клики", short: "Клики", kind: "number" },
  ctr_pct: { label: "CTR", short: "CTR, %", kind: "percent" },
  cpc_rub: { label: "CPC", short: "CPC, ₽", kind: "money" },
  cpm_rub: { label: "CPM", short: "CPM, ₽", kind: "money" },
  video_views_25: { label: "Видео 25%", short: "Видео 25%", kind: "number" },
  video_views_50: { label: "Видео 50%", short: "Видео 50%", kind: "number" },
  video_views_75: { label: "Видео 75%", short: "Видео 75%", kind: "number" },
  video_views_100: { label: "Видео 100%", short: "Видео 100%", kind: "number" },
  vtr_pct: { label: "VTR", short: "VTR, %", kind: "percent" },
};
const avitoUiByDashboard = {};
let lastAvitoPayload = null;

function avitoMetricText(key, value) {
  const kind = avitoMetricCatalog[key]?.kind;
  return avitoMetric(value, { money: kind === "money", percent: kind === "percent" });
}

function avitoDefaultMetrics(section) {
  if (section === "overview") return ["spend_rub", "views", "clicks"];
  if (section === "campaigns") return ["spend_rub", "clicks", "ctr_pct"];
  if (section === "groups") return ["spend_rub", "clicks", "cpc_rub"];
  if (section === "creatives") return ["spend_rub", "views", "vtr_pct"];
  return ["spend_rub", "views", "ctr_pct"];
}

function avitoUi(payload) {
  const key = state.dashboard;
  if (!avitoUiByDashboard[key]) {
    avitoUiByDashboard[key] = {
      q: "", filters: {}, metrics: avitoDefaultMetrics(payload.section), axes: {},
      types: { spend_rub: "bar", bonus_spend_rub: "bar" },
      scatterX: "spend_rub", scatterY: "ctr_pct", scatterSize: "clicks", rankingMetric: "spend_rub",
    };
  }
  return avitoUiByDashboard[key];
}

function avitoChartMetricConfigs() {
  const colors = ["#ff4747", "#111827", "#06a976", "#2563eb", "#7c3aed", "#d97706", "#0891b2", "#db2777", "#65a30d", "#4f46e5", "#0f766e", "#be123c"];
  return Object.entries(avitoMetricCatalog).map(([key, metric], index) => ({
    key, label: metric.short || metric.label, color: colors[index % colors.length],
    type: metric.kind === "percent" ? "pct" : "number",
    digits: metric.kind === "number" ? 0 : 2,
    suffix: metric.kind === "percent" ? "%" : (metric.kind === "money" ? " ₽" : ""),
  }));
}

function avitoFilterSpecs(section) {
  if (["overview", "campaigns"].includes(section)) return [["status", "Статус"], ["campaign_type", "Тип"], ["payment_model", "Оплата"]];
  if (section === "groups") return [["campaign_name", "Кампания"], ["status", "Статус"]];
  if (section === "creatives") return [["campaign_name", "Кампания"], ["group_name", "Группа"], ["status", "Статус"]];
  return [["campaign_name", "Кампания"]];
}

function avitoFilteredRows(payload, ui) {
  const query = ui.q.trim().toLocaleLowerCase("ru-RU");
  return (payload.rows || []).filter((row) => {
    if (query && !Object.values(row).some((value) => String(value ?? "").toLocaleLowerCase("ru-RU").includes(query))) return false;
    return Object.entries(ui.filters).every(([key, value]) => !value || String(row[key] ?? "") === value);
  });
}

function avitoAggregate(rows) {
  const additive = ["views", "clicks", "spend_rub", "bonus_spend_rub", "video_views_25", "video_views_50", "video_views_75", "video_views_100"];
  const result = {};
  additive.forEach((key) => {
    const values = rows.map((row) => Number(row[key])).filter(Number.isFinite);
    result[key] = values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  });
  result.ctr_pct = result.views > 0 && result.clicks !== null ? result.clicks * 100 / result.views : null;
  result.cpc_rub = result.clicks > 0 && result.spend_rub !== null ? result.spend_rub / result.clicks : null;
  result.cpm_rub = result.views > 0 && result.spend_rub !== null ? result.spend_rub * 1000 / result.views : null;
  result.vtr_pct = result.views > 0 && result.video_views_100 !== null ? result.video_views_100 * 100 / result.views : null;
  return result;
}

function avitoTimeRows(payload, filteredRows) {
  let source = payload.section === "daily" ? filteredRows : (payload.trend_rows || []);
  if (["overview", "campaigns"].includes(payload.section)) {
    const campaignIds = new Set(filteredRows.map((row) => String(row.campaign_id)));
    source = source.filter((row) => campaignIds.has(String(row.campaign_id)));
  }
  const byDate = new Map();
  source.forEach((row) => {
    if (!row.report_date) return;
    if (!byDate.has(row.report_date)) byDate.set(row.report_date, []);
    byDate.get(row.report_date).push(row);
  });
  return [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([report_date, rows]) => ({ report_date, ...avitoAggregate(rows) }));
}

function avitoTimeChart(rows, metricKey, chartType) {
  const metric = avitoMetricCatalog[metricKey];
  const data = rows.filter((row) => row[metricKey] !== null && row[metricKey] !== undefined);
  if (!data.length) return `<article class="avito-chart-card"><header><span>${escapeHtml(metric.label)}</span><strong>—</strong></header><div class="avito-empty">Нет данных для графика</div></article>`;
  const width = 680, height = 220, left = 58, right = 16, top = 18, bottom = 34;
  const values = data.map((row) => Number(row[metricKey]));
  const max = Math.max(...values, 0) || 1;
  const x = (index) => left + (index * (width - left - right) / Math.max(1, data.length - 1));
  const y = (value) => top + (1 - value / max) * (height - top - bottom);
  const points = values.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  const barWidth = Math.max(3, Math.min(24, (width - left - right) / Math.max(1, data.length) - 2));
  const marks = chartType === "columns"
    ? values.map((value, index) => `<rect x="${(x(index) - barWidth / 2).toFixed(1)}" y="${y(value).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(0, height - bottom - y(value)).toFixed(1)}" class="avito-column"><title>${escapeHtml(formatRuDate(data[index].report_date))}: ${escapeHtml(avitoMetricText(metricKey, value))}</title></rect>`).join("")
    : `<polyline points="${points}" class="avito-line is-spend" />${values.map((value, index) => `<circle cx="${x(index).toFixed(1)}" cy="${y(value).toFixed(1)}" r="3" class="avito-point"><title>${escapeHtml(formatRuDate(data[index].report_date))}: ${escapeHtml(avitoMetricText(metricKey, value))}</title></circle>`).join("")}`;
  return `<article class="avito-chart-card"><header><span>${escapeHtml(metric.label)}</span><strong>${escapeHtml(avitoMetricText(metricKey, avitoAggregate(data)[metricKey]))}</strong></header>
    <svg class="avito-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metric.label)} по дням">
      ${[0, .5, 1].map((ratio) => { const yy = top + ratio * (height - top - bottom); return `<line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="avito-grid"/><text x="${left - 8}" y="${yy + 4}" text-anchor="end" class="avito-scale">${escapeHtml(avitoMetricText(metricKey, max * (1 - ratio)))}</text>`; }).join("")}
      ${marks}
      <text x="${left}" y="${height - 9}" class="avito-tick">${escapeHtml(formatShortDate(data[0].report_date))}</text>
      <text x="${width - right}" y="${height - 9}" text-anchor="end" class="avito-tick">${escapeHtml(formatShortDate(data[data.length - 1].report_date))}</text>
    </svg></article>`;
}

function avitoEntityName(row, section) {
  if (section === "campaigns" || section === "overview") return row.name || row.campaign_id;
  if (section === "groups") return row.name || row.group_id;
  return row.name || row.creative_id;
}

function avitoRankingChart(rows, metricKey, chartType, section) {
  const metric = avitoMetricCatalog[metricKey];
  const data = rows.filter((row) => Number.isFinite(Number(row[metricKey]))).sort((a, b) => Number(b[metricKey]) - Number(a[metricKey])).slice(0, 12);
  if (!data.length) return `<article class="avito-chart-card"><header><span>${escapeHtml(metric.label)}</span><strong>—</strong></header><div class="avito-empty">Нет данных для сравнения</div></article>`;
  const max = Math.max(...data.map((row) => Number(row[metricKey])), 0) || 1;
  if (chartType === "columns") {
    const width = 680, height = 250, left = 48, right = 12, top = 16, bottom = 62;
    const slot = (width - left - right) / data.length, bar = Math.max(8, Math.min(36, slot - 8));
    return `<article class="avito-chart-card"><header><span>${escapeHtml(metric.label)} · топ ${data.length}</span><strong>${escapeHtml(avitoMetricText(metricKey, data[0][metricKey]))}</strong></header><svg class="avito-ranking is-columns" viewBox="0 0 ${width} ${height}" role="img" aria-label="Рейтинг по ${escapeHtml(metric.label)}">${[0,.5,1].map((r)=>`<line x1="${left}" y1="${top+r*(height-top-bottom)}" x2="${width-right}" y2="${top+r*(height-top-bottom)}" class="avito-grid"/>`).join("")}${data.map((row,index)=>{const value=Number(row[metricKey]);const h=value/max*(height-top-bottom);const xx=left+index*slot+(slot-bar)/2;const name=String(avitoEntityName(row,section));return `<rect x="${xx}" y="${height-bottom-h}" width="${bar}" height="${h}" class="avito-column"><title>${escapeHtml(name)}: ${escapeHtml(avitoMetricText(metricKey,value))}</title></rect><text x="${xx+bar/2}" y="${height-bottom+12}" text-anchor="end" transform="rotate(-38 ${xx+bar/2} ${height-bottom+12})" class="avito-rank-label">${escapeHtml(name.slice(0,18))}</text>`;}).join("")}</svg></article>`;
  }
  const rowHeight = 28, width = 680, left = 210, right = 92, height = 18 + data.length * rowHeight;
  return `<article class="avito-chart-card"><header><span>${escapeHtml(metric.label)} · топ ${data.length}</span><strong>${escapeHtml(avitoMetricText(metricKey, data[0][metricKey]))}</strong></header><svg class="avito-ranking" viewBox="0 0 ${width} ${height}" role="img" aria-label="Рейтинг по ${escapeHtml(metric.label)}">${data.map((row,index)=>{const value=Number(row[metricKey]);const yy=12+index*rowHeight;const name=String(avitoEntityName(row,section));const barWidth=(width-left-right)*value/max;return `<text x="${left-10}" y="${yy+13}" text-anchor="end" class="avito-rank-label">${escapeHtml(name.slice(0,28))}</text><rect x="${left}" y="${yy}" width="${Math.max(1,barWidth)}" height="16" class="avito-rank-bar"><title>${escapeHtml(name)}: ${escapeHtml(avitoMetricText(metricKey,value))}</title></rect><text x="${width-right+8}" y="${yy+13}" class="avito-rank-value">${escapeHtml(avitoMetricText(metricKey,value))}</text>`;}).join("")}</svg></article>`;
}

function avitoSeriesLegend(rows, metrics) {
  const aggregate = avitoAggregate(rows);
  return `<div class="avito-series-legend">${metrics.map((key, index) => `<span><i class="is-${index}"></i><b>${escapeHtml(avitoMetricCatalog[key].label)}</b><em>${escapeHtml(avitoMetricText(key, aggregate[key]))}</em></span>`).join("")}</div>`;
}

function avitoCombinedTimeChart(rows, metrics, chartType) {
  const available = metrics.filter((key) => rows.some((row) => row[key] !== null && row[key] !== undefined));
  if (!rows.length || !available.length) return '<section class="avito-chart-card avito-chart-combined"><div class="avito-empty">Нет данных для диаграммы</div></section>';
  const width = 1220, height = 292, left = 64, right = 22, top = 22, bottom = 38;
  const sameKind = new Set(available.map((key) => avitoMetricCatalog[key].kind)).size === 1;
  const seriesMax = Object.fromEntries(available.map((key) => [key, Math.max(...rows.map((row) => Number(row[key]) || 0), 0) || 1]));
  const commonMax = Math.max(...Object.values(seriesMax));
  const x = (index) => left + (index * (width - left - right) / Math.max(1, rows.length - 1));
  const scaled = (key, value) => Number(value || 0) / (sameKind ? commonMax : seriesMax[key]);
  const y = (key, value) => top + (1 - scaled(key, value)) * (height - top - bottom);
  const colors = ["#ff4747", "#111827", "#06a976"];
  const slot = (width - left - right) / Math.max(1, rows.length);
  const columnWidth = Math.max(2, Math.min(14, (slot - 3) / available.length));
  const marks = available.map((key, seriesIndex) => {
    if (chartType === "columns") return rows.map((row, index) => {
      const xx = x(index) - (available.length * columnWidth) / 2 + seriesIndex * columnWidth;
      const yy = y(key, row[key]);
      return `<rect x="${xx.toFixed(1)}" y="${yy.toFixed(1)}" width="${Math.max(1,columnWidth-1).toFixed(1)}" height="${Math.max(0,height-bottom-yy).toFixed(1)}" fill="${colors[seriesIndex]}"><title>${escapeHtml(formatRuDate(row.report_date))} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${escapeHtml(avitoMetricText(key,row[key]))}</title></rect>`;
    }).join("");
    const points = rows.map((row, index) => `${x(index).toFixed(1)},${y(key,row[key]).toFixed(1)}`).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${colors[seriesIndex]}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${rows.map((row,index)=>`<circle cx="${x(index).toFixed(1)}" cy="${y(key,row[key]).toFixed(1)}" r="2.7" fill="${colors[seriesIndex]}" stroke="#fff" stroke-width="1"><title>${escapeHtml(formatRuDate(row.report_date))} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${escapeHtml(avitoMetricText(key,row[key]))}</title></circle>`).join("")}`;
  }).join("");
  const axisLabels = [0, .5, 1].map((ratio) => {
    const yy = top + ratio * (height - top - bottom);
    const label = sameKind ? avitoMetricText(available[0], commonMax * (1 - ratio)) : `${Math.round((1-ratio)*100)}%`;
    return `<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" class="avito-grid"/><text x="${left-9}" y="${yy+4}" text-anchor="end" class="avito-scale">${escapeHtml(label)}</text>`;
  }).join("");
  return `<section class="avito-chart-card avito-chart-combined"><header><div><span>Динамика выбранных метрик</span><small>${sameKind ? "Общая абсолютная шкала" : "Индекс 0–100% от максимума каждой метрики"}</small></div>${avitoSeriesLegend(rows,available)}</header><svg class="avito-combined-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика выбранных метрик Avito Ads">${axisLabels}${marks}<text x="${left}" y="${height-10}" class="avito-tick">${escapeHtml(formatShortDate(rows[0].report_date))}</text><text x="${(left+width-right)/2}" y="${height-10}" text-anchor="middle" class="avito-tick">${escapeHtml(formatShortDate(rows[Math.floor(rows.length/2)].report_date))}</text><text x="${width-right}" y="${height-10}" text-anchor="end" class="avito-tick">${escapeHtml(formatShortDate(rows[rows.length-1].report_date))}</text></svg></section>`;
}

function avitoCombinedRankingChart(rows, metrics, chartType, section) {
  const available = metrics.filter((key) => rows.some((row) => Number.isFinite(Number(row[key]))));
  if (!rows.length || !available.length) return '<section class="avito-chart-card avito-chart-combined"><div class="avito-empty">Нет данных для диаграммы</div></section>';
  const primary = available[0];
  const data = [...rows].filter((row)=>Number.isFinite(Number(row[primary]))).sort((a,b)=>Number(b[primary])-Number(a[primary])).slice(0,12);
  const sameKind = new Set(available.map((key)=>avitoMetricCatalog[key].kind)).size===1;
  const maxByMetric = Object.fromEntries(available.map((key)=>[key,Math.max(...data.map((row)=>Number(row[key])||0),0)||1]));
  const commonMax = Math.max(...Object.values(maxByMetric));
  const colors=["#ff4747","#111827","#06a976"];
  if (chartType === "columns") {
    const width=1220,height=330,left=58,right=18,top=18,bottom=76,slot=(width-left-right)/Math.max(1,data.length),barWidth=Math.max(3,Math.min(20,(slot-8)/available.length));
    const columns=data.map((row,rowIndex)=>{const label=String(avitoEntityName(row,section));return `${available.map((key,index)=>{const value=Number(row[key])||0;const scale=value/(sameKind?commonMax:maxByMetric[key]);const h=scale*(height-top-bottom);const xx=left+rowIndex*slot+(slot-available.length*barWidth)/2+index*barWidth;return `<rect x="${xx}" y="${height-bottom-h}" width="${Math.max(2,barWidth-1)}" height="${h}" fill="${colors[index]}"><title>${escapeHtml(label)} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${escapeHtml(avitoMetricText(key,value))}</title></rect>`;}).join("")}<text x="${left+rowIndex*slot+slot/2}" y="${height-bottom+13}" text-anchor="end" transform="rotate(-38 ${left+rowIndex*slot+slot/2} ${height-bottom+13})" class="avito-rank-label">${escapeHtml(label.slice(0,21))}</text>`;}).join("");
    return `<section class="avito-chart-card avito-chart-combined"><header><div><span>Сравнение ${data.length} лидеров</span><small>${sameKind ? "Общая абсолютная шкала" : "Каждая метрика нормирована по своему максимуму"}</small></div>${avitoSeriesLegend(data,available)}</header><svg class="avito-combined-ranking is-columns" viewBox="0 0 ${width} ${height}" role="img" aria-label="Сравнение выбранных метрик Avito Ads">${[0,.5,1].map((ratio)=>`<line x1="${left}" y1="${top+ratio*(height-top-bottom)}" x2="${width-right}" y2="${top+ratio*(height-top-bottom)}" class="avito-grid"/>`).join("")}${columns}</svg></section>`;
  }
  const width=1220,left=250,right=100,seriesHeight=10,rowGap=9,rowHeight=available.length*seriesHeight+rowGap,height=28+data.length*rowHeight;
  const bars=data.map((row,rowIndex)=>{const yy=15+rowIndex*rowHeight;const label=String(avitoEntityName(row,section));return `<text x="${left-12}" y="${yy+(available.length*seriesHeight)/2+4}" text-anchor="end" class="avito-rank-label">${escapeHtml(label.slice(0,34))}</text>${available.map((key,index)=>{const value=Number(row[key])||0;const scale=value/(sameKind?commonMax:maxByMetric[key]);const barWidth=(width-left-right)*scale;return `<rect x="${left}" y="${yy+index*seriesHeight}" width="${Math.max(1,barWidth)}" height="7" fill="${colors[index]}"><title>${escapeHtml(label)} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${escapeHtml(avitoMetricText(key,value))}</title></rect>`;}).join("")}`;}).join("");
  return `<section class="avito-chart-card avito-chart-combined"><header><div><span>Сравнение ${data.length} лидеров</span><small>${sameKind ? "Общая абсолютная шкала" : "Каждая метрика нормирована по своему максимуму"}</small></div>${avitoSeriesLegend(data,available)}</header><svg class="avito-combined-ranking" viewBox="0 0 ${width} ${height}" role="img" aria-label="Сравнение выбранных метрик Avito Ads">${bars}</svg></section>`;
}

function avitoFunnelStyleChart(rows, ui, section) {
  if (!rows.length) return '<div class="empty-chart">Нет данных для графика</div>';
  const availableMetrics = avitoChartMetricConfigs();
  const metrics = availableMetrics.filter((metric) => ui.metrics.includes(metric.key));
  const width = 1500, height = 390;
  const timeMode = ["overview", "daily"].includes(section);
  const metricAxis = (metric) => ui.axes[metric.key] || (avitoMetricCatalog[metric.key].kind === "number" ? "left" : "right");
  const metricChartType = (metric) => ui.types[metric.key] === "bar" ? "bar" : "line";
  const rightMetrics = metrics.filter((metric) => metricAxis(metric) === "right");
  const leftMetrics = metrics.filter((metric) => metricAxis(metric) !== "right");
  const data = rows.map((row, index) => ({ ...row, index, chart_label: timeMode ? row.report_date : avitoEntityName(row, section) }));
  const rightAxisGroups = groupMetricsByAxisUnit(rightMetrics);
  const rightAxisLayout = compactRightAxisLayout(rightAxisGroups.length, { baseRight: 10, emptyRight: 16, tail: 4 });
  const pad = { left: 82, right: rightAxisLayout.padRight, top: 26, bottom: 56 };
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const leftValues = data.flatMap((row) => leftMetrics.map((metric) => metricNumber(row, metric.key))).filter((value) => value !== null);
  const leftMax = Math.max(...leftValues, 1);
  const rightMaxByGroup = rightAxisMaxByGroup(data, rightAxisGroups, (row, metric) => metricNumber(row, metric.key));
  const x = (index) => pad.left + (data.length === 1 ? plotW / 2 : index / (data.length - 1) * plotW);
  const yLeft = (value) => pad.top + (1 - value / leftMax) * plotH;
  const yRight = (metric, value) => { const group = rightAxisGroupByMetric(rightAxisGroups, metric); return pad.top + (1 - value / (rightMaxByGroup.get(group?.key) || 1)) * plotH; };
  const yFor = (metric, value) => metricAxis(metric) === "right" ? yRight(metric, value) : yLeft(value);
  const ticks = [0, .25, .5, .75, 1];
  const rightTicks = rightAxisGroups.length > 2 ? [0, .5, 1] : ticks;
  const xTicks = compactChartDateTicks(data, 11);
  const barMetrics = metrics.filter((metric) => metricChartType(metric) === "bar");
  const lineMetrics = metrics.filter((metric) => metricChartType(metric) !== "bar");
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW;
  const groupW = Math.min(46, Math.max(10, step * .7));
  const barW = Math.max(3, groupW / Math.max(barMetrics.length, 1) - 3);
  const tickLabel = (row) => timeMode ? formatWeeklyDynamicsPeriodLabel(row.chart_label) : compactMetricLabel(row.chart_label, 20);
  return `<div class="chart-controls funnel-chart-controls avito-chart-metric-controls">${availableMetrics.map((metric)=>`<label class="metric-toggle" style="--metric-color:${metric.color}"><input type="checkbox" data-avito-metric value="${metric.key}" ${ui.metrics.includes(metric.key)?"checked":""}><span>${escapeHtml(metric.label)}</span></label>`).join("")}</div>
    <svg class="combo-svg funnel-combo-svg avito-funnel-combo-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Avito Ads по выбранным метрикам">
      <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" rx="6" class="chart-plot-bg"/>
      ${ticks.map((tick)=>`<line x1="${pad.left}" y1="${yLeft(leftMax*tick)}" x2="${width-pad.right}" y2="${yLeft(leftMax*tick)}" class="chart-grid-line"/><text x="${pad.left-10}" y="${yLeft(leftMax*tick)+4}" class="axis-label" text-anchor="end">${escapeHtml(formatAxisValue(leftMax*tick))}</text>`).join("")}
      ${rightAxisGroups.length ? rightTicks.map((tick)=>rightAxisGroups.map((group,axisIndex)=>{const axisX=width-pad.right+rightAxisLayout.valueOffset+axisIndex*rightAxisLayout.spacing;const maxValue=rightMaxByGroup.get(group.key)||1;const color=group.metrics[0]?.color||"#111827";return `<text x="${axisX}" y="${pad.top+(1-tick)*plotH+4}" class="axis-label funnel-axis-value" style="fill:${color};font-size:${rightAxisLayout.valueFontSize}px"><title>${escapeHtml(group.metrics.map((metric)=>metric.label).join(" / "))}</title>${escapeHtml(rightAxisGroupValue(group,maxValue*tick,rightAxisGroups.length,maxValue))}</text>`;}).join("")).join("") : ""}
      <line x1="${pad.left}" y1="${height-pad.bottom}" x2="${width-pad.right}" y2="${height-pad.bottom}" class="axis"/><line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height-pad.bottom}" class="axis"/>
      ${rightAxisGroups.map((group,axisIndex)=>{const axisX=width-pad.right+axisIndex*rightAxisLayout.spacing;const color=group.metrics[0]?.color||"#111827";return `<line x1="${axisX}" y1="${pad.top}" x2="${axisX}" y2="${height-pad.bottom}" class="axis" style="stroke:${color}"/><text x="${axisX+rightAxisLayout.titleOffset}" y="18" class="axis-label axis-title funnel-axis-title" style="fill:${color}">${escapeHtml(rightAxisGroupTitle(group))}</text>`;}).join("")}
      ${xTicks.map((row,index)=>`<line x1="${x(row.index)}" y1="${pad.top}" x2="${x(row.index)}" y2="${height-pad.bottom}" class="chart-grid-line vertical"/>${renderChartDateTick(tickLabel(row),x(row.index),height-27,index===0?"start":(index===xTicks.length-1?"end":"middle"))}`).join("")}
      ${barMetrics.map((metric,metricIndex)=>data.map((row)=>{const value=metricNumber(row,metric.key);if(value===null)return "";const barH=Math.max(0,height-pad.bottom-yFor(metric,value));const barX=x(row.index)-groupW/2+metricIndex*(barW+3);return `<rect x="${barX}" y="${yFor(metric,value)}" width="${barW}" height="${barH}" rx="2" class="funnel-metric-bar" style="fill:${metric.color}"><title>${escapeHtml(String(row.chart_label))} | ${escapeHtml(metric.label)}: ${formatNumber(value,metric.digits||0)}${metric.suffix||""}</title></rect>`;}).join("")).join("")}
      ${lineMetrics.map((metric)=>linePointSegments(data,metric,x,yFor).map((points)=>`<polyline points="${points.join(" ")}" class="funnel-metric-line" style="stroke:${metric.color}"/>`).join("")).join("")}
      ${lineMetrics.map((metric)=>data.map((row)=>{const value=metricNumber(row,metric.key);if(value===null)return "";return `<circle cx="${x(row.index)}" cy="${yFor(metric,value)}" r="4" class="funnel-metric-dot" style="fill:${metric.color}"><title>${escapeHtml(String(row.chart_label))} | ${escapeHtml(metric.label)}: ${formatNumber(value,metric.digits||0)}${metric.suffix||""}</title></circle>`;}).join("")).join("")}
      <text x="${pad.left}" y="18" class="axis-label axis-title">${escapeHtml(chartAxisCaption(leftMetrics))}</text>
    </svg>
    <div class="legend funnel-legend avito-funnel-legend">${metrics.map((metric)=>`<span class="legend-metric" data-avito-legend="${metric.key}" title="Клик: линия / столбцы"><i class="${metricChartType(metric)==="bar"?"legend-bar-key":""}" style="background:${metric.color}"></i><span class="legend-label">${escapeHtml(metric.label)}</span><select class="axis-select" data-avito-axis-select="${metric.key}" aria-label="Ось для ${escapeHtml(metric.label)}"><option value="left" ${metricAxis(metric)==="left"?"selected":""}>осн.</option><option value="right" ${metricAxis(metric)==="right"?"selected":""}>доп.</option></select></span>`).join("")}</div>`;
}

function avitoMetricOptions(selected) {
  return Object.entries(avitoMetricCatalog).map(([key, metric]) => `<option value="${key}" ${selected===key?"selected":""}>${escapeHtml(metric.label)}</option>`).join("");
}

function avitoHasMetricValue(row, key) {
  const raw=row?.[key];
  return raw!==null && raw!==undefined && raw!=="" && Number.isFinite(Number(raw));
}

function avitoCampaignBubbleChart(rows, ui) {
  const xKey=ui.scatterX, yKey=ui.scatterY, sizeKey=ui.scatterSize;
  const data=rows.filter((row)=>[xKey,yKey,sizeKey].every((key)=>avitoHasMetricValue(row,key)));
  const controls=`<div class="avito-special-chart-controls"><label><span>Ось X</span><select data-avito-scatter="x">${avitoMetricOptions(xKey)}</select></label><label><span>Ось Y</span><select data-avito-scatter="y">${avitoMetricOptions(yKey)}</select></label><label><span>Размер точки</span><select data-avito-scatter="size">${avitoMetricOptions(sizeKey)}</select></label></div>`;
  if(!data.length)return `${controls}<div class="empty-chart">Нет данных для карты эффективности</div>`;
  const width=1500,height=430,pad={left:94,right:36,top:34,bottom:62};
  const plotW=width-pad.left-pad.right,plotH=height-pad.top-pad.bottom;
  const xMax=Math.max(...data.map((row)=>Number(row[xKey])),1),yMax=Math.max(...data.map((row)=>Number(row[yKey])),1),sizeMax=Math.max(...data.map((row)=>Number(row[sizeKey])),1);
  const x=(value)=>pad.left+Number(value)/xMax*plotW,y=(value)=>pad.top+(1-Number(value)/yMax)*plotH;
  const tone=(row)=>String(row.status||"").toLowerCase()==="active"?"#06a976":(String(row.status||"").toLowerCase()==="finished"?"#94a3b8":"#ff4747");
  return `${controls}<svg class="avito-special-svg avito-bubble-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Карта эффективности кампаний">${[0,.25,.5,.75,1].map((tick)=>`<line x1="${pad.left}" y1="${pad.top+tick*plotH}" x2="${width-pad.right}" y2="${pad.top+tick*plotH}" class="chart-grid-line"/><text x="${pad.left-10}" y="${pad.top+tick*plotH+4}" text-anchor="end" class="axis-label">${escapeHtml(avitoMetricText(yKey,yMax*(1-tick)))}</text><line x1="${pad.left+tick*plotW}" y1="${pad.top}" x2="${pad.left+tick*plotW}" y2="${height-pad.bottom}" class="chart-grid-line vertical"/><text x="${pad.left+tick*plotW}" y="${height-pad.bottom+22}" text-anchor="middle" class="axis-label">${escapeHtml(avitoMetricText(xKey,xMax*tick))}</text>`).join("")}${data.map((row)=>{const radius=7+Math.sqrt(Math.max(0,Number(row[sizeKey]))/sizeMax)*25;const name=String(row.name||row.campaign_id);return `<circle cx="${x(row[xKey])}" cy="${y(row[yKey])}" r="${radius}" fill="${tone(row)}" fill-opacity=".72" stroke="#fff" stroke-width="2"><title>${escapeHtml(name)} | ${escapeHtml(avitoMetricCatalog[xKey].label)}: ${escapeHtml(avitoMetricText(xKey,row[xKey]))} | ${escapeHtml(avitoMetricCatalog[yKey].label)}: ${escapeHtml(avitoMetricText(yKey,row[yKey]))} | ${escapeHtml(avitoMetricCatalog[sizeKey].label)}: ${escapeHtml(avitoMetricText(sizeKey,row[sizeKey]))}</title></circle><text x="${x(row[xKey])+radius+5}" y="${y(row[yKey])+4}" class="avito-bubble-label">${escapeHtml(name.slice(0,24))}</text>`;}).join("")}<text x="${pad.left}" y="18" class="axis-label axis-title">Y: ${escapeHtml(avitoMetricCatalog[yKey].label)}</text><text x="${width-pad.right}" y="${height-12}" text-anchor="end" class="axis-label axis-title">X: ${escapeHtml(avitoMetricCatalog[xKey].label)}</text></svg><div class="avito-status-legend"><span><i class="is-active"></i>active</span><span><i class="is-finished"></i>finished</span><span><i></i>прочие</span></div>`;
}

function avitoGroupRankingChart(rows, ui) {
  const key=ui.rankingMetric;
  const data=rows.filter((row)=>avitoHasMetricValue(row,key)).sort((a,b)=>Number(b[key])-Number(a[key])).slice(0,15);
  const controls=`<div class="avito-special-chart-controls"><label><span>Ранжировать по</span><select data-avito-ranking-metric>${avitoMetricOptions(key)}</select></label></div>`;
  if(!data.length)return `${controls}<div class="empty-chart">Нет данных для ранжирования</div>`;
  const width=1500,left=330,right=150,rowH=27,height=28+data.length*rowH,max=Math.max(...data.map((row)=>Number(row[key])),1);
  return `${controls}<svg class="avito-special-svg avito-group-ranking" viewBox="0 0 ${width} ${height}" role="img" aria-label="Рейтинг групп">${data.map((row,index)=>{const yy=14+index*rowH,value=Number(row[key]),bar=(width-left-right)*value/max,name=String(row.name||row.group_id);return `<text x="${left-12}" y="${yy+14}" text-anchor="end" class="avito-rank-label">${escapeHtml(name.slice(0,38))}</text><rect x="${left}" y="${yy}" width="${Math.max(1,bar)}" height="17" class="avito-group-bar"><title>${escapeHtml(name)} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${escapeHtml(avitoMetricText(key,value))}</title></rect><text x="${width-right+10}" y="${yy+14}" class="avito-rank-value">${escapeHtml(avitoMetricText(key,value))}</text>`;}).join("")}</svg>`;
}

function avitoCreativeHeatmap(rows, ui) {
  const metrics=ui.metrics.slice(0,3);
  const primary=metrics[0];
  const data=[...rows].filter((row)=>avitoHasMetricValue(row,primary)).sort((a,b)=>Number(b[primary])-Number(a[primary])).slice(0,15);
  const controls=`<div class="chart-controls funnel-chart-controls avito-chart-metric-controls">${avitoChartMetricConfigs().map((metric)=>`<label class="metric-toggle" style="--metric-color:${metric.color}"><input type="checkbox" data-avito-metric value="${metric.key}" ${metrics.includes(metric.key)?"checked":""}><span>${escapeHtml(metric.label)}</span></label>`).join("")}</div>`;
  if(!data.length)return `${controls}<div class="empty-chart">Нет данных для матрицы креативов</div>`;
  const width=1500,left=350,right=24,top=58,rowH=30,colW=(width-left-right)/metrics.length,height=top+data.length*rowH+12;
  const maxes=Object.fromEntries(metrics.map((key)=>[key,Math.max(...data.map((row)=>Number(row[key])||0),1)]));
  return `${controls}<svg class="avito-special-svg avito-creative-heatmap" viewBox="0 0 ${width} ${height}" role="img" aria-label="Тепловая матрица креативов">${metrics.map((key,index)=>`<text x="${left+index*colW+colW/2}" y="28" text-anchor="middle" class="avito-heatmap-head">${escapeHtml(avitoMetricCatalog[key].label)}</text>`).join("")}${data.map((row,rowIndex)=>{const yy=top+rowIndex*rowH,name=String(row.name||row.creative_id);return `<text x="${left-12}" y="${yy+20}" text-anchor="end" class="avito-rank-label">${escapeHtml(name.slice(0,42))}</text>${metrics.map((key,colIndex)=>{const raw=row[key],value=Number(raw),available=raw!==null&&raw!==undefined&&Number.isFinite(value),opacity=available ? .12+.78*Math.max(0,value)/maxes[key] : 0;return `<rect x="${left+colIndex*colW+2}" y="${yy+2}" width="${colW-4}" height="${rowH-4}" fill="#ff4747" fill-opacity="${available?opacity:0}" stroke="#e7ecea"><title>${escapeHtml(name)} · ${escapeHtml(avitoMetricCatalog[key].label)}: ${available?escapeHtml(avitoMetricText(key,value)):"—"}</title></rect><text x="${left+colIndex*colW+colW/2}" y="${yy+20}" text-anchor="middle" class="avito-heatmap-value ${opacity>.55?"is-inverse":""}">${available?escapeHtml(avitoMetricText(key,value)):"—"}</text>`;}).join("")}`;}).join("")}</svg>`;
}

function avitoChartWorkbench(payload, rows, ui) {
  if(payload.section==="campaigns")return {title:"Карта эффективности кампаний",subtitle:"Оси и размер точки настраиваются независимо",body:avitoCampaignBubbleChart(rows,ui)};
  if(payload.section==="groups")return {title:"Рейтинг групп",subtitle:"Сравнение групп по выбранному показателю",body:avitoGroupRankingChart(rows,ui)};
  if(payload.section==="creatives")return {title:"Матрица креативов",subtitle:"Интенсивность цвета показывает относительное значение",body:avitoCreativeHeatmap(rows,ui)};
  return {title:"Динамика и сравнение метрик",subtitle:"Выберите показатели, ось и тип каждой серии",body:avitoFunnelStyleChart(rows,ui,payload.section)};
}

function avitoCellValue(key, value) {
  if (value === null || value === undefined || value === "") return '<span class="avito-missing">—</span>';
  if (key === "report_date") return escapeHtml(formatRuDate(value));
  if (key === "status") return `<span class="avito-status is-${escapeHtml(String(value).toLowerCase())}">${escapeHtml(value)}</span>`;
  if (key.endsWith("_rub")) return avitoMetric(value, { money: true });
  if (key.endsWith("_pct") || ["q25", "q50", "q75"].includes(key)) return avitoMetric(value, { percent: true });
  if (["views", "clicks", "video_views_25", "video_views_50", "video_views_75", "video_views_100"].includes(key)) return avitoMetric(value);
  return escapeHtml(value);
}

function renderAvitoDashboard(payload) {
  const root = qs("avitoDashboard");
  if (!root) return;
  lastAvitoPayload = payload;
  const ui = avitoUi(payload);
  const coverage = payload.coverage || {};
  const summary = payload.summary || {};
  const missing = (coverage.missing_ranges || []).slice(0, 4)
    .map((range) => `${formatRuDate(range.date_from)}–${formatRuDate(range.date_to)}`).join(", ");
  const coverageLabel = coverage.status === "complete" ? "Полное покрытие" : (coverage.status === "partial" ? "Частичное покрытие" : (coverage.status === "not_started" ? "Ещё не настроено" : "Данных за период нет"));
  const sectionLabel = payload.section === "overview" ? "Кампании за период" : (avitoDashboardTitles[state.dashboard] || "Avito Ads");
  const kpis = payload.section === "overview" ? [
    ["Баланс", avitoMetric(summary.balance_rub, { money: true })],
    ["Бонусный баланс", avitoMetric(summary.bonus_balance_rub, { money: true })],
    ["Кампании", avitoMetric(summary.campaigns)],
    ["Расход", avitoMetric(summary.spend_rub, { money: true })],
    ["Показы", avitoMetric(summary.views)],
    ["Клики", avitoMetric(summary.clicks)],
  ] : [
    ["Расход", avitoMetric(summary.spend_rub, { money: true })],
    ["Показы", avitoMetric(summary.views)],
    ["Клики", avitoMetric(summary.clicks)],
    ["CTR", avitoMetric(summary.ctr_pct, { percent: true })],
    ["CPC", avitoMetric(summary.cpc_rub, { money: true })],
    ["VTR", avitoMetric(summary.vtr_pct, { percent: true })],
  ];
  const columns = payload.columns || [];
  const rows = avitoFilteredRows(payload, ui);
  const isOverview = payload.section === "overview";
  const isDaily = payload.section === "daily";
  const entityLabels = { campaign: "кампания", group: "группа", creative: "креатив" };
  const grainLabel = entityLabels[payload.grain] || payload.grain || "кампания";
  const dataWindow = `${formatRuDate(coverage.data_from || "")} — ${formatRuDate(coverage.data_to || "")}`;
  const tableMarkup = `<section class="avito-panel avito-table-panel"><header><div><h3>${escapeHtml(sectionLabel)}</h3><span>${rows.length.toLocaleString("ru-RU")} ${rows.length === 1 ? "строка" : "строк"} · уровень: ${escapeHtml(grainLabel)}</span></div></header>
      <div class="avito-table-scroll"><table><thead><tr>${columns.map((column) => `<th data-key="${escapeHtml(column.key)}">${escapeHtml(column.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows.length ? rows.map((row) => `<tr>${columns.map((column) => `<td data-key="${escapeHtml(column.key)}">${avitoCellValue(column.key, row[column.key])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${Math.max(1, columns.length)}" class="avito-empty-cell">За выбранный период строки этого уровня не получены.</td></tr>`}</tbody></table></div>
    </section>`;
  const chartRows = (isOverview || isDaily) ? avitoTimeRows(payload, rows) : rows;
  const chartWorkbench = avitoChartWorkbench(payload, chartRows, ui);
  const chartsMarkup = `<section class="avito-panel avito-chart-workbench" aria-label="Настраиваемая диаграмма"><header><div><h3>${escapeHtml(chartWorkbench.title)}</h3><span>${escapeHtml(chartWorkbench.subtitle)}</span></div></header><div class="avito-chart-workbench-body">${chartWorkbench.body}</div></section>`;
  const aggregate = avitoAggregate(rows);
  if (isOverview) {
    kpis.splice(0, kpis.length,
      ["Баланс", avitoMetric(summary.balance_rub, { money: true })],
      ["Кампании", avitoMetric(rows.length)],
      ["Расход", avitoMetricText("spend_rub", aggregate.spend_rub)],
      ["Показы", avitoMetricText("views", aggregate.views)],
      ["Клики", avitoMetricText("clicks", aggregate.clicks)],
      ["CTR", avitoMetricText("ctr_pct", aggregate.ctr_pct)]);
  } else {
    kpis.splice(0, kpis.length,
      ["Строки", avitoMetric(rows.length)],
      ["Расход", avitoMetricText("spend_rub", aggregate.spend_rub)],
      ["Показы", avitoMetricText("views", aggregate.views)],
      ["Клики", avitoMetricText("clicks", aggregate.clicks)],
      ["CTR", avitoMetricText("ctr_pct", aggregate.ctr_pct)],
      ["CPC", avitoMetricText("cpc_rub", aggregate.cpc_rub)]);
  }
  const filterControls = avitoFilterSpecs(payload.section).map(([key, label]) => {
    const options = [...new Set((payload.rows || []).map((row) => String(row[key] ?? "")).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"ru"));
    return `<label><span>${escapeHtml(label)}</span><select data-avito-filter="${escapeHtml(key)}"><option value="">Все</option>${options.map((value)=>`<option value="${escapeHtml(value)}" ${ui.filters[key]===value?"selected":""}>${escapeHtml(value)}</option>`).join("")}</select></label>`;
  }).join("");
  const controlsMarkup = `<section class="avito-controls" aria-label="Фильтры и настройка отчёта"><label class="is-search"><span>Поиск</span><input type="search" value="${escapeHtml(ui.q)}" placeholder="Название или ID" data-avito-search></label>${filterControls}<button type="button" class="avito-reset" data-avito-reset>Сбросить</button><button type="button" class="avito-export" data-avito-export aria-label="Скачать Excel" title="Скачать Excel">Excel</button></section>`;
  root.innerHTML = `
    <header class="avito-report-head">
      <div><span class="avito-eyebrow">AVITO ADS</span><h2>${escapeHtml((avitoDashboardTitles[state.dashboard] || "Avito Ads").replace("Avito Ads · ", ""))}</h2>
      <p>Рекламный кабинет · только чтение · ${escapeHtml(grainLabel)}</p></div>
      <div class="avito-coverage is-${escapeHtml(coverage.status || "empty")}"><strong>${escapeHtml(coverageLabel)}</strong><span>проверено ${coverage.covered_days ?? 0}/${coverage.requested_days ?? "—"} дней · строки есть за ${coverage.days_with_data ?? 0} дней · ${coverage.coverage_pct ?? "—"}%</span></div>
    </header>
    ${payload.available === false ? `<div class="avito-notice">${escapeHtml(coverage.note || "Инфраструктура Avito Ads ещё не готова.")}</div>` : ""}
    ${controlsMarkup}
    <div class="avito-kpis">${kpis.map(([label, value], index) => `<article class="${index === 0 ? "is-primary" : ""}"><span>${label}</span><strong>${value}</strong></article>`).join("")}</div>
    <div class="avito-source-line"><span><b>Период данных</b> ${escapeHtml(dataWindow)}</span><span><b>Синхронизация</b> ${escapeHtml(coverage.synced_at ? new Date(coverage.synced_at).toLocaleString("ru-RU") : "—")}</span></div>
    ${missing ? `<div class="avito-notice is-warning"><strong>Нет строк за дни:</strong> ${escapeHtml(missing)}${(coverage.missing_ranges || []).length > 4 ? "…" : ""}. Отсутствие не заменено нулём.</div>` : ""}
    ${(isOverview || isDaily) ? `${chartsMarkup}${tableMarkup}` : `${tableMarkup}${chartsMarkup}`}
    <p class="avito-footnote"><strong>Источник:</strong> Avito Ads API. ${escapeHtml(coverage.note || "")}</p>`;
  root.querySelector("[data-avito-search]")?.addEventListener("input", (event) => { ui.q = event.target.value; renderAvitoDashboard(payload); root.querySelector("[data-avito-search]")?.focus(); });
  root.querySelectorAll("[data-avito-filter]").forEach((select) => select.addEventListener("change", () => { ui.filters[select.dataset.avitoFilter] = select.value; renderAvitoDashboard(payload); }));
  root.querySelectorAll("[data-avito-metric]").forEach((checkbox) => checkbox.addEventListener("change", () => {
    const selected = [...root.querySelectorAll("[data-avito-metric]:checked")].map((item) => item.value);
    if (!selected.length || selected.length > 3) { checkbox.checked = !checkbox.checked; return; }
    ui.metrics = selected; renderAvitoDashboard(payload);
  }));
  root.querySelectorAll("[data-avito-axis-select]").forEach((select) => select.addEventListener("change", () => { ui.axes[select.dataset.avitoAxisSelect] = select.value === "right" ? "right" : "left"; renderAvitoDashboard(payload); }));
  root.querySelectorAll("[data-avito-legend]").forEach((legend) => legend.addEventListener("click", (event) => { if (event.target.closest("select")) return; const key=legend.dataset.avitoLegend; ui.types[key]=ui.types[key]==="bar"?"line":"bar"; renderAvitoDashboard(payload); }));
  root.querySelectorAll("[data-avito-scatter]").forEach((select)=>select.addEventListener("change",()=>{const target=select.dataset.avitoScatter;ui[target==="x"?"scatterX":target==="y"?"scatterY":"scatterSize"]=select.value;renderAvitoDashboard(payload);}));
  root.querySelector("[data-avito-ranking-metric]")?.addEventListener("change",(event)=>{ui.rankingMetric=event.target.value;renderAvitoDashboard(payload);});
  root.querySelector("[data-avito-reset]")?.addEventListener("click", () => { avitoUiByDashboard[state.dashboard] = null; renderAvitoDashboard(payload); });
  root.querySelector("[data-avito-export]")?.addEventListener("click", exportTableToExcel);
}

async function loadAvitoDashboard() {
  const today = toIsoDate(new Date());
  if (!qs("date_to").value) qs("date_to").value = today;
  if (!qs("date_from").value) qs("date_from").value = shiftDate(qs("date_to").value, -29);
  updateDateRangeToggle();
  updateNavigation();
  updateFilterVisibility();
  setAvitoShellVisible(true);
  qs("exportExcel")?.classList.remove("hidden");
  setChartExportButtonsVisible(false);
  qs("dashboardTitle").textContent = avitoDashboardTitles[state.dashboard] || "Avito Ads";
  const params = new URLSearchParams({
    client: currentClient(), dashboard: state.dashboard,
    date_from: qs("date_from").value, date_to: qs("date_to").value,
  });
  const payload = await getJson(`/api/avito-ads-dashboard?${params.toString()}`);
  renderAvitoDashboard(payload);
  qs("status").textContent = `${clientLabel()} · Avito Ads · обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
  markFiltersDirty(false);
  finishInitialReportLoading();
}

async function loadYandexDashboard() {
  const today = toIsoDate(new Date());
  if (!qs("date_to").value) qs("date_to").value = today;
  if (!qs("date_from").value) qs("date_from").value = shiftDate(qs("date_to").value, -90);
  updateDateRangeToggle();
  updateNavigation();
  updateFilterVisibility();
  setYandexShellVisible(true);
  qs("exportExcel")?.classList.add("hidden");
  setChartExportButtonsVisible(false);
  qs("dashboardTitle").textContent = yandexDashboardTitles[state.dashboard] || "Яндекс Маркет";
  const selectedStore = state.dashboard === "yandexPromotion" ? "" : (yandexSelectedStoreByClient[currentClient()] || "");
  const params = new URLSearchParams({
    client: currentClient(),
    dashboard: state.dashboard,
    date_from: qs("date_from").value,
    date_to: qs("date_to").value,
  });
  if (selectedStore) params.set("store", selectedStore);
  const payload = await getJson(`/api/yandex-market/analytics?${params.toString()}`);
  window.YandexMarketDashboard?.render(qs("yandexDashboard"), payload, {
    dashboard: state.dashboard,
    clientLabel: clientLabel(),
    onStoreChange: (store) => {
      yandexSelectedStoreByClient[currentClient()] = store || "";
      loadYandexDashboard().catch((error) => setStatusError(error, "Загрузка отчёта Яндекс Маркета"));
    },
  });
  qs("status").textContent = `${clientLabel()} · Яндекс Маркет · обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
  markFiltersDirty(false);
  finishInitialReportLoading();
}

async function loadData() {
  if (state.mode === "api") persistDashboardState();
  qs("status").textContent = "Загрузка данных...";
  qs("advCampaignTableSection")?.classList.add("hidden");
  setSkuCardPageVisible(false);
  ensureDashboardSupportedForClient();
  if (state.dashboard !== "admin" && !currentClient()) {
    showAccountRequiredPlaceholder();
    return;
  }
  clearAccountRequiredPlaceholder();
  if (state.dashboard !== "seoMonitoring") window.SeoProjectsApp?.unmount?.();
  if (state.dashboard === "admin" && !adminAccessEnabled()) {
    state.dashboard = "abc";
  }
  setYandexShellVisible(false);
  if (state.dashboard === "salesPlanning" || state.dashboard === "mediaPlan") {
    updateNavigation();
    if (state.dashboard === "mediaPlan") await renderKmMediaPlanDashboard();
    else await renderKmSalesPlanningDashboard();
    persistDashboardState();
    return;
  }
  if (state.dashboard === "profitLoss" || state.dashboard === "unitEconomics") {
    updateNavigation();
    await renderKmFinanceDashboard();
    persistDashboardState();
    return;
  }
  setFinanceShellVisible(false);
  setAvitoShellVisible(false);
  if (isAvitoDashboard()) {
    await loadAvitoDashboard();
    persistDashboardState();
    return;
  }
  if (isYandexDashboard()) {
    await loadYandexDashboard();
    persistDashboardState();
    return;
  }
  if (state.dashboard === "seoMonitoring" && window.SeoProjectsApp) {
    updateNavigation();
    updateFilterVisibility();
    qs("dashboardTitle").textContent = state.seoProjectsNavSource === "semantics" ? "Семантика, генерация SEO" : "Мониторинг SEO";
    document.querySelector(".filters")?.classList.add("hidden");
    document.querySelector(".kpis")?.classList.add("hidden");
    document.querySelector(".visuals")?.classList.add("hidden");
    document.querySelector(".table-wrap")?.classList.add("hidden");
    document.querySelector(".pagination")?.classList.add("hidden");
    qs("exportExcel")?.classList.add("hidden");
    setChartExportButtonsVisible(false);
    await window.SeoProjectsApp.mount({
      client: currentClient(),
      marketplace: currentMarketplace(),
      marketplaces: clientMarketplaceIds(),
      projectKind: state.seoProjectsNavSource === "semantics" ? "generation" : "monitoring",
    });
    qs("status").textContent = `${clientLabel()} · ${currentMarketplaceLabel()}: SEO-проекты обновлены ${new Date().toLocaleTimeString("ru-RU")}`;
    markFiltersDirty(false);
    persistDashboardState();
    return;
  }
  if (state.dashboard === "admin") {
    setChartExportButtonsVisible(false);
    updateNavigation();
    updateFilterVisibility();
    await renderAdminDashboard();
    persistDashboardState();
    return;
  }
  qs("exportExcel").classList.remove("hidden");
  document.querySelector(".filters").classList.remove("hidden");
  document.querySelector(".kpis").classList.remove("hidden");
  document.querySelector(".visuals").classList.remove("admin-dashboard");
  qs("abcChart").closest(".panel").classList.remove("hidden");
  let summary;
  let rows;
  let columns;
  let dailyRows = [];
  let advWaterfalls = {};
  let monthlyRows = [];
  let scorecardRows = [];
  let weeklyRankings = {};
  let boironAdvExtras = null;
  let advCampaignsPayload = { rows: [], columns: [], total: 0 };
  let wbSearchQueriesPayload = {};
  let wbEntrancePayload = {};
  let inventoryProductsPayload = {};
  let dataUnavailableMessage = "";

  if (state.mode === "static") {
    const filtered = filteredStaticRows();
    rows = pagedStaticRows(filtered);
    summary = summarizeRows(filtered);
    columns = defaultColumns.filter((column) => rows.some((row) => Object.prototype.hasOwnProperty.call(row, column.key)));
  } else {
    const query = currentQuery();
    if (state.dashboard === "seoMonitoring") {
      const statsPayload = await getJson(`/api/seo-monitoring-products?${query}`);
      summary = {};
      rows = statsPayload.rows || [];
      columns = statsPayload.columns || [];
      state.page = statsPayload.page || 1;
      state.totalPages = statsPayload.total_pages || 1;
      state.total = statsPayload.total || 0;
      state.sortCol = statsPayload.sort_col || "zakazano_rub";
      state.sortDir = statsPayload.sort_dir || "desc";
      state.seoMonitoringValuationNote = statsPayload.valuation_note || "";
    } else if (state.dashboard === "wbSearchQueries") {
      const statsPayload = await getJson(`/api/wb-search-queries-dashboard?${query}`);
      wbSearchQueriesPayload = statsPayload || {};
      summary = statsPayload.summary || {};
      rows = statsPayload.rows || [];
      columns = statsPayload.columns || [];
      dailyRows = statsPayload.daily || [];
      state.page = statsPayload.page || 1;
      state.totalPages = statsPayload.total_pages || 1;
      state.total = statsPayload.total || 0;
      state.sortCol = statsPayload.sort_col || "category_rank";
      state.sortDir = statsPayload.sort_dir || "desc";
    } else if (state.dashboard === "wbEntrance") {
      const statsPayload = await getJson(`/api/wb-entrance-dashboard?${query}`);
      wbEntrancePayload = statsPayload || {};
      summary = statsPayload.summary || {};
      rows = statsPayload.rows || [];
      columns = statsPayload.columns || [];
      dailyRows = statsPayload.daily || [];
      state.page = statsPayload.page || 1;
      state.totalPages = statsPayload.total_pages || 1;
      state.total = statsPayload.total || 0;
      state.sortCol = statsPayload.sort_col || "ordered_units";
      state.sortDir = statsPayload.sort_dir || "desc";
    } else {
    const summaryUrl = state.dashboard === "sku"
      ? `/api/sku-summary?${query}`
      : (state.dashboard === "product" ? `/api/product-summary?${query}` : (state.dashboard === "adv" ? `/api/adv-summary?${query}` : (state.dashboard === "mediaAdv" ? `/api/media-adv-summary?${query}` : (state.dashboard === "inventoryHistory" ? `/api/inventory-history-summary?${query}` : ((state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") ? `/api/funnel-summary?${query}` : (state.dashboard === "planfact" ? `/api/planfact-summary?${query}` : `/api/summary?${query}`))))));
    const statsUrl = state.dashboard === "planfact" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" ? null : (state.dashboard === "sku"
      ? `/api/sku-stats?${query}`
      : (state.dashboard === "product" ? `/api/product-stats?${query}` : (state.dashboard === "adv" ? `/api/adv-stats?${query}` : (state.dashboard === "mediaAdv" ? `/api/media-adv-stats?${query}` : (state.dashboard === "funnel" ? `/api/funnel-stats?${query}` : `/api/stats?${query}`)))));
    const dailyUrl = state.dashboard === "adv" ? `/api/adv-daily?${query}` : (state.dashboard === "mediaAdv" ? `/api/media-adv-daily?${query}` : (state.dashboard === "inventoryHistory" ? `/api/inventory-history?${query}` : ((state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") ? `/api/funnel-daily?${query}` : (state.dashboard === "planfact" ? `/api/planfact-daily?${query}` : null))));
    const waterfallsUrl = state.dashboard === "adv" ? `/api/adv-waterfalls?${query}` : (state.dashboard === "mediaAdv" ? `/api/media-adv-waterfalls?${query}` : (state.dashboard === "planfact" ? `/api/planfact-monthly?${query}` : null));
    const scorecardUrl = state.dashboard === "planfact" ? `/api/planfact-scorecard?${query}` : null;
    const weeklyDynamicsUrl = state.dashboard === "weeklyDynamics" ? `/api/weekly-dynamics?${query}` : null;
    const inventoryProductsUrl = state.dashboard === "inventoryHistory" ? `/api/inventory-history-products?${query}` : null;
    const advCampaignsUrl = state.dashboard === "adv" && currentMarketplace() === "ozon" ? `/api/adv-campaigns?${query}` : null;
    const optionalJson = (url, fallback) => {
      if (!url) return Promise.resolve(fallback);
      if (state.dashboard !== "mediaAdv") return getJson(url);
      return getJson(url).catch((error) => {
        if (friendlyErrorState(error) === "preparing") {
          console.error(`[KOKOC BI] Медийная реклама: витрина недоступна`, error);
          dataUnavailableMessage = dataUnavailableMessage || friendlyErrorMessage(error);
          return fallback;
        }
        throw error;
      });
    };
    const [summaryPayload, statsPayload, dailyPayload, waterfallsPayload, scorecardPayload, weeklyDynamicsPayload, fetchedInventoryProductsPayload, fetchedAdvCampaignsPayload] = await Promise.all([
      optionalJson(summaryUrl, {}),
      statsUrl ? optionalJson(statsUrl, { rows: [], columns: [], page: 1, total_pages: 1, total: 0, sort_col: state.sortCol, sort_dir: state.sortDir }) : Promise.resolve({ rows: [], columns: [], page: 1, total_pages: 1, total: 0, sort_col: state.sortCol, sort_dir: state.sortDir }),
      dailyUrl ? optionalJson(dailyUrl, { rows: [] }) : Promise.resolve({ rows: [] }),
      waterfallsUrl ? optionalJson(waterfallsUrl, {}) : Promise.resolve({}),
      scorecardUrl ? optionalJson(scorecardUrl, { rows: [] }) : Promise.resolve({ rows: [] }),
      weeklyDynamicsUrl ? optionalJson(weeklyDynamicsUrl, {}) : Promise.resolve({}),
      inventoryProductsUrl ? optionalJson(inventoryProductsUrl, {}) : Promise.resolve({}),
      advCampaignsUrl ? optionalJson(advCampaignsUrl, { rows: [], columns: [], total: 0 }) : Promise.resolve({ rows: [], columns: [], total: 0 }),
    ]);
    inventoryProductsPayload = fetchedInventoryProductsPayload || {};
    advCampaignsPayload = fetchedAdvCampaignsPayload || { rows: [], columns: [], total: 0 };
    if (state.dashboard === "inventoryHistory") lastInventoryHistoryProductsPayload = inventoryProductsPayload;
    summary = state.dashboard === "inventoryHistory" ? (inventoryProductsPayload.summary || summaryPayload) : summaryPayload;
    rows = statsPayload.rows;
    columns = statsPayload.columns;
    dailyRows = dailyPayload.rows || [];
    advWaterfalls = state.dashboard === "adv" ? (waterfallsPayload || {}) : {};
    if (state.dashboard === "mediaAdv") advWaterfalls = waterfallsPayload || {};
    monthlyRows = state.dashboard === "planfact" ? (waterfallsPayload.rows || []) : [];
    scorecardRows = state.dashboard === "planfact" ? (scorecardPayload.rows || []) : [];
    weeklyRankings = state.dashboard === "weeklyDynamics" ? (weeklyDynamicsPayload || {}) : {};
    if (state.dashboard === "weeklyDynamics") lastWeeklyDynamicsRankings = weeklyRankings;
    if (state.dashboard === "planfact") {
      summary = scorecardRows[0] || summary;
    }
    if (state.dashboard === "adv" && currentClient() === "boiron") {
      const [planfactPayload, analysisPayload] = await Promise.all([
        getJson(`/api/boiron-adv-planfact?${query}`),
        getJson(`/api/boiron-adv-analysis?${query}`),
      ]);
      boironAdvExtras = { planfact: planfactPayload, analysis: analysisPayload, query };
    }
    state.page = statsPayload.page;
    state.totalPages = statsPayload.total_pages;
    state.total = statsPayload.total;
    state.sortCol = statsPayload.sort_col;
    state.sortDir = statsPayload.sort_dir;
    }
  }

  state.rows = rows;
  if (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics") {
    updateFunnelAvailableMetrics(summary);
  } else {
    state.funnelAvailableMetrics = null;
  }
  const selectedCategoriesForTitle = selectedValues("category");
  const skuTitle = selectedCategoriesForTitle.length === 1
    ? `SKU-скоринг: ${selectedCategoriesForTitle[0]}`
    : `SKU-скоринг: ${selectedCategoriesForTitle.length || "все"} категорий`;
  const productTitle = selectedCategoriesForTitle.length === 1
    ? `ABC по продуктам: ${selectedCategoriesForTitle[0]}`
    : `ABC по продуктам: ${selectedCategoriesForTitle.length || "все"} категорий`;
  const advMarketplace = qs("marketplace").selectedOptions[0]?.textContent || "Ozon";
  const selectedBoironBrandsForTitle = selectedBoironBrandValues();
  const advTitle = currentClient() === "boiron"
    ? (selectedBoironBrandsForTitle.length === 1
        ? `Товарная реклама ${advMarketplace}: ${selectedBoironBrandsForTitle[0]}`
        : `Товарная реклама ${advMarketplace}: ${selectedBoironBrandsForTitle.length ? `${selectedBoironBrandsForTitle.length} брендов` : "все бренды"}`)
    : (selectedCategoriesForTitle.length === 1
        ? `Реклама ${advMarketplace}: ${selectedCategoriesForTitle[0]}`
        : `Реклама ${advMarketplace}: ${selectedCategoriesForTitle.length || "все"} категорий`);
  const mediaAdvMarketplace = qs("marketplace").selectedOptions[0]?.textContent || "Ozon";
  const mediaAdvTitle = selectedCategoriesForTitle.length === 1
    ? `Медийная реклама ${mediaAdvMarketplace}: ${selectedCategoriesForTitle[0]}`
    : `Медийная реклама ${mediaAdvMarketplace}: ${selectedCategoriesForTitle.length || "все"} форматы`;
  const funnelMarketplace = qs("marketplace").selectedOptions[0]?.textContent || "Ozon";
  const inventoryHistoryTitle = selectedCategoriesForTitle.length === 1
    ? `История запасов ${funnelMarketplace}: ${selectedCategoriesForTitle[0]}`
    : `История запасов ${funnelMarketplace}`;
  const funnelTitle = selectedCategoriesForTitle.length === 1
    ? `Воронка продаж ${funnelMarketplace}: ${selectedCategoriesForTitle[0]}`
    : `Воронка продаж ${funnelMarketplace}: ${selectedCategoriesForTitle.length || "все"} категорий`;
  const weeklyDynamicsTitle = selectedCategoriesForTitle.length === 1
    ? `Еженедельная динамика ${funnelMarketplace}: ${selectedCategoriesForTitle[0]}`
    : `Еженедельная динамика ${funnelMarketplace}: ${selectedCategoriesForTitle.length || "все"} категорий`;
  const planFactTitle = "План/факт";
  const seoMonitoringTitle = selectedCategoriesForTitle.length === 1
    ? `Мониторинг SEO: ${selectedCategoriesForTitle[0]}`
    : "Мониторинг SEO";
  qs("dashboardTitle").textContent = state.dashboard === "sku" ? skuTitle : (state.dashboard === "product" ? productTitle : (state.dashboard === "adv" ? advTitle : (state.dashboard === "mediaAdv" ? mediaAdvTitle : (state.dashboard === "funnel" ? funnelTitle : (state.dashboard === "weeklyDynamics" ? weeklyDynamicsTitle : (state.dashboard === "inventoryHistory" ? inventoryHistoryTitle : (state.dashboard === "planfact" ? planFactTitle : (state.dashboard === "seoMonitoring" ? seoMonitoringTitle : "ABC по категориям"))))))));
  if (state.dashboard === "wbSearchQueries") qs("dashboardTitle").textContent = "запросы ВБ";
  if (state.dashboard === "wbEntrance") qs("dashboardTitle").textContent = "Точки входа WB";
  qs("backToAbc").classList.toggle("hidden", state.dashboard !== "sku" && !(state.dashboard === "product" && state.drillCategory));
  updateNavigation();
  updateFilterVisibility();
  const visuals = document.querySelector(".visuals");
  const isSeoMonitoring = state.dashboard === "seoMonitoring";
  const isWbSearchQueries = state.dashboard === "wbSearchQueries";
  const isWbEntrance = state.dashboard === "wbEntrance";
  document.querySelector(".kpis")?.classList.toggle("hidden", isSeoMonitoring);
  visuals.classList.toggle("hidden", isSeoMonitoring);
  visuals.classList.toggle("adv-dashboard", state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "planfact" || isWbSearchQueries || isWbEntrance);
  qs("exportExcel")?.classList.toggle("hidden", isSeoMonitoring || isWbSearchQueries || isWbEntrance || state.dashboard === "inventoryHistory");
  setChartExportButtonsVisible(!isSeoMonitoring && chartExportDashboards().has(state.dashboard));
  updateChartMetricControls();
  if (!isSeoMonitoring) renderKpis(summary, isWbEntrance ? wbEntrancePayload : wbSearchQueriesPayload);
  if (state.dashboard === "sku") {
    renderSkuCharts(rows);
  } else if (state.dashboard === "product") {
    renderProductCharts(rows);
  } else if (state.dashboard === "adv") {
    renderAdvCharts(dailyRows, advWaterfalls);
    if (currentClient() === "boiron") renderBoironAdvExtras(boironAdvExtras);
  } else if (state.dashboard === "mediaAdv") {
    renderMediaAdvCharts(dailyRows, advWaterfalls);
  } else if (state.dashboard === "funnel") {
    renderFunnelCharts(dailyRows);
  } else if (state.dashboard === "inventoryHistory") {
    renderInventoryHistoryCharts(dailyRows, inventoryProductsPayload);
  } else if (state.dashboard === "weeklyDynamics") {
    renderWeeklyDynamicsCharts(dailyRows, weeklyRankings);
  } else if (state.dashboard === "planfact") {
    resetPlanFactDefaultChartState();
    renderPlanFactCharts(dailyRows, monthlyRows, scorecardRows);
  } else if (state.dashboard === "wbSearchQueries") {
    renderWbSearchQueriesCharts(dailyRows, wbSearchQueriesPayload);
  } else if (state.dashboard === "wbEntrance") {
    renderWbEntranceCharts(dailyRows, wbEntrancePayload);
  } else if (state.dashboard === "seoMonitoring") {
  } else {
    qs("abcChart").classList.remove("bar-chart");
    renderBars(rows);
    renderAbc(rows);
  }
  renderAdvCampaignTable(advCampaignsPayload);
  const showTable = state.dashboard !== "planfact" && state.dashboard !== "weeklyDynamics" && state.dashboard !== "inventoryHistory";
  setTableVisible(showTable);
  if (showTable) {
    if (state.dashboard === "seoMonitoring") renderSeoMonitoringTable(columns, rows);
    else renderTable(columns, rows);
    if (isWbSearchQueries || isWbEntrance) qs("exportTableExcel")?.classList.add("hidden");
    renderPagination();
  }

  if (state.mode === "static") {
    const generatedAt = window.DASHBOARD_DATA.generated_at || "неизвестно";
    qs("status").textContent = `Автономная выгрузка: ${generatedAt}`;
  } else {
    const marketplace = qs("marketplace").selectedOptions[0]?.textContent || "";
    const section = state.dashboard === "sku" ? "SKU-скоринг" : (state.dashboard === "product" ? "ABC по продуктам" : (state.dashboard === "adv" ? `Реклама ${marketplace || ""}`.trim() : (state.dashboard === "mediaAdv" ? `Медийная реклама ${marketplace || ""}`.trim() : (state.dashboard === "funnel" ? "Воронка продаж" : (state.dashboard === "weeklyDynamics" ? "Еженедельная динамика" : (state.dashboard === "inventoryHistory" ? "История запасов" : (state.dashboard === "planfact" ? "План/факт" : (state.dashboard === "seoMonitoring" ? "Мониторинг SEO" : "ABC по категориям"))))))));
    qs("status").textContent = dataUnavailableMessage || `${clientLabel()} · ${marketplace}: ${section} обновлен ${new Date().toLocaleTimeString("ru-RU")}`;
    if (isWbSearchQueries) {
      qs("status").textContent = `${clientLabel()} · WB: запросы ВБ обновлен ${new Date().toLocaleTimeString("ru-RU")}`;
    }
  }
  markFiltersDirty(false);
  persistDashboardState();
}

function debounce(fn, wait = 350) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function boot() {
  if (hasExplicitDashboardContext()) {
    document.documentElement.classList.add("explicit-dashboard-context");
  }
  restoreDashboardState();
  if (state.dashboard === "admin") state.client = "";
  renderClientSelector();
  document.documentElement.classList.remove("explicit-dashboard-preboot-pending");
  try {
    if (state.mode === "api") {
      const selectedClient = currentClient();
      const health = await getJson(selectedClient ? `/api/health?client=${encodeURIComponent(selectedClient)}` : "/api/health");
      state.clientLocked = Boolean(health.client_locked);
      if (isSharedDashboardRoute()) state.clientLocked = false;
      if (Array.isArray(health.clients) && health.clients.length) {
        state.clients = health.clients;
        rememberNavigationEntitlements(health.clients);
      }
      if (state.client && !state.clients.some((client) => client.key === state.client)) {
        state.client = state.clients[0]?.key || "toptop";
      }
      ensureDashboardSupportedForClient(state.client);
      if (state.clientLocked) {
        state.client = health.locked_client || health.client || state.clients[0]?.key || "sportmaster";
        if (state.dashboard === "admin") state.dashboard = firstSupportedDashboard(state.client);
      }
      renderClientSelector();
      applyLockedClientUi();
    }
  } catch (error) {
    if (window.DASHBOARD_DATA) {
      state.mode = "static";
    } else {
      setStatusError(error);
      finishInitialReportLoading(error);
      return;
    }
  }

  if (state.dashboard === "admin") {
    state.adminLoginRequested = true;
    if (!(await checkAdminAuth())) {
      finishInitialReportLoading();
      showAdminLogin(state.adminAuthConfigured === false ? "Авторизация админки пока не настроена." : "");
      return;
    } else {
      state.adminLoginRequested = false;
    }
  }

  try {
    if (state.dashboard !== "admin" && !isYandexDashboard()) await loadFilters();
    await loadData();
    finishInitialReportLoading();
  } catch (error) {
    setStatusError(error);
    finishInitialReportLoading(error);
  }
}

const delayedLoad = debounce(() => {
  if (state.dashboard === "sku" || state.dashboard === "product") {
    state.drillCategory = "";
  }
  state.page = 1;
  loadData().catch((error) => {
    setStatusError(error);
  });
});

dataFilterIds.forEach((id) => {
  if (id !== "category" && id !== "category_level") {
    const element = qs(id);
    if (!element) return;
    element.addEventListener("change", () => {
      if (id === "adv_campaign_id") syncPendingAdvCampaignFilter();
      markFiltersDirty();
      refreshFilterOptionsAfterDraftChange();
    });
    if (id === "gj_model" || id === "sm_model") {
      element.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        markFiltersDirty();
        refreshFilterOptionsAfterDraftChange();
      });
    }
  }
});

["article", "product"].forEach((id) => {
  qs(id).addEventListener("input", () => {
    markFiltersDirty();
    refreshFilterOptionsAfterDraftChange();
  });
});

qs("period_group").addEventListener("change", () => {
  if (state.dashboard === "weeklyDynamics" && lastFunnelDailyRows.length) {
    renderWeeklyDynamicsCharts(lastFunnelDailyRows, lastWeeklyDynamicsRankings);
    persistDashboardState();
  } else if (state.dashboard === "funnel" && lastFunnelDailyRows.length) {
    renderFunnelCharts(lastFunnelDailyRows);
    persistDashboardState();
  } else if (state.dashboard === "adv" && lastAdvDailyRows.length) {
    renderAdvCharts(lastAdvDailyRows, lastAdvWaterfalls);
    persistDashboardState();
  } else if (state.dashboard === "mediaAdv" && lastMediaAdvDailyRows.length) {
    renderMediaAdvCharts(lastMediaAdvDailyRows, lastMediaAdvWaterfalls);
    persistDashboardState();
  } else {
    delayedLoad();
  }
});

function handlePlanFactChartChange(event) {
  const planfactAxisSelect = event.target.closest("[data-planfact-axis-select]");
  if (planfactAxisSelect && state.dashboard === "planfact") {
    const group = planfactAxisSelect.dataset.planfactGroup === "expense" ? "expense" : "revenue";
    const groupState = planfactGroupState(group);
    state[groupState.axesKey][planfactAxisSelect.dataset.planfactAxisSelect] = planfactAxisSelect.value === "right" ? "right" : "left";
    renderPlanFactCharts(lastPlanFactDailyRows, lastPlanFactMonthlyRows, lastPlanFactScorecardRows);
    persistDashboardState();
    return true;
  }
  const planfactInput = event.target.closest("[data-planfact-metric]");
  if (planfactInput && state.dashboard === "planfact") {
    const group = planfactInput.dataset.planfactGroup === "expense" ? "expense" : "revenue";
    const groupState = planfactGroupState(group);
    const selected = new Set(state[groupState.selectedKey]);
    if (planfactInput.checked) {
      selected.add(planfactInput.dataset.planfactMetric);
    } else {
      selected.delete(planfactInput.dataset.planfactMetric);
    }
    if (!selected.size) {
      selected.add(planfactInput.dataset.planfactMetric);
    }
    state[groupState.selectedKey] = [...selected];
    renderPlanFactCharts(lastPlanFactDailyRows, lastPlanFactMonthlyRows, lastPlanFactScorecardRows);
    persistDashboardState();
    return true;
  }
  return false;
}

function handlePlanFactChartClick(event) {
  const planfactLegend = event.target.closest("[data-planfact-legend]");
  if (planfactLegend && state.dashboard === "planfact" && !event.target.closest("select")) {
    const key = planfactLegend.dataset.planfactLegend;
    const group = planfactLegend.dataset.planfactGroup === "expense" ? "expense" : "revenue";
    const groupState = planfactGroupState(group);
    state[groupState.typesKey][key] = state[groupState.typesKey][key] === "bar" ? "line" : "bar";
    renderPlanFactCharts(lastPlanFactDailyRows, lastPlanFactMonthlyRows, lastPlanFactScorecardRows);
    persistDashboardState();
    return true;
  }
  return false;
}

qs("barChart").addEventListener("change", (event) => {
  const editedClientForm = event.target.closest("[data-admin-client-form]");
  if (editedClientForm) editedClientForm.dataset.adminClientDraftDirty = "true";
  const onboardingMarketplace = event.target.closest('input[name="clientOnboardingMarketplace"]');
  if (onboardingMarketplace && state.dashboard === "admin") {
    state.adminOnboardingDraftName = qs("newClientName")?.value || "";
    state.adminOnboardingMarketplace = CLIENT_ONBOARDING_MARKETPLACES[onboardingMarketplace.value]
      ? onboardingMarketplace.value
      : "ozon";
    renderAdminDashboardContent(lastAdminPayload);
    return;
  }
  const historyClientSelect = event.target.closest("[data-client-history-client]");
  if (historyClientSelect && state.dashboard === "admin") {
    const previous = (state.adminClientRegistry?.clients || []).find((row) => row.key === state.adminOnboardingClientKey);
    if (previous) {
      previous.history_date_from = qs("clientHistoryDateFrom")?.value || previous.history_date_from;
      previous.history_date_to = qs("clientHistoryDateTo")?.value || previous.history_date_to;
      rememberWbStockHistoryRange(previous.key);
    }
    state.adminOnboardingClientKey = historyClientSelect.value || "";
    state.adminHistoryOverwrite = false;
    renderAdminDashboardContent(lastAdminPayload);
    persistDashboardState();
    if (["wb", "avito", "yandex_market"].includes(state.adminHistoryMarketplace)) {
      loadAdminClientHistoryInspection().catch((error) => setStatusError(error));
    }
    return;
  }
  const historyOverwrite = event.target.closest("[data-client-history-overwrite]");
  if (historyOverwrite && state.dashboard === "admin") {
    state.adminHistoryOverwrite = historyOverwrite.checked;
    const warning = document.querySelector("[data-client-history-overwrite-warning]");
    if (warning) warning.hidden = !state.adminHistoryOverwrite;
    return;
  }
  const adminClientSelect = event.target.closest("[data-admin-client]");
  if (adminClientSelect && state.dashboard === "admin") {
    state.adminClient = adminClientSelect.value || "toptop";
    state.adminImportStatuses = {};
    state.adminTerminalLines = [];
    state.adminLiveProgress = null;
    renderAdminDashboard()
      .then(() => persistDashboardState())
      .catch((error) => {
        setStatusError(error);
      });
    return;
  }
  if (handlePlanFactChartChange(event)) return;
  const advAxisSelect = event.target.closest("[data-adv-axis-select]");
  if (advAxisSelect && state.dashboard === "adv") {
    state.advMetricAxes[advAxisSelect.dataset.advAxisSelect] = advAxisSelect.value === "right" ? "right" : "left";
    renderAdvCharts(lastAdvDailyRows, lastAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const advInput = event.target.closest("[data-adv-metric]");
  if (advInput && state.dashboard === "adv") {
    const selected = new Set(state.advSelectedMetrics);
    if (advInput.checked) {
      selected.add(advInput.dataset.advMetric);
    } else {
      selected.delete(advInput.dataset.advMetric);
    }
    state.advSelectedMetrics = [...selected].filter((key) => advMetricConfigs.some((metric) => metric.key === key));
    if (!state.advSelectedMetrics.length) state.advSelectedMetrics = [advInput.dataset.advMetric];
    renderAdvCharts(lastAdvDailyRows, lastAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const mediaAdvAxisSelect = event.target.closest("[data-media-adv-axis-select]");
  if (mediaAdvAxisSelect && state.dashboard === "mediaAdv") {
    state.mediaAdvMetricAxes[mediaAdvAxisSelect.dataset.mediaAdvAxisSelect] = mediaAdvAxisSelect.value === "right" ? "right" : "left";
    renderMediaAdvCharts(lastMediaAdvDailyRows, lastMediaAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const mediaAdvInput = event.target.closest("[data-media-adv-metric]");
  if (mediaAdvInput && state.dashboard === "mediaAdv") {
    const selected = new Set(state.mediaAdvSelectedMetrics);
    if (mediaAdvInput.checked) {
      selected.add(mediaAdvInput.dataset.mediaAdvMetric);
    } else {
      selected.delete(mediaAdvInput.dataset.mediaAdvMetric);
    }
    state.mediaAdvSelectedMetrics = [...selected].filter((key) => mediaAdvMetricConfigs.some((metric) => metric.key === key));
    if (!state.mediaAdvSelectedMetrics.length) state.mediaAdvSelectedMetrics = [mediaAdvInput.dataset.mediaAdvMetric];
    renderMediaAdvCharts(lastMediaAdvDailyRows, lastMediaAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const wbSearchTypeSelect = event.target.closest("[data-wb-search-type-select]");
  if (wbSearchTypeSelect && ["wbSearchQueries", "wbEntrance"].includes(state.dashboard)) {
    const chartState = wbChartStateConfig();
    state[chartState.typesKey][wbSearchTypeSelect.dataset.wbSearchTypeSelect] = wbSearchTypeSelect.value === "bar" ? "bar" : "line";
    if (state.dashboard === "wbEntrance") renderWbEntranceMainChart();
    else renderWbSearchQueriesMainChart();
    persistDashboardState();
    return;
  }
  const wbSearchAxisSelect = event.target.closest("[data-wb-search-axis-select]");
  if (wbSearchAxisSelect && ["wbSearchQueries", "wbEntrance"].includes(state.dashboard)) {
    const chartState = wbChartStateConfig();
    state[chartState.axesKey][wbSearchAxisSelect.dataset.wbSearchAxisSelect] = wbSearchAxisSelect.value === "right" ? "right" : "left";
    if (state.dashboard === "wbEntrance") renderWbEntranceMainChart();
    else renderWbSearchQueriesMainChart();
    persistDashboardState();
    return;
  }
  const wbSearchInput = event.target.closest("[data-wb-search-metric]");
  if (wbSearchInput && ["wbSearchQueries", "wbEntrance"].includes(state.dashboard)) {
    const chartState = wbChartStateConfig();
    const selected = new Set(state[chartState.selectedKey]);
    const key = wbSearchInput.dataset.wbSearchMetric;
    if (wbSearchInput.checked) {
      selected.add(key);
    } else {
      selected.delete(key);
    }
    const activeMetricConfigs = state.dashboard === "wbEntrance" ? wbEntranceChartMetricConfigs : wbSearchChartMetricConfigs;
    const allowedMetrics = new Set(activeMetricConfigs.map((metric) => metric.field));
    const filteredSelected = [...selected].filter((metricKey) => allowedMetrics.has(metricKey));
    state[chartState.selectedKey] = filteredSelected.length ? filteredSelected : [key];
    if (state.dashboard === "wbEntrance") renderWbEntranceMainChart();
    else renderWbSearchQueriesMainChart();
    persistDashboardState();
    return;
  }
  const axisSelect = event.target.closest("[data-funnel-axis-select]");
  if (axisSelect && (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory")) {
    state.funnelMetricAxes[axisSelect.dataset.funnelAxisSelect] = axisSelect.value === "right" ? "right" : "left";
    renderFunnelMainChart();
    persistDashboardState();
    return;
  }
  const input = event.target.closest("[data-funnel-metric]");
  if (!input || (state.dashboard !== "funnel" && state.dashboard !== "weeklyDynamics" && state.dashboard !== "inventoryHistory")) return;
  const selected = new Set(state.funnelSelectedMetrics);
  const allowedMetrics = new Set(funnelMetricConfigsForMarketplace().map((metric) => metric.key));
  if (input.checked) {
    selected.add(input.dataset.funnelMetric);
  } else {
    selected.delete(input.dataset.funnelMetric);
  }
  const filteredSelected = [...selected].filter((key) => allowedMetrics.has(key));
  state.funnelSelectedMetrics = filteredSelected.length ? filteredSelected : [input.dataset.funnelMetric];
  renderFunnelMainChart();
  persistDashboardState();
});

qs("abcChart").addEventListener("change", (event) => {
  handlePlanFactChartChange(event);
});

qs("barChart").addEventListener("click", async (event) => {
  if (handlePlanFactChartClick(event)) return;
  const wbSearchReset = event.target.closest("[data-wb-search-chart-reset]");
  if (wbSearchReset && ["wbSearchQueries", "wbEntrance"].includes(state.dashboard)) {
    const chartState = wbChartStateConfig();
    state[chartState.selectedKey] = [...chartState.defaults];
    state[chartState.axesKey] = {};
    state[chartState.typesKey] = { ...chartState.defaultTypes };
    if (state.dashboard === "wbEntrance") renderWbEntranceMainChart();
    else renderWbSearchQueriesMainChart();
    persistDashboardState();
    return;
  }
  const wbSearchLegend = event.target.closest("[data-wb-search-legend]");
  if (wbSearchLegend && ["wbSearchQueries", "wbEntrance"].includes(state.dashboard) && !event.target.closest("select")) {
    const chartState = wbChartStateConfig();
    const key = wbSearchLegend.dataset.wbSearchLegend;
    state[chartState.typesKey][key] = state[chartState.typesKey][key] === "bar" ? "line" : "bar";
    if (state.dashboard === "wbEntrance") renderWbEntranceMainChart();
    else renderWbSearchQueriesMainChart();
    persistDashboardState();
    return;
  }
  const advLegend = event.target.closest("[data-adv-legend]");
  if (advLegend && state.dashboard === "adv" && !event.target.closest("select")) {
    const key = advLegend.dataset.advLegend;
    state.advMetricTypes[key] = state.advMetricTypes[key] === "bar" ? "line" : "bar";
    renderAdvCharts(lastAdvDailyRows, lastAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const mediaAdvLegend = event.target.closest("[data-media-adv-legend]");
  if (mediaAdvLegend && state.dashboard === "mediaAdv" && !event.target.closest("select")) {
    const key = mediaAdvLegend.dataset.mediaAdvLegend;
    state.mediaAdvMetricTypes[key] = state.mediaAdvMetricTypes[key] === "bar" ? "line" : "bar";
    renderMediaAdvCharts(lastMediaAdvDailyRows, lastMediaAdvWaterfalls);
    persistDashboardState();
    return;
  }
  const clearTerminalButton = event.target.closest("[data-clear-admin-terminal]");
  if (clearTerminalButton && state.dashboard === "admin") {
    state.adminTerminalLines = [];
    state.adminLiveProgress = null;
    updateAdminLiveProgressElement();
    updateAdminTerminal();
    return;
  }
  const clearApiTerminalButton = event.target.closest("[data-clear-api-terminal]");
  if (clearApiTerminalButton && state.dashboard === "admin") {
    state.apiTerminalLines = [];
    updateApiTerminal();
    return;
  }
  const wbPromotionStatusToggle = event.target.closest("[data-wb-promotion-status-toggle]");
  if (wbPromotionStatusToggle && state.dashboard === "admin") {
    event.stopPropagation();
    renderWbPromotionAdvertStatusMenu();
    toggleSingleDropdown("wbPromotionAdvertsStatusesMenu");
    return;
  }
  if (event.target.closest("#wbPromotionAdvertsStatusesMenu")) {
    event.stopPropagation();
    return;
  }
  const saveAdminClientButton = event.target.closest("[data-save-admin-client]");
  if (saveAdminClientButton && state.dashboard === "admin") {
    saveAdminClientFromUi(saveAdminClientButton).catch((error) => {
      const status = qs("newClientSaveStatus");
      if (status) {
        status.className = "client-save-status is-error";
        status.textContent = error.message;
      }
      setStatusError(error);
    });
    return;
  }
  const toggleAdminClientButton = event.target.closest("[data-toggle-admin-client]");
  if (toggleAdminClientButton && state.dashboard === "admin") {
    const key = toggleAdminClientButton.dataset.toggleAdminClient || "";
    state.adminOnboardingClientKey = state.adminOnboardingClientKey === key ? "" : key;
    renderAdminDashboardContent(lastAdminPayload);
    persistDashboardState();
    if (state.adminOnboardingClientKey) {
      getJson(`/api/admin/client-onboarding?client=${encodeURIComponent(key)}`).then((result) => {
        mergeOnboardingClient(result.client);
        renderAdminDashboardContent(lastAdminPayload);
        window.requestAnimationFrame(() => scrollAdminClientAssortmentTerminal(key));
        if (result.client?.operation === "assortment" && result.client?.operation_status === "running") {
          pollAdminClientOnboarding(key).catch((error) => setStatusError(error));
        }
      }).catch((error) => setStatusError(error));
    }
    return;
  }
  const saveAdminClientAccessButton = event.target.closest("[data-save-admin-client-access]");
  if (saveAdminClientAccessButton && state.dashboard === "admin") {
    saveAdminClientAccessFromUi(saveAdminClientAccessButton).catch((error) => {
      const status = saveAdminClientAccessButton.closest("[data-admin-client-form]")?.querySelector("[data-admin-client-save-status]");
      if (status && !error.isAdminClientValidation) {
        status.textContent = `Ошибка: ${error.message}`;
        status.classList.add("is-error");
      }
      if (!error.isAdminClientValidation) setStatusError(error);
    });
    return;
  }
  const discoverYandexMarketButton = event.target.closest("[data-discover-yandex-market]");
  if (discoverYandexMarketButton && state.dashboard === "admin") {
    discoverAdminYandexMarket(discoverYandexMarketButton).catch((error) => {
      const form = discoverYandexMarketButton.closest("[data-admin-client-form]");
      const status = form?.querySelector("[data-yandex-market-status]");
      if (status && !error.isAdminClientValidation) status.textContent = `Ошибка: ${error.message}`;
      if (!error.isAdminClientValidation) setStatusError(error);
    });
    return;
  }
  const retryClientOnboardingButton = event.target.closest("[data-retry-client-onboarding]");
  if (retryClientOnboardingButton && state.dashboard === "admin") {
    retryAdminClientOnboarding(retryClientOnboardingButton).catch((error) => {
      setStatusError(error);
    });
    return;
  }
  const openClientHistoryButton = event.target.closest("[data-open-client-history]");
  if (openClientHistoryButton && state.dashboard === "admin") {
    openAdminClientHistory(openClientHistoryButton.dataset.openClientHistory || "");
    return;
  }
  const refreshClientHistoryInspection = event.target.closest("[data-refresh-client-history-inspection]");
  if (refreshClientHistoryInspection && state.dashboard === "admin") {
    await loadAdminClientHistoryInspection({force: true}).catch((error) => setStatusError(error));
    return;
  }
  const closeClientHistoryButton = event.target.closest("[data-close-client-history]");
  if (closeClientHistoryButton && state.dashboard === "admin") {
    closeAdminClientHistory();
    return;
  }
  const stopClientHistoryButton = event.target.closest("[data-stop-client-history]");
  if (stopClientHistoryButton && state.dashboard === "admin") {
    stopAdminClientHistory(stopClientHistoryButton).catch((error) => {
      setStatusError(error);
    });
    return;
  }
  const clientHistoryMarketplaceButton = event.target.closest("[data-client-history-marketplace]");
  if (clientHistoryMarketplaceButton && state.dashboard === "admin") {
    const selected = (state.adminClientRegistry?.clients || []).find((row) => row.key === state.adminOnboardingClientKey);
    if (selected) {
      selected.history_date_from = qs("clientHistoryDateFrom")?.value || selected.history_date_from;
      selected.history_date_to = qs("clientHistoryDateTo")?.value || selected.history_date_to;
      rememberWbStockHistoryRange(selected.key);
    }
    const requestedMarketplace = clientHistoryMarketplaceButton.dataset.clientHistoryMarketplace;
    state.adminHistoryMarketplace = ["ozon", "wb", "avito", "yandex_market"].includes(requestedMarketplace)
      ? requestedMarketplace
      : "ozon";
    state.adminHistoryOverwrite = false;
    state.adminHistoryInspection = null;
    state.adminHistoryInspectionKey = "";
    renderAdminDashboardContent(lastAdminPayload);
    persistDashboardState();
    if (["wb", "avito", "yandex_market"].includes(state.adminHistoryMarketplace)) {
      loadAdminClientHistoryInspection({force: true}).catch((error) => setStatusError(error));
    }
    return;
  }
  const clientHistoryButton = event.target.closest("[data-start-client-history], [data-start-client-history-all], [data-resume-client-history]");
  if (clientHistoryButton && state.dashboard === "admin") {
    event.preventDefault();
    requestAdminClientHistoryStart(clientHistoryButton).catch((error) => {
      setStatusError(error);
    });
    return;
  }
  const cancelClientHistoryStart = event.target.closest("[data-cancel-client-history-start]");
  if (cancelClientHistoryStart && state.dashboard === "admin") {
    state.adminHistoryStartConfirmation = null;
    renderAdminDashboardContent(lastAdminPayload);
    return;
  }
  const confirmClientHistoryStart = event.target.closest("[data-confirm-client-history-start]");
  if (confirmClientHistoryStart && state.dashboard === "admin") {
    const request = state.adminHistoryStartConfirmation;
    if (!request) return;
    startAdminClientHistory(request, confirmClientHistoryStart).catch((error) => {
      setStatusError(error);
    });
    return;
  }
  const validateClientPathsButton = event.target.closest("[data-validate-client-paths]");
  if (validateClientPathsButton && state.dashboard === "admin") {
    await validateClientPathsFromUi(validateClientPathsButton);
    return;
  }
  const saveWbApiTokenButton = event.target.closest("[data-save-wb-api-token]");
  if (saveWbApiTokenButton && state.dashboard === "admin") {
    await saveWbApiTokenFromUi(saveWbApiTokenButton);
    return;
  }
  const saveOzonSeoCredentialsButton = event.target.closest("[data-save-ozon-seo-credentials]");
  if (saveOzonSeoCredentialsButton && state.dashboard === "admin") {
    await saveOzonSeoCredentialsFromUi(saveOzonSeoCredentialsButton);
    return;
  }
  const stopWbApiButton = event.target.closest("[data-stop-wb-api]");
  if (stopWbApiButton && state.dashboard === "admin") {
    await stopWbApiScriptFromUi(stopWbApiButton);
    return;
  }
  const runWbPromotionCountButton = event.target.closest("[data-run-wb-promotion-count]");
  if (runWbPromotionCountButton && state.dashboard === "admin") {
    await runWbPromotionCountFromUi(runWbPromotionCountButton);
    return;
  }
  const runWbPromotionAdvertsButton = event.target.closest("[data-run-wb-promotion-adverts]");
  if (runWbPromotionAdvertsButton && state.dashboard === "admin") {
    await runWbPromotionAdvertsFromUi(runWbPromotionAdvertsButton);
    return;
  }
  const runWbPromotionFullstatsButton = event.target.closest("[data-run-wb-promotion-fullstats]");
  if (runWbPromotionFullstatsButton && state.dashboard === "admin") {
    await runWbPromotionFullstatsFromUi(runWbPromotionFullstatsButton);
    return;
  }
  const runWbContentCategoriesButton = event.target.closest("[data-run-wb-content-categories]");
  if (runWbContentCategoriesButton && state.dashboard === "admin") {
    await runWbContentCategoriesFromUi(runWbContentCategoriesButton);
    return;
  }
  const runWbContentCardsButton = event.target.closest("[data-run-wb-content-cards]");
  if (runWbContentCardsButton && state.dashboard === "admin") {
    await runWbContentCardsFromUi(runWbContentCardsButton);
    return;
  }
  const runWbContentCharacteristicsButton = event.target.closest("[data-run-wb-content-characteristics]");
  if (runWbContentCharacteristicsButton && state.dashboard === "admin") {
    await runWbContentCharacteristicsFromUi(runWbContentCharacteristicsButton);
    return;
  }
  const runOzonSeoDetailsButton = event.target.closest("[data-run-ozon-seo-details]");
  if (runOzonSeoDetailsButton && state.dashboard === "admin") {
    await runOzonSeoDetailsFromUi(runOzonSeoDetailsButton);
    return;
  }
  const runWbAnalyticsReportButton = event.target.closest("[data-run-wb-analytics-method]");
  if (runWbAnalyticsReportButton && state.dashboard === "admin") {
    await runWbAnalyticsReportFromUi(runWbAnalyticsReportButton);
    return;
  }
  const runWbAnalyticsCsvButton = event.target.closest("[data-wb-csv-action], [data-wb-csv-row]");
  if (runWbAnalyticsCsvButton && state.dashboard === "admin") {
    await runWbAnalyticsCsvAction(runWbAnalyticsCsvButton);
    return;
  }
  const apiExportMarketplaceButton = event.target.closest("[data-api-export-marketplace]");
  if (apiExportMarketplaceButton && state.dashboard === "admin") {
    state.apiExportMarketplace = apiExportMarketplaceButton.dataset.apiExportMarketplace === "ozon" ? "ozon" : "wb";
    state.apiExportMode = state.apiExportMarketplace === "ozon" ? "seo" : "advertising";
    renderAdminDashboardContent(lastAdminPayload);
    persistDashboardState();
    return;
  }
  const apiExportModeButton = event.target.closest("[data-api-export-mode]");
  if (apiExportModeButton && state.dashboard === "admin") {
    state.apiExportMode = apiExportModeButton.dataset.apiExportMode || "advertising";
    renderAdminDashboardContent(lastAdminPayload);
    persistDashboardState();
    return;
  }
  const openApiExportLogButton = event.target.closest("[data-open-api-export-log]");
  if (openApiExportLogButton && state.dashboard === "admin") {
    const methodKey = openApiExportLogButton.dataset.openApiExportLog || "";
    const logPath = methodKey === "ozon_seo_details"
      ? (state.ozonSeoLastLogFile || state.wbApiLastLogFiles.ozon_seo_details)
      : state.wbApiLastLogFiles[methodKey];
    await openWbPromotionResultFileFromUi(openApiExportLogButton, logPath);
    return;
  }
  const openWbPromotionCountButton = event.target.closest("[data-open-wb-promotion-count-result]");
  if (openWbPromotionCountButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbPromotionCountButton, state.wbApiLastPromotionCountResultFile);
    return;
  }
  const openWbPromotionAdvertsButton = event.target.closest("[data-open-wb-promotion-adverts-result]");
  if (openWbPromotionAdvertsButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbPromotionAdvertsButton, state.wbApiLastPromotionAdvertsResultFile);
    return;
  }
  const openWbPromotionStatsButton = event.target.closest("[data-open-wb-promotion-stats-result]");
  if (openWbPromotionStatsButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbPromotionStatsButton, state.wbApiLastPromotionStatsResultFile);
    return;
  }
  const openWbContentCategoriesButton = event.target.closest("[data-open-wb-content-categories-result]");
  if (openWbContentCategoriesButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbContentCategoriesButton, state.wbApiLastContentCategoriesResultFile);
    return;
  }
  const openWbContentCardsButton = event.target.closest("[data-open-wb-content-cards-result]");
  if (openWbContentCardsButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbContentCardsButton, state.wbApiLastContentCardsResultFile);
    return;
  }
  const openWbContentCharacteristicsButton = event.target.closest("[data-open-wb-content-characteristics-result]");
  if (openWbContentCharacteristicsButton && state.dashboard === "admin") {
    await openWbPromotionResultFileFromUi(openWbContentCharacteristicsButton, state.wbApiLastContentCharacteristicsResultFile);
    return;
  }
  const saveOzonPerformanceButton = event.target.closest("[data-save-ozon-performance-credentials]");
  if (saveOzonPerformanceButton && state.dashboard === "admin") {
    saveOzonPerformanceCredentialsFromUi(saveOzonPerformanceButton).catch((error) => {
      setStatusError(error);
    });
    return;
  }
  const editAdminUserButton = event.target.closest("[data-edit-admin-user]");
  if (editAdminUserButton && state.dashboard === "admin") {
    event.preventDefault();
    const userId = Number(editAdminUserButton.dataset.editAdminUser || 0);
    if (!userId) return;
    if (userId === Number(state.adminEditingUserId)) {
      focusAdminUserEditor();
      return;
    }
    state.adminEditingUserId = userId;
    renderAdminDashboardContent(lastAdminPayload);
    focusAdminUserEditor();
    return;
  }
  const startClientAssortmentButton = event.target.closest("[data-start-client-assortment]");
  if (startClientAssortmentButton && state.dashboard === "admin") {
    startAdminClientAssortment(startClientAssortmentButton).catch((error) => setStatusError(error));
    return;
  }
  const stopClientAssortmentButton = event.target.closest("[data-stop-client-assortment]");
  if (stopClientAssortmentButton && state.dashboard === "admin") {
    stopAdminClientAssortment(stopClientAssortmentButton).catch((error) => setStatusError(error));
    return;
  }
  const toggleAdminUserPasswordButton = event.target.closest("[data-toggle-admin-user-password]");
  if (toggleAdminUserPasswordButton && state.dashboard === "admin") {
    event.preventDefault();
    const field = toggleAdminUserPasswordButton.closest(".admin-user-password-field");
    const input = field?.querySelector('input[name="password"]');
    if (!input) return;
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    const label = visible ? "Скрыть пароль" : "Показать пароль";
    toggleAdminUserPasswordButton.title = label;
    toggleAdminUserPasswordButton.setAttribute("aria-label", label);
    toggleAdminUserPasswordButton.setAttribute("aria-pressed", String(visible));
    toggleAdminUserPasswordButton.innerHTML = adminActionIcon(visible ? "eyeOff" : "eye");
    input.focus({ preventScroll: true });
    return;
  }
  const toggleAdminClientSecretButton = event.target.closest("[data-toggle-admin-client-secret]");
  if (toggleAdminClientSecretButton && state.dashboard === "admin") {
    event.preventDefault();
    const field = toggleAdminClientSecretButton.closest(".admin-client-secret-field");
    const input = field?.querySelector("[data-admin-client-credential]");
    if (!input) return;
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    const label = visible ? "Скрыть значение" : "Показать значение";
    toggleAdminClientSecretButton.title = label;
    toggleAdminClientSecretButton.setAttribute("aria-label", label);
    toggleAdminClientSecretButton.setAttribute("aria-pressed", String(visible));
    toggleAdminClientSecretButton.innerHTML = adminActionIcon(visible ? "eyeOff" : "eye");
    input.focus({ preventScroll: true });
    return;
  }
  const newAdminUserButton = event.target.closest("[data-new-admin-user]");
  if (newAdminUserButton && state.dashboard === "admin") {
    state.adminEditingUserId = 0;
    renderAdminDashboardContent(lastAdminPayload);
    return;
  }
  const resetAdminDatabaseFiltersButton = event.target.closest("[data-reset-admin-database-filters]");
  if (resetAdminDatabaseFiltersButton && state.dashboard === "admin") {
    event.preventDefault();
    resetAdminDatabaseFilters();
    return;
  }
  const refreshAdminDatabaseButton = event.target.closest("[data-refresh-admin-database]");
  if (refreshAdminDatabaseButton && state.dashboard === "admin") {
    refreshAdminDatabaseButton.disabled = true;
    loadAdminDatabaseOverview(true)
      .then(() => {
        renderAdminDashboardContent(lastAdminPayload);
        qs("status").textContent = "Структура БД обновлена";
      })
      .catch((error) => {
        setStatusError(error);
      })
      .finally(() => {
        refreshAdminDatabaseButton.disabled = false;
      });
    return;
  }
  const adminModeButton = event.target.closest("[data-admin-mode]");
  if (adminModeButton && state.dashboard === "admin") {
    switchAdminModeFromUi(adminModeButton);
    return;
  }
  const startAllClientsDailyButton = event.target.closest("[data-start-all-clients-daily]");
  if (startAllClientsDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDaily("start").catch((error) => setStatusError(error));
    return;
  }
  const stopAllClientsDailyButton = event.target.closest("[data-stop-all-clients-daily]");
  if (stopAllClientsDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDaily("stop").catch((error) => setStatusError(error));
    return;
  }
  const resumeAllClientsDailyButton = event.target.closest("[data-resume-all-clients-daily]");
  if (resumeAllClientsDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDaily("resume").catch((error) => setStatusError(error));
    return;
  }
  const startAllClientsAvitoButton = event.target.closest("[data-start-all-clients-avito]");
  if (startAllClientsAvitoButton && state.dashboard === "admin") {
    runAdminAllClientsAvito("start").catch((error) => setStatusError(error));
    return;
  }
  const stopAllClientsAvitoButton = event.target.closest("[data-stop-all-clients-avito]");
  if (stopAllClientsAvitoButton && state.dashboard === "admin") {
    runAdminAllClientsAvito("stop").catch((error) => setStatusError(error));
    return;
  }
  const resumeAllClientsAvitoButton = event.target.closest("[data-resume-all-clients-avito]");
  if (resumeAllClientsAvitoButton && state.dashboard === "admin") {
    runAdminAllClientsAvito("resume").catch((error) => setStatusError(error));
    return;
  }
  const startClientDailyButton = event.target.closest("[data-start-client-daily]");
  if (startClientDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDailyClient("start", startClientDailyButton.dataset.startClientDaily)
      .catch((error) => setStatusError(error));
    return;
  }
  const stopClientDailyButton = event.target.closest("[data-stop-client-daily]");
  if (stopClientDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDailyClient("stop", stopClientDailyButton.dataset.stopClientDaily)
      .catch((error) => setStatusError(error));
    return;
  }
  const resumeClientDailyButton = event.target.closest("[data-resume-client-daily]");
  if (resumeClientDailyButton && state.dashboard === "admin") {
    runAdminAllClientsDailyClient("resume", resumeClientDailyButton.dataset.resumeClientDaily)
      .catch((error) => setStatusError(error));
    return;
  }
  const startAllClientsAssortmentButton = event.target.closest("[data-start-all-clients-assortment]");
  if (startAllClientsAssortmentButton && state.dashboard === "admin") {
    runAdminAllClientsAssortment("start").catch((error) => setStatusError(error));
    return;
  }
  const stopAllClientsAssortmentButton = event.target.closest("[data-stop-all-clients-assortment]");
  if (stopAllClientsAssortmentButton && state.dashboard === "admin") {
    runAdminAllClientsAssortment("stop").catch((error) => setStatusError(error));
    return;
  }
  const resumeAllClientsAssortmentButton = event.target.closest("[data-resume-all-clients-assortment]");
  if (resumeAllClientsAssortmentButton && state.dashboard === "admin") {
    runAdminAllClientsAssortment("resume").catch((error) => setStatusError(error));
    return;
  }
  const stopAdminImportButton = event.target.closest("[data-stop-admin-import]");
  if (stopAdminImportButton && state.dashboard === "admin") {
    stopAdminImport().catch((error) => {
      appendAdminTerminalLine(`Ошибка остановки процесса: ${error.message}. Следующие скрипты запускаться не будут.`, "error");
      state.adminStopRequested = true;
      renderAdminDashboardContent(lastAdminPayload);
    });
    return;
  }
  const dailyChainButton = event.target.closest("[data-run-daily-chain]");
  if (dailyChainButton && state.dashboard === "admin") {
    runDailyImportChain(dailyChainButton.dataset.runDailyChain || "all").catch((error) => {
      state.adminImportRunning = false;
      state.adminCurrentImportKey = "";
      setStatusError(error);
      renderAdminDashboardContent(lastAdminPayload);
    });
    return;
  }
  const funnelLegend = event.target.closest("[data-funnel-legend]");
  if (funnelLegend && (state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory") && !event.target.closest("select")) {
    const key = funnelLegend.dataset.funnelLegend;
    state.funnelMetricTypes[key] = state.funnelMetricTypes[key] === "bar" ? "line" : "bar";
    renderFunnelMainChart();
    persistDashboardState();
    return;
  }
  const button = event.target.closest("[data-run-import]");
  if (!button || state.dashboard !== "admin") return;
  const row = adminRowsForMode(lastAdminPayload).find((item) => item.key === button.dataset.runImport);
  if (!row) return;
  captureAdminApiDates();
  button.disabled = true;
  state.adminImportRunning = true;
  state.adminStopRequested = false;
  try {
    const result = await runAdminImportInline(row);
    qs("status").textContent = result.stopped ? `Остановлено: ${row.report}` : `Импорт завершен: ${row.report}`;
  } catch (error) {
    state.adminImportStatuses[row.key] = {
      state: "error",
      title: "Ошибка",
      detail: error.message,
    };
    setStatusError(error);
  } finally {
    state.adminImportRunning = false;
    state.adminCurrentImportKey = "";
    renderAdminDashboardContent(lastAdminPayload);
  }
});

qs("abcChart").addEventListener("click", (event) => {
  handlePlanFactChartClick(event);
});

qs("categoryToggle").addEventListener("click", () => {
  toggleSingleDropdown("categoryMenu", "categorySearch");
});

qs("categoryMenu").addEventListener("input", (event) => {
  if (event.target?.id !== "categorySearch") return;
  renderCategoryOptions(event.target.value || "");
});

qs("categoryMenu").addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (target.id === "categorySearch") return;
  if (target.dataset.all === "true" && target.checked) {
    state.selectedCategories = [];
  } else if (target.value) {
    const selected = new Set(state.selectedCategories);
    if (target.checked) {
      selected.add(target.value);
    } else {
      selected.delete(target.value);
    }
    state.selectedCategories = state.categoryNames.filter((value) => selected.has(value));
  }
  renderCategoryOptions(qs("categorySearch")?.value || "");
  updateCategoryToggle();
  markFiltersDirty();
  if (state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    setProductFilter("");
  }
  refreshFilterOptionsAfterDraftChange();
});

document.querySelectorAll("[data-sportmaster-multi]").forEach((dropdown) => {
  const id = dropdown.dataset.sportmasterMulti;
  qs(`${id}Toggle`)?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleSingleDropdown(`${id}Menu`, `${id}Search`);
  });
  qs(`${id}Menu`)?.addEventListener("click", (event) => {
    event.stopPropagation();
  });
  qs(`${id}Menu`)?.addEventListener("input", (event) => {
    if (event.target?.id !== `${id}Search`) return;
    renderSportmasterFilterOptions(id, event.target.value || "");
    qs(`${id}Search`)?.focus();
  });
  qs(`${id}Menu`)?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.id === `${id}Search`) return;
    if (target.dataset.all === "true" && target.checked) {
      setSelectedSportmasterValues(id, []);
    } else {
      const values = Array.from(qs(`${id}Menu`).querySelectorAll('input[type="checkbox"]:not([data-all])'))
        .filter((input) => input.checked)
        .map((input) => input.value)
        .filter(Boolean);
      setSelectedSportmasterValues(id, values);
    }
    markFiltersDirty();
    refreshFilterOptionsAfterDraftChange();
  });
});

document.querySelectorAll("[data-boiron-multi]").forEach((dropdown) => {
  const id = dropdown.dataset.boironMulti;
  qs(`${id}Toggle`)?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleSingleDropdown(`${id}Menu`, `${id}Search`);
  });
  qs(`${id}Menu`)?.addEventListener("click", (event) => {
    event.stopPropagation();
  });
  qs(`${id}Menu`)?.addEventListener("input", (event) => {
    if (event.target?.id !== `${id}Search`) return;
    renderBoironBrandFilterOptions(id, event.target.value || "");
    qs(`${id}Search`)?.focus();
  });
  qs(`${id}Menu`)?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.id === `${id}Search`) return;
    if (target.dataset.all === "true" && target.checked) {
      setSelectedBoironBrandValues(id, []);
    } else {
      const values = Array.from(qs(`${id}Menu`).querySelectorAll('input[type="checkbox"]:not([data-all])'))
        .filter((input) => input.checked)
        .map((input) => input.value)
        .filter(Boolean);
      setSelectedBoironBrandValues(id, values);
    }
    markFiltersDirty();
    refreshFilterOptionsAfterDraftChange();
  });
});

document.addEventListener("change", (event) => {
  const statusMenu = event.target.closest("#wbPromotionAdvertsStatusesMenu");
  if (!statusMenu || state.dashboard !== "admin") return;
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (target.dataset.all === "true" && target.checked) {
    setWbPromotionAdvertStatuses(wbPromotionAdvertStatusOptions.map((option) => option.value));
    return;
  }
  if (target.dataset.all === "true") {
    setWbPromotionAdvertStatuses(WB_PROMOTION_ADVERTS_DEFAULT_STATUSES);
    return;
  }
  const selected = new Set(selectedWbPromotionAdvertStatuses());
  if (target.checked) {
    selected.add(target.value);
  } else {
    selected.delete(target.value);
  }
  if (!selected.size) {
    target.checked = true;
    return;
  }
  setWbPromotionAdvertStatuses([...selected]);
});

qs("seoStatusToggle")?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleSingleDropdown("seoStatusMenu");
});

qs("collectionStatusToggle")?.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleSingleDropdown("collectionStatusMenu");
});

qs("seoStatusMenu")?.addEventListener("click", (event) => {
  event.stopPropagation();
});

qs("collectionStatusMenu")?.addEventListener("click", (event) => {
  event.stopPropagation();
});

qs("collectionStatusMenu")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (target.dataset.all === "true" && target.checked) {
    setSelectedCollectionStatuses([]);
  } else {
    const values = Array.from(qs("collectionStatusMenu").querySelectorAll('input[type="checkbox"]:not([data-all])'))
      .filter((input) => input.checked)
      .map((input) => input.value)
      .filter(Boolean);
    setSelectedCollectionStatuses(values);
  }
  markFiltersDirty();
  refreshFilterOptionsAfterDraftChange();
});

qs("seoStatusMenu")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  if (target.dataset.all === "true" && target.checked) {
    setSelectedSeoStatuses([]);
  } else {
    const values = Array.from(qs("seoStatusMenu").querySelectorAll('input[type="checkbox"]:not([data-all])'))
      .filter((input) => input.checked)
      .map((input) => input.value)
      .filter(Boolean);
    setSelectedSeoStatuses(values);
  }
  markFiltersDirty();
  refreshFilterOptionsAfterDraftChange();
});

document.addEventListener("click", async (event) => {
  const adminTopbarModeButton = event.target.closest(".admin-topbar-tabs [data-admin-mode]");
  if (adminTopbarModeButton && state.dashboard === "admin") {
    event.preventDefault();
    switchAdminModeFromUi(adminTopbarModeButton);
    return;
  }
  const weeklyExportButton = event.target.closest("[data-weekly-export]");
  if (weeklyExportButton) {
    try {
      weeklyExportButton.disabled = true;
      qs("status").textContent = "Готовлю Excel с живой диаграммой...";
      await exportWeeklyDynamicsToExcel(weeklyExportButton.dataset.weeklyExport || "trend");
      qs("status").textContent = "Excel с живой диаграммой сформирован";
    } catch (error) {
      setStatusError(error);
    } finally {
      weeklyExportButton.disabled = false;
    }
    return;
  }
  const chartExportButton = event.target.closest("[data-chart-export]");
  if (chartExportButton) {
    try {
      chartExportButton.disabled = true;
      qs("status").textContent = "Готовлю Excel с диаграммой...";
      await exportChartToExcel(chartExportButton.dataset.chartExport || "primary", chartExportButton);
      qs("status").textContent = "Excel с диаграммой сформирован";
    } catch (error) {
      setStatusError(error);
    } finally {
      chartExportButton.disabled = false;
    }
    return;
  }
  const boironGenerateButton = event.target.closest("[data-boiron-generate-note]");
  if (boironGenerateButton) {
    generateBoironAnalysisNote(boironGenerateButton);
    return;
  }
  if (!event.target.closest(".column-filter-popover") && !event.target.closest(".column-filter-btn")) {
    closeColumnFilterPopover();
  }
  if (!qs("categoryDropdown").contains(event.target)) {
    qs("categoryMenu").classList.add("hidden");
  }
  if (!qs("collectionStatusDropdown").contains(event.target)) {
    qs("collectionStatusMenu").classList.add("hidden");
  }
  if (!qs("seoStatusDropdown").contains(event.target)) {
    qs("seoStatusMenu").classList.add("hidden");
  }
  if (!qs("productDropdown").contains(event.target)) {
    qs("productMenu").classList.add("hidden");
  }
  document.querySelectorAll("[data-sportmaster-multi]").forEach((dropdown) => {
    if (!dropdown.contains(event.target)) qs(`${dropdown.dataset.sportmasterMulti}Menu`)?.classList.add("hidden");
  });
  document.querySelectorAll("[data-boiron-multi]").forEach((dropdown) => {
    if (!dropdown.contains(event.target)) qs(`${dropdown.dataset.boironMulti}Menu`)?.classList.add("hidden");
  });
  const wbPromotionStatusesDropdown = qs("wbPromotionAdvertsStatusesDropdown");
  if (wbPromotionStatusesDropdown && !wbPromotionStatusesDropdown.contains(event.target)) {
    qs("wbPromotionAdvertsStatusesMenu")?.classList.add("hidden");
  }
  if (!qs("dateRange").contains(event.target)) {
    qs("dateRangeMenu").classList.add("hidden");
  }
});

qs("dateRangeToggle").addEventListener("click", (event) => {
  event.stopPropagation();
  if (state.dashboard === "planfact") {
    renderPlanFactMonthPicker();
    toggleSingleDropdown("dateRangeMenu");
    return;
  }
  datePickerLeftMonth = null;
  draftDateFrom = qs("date_from").value || "";
  draftDateTo = qs("date_to").value || "";
  draftDateClickStep = 0;
  renderDateRangePicker();
  qs("dateRangeMenu").style.setProperty(
    "--date-range-menu-top",
    `${Math.min(window.innerHeight - 100, qs("dateRangeToggle").getBoundingClientRect().bottom + 6)}px`
  );
  toggleSingleDropdown("dateRangeMenu");
});

qs("dateRangeMenu").addEventListener("click", (event) => {
  event.stopPropagation();
  const planfactMonth = event.target.closest("[data-planfact-month]");
  if (planfactMonth && state.dashboard === "planfact") {
    setDateRange(planfactMonth.dataset.from, planfactMonth.dataset.to);
    qs("dateRangeMenu").classList.add("hidden");
    return;
  }
  const preset = event.target.closest("[data-preset]");
  if (preset) {
    applyDatePreset(preset.dataset.preset);
    return;
  }
  if (event.target.closest("#dateRangeApply")) {
    setDateRange(draftDateFrom, draftDateTo);
    qs("dateRangeMenu").classList.add("hidden");
    return;
  }
  const day = event.target.closest("[data-date]");
  if (day) handleCalendarDateClick(day.dataset.date);
});

qs("dateRangeMenu").addEventListener("change", (event) => {
  event.stopPropagation();
  const select = event.target;
  if (!select.classList.contains("calendar-month") && !select.classList.contains("calendar-year")) return;
  const side = select.dataset.side;
  const panelDate = side === "right" ? addMonths(datePickerLeftMonth, 1) : datePickerLeftMonth;
  const monthSelect = qs("dateRangeMenu").querySelector(`.calendar-month[data-side="${side}"]`);
  const yearSelect = qs("dateRangeMenu").querySelector(`.calendar-year[data-side="${side}"]`);
  const picked = new Date(Number(yearSelect.value), Number(monthSelect.value), 1);
  datePickerLeftMonth = side === "right" ? addMonths(picked, -1) : picked;
  renderDateRangePicker();
});

qs("productToggle").addEventListener("click", () => {
  toggleSingleDropdown("productMenu", "productSearch");
  if (state.dashboard === "product" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "seoMonitoring" || state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    loadFunnelProductOptions(qs("productSearch").value || "").catch((error) => {
      qs("productOptions").innerHTML = `<button class="product-option" type="button" data-value="">Ошибка: ${escapeHtml(error.message)}</button>`;
    });
  }
});

qs("productSearch").addEventListener("input", () => {
  const value = qs("productSearch").value;
  qs("product").value = value;
  qs("productToggle").textContent = value || "Все товары";
  if (state.dashboard === "product" || state.dashboard === "funnel" || state.dashboard === "weeklyDynamics" || state.dashboard === "inventoryHistory" || state.dashboard === "seoMonitoring" || state.dashboard === "wbSearchQueries" || state.dashboard === "wbEntrance") {
    loadFunnelProductOptions(value).catch((error) => {
      qs("productOptions").innerHTML = `<button class="product-option" type="button" data-value="">Ошибка: ${escapeHtml(error.message)}</button>`;
    });
  } else {
    renderProductOptions(value);
  }
  markFiltersDirty();
});

qs("productOptions").addEventListener("click", (event) => {
  const button = event.target.closest(".product-option");
  if (!button) return;
  setProductFilter(button.dataset.value || "");
  qs("productMenu").classList.add("hidden");
  markFiltersDirty();
  refreshFilterOptionsAfterDraftChange();
});

qs("primaryChartMetricSelect").addEventListener("change", () => {
  state.primaryChartMetric = normalizeChartMetricKey(qs("primaryChartMetricSelect").value);
  renderCurrentAbcCharts();
  persistDashboardState();
});

qs("secondaryChartMetricSelect").addEventListener("change", () => {
  state.secondaryChartMetric = normalizeChartMetricKey(qs("secondaryChartMetricSelect").value);
  renderCurrentAbcCharts();
  persistDashboardState();
});

qs("sort").addEventListener("change", () => {
  state.sortCol = (state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "planfact") ? "report_date" : selectedMetricField();
  resetPanelChartMetricsToSort();
  state.sortDir = "desc";
  state.page = 1;
  loadData().catch((error) => {
    setStatusError(error);
  });
});

qs("category_level").addEventListener("change", () => {
  state.page = 1;
  state.drillCategory = "";
  state.sortCol = selectedMetricField();
  state.sortDir = "desc";
  resetColumnFilters();
  loadData().catch((error) => {
    setStatusError(error);
  });
});

qs("marketplace").addEventListener("change", () => {
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = (state.dashboard === "adv" || state.dashboard === "mediaAdv" || state.dashboard === "funnel" || state.dashboard === "planfact") ? "report_date" : selectedMetricField();
  state.sortDir = "desc";
  resetColumnFilters();
  resetCategoryMenu();
  resetSportmasterFilters();
  resetBoironBrandFilters();
  resetMediaAdvFilters();
  clearAdvCampaignFilter();
  if (state.dashboard === "mediaAdv") {
    state.mediaAdvDateRangeInitialized = false;
    if (currentMarketplace() === "wb" && qs("media_level")) qs("media_level").value = qs("media_level").value || "campaign";
  }
  loadFilters({ preserveCategories: false })
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
});

qs("clientSelect")?.addEventListener("change", () => {
  if (state.clientLocked) return;
  state.client = currentClient();
  ensureDashboardSupportedForClient();
  persistDashboardState();
  resetClientScopedState();
  renderClientSelector();
  loadFilters({ preserveCategories: false })
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
});

function openSkuDashboard(category) {
  state.dashboard = "sku";
  state.drillCategory = category || "";
  state.page = 1;
  state.sortCol = "total_stock_qty";
  state.sortDir = "desc";
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openAbcDashboard() {
  state.dashboard = "abc";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = selectedMetricField();
  state.sortDir = "desc";
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openProductDashboard(category = "") {
  state.dashboard = "product";
  state.drillCategory = category || "";
  state.selectedCategories = category ? [category] : selectedValues("category");
  state.page = 1;
  state.sortCol = selectedMetricField();
  state.sortDir = "desc";
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openAdvDashboard() {
  state.dashboard = "adv";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openMediaAdvDashboard() {
  state.dashboard = "mediaAdv";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  resetColumnFilters();
  setMarketplaceValue("ozon");
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openFunnelDashboard() {
  state.dashboard = "funnel";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  resetColumnFilters();
  setMarketplaceValue("ozon");
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openInventoryHistoryDashboard() {
  state.dashboard = "inventoryHistory";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  state.funnelSelectedMetrics = [...inventoryHistoryDefaultMetrics];
  state.funnelMetricAxes = { ordered_units: "right", adv_orders: "right", organic_orders: "right" };
  state.funnelMetricTypes = { stock_inflow_qty: "bar", stock_outflow_qty: "bar" };
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openWeeklyDynamicsDashboard() {
  state.dashboard = "weeklyDynamics";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  resetColumnFilters();
  setMarketplaceValue("ozon");
  if (qs("period_group")) qs("period_group").value = "week";
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openSeoMonitoringDashboard() {
  state.dashboard = "seoMonitoring";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "zakazano_rub";
  state.sortDir = "desc";
  resetColumnFilters();
  loadFilters()
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openWbSearchQueriesDashboard() {
  state.dashboard = "wbSearchQueries";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "category_rank";
  state.sortDir = "asc";
  resetColumnFilters();
  if (qs("date_from")) qs("date_from").value = "";
  if (qs("date_to")) qs("date_to").value = "";
  if (qs("article")) qs("article").value = "";
  setProductFilter("");
  setMarketplaceValue("wb");
  loadFilters({ preserveCategories: false })
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openWbEntranceDashboard() {
  state.dashboard = "wbEntrance";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "ordered_units";
  state.sortDir = "desc";
  resetColumnFilters();
  if (qs("date_from")) qs("date_from").value = "";
  if (qs("date_to")) qs("date_to").value = "";
  if (qs("article")) qs("article").value = "";
  wbEntranceFilterIds.forEach((id) => { if (qs(id)) qs(id).value = ""; });
  setProductFilter("");
  setMarketplaceValue("wb");
  loadFilters({ preserveCategories: false })
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

function openFinanceDashboard(dashboard) {
  state.dashboard = dashboard;
  state.drillCategory = "";
  state.page = 1;
  if (!clientMarketplaceIds().includes(currentMarketplace())) setMarketplaceValue(clientMarketplaceIds()[0]);
  persistDashboardState();
  loadData().catch((error) => {
    setStatusError(error);
  });
}

function openAvitoDashboard(dashboard) {
  if (!avitoDashboards.has(dashboard)) return;
  state.dashboard = dashboard;
  state.drillCategory = "";
  state.page = 1;
  state.columnFilters = {};
  loadData().catch((error) => setStatusError(error, "Загрузка Avito Ads"));
}

function openYandexDashboard(dashboard) {
  if (!yandexDashboards.has(dashboard)) return;
  state.dashboard = dashboard;
  state.drillCategory = "";
  state.page = 1;
  state.columnFilters = {};
  loadData().catch((error) => setStatusError(error, "Загрузка отчёта Яндекс Маркета"));
}

function openPlanFactDashboard() {
  state.dashboard = "planfact";
  state.drillCategory = "";
  state.page = 1;
  state.sortCol = "report_date";
  state.sortDir = "asc";
  state.planfactDateRangeInitialized = false;
  if (qs("date_from")) qs("date_from").value = "";
  if (qs("date_to")) qs("date_to").value = "";
  resetColumnFilters();
  loadFilters({ preserveCategories: false })
    .then(loadData)
    .catch((error) => {
      setStatusError(error);
    });
}

async function openAdminDashboard() {
  if (!adminAccessEnabled()) return;
  state.adminLoginRequested = true;
  if (!(await checkAdminAuth())) {
    showAdminLogin(state.adminAuthConfigured === false ? "Авторизация админки пока не настроена." : "");
    return;
  }
  state.adminLoginRequested = false;
  enterAdminDashboard();
}

function toggleMobileNav(open) {
  const layout = document.querySelector(".layout");
  const toggle = qs("mobileNavToggle");
  if (!layout || !toggle) return;
  const shouldOpen = open === undefined ? !layout.classList.contains("nav-open") : Boolean(open);
  layout.classList.toggle("nav-open", shouldOpen);
  toggle.setAttribute("aria-expanded", String(shouldOpen));
}

function closeMobileNav() {
  toggleMobileNav(false);
}

qs("mobileNavToggle")?.addEventListener("click", () => toggleMobileNav());
qs("mobileNavOverlay")?.addEventListener("click", closeMobileNav);
syncInterfaceToggle();
qs("navReactUi")?.addEventListener("click", (event) => {
  event.preventDefault();
  persistDashboardState();
  window.location.href = oppositeInterfaceUrl();
});
document.querySelectorAll(".sidebar .nav-item").forEach((button) => {
  button.addEventListener("click", closeMobileNav);
});

qs("backToAbc").addEventListener("click", () => {
  openAbcDashboard();
});

qs("navAbc").addEventListener("click", () => {
  openAbcDashboard();
});

qs("navProduct").addEventListener("click", () => {
  openProductDashboard();
});

qs("navAdv").addEventListener("click", () => {
  openAdvDashboard();
});

qs("navMediaAdv").addEventListener("click", () => {
  openMediaAdvDashboard();
});

qs("navFunnel").addEventListener("click", () => {
  openFunnelDashboard();
});

qs("navWeeklyDynamics").addEventListener("click", () => {
  openWeeklyDynamicsDashboard();
});

qs("navInventoryHistory")?.addEventListener("click", () => {
  openInventoryHistoryDashboard();
});

qs("navSeoMonitoring").addEventListener("click", () => {
  state.seoProjectsNavSource = "monitoring";
  const url = new URL(window.location.href);
  url.searchParams.delete("seo_workspace");
  url.searchParams.delete("seo_project");
  url.searchParams.delete("seo_new");
  history.replaceState({}, "", url);
  openSeoMonitoringDashboard();
});

qs("navSeoBot")?.addEventListener("click", () => {
  state.seoProjectsNavSource = "semantics";
  const url = new URL(window.location.href);
  url.searchParams.set("seo_workspace", "generation");
  url.searchParams.delete("seo_project");
  url.searchParams.delete("seo_new");
  history.replaceState({}, "", url);
  openSeoMonitoringDashboard();
});

qs("navWbSearchQueries").addEventListener("click", () => {
  openWbSearchQueriesDashboard();
});

qs("navWbEntrance").addEventListener("click", () => {
  openWbEntranceDashboard();
});

qs("navAvitoOverview")?.addEventListener("click", () => openAvitoDashboard("avitoOverview"));
qs("navAvitoCampaigns")?.addEventListener("click", () => openAvitoDashboard("avitoCampaigns"));
qs("navAvitoGroups")?.addEventListener("click", () => openAvitoDashboard("avitoGroups"));
qs("navAvitoCreatives")?.addEventListener("click", () => openAvitoDashboard("avitoCreatives"));
qs("navAvitoDaily")?.addEventListener("click", () => openAvitoDashboard("avitoDaily"));
qs("navYandexOverview")?.addEventListener("click", () => openYandexDashboard("yandexOverview"));
qs("navYandexFunnel")?.addEventListener("click", () => openYandexDashboard("yandexFunnel"));
qs("navYandexFinance")?.addEventListener("click", () => openYandexDashboard("yandexFinance"));
qs("navYandexPromotion")?.addEventListener("click", () => openYandexDashboard("yandexPromotion"));
qs("navYandexInventory")?.addEventListener("click", () => openYandexDashboard("yandexInventory"));

qs("navPlanFact").addEventListener("click", () => {
  openPlanFactDashboard();
});

qs("navSalesPlanning")?.addEventListener("click", () => {
  openFinanceDashboard("salesPlanning");
});

qs("navMediaPlan")?.addEventListener("click", () => {
  openFinanceDashboard("mediaPlan");
});

qs("navProfitLoss").addEventListener("click", () => {
  openFinanceDashboard("profitLoss");
});

qs("navUnitEconomics").addEventListener("click", () => {
  openFinanceDashboard("unitEconomics");
});

qs("navSku").addEventListener("click", () => {
  const selected = selectedValues("category");
  openSkuDashboard(selected[0] || "");
});

document.querySelectorAll(".sidebar .nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    window.setTimeout(() => persistDashboardState(), 0);
  });
});

qs("navAdmin").addEventListener("click", () => {
  openAdminDashboard();
});

qs("adminLoginForm")?.addEventListener("submit", submitAdminLogin);
qs("adminLoginCancel")?.addEventListener("click", () => {
  state.adminLoginRequested = false;
  hideAdminLogin();
  if (state.dashboard === "admin") {
    const url = new URL(window.location.href);
    url.search = "";
    window.location.href = url.toString();
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-admin-user-form]");
  if (!form) return;
  event.preventDefault();
  saveAdminUserFromUi(form).catch((error) => {
    setStatusError(error);
  });
});

document.addEventListener("input", (event) => {
  const editedClientForm = event.target.closest("[data-admin-client-form]");
  if (editedClientForm) editedClientForm.dataset.adminClientDraftDirty = "true";
  const invalidCredential = event.target.closest('[data-admin-client-credential][aria-invalid="true"]');
  if (invalidCredential) {
    invalidCredential.removeAttribute("aria-invalid");
    const field = invalidCredential.closest(".admin-client-credential-field");
    field?.classList.remove("is-invalid");
    const fieldStatus = field?.querySelector(".admin-client-credential-status");
    if (fieldStatus?.dataset.validationOriginalStatus !== undefined) {
      fieldStatus.textContent = fieldStatus.dataset.validationOriginalStatus;
      delete fieldStatus.dataset.validationOriginalStatus;
    }
    const form = invalidCredential.closest("[data-admin-client-form]");
    if (form && !form.querySelector('[data-admin-client-credential][aria-invalid="true"]')) {
      const saveStatus = form.querySelector("[data-admin-client-save-status]");
      if (saveStatus) {
        saveStatus.textContent = "Проверьте изменения и нажмите «Сохранить клиента».";
        saveStatus.classList.remove("is-error");
      }
    }
    return;
  }
  const dbFilter = event.target.closest("[data-admin-db-filter]");
  if (dbFilter && state.dashboard === "admin" && dbFilter.dataset.adminDbFilter === "search") {
    updateAdminDatabaseFilter(
      "search",
      dbFilter.value,
      true,
      dbFilter.selectionStart,
      dbFilter.selectionEnd,
    );
    return;
  }
  const search = event.target.closest("[data-admin-permission-search]");
  if (!search) return;
  const dropdown = search.closest("[data-admin-permission-dropdown]");
  const query = String(search.value || "").trim().toLowerCase();
  const options = [...dropdown.querySelectorAll("[data-admin-permission-option]")];
  options.forEach((option) => {
    const text = option.dataset.searchText || option.textContent.toLowerCase();
    option.hidden = Boolean(query && !text.includes(query));
  });
  const empty = dropdown.querySelector("[data-admin-permission-empty]");
  if (empty) empty.hidden = options.some((option) => !option.hidden);
});

document.addEventListener("change", (event) => {
  const selectionMaster = event.target.closest("[data-all-clients-selection-master]");
  if (selectionMaster) {
    adminAllClientsMasterTargets(selectionMaster).forEach((checkbox) => {
      checkbox.checked = selectionMaster.checked;
      state.adminAllClientsTaskSelection[checkbox.dataset.selectionKey] = checkbox.checked;
    });
    syncAdminAllClientsSelectionMasters(selectionMaster.closest(".admin-all-clients-workspace") || document);
    return;
  }
  const stageCheckbox = event.target.closest("[data-all-clients-task-select]");
  if (stageCheckbox) {
    state.adminAllClientsTaskSelection[stageCheckbox.dataset.selectionKey] = stageCheckbox.checked;
    syncAdminAllClientsSelectionMasters(stageCheckbox.closest(".admin-all-clients-workspace") || document);
    return;
  }
  const dbFilter = event.target.closest("[data-admin-db-filter]");
  if (dbFilter && state.dashboard === "admin") {
    updateAdminDatabaseFilter(dbFilter.dataset.adminDbFilter || "", dbFilter.value);
    return;
  }
  const dropdown = event.target.closest("[data-admin-permission-dropdown]");
  if (!dropdown) return;
  const selectAll = event.target.closest("[data-admin-permission-all]");
  if (selectAll) {
    const visibleInputs = [...dropdown.querySelectorAll("[data-admin-permission-option]:not([hidden]) input")];
    visibleInputs.forEach((input) => {
      input.checked = selectAll.checked;
      input.closest("[data-admin-permission-option]")?.classList.toggle("is-on", input.checked);
    });
  } else {
    const option = event.target.closest("[data-admin-permission-option]");
    if (option) option.classList.toggle("is-on", event.target.checked);
  }
  syncAdminPermissionDropdowns(dropdown);
});

function syncAdminPermissionDropdowns(root = document) {
  root.querySelectorAll("[data-admin-permission-dropdown]").forEach((dropdown) => {
    const options = [...dropdown.querySelectorAll("[data-admin-permission-option]")];
    const checked = options.filter((option) => option.querySelector("input")?.checked);
    options.forEach((option) => option.classList.toggle("is-on", option.querySelector("input")?.checked));
    const summary = dropdown.querySelector("[data-admin-permission-summary]");
    if (summary) summary.textContent = adminPermissionSummary(checked.length, options.length);
    const all = dropdown.querySelector("[data-admin-permission-all]");
    if (all) {
      all.checked = options.length > 0 && checked.length === options.length;
      all.indeterminate = checked.length > 0 && checked.length < options.length;
    }
  });
}
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-admin-logout]")) return;
  logoutAdmin().catch((error) => {
    setStatusError(error);
  });
});

qs("prevPage").addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadData().catch((error) => {
      setStatusError(error);
    });
  }
});

qs("nextPage").addEventListener("click", () => {
  if (state.page < state.totalPages) {
    state.page += 1;
    loadData().catch((error) => {
      setStatusError(error);
    });
  }
});

qs("refresh")?.addEventListener("click", () => {
  loadData().catch((error) => {
    setStatusError(error);
  });
});

qs("skuCardBack")?.addEventListener("click", closeSkuCard);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !qs("skuCardPage")?.classList.contains("hidden")) closeSkuCard();
  if (event.key === "Escape") closeMobileNav();
});

qs("exportExcel")?.addEventListener("click", () => {
  exportReportToExcel(qs("exportExcel")).catch((error) => {
    setStatusError(error, "Экспорт отчета");
  });
});
qs("exportTableExcel")?.addEventListener("click", exportTableToExcel);
qs("applyFilters")?.addEventListener("click", () => {
  applyPendingFilters().catch((error) => {
    setStatusError(error);
  });
});
qs("resetFilters")?.addEventListener("click", () => {
  resetAllFilters().catch((error) => {
    setStatusError(error);
  });
});
qs("resetCampaignTableFilters")?.addEventListener("click", () => {
  resetAllFilters().catch((error) => {
    setStatusError(error);
  });
});

boot();
