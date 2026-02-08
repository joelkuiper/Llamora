export function isNearBottom(element, threshold = 0) {
  if (!(element instanceof Element)) {
    return true;
  }

  const distance = element.scrollHeight - element.clientHeight - element.scrollTop;

  return distance < threshold;
}
