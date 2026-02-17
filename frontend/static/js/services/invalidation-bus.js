import { deleteValue, listKeys } from "./lockbox-store.js";

const listeners = new Set();

const normalizeEntry = (entry) => {
  if (!entry || typeof entry !== "object") return null;
  const namespace = String(entry.namespace || "").trim();
  const key = String(entry.key || "").trim();
  const prefix = String(entry.prefix || "").trim();
  const reason = String(entry.reason || "").trim();
  if (!namespace) return null;
  if (!key && !prefix) return null;
  return {
    namespace,
    key: key || null,
    prefix: prefix || null,
    reason,
  };
};

const emit = async (payload) => {
  const entries = Array.isArray(payload?.keys) ? payload.keys : [];
  const normalized = entries.map(normalizeEntry).filter(Boolean);
  if (!normalized.length) return;
  await Promise.all(
    normalized.map(async (item) => {
      if (item.key) {
        await deleteValue(item.namespace, item.key);
        return;
      }
      if (item.prefix) {
        const keys = await listKeys(item.namespace);
        await Promise.all(
          keys
            .filter((key) => key.startsWith(item.prefix))
            .map((key) => deleteValue(item.namespace, key)),
        );
      }
    }),
  );
  listeners.forEach((listener) => {
    try {
      listener({ keys: normalized, reason: payload?.reason || "" });
    } catch {
      // no-op
    }
  });
};

export const invalidateCache = async ({ namespace, key, prefix, reason } = {}) => {
  const payload = {
    reason: reason || "manual",
    keys: [
      {
        namespace,
        key,
        prefix,
        reason,
      },
    ],
  };
  await emit(payload);
};

export const onInvalidation = (listener) => {
  if (typeof listener !== "function") return () => {};
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export const handleInvalidationEvent = (event) => {
  const detail = event?.detail;
  if (!detail) return;
  void emit(detail);
};

export default {
  invalidateCache,
  onInvalidation,
  handleInvalidationEvent,
};
