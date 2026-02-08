const AUTOBOUND_ATTR = "data-autosize-bound";
const SKIP_BLUR_CANCEL_ATTR = "data-skip-blur-cancel";
const ENTRY_HEIGHT_ATTR = "data-entry-main-height";
const EMPTY_EDIT_CLASS = "entry-edit-empty";
const SAVE_DISABLED_ATTR = "aria-disabled";
const SAVE_DISABLED_DATA = "data-save-disabled";
const CARET_PLACED_ATTR = "data-caret-placed";

const EDIT_FLOW = {
  blur: "save",
  escape: "cancel",
  toggle: "cancel",
  saveShortcut: "save",
};

function getScrollContainer(node) {
  let current = node?.parentElement || null;
  while (current && current !== document.body) {
    const styles = window.getComputedStyle(current);
    if (
      (styles.overflowY === "auto" || styles.overflowY === "scroll") &&
      current.scrollHeight > current.clientHeight
    ) {
      return current;
    }
    current = current.parentElement;
  }
  return document.scrollingElement || document.documentElement;
}

function getEntryMainHeight(entryMain) {
  if (!(entryMain instanceof Element)) return null;
  return entryMain.getBoundingClientRect().height || null;
}

function findEntryMainFromDetail(detail) {
  const elt = detail?.elt instanceof Element ? detail.elt : null;
  const target = detail?.target instanceof Element ? detail.target : null;
  if (elt?.classList?.contains("entry-main")) return elt;
  if (target?.classList?.contains("entry-main")) return target;
  const fromElt = elt?.querySelector?.(".entry-main") || null;
  if (fromElt) return fromElt;
  const fromTarget = target?.querySelector?.(".entry-main") || null;
  if (fromTarget) return fromTarget;
  return null;
}

function resizeTextarea(textarea) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  const scrollContainer = getScrollContainer(textarea);
  const prevScrollTop = scrollContainer instanceof Element ? scrollContainer.scrollTop : null;
  textarea.style.height = "auto";
  const styles = window.getComputedStyle(textarea);
  const max = parseFloat(styles.maxHeight || "");
  const min = parseFloat(styles.minHeight || "");
  const height = textarea.scrollHeight;
  let desired = height;
  if (Number.isFinite(min) && min > 0) {
    desired = Math.max(desired, min);
  }
  if (Number.isFinite(max) && max > 0) {
    desired = Math.min(desired, max);
  }
  textarea.style.height = `${desired}px`;
  if (Number.isFinite(max) && max > 0) {
    textarea.style.overflowY = desired >= max ? "auto" : "hidden";
  } else {
    textarea.style.overflowY = "hidden";
  }
  if (
    scrollContainer instanceof Element &&
    prevScrollTop !== null &&
    scrollContainer.scrollTop !== prevScrollTop
  ) {
    scrollContainer.scrollTop = prevScrollTop;
  }
}

function placeCaretAtEnd(textarea, force = false) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  if (!force && textarea.getAttribute(CARET_PLACED_ATTR) === "true") return;
  const length = textarea.value.length;
  try {
    textarea.setSelectionRange(length, length);
  } catch (_err) {
    return;
  }
  textarea.setAttribute(CARET_PLACED_ATTR, "true");
}

function scheduleCaretPlacement(textarea, force = false) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      placeCaretAtEnd(textarea, force);
    });
  });
}

function submitEdit(form) {
  if (!form || form.classList.contains("htmx-request")) return;
  const textarea = form.querySelector(".entry-edit-area");
  if (textarea instanceof HTMLTextAreaElement) {
    if (!textarea.value.trim()) {
      return;
    }
  }
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.submit();
  }
}

function cancelEdit(form) {
  if (!form) return;
  const cancel = form.querySelector(".entry-edit-cancel");
  if (cancel instanceof HTMLElement) {
    cancel.click();
  }
}

function bindTextarea(textarea) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  if (textarea.getAttribute(AUTOBOUND_ATTR) === "true") return;
  textarea.setAttribute(AUTOBOUND_ATTR, "true");
  const form = textarea.closest("form[data-entry-edit-form]");
  const saveButton = form?.querySelector(".entry-edit-save");
  const updateSaveState = () => {
    const isEmpty = !textarea.value.trim();
    if (form) {
      form.classList.toggle(EMPTY_EDIT_CLASS, isEmpty);
    }
    if (saveButton instanceof HTMLButtonElement) {
      saveButton.disabled = isEmpty;
      saveButton.setAttribute(SAVE_DISABLED_ATTR, isEmpty ? "true" : "false");
      saveButton.toggleAttribute(SAVE_DISABLED_DATA, isEmpty);
    }
  };
  updateSaveState();
  resizeTextarea(textarea);
  scheduleCaretPlacement(textarea);
  textarea.addEventListener("focus", () => {
    scheduleCaretPlacement(textarea);
  });
  textarea.addEventListener("input", () => {
    resizeTextarea(textarea);
    updateSaveState();
  });
  textarea.addEventListener("blur", () => {
    if (!form) return;
    if (form.classList.contains("htmx-request")) return;
    if (form.getAttribute(SKIP_BLUR_CANCEL_ATTR) === "true") {
      form.removeAttribute(SKIP_BLUR_CANCEL_ATTR);
      return;
    }
    if (!textarea.value.trim()) {
      updateSaveState();
      return;
    }
    setTimeout(() => {
      const active = document.activeElement;
      if (active && form.contains(active)) {
        return;
      }
      if (EDIT_FLOW.blur === "cancel") {
        cancelEdit(form);
      } else {
        submitEdit(form);
      }
    }, 0);
  });

  textarea.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    if (EDIT_FLOW.escape === "cancel") {
      cancelEdit(form);
    } else {
      submitEdit(form);
    }
  });

  textarea.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (!(event.metaKey || event.ctrlKey)) return;
    event.preventDefault();
    if (!textarea.value.trim()) {
      updateSaveState();
      return;
    }
    if (EDIT_FLOW.saveShortcut === "save") {
      submitEdit(form);
    }
  });

  if (form) {
    form.addEventListener("submit", (event) => {
      if (!textarea.value.trim()) {
        event.preventDefault();
        updateSaveState();
      }
    });
  }
}

function bindInNode(node) {
  if (!(node instanceof Element)) return;
  if (node.matches(".entry-edit-area")) {
    bindTextarea(node);
  }
  node.querySelectorAll?.(".entry-edit-area").forEach((el) => {
    bindTextarea(el);
  });
}

function initEntryEditAutosize(root = document) {
  root.querySelectorAll?.(".entry-edit-area").forEach((el) => {
    bindTextarea(el);
  });
}

document.addEventListener("mousedown", (event) => {
  const editButton = event.target?.closest?.(".entry-edit");
  if (!editButton) return;
  const entry = editButton.closest(".entry");
  if (!entry) return;
  const editForm = entry.querySelector("form[data-entry-edit-form]");
  if (!editForm) return;
  if (!entry.querySelector(".entry-main--editing")) return;
  if (EDIT_FLOW.toggle === "cancel") {
    editForm.setAttribute(SKIP_BLUR_CANCEL_ATTR, "true");
  }
});

document.addEventListener("htmx:load", (event) => {
  const target = event.detail?.elt || document;
  bindInNode(target);
});

document.body?.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail?.target;
  if (target) {
    bindInNode(target);
  }
});

document.body?.addEventListener("htmx:beforeSwap", (event) => {
  const entryMain = findEntryMainFromDetail(event.detail);
  if (!entryMain) return;
  const entry = entryMain.closest(".entry");
  if (!entry) return;
  const height = getEntryMainHeight(entryMain);
  if (Number.isFinite(height)) {
    entry.setAttribute(ENTRY_HEIGHT_ATTR, String(height));
  }
});

document.body?.addEventListener("htmx:afterSwap", (event) => {
  const entryMain = findEntryMainFromDetail(event.detail);
  if (!entryMain) return;
  const entry = entryMain.closest(".entry");
  const _prevHeight = entry ? parseFloat(entry.getAttribute(ENTRY_HEIGHT_ATTR) || "") : NaN;
  if (entry) {
    entry.removeAttribute(ENTRY_HEIGHT_ATTR);
  }
  const textarea = entryMain.querySelector(".entry-edit-area");
  if (textarea instanceof HTMLTextAreaElement) {
    resizeTextarea(textarea);
    scheduleCaretPlacement(textarea, true);
  }
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => initEntryEditAutosize());
} else {
  initEntryEditAutosize();
}
