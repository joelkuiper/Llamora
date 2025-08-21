function setupAddButton(container) {
  if (container.dataset.popInit === "1") return;
  container.dataset.popInit = "1";

  const btn = container.querySelector('.add-tag-btn');
  const pop = container.querySelector('.tag-popover');
  const form = pop?.querySelector('form');
  const input = form?.querySelector('input[name="tag"]');
  const submit = form?.querySelector('button[type="submit"]');
  if (!btn || !pop || !form || !input || !submit) return;

  const updateState = () => {
    submit.disabled = !input.value.trim();
  };
  input.addEventListener('input', updateState);
  updateState();

  let instance;

  const hide = () => {
    if (pop.hidden) return;
    pop.classList.remove('tp-open');
    btn.classList.remove('active');
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
    btn.classList.add('active');
    instance = instance || Popper.createPopper(btn, pop, { placement: 'bottom' });
    instance.update();
    input.focus();
    document.addEventListener('click', outside, true);
    document.addEventListener('keydown', onKey);
  });

  form.addEventListener('htmx:configRequest', (evt) => {
    let value = input.value.trim();
    if (!value) {
      evt.preventDefault();
      return;
    }
    if (!value.startsWith('#')) {
      value = `#${value}`;
      input.value = value;
    }
    evt.detail.parameters.tag = value;
  });

  form.addEventListener('htmx:afterRequest', () => {
    form.reset();
    updateState();
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
