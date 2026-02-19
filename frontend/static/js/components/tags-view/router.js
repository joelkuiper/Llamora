import { resolveCurrentDay } from "../../services/day-resolution.js";
import { getViewState } from "../../services/view-state.js";
import { getActiveDay } from "../entries-view/active-day-store.js";
import {
  buildTagPageUrl,
  isTagsPath,
  normalizeTagsNavUrl,
  parseTagFromPath,
} from "./tags-nav-url.js";

export { isTagsPath, normalizeTagsNavUrl, parseTagFromPath };

export const readTagFromUrl = () => {
  const viewState = getViewState();
  const selected = String(viewState?.selected_tag || "").trim();
  if (selected) return selected;
  const url = new URL(window.location.href);
  const fromPath = parseTagFromPath(url.pathname);
  if (fromPath) return fromPath;
  return String(url.searchParams.get("tag") || "").trim();
};

export const syncTagsHistoryUrl = ({ selectedTag } = {}) => {
  const detailTag = String(selectedTag || "").trim();
  const currentUrl = new URL(window.location.href);
  const tag = detailTag || parseTagFromPath(currentUrl.pathname) || "";
  const nextUrl = buildTagPageUrl(tag, { day: getTagsDay() });
  window.history.replaceState(window.history.state, "", nextUrl);
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
  return buildTagPageUrl(tag, { day: getTagsDay() });
};

export const getTagsDay = () => {
  return resolveCurrentDay({
    viewState: getViewState(),
    activeDay: getActiveDay(),
    url: window.location.href,
  });
};
