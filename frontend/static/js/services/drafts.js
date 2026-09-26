// Unsent drafts, per day: the text and the ids of images already uploaded
// for it (spec §10.1). Stored in sessionStorage via draftStore; drafts saved
// before images existed were plain strings and still read as text.

import { draftStore } from "../utils/storage.js";

function cleanIds(value) {
  return Array.isArray(value) ? value.filter((id) => typeof id === "string" && id) : [];
}

/** The draft for ``day``: ``{ text, images }`` (empty when there is none). */
export function readDraft(day) {
  const raw = day ? draftStore.get(day) : null;
  if (!raw) return { text: "", images: [] };
  if (typeof raw === "string") return { text: raw, images: [] };
  return { text: String(raw.text ?? ""), images: cleanIds(raw.images) };
}

export function isEmptyDraft(draft) {
  return !draft || (!String(draft.text ?? "").trim() && !cleanIds(draft.images).length);
}

/** Save the draft for ``day``; an empty draft is removed instead. */
export function writeDraft(day, { text = "", images = [] } = {}) {
  if (!day) return;
  const draft = { text: String(text ?? ""), images: cleanIds(images) };
  if (!draft.text && !draft.images.length) {
    draftStore.delete(day);
    return;
  }
  draftStore.set(day, draft);
}

export function clearDraft(day) {
  if (day) draftStore.delete(day);
}
