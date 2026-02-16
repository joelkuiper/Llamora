import { formatIsoDate, navigateToDate, parseDateFromSource } from "./day.js";
import { getActiveDay } from "./components/entries-view/active-day-store.js";
import { requestScrollForceEdge } from "./scroll-manager.js";
import { registerShortcut } from "./utils/global-shortcuts.js";
import { motionSafeBehavior } from "./utils/motion.js";

let registered = false;

const focusEntryComposer = () => {
  const textarea = document.querySelector("entry-form textarea");
  if (!(textarea instanceof HTMLTextAreaElement) || textarea.disabled) {
    return false;
  }

  textarea.focus({ preventScroll: true });
  const { value } = textarea;
  const caret = typeof value === "string" ? value.length : 0;
  try {
    textarea.setSelectionRange(caret, caret);
  } catch (_err) {
    /* no-op */
  }

  if (typeof textarea.scrollIntoView === "function") {
    requestAnimationFrame(() => {
      textarea.scrollIntoView({
        block: "center",
        behavior: motionSafeBehavior("smooth"),
      });
    });
  }

  return true;
};

const clickIfEnabled = (selector) => {
  const el = document.querySelector(selector);
  if (!(el instanceof HTMLElement)) {
    return false;
  }
  if (typeof el.matches === "function" && el.matches(":disabled")) {
    return false;
  }
  if ("disabled" in el && el.disabled) {
    return false;
  }
  el.focus({ preventScroll: true });
  el.click();
  return true;
};

const getActiveDate = () => {
  const source = getActiveDay() || "";
  const parsed = parseDateFromSource(source);
  return parsed?.date ?? null;
};

const goToPreviousDay = () => {
  if (clickIfEnabled("#prev-day")) {
    return true;
  }
  const active = getActiveDate();
  if (!active) {
    return false;
  }
  const prev = new Date(active);
  prev.setDate(active.getDate() - 1);
  navigateToDate(formatIsoDate(prev));
  return true;
};

const goToNextDay = () => {
  if (clickIfEnabled("#next-day")) {
    return true;
  }
  const active = getActiveDate();
  if (!active) {
    return false;
  }
  const next = new Date(active);
  next.setDate(active.getDate() + 1);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (next > today) {
    return false;
  }
  navigateToDate(formatIsoDate(next));
  return true;
};

const goToToday = () => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayIso = formatIsoDate(today);
  const active = getActiveDate();
  if (active && active.getTime() === today.getTime()) {
    return focusEntryComposer();
  }

  const returnLink = document.querySelector(".return-today a");
  if (returnLink instanceof HTMLElement) {
    returnLink.click();
    return true;
  }

  navigateToDate(todayIso);
  return true;
};

const toggleCalendar = () => clickIfEnabled("#calendar-btn");

const scrollEntriesToBottom = () => {
  requestScrollForceEdge({ source: "shortcut", force: true, direction: "down" });
  return true;
};

export function initGlobalShortcuts() {
  if (registered) {
    return;
  }
  registered = true;

  registerShortcut({
    key: "c",
    shiftKey: true,
    handler: () => focusEntryComposer(),
    preventDefault: true,
  });

  registerShortcut({
    key: "arrowleft",
    shiftKey: true,
    handler: () => goToPreviousDay(),
    preventDefault: true,
  });

  registerShortcut({
    key: "arrowright",
    shiftKey: true,
    handler: () => goToNextDay(),
    preventDefault: true,
  });

  registerShortcut({
    key: "t",
    shiftKey: true,
    handler: () => goToToday(),
    preventDefault: true,
  });

  registerShortcut({
    key: "b",
    shiftKey: true,
    handler: () => scrollEntriesToBottom(),
    preventDefault: true,
  });

  registerShortcut({
    key: "k",
    shiftKey: true,
    handler: () => toggleCalendar(),
    preventDefault: true,
  });
}
