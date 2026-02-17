const SHORTCODE_PATTERN = /^:[a-z0-9_+-]+:$/i;

const normalizeShortcode = (value) => {
  const raw = String(value ?? "")
    .trim()
    .toLowerCase();
  return SHORTCODE_PATTERN.test(raw) ? raw : "";
};

export const isShortcodeLookupQuery = (value) => {
  const raw = String(value ?? "")
    .trim()
    .toLowerCase();
  if (!raw.startsWith(":")) return false;
  const body = raw.slice(1);
  if (!body) return false;
  return /^[a-z0-9_+\-:]+$/.test(body);
};

export const shortcodeSearchTokens = (shortcode) => {
  const normalized = normalizeShortcode(shortcode);
  if (!normalized) return [];
  const body = normalized.slice(1, -1);
  if (!body) return [normalized];
  const compact = body.replaceAll("_", "");
  return Array.from(
    new Set([normalized, body, body.replaceAll("_", " "), compact].filter(Boolean)),
  );
};

export const fetchEmojiShortcodeSuggestions = async (query, { signal = null, limit = 20 } = {}) => {
  if (!isShortcodeLookupQuery(query)) {
    return [];
  }
  const q = String(query ?? "").trim();
  const params = new URLSearchParams({
    q,
    limit: String(Math.max(1, Math.min(64, Number(limit) || 20))),
  });
  const response = await fetch(`/emoji/suggest?${params.toString()}`, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    return [];
  }
  const payload = await response.json().catch(() => null);
  if (!payload || !Array.isArray(payload.suggestions)) {
    return [];
  }
  return payload.suggestions
    .map((item) => {
      const shortcode = normalizeShortcode(item?.shortcode);
      const emoji = String(item?.emoji || "").trim();
      if (!shortcode || !emoji) {
        return null;
      }
      return {
        shortcode,
        emoji,
        label: String(item?.label || "").trim(),
      };
    })
    .filter(Boolean);
};
