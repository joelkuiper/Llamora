import { getFrameState } from "../../services/app-state.js";
import {
  buildTagPageUrl,
  isTagsPath,
  normalizeTagsNavUrl,
  parseTagFromPath,
} from "./tags-nav-url.js";

export { isTagsPath, normalizeTagsNavUrl, parseTagFromPath };

/**
 * Returns the day for the tags view from 🟢 frame state (URL-derived).
 * The frame state is authoritative here — the tags view is always reached
 * through a full swap that includes a view-state JSON.
 */
export const getTagsDay = () => getFrameState().day;

/**
 * Reads the currently selected tag.
 * Checks frame state first (server-authoritative), then falls back to URL
 * parsing for the case where the frame hasn't been hydrated yet.
 */
export const readTagFromUrl = () => {
  const { selectedTag } = getFrameState();
  if (selectedTag) return selectedTag;
  const url = new URL(window.location.href);
  return parseTagFromPath(url.pathname) || String(url.searchParams.get("tag") || "").trim();
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
  if (!tagOverride && !isTagsPath(url.pathname)) return "";
  const { selectedTag } = getFrameState();
  const tag =
    tagOverride ||
    selectedTag ||
    parseTagFromPath(url.pathname) ||
    String(url.searchParams.get("tag") || "").trim();
  return buildTagPageUrl(tag, { day: getTagsDay() });
};
