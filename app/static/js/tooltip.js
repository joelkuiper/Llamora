export function initTooltips(root = document) {
  const elements = root.querySelectorAll('[data-tooltip-title]');
  elements.forEach((el) => {
    if (el.dataset.tooltipInit === '1') return;
    el.dataset.tooltipInit = '1';

    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.innerHTML = `<div class="tooltip-inner">${el.dataset.tooltipTitle}</div>`;
    tooltip.hidden = true;

    document.body.appendChild(tooltip);

    const instance = Popper.createPopper(el, tooltip, {
      placement: 'bottom',
      modifiers: [
        { name: 'offset', options: { offset: [0, 8] } },
      ],
    });

    const show = () => {
      tooltip.hidden = false;
      instance.update();
    };
    const hide = () => {
      tooltip.hidden = true;
    };

    el.addEventListener('mouseenter', show);
    el.addEventListener('focus', show);
    el.addEventListener('mouseleave', hide);
    el.addEventListener('blur', hide);

    // store cleanup to avoid duplicated listeners and orphaned tooltips
    el._tooltipCleanup = () => {
      el.removeEventListener('mouseenter', show);
      el.removeEventListener('focus', show);
      el.removeEventListener('mouseleave', hide);
      el.removeEventListener('blur', hide);
      instance.destroy();
      tooltip.remove();
      delete el.dataset.tooltipInit;
      delete el._tooltipCleanup;
    };
  });
}

// Initial run
initTooltips();

// Re-run after HTMX swaps
if (window.htmx) {
  document.body.addEventListener('htmx:load', (evt) => initTooltips(evt.target));
  document.body.addEventListener('htmx:beforeCleanupElement', (evt) => {
    const el = evt.target;
    if (el._tooltipCleanup) {
      el._tooltipCleanup();
    }
  });
}
