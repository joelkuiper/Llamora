const DEFAULT_TIMEZONE = "UTC";
export const TIMEZONE_COOKIE_NAME = "tz";
export const TIMEZONE_HEADER_NAME = "X-Timezone";
export const TIMEZONE_QUERY_PARAM = "tz";
export const TIMEZONE_CHANGE_EVENT = "timezonechange";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

function isDocumentAvailable() {
  return typeof document !== "undefined" && document !== null;
}

function isWindowAvailable() {
  return typeof window !== "undefined" && window !== null;
}

function sanitizeStoredTimezone(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function readCookie(name) {
  if (!isDocumentAvailable()) return null;
  const cookies = document.cookie ? document.cookie.split("; ") : [];
  for (const cookie of cookies) {
    if (!cookie) continue;
    const [rawName, ...rest] = cookie.split("=");
    if (rawName !== name) continue;
    const rawValue = rest.join("=");
    try {
      return decodeURIComponent(rawValue);
    } catch (_err) {
      return rawValue;
    }
  }
  return null;
}

function writeCookie(name, value) {
  if (!isDocumentAvailable()) return;
  const encodedValue = encodeURIComponent(value);
  const directives = [
    `${name}=${encodedValue}`,
    "path=/",
    "SameSite=Lax",
    `Max-Age=${COOKIE_MAX_AGE_SECONDS}`,
  ];
  if (isWindowAvailable() && window.location?.protocol === "https:") {
    directives.push("Secure");
  }
  document.cookie = directives.join("; ");
}

function resolveTimezone() {
  if (typeof Intl !== "object" || typeof Intl.DateTimeFormat !== "function") {
    return DEFAULT_TIMEZONE;
  }
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (typeof tz === "string" && tz.trim()) {
      return tz;
    }
  } catch (_err) {
    // ignore and fall back to default
  }
  return DEFAULT_TIMEZONE;
}

function dispatchTimezoneChange(nextTimezone, previousTimezone) {
  if (!isWindowAvailable() || typeof window.dispatchEvent !== "function") {
    return;
  }
  if (typeof CustomEvent === "function") {
    window.dispatchEvent(
      new CustomEvent(TIMEZONE_CHANGE_EVENT, {
        detail: { timezone: nextTimezone, previousTimezone },
      }),
    );
    return;
  }
  if (isDocumentAvailable() && typeof document.createEvent === "function") {
    const event = document.createEvent("CustomEvent");
    event.initCustomEvent(TIMEZONE_CHANGE_EVENT, false, false, {
      timezone: nextTimezone,
      previousTimezone,
    });
    window.dispatchEvent(event);
  }
}

let memoizedTimezone = sanitizeStoredTimezone(readCookie(TIMEZONE_COOKIE_NAME));

export function getTimezone() {
  const resolved = resolveTimezone();
  const zone = typeof resolved === "string" && resolved.trim() ? resolved : DEFAULT_TIMEZONE;
  const previous = memoizedTimezone;

  memoizedTimezone = zone;
  writeCookie(TIMEZONE_COOKIE_NAME, zone);

  if (previous && previous !== zone) {
    dispatchTimezoneChange(zone, previous);
  }

  return zone;
}

export function applyTimezoneHeader(headers, zone = getTimezone()) {
  if (headers && typeof headers === "object") {
    headers[TIMEZONE_HEADER_NAME] = zone;
  }
  return zone;
}

export function applyTimezoneSearchParam(searchParams, zone = getTimezone()) {
  if (searchParams && typeof searchParams.set === "function") {
    searchParams.set(TIMEZONE_QUERY_PARAM, zone);
  }
  return zone;
}

export function buildTimezoneQueryParam(zone = getTimezone()) {
  return `${TIMEZONE_QUERY_PARAM}=${encodeURIComponent(zone)}`;
}

export function formatIsoDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "";
  }
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
  if (date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d) {
    return null;
  }
  return { date, year: y, month: m, day: d };
}

export function parseIsoDate(value) {
  return parseDateFromSource(value);
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
    // SQLite CURRENT_TIMESTAMP produces space-delimited UTC strings (e.g. "2025-01-15 14:30:00").
    // Append "Z" to interpret them as UTC.
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(raw)) {
      return new Date(`${raw.replace(" ", "T")}Z`);
    }
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed;
    }
    const fallback = raw.replace(/([+-]\d{2}):(\d{2})$/, "$1$2");
    return new Date(fallback);
  }
  return new Date(value);
}

export function getClientToday(now = new Date()) {
  return formatIsoDate(now);
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

function getRelativeFormatter() {
  const locale = getLocaleForTime();
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
}

function resolveRelativeUnit(diffMs) {
  const absMs = Math.abs(diffMs);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const week = 7 * day;
  const month = 30 * day;
  const year = 365 * day;

  if (absMs < minute) return { unit: "second", size: 1_000 };
  if (absMs < hour) return { unit: "minute", size: minute };
  if (absMs < day) return { unit: "hour", size: hour };
  if (absMs < week) return { unit: "day", size: day };
  if (absMs < month) return { unit: "week", size: week };
  if (absMs < year) return { unit: "month", size: month };
  return { unit: "year", size: year };
}

export function formatRelativeTime(value, now = new Date()) {
  const date = normalizeTimeValue(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const diffMs = date.getTime() - now.getTime();
  const { unit, size } = resolveRelativeUnit(diffMs);
  const amount = Math.round(diffMs / size);
  return getRelativeFormatter().format(amount, unit);
}

export function formatLocalDateTimeContext(value, now = new Date()) {
  const date = normalizeTimeValue(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const relative = formatRelativeTime(date, now);
  const hour12 = getClockFormat() === "12h";
  const locale = getLocaleForTime();
  const absolute = new Intl.DateTimeFormat(locale, {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12,
  }).format(date);
  if (!relative) {
    return absolute;
  }
  return `${relative} · ${absolute}`;
}
