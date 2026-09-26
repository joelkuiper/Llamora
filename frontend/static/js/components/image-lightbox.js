// Lightbox for entry images.
//
// A fixed overlay stacked with nextModalZ() instead of a native <dialog>:
// the confirm modal (a stacked overlay too) must be able to open above it to
// confirm a removal. The rest of the page is made inert while it is open and
// focus is kept inside. Thumbnails are found by a delegated click listener,
// so entries swapped in by htmx need no setup; the gallery is always the
// clicked entry's own thumbnails, in order.

import { createInlineSpinner } from "../ui.js";
import { nextModalZ } from "../utils/modal-stack.js";
import { prefersReducedMotion } from "../utils/motion.js";
import { ReactiveElement } from "../utils/reactive-element.js";
import { transitionHide, transitionShow } from "../utils/transition.js";

const ITEM_SELECTOR = "[data-lightbox-item]";
const SWIPE_DISTANCE = 50;
const CROSSFADE_MS = 150;
// Only show the spinner for images that take a moment (cached ones don't).
const SPINNER_DELAY_MS = 150;

function variantUrl(item, variant) {
  const src = item.querySelector("img")?.getAttribute("src") || "";
  return src.replace(/\/thumb$/, `/${variant}`);
}

function galleryOf(item) {
  const slot = item.closest(".entry-images-slot");
  if (!slot) return null;
  return {
    slot,
    entryId: slot.dataset.entryId || "",
    items: Array.from(slot.querySelectorAll(ITEM_SELECTOR)),
  };
}

function confirmModalOpen() {
  return Boolean(document.getElementById("confirm-modal")?.classList.contains("is-open"));
}

// Fresh from the page rather than data-textless alone: an edit may have
// removed the text since the grid was rendered.
function entryIsTextless(gallery) {
  const body = document.getElementById(`entry-${gallery.entryId}-body`);
  if (body) {
    return !body.textContent.trim();
  }
  return gallery.slot.dataset.textless === "true";
}

class ImageLightboxElement extends ReactiveElement {
  #gallery = null;
  #index = 0;
  // The shown image's own size. Never read back from the <img>: once it is
  // rendered, img.width/height report its current box (the previous image's).
  #size = { width: 0, height: 0 };
  #opener = null;
  #inerted = [];
  #cancelHide = null;
  #swapTimer = null;
  #spinner = null;
  #spinnerTimer = null;
  #error = null;
  #pointer = null;
  #suppressClick = false;

  #image = null;
  #stage = null;
  #panel = null;
  #prev = null;
  #next = null;
  #counter = null;
  #full = null;
  #remove = null;

  connectedCallback() {
    super.connectedCallback();
    this.#image = this.querySelector(".image-lightbox__image");
    this.#stage = this.querySelector(".image-lightbox__stage");
    this.#panel = this.querySelector(".image-lightbox__panel");
    this.#prev = this.querySelector("[data-lightbox-action='prev']");
    this.#next = this.querySelector("[data-lightbox-action='next']");
    this.#counter = this.querySelector(".image-lightbox__counter");
    this.#full = this.querySelector(".image-lightbox__full");
    this.#remove = this.querySelector("[data-lightbox-action='remove']");
    this.#error = this.querySelector(".image-lightbox__error");
    const spinnerEl = this.querySelector(".image-lightbox__spinner");
    this.#spinner = spinnerEl ? createInlineSpinner(spinnerEl) : null;

    this.addListener(this, "click", (event) => this.#onClick(event));
    this.addListener(this, "pointerdown", (event) => this.#onPointerDown(event));
    this.addListener(this, "pointerup", (event) => this.#onPointerUp(event));
    this.addListener(this, "pointercancel", () => {
      this.#pointer = null;
    });
    // Capture phase: runs before the confirm modal's own Escape handler, so
    // an Escape meant for the confirmation does not also close the lightbox.
    this.addListener(document, "keydown", (event) => this.#onKeydown(event), true);
    this.addListener(window, "resize", () => this.#fit());
    this.addListener(this.#image, "load", () => this.#onImageLoad());
    this.addListener(this.#image, "error", () => this.#onImageError());
    this.addListener(document.body, "htmx:beforeSwap", (event) => {
      const target = event.detail?.target;
      if (this.isOpen && target instanceof Element && target.id === "content-wrapper") {
        this.close({ restoreFocus: false });
      }
    });
    this.addListener(this.#remove, "htmx:afterRequest", (event) => this.#afterRemove(event));
  }

  get isOpen() {
    return !this.hidden && this.classList.contains("is-open");
  }

  open(item) {
    const gallery = galleryOf(item);
    if (!gallery || !gallery.items.length) return;
    this.#gallery = gallery;
    this.#opener = item;
    if (this.#cancelHide) {
      this.#cancelHide();
      this.#cancelHide = null;
    }
    if (this.parentElement !== document.body) {
      document.body.appendChild(this);
    }
    this.style.zIndex = String(nextModalZ());
    this.#setInert(true);
    this.removeAttribute("aria-hidden");
    // Visible (still transparent) first, so the stage has a size to fit into.
    transitionShow(this, "is-open");
    this.#show(Math.max(0, gallery.items.indexOf(item)), { instant: true });
    requestAnimationFrame(() => this.querySelector(".image-lightbox__close")?.focus());
  }

  close({ restoreFocus = true } = {}) {
    if (this.hidden) return;
    this.#setLoading(false);
    this.setAttribute("aria-hidden", "true");
    this.#setInert(false);
    this.#cancelHide = transitionHide(this, "is-open", prefersReducedMotion() ? 0 : 180);
    const target = this.#focusTargetAfterClose();
    this.#gallery = null;
    this.#opener = null;
    if (restoreFocus && target) {
      target.focus({ preventScroll: true });
    }
  }

  // -- showing ----------------------------------------------------------

  #show(index, { instant = false } = {}) {
    const items = this.#gallery?.items || [];
    if (!items.length) return;
    this.#index = Math.min(Math.max(index, 0), items.length - 1);
    const item = items[this.#index];
    const count = items.length;
    const position = this.#index + 1;

    this.#counter.textContent = count > 1 ? `${position} / ${count}` : "";
    this.#panel.setAttribute("aria-label", `Image ${position} of ${count}`);
    this.#prev.hidden = count < 2;
    this.#next.hidden = count < 2;
    this.#prev.disabled = this.#index === 0;
    this.#next.disabled = this.#index === count - 1;
    this.#full.href = variantUrl(item, "full");
    this.dataset.imageId = item.dataset.imageId || "";

    const swap = () => {
      this.#swapTimer = null;
      this.#size = {
        width: Number(item.dataset.width) || 0,
        height: Number(item.dataset.height) || 0,
      };
      this.#image.setAttribute("width", String(this.#size.width));
      this.#image.setAttribute("height", String(this.#size.height));
      this.#fit();
      // A new src keeps painting the previous picture until it loads: hide
      // the image until then (and show the spinner if that takes a moment).
      this.#setLoading(true);
      this.#image.src = variantUrl(item, "display");
      if (this.#image.complete && this.#image.naturalWidth) {
        this.#onImageLoad();
      }
    };
    if (this.#swapTimer) {
      clearTimeout(this.#swapTimer);
      this.#swapTimer = null;
    }
    if (instant || prefersReducedMotion()) {
      swap();
    } else {
      this.#image.classList.add("is-changing");
      this.#swapTimer = setTimeout(swap, CROSSFADE_MS);
    }
    this.#preload(items[this.#index - 1]);
    this.#preload(items[this.#index + 1]);
  }

  #preload(item) {
    if (!item) return;
    const img = new Image();
    img.decoding = "async";
    img.src = variantUrl(item, "display");
  }

  // The image itself is the truth once it has loaded (data-width/height only
  // size the box beforehand).
  #setLoading(loading) {
    if (this.#spinnerTimer) {
      clearTimeout(this.#spinnerTimer);
      this.#spinnerTimer = null;
    }
    this.classList.toggle("is-loading", loading);
    if (this.#error) this.#error.hidden = true;
    if (!loading) {
      this.classList.remove("is-spinning");
      this.#spinner?.stop();
      return;
    }
    this.#spinnerTimer = setTimeout(() => {
      this.#spinnerTimer = null;
      this.classList.add("is-spinning");
      this.#spinner?.start();
    }, SPINNER_DELAY_MS);
  }

  #onImageError() {
    if (!this.isOpen) return;
    this.#setLoading(false);
    this.#image.classList.remove("is-changing");
    this.classList.add("is-loading"); // keep the broken image hidden
    if (this.#error) this.#error.hidden = false;
  }

  #onImageLoad() {
    this.#setLoading(false);
    const { naturalWidth, naturalHeight } = this.#image;
    if (
      naturalWidth &&
      naturalHeight &&
      (naturalWidth !== this.#size.width || naturalHeight !== this.#size.height)
    ) {
      this.#size = { width: naturalWidth, height: naturalHeight };
      this.#fit();
    }
    this.#image.classList.remove("is-changing");
  }

  // Size the image box from the image's own dimensions before it arrives, so
  // it opens at its final size and aspect ratio (never upscaled).
  #fit() {
    const { width, height } = this.#size;
    if (!width || !height || !this.#stage) return;
    const boxWidth = this.#stage.clientWidth;
    const boxHeight = this.#stage.clientHeight;
    const scale = Math.min(1, boxWidth / width, boxHeight / height);
    this.#image.style.width = `${Math.floor(width * scale)}px`;
    this.#image.style.height = `${Math.floor(height * scale)}px`;
  }

  #step(delta) {
    const items = this.#gallery?.items || [];
    const next = this.#index + delta;
    if (next < 0 || next >= items.length) return;
    this.#show(next);
  }

  // -- removing ---------------------------------------------------------

  #confirmRemove() {
    const gallery = this.#gallery;
    const item = gallery?.items[this.#index];
    const htmx = globalThis.htmx;
    if (!item || !htmx) return;
    const deletesEntry = gallery.items.length === 1 && entryIsTextless(gallery);
    const button = this.#remove;
    if (deletesEntry) {
      button.dataset.confirmTitle = "Delete entry?";
      button.dataset.confirmMessage =
        "This entry has no text. Removing its only image deletes the entry.";
      button.dataset.confirmConfirm = "Delete entry";
    } else {
      button.dataset.confirmTitle = "Remove image?";
      button.dataset.confirmMessage = "It will be deleted from this entry.";
      button.dataset.confirmConfirm = "Remove";
    }
    button.dataset.deletesEntry = deletesEntry ? "true" : "false";
    const imageId = encodeURIComponent(item.dataset.imageId || "");
    const entryTarget = document.getElementById(`entry-${gallery.entryId}`);
    // The confirm modal (htmx:confirm on the source) asks first; "Keep"
    // means no request at all.
    htmx.ajax("DELETE", `/i/${imageId}${deletesEntry ? "?with_entry=1" : ""}`, {
      source: button,
      target: deletesEntry && entryTarget ? entryTarget : button,
      swap: deletesEntry && entryTarget ? "delete swap:180ms" : "none",
    });
  }

  #afterRemove(event) {
    if (!event.detail?.successful || !this.#gallery) return;
    if (this.#remove.dataset.deletesEntry === "true") {
      document
        .getElementById(`entry-${this.#gallery.entryId}`)
        ?.classList.add("motion-animate-entry-delete");
      this.close({ restoreFocus: false });
      return;
    }
    // The response swapped the entry's grid out-of-band: re-read it.
    const slot = document.getElementById(`entry-images-${this.#gallery.entryId}`);
    const items = slot ? Array.from(slot.querySelectorAll(ITEM_SELECTOR)) : [];
    if (!items.length) {
      this.#gallery = { ...this.#gallery, slot, items };
      this.close();
      return;
    }
    this.#gallery = { ...this.#gallery, slot, items };
    this.#opener = null;
    this.#show(Math.min(this.#index, items.length - 1));
  }

  // -- events -----------------------------------------------------------

  #onClick(event) {
    if (this.#suppressClick) {
      this.#suppressClick = false;
      return;
    }
    if (event.target === this.#image) return;
    const action = event.target?.closest?.("[data-lightbox-action]");
    if (!action || !this.contains(action)) return;
    switch (action.dataset.lightboxAction) {
      case "prev":
        this.#step(-1);
        break;
      case "next":
        this.#step(1);
        break;
      case "remove":
        this.#confirmRemove();
        break;
      case "close":
        this.close();
        break;
    }
  }

  #onKeydown(event) {
    if (!this.isOpen || confirmModalOpen()) return;
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        this.close();
        break;
      case "ArrowLeft":
        event.preventDefault();
        this.#step(-1);
        break;
      case "ArrowRight":
        event.preventDefault();
        this.#step(1);
        break;
      case "Tab":
        this.#trapFocus(event);
        break;
    }
  }

  #onPointerDown(event) {
    if (!this.isOpen || event.button !== 0) return;
    if (event.target?.closest?.("button, a")) return;
    this.#pointer = { x: event.clientX, y: event.clientY };
  }

  #onPointerUp(event) {
    const start = this.#pointer;
    this.#pointer = null;
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (Math.abs(dx) < SWIPE_DISTANCE || Math.abs(dx) < Math.abs(dy)) return;
    // A swipe, not a click: don't let it close the lightbox.
    this.#suppressClick = true;
    setTimeout(() => {
      this.#suppressClick = false;
    }, 0);
    this.#step(dx < 0 ? 1 : -1);
  }

  #trapFocus(event) {
    const focusable = Array.from(this.querySelectorAll("button, a[href]")).filter(
      (el) => !el.hidden && !el.disabled && el.offsetParent !== null,
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !this.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !this.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  // -- page state -------------------------------------------------------

  #setInert(on) {
    if (on) {
      const keep = new Set([this, document.getElementById("confirm-modal")]);
      this.#inerted = Array.from(document.body.children).filter(
        (el) => !keep.has(el) && !el.inert && el.tagName !== "SCRIPT",
      );
      for (const el of this.#inerted) el.inert = true;
    } else {
      for (const el of this.#inerted) el.inert = false;
      this.#inerted = [];
    }
  }

  #focusTargetAfterClose() {
    if (this.#opener?.isConnected) return this.#opener;
    const items = this.#gallery?.items || [];
    const candidate = items[Math.min(this.#index, items.length - 1)];
    if (candidate?.isConnected) return candidate;
    const entryId = this.#gallery?.entryId;
    return entryId ? document.getElementById(`entry-${entryId}`) : null;
  }
}

if (!customElements.get("image-lightbox")) {
  customElements.define("image-lightbox", ImageLightboxElement);
}

// -- thumbnails -----------------------------------------------------------

// Fade thumbnails in as they load; mark the ones that fail. Only images still
// loading are touched, so cached thumbnails (and pages without this module)
// simply show.
function markThumbnails(scope = document) {
  for (const img of scope.querySelectorAll(`${ITEM_SELECTOR} img`)) {
    const button = img.closest(ITEM_SELECTOR);
    if (!button || button.dataset.state) continue;
    if (!img.complete) {
      button.dataset.state = "loading";
    } else if (!img.naturalWidth) {
      button.dataset.state = "error";
    }
  }
}

function onThumbnailSettled(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement)) return;
  const button = img.closest(ITEM_SELECTOR);
  if (!button) return;
  button.dataset.state = event.type === "error" ? "error" : "loaded";
}

if (!globalThis.__imageLightboxBound) {
  globalThis.__imageLightboxBound = true;

  document.addEventListener(
    "click",
    (event) => {
      const item = event.target?.closest?.(ITEM_SELECTOR);
      if (!item) return;
      event.preventDefault();
      const lightbox = document.getElementById("image-lightbox");
      if (lightbox && typeof lightbox.open === "function") {
        lightbox.open(item);
      }
    },
    false,
  );
  // load/error don't bubble; capture them for every thumbnail.
  document.addEventListener("load", onThumbnailSettled, true);
  document.addEventListener("error", onThumbnailSettled, true);
  document.body.addEventListener("htmx:afterSettle", (event) =>
    markThumbnails(event.detail?.elt instanceof Element ? event.detail.elt : document),
  );
  markThumbnails();
}
