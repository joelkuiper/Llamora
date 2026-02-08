import { createPopover } from "../popover.js";
import { createListenerBag } from "./events.js";

function withOffset(modifiers = [], offset = [0, 8]) {
  const existingOffset = modifiers.find((mod) => mod?.name === "offset");
  if (existingOffset) {
    return modifiers;
  }
  return [...modifiers, { name: "offset", options: { offset } }];
}

export function createTooltipPopover(trigger, popover, options = {}) {
  const { offset = [0, 8], popperOptions = {}, onHidden, ...popoverOptions } = options;

  const listeners = createListenerBag();
  let destroyed = false;

  const controller = createPopover(trigger, popover, {
    animation: null,
    closeOnOutside: false,
    closeOnEscape: false,
    popperOptions: {
      strategy: "fixed",
      ...popperOptions,
      modifiers: withOffset(popperOptions.modifiers || [], offset),
    },
    onHidden: () => {
      onHidden?.();
    },
    ...popoverOptions,
  });

  const cleanup = () => {
    if (destroyed) return Promise.resolve();
    destroyed = true;
    listeners.abort();
    const result = controller.hide?.();
    const finalize = () => controller.destroy?.();
    if (result && typeof result.then === "function") {
      return result.finally(finalize);
    }
    finalize();
    return Promise.resolve();
  };

  listeners.add(window, "pagehide", cleanup);
  listeners.add(document, "visibilitychange", () => {
    if (document.hidden) {
      cleanup();
    }
  });
  if (window.htmx) {
    listeners.add(document.body, "htmx:beforeHistorySave", cleanup);
  }

  return { controller, cleanup };
}
