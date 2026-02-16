export const parseTagFromPath = (pathname) => {
  if (!pathname || !pathname.startsWith("/t/")) return "";
  const raw = pathname.slice(3);
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

export const isTagsPath = (pathname) => pathname === "/t" || pathname.startsWith("/t/");

export const readTagFromUrl = () => {
  const url = new URL(window.location.href);
  const fromPath = parseTagFromPath(url.pathname);
  if (fromPath) return fromPath;
  return String(url.searchParams.get("tag") || "").trim();
};

export const updateUrlSortParams = (rawUrl, sortKind, sortDir) => {
  if (!rawUrl) return rawUrl;
  try {
    const current = new URL(window.location.href);
    const next = new URL(rawUrl, current.origin);
    next.searchParams.set("sort_kind", sortKind);
    next.searchParams.set("sort_dir", sortDir);
    return `${next.pathname}${next.search}${next.hash}`;
  } catch {
    return rawUrl;
  }
};

export const updateUrlSort = ({ sortKind, sortDir, selectedTag } = {}) => {
  const detailTag = String(selectedTag || "").trim();
  const url = new URL(window.location.href);
  const tag = detailTag || parseTagFromPath(url.pathname) || "";
  url.pathname = tag ? `/t/${encodeURIComponent(tag)}` : "/t";
  if (sortKind) {
    url.searchParams.set("sort_kind", sortKind);
  }
  if (sortDir) {
    url.searchParams.set("sort_dir", sortDir);
  }
  url.searchParams.delete("tag");
  url.searchParams.delete("target");
  window.history.replaceState(window.history.state, "", url.toString());
};

export const getTagsLocationKey = (tagOverride) => {
  const url = new URL(window.location.href);
  const pathname = url.pathname;
  if (!tagOverride && !isTagsPath(pathname)) {
    return "";
  }
  const tag =
    tagOverride || parseTagFromPath(pathname) || String(url.searchParams.get("tag") || "").trim();
  const nextPath = tag ? `/t/${encodeURIComponent(tag)}` : "/t";
  const params = new URLSearchParams(url.search);
  params.delete("tag");
  params.delete("target");
  const qs = params.toString();
  return qs ? `${nextPath}?${qs}` : nextPath;
};

export const getTagsDay = () => {
  const fromDom = String(document.querySelector("#tags-view")?.dataset?.day || "").trim();
  if (fromDom) return fromDom;
  const url = new URL(window.location.href);
  const fromQuery = String(url.searchParams.get("day") || "").trim();
  if (fromQuery) return fromQuery;
  const match = url.pathname.match(/\/d\/(\d{4}-\d{2}-\d{2})$/);
  return String(match?.[1] || "").trim();
};
