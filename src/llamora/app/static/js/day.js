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

export function navigateToDate(dateStr) {
  if (typeof dateStr !== "string" || !dateStr) {
    return false;
  }

  const targetId = "#content-wrapper";
  if (window.htmx) {
    window.htmx.ajax("GET", `/c/${dateStr}`, {
      target: targetId,
      swap: "outerHTML",
      pushUrl: `/d/${dateStr}`,
    });
    return true;
  }

  window.location.assign(`/d/${dateStr}`);
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
export function initDayNav(chat, options = {}) {
  const prevBtn = document.getElementById("prev-day");
  const nextBtn = document.getElementById("next-day");
  if (!prevBtn || !nextBtn) return;

  const activeDaySource =
    options.activeDay ||
    chat?.dataset?.date ||
    document.body?.dataset?.activeDay ||
    "";

  const parsed = parseDateFromSource(activeDaySource);
  const currentDate = parsed?.date ?? null;

  const labelNode = document.getElementById("calendar-label");
  if (labelNode) {
    const labelText =
      options.label ||
      chat?.dataset?.longDate ||
      document.body?.dataset?.activeDayLabel ||
      (currentDate ? formatLongDate(currentDate) : activeDaySource);
    if (typeof labelText === "string") {
      const previousLabel = labelNode.textContent;
      labelNode.textContent = labelText;
      if (previousLabel !== labelText) {
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
}
