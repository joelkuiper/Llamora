function setupAddButton(container) {
  const btn = container.querySelector('.add-tag-btn');
  const pop = container.querySelector('.tag-popover');
  if (!btn || !pop) return;
  let instance;
  btn.addEventListener('click', () => {
    pop.hidden = !pop.hidden;
    if (!pop.hidden) {
      instance = instance || Popper.createPopper(btn, pop, { placement: 'bottom' });
      instance.update();
      pop.querySelector('input')?.focus();
    }
  });
}

export function initTagPopovers(root = document) {
  root.querySelectorAll('.meta-chips').forEach(setupAddButton);
}
