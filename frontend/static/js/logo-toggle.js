import { runWhenDocumentReady } from "./utils/dom-ready.js";

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

function isFocusVisible(anchor) {
  if (typeof document === "undefined") {
    return false;
  }
  return (
    anchor === document.activeElement && anchor.matches(":focus-visible")
  );
}

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
  anchor.removeAttribute("data-logo-pressed");
  const settle = () => {
    const shouldHover = anchor.matches(":hover") || isFocusVisible(anchor);
    applyState(img, shouldHover ? "hover" : "default");
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(settle);
  } else {
    settle();
  }
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

  const setPressed = () => anchor.setAttribute("data-logo-pressed", "true");
  const clearPressed = () => anchor.removeAttribute("data-logo-pressed");

  const toDefault = () => {
    clearPressed();
    applyState(img, "default");
  };
  const toHover = () => {
    clearPressed();
    applyState(img, "hover");
  };
  const toActive = () => {
    setPressed();
    applyState(img, "active");
  };
  const reset = () => {
    clearPressed();
    releaseState(anchor, img);
  };

  const handleFocus = () => {
    if (isFocusVisible(anchor)) {
      toHover();
      return;
    }
    toDefault();
  };

  anchor.addEventListener("pointerenter", toHover);
  anchor.addEventListener("pointerleave", reset);
  anchor.addEventListener("pointerdown", (event) => {
    if (event.button === 0) {
      toActive();
    }
  });
  anchor.addEventListener("pointerup", reset);
  anchor.addEventListener("pointercancel", reset);
  anchor.addEventListener("focus", handleFocus);
  anchor.addEventListener("blur", toDefault);
  anchor.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      toActive();
    }
  });
  anchor.addEventListener("keyup", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      handleFocus();
    }
  });

  anchor.addEventListener("htmx:beforeRequest", () => {
    setPressed();
    applyState(img, "active");
  });

  const clearAfterRequest = () => {
    reset();
  };

  anchor.addEventListener("htmx:afterRequest", clearAfterRequest);
  anchor.addEventListener("htmx:requestError", clearAfterRequest);
  anchor.addEventListener("htmx:responseError", clearAfterRequest);
  anchor.addEventListener("htmx:sendError", clearAfterRequest);

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
    initLogoToggles();
  });
}

runWhenDocumentReady(() => {
  initLogoToggles();
  setupHtmxHooks();
});

if (typeof window !== "undefined") {
  window.addEventListener("pageshow", () => {
    refreshAll();
  });
}
