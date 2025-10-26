export function setTimezoneCookie() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  document.cookie = `tz=${tz}; path=/`;
  return tz;
}

