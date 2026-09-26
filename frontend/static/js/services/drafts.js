// Unsent drafts, per day: the text and the ids of images already uploaded
// for it. Stored in sessionStorage via draftStore; drafts saved
// before images existed were plain strings and still read as text.
//
// Drafts belong to a user (the page's opaque data-draft-owner): they survive
// a forced re-login (an expired session, a server restart) and come back for
// the same user only. An explicit logout clears them.

import { draftStore } from "../utils/storage.js";

function draftKey(day) {
  const owner = document.body?.dataset.draftOwner || "";
  return owner && day ? `${owner}:${day}` : null;
}

if (!globalThis.__draftsLogoutBound) {
  globalThis.__draftsLogoutBound = true;
  // Leaving on purpose (e.g. a shared computer): take the drafts along.
  document.addEventListener(
    "submit",
    (event) => {
      if (event.target?.matches?.('form[action$="/logout"]')) {
        draftStore.clear();
      }
    },
    true,
  );
}

function cleanIds(value) {
  return Array.isArray(value) ? value.filter((id) => typeof id === "string" && id) : [];
}

/** The draft for ``day``: ``{ text, images }`` (empty when there is none). */
export function readDraft(day) {
  const key = draftKey(day);
  const raw = key ? draftStore.get(key) : null;
  if (!raw) return { text: "", images: [] };
  if (typeof raw === "string") return { text: raw, images: [] };
  return { text: String(raw.text ?? ""), images: cleanIds(raw.images) };
}

export function isEmptyDraft(draft) {
  return !draft || (!String(draft.text ?? "").trim() && !cleanIds(draft.images).length);
}

/** Save the draft for ``day``; an empty draft is removed instead. */
export function writeDraft(day, { text = "", images = [] } = {}) {
  const key = draftKey(day);
  if (!key) return;
  const draft = { text: String(text ?? ""), images: cleanIds(images) };
  if (!draft.text && !draft.images.length) {
    draftStore.delete(key);
    return;
  }
  draftStore.set(key, draft);
}

export function clearDraft(day) {
  const key = draftKey(day);
  if (key) draftStore.delete(key);
}
