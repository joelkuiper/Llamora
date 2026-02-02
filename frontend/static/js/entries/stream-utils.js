import { TYPING_INDICATOR_SELECTOR } from "../typing-indicator.js";
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

export function findStreamByUserMsgId(root, msgId) {
  const normalized = normalizeStreamId(msgId);
  if (!normalized) {
    return null;
  }

  const searchRoot = getSearchRoot(root);
  return searchRoot.querySelector(
    `llm-stream[data-user-msg-id="${escapeAttributeValue(normalized)}"]`
  );
}

export function findTypingIndicator(root, msgId) {
  const searchRoot = getSearchRoot(root);
  const normalized = normalizeStreamId(msgId);

  if (normalized) {
    const stream = findStreamByUserMsgId(searchRoot, normalized);
    const scoped = stream?.querySelector(TYPING_INDICATOR_SELECTOR);
    if (scoped) {
      return scoped;
    }

    const typed = Array.from(
      searchRoot.querySelectorAll(TYPING_INDICATOR_SELECTOR)
    ).find((node) => node?.dataset?.userMsgId === normalized);
    if (typed) {
      return typed;
    }
  }

  return searchRoot.querySelector(TYPING_INDICATOR_SELECTOR);
}
