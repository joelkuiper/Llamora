const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export const normalizeIsoDay = (value) => {
  const day = String(value || "").trim();
  return ISO_DAY_RE.test(day) ? day : "";
};
