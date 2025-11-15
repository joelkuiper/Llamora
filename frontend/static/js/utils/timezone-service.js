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
    } catch (err) {
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
  } catch (err) {
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
      })
    );
    return;
  }
  if (isDocumentAvailable() && typeof document.createEvent === "function") {
    const event = document.createEvent("CustomEvent");
    event.initCustomEvent(
      TIMEZONE_CHANGE_EVENT,
      false,
      false,
      { timezone: nextTimezone, previousTimezone }
    );
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
