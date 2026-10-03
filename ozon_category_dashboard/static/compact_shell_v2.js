(function () {
  "use strict";

  const CLASS_NAME = "pfm-compact-sidebar-active";
  const ICON_VERSION = "pulse-rail-v1";
  const TOOLTIP_DELAY_MS = 90;
  const TOOLTIP_ID = "pfm-nav-tooltip";
  let tooltipTimer = 0;
  let tooltipOwner = null;
  const shortLabels = {
    navPortfolio: "Общий",
    navClientReview: "Ревью",
    navPlanFact: "План/факт",
    navSalesPlanning: "План",
    navMediaPlan: "Медиаплан",
    navProfitLoss: "P&L",
    navUnitEconomics: "Юнит",
    navReviews: "Отзывы",
    navAdv: "Товарная",
    navMediaAdv: "Медийная",
    navFunnel: "Воронка",
    navWeeklyDynamics: "Недели",
    navInventoryHistory: "Запасы",
    navCommercialRadar: "Health",
    navHealthCheck: "Health",
    navAssortmentDevelopment: "Ассорт.",
    navSeoMonitoring: "SEO",
    navWbSearchQueries: "Запросы",
    navWbEntrance: "Входы WB",
    navAvitoOverview: "Avito",
    navAvitoCampaigns: "Кампании",
    navAvitoGroups: "Группы",
    navAvitoCreatives: "Креативы",
    navAvitoDaily: "По дням",
    navAbc: "ABC кат.",
    navProduct: "ABC SKU",
    navSku: "Скоринг",
    navAdmin: "Админ",
  };
  const iconPaths = {
    navPortfolio: '<rect x="3" y="4" width="7" height="7" rx="2"/><rect x="14" y="4" width="7" height="7" rx="2"/><rect x="3" y="15" width="7" height="6" rx="2"/><rect x="14" y="15" width="7" height="6" rx="2"/>',
    navClientReview: '<circle cx="12" cy="8" r="3"/><path d="M5 20c.7-4 3-6 7-6s6.3 2 7 6"/>',
    navPlanFact: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navKonstexProfitLoss: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navProfitLoss: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navKonstexUnitEconomics: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navUnitEconomics: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navSalesPlanning: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h3M13 14h3"/>',
    navMediaPlan: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h3M13 14h3"/>',
    navReviews: '<path d="M4 5h16v11H9l-5 4V5Z"/><path d="M8 9h8M8 12h5"/>',
    navAdv: '<path d="m4 13 11-5v8L4 11v2Zm11-3 4-2v8l-4-2M6 13l1 6h4l-2-5"/>',
    navMediaAdv: '<path d="m4 13 11-5v8L4 11v2Zm11-3 4-2v8l-4-2M6 13l1 6h4l-2-5"/>',
    navWbAdSearchQueries: '<path d="m4 13 11-5v8L4 11v2Zm11-3 4-2v8l-4-2M6 13l1 6h4l-2-5"/>',
    navFunnel: '<path d="M4 5h16l-6 7v5l-4 2v-7L4 5Z"/>',
    navWbEntrance: '<path d="M4 5h16l-6 7v5l-4 2v-7L4 5Z"/>',
    navAvitoOverview: '<path d="M4 19V9M10 19V5M16 19v-7M3 19h18"/>',
    navAvitoCampaigns: '<path d="m4 13 11-5v8L4 11v2Zm11-3 4-2v8l-4-2M6 13l1 6h4l-2-5"/>',
    navAvitoGroups: '<circle cx="8" cy="8" r="3"/><circle cx="16" cy="8" r="3"/><path d="M3 20c.5-4 2-6 5-6s4.5 2 5 6M11 20c.5-4 2-6 5-6s4.5 2 5 6"/>',
    navAvitoCreatives: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="m7 16 4-4 3 3 3-4 3 4M8 8h.01"/>',
    navAvitoDaily: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h3M13 14h3"/>',
    navWeeklyDynamics: '<path d="m4 16 4-5 4 3 6-8M18 6h-4M18 6v4"/>',
    navInventoryHistory: '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8M12 12v8"/>',
    navAssortmentDevelopment: '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8M18 15v6M15 18h6"/>',
    navKonstexAssortmentDevelopment: '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8M18 15v6M15 18h6"/>',
    navCommercialRadar: '<path d="M12 21s-8-4.6-8-11a4 4 0 0 1 7-2.6L12 9l1-1.6A4 4 0 0 1 20 10c0 6.4-8 11-8 11Z"/><path d="M7 13h3l1-3 2 6 1-3h3"/>',
    navHealthCheck: '<path d="M12 21s-8-4.6-8-11a4 4 0 0 1 7-2.6L12 9l1-1.6A4 4 0 0 1 20 10c0 6.4-8 11-8 11Z"/><path d="M7 13h3l1-3 2 6 1-3h3"/>',
    navSeoMonitoring: '<circle cx="10" cy="10" r="5"/><path d="m14 14 6 6M4 19h7"/>',
    navWbSearchQueries: '<circle cx="10" cy="10" r="5"/><path d="m14 14 6 6M4 19h7"/>',
    navAbc: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    navProduct: '<path d="m4 8 8-4 8 4-8 4-8-4Zm0 0v8l8 4 8-4V8"/>',
    navSku: '<path d="M5 5v14M8 5v14M12 5v14M15 5v14M19 5v14"/>',
    navAdmin: '<path d="M12 3a3 3 0 0 0-3 3v1H7a3 3 0 0 0-3 3v6a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-6a3 3 0 0 0-3-3h-2V6a3 3 0 0 0-3-3Z"/><path d="M9 7V6a3 3 0 0 1 6 0v1M9 13h6"/>',
  };

  function svgFor(id) {
    const path = iconPaths[id] || '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 9v12"/>';
    return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" data-icon-version="${ICON_VERSION}">${path}</svg>`;
  }

  function decorateItem(item) {
    if (!item) return;
    const currentIcon = item.querySelector(":scope > .pfm-nav-icon");
    if (item.dataset.pfmIconVersion === ICON_VERSION && currentIcon) return;
    const label = String(
      item.dataset.compactLabel || item.getAttribute("aria-label") || item.textContent || ""
    ).trim();
    if (!label) return;
    const visibleLabel = shortLabels[item.id] || label;
    item.dataset.compactLabel = label;
    item.dataset.pfmIconVersion = ICON_VERSION;
    item.removeAttribute("title");
    item.setAttribute("aria-label", label);
    item.replaceChildren();
    const icon = document.createElement("span");
    icon.className = "pfm-nav-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = svgFor(item.id);
    const text = document.createElement("span");
    text.className = "pfm-nav-label";
    text.textContent = visibleLabel;
    item.append(icon, text);
  }

  function decorateSwitch() {
    const link = document.getElementById("navReactUi");
    if (!link) return;
    const targetUi = link.dataset.targetUi
      || (String(link.getAttribute("aria-label") || link.textContent || "").toLowerCase().includes("легаси") ? "legacy" : "react");
    const visibleLabel = targetUi === "legacy" ? "Легаси" : "React";
    const accessibleLabel = link.getAttribute("aria-label")
      || (targetUi === "legacy" ? "Перейти в легаси-интерфейс" : "Перейти в React-интерфейс");
    const currentIcon = link.querySelector(":scope > .pfm-ui-switch-icon");
    link.dataset.compactLabel = accessibleLabel;
    link.classList.add("pfm-ui-switch-link");
    link.dataset.pfmIconVersion = ICON_VERSION;
    link.setAttribute("title", accessibleLabel);
    link.setAttribute("aria-label", accessibleLabel);
    if (currentIcon) {
      const text = link.querySelector(":scope > .pfm-ui-switch-label");
      if (text && text.textContent !== visibleLabel) text.textContent = visibleLabel;
      return;
    }
    link.replaceChildren();
    const icon = document.createElement("span");
    icon.className = "pfm-ui-switch-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="13" width="8" height="7" rx="1.5"/><path d="M13 7h6l-2-2M11 17H5l2 2"/></svg>';
    const text = document.createElement("span");
    text.className = "pfm-ui-switch-label";
    text.textContent = visibleLabel;
    link.append(icon, text);
  }

  function tooltipElement() {
    let tooltip = document.getElementById(TOOLTIP_ID);
    if (tooltip) return tooltip;
    tooltip = document.createElement("div");
    tooltip.id = TOOLTIP_ID;
    tooltip.className = "pfm-nav-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.setAttribute("aria-hidden", "true");
    tooltip.dataset.visible = "false";
    document.body.append(tooltip);
    return tooltip;
  }

  function hideTooltip() {
    window.clearTimeout(tooltipTimer);
    tooltipTimer = 0;
    const tooltip = document.getElementById(TOOLTIP_ID);
    if (tooltip) {
      tooltip.dataset.visible = "false";
      tooltip.setAttribute("aria-hidden", "true");
    }
    tooltipOwner?.removeAttribute("aria-describedby");
    tooltipOwner = null;
  }

  function showTooltip(owner) {
    if (!owner?.isConnected || window.innerWidth < 901) return;
    const label = String(owner.dataset.compactLabel || owner.getAttribute("aria-label") || "").trim();
    if (!label) return;
    const tooltip = tooltipElement();
    const rect = owner.getBoundingClientRect();
    tooltipOwner?.removeAttribute("aria-describedby");
    tooltipOwner = owner;
    tooltip.textContent = label;
    tooltip.style.left = `${Math.round(rect.right + 11)}px`;
    tooltip.style.top = `${Math.round(Math.max(18, Math.min(window.innerHeight - 18, rect.top + rect.height / 2)))}px`;
    tooltip.setAttribute("aria-hidden", "false");
    owner.setAttribute("aria-describedby", TOOLTIP_ID);
    window.requestAnimationFrame(() => { tooltip.dataset.visible = "true"; });
  }

  function scheduleTooltip(owner) {
    window.clearTimeout(tooltipTimer);
    tooltipTimer = window.setTimeout(() => showTooltip(owner), TOOLTIP_DELAY_MS);
  }

  function tooltipTarget(target) {
    return target instanceof Element
      ? target.closest(".sidebar .nav-item, .sidebar .pfm-ui-switch-link")
      : null;
  }

  function bindSidebarTooltip(sidebar) {
    if (!sidebar || sidebar.dataset.pfmTooltipBound === "true") return;
    sidebar.dataset.pfmTooltipBound = "true";
    sidebar.addEventListener("pointerover", (event) => {
      const owner = tooltipTarget(event.target);
      if (!owner || owner.contains(event.relatedTarget)) return;
      scheduleTooltip(owner);
    });
    sidebar.addEventListener("pointerout", (event) => {
      const owner = tooltipTarget(event.target);
      if (!owner || owner.contains(event.relatedTarget)) return;
      hideTooltip();
    });
    sidebar.addEventListener("focusin", (event) => {
      const owner = tooltipTarget(event.target);
      if (owner) scheduleTooltip(owner);
    });
    sidebar.addEventListener("focusout", hideTooltip);
    sidebar.addEventListener("scroll", hideTooltip, { passive: true });
    window.addEventListener("resize", hideTooltip, { passive: true });
  }

  function enforce() {
    document.body?.classList.add(CLASS_NAME);
    document.querySelector(".filters")?.classList.add("compact-filters");
    document.querySelectorAll(".sidebar .nav-item").forEach(decorateItem);
    decorateSwitch();
    bindSidebarTooltip(document.querySelector(".sidebar"));
    const apply = document.getElementById("applyFilters");
    const reset = document.getElementById("resetFilters");
    if (apply) {
      apply.title = "Применить фильтры";
      apply.setAttribute("aria-label", "Применить фильтры");
    }
    if (reset) {
      reset.title = "Сбросить фильтры";
      reset.setAttribute("aria-label", "Сбросить фильтры");
    }
  }

  enforce();
  const sidebar = document.querySelector(".sidebar");
  if (sidebar) {
    new MutationObserver(enforce).observe(sidebar, { childList: true, subtree: true });
  }
  const filters = document.querySelector(".filters");
  if (filters) {
    new MutationObserver(() => {
      if (!filters.classList.contains("compact-filters")) enforce();
    }).observe(filters, { attributes: true, attributeFilter: ["class"] });
  }
  window.addEventListener("pageshow", enforce);
  window.addEventListener("popstate", enforce);
})();
