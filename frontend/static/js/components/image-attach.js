// <image-attach>: attaching images to an entry form.
//
// Owns everything about uploads: the file picker, drops and pastes, an
// upload queue (each image uploads on its own as soon as it is added), the
// tray of tiles with their remove buttons, and one hidden
// <input name="image_ids"> per uploaded image, so htmx sends them with the
// form. The parent form only asks `ids`, `busy` and `count`, listens for
// `image-attach:change`, and calls `clear()` after a successful send.
//
// Attributes:
//   data-drop-zone="page"  files dropped anywhere on the page land here (the
//                          entry form); "form": only drops on this form (an
//                          entry being edited), which the page zone leaves be.
//   data-initial-ids       JSON ids already on the entry (editing). Removing
//                          one only takes it out of the tray; saving decides.
//   data-send-empty        with no images left, send one empty image_ids value
//                          ("no images") rather than none ("leave them be").

import { ReactiveElement } from "../utils/reactive-element.js";

const ACCEPTED_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
  "image/heic",
  "image/heif",
];
// Browsers often don't know HEIC's type, so the picker also offers them by name.
const ACCEPTED_EXTENSIONS = [".heic", ".heif"];
const MAX_PARALLEL_UPLOADS = 3;
const COLLAPSE_MS = 160;

const NOTE_NOT_IMAGES = "Only images can be attached.";
const DROP_FORM_ATTR = "data-image-drop-form";

let nextKey = 0;

function csrfToken() {
  return document.body?.dataset.csrfToken || "";
}

function isImageFile(file) {
  if (!file) return false;
  if (ACCEPTED_TYPES.includes(file.type)) return true;
  // Some platforms leave the type empty; the server decides in the end.
  const name = file.name || "";
  // HEIC's type varies by browser (image/heic, none, octet-stream): go by name.
  if (/\.(heic|heif)$/i.test(name)) return true;
  return !file.type && /\.(jpe?g|png|webp|gif)$/i.test(name);
}

function hasFiles(event) {
  const types = event?.dataTransfer?.types;
  return Boolean(types && Array.from(types).includes("Files"));
}

function prefersReducedMotion() {
  return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function formatBytes(bytes) {
  const mib = bytes / (1024 * 1024);
  return mib >= 1 ? `${Math.round(mib)} MB` : `${Math.round(bytes / 1024)} KB`;
}

class ImageAttachElement extends ReactiveElement {
  #items = [];
  #form = null;
  #input = null;
  #tray = null;
  #note = null;
  #overlay = null;
  #fields = null;
  #pick = null;
  #textarea = null;
  #dragDepth = 0;
  #disabled = false;
  #built = false;

  connectedCallback() {
    super.connectedCallback();
    this.#form = this.closest("form");
    this.#pick = this.#form?.querySelector("[data-image-attach-pick]") ?? null;
    this.#textarea = this.#form?.querySelector("textarea") ?? null;
    this.#build();
    this.#bind();
    this.#restoreInitial();
  }

  disconnectedCallback() {
    // Leaving the page: uploads in flight are abandoned; the server sweeps
    // anything that was stored but never sent.
    for (const item of this.#items) {
      item.xhr?.abort();
      this.#releasePreview(item);
    }
    this.#items = [];
    super.disconnectedCallback();
  }

  // -- the parent form's view -------------------------------------------

  /** Ids of uploaded images, in tray order. */
  get ids() {
    return this.#items.filter((item) => item.state === "done").map((item) => item.id);
  }

  /** Whether any image is still waiting or uploading. */
  get busy() {
    return this.#items.some((item) => item.state === "queued" || item.state === "uploading");
  }

  /** Tiles that count towards the limit (failed ones don't). */
  get count() {
    return this.#items.filter((item) => item.state !== "failed").length;
  }

  get maxCount() {
    return Number(this.dataset.maxCount) || 8;
  }

  get maxBytes() {
    return Number(this.dataset.maxBytes) || 20 * 1024 * 1024;
  }

  set disabled(value) {
    this.#disabled = Boolean(value);
    if (this.#pick) this.#pick.disabled = this.#disabled;
    if (this.#disabled) this.#hideOverlay();
  }

  get disabled() {
    return this.#disabled;
  }

  /** Open the file picker. */
  pick() {
    if (!this.#disabled) this.#input?.click();
  }

  /** Add files (from the picker, a drop or a paste). */
  addFiles(fileList) {
    if (this.#disabled) return;
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const images = files.filter(isImageFile);
    const notes = [];
    if (images.length < files.length) notes.push(NOTE_NOT_IMAGES);

    const room = Math.max(0, this.maxCount - this.count);
    if (images.length > room) {
      notes.push(`An entry can have up to ${this.maxCount} images.`);
    }
    for (const file of images.slice(0, room)) {
      const item = {
        key: `a${nextKey++}`,
        file,
        id: null,
        state: "queued",
        progress: 0,
        message: "",
        xhr: null,
        preview: URL.createObjectURL(file),
        el: null,
      };
      if (file.size > this.maxBytes) {
        item.state = "failed";
        item.message = `Too large (max ${formatBytes(this.maxBytes)})`;
      }
      this.#items.push(item);
      this.#renderTile(item);
    }
    this.#showNote(notes.join(" "));
    this.#pump();
    this.#changed();
  }

  /** Show already-uploaded images (a restored draft, or an entry being edited). */
  restore(ids, { existing = false } = {}) {
    for (const id of ids || []) {
      if (!id || this.#items.some((item) => item.id === id)) continue;
      const item = {
        key: `a${nextKey++}`,
        file: null,
        id,
        state: "done",
        progress: 1,
        message: "",
        xhr: null,
        preview: null,
        el: null,
        restored: true,
        existing,
      };
      this.#items.push(item);
      this.#renderTile(item);
    }
    this.#changed();
  }

  /** Empty the tray after a send: the images belong to the entry now. */
  clear() {
    for (const item of this.#items) {
      item.xhr?.abort();
      this.#releasePreview(item);
    }
    this.#items = [];
    this.#tray.replaceChildren();
    this.#showNote("");
    this.#syncFields();
    this.#changed();
  }

  // -- structure ----------------------------------------------------------

  #build() {
    if (this.#built) return;
    this.#built = true;

    this.#input = document.createElement("input");
    this.#input.type = "file";
    this.#input.accept = [...ACCEPTED_TYPES, ...ACCEPTED_EXTENSIONS].join(",");
    this.#input.multiple = true;
    this.#input.hidden = true;
    this.#input.className = "image-attach__input";
    this.#input.setAttribute("aria-hidden", "true");
    this.#input.tabIndex = -1;

    this.#tray = document.createElement("ul");
    this.#tray.className = "image-attach__tray";
    this.#tray.setAttribute("role", "list");
    this.#tray.setAttribute("aria-label", "Images to attach");

    this.#note = document.createElement("p");
    this.#note.className = "image-attach__note";
    this.#note.setAttribute("aria-live", "polite");
    this.#note.hidden = true;

    this.#fields = document.createElement("div");
    this.#fields.className = "image-attach__fields";
    this.#fields.hidden = true;

    this.#overlay = document.createElement("div");
    this.#overlay.className = "image-attach__overlay";
    this.#overlay.setAttribute("aria-hidden", "true");
    this.#overlay.innerHTML =
      '<span class="icon-mask icon-image-plus image-attach__overlay-icon"></span>' +
      '<span class="image-attach__overlay-label">Drop images to attach</span>';

    this.replaceChildren(this.#input, this.#tray, this.#note, this.#fields, this.#overlay);
  }

  #restoreInitial() {
    const raw = this.dataset.initialIds;
    if (!raw) return;
    delete this.dataset.initialIds;
    let ids = [];
    try {
      ids = JSON.parse(raw);
    } catch {
      return;
    }
    if (Array.isArray(ids) && ids.length) this.restore(ids, { existing: true });
  }

  #bind() {
    this.addListener(this.#input, "change", () => {
      this.addFiles(this.#input.files);
      this.#input.value = "";
    });
    if (this.#pick) {
      this.addListener(this.#pick, "click", (event) => {
        event.preventDefault();
        this.pick();
      });
    }
    if (this.#textarea) {
      this.addListener(this.#textarea, "paste", (event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (!files.length) return;
        // Leave text pastes alone; take over only when images are pasted.
        event.preventDefault();
        this.addFiles(files);
      });
    }
    this.addListener(this.#tray, "click", (event) => {
      const button = event.target.closest?.(".image-attach__remove");
      if (!button) return;
      const item = this.#itemFor(button);
      if (item) this.#remove(item);
    });
    this.addListener(this.#tray, "keydown", (event) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const tile = event.target.closest?.(".image-attach__tile");
      if (!tile || event.target !== tile) return;
      event.preventDefault();
      const item = this.#itemFor(tile);
      if (item) this.#remove(item);
    });
    // Pressing a control must not take focus from the textarea: an entry
    // being edited saves when its textarea loses focus.
    const keepFocus = (event) => {
      if (event.target.closest?.("button, [data-image-attach-pick]")) {
        event.preventDefault();
      }
    };
    this.addListener(this.#tray, "pointerdown", keepFocus);
    if (this.#pick) this.addListener(this.#pick, "pointerdown", keepFocus);

    if (this.dataset.dropZone === "page") {
      this.#bindPageDrops();
    } else if (this.dataset.dropZone === "form" && this.#form) {
      this.#bindFormDrops();
    }
  }

  #bindFormDrops() {
    const form = this.#form;
    form.setAttribute(DROP_FORM_ATTR, "");
    this.addListener(form, "dragenter", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.#dragDepth += 1;
      if (!this.#disabled) this.#showOverlay();
    });
    this.addListener(form, "dragover", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = this.#disabled ? "none" : "copy";
      }
    });
    this.addListener(form, "dragleave", (event) => {
      if (!hasFiles(event)) return;
      this.#dragDepth = Math.max(0, this.#dragDepth - 1);
      if (this.#dragDepth === 0) this.#hideOverlay();
    });
    this.addListener(form, "drop", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.#dragDepth = 0;
      this.#hideOverlay();
      this.addFiles(event.dataTransfer?.files);
    });
    this.addListener(globalThis, "dragend", () => {
      this.#dragDepth = 0;
      this.#hideOverlay();
    });
  }

  #bindPageDrops() {
    const win = globalThis;
    // Drags over an entry being edited belong to that entry's own zone.
    const overOtherZone = (event) =>
      Boolean(event.target?.closest?.(`[${DROP_FORM_ATTR}]`)) &&
      !this.#form?.contains(event.target);
    this.addListener(win, "dragenter", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.#dragDepth += 1;
      if (overOtherZone(event) || this.#disabled) {
        this.#hideOverlay();
      } else {
        this.#showOverlay();
      }
    });
    this.addListener(win, "dragover", (event) => {
      if (!hasFiles(event)) return;
      // Never let the browser open a dropped file in the tab.
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = this.#disabled ? "none" : "copy";
      }
    });
    this.addListener(win, "dragleave", (event) => {
      if (!hasFiles(event)) return;
      // Every element entered is also left; the count reaches 0 only when
      // the drag leaves the window (moving between children keeps it up).
      this.#dragDepth = Math.max(0, this.#dragDepth - 1);
      if (this.#dragDepth === 0) this.#hideOverlay();
    });
    this.addListener(win, "drop", (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.#dragDepth = 0;
      this.#hideOverlay();
      if (!overOtherZone(event)) this.addFiles(event.dataTransfer?.files);
    });
    this.addListener(win, "dragend", () => {
      this.#dragDepth = 0;
      this.#hideOverlay();
    });
    this.addListener(document, "keydown", (event) => {
      if (event.key === "Escape" && this.#overlay?.classList.contains("is-visible")) {
        this.#dragDepth = 0;
        this.#hideOverlay();
      }
    });
  }

  #showOverlay() {
    this.#overlay?.classList.add("is-visible");
    this.#form?.classList.add("is-dropping");
  }

  #hideOverlay() {
    this.#overlay?.classList.remove("is-visible");
    this.#form?.classList.remove("is-dropping");
  }

  #showNote(text) {
    if (!this.#note) return;
    this.#note.textContent = text;
    this.#note.hidden = !text;
  }

  // -- tiles --------------------------------------------------------------

  #itemFor(element) {
    const key = element.closest?.(".image-attach__tile")?.dataset.key;
    return this.#items.find((item) => item.key === key) ?? null;
  }

  #renderTile(item) {
    const tile = document.createElement("li");
    tile.className = "image-attach__tile";
    tile.dataset.key = item.key;
    tile.tabIndex = 0;

    const img = document.createElement("img");
    img.className = "image-attach__preview";
    img.alt = "";
    img.decoding = "async";
    img.src = item.preview || `/i/${encodeURIComponent(item.id)}/thumb`;
    if (item.restored) {
      // A restored upload that was swept in the meantime just goes away.
      img.addEventListener("error", () => this.#drop(item), { once: true });
    }

    const progress = document.createElement("span");
    progress.className = "image-attach__progress";
    progress.setAttribute("aria-hidden", "true");
    progress.append(document.createElement("span"));

    const message = document.createElement("span");
    message.className = "image-attach__message";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "image-attach__remove";
    remove.setAttribute("aria-label", "Remove image");
    remove.dataset.tooltipTitle = "Remove image";
    remove.innerHTML = '<span class="icon-mask icon-x" aria-hidden="true"></span>';

    tile.append(img, progress, message, remove);
    item.el = tile;
    this.#tray.append(tile);
    this.#updateTile(item);
  }

  #updateTile(item) {
    const tile = item.el;
    if (!tile) return;
    tile.dataset.state = item.state;
    tile.style.setProperty("--progress", String(item.progress));
    const message = tile.querySelector(".image-attach__message");
    if (message) message.textContent = item.state === "failed" ? item.message : "";
    const position = this.#items.indexOf(item) + 1;
    const label =
      item.state === "failed"
        ? `Image ${position}: ${item.message}`
        : item.state === "done"
          ? `Image ${position}`
          : `Image ${position}, uploading`;
    tile.setAttribute("aria-label", label);
  }

  #remove(item) {
    item.xhr?.abort();
    item.xhr = null;
    // Images already on the entry are only taken out of the tray; saving the
    // edit removes them (and cancelling keeps them).
    if (item.state === "done" && item.id && !item.existing) {
      // Nothing has been sent yet, so there is nothing to confirm.
      fetch(`/i/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        headers: { "X-CSRFToken": csrfToken(), "HX-Request": "true" },
        credentials: "same-origin",
      }).catch(() => {
        // Unsent images are swept by the server anyway.
      });
    }
    const index = this.#items.indexOf(item);
    const next = this.#items[index + 1]?.el || this.#items[index - 1]?.el || null;
    this.#drop(item);
    if (next) {
      next.focus({ preventScroll: true });
    } else {
      this.#textarea?.focus({ preventScroll: true });
    }
  }

  /** Take a tile away (collapsing it), without touching the server. */
  #drop(item) {
    const index = this.#items.indexOf(item);
    if (index === -1) return;
    this.#items.splice(index, 1);
    this.#releasePreview(item);
    const tile = item.el;
    item.el = null;
    if (tile) {
      if (prefersReducedMotion()) {
        tile.remove();
      } else {
        tile.classList.add("is-leaving");
        setTimeout(() => tile.remove(), COLLAPSE_MS);
      }
    }
    for (const other of this.#items) this.#updateTile(other);
    this.#showNote("");
    this.#pump();
    this.#changed();
  }

  #releasePreview(item) {
    if (item.preview) {
      URL.revokeObjectURL(item.preview);
      item.preview = null;
    }
  }

  // -- uploads ------------------------------------------------------------

  #pump() {
    let active = this.#items.filter((item) => item.state === "uploading").length;
    for (const item of this.#items) {
      if (active >= MAX_PARALLEL_UPLOADS) break;
      if (item.state === "queued") {
        this.#upload(item);
        active += 1;
      }
    }
  }

  #upload(item) {
    const url = this.dataset.uploadUrl || "/i";
    const body = new FormData();
    body.append("image", item.file, item.file.name || "image");

    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    item.state = "uploading";
    this.#updateTile(item);

    xhr.open("POST", url);
    xhr.setRequestHeader("X-CSRFToken", csrfToken());
    xhr.setRequestHeader("HX-Request", "true");
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      item.progress = event.loaded / event.total;
      this.#updateTile(item);
    });
    xhr.addEventListener("load", () => {
      if (item.xhr !== xhr) return;
      item.xhr = null;
      const payload = xhr.response || {};
      if (xhr.status === 201 && payload.id) {
        item.id = String(payload.id);
        item.state = "done";
        item.progress = 1;
        item.file = null;
      } else {
        item.state = "failed";
        item.message = payload.message || (xhr.status === 413 ? "Too large" : "Upload failed");
      }
      this.#finish(item);
    });
    const fail = () => {
      if (item.xhr !== xhr) return;
      item.xhr = null;
      item.state = "failed";
      item.message = "Upload failed";
      this.#finish(item);
    };
    xhr.addEventListener("error", fail);
    xhr.addEventListener("timeout", fail);
    xhr.send(body);
  }

  #finish(item) {
    this.#updateTile(item);
    this.#syncFields();
    this.#pump();
    this.#changed();
  }

  #syncFields() {
    if (!this.#fields) return;
    const ids = this.ids;
    const values = ids.length || !this.hasAttribute("data-send-empty") ? ids : [""];
    const inputs = values.map((id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "image_ids";
      input.value = id;
      return input;
    });
    this.#fields.replaceChildren(...inputs);
  }

  #changed() {
    this.#syncFields();
    this.toggleAttribute("data-has-images", this.#items.length > 0);
    this.dispatchEvent(
      new CustomEvent("image-attach:change", {
        bubbles: true,
        detail: { count: this.count, busy: this.busy, ids: this.ids },
      }),
    );
  }
}

if (!customElements.get("image-attach")) {
  customElements.define("image-attach", ImageAttachElement);
}
