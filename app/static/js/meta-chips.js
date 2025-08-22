function setupAddButton(container) {
  if (container.dataset.popInit === "1") return;
  container.dataset.popInit = "1";

  const btn = container.querySelector('.add-tag-btn');
  const pop = container.querySelector('.tag-popover');
  const form = pop?.querySelector('form');
  const input = form?.querySelector('input[name="tag"]');
  const submit = form?.querySelector('button[type="submit"]');
  const suggestions = pop?.querySelector('.tag-suggestions');
  const close = pop?.querySelector('.overlay-close');
  const tagContainer = container.querySelector('.meta-tags');
  if (!btn || !pop || !form || !input || !submit || !tagContainer) return;

  const normalize = (v) => (v.startsWith('#') ? v : `#${v}`);

  const updateState = () => {
    const raw = input.value.trim();
    if (!raw) {
      submit.disabled = true;
      return;
    }
    const tag = normalize(raw).toLowerCase();
    const existing = Array.from(tagContainer.querySelectorAll('.chip-label')).map((el) => el.textContent.trim().toLowerCase());
    submit.disabled = existing.includes(tag);
  };
  input.addEventListener('input', updateState);
  updateState();

  let instance;

  const hide = () => {
    if (pop.hidden) return;
    if (suggestions) {
      suggestions.innerHTML = "";
      delete suggestions.dataset.loaded;
    }
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
    if (!pop.contains(e.target) && e.target !== btn) hide();
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
    if (suggestions && !suggestions.dataset.loaded) {
      htmx.trigger(suggestions, 'tag-popover:show');
    }
  });

  close?.addEventListener('click', (e) => {
    e.preventDefault();
    hide();
  });

  suggestions?.addEventListener('htmx:afterSwap', () => {
    if (suggestions.innerHTML.trim()) {
      suggestions.dataset.loaded = '1';
    } else {
      delete suggestions.dataset.loaded;
    }
  });

  form.addEventListener('htmx:configRequest', (evt) => {
    let value = input.value.trim();
    if (!value) {
      evt.preventDefault();
      return;
    }
    value = normalize(value);
    input.value = value;
    const existing = Array.from(tagContainer.querySelectorAll('.chip-label')).map((el) => el.textContent.trim().toLowerCase());
    if (existing.includes(value.toLowerCase())) {
      evt.preventDefault();
      return;
    }
    evt.detail.parameters.tag = value;
  });

  form.addEventListener('htmx:afterRequest', () => {
    form.reset();
    updateState();
    hide();
  });

  container.addEventListener('htmx:afterSwap', (evt) => {
    if (evt.target === tagContainer) {
      const chip = tagContainer.lastElementChild;
      if (chip?.classList.contains('meta-chip')) {
        chip.classList.add('chip-enter');
        chip.addEventListener('animationend', () => chip.classList.remove('chip-enter'), { once: true });
        const label = chip.querySelector('.chip-label')?.textContent.trim().toLowerCase();
        if (label) {
          pop.querySelectorAll('.tag-suggestion').forEach((btn) => {
            if (btn.textContent.trim().toLowerCase() === label) {
              btn.remove();
            }
          });
        }
      }
      updateState();
    } else if (evt.target.classList?.contains('chip-tombstone')) {
      evt.target.remove();
      updateState();
    }
  });
}

export function initTagPopovers(root = document) {
  root.querySelectorAll('.meta-chips').forEach(setupAddButton);
}
