import { TYPING_INDICATOR_SELECTOR } from "../../typing-indicator.js";
import { normalizeStreamId } from "./stream-id.js";

function escapeAttributeValue(value) {
  if (window.CSS?.escape) {
    return window.CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}

function getSearchRoot(root) {
  if (root && typeof root.querySelector === "function") {
    return root;
  }
  return document;
}

export function findStreamByEntryId(root, entryId) {
  const normalized = normalizeStreamId(entryId);
  if (!normalized) {
    return null;
  }

  const searchRoot = getSearchRoot(root);
  return searchRoot.querySelector(
    `response-stream[data-entry-id="${escapeAttributeValue(normalized)}"]`,
  );
}

export function findTypingIndicator(root, entryId) {
  const searchRoot = getSearchRoot(root);
  const normalized = normalizeStreamId(entryId);

  if (normalized) {
    const stream = findStreamByEntryId(searchRoot, normalized);
    const scoped = stream?.querySelector(TYPING_INDICATOR_SELECTOR);
    if (scoped) {
      return scoped;
    }

    const typed = Array.from(searchRoot.querySelectorAll(TYPING_INDICATOR_SELECTOR)).find(
      (node) => node?.dataset?.entryId === normalized,
    );
    if (typed) {
      return typed;
    }
  }

  return searchRoot.querySelector(TYPING_INDICATOR_SELECTOR);
}
