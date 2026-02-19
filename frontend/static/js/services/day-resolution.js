const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export const normalizeIsoDay = (value) => {
  const day = String(value || "").trim();
  return ISO_DAY_RE.test(day) ? day : "";
};

const resolveUrlObject = (inputUrl) => {
  try {
    if (inputUrl instanceof URL) {
      return inputUrl;
    }
    return new URL(String(inputUrl || window.location.href), window.location.origin);
  } catch {
    return null;
  }
};

export const resolveCurrentDay = ({ viewState = null, activeDay = "", url } = {}) => {
  const normalizedActiveDay = normalizeIsoDay(activeDay);
  const normalizedStateDay = normalizeIsoDay(viewState?.day);
  const view = String(viewState?.view || "")
    .trim()
    .toLowerCase();
  if (view === "tags" && normalizedStateDay) {
    return normalizedStateDay;
  }
  if (normalizedActiveDay) {
    return normalizedActiveDay;
  }
  if (normalizedStateDay) {
    return normalizedStateDay;
  }

  const resolvedUrl = resolveUrlObject(url);
  const queryDay = normalizeIsoDay(resolvedUrl?.searchParams?.get("day"));
  if (queryDay) {
    return queryDay;
  }
  const pathDay = normalizeIsoDay(resolvedUrl?.pathname?.match(/^\/d\/(\d{4}-\d{2}-\d{2})$/)?.[1]);
  return pathDay;
};
