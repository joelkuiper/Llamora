import { createListenerBag } from "../utils/events.js";
import {
  applyTimezoneHeader,
  applyTimezoneSearchParam,
  buildTimezoneQueryParam,
  formatLocalDateTimeContext,
  formatLocalTime,
  formatLocalTimestamp,
  formatRelativeTime,
  getClientToday,
  getTimezone,
  TIMEZONE_QUERY_PARAM,
} from "./datetime.js";
import { clearDraft, isEmptyDraft, readDraft, writeDraft } from "./drafts.js";

const ESCAPED_TIMEZONE_PARAM = TIMEZONE_QUERY_PARAM.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const TIMEZONE_QUERY_PARAM_PATTERN = new RegExp(`[?&]${ESCAPED_TIMEZONE_PARAM}=`);

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
    const style = String(el.dataset?.timeStyle || "")
      .trim()
      .toLowerCase();
    let timeText =
      style === "ago-date-time" ? formatLocalDateTimeContext(raw) : formatLocalTime(raw);
    if (!timeText) {
      timeText = formatLocalTimestamp(raw);
    }
    if (timeText) {
      el.textContent = timeText;
    }
    const stamp = formatLocalTimestamp(raw);
    const relative = formatRelativeTime(raw);
    const tooltipText = formatLocalDateTimeContext(raw);
    if (tooltipText) {
      el.dataset.tooltipTitle = tooltipText;
    } else if (stamp) {
      el.dataset.tooltipTitle = relative ? `${relative} · ${stamp}` : stamp;
    } else if (relative) {
      el.dataset.tooltipTitle = relative;
    }
  });
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

/**
 * Move a page that was showing ``fromDay`` to the writer's new today.
 *
 * Unsent text in the composer (or ``fromDay``'s saved draft) moves to the new
 * day's draft, so nothing typed is lost. The page is re-rendered in place via
 * /e/today, whose push of /d/today becomes a history replace when that is
 * already the URL: no query string, no extra Back step.
 */
export function rollOverToToday(fromDay, today = updateClientToday()) {
  // The unsent draft (text and uploaded images) moves to the new day.
  const composer = document.getElementById("entry-text");
  const attach = document.querySelector("#entry-form image-attach");
  const saved = readDraft(fromDay);
  const unsent = {
    text: composer?.value || saved.text,
    images: attach?.ids?.length ? attach.ids : saved.images,
  };
  if (fromDay && fromDay !== today) {
    clearDraft(fromDay);
  }
  if (!isEmptyDraft(unsent) && today && isEmptyDraft(readDraft(today))) {
    writeDraft(today, unsent);
  }

  const htmx = globalThis.htmx;
  const target = document.getElementById("content-wrapper");
  if (!htmx?.ajax || !target) {
    navigateToToday(getTimezone());
    return;
  }
  const params = new URLSearchParams();
  applyTimezoneSearchParam(params, getTimezone());
  if (today) {
    params.set("client_today", today);
  }
  htmx.ajax("GET", `/e/today?${params.toString()}`, {
    target,
    swap: "outerHTML",
    source: document.body,
  });
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
      rollOverToToday(entriesElement.dataset.date, today);
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
