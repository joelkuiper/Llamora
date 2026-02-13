import {
  applyTimezoneSearchParam,
  formatIsoDate,
  getTimezone,
  parseDateFromSource,
} from "./services/datetime.js";

export { formatIsoDate, parseDateFromSource } from "./services/datetime.js";

import {
  ACTIVE_DAY_CHANGED_EVENT,
  getActiveDay,
  getActiveDayLabel,
} from "./entries/active-day-store.js";
import { getCurrentView } from "./lifecycle.js";
import { updateClientToday } from "./services/time.js";
import { triggerLabelFlash } from "./utils/motion.js";

function ordinalSuffix(day) {
  if (!Number.isFinite(day)) return "";
  const mod100 = day % 100;
  if (mod100 >= 11 && mod100 <= 13) {
    return "th";
  }
  switch (day % 10) {
    case 1:
      return "st";
    case 2:
      return "nd";
    case 3:
      return "rd";
    default:
      return "th";
  }
}

let navListenerRegistered = false;
let viewChangeListenerRegistered = false;

function formatLongDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "";
  }
  const day = date.getDate();
  const month = date.toLocaleDateString(undefined, { month: "long" });
  const year = date.getFullYear();
  return `${day}${ordinalSuffix(day)} of ${month} ${year}`;
}

export function navigateToDate(dateStr) {
  if (typeof dateStr !== "string" || !dateStr) {
    return false;
  }

  const zone = getTimezone();
  const params = new URLSearchParams();
  applyTimezoneSearchParam(params, zone);
  const today = updateClientToday();
  if (today) {
    params.set("client_today", today);
  }
  const query = params.toString();
  const htmxUrl = `/e/${dateStr}?${query}`;
  const pushUrl = `/d/${dateStr}?${query}`;

  const targetId = "#content-wrapper";
  if (window.htmx) {
    const source =
      document.getElementById("calendar-control") || document.body || document.documentElement;
    const request = window.htmx.ajax("GET", htmxUrl, {
      target: targetId,
      swap: "outerHTML",
      pushUrl,
      source,
    });
    if (request && typeof request.catch === "function") {
      request.catch(() => {});
    }
    return true;
  }

  window.location.assign(pushUrl);
  return true;
}

function updateNavButton(button, { disabled, tooltip, onClick }) {
  if (!button) return;
  button.disabled = Boolean(disabled);
  if (button.disabled) {
    button.setAttribute("aria-disabled", "true");
  } else {
    button.removeAttribute("aria-disabled");
  }
  if (button.disabled) {
    button.removeAttribute("data-tooltip-title");
    button.onclick = null;
    return;
  }

  if (tooltip) {
    button.dataset.tooltipTitle = tooltip;
  } else {
    button.removeAttribute("data-tooltip-title");
  }

  button.onclick = typeof onClick === "function" ? onClick : null;
}

/* Initialize previous/next day navigation buttons */
const resolveNavElements = () => {
  const prevBtn = document.getElementById("prev-day");
  const nextBtn = document.getElementById("next-day");
  const labelNode = document.getElementById("calendar-label");
  if (!prevBtn || !nextBtn) {
    return null;
  }
  return { prevBtn, nextBtn, labelNode };
};

const getMinDateFromDom = () => {
  const source =
    document?.querySelector?.("#entries")?.dataset?.minDate ||
    document?.body?.dataset?.minDate ||
    "";
  const parsed = parseDateFromSource(source);
  return parsed?.date ?? null;
};

const syncNavMinDate = () => {
  const minDate = getMinDateFromDom();
  if (minDate) {
    document.body.dataset.minDate = formatIsoDate(minDate);
  }
  return minDate;
};

function setNavDisabledForView(isDiary) {
  const elements = resolveNavElements();
  if (!elements) return;
  const { prevBtn, nextBtn } = elements;
  const calBtn = document.getElementById("calendar-btn");

  if (!isDiary) {
    updateNavButton(prevBtn, { disabled: true });
    updateNavButton(nextBtn, { disabled: true });
    if (calBtn) {
      calBtn.disabled = true;
      calBtn.setAttribute("aria-disabled", "true");
      calBtn.removeAttribute("data-tooltip-title");
    }
  } else {
    if (calBtn) {
      calBtn.disabled = false;
      calBtn.removeAttribute("aria-disabled");
      calBtn.dataset.tooltipTitle = "Change day";
    }
    applyDayStateToNav({ activeDay: getActiveDay(), label: getActiveDayLabel() });
  }
}

function ensureViewSyncListener() {
  if (viewChangeListenerRegistered) {
    return;
  }

  document.addEventListener("app:view-changed", (event) => {
    setNavDisabledForView(event.detail?.view === "diary");
  });

  document.addEventListener("app:rehydrate", () => {
    const view = document.getElementById("main-content")?.dataset?.view || "diary";
    setNavDisabledForView(view === "diary");
  });

  viewChangeListenerRegistered = true;
}

const applyDayStateToNav = ({ activeDay, label, forceFlash = false }) => {
  if (getCurrentView() !== "diary") return;

  const elements = resolveNavElements();
  if (!elements) return;

  const { prevBtn, nextBtn, labelNode } = elements;

  const activeDaySource = typeof activeDay === "string" ? activeDay : "";
  const parsed = parseDateFromSource(activeDaySource);
  const currentDate = parsed?.date ?? null;
  const minDate = syncNavMinDate();
  const isFirstDay = Boolean(currentDate && minDate && currentDate.getTime() === minDate.getTime());

  if (labelNode) {
    const labelText =
      typeof label === "string" && label
        ? label
        : currentDate
          ? formatLongDate(currentDate)
          : activeDaySource;
    if (typeof labelText === "string") {
      const previousLabel = labelNode.textContent;
      labelNode.textContent = labelText;
      if (previousLabel !== labelText || forceFlash) {
        triggerLabelFlash(labelNode);
      }
    }
  }

  if (!currentDate) {
    updateNavButton(prevBtn, { disabled: true });
    updateNavButton(nextBtn, { disabled: true });
    return;
  }

  if (isFirstDay) {
    prevBtn.classList.add("is-hidden");
    prevBtn.setAttribute("aria-hidden", "true");
  } else {
    prevBtn.classList.remove("is-hidden");
    prevBtn.removeAttribute("aria-hidden");
  }

  const prevDate = new Date(currentDate);
  prevDate.setDate(currentDate.getDate() - 1);
  const nextDate = new Date(currentDate);
  nextDate.setDate(currentDate.getDate() + 1);

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);

  const prevIso = formatIsoDate(prevDate);
  const nextIso = formatIsoDate(nextDate);
  const yesterdayIso = formatIsoDate(yesterday);

  updateNavButton(prevBtn, {
    disabled: false,
    tooltip: prevIso === yesterdayIso ? "Yesterday" : "Previous day",
    onClick: () => navigateToDate(prevIso),
  });

  updateNavButton(nextBtn, {
    disabled: nextDate > today,
    tooltip: nextDate > today ? null : "Next day",
    onClick: nextDate > today ? null : () => navigateToDate(nextIso),
  });
};

const handleActiveDayChange = (event) => {
  const detail = event?.detail || {};
  const activeDay = typeof detail.activeDay === "string" ? detail.activeDay : getActiveDay();
  const label =
    typeof detail.activeDayLabel === "string" ? detail.activeDayLabel : getActiveDayLabel();
  const forceFlash = Boolean(detail.forceFlash);
  applyDayStateToNav({ activeDay, label, forceFlash });
};

export function initDayNav(entries, options = {}) {
  const { forceFlash = false, activeDay, label } = options;
  const currentDay = activeDay || getActiveDay() || entries?.dataset?.date || "";
  const currentLabel = label || getActiveDayLabel() || entries?.dataset?.longDate || null;

  applyDayStateToNav({
    activeDay: currentDay,
    label: currentLabel,
    forceFlash,
  });

  if (!navListenerRegistered) {
    document.addEventListener(ACTIVE_DAY_CHANGED_EVENT, handleActiveDayChange);
    navListenerRegistered = true;
  }

  ensureViewSyncListener();
}

ensureViewSyncListener();
