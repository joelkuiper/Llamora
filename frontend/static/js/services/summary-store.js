import lockbox from "../lockbox.js";

const SUMMARY_NAMESPACE = "summary";

const normalizeWords = (value) => {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

const makeTagKey = (tag, words) => {
  const base = `tag:${String(tag || "").trim()}`;
  const count = normalizeWords(words);
  return count ? `${base}:w${count}` : base;
};
const makeDayKey = (date) => `day:${String(date || "").trim()}`;

const readPayload = (payload, digest, field) => {
  if (!payload || typeof payload !== "object") return null;
  if (digest != null && String(payload.digest || "") !== String(digest)) return null;
  const value = payload[field];
  return typeof value === "string" ? value : null;
};

export async function getTagSummary(tag, options = {}) {
  const { digest, words } = options || {};
  const key = makeTagKey(tag, words);
  if (!key || key === "tag:") return null;
  try {
    const payload = await lockbox.get(SUMMARY_NAMESPACE, key);
    return readPayload(payload, digest, "html");
  } catch {
    return null;
  }
}

export async function setTagSummary(tag, value, options = {}) {
  const { digest, words } = options || {};
  const key = makeTagKey(tag, words);
  if (!key || key === "tag:") return false;
  if (value == null) return false;
  try {
    await lockbox.set(SUMMARY_NAMESPACE, key, {
      digest: String(digest ?? ""),
      html: value,
    });
    return true;
  } catch {
    return false;
  }
}

export async function deleteTagSummary(tag, options = {}) {
  const { words } = options || {};
  const key = makeTagKey(tag, words);
  if (!key || key === "tag:") return false;
  try {
    await lockbox.delete(SUMMARY_NAMESPACE, key);
    return true;
  } catch {
    return false;
  }
}

export async function getDaySummary(date, options = {}) {
  const { digest } = options || {};
  const key = makeDayKey(date);
  if (!key || key === "day:") return null;
  try {
    const payload = await lockbox.get(SUMMARY_NAMESPACE, key);
    return readPayload(payload, digest, "text");
  } catch {
    return null;
  }
}

export async function setDaySummary(date, value, options = {}) {
  const { digest } = options || {};
  const key = makeDayKey(date);
  if (!key || key === "day:") return false;
  if (value == null) return false;
  try {
    await lockbox.set(SUMMARY_NAMESPACE, key, {
      digest: String(digest ?? ""),
      text: value,
    });
    return true;
  } catch {
    return false;
  }
}

export async function deleteDaySummary(date) {
  const key = makeDayKey(date);
  if (!key || key === "day:") return false;
  try {
    await lockbox.delete(SUMMARY_NAMESPACE, key);
    return true;
  } catch {
    return false;
  }
}
