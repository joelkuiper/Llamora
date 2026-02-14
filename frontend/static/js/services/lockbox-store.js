import lockbox from "../lockbox.js";

export async function getValue(namespace, key) {
  if (!namespace || !key) return null;
  try {
    return await lockbox.get(namespace, key);
  } catch {
    return null;
  }
}

export async function setValue(namespace, key, value) {
  if (!namespace || !key) return false;
  try {
    await lockbox.set(namespace, key, value);
    return true;
  } catch {
    return false;
  }
}

export async function deleteValue(namespace, key) {
  if (!namespace || !key) return false;
  try {
    await lockbox.delete(namespace, key);
    return true;
  } catch {
    return false;
  }
}

export async function listKeys(namespace) {
  if (!namespace) return [];
  try {
    const keys = await lockbox.list(namespace);
    return Array.isArray(keys) ? keys : [];
  } catch {
    return [];
  }
}
