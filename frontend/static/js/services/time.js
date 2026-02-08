import { createListenerBag } from "../utils/events.js";
import {
  applyTimezoneHeader,
  applyTimezoneSearchParam,
  buildTimezoneQueryParam,
  formatIsoDate,
  getTimezone,
  TIMEZONE_QUERY_PARAM,
} from "./datetime.js";
export { getTimezone } from "./datetime.js";

const ESCAPED_TIMEZONE_PARAM = TIMEZONE_QUERY_PARAM.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const TIMEZONE_QUERY_PARAM_PATTERN = new RegExp(`[?&]${ESCAPED_TIMEZONE_PARAM}=`);

export function getClientToday(now = new Date()) {
  return formatIsoDate(now);
}

export function updateClientToday(target = document?.body, now = new Date()) {
  const today = getClientToday(now);
  if (target?.dataset) {
    target.dataset.clientToday = today;
  }
  return today;
}

export function applyRequestTimeHeaders(headers) {
  const timezone = applyTimezoneHeader(headers, getTimezone());
  const clientToday = updateClientToday();

  if (headers && typeof headers === "object" && clientToday) {
    headers["X-Client-Today"] = clientToday;
  }

  return { timezone, clientToday };
}

export function getClockFormat() {
  const raw = document?.body?.dataset?.clockFormat ?? "";
  return raw === "12h" ? "12h" : "24h";
}

function getLocaleForTime() {
  const docLocale = document?.documentElement?.lang ?? "";
  if (docLocale) {
    return docLocale;
  }
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  return "en-US";
}

export function formatLocalTime(value) {
  const date = normalizeTimeValue(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const hour12 = getClockFormat() === "12h";
  const locale = getLocaleForTime();
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    hour12,
  }).format(date);
}

export function formatLocalTimestamp(value) {
  const date = normalizeTimeValue(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const hour12 = getClockFormat() === "12h";
  const locale = getLocaleForTime();
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12,
  }).format(date);
}

export function formatTimeElements(root = document) {
  if (!root || typeof root.querySelectorAll !== "function") {
    return;
  }
  const nodes = root.querySelectorAll("time.entry-time");
  if (!nodes.length) {
    return;
  }
  nodes.forEach((el) => {
    const raw = el.dataset?.timeRaw || el.getAttribute("datetime") || "";
    if (!raw) return;
    const timeText = formatLocalTime(raw);
    if (timeText) {
      el.textContent = timeText;
    }
    const stamp = formatLocalTimestamp(raw);
    if (stamp) {
      el.title = stamp;
    }
  });
}

function normalizeTimeValue(value) {
  if (value instanceof Date) {
    return value;
  }
  if (typeof value === "string") {
    const raw = value.trim();
    if (!raw) {
      return new Date("");
    }
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(raw)) {
      return new Date(`${raw.replace(" ", "T")}Z`);
    }
    return new Date(raw);
  }
  return new Date(value);
}

export function applyTimezoneSearch(searchParams, zone = getTimezone()) {
  return applyTimezoneSearchParam(searchParams, zone);
}

export function applyTimezoneQuery(url, zone = getTimezone()) {
  if (typeof url !== "string" || !url) {
    return url;
  }

  let resolvedUrl = url;

  try {
    const base = window.location?.origin || undefined;
    const parsed = new URL(url, base);
    applyTimezoneSearchParam(parsed.searchParams, zone);
    resolvedUrl = `${parsed.pathname}${parsed.search}`;
  } catch (_err) {
    if (!TIMEZONE_QUERY_PARAM_PATTERN.test(resolvedUrl)) {
      const separator = resolvedUrl.includes("?") ? "&" : "?";
      resolvedUrl = `${resolvedUrl}${separator}${buildTimezoneQueryParam(zone)}`;
    }
  }

  return resolvedUrl;
}

export function navigateToToday(zone = getTimezone()) {
  try {
    const url = new URL("/d/today", window.location.origin);
    applyTimezoneSearchParam(url.searchParams, zone);
    const today = updateClientToday(document?.body, new Date());
    if (today) {
      url.searchParams.set("client_today", today);
    }
    window.location.href = `${url.pathname}${url.search}`;
  } catch (_err) {
    const today = updateClientToday(document?.body, new Date());
    const params = new URLSearchParams(buildTimezoneQueryParam(zone));
    if (today) {
      params.set("client_today", today);
    }
    window.location.href = `/d/today?${params.toString()}`;
  }
}

export function scheduleMidnightRollover(entriesElement) {
  if (!entriesElement) return () => {};

  let timeoutId = null;
  const listeners = createListenerBag();

  const runCheck = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }

    const now = new Date();
    const today = updateClientToday(document?.body, now);

    if (entriesElement.dataset.date !== today) {
      navigateToToday(getTimezone());
      return;
    }

    const nextMidnight = new Date(now);
    nextMidnight.setHours(24, 0, 0, 0);
    timeoutId = window.setTimeout(runCheck, nextMidnight.getTime() - now.getTime());
  };

  const handleVisibility = () => {
    if (document.visibilityState === "visible") {
      runCheck();
    }
  };

  listeners.add(document, "visibilitychange", handleVisibility);
  runCheck();

  return () => {
    listeners.abort();
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  };
}
