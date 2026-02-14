import lockbox from "../lockbox.js";

const SUMMARY_NAMESPACE = "summary";

const makeTagKey = (tag) => `tag:${String(tag || "").trim()}`;
const makeDayKey = (date) => `day:${String(date || "").trim()}`;

export async function getTagSummary(tag) {
  const key = makeTagKey(tag);
  if (!key || key === "tag:") return null;
  try {
    return await lockbox.get(SUMMARY_NAMESPACE, key);
  } catch {
    return null;
  }
}

export async function setTagSummary(tag, value) {
  const key = makeTagKey(tag);
  if (!key || key === "tag:") return false;
  if (value == null) return false;
  try {
    await lockbox.set(SUMMARY_NAMESPACE, key, value);
    return true;
  } catch {
    return false;
  }
}

export async function deleteTagSummary(tag) {
  const key = makeTagKey(tag);
  if (!key || key === "tag:") return false;
  try {
    await lockbox.delete(SUMMARY_NAMESPACE, key);
    return true;
  } catch {
    return false;
  }
}

export async function getDaySummary(date) {
  const key = makeDayKey(date);
  if (!key || key === "day:") return null;
  try {
    return await lockbox.get(SUMMARY_NAMESPACE, key);
  } catch {
    return null;
  }
}

export async function setDaySummary(date, value) {
  const key = makeDayKey(date);
  if (!key || key === "day:") return false;
  if (value == null) return false;
  try {
    await lockbox.set(SUMMARY_NAMESPACE, key, value);
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
