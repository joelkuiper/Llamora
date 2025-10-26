import { createPopover } from "./popover.js";

function setupAddButton(container) {
  if (container.dataset.popInit === "1") return;
  container.dataset.popInit = "1";

  const btn = container.querySelector('.add-tag-btn');
  const pop = container.querySelector('.tag-popover');
  const form = pop?.querySelector('form');
  const input = form?.querySelector('input[name="tag"]');
  const submit = form?.querySelector('button[type="submit"]');
  const panel = pop?.querySelector('.tp-content');
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

  const popover = createPopover(btn, pop, {
    getPanel: () => panel,
    onShow: () => {
      btn.classList.add('active');
      if (suggestions && !suggestions.dataset.loaded) {
        htmx.trigger(suggestions, 'tag-popover:show');
      }
      input.focus();
    },
    onHide: () => {
      btn.classList.remove('active');
    },
    onHidden: () => {
      if (suggestions) {
        suggestions.innerHTML = "";
        delete suggestions.dataset.loaded;
      }
    },
  });

  btn.addEventListener('click', () => {
    if (popover.isOpen) {
      popover.hide();
      return;
    }
    popover.show();
  });

  close?.addEventListener('click', (e) => {
    e.preventDefault();
    popover.hide();
  });

  suggestions?.addEventListener('htmx:afterSwap', () => {
    if (suggestions.innerHTML.trim()) {
      suggestions.dataset.loaded = '1';
    } else {
      delete suggestions.dataset.loaded;
    }
    popover.update();
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
    popover.hide();
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
