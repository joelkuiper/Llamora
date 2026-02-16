import { armEntryAnimations, armInitialEntryAnimations } from "../../entries/entry-animations.js";
import { formatTimeElements } from "../../services/time.js";
import { clearScrollTarget, flashHighlight } from "../../ui.js";
import { state } from "./state.js";
import { findDetail, findList } from "./dom.js";
import { readTagFromUrl, updateUrlSortParams } from "./router.js";

export const getSelectedTrace = (root = document) =>
  String(findDetail(root)?.dataset?.selectedTag || "").trim();

const normalizeSortKind = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "count"
    ? "count"
    : "alpha";

const normalizeSortDir = (value) =>
  String(value || "")
    .trim()
    .toLowerCase() === "desc"
    ? "desc"
    : "asc";

const readSortFromDom = (root = document) => {
  const detail = findDetail(root);
  const list = findList(root);
  const detailKind = detail?.dataset?.sortKind;
  const detailDir = detail?.dataset?.sortDir;
  const listKind = list?.dataset?.sortKind;
  const listDir = list?.dataset?.sortDir;
  const rawKind = listKind || detailKind;
  const rawDir = listDir || detailDir;
  return {
    kind: rawKind ? normalizeSortKind(rawKind) : state.sortKind || "count",
    dir: rawDir ? normalizeSortDir(rawDir) : state.sortDir || "desc",
  };
};

export const refreshDetailLinksForSort = (root = document) => {
  const detail = findDetail(root);
  if (!detail) return;
  const kind = state.sortKind;
  const dir = state.sortDir;
  detail.querySelectorAll(".tags-view__related-link, .tags-view__entry-tag").forEach((link) => {
    if (!(link instanceof HTMLAnchorElement)) return;
    const href = link.getAttribute("href");
    const hxGet = link.getAttribute("hx-get");
    const hxPush = link.getAttribute("hx-push-url");
    if (href) link.setAttribute("href", updateUrlSortParams(href, kind, dir));
    if (hxGet) link.setAttribute("hx-get", updateUrlSortParams(hxGet, kind, dir));
    if (hxPush) link.setAttribute("hx-push-url", updateUrlSortParams(hxPush, kind, dir));
  });
  detail.querySelectorAll("entry-tags").forEach((el) => {
    if (!(el instanceof HTMLElement)) return;
    const pageTemplate = el.dataset.tagNavigatePageTemplate;
    const fragmentTemplate = el.dataset.tagNavigateFragmentTemplate;
    const addUrl = el.dataset.addTagUrl;
    const suggestUrl = el.dataset.suggestionsUrl;
    if (pageTemplate) {
      el.dataset.tagNavigatePageTemplate = updateUrlSortParams(pageTemplate, kind, dir);
    }
    if (fragmentTemplate) {
      el.dataset.tagNavigateFragmentTemplate = updateUrlSortParams(fragmentTemplate, kind, dir);
    }
    if (addUrl) {
      el.dataset.addTagUrl = updateUrlSortParams(addUrl, kind, dir);
    }
    if (suggestUrl) {
      el.dataset.suggestionsUrl = updateUrlSortParams(suggestUrl, kind, dir);
    }
  });
};

export const syncFromDetail = (root = document, { ensureActiveRowPresent, setActiveTag } = {}) => {
  const detail = findDetail(root);
  const sortFromDom = readSortFromDom(root);
  const urlTag = readTagFromUrl();
  const target = String(new URLSearchParams(window.location.search).get("target") || "").trim();
  const keepTargetScrollForHighlight = target.startsWith("tag-index-");

  state.sortKind = sortFromDom.kind;
  state.sortDir = sortFromDom.dir;

  if (detail) {
    const detailTag = String(detail.dataset.selectedTag || "").trim();
    const selectedTag = detailTag || urlTag;
    if (selectedTag) {
      detail.dataset.selectedTag = selectedTag;
    }
    if (selectedTag && typeof ensureActiveRowPresent === "function") {
      ensureActiveRowPresent(selectedTag);
    }
    if (selectedTag && typeof setActiveTag === "function") {
      setActiveTag(selectedTag, root, {
        behavior: "auto",
        scroll: !keepTargetScrollForHighlight,
      });
    }
  }
};

export const updateSelectedTagCounts = (root = document, delta = -1) => {
  const detail = findDetail(root);
  if (!detail) return;
  const rawCount =
    Number.parseInt(detail.dataset.selectedTagCount || "", 10) ||
    Number.parseInt(detail.querySelector(".tags-view__meta")?.textContent || "", 10) ||
    0;
  const nextCount = Math.max(0, rawCount + delta);
  detail.dataset.selectedTagCount = String(nextCount);
  const metaEl = detail.querySelector(".tags-view__meta");
  if (metaEl) {
    const parts = metaEl.textContent.split("·");
    const suffix = parts.length > 1 ? parts.slice(1).join("·").trim() : "";
    const entryLabel = nextCount === 1 ? "entry" : "entries";
    metaEl.textContent = suffix
      ? `${nextCount} ${entryLabel} · ${suffix}`
      : `${nextCount} ${entryLabel}`;
  }
  const activeRow = document.querySelector(".tags-view__index-row.is-active");
  if (activeRow instanceof HTMLElement) {
    const countEl = activeRow.querySelector(".tags-view__index-count");
    if (countEl) {
      countEl.textContent = String(nextCount);
    }
    activeRow.dataset.tagsCount = String(nextCount);
  }
};

export const animateDetailEntries = (root = document) => {
  const detail = findDetail(root);
  if (!detail) return;
  formatTimeElements(detail);
  armEntryAnimations(detail);
  const entries = detail.querySelector(".tags-view__entries");
  if (!(entries instanceof HTMLElement)) return;
  armInitialEntryAnimations(entries);
};

export const highlightRequestedTag = (root = document, { setActiveTag } = {}) => {
  const params = new URLSearchParams(window.location.search);
  const target = String(params.get("target") || "").trim();
  if (!target || !target.startsWith("tag-index-")) return;
  const row = document.getElementById(target);
  if (row instanceof HTMLElement) {
    const tagName = row.dataset.tagName || "";
    if (tagName && typeof setActiveTag === "function") {
      setActiveTag(tagName, root, { behavior: "smooth" });
    }
    flashHighlight(row);
  }
  clearScrollTarget(target, { emitEvent: false });
};
