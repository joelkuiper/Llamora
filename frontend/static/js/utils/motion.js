const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function getMotionMediaQuery() {
  if (typeof window === "undefined") {
    return null;
  }
  if (typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia(MOTION_QUERY);
}

export function prefersReducedMotion() {
  const query = getMotionMediaQuery();
  return Boolean(query?.matches);
}

export function motionSafeBehavior(behavior = "smooth", reduceMotion = prefersReducedMotion()) {
  if (!reduceMotion) {
    return behavior;
  }
  return behavior === "smooth" ? "auto" : behavior;
}
