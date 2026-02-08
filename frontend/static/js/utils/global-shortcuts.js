const shortcuts = [];
let listening = false;

const MODIFIER_PROPS = ["ctrlKey", "altKey", "metaKey", "shiftKey"];

const isEditableTarget = (target) => {
  if (!(target instanceof Element)) {
    return false;
  }
  const tagName = target.tagName;
  return (
    tagName === "INPUT" ||
    tagName === "TEXTAREA" ||
    target.isContentEditable ||
    target.closest?.("[contenteditable='true']")
  );
};

const removeEntry = (entry) => {
  const index = shortcuts.indexOf(entry);
  if (index === -1) {
    return;
  }
  shortcuts.splice(index, 1);
  if (!shortcuts.length && listening) {
    document.removeEventListener("keydown", handleKeydown, true);
    listening = false;
  }
};

const ensureListening = () => {
  if (listening || !shortcuts.length) {
    return;
  }
  document.addEventListener("keydown", handleKeydown, true);
  listening = true;
};

const matchesShortcut = (event, entry) => {
  if (entry.ignorePrevented !== true && event.defaultPrevented) {
    return false;
  }

  if (!entry.allowInInputs && isEditableTarget(event.target)) {
    return false;
  }

  if (entry.code && event.code !== entry.code) {
    return false;
  }

  if (entry.key) {
    const eventKey = entry.caseSensitive ? event.key : event.key.toLowerCase();
    if (eventKey !== entry.key) {
      return false;
    }
  }

  for (const prop of MODIFIER_PROPS) {
    const expected = entry[prop];
    if (expected === undefined) {
      if (event[prop]) {
        return false;
      }
      continue;
    }
    if (Boolean(expected) !== Boolean(event[prop])) {
      return false;
    }
  }

  if (typeof entry.when === "function" && !entry.when(event)) {
    return false;
  }

  return true;
};

function handleKeydown(event) {
  if (!shortcuts.length) {
    return;
  }

  for (const entry of shortcuts.slice()) {
    if (!entry || entry.disabled) {
      continue;
    }

    if (!matchesShortcut(event, entry)) {
      continue;
    }

    if (entry.preventDefault && !event.defaultPrevented) {
      event.preventDefault();
    }

    if (entry.stopPropagation) {
      event.stopPropagation();
    }

    try {
      entry.handler?.(event);
    } catch (error) {
      console.error("Error running shortcut handler", error);
    }

    if (entry.once) {
      entry.abort();
    }

    if (entry.consume !== false) {
      break;
    }
  }
}

const createEntry = (options = {}) => {
  const {
    key = null,
    code = null,
    handler = null,
    signal = null,
    allowInInputs = false,
    preventDefault = false,
    stopPropagation = false,
    consume = true,
    once = false,
    caseSensitive = false,
    ignorePrevented = false,
    when = null,
    priority = 0,
  } = options;

  if (!handler || (!key && !code)) {
    return null;
  }

  const entry = {
    key: key && !caseSensitive ? key.toLowerCase() : key,
    caseSensitive,
    code,
    handler,
    allowInInputs,
    preventDefault,
    stopPropagation,
    consume,
    once,
    ignorePrevented,
    when,
    priority: Number.isFinite(priority) ? priority : 0,
    abort() {
      if (entry.disabled) return;
      entry.disabled = true;
      if (signalSubscription) {
        signalSubscription();
      }
      removeEntry(entry);
    },
  };

  for (const prop of MODIFIER_PROPS) {
    if (options[prop] !== undefined) {
      entry[prop] = Boolean(options[prop]);
    }
  }

  let signalSubscription = null;
  if (signal instanceof AbortSignal) {
    if (signal.aborted) {
      entry.disabled = true;
    } else {
      const onAbort = () => entry.abort();
      signal.addEventListener("abort", onAbort, { once: true });
      signalSubscription = () => signal.removeEventListener("abort", onAbort, { once: true });
    }
  }

  return entry.disabled ? null : entry;
};

const sortShortcuts = () => {
  shortcuts.sort((a, b) => b.priority - a.priority);
};

export function registerShortcut(options) {
  const entry = createEntry(options);
  if (!entry) {
    return { abort() {} };
  }

  shortcuts.push(entry);
  sortShortcuts();
  ensureListening();

  return {
    abort() {
      entry.abort();
    },
  };
}

export function createShortcutBag() {
  const controller = new AbortController();
  const { signal } = controller;

  return {
    add(options) {
      return registerShortcut({ ...options, signal });
    },
    abort() {
      controller.abort();
    },
  };
}

export function clearShortcuts() {
  while (shortcuts.length) {
    const entry = shortcuts.pop();
    entry?.abort?.();
  }
  if (listening) {
    document.removeEventListener("keydown", handleKeydown, true);
    listening = false;
  }
}

export function getRegisteredShortcuts() {
  return shortcuts.slice();
}
