function sanitizeTarget(value) {
  if (!value) {
    return null;
  }

  try {
    const url = new URL(value, window.location.origin);
    const path = url.pathname;
    if (path.startsWith("/login") || path.startsWith("/logout")) {
      return null;
    }
    if (path.startsWith("/profile")) {
      return "/";
    }
    return `${path}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

function initProfileNavigation(force = false) {
  document.title = "Profile";
  const backBtn = document.getElementById("profile-back");
  if (!backBtn) {
    return;
  }

  if (backBtn.dataset.profileBackInit === "true" && !force) {
    return;
  }

  if (backBtn._profileBackController) {
    backBtn._profileBackController.abort();
  }
  const abortController = new AbortController();
  backBtn._profileBackController = abortController;

  let target = sanitizeTarget(sessionStorage.getItem("profile-return"));
  if (!target) {
    target = sanitizeTarget(document.referrer) ?? "/";
    sessionStorage.setItem("profile-return", target);
  }
  if (!target) {
    target = "/";
  }
  backBtn.addEventListener(
    "click",
    () => {
    sessionStorage.removeItem("profile-return");
    window.location.href = target;
    },
    { signal: abortController.signal },
  );

  backBtn.dataset.profileBackInit = "true";
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initProfileNavigation, { once: true });
  } else {
    initProfileNavigation();
  }
}

window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    initProfileNavigation(true);
  }
});
