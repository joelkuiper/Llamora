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
    pop.classList.add('tp-closing');
    const clear = () => {
      pop.hidden = true;
      pop.classList.remove('tp-closing');
    };
    clear();
    // pop.addEventListener('animationend', clear, { once: true });
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
    pop.classList.add('tp-enter');
    pop.addEventListener('animationend', () => pop.classList.remove('tp-enter'), { once: true });
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
}

export function initTagPopovers(root = document) {
  root.querySelectorAll('.meta-chips').forEach(setupAddButton);
}
