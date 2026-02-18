import { getViewState } from "../../services/view-state.js";

export const parseTagFromPath = (pathname) => {
  if (!pathname || !pathname.startsWith("/t/")) return "";
  const raw = pathname.slice(3);
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

export const isTagsPath = (pathname) => pathname === "/t" || pathname.startsWith("/t/");

export const readTagFromUrl = () => {
  const viewState = getViewState();
  const selected = String(viewState?.selected_tag || "").trim();
  if (selected) return selected;
  const url = new URL(window.location.href);
  const fromPath = parseTagFromPath(url.pathname);
  if (fromPath) return fromPath;
  return String(url.searchParams.get("tag") || "").trim();
};

export const normalizeTagsNavUrl = (rawUrl) => {
  if (!rawUrl) return rawUrl;
  try {
    const current = new URL(window.location.href);
    const next = new URL(rawUrl, current.origin);
    const day = getTagsDay();
    if (day && isTagsPath(next.pathname)) {
      next.searchParams.set("day", day);
    }
    next.searchParams.delete("sort_kind");
    next.searchParams.delete("sort_dir");
    return `${next.pathname}${next.search}${next.hash}`;
  } catch {
    return rawUrl;
  }
};

export const syncTagsHistoryUrl = ({ selectedTag } = {}) => {
  const detailTag = String(selectedTag || "").trim();
  const url = new URL(window.location.href);
  const tag = detailTag || parseTagFromPath(url.pathname) || "";
  const day = getTagsDay();
  url.pathname = tag ? `/t/${encodeURIComponent(tag)}` : "/t";
  if (day) {
    url.searchParams.set("day", day);
  }
  url.searchParams.delete("sort_kind");
  url.searchParams.delete("sort_dir");
  url.searchParams.delete("tag");
  url.searchParams.delete("target");
  window.history.replaceState(window.history.state, "", url.toString());
};

export const getTagsLocationKey = (tagOverride) => {
  const url = new URL(window.location.href);
  const pathname = url.pathname;
  if (!tagOverride && !isTagsPath(pathname)) {
    return "";
  }
  const viewState = getViewState();
  const tag =
    tagOverride ||
    String(viewState?.selected_tag || "").trim() ||
    parseTagFromPath(pathname) ||
    String(url.searchParams.get("tag") || "").trim();
  const nextPath = tag ? `/t/${encodeURIComponent(tag)}` : "/t";
  const params = new URLSearchParams(url.search);
  const day = getTagsDay();
  if (day) {
    params.set("day", day);
  }
  params.delete("sort_kind");
  params.delete("sort_dir");
  params.delete("tag");
  params.delete("target");
  const qs = params.toString();
  return qs ? `${nextPath}?${qs}` : nextPath;
};

export const getTagsDay = () => {
  const viewState = getViewState();
  const fromState = String(viewState?.day || "").trim();
  if (fromState) return fromState;
  const fromDom = String(document.querySelector("#tags-view")?.dataset?.day || "").trim();
  if (fromDom) return fromDom;
  const url = new URL(window.location.href);
  const fromQuery = String(url.searchParams.get("day") || "").trim();
  if (fromQuery) return fromQuery;
  const match = url.pathname.match(/\/d\/(\d{4}-\d{2}-\d{2})$/);
  return String(match?.[1] || "").trim();
};
