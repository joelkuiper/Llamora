import { buildTimezoneQueryParam, getTimezone } from "./utils/timezone-service.js";
import {
  ACTIVE_DAY_CHANGED_EVENT,
  getActiveDay,
  getActiveDayLabel,
} from "./chat/active-day-store.js";

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

const LABEL_FLASH_CLASS = "text-glow-flash";
let navListenerRegistered = false;

function triggerLabelFlash(node) {
  if (!node) return;
  node.classList.remove(LABEL_FLASH_CLASS);
  // Force reflow so the animation can replay when the class is re-added.
  void node.offsetWidth; // eslint-disable-line no-void
  node.classList.add(LABEL_FLASH_CLASS);
}

function formatLongDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "";
  }
  const day = date.getDate();
  const month = date.toLocaleDateString(undefined, { month: "long" });
  const year = date.getFullYear();
  return `${day}${ordinalSuffix(day)} of ${month} ${year}`;
}

export function formatIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function parseDateFromSource(value) {
  if (typeof value !== "string") return null;
  const parts = value.split("-");
  if (parts.length !== 3) return null;
  const [y, m, d] = parts.map((part) => Number.parseInt(part, 10));
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) {
    return null;
  }
  const date = new Date(y, m - 1, d);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  date.setHours(0, 0, 0, 0);
  if (
    date.getFullYear() !== y ||
    date.getMonth() !== m - 1 ||
    date.getDate() !== d
  ) {
    return null;
  }
  return { date, year: y, month: m, day: d };
}

export function navigateToDate(dateStr, options = {}) {
  if (typeof dateStr !== "string" || !dateStr) {
    return false;
  }

  const params = new URLSearchParams(buildTimezoneQueryParam(getTimezone()));
  const scrollTarget =
    typeof options?.targetId === "string" ? options.targetId.trim() : "";

  if (scrollTarget) {
    params.set("target", scrollTarget);
  }

  const query = params.toString();
  const htmxUrl = `/c/${dateStr}?${query}`;
  const pushUrl = `/d/${dateStr}?${query}`;

  const contentTarget = "#content-wrapper";
  if (window.htmx) {
    window.htmx.ajax("GET", htmxUrl, {
      target: contentTarget,
      swap: "outerHTML",
      pushUrl,
    });
    return true;
  }

  window.location.assign(pushUrl);
  return true;
}

function updateNavButton(button, { disabled, tooltip, onClick }) {
  if (!button) return;
  button.disabled = Boolean(disabled);
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

const applyDayStateToNav = ({ activeDay, label, forceFlash = false }) => {
  const elements = resolveNavElements();
  if (!elements) return;

  const { prevBtn, nextBtn, labelNode } = elements;

  const activeDaySource = typeof activeDay === "string" ? activeDay : "";
  const parsed = parseDateFromSource(activeDaySource);
  const currentDate = parsed?.date ?? null;

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
  const activeDay =
    typeof detail.activeDay === "string" ? detail.activeDay : getActiveDay();
  const label =
    typeof detail.activeDayLabel === "string"
      ? detail.activeDayLabel
      : getActiveDayLabel();
  const forceFlash = Boolean(detail.forceFlash);
  applyDayStateToNav({ activeDay, label, forceFlash });
};

export function initDayNav(chat, options = {}) {
  const { forceFlash = false, activeDay, label } = options;
  const currentDay =
    activeDay || getActiveDay() || chat?.dataset?.date || "";
  const currentLabel =
    label || getActiveDayLabel() || chat?.dataset?.longDate || null;

  applyDayStateToNav({
    activeDay: currentDay,
    label: currentLabel,
    forceFlash,
  });

  if (!navListenerRegistered) {
    document.addEventListener(
      ACTIVE_DAY_CHANGED_EVENT,
      handleActiveDayChange
    );
    navListenerRegistered = true;
  }
}
