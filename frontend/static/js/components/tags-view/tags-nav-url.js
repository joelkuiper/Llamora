import { normalizeIsoDay } from "../../services/day-resolution.js";

export const isTagsPath = (pathname) =>
  String(pathname || "").trim() === "/t" ||
  String(pathname || "")
    .trim()
    .startsWith("/t/");

export const parseTagFromPath = (pathname) => {
  const path = String(pathname || "").trim();
  if (!path.startsWith("/t/")) return "";
  const raw = path.slice(3);
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

export const buildTagPageUrl = (tagName, { day = "" } = {}) => {
  const cleanTag = String(tagName || "").trim();
  const cleanDay = normalizeIsoDay(day);
  const path = cleanTag ? `/t/${encodeURIComponent(cleanTag)}` : "/t";
  if (!cleanDay) {
    return path;
  }
  const params = new URLSearchParams({ day: cleanDay });
  return `${path}?${params.toString()}`;
};

export const buildTagDetailFragmentUrl = (day, tagName, { tagHash = "" } = {}) => {
  const cleanDay = normalizeIsoDay(day);
  if (!cleanDay) return "";
  const cleanTag = String(tagName || "").trim();
  if (!cleanTag) return "";
  const params = new URLSearchParams({ tag: cleanTag });
  const cleanTagHash = String(tagHash || "").trim();
  if (cleanTagHash) {
    params.set("tag_hash", cleanTagHash);
  }
  return `/fragments/tags/${encodeURIComponent(cleanDay)}/detail?${params.toString()}`;
};

export const normalizeTagsNavUrl = (rawUrl, { day = "" } = {}) => {
  if (!rawUrl) return rawUrl;
  try {
    const current = new URL(window.location.href);
    const next = new URL(rawUrl, current.origin);
    const cleanDay = normalizeIsoDay(day);
    if (cleanDay && isTagsPath(next.pathname)) {
      next.searchParams.set("day", cleanDay);
    }
    return `${next.pathname}${next.search}${next.hash}`;
  } catch {
    return rawUrl;
  }
};
