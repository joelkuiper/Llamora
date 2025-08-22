let tooltipEl;
let innerEl;
let popperInstance;
let currentTarget;
let initialized = false;

function ensureEl() {
  if (tooltipEl && tooltipEl.isConnected) return;
  tooltipEl = document.createElement('div');
  tooltipEl.className = 'tooltip';
  tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
  innerEl = tooltipEl.querySelector('.tooltip-inner');
  document.body.appendChild(tooltipEl);
}

function show(el) {
  ensureEl();
  hide();
  innerEl.textContent = el.dataset.tooltipTitle || '';
  tooltipEl.style.top = '';
  tooltipEl.style.left = '';
  popperInstance = Popper.createPopper(el, tooltipEl, {
    placement: 'bottom',
    strategy: 'fixed',
    modifiers: [
      { name: 'offset', options: { offset: [0, 8] } },
    ],
  });
  tooltipEl.classList.add('visible');
  currentTarget = el;
}

function hide() {
  if (!tooltipEl) return;
  tooltipEl.classList.remove('visible');
  if (popperInstance) {
    const { top, left } = tooltipEl.getBoundingClientRect();
    popperInstance.destroy();
    popperInstance = null;
    Object.assign(tooltipEl.style, {
      top: `${top}px`,
      left: `${left}px`,
      position: 'fixed',
    });
  }
  currentTarget = null;
}

function cleanup() {
  hide();
  if (tooltipEl) {
    tooltipEl.remove();
    tooltipEl = null;
    innerEl = null;
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

