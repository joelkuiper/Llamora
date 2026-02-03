const editState = new WeakMap();

function getEntryText(entry, body) {
  const raw = entry?.dataset?.entryText;
  if (raw) return raw;
  if (body?.dataset?.markdownSource) return body.dataset.markdownSource;
  return (body?.textContent || "").trim();
}

function setEditable(body, enabled) {
  if (!body) return;
  body.setAttribute("contenteditable", enabled ? "true" : "false");
  body.setAttribute("spellcheck", enabled ? "true" : "false");
}

function cleanupEdit(entry) {
  const state = editState.get(entry);
  if (!state) return;
  const { body, strip, editButton } = state;
  if (strip && strip.parentElement) {
    strip.remove();
  }
  setEditable(body, false);
  body.removeAttribute("data-editing");
  entry.classList.remove("is-editing");
  if (editButton) {
    editButton.classList.remove("active");
    editButton.setAttribute("aria-pressed", "false");
  }
  editState.delete(entry);
}

function restoreEntry(entry) {
  const state = editState.get(entry);
  if (!state) return;
  const { body, html, rendered, text } = state;
  body.innerHTML = html;
  if (rendered) {
    body.dataset.rendered = "true";
    delete body.dataset.markdownSource;
  } else {
    delete body.dataset.rendered;
    body.dataset.markdownSource = text;
  }
  cleanupEdit(entry);
}

function buildActionStrip() {
  const strip = document.createElement("div");
  strip.className = "entry-edit-strip";

  const save = document.createElement("button");
  save.type = "button";
  save.className = "entry-edit-save";
  save.setAttribute("data-edit-action", "save");
  save.setAttribute("aria-label", "Save entry");
  save.innerHTML = "<span aria-hidden=\"true\">✓</span><span class=\"entry-edit-label\">Save</span>";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "entry-edit-cancel";
  cancel.setAttribute("data-edit-action", "cancel");
  cancel.setAttribute("aria-label", "Cancel editing");
  cancel.innerHTML = "<span aria-hidden=\"true\">×</span><span class=\"entry-edit-label\">Cancel</span>";

  strip.append(save, cancel);
  return strip;
}

function requestSave(entry) {
  const state = editState.get(entry);
  if (!state) return;
  const { body, text } = state;
  const value = (body.textContent || "").trim();
  if (!value) {
    restoreEntry(entry);
    return;
  }
  entry.dataset.entryText = value;
  cleanupEdit(entry);
  if (window.htmx?.ajax) {
    window.htmx.ajax("PUT", `/e/entry/${entry.dataset.entryId}`, {
      target: `#entry-${entry.dataset.entryId} .entry-main`,
      swap: "innerHTML",
      select: ".entry-main",
      values: { text: value },
    });
  } else {
    body.textContent = text;
  }
}

function startEditing(entry) {
  if (!entry || entry.classList.contains("is-editing")) return;
  const body = entry.querySelector(".markdown-body");
  if (!body) return;
  const editButton = entry.querySelector(".entry-edit");

  const text = getEntryText(entry, body);
  const state = {
    body,
    html: body.innerHTML,
    rendered: body.dataset.rendered === "true",
    text,
    ignoreBlur: false,
    strip: null,
    editButton,
  };
  editState.set(entry, state);

  entry.classList.add("is-editing");
  if (editButton) {
    editButton.classList.add("active");
    editButton.setAttribute("aria-pressed", "true");
  }
  body.dataset.editing = "true";
  body.textContent = text;
  setEditable(body, true);

  const strip = buildActionStrip();
  state.strip = strip;
  body.parentElement?.appendChild(strip);

  strip.addEventListener("mousedown", () => {
    state.ignoreBlur = true;
  });
  strip.addEventListener("mouseup", () => {
    queueMicrotask(() => {
      state.ignoreBlur = false;
    });
  });

  strip.addEventListener("click", (event) => {
    const action = event.target?.closest?.("[data-edit-action]")?.getAttribute("data-edit-action");
    if (!action) return;
    event.preventDefault();
    if (action === "save") {
      requestSave(entry);
    } else if (action === "cancel") {
      restoreEntry(entry);
    }
  });

  body.addEventListener("blur", () => {
    if (state.ignoreBlur) return;
    requestSave(entry);
  });

  body.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      restoreEntry(entry);
      return;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      requestSave(entry);
    }
  });

  body.focus();
}

function handleEditClick(event) {
  const button = event.target?.closest?.(".entry-edit");
  if (!button) return;
  const entry = button.closest(".entry.user");
  if (!entry) return;
  event.preventDefault();
  if (entry.classList.contains("is-editing")) {
    restoreEntry(entry);
    return;
  }
  startEditing(entry);
}

function initEntryEdit() {
  if (document.body?.dataset.entryEditInit === "true") {
    return;
  }
  document.addEventListener("click", handleEditClick);
  if (document.body) {
    document.body.dataset.entryEditInit = "true";
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initEntryEdit, { once: true });
} else {
  initEntryEdit();
}

document.addEventListener("app:rehydrate", initEntryEdit);
