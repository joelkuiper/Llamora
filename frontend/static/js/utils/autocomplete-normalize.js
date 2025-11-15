export const normalizeAutocompleteValue = (value) => {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim().toLowerCase();
  }
  if (typeof value === "object") {
    const fields = ["value", "key", "id", "label"];
    for (const field of fields) {
      const candidate = value[field];
      if (typeof candidate === "string") {
        return candidate.trim().toLowerCase();
      }
    }
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
};
