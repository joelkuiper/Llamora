import { cacheLoader } from "../../services/cache-loader.js";
import { syncSummarySkeletons } from "../../services/summary-skeleton.js";

export const getSummaryElement = (root = document) =>
  root.querySelector?.(".tags-view__summary[data-tag-hash]") ||
  document.querySelector(".tags-view__summary[data-tag-hash]");

export const hydrateTagsViewSummary = async (root = document) => {
  const summaryEl = getSummaryElement(root);
  if (!summaryEl) return;
  await cacheLoader.hydrate(summaryEl);
};

export const cacheTagsViewSummary = (summaryEl) => {
  if (!(summaryEl instanceof HTMLElement)) return;
  void cacheLoader.capture(summaryEl);
};

export { syncSummarySkeletons };
