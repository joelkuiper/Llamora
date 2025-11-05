const SCHEME_QUERY = "(prefers-color-scheme: dark)";
const darkScheme = typeof window !== "undefined" && window.matchMedia
  ? window.matchMedia(SCHEME_QUERY)
  : null;

const DATA_KEYS = {
  light: {
    default: "lightDefault",
    hover: "lightHover",
    active: "lightActive",
  },
  dark: {
    default: "darkDefault",
    hover: "darkHover",
    active: "darkActive",
  },
};

let schemeListenerRegistered = false;

function currentPalette() {
  if (darkScheme && typeof darkScheme.matches === "boolean") {
    return darkScheme.matches ? "dark" : "light";
  }
  return "light";
}

function pickSource(img, state) {
  const palette = currentPalette();
  const paletteKeys = DATA_KEYS[palette] ?? DATA_KEYS.light;
  const key = paletteKeys[state] ?? paletteKeys.default;
  const fallbackKey = paletteKeys.default;
  const next = img.dataset[key] || img.dataset[fallbackKey];
  return next || img.getAttribute("src") || "";
}

function applyState(img, state) {
  const next = pickSource(img, state);
  if (next && img.getAttribute("src") !== next) {
    img.setAttribute("src", next);
  }
  img.dataset.logoState = state;
}

function releaseState(anchor, img) {
  const shouldHover = anchor.matches(":hover") || anchor.matches(":focus");
  applyState(img, shouldHover ? "hover" : "default");
}

function initLogo(anchor) {
  if (!(anchor instanceof HTMLElement)) {
    return;
  }

  if (anchor.dataset.logoInit === "true") {
    return;
  }

  const img = anchor.querySelector("img.logo");
  if (!(img instanceof HTMLImageElement)) {
    return;
  }

  const toDefault = () => applyState(img, "default");
  const toHover = () => applyState(img, "hover");
  const toActive = () => applyState(img, "active");
  const reset = () => releaseState(anchor, img);

  anchor.addEventListener("pointerenter", toHover);
  anchor.addEventListener("pointerleave", toDefault);
  anchor.addEventListener("pointerdown", (event) => {
    if (event.button === 0) {
      toActive();
    }
  });
  anchor.addEventListener("pointerup", reset);
  anchor.addEventListener("pointercancel", reset);
  anchor.addEventListener("focus", toHover);
  anchor.addEventListener("blur", toDefault);
  anchor.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      toActive();
    }
  });
  anchor.addEventListener("keyup", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      toHover();
    }
  });

  applyState(img, img.dataset.logoState ?? "default");

  anchor.dataset.logoInit = "true";
}

function refreshAll() {
  document.querySelectorAll("[data-logo-toggle]").forEach((anchor) => {
    const img = anchor.querySelector("img.logo");
    if (!(img instanceof HTMLImageElement)) {
      return;
    }
    const state = img.dataset.logoState ?? "default";
    applyState(img, state);
  });
}

function ensureSchemeListener() {
  if (!darkScheme || schemeListenerRegistered) {
    return;
  }
  darkScheme.addEventListener("change", () => {
    refreshAll();
  });
  schemeListenerRegistered = true;
}

export function initLogoToggles() {
  ensureSchemeListener();
  document.querySelectorAll("[data-logo-toggle]").forEach((anchor) => {
    initLogo(anchor);
  });
  refreshAll();
}

export function refreshLogoToggles() {
  refreshAll();
}

function setupHtmxHooks() {
  if (typeof document === "undefined" || !document.body) {
    return;
  }
  document.body.addEventListener("htmx:afterSwap", () => {
    initLogoToggles();
  });
  document.body.addEventListener("htmx:historyRestore", () => {
    refreshAll();
  });
}

function initWhenReady() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      initLogoToggles();
      setupHtmxHooks();
    }, { once: true });
    return;
  }
  initLogoToggles();
  setupHtmxHooks();
}

initWhenReady();

if (typeof window !== "undefined") {
  window.addEventListener("pageshow", () => {
    refreshAll();
  });
}
