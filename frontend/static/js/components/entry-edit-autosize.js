const AUTOBOUND_ATTR = "data-autosize-bound";

function resizeTextarea(textarea) {
  if (!(textarea instanceof HTMLTextAreaElement)) return;
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
    setTimeout(() => {
      const active = document.activeElement;
      if (active && form.contains(active)) {
        return;
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    }, 0);
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
