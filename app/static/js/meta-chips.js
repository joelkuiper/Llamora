function setupAddButton(container) {
  if (container.dataset.popInit === "1") return;
  container.dataset.popInit = "1";

  const btn = container.querySelector('.add-tag-btn');
  const pop = container.querySelector('.tag-popover');
  const form = pop?.querySelector('form');
  if (!btn || !pop || !form) return;

  let instance;

  const hide = () => {
    if (pop.hidden) return;
    pop.classList.remove('tp-open');
    const clear = (e) => {
      if (e && e.target !== pop) return;
      pop.hidden = true;
      pop.removeEventListener('transitionend', clear);
    };
    pop.addEventListener('transitionend', clear);
    document.removeEventListener('click', outside, true);
    document.removeEventListener('keydown', onKey);
  };

  const outside = (e) => {
    if (!container.contains(e.target)) hide();
  };

  const onKey = (e) => {
    if (e.key === 'Escape') hide();
  };

  btn.addEventListener('click', () => {
    if (!pop.hidden) { hide(); return; }
    pop.hidden = false;
    requestAnimationFrame(() => pop.classList.add('tp-open'));
    instance = instance || Popper.createPopper(btn, pop, { placement: 'bottom' });
    instance.update();
    pop.querySelector('input')?.focus();
    document.addEventListener('click', outside, true);
    document.addEventListener('keydown', onKey);
  });

  form.addEventListener('htmx:configRequest', (evt) => {
    const input = form.querySelector('input[name="tag"]');
    if (!input) return;
    let value = input.value.trim();
    if (value && !value.startsWith('#')) {
      value = `#${value}`;
      input.value = value;
    }
    evt.detail.parameters.tag = value;
  });

  form.addEventListener('htmx:afterRequest', () => {
    form.reset();
    hide();
  });

  container.addEventListener('htmx:afterSwap', (evt) => {
    if (evt.target === pop) {
      const chip = pop.previousElementSibling;
      if (chip?.classList.contains('meta-chip')) {
        chip.classList.add('chip-enter');
        chip.addEventListener('animationend', () => chip.classList.remove('chip-enter'), { once: true });
      }
    } else if (evt.target.classList?.contains('chip-tombstone')) {
      evt.target.remove();
    }
  });
}

export function initTagPopovers(root = document) {
  root.querySelectorAll('.meta-chips').forEach(setupAddButton);
}
