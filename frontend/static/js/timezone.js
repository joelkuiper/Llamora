export function setTimezoneCookie() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const encodedTz = encodeURIComponent(tz);
  const cookieDirectives = [
    `tz=${encodedTz}`,
    "path=/",
    "SameSite=Lax",
    `Max-Age=${60 * 60 * 24 * 30}`,
  ];

  if (window.location.protocol === "https:") {
    cookieDirectives.push("Secure");
  }

  document.cookie = cookieDirectives.join("; ");
  return tz;
}

