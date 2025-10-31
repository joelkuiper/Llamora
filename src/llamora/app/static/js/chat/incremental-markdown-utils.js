export function computeSharedPrefixLength(previous, next) {
  const prev = Array.isArray(previous) ? previous : [];
  const curr = Array.isArray(next) ? next : [];
  const max = Math.min(prev.length, curr.length);
  let index = 0;
  for (; index < max; index += 1) {
    if (prev[index] !== curr[index]) {
      break;
    }
  }
  return index;
}
