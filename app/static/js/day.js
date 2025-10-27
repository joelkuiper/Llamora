const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

function parseIsoDate(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const [datePart] = trimmed.split(/[T\s]/, 1);
  const match = ISO_DATE_RE.exec(datePart);
  if (!match) return null;

  const year = Number.parseInt(match[1], 10);
  const month = Number.parseInt(match[2], 10);
  const day = Number.parseInt(match[3], 10);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null;
  }

  const date = new Date(year, month - 1, day);
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null;
  }

  date.setHours(0, 0, 0, 0);
  return { date, year, month, day };
}

function formatIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

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

function formatLongDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "";
  }
  const day = date.getDate();
  const month = date.toLocaleDateString(undefined, { month: "long" });
  const year = date.getFullYear();
  return `${day}${ordinalSuffix(day)} of ${month} ${year}`;
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

  const parsed = parseIsoDate(activeDaySource);
  const currentDate = parsed?.date ?? null;

  const labelNode = document.getElementById("calendar-label");
  if (labelNode) {
    const labelText =
      options.label ||
      chat?.dataset?.longDate ||
      document.body?.dataset?.activeDayLabel ||
      (currentDate ? formatLongDate(currentDate) : activeDaySource);
    if (typeof labelText === "string") {
      labelNode.textContent = labelText;
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
    onClick: () => navigate(prevIso),
  });

  updateNavButton(nextBtn, {
    disabled: nextDate > today,
    tooltip: nextDate > today ? null : "Next day",
    onClick: nextDate > today ? null : () => navigate(nextIso),
  });

  function navigate(dateStr) {
    htmx.ajax("GET", `/c/${dateStr}`, {
      target: "#content-wrapper",
      swap: "outerHTML",
      pushUrl: `/d/${dateStr}`,
    });
  }
}
