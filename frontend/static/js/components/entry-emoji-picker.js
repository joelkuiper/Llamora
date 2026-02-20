import { createPopover } from "../popover.js";
import { EmojiMart } from "../vendor/setup-globals.js";

const TRIGGER_SELECTOR = "[data-entry-emoji-trigger]";
const EDIT_FORM_SELECTOR = "form[data-entry-edit-form]";
const SKIP_BLUR_CANCEL_ATTR = "data-skip-blur-cancel";
const EMOJI_DATA_URL = "/static/js/vendor/emoji-mart-data-native-14.json";

const state = {
  root: null,
  panel: null,
  popover: null,
  picker: null,
  dataPromise: null,
  trigger: null,
  textarea: null,
  form: null,
  initialized: false,
};

const isTextareaTarget = (node) => node instanceof HTMLTextAreaElement && !node.disabled;

const findTargetTextarea = (trigger) => {
  if (!(trigger instanceof HTMLElement)) return null;
  const form = trigger.closest("form");
  if (!form) return null;
  const textarea = form.querySelector("textarea[name='text']");
  return isTextareaTarget(textarea) ? textarea : null;
};

const markEditFormSkipBlur = (form) => {
  if (!(form instanceof HTMLFormElement)) return;
  if (!form.matches(EDIT_FORM_SELECTOR)) return;
  form.setAttribute(SKIP_BLUR_CANCEL_ATTR, "true");
};

const insertAtCaret = (textarea, text) => {
  if (!isTextareaTarget(textarea)) return;
  const value = String(text || "");
  if (!value) return;
  const start = Number.isFinite(textarea.selectionStart)
    ? textarea.selectionStart
    : textarea.value.length;
  const end = Number.isFinite(textarea.selectionEnd) ? textarea.selectionEnd : start;
  textarea.setRangeText(value, start, end, "end");
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
};

const readNativeEmoji = (payload) => {
  if (!payload) return "";
  if (typeof payload === "string") return payload;
  const native = String(payload.native || payload.emoji || "").trim();
  return native;
};

const loadEmojiData = async () => {
  if (state.dataPromise) return state.dataPromise;
  state.dataPromise = fetch(EMOJI_DATA_URL, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Emoji data request failed (${response.status})`);
      }
      return response.json();
    })
    .catch((error) => {
      console.error("[emoji-picker] failed to load emoji data", error);
      state.dataPromise = null;
      throw error;
    });
  return state.dataPromise;
};

const ensureRoot = () => {
  if (state.root && state.panel) {
    return state.root;
  }

  const root = document.createElement("div");
  root.id = "entry-emoji-picker-popover";
  root.className = "emoji-picker-popover glass-panel";
  root.hidden = true;
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-label", "Emoji picker");

  const panel = document.createElement("div");
  panel.className = "emoji-picker-popover__panel";
  panel.textContent = "Loading emoji…";
  root.append(panel);

  (document.body || document.documentElement).append(root);
  state.root = root;
  state.panel = panel;
  return root;
};

const ensurePopover = (trigger) => {
  const root = ensureRoot();
  if (!state.popover) {
    state.popover = createPopover(trigger, root, {
      placement: "top-start",
      getPanel: () => state.panel,
      onHidden: () => {
        state.trigger = null;
        state.textarea = null;
        state.form = null;
      },
      isEventOutside: (event) => {
        const target = event.target;
        return target instanceof Node && !root.contains(target) && !trigger.contains(target);
      },
    });
    return state.popover;
  }

  state.popover.destroy();
  state.popover = createPopover(trigger, root, {
    placement: "top-start",
    getPanel: () => state.panel,
    onHidden: () => {
      state.trigger = null;
      state.textarea = null;
      state.form = null;
    },
    isEventOutside: (event) => {
      const target = event.target;
      return target instanceof Node && !root.contains(target) && !trigger.contains(target);
    },
  });
  return state.popover;
};

const ensurePicker = async () => {
  if (state.picker || !(state.panel instanceof HTMLElement)) {
    return state.picker;
  }
  if (!EmojiMart?.Picker) {
    state.panel.textContent = "Emoji unavailable.";
    return null;
  }

  const data = await loadEmojiData();
  const picker = new EmojiMart.Picker({
    data,
    set: "native",
    theme: "light",
    previewPosition: "none",
    skinTonePosition: "none",
    navPosition: "top",
    searchPosition: "sticky",
    maxFrequentRows: 2,
    autoFocus: true,
    dynamicWidth: true,
    onEmojiSelect: (emoji) => {
      const native = readNativeEmoji(emoji);
      if (!native || !isTextareaTarget(state.textarea)) {
        return;
      }
      markEditFormSkipBlur(state.form);
      insertAtCaret(state.textarea, native);
      state.textarea.focus({ preventScroll: true });
      state.popover?.hide();
    },
  });

  state.panel.replaceChildren(picker);
  state.picker = picker;
  return picker;
};

const openPicker = async (trigger) => {
  const textarea = findTargetTextarea(trigger);
  if (!textarea) return;
  const form = trigger.closest("form");

  state.trigger = trigger;
  state.textarea = textarea;
  state.form = form instanceof HTMLFormElement ? form : null;
  markEditFormSkipBlur(state.form);

  const popover = ensurePopover(trigger);
  popover.show();
  popover.update();
  await ensurePicker();
  popover.update();
};

const handleTriggerMouseDown = (event) => {
  const trigger = event.target?.closest?.(TRIGGER_SELECTOR);
  if (!(trigger instanceof HTMLElement)) return;
  event.preventDefault();
  const textarea = findTargetTextarea(trigger);
  if (textarea) {
    textarea.focus({ preventScroll: true });
  }
};

const handleTriggerClick = (event) => {
  const trigger = event.target?.closest?.(TRIGGER_SELECTOR);
  if (!(trigger instanceof HTMLElement)) return;
  event.preventDefault();
  void openPicker(trigger);
};

const handlePopoverMouseDown = () => {
  markEditFormSkipBlur(state.form);
};

const initEntryEmojiPicker = () => {
  if (state.initialized) return;
  state.initialized = true;

  document.addEventListener("mousedown", handleTriggerMouseDown);
  document.addEventListener("click", handleTriggerClick);

  const root = ensureRoot();
  root.addEventListener("mousedown", handlePopoverMouseDown, true);

  document.addEventListener("app:teardown", () => {
    state.popover?.hide();
  });
};

initEntryEmojiPicker();
