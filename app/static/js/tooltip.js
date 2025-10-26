import { createPopover } from "./popover.js";

let tooltipEl;
let innerEl;
let popoverController;
let currentTarget;
let lastRect;
let initialized = false;

function ensureEl() {
  if (tooltipEl && tooltipEl.isConnected) return;
  tooltipEl = document.createElement('div');
  tooltipEl.className = 'tooltip';
  tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
  innerEl = tooltipEl.querySelector('.tooltip-inner');
  document.body.appendChild(tooltipEl);
  tooltipEl.hidden = true;
}

function show(el) {
  ensureEl();
  hide();
  innerEl.textContent = el.dataset.tooltipTitle || '';
  Object.assign(tooltipEl.style, {
    top: '',
    left: '',
    position: '',
  });
  popoverController = createPopover(el, tooltipEl, {
    animation: null,
    closeOnOutside: false,
    closeOnEscape: false,
    popperOptions: {
      placement: 'bottom',
      strategy: 'fixed',
      modifiers: [{ name: 'offset', options: { offset: [0, 8] } }],
    },
    onShow: () => {
      tooltipEl.classList.add('visible');
    },
    onHide: () => {
      tooltipEl.classList.remove('visible');
      const rect = tooltipEl.getBoundingClientRect();
      lastRect = { top: rect.top, left: rect.left };
    },
    onHidden: () => {
      tooltipEl.hidden = true;
      if (lastRect) {
        Object.assign(tooltipEl.style, {
          top: `${lastRect.top}px`,
          left: `${lastRect.left}px`,
          position: 'fixed',
        });
        lastRect = null;
      }
    },
  });
  popoverController.show();
  currentTarget = el;
}

function hide() {
  if (!popoverController) {
    if (tooltipEl) {
      tooltipEl.classList.remove('visible');
      tooltipEl.hidden = true;
    }
    currentTarget = null;
    lastRect = null;
    return Promise.resolve();
  }
  const controller = popoverController;
  popoverController = null;
  currentTarget = null;
  return controller.hide().finally(() => {
    controller.destroy();
  });
}

function cleanup() {
  const pending = hide();
  const removeEl = () => {
    if (tooltipEl) {
      tooltipEl.remove();
      tooltipEl = null;
      innerEl = null;
      lastRect = null;
    }
  };
  if (pending && typeof pending.then === 'function') {
    pending.finally(removeEl);
  } else {
    removeEl();
  }
}

export function initTooltips() {
  if (initialized) return;
  initialized = true;

  const getTrigger = (el) => {
    const trigger = el.closest('[data-tooltip-title]');
    return trigger && !trigger.classList.contains('active') ? trigger : null;
  };

  document.addEventListener('mouseover', (e) => {
    const trigger = getTrigger(e.target);
    if (!trigger || trigger === currentTarget) return;
    show(trigger);
  });

  document.addEventListener('mouseout', (e) => {
    if (!currentTarget) return;
    if (e.relatedTarget && currentTarget.contains(e.relatedTarget)) return;
    if (getTrigger(e.target) !== currentTarget) return;
    hide();
  });

  document.addEventListener('focusin', (e) => {
    const trigger = getTrigger(e.target);
    if (trigger) show(trigger);
  });

  document.addEventListener('focusout', (e) => {
    if (currentTarget && getTrigger(e.target) === currentTarget) hide();
  });

  document.addEventListener('click', hide);

  if (window.htmx) {
    document.body.addEventListener('htmx:beforeHistorySave', cleanup);
  }

  window.addEventListener('pagehide', cleanup);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cleanup();
  });
}

initTooltips();

