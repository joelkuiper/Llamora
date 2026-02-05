const AUTOBOUND_ATTR = "data-autosize-bound";
const SKIP_BLUR_CANCEL_ATTR = "data-skip-blur-cancel";

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

function resizeTextarea(textarea) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  const scrollContainer = getScrollContainer(textarea);
  const prevScrollTop =
    scrollContainer instanceof Element ? scrollContainer.scrollTop : null;
  textarea.style.height = "auto";
  const styles = window.getComputedStyle(textarea);
  const max = parseFloat(styles.maxHeight || "");
  const height = textarea.scrollHeight;
  if (Number.isFinite(max) && max > 0) {
    textarea.style.height = `${Math.min(height, max)}px`;
    textarea.style.overflowY = height > max ? "auto" : "hidden";
  } else {
    textarea.style.height = `${height}px`;
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

function submitEdit(form) {
  if (!form || form.classList.contains("htmx-request")) return;
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
  resizeTextarea(textarea);
  textarea.addEventListener("input", () => resizeTextarea(textarea));
  textarea.addEventListener("blur", () => {
    const form = textarea.closest("form[data-entry-edit-form]");
    if (!form) return;
    if (form.classList.contains("htmx-request")) return;
    if (form.getAttribute(SKIP_BLUR_CANCEL_ATTR) === "true") {
      form.removeAttribute(SKIP_BLUR_CANCEL_ATTR);
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
    const form = textarea.closest("form[data-entry-edit-form]");
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
    const form = textarea.closest("form[data-entry-edit-form]");
    if (EDIT_FLOW.saveShortcut === "save") {
      submitEdit(form);
    }
  });
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

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => initEntryEditAutosize());
} else {
  initEntryEditAutosize();
}
