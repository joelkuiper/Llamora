import { autoUpdate, computePosition, flip, offset as offsetMiddleware, shift } from "./vendor/setup-globals.js";
import { createListenerBag } from "./utils/events.js";

const DEFAULT_TIMEOUT = 250;

const defaultAnimation = {
  popoverEnter: "fade-enter",
  popoverExit: "fade-exit",
  panelEnter: "pop-enter",
  panelExit: "pop-exit",
};

function isNode(target) {
  return typeof Node !== "undefined" ? target instanceof Node : !!target;
}

function runAnimation(el, className, remove = []) {
  if (!el) {
    return Promise.resolve();
  }
  remove.forEach((cls) => {
    el.classList.remove(cls);
  });
  // Force reflow to restart the animation when classes reapply.
  void el.getBoundingClientRect();
  el.classList.add(className);
  return new Promise((resolve) => {
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      el.classList.remove(className);
      resolve();
    };
    el.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, DEFAULT_TIMEOUT);
  });
}

function applyEnterAnimation(popover, panel, animation) {
  if (!animation) return;
  const { popoverEnter, popoverExit, panelEnter, panelExit } = animation;
  runAnimation(popover, popoverEnter, [popoverEnter, popoverExit]);
  runAnimation(panel, panelEnter, [panelEnter, panelExit]);
}

function applyExitAnimation(popover, panel, animation) {
  if (!animation) return Promise.resolve();
  const { popoverEnter, popoverExit, panelEnter, panelExit } = animation;
  return Promise.all([
    runAnimation(popover, popoverExit, [popoverEnter, popoverExit]),
    runAnimation(panel, panelExit, [panelEnter, panelExit]),
  ]);
}

export function createPopover(trigger, popover, options = {}) {
  const {
    placement = "bottom",
    popperOptions = {},
    getPanel = () => popover.querySelector(".tp-content"),
    animation = defaultAnimation,
    closeOnOutside = true,
    closeOnEscape = true,
    isEventOutside = (event) => {
      const target = event.target;
      return (
        isNode(target) && !popover.contains(target) && (trigger ? !trigger.contains(target) : true)
      );
    },
    onBeforeShow,
    onShow,
    onHide,
    onHidden,
  } = options;

  let autoUpdateCleanup = null;
  let open = false;
  let globalListeners = null;
  let version = 0;

  const hasMiddleware = (middleware, name) =>
    Array.isArray(middleware) && middleware.some((item) => item?.name === name);

  const extractOffset = (modifiers = []) => {
    const offsetMod = modifiers.find((mod) => mod?.name === "offset");
    return offsetMod?.options?.offset;
  };

  const buildFloatingOptions = () => {
    const strategy = popperOptions.strategy || "absolute";
    const middleware = Array.isArray(popperOptions.middleware)
      ? [...popperOptions.middleware]
      : [];
    const legacyOffset = extractOffset(popperOptions.modifiers || []);
    if (legacyOffset && !hasMiddleware(middleware, "offset")) {
      middleware.unshift(offsetMiddleware(legacyOffset));
    }
    if (!hasMiddleware(middleware, "flip")) {
      middleware.push(flip());
    }
    if (!hasMiddleware(middleware, "shift")) {
      middleware.push(shift({ padding: 8 }));
    }
    return { placement, strategy, middleware };
  };

  const applyPosition = async () => {
    if (!trigger || !popover) return;
    if (typeof computePosition !== "function") return;
    const currentVersion = version;
    const floatingOptions = buildFloatingOptions();
    let result;
    try {
      result = await computePosition(trigger, popover, floatingOptions);
    } catch (_error) {
      return;
    }
    const { x, y, placement: resolvedPlacement } = result;
    if (!open || currentVersion !== version) return;
    popover.style.position = floatingOptions.strategy;
    popover.style.left = `${x}px`;
    popover.style.top = `${y}px`;
    popover.style.transform = "";
    popover.setAttribute("data-floating-ui-placement", resolvedPlacement);
  };

  const stopAutoUpdate = () => {
    autoUpdateCleanup?.();
    autoUpdateCleanup = null;
  };

  const startAutoUpdate = () => {
    stopAutoUpdate();
    if (typeof autoUpdate !== "function") {
      return;
    }
    autoUpdateCleanup = autoUpdate(trigger, popover, () => {
      applyPosition();
    });
  };

  const outsideHandler = (event) => {
    if (isEventOutside(event)) {
      hide();
    }
  };

  const keyHandler = (event) => {
    if (event.key === "Escape") {
      hide();
    }
  };

  const addGlobalListeners = () => {
    globalListeners?.abort();
    globalListeners = createListenerBag();
    if (closeOnOutside) {
      globalListeners.add(document, "click", outsideHandler, true);
    }
    if (closeOnEscape) {
      globalListeners.add(document, "keydown", keyHandler);
    }
  };

  const removeGlobalListeners = () => {
    globalListeners?.abort();
    globalListeners = null;
  };

  const animateOpen = () => {
    const panel = getPanel ? getPanel() : null;
    applyEnterAnimation(popover, panel, animation);
  };

  const animateClose = () => {
    const panel = getPanel ? getPanel() : null;
    return applyExitAnimation(popover, panel, animation);
  };

  const show = () => {
    if (open) return;
    version += 1;
    onBeforeShow?.();
    popover.hidden = false;
    applyPosition();
    startAutoUpdate();
    open = true;
    addGlobalListeners();
    animateOpen();
    onShow?.();
  };

  const hide = () => {
    if (!open) return Promise.resolve();
    const hideVersion = version;
    open = false;
    removeGlobalListeners();
    onHide?.();
    const result = animateClose();
    const finalize = () => {
      if (hideVersion === version) {
        popover.hidden = true;
        stopAutoUpdate();
        onHidden?.();
      }
    };
    return Promise.resolve(result).then(finalize);
  };

  const destroy = () => {
    removeGlobalListeners();
    if (open) {
      popover.hidden = true;
      open = false;
    }
    version += 1;
    stopAutoUpdate();
    popover.removeAttribute("data-floating-ui-placement");
    popover.style.left = "";
    popover.style.top = "";
    popover.style.transform = "";
  };

  const update = () => {
    if (open) {
      applyPosition();
    }
  };

  const animateOpenPublic = () => {
    if (!open) return;
    animateOpen();
  };

  return {
    show,
    hide,
    destroy,
    update,
    animateOpen: animateOpenPublic,
    get isOpen() {
      return open;
    },
  };
}

export function playPopoverEnter(popover, panel, animation = defaultAnimation) {
  applyEnterAnimation(popover, panel, animation);
}

export function playPopoverExit(popover, panel, animation = defaultAnimation) {
  return applyExitAnimation(popover, panel, animation);
}
