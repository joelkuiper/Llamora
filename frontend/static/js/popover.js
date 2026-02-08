/* global Popper */
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
  remove.forEach((cls) => el.classList.remove(cls));
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

  let popperInstance = null;
  let open = false;
  let globalListeners = null;
  let version = 0;

  const ensurePopper = () => {
    if (!popperInstance) {
      popperInstance = Popper.createPopper(trigger, popover, {
        placement,
        ...popperOptions,
      });
    }
    popperInstance.update();
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
    const currentVersion = version;
    onBeforeShow?.();
    popover.hidden = false;
    ensurePopper();
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
    if (popperInstance) {
      popperInstance.destroy();
      popperInstance = null;
    }
  };

  const update = () => {
    if (popperInstance) {
      popperInstance.update();
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
