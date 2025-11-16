import {
  applyTimezoneHeader,
  applyTimezoneSearchParam,
  buildTimezoneQueryParam,
  formatIsoDate,
  getTimezone,
  TIMEZONE_QUERY_PARAM,
} from "./datetime.js";
import { createListenerBag } from "../utils/events.js";
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
  } catch (err) {
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
    window.location.href = `${url.pathname}${url.search}`;
  } catch (err) {
    window.location.href = `/d/today?${buildTimezoneQueryParam(zone)}`;
  }
}

export function scheduleMidnightRollover(chatElement) {
  if (!chatElement) return () => {};

  let timeoutId = null;
  const listeners = createListenerBag();

  const runCheck = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }

    const now = new Date();
    const today = updateClientToday(document?.body, now);

    if (chatElement.dataset.date !== today) {
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
