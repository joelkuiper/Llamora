const DEFAULT_OPTIONS = {
  minLength: 1,
  caseSensitive: false,
  emitInputEvent: true,
  maxCandidates: 50,
  prepareQuery: null,
  prepareCandidate: null,
  onCommit: null,
};

function isInlineWrapper(node) {
  return node instanceof HTMLElement && node.classList.contains("inline-autocomplete");
}

function pruneAncestorWrappers(wrapper) {
  let ancestor = wrapper?.parentElement;
  while (isInlineWrapper(ancestor)) {
    const parentNode = ancestor.parentNode;
    if (!parentNode) {
      break;
    }

    const children = Array.from(ancestor.childNodes);
    parentNode.insertBefore(wrapper, ancestor);
    for (const child of children) {
      if (child !== wrapper) {
        parentNode.insertBefore(child, ancestor);
      }
    }
    parentNode.removeChild(ancestor);
    ancestor = wrapper.parentElement;
  }
}

function releaseAncestorEmptyClasses(wrapper) {
  let current = wrapper;
  while (current instanceof HTMLElement) {
    if (current.classList.contains("inline-autocomplete")) {
      current.classList.remove("inline-autocomplete--empty");
    }
    current = current.parentElement;
  }
}

export function ensureInlineAutocompleteElements(input) {
  if (!(input instanceof HTMLElement)) return null;
  const parent = input.parentNode;
  if (!parent) return null;

  let wrapper = null;

  const directParent = input.parentElement;
  if (isInlineWrapper(directParent)) {
    wrapper = directParent;
  } else {
    const closestWrapper = input.closest(".inline-autocomplete");
    if (isInlineWrapper(closestWrapper)) {
      wrapper = closestWrapper;
      if (wrapper !== input.parentElement) {
        wrapper.appendChild(input);
      }
    }
  }

  if (wrapper) {
    pruneAncestorWrappers(wrapper);
    wrapper.classList.add("inline-autocomplete");
    releaseAncestorEmptyClasses(wrapper);
    input.classList.add("inline-autocomplete__input");
    return { wrapper, ownsWrapper: false };
  }

  wrapper = document.createElement("span");
  wrapper.className = "inline-autocomplete inline-autocomplete--empty";
  parent.insertBefore(wrapper, input);
  wrapper.appendChild(input);
  input.classList.add("inline-autocomplete__input");
  return { wrapper, ownsWrapper: true };
}

function asArray(entry) {
  if (entry == null) return [];
  if (Array.isArray(entry)) return entry;
  return [entry];
}

function sanitizeString(value) {
  return typeof value === "string" ? value.trim() : "";
}

export class InlineAutocompleteController {
  #input;
  #options;
  #wrapper;
  #candidates;
  #currentSuggestion;
  #ownsWrapper;
  #typedPrefix;
  #pendingDeletion;
  #inputHandler;
  #keydownHandler;
  #blurHandler;
  #focusHandler;
  #rawEntries;

  constructor(input, options = {}) {
    if (!(input instanceof HTMLInputElement)) {
      throw new TypeError("InlineAutocompleteController requires an input element");
    }

    this.#input = input;
    this.#options = { ...DEFAULT_OPTIONS, ...options };
    this.#candidates = [];
    this.#rawEntries = [];
    this.#currentSuggestion = null;
    this.#ownsWrapper = false;
    this.#typedPrefix = input.value ?? "";
    this.#pendingDeletion = false;

    this.#wrapInput();
    this.#attachListeners();
    this.#updateSuggestion();
  }

  destroy() {
    this.#detachListeners();
    this.#unwrapInput();
    this.#candidates = [];
    this.#rawEntries = [];
    this.#currentSuggestion = null;
    this.#wrapper = null;
    this.#ownsWrapper = false;
    this.#input = null;
    this.#typedPrefix = "";
    this.#pendingDeletion = false;
  }

  setCandidates(entries) {
    const items = asArray(entries).slice(0, this.#options.maxCandidates);
    this.#rawEntries = items.slice();
    const processed = [];
    const map = new Map();


    for (const entry of items) {
      let value;
      let tokens;
      let display;
      if (typeof entry === "string") {
        value = sanitizeString(entry);
        tokens = [value];
        display = value;
      } else if (entry && typeof entry.value === "string") {
        value = sanitizeString(entry.value);
        tokens = asArray(entry.tokens ?? entry.alternates ?? entry.aliases ?? [entry.value]);
        display = sanitizeString(entry.display ?? entry.displayValue ?? value);
      } else {
        continue;
      }

      if (!value) continue;
      if (!display) display = value;

      const normalizedTokens = [];
      for (const token of tokens) {
        const raw = sanitizeString(token);
        if (!raw) continue;
        const prepared = this.#options.prepareCandidate
          ? this.#options.prepareCandidate(raw)
          : raw;
        const normalized = this.#options.caseSensitive
          ? prepared
          : prepared.toLowerCase();
        if (!normalized) continue;
        if (!normalizedTokens.includes(normalized)) {
          normalizedTokens.push(normalized);
        }
      }

      if (!normalizedTokens.length) continue;

      if (map.has(value)) {
        const existing = map.get(value);
        for (const token of normalizedTokens) {
          if (!existing.normalizedTokens.includes(token)) {
            existing.normalizedTokens.push(token);
          }
        }
        continue;
      }

      const record = { value, display, normalizedTokens };
      map.set(value, record);
      processed.push(record);
      if (processed.length >= this.#options.maxCandidates) {
        break;
      }
    }

    this.#candidates = processed;
    this.#updateSuggestion();
  }

  addCandidate(entry, { prepend = true } = {}) {
    const items = this.#rawEntries.slice();
    if (prepend) {
      items.unshift(entry);
    } else {
      items.push(entry);
    }
    this.setCandidates(items.slice(0, this.#options.maxCandidates));
  }

  clearCandidates() {
    this.setCandidates([]);
  }

  #wrapInput() {
    if (this.#wrapper) return;
    const input = this.#input;
    const prepared = ensureInlineAutocompleteElements(input);
    if (!prepared) return;

    this.#wrapper = prepared.wrapper;
    this.#ownsWrapper = prepared.ownsWrapper;
  }

  #unwrapInput() {
    const input = this.#input;
    const wrapper = this.#wrapper;
    if (!input || !wrapper) return;

    input.classList.remove("inline-autocomplete__input");

    if (!this.#ownsWrapper) {
      return;
    }

    const parent = wrapper.parentNode;
    if (parent) {
      parent.insertBefore(input, wrapper);
      parent.removeChild(wrapper);
    }
  }

  #attachListeners() {
    const input = this.#input;
    if (!input) return;

    this.#inputHandler = (event) => this.#updateSuggestion({ fromUserInput: true, inputEvent: event });
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#blurHandler = () => {
      this.#clearSuggestion({ restore: true });
    };
    this.#focusHandler = () => {
      this.#updateSuggestion();
    };

    input.addEventListener("input", this.#inputHandler);
    input.addEventListener("keydown", this.#keydownHandler);
    input.addEventListener("blur", this.#blurHandler);
    input.addEventListener("focus", this.#focusHandler);
  }

  #detachListeners() {
    const input = this.#input;
    if (input) {
      input.removeEventListener("input", this.#inputHandler);
      input.removeEventListener("keydown", this.#keydownHandler);
      input.removeEventListener("blur", this.#blurHandler);
      input.removeEventListener("focus", this.#focusHandler);
    }
  }

  #normalizeQuery(raw) {
    const prepared = this.#options.prepareQuery
      ? this.#options.prepareQuery(raw)
      : raw;
    if (!prepared) {
      return "";
    }
    return this.#options.caseSensitive ? prepared : prepared.toLowerCase();
  }

  #updateSuggestion({ fromUserInput = false, inputEvent = null } = {}) {
    const input = this.#input;
    if (!input) return;
    const rawValue = input.value ?? "";

    if (fromUserInput) {
      this.#typedPrefix = rawValue;
      const inputType = inputEvent?.inputType ?? "";
      if (inputType && inputType.startsWith("delete")) {
        this.#pendingDeletion = true;
        this.#currentSuggestion = null;
        this.#wrapper?.classList.add("inline-autocomplete--empty");
        if (typeof input.setSelectionRange === "function") {
          const collapseIndex = rawValue.length;
          input.setSelectionRange(collapseIndex, collapseIndex);
        }
        return;
      }
      this.#pendingDeletion = false;
    } else if (this.#currentSuggestion == null) {
      this.#typedPrefix = rawValue;
    } else if (this.#typedPrefix == null) {
      this.#typedPrefix = rawValue;
    }

    if (this.#pendingDeletion) {
      return;
    }

    const prefix = this.#typedPrefix ?? "";
    const match = this.#resolveMatch(prefix);

    if (!match) {
      this.#currentSuggestion = null;
      this.#wrapper?.classList.add("inline-autocomplete--empty");
      if (input.value !== prefix) {
        input.value = prefix;
      }
      if (typeof input.setSelectionRange === "function") {
        const collapseIndex = prefix.length;
        input.setSelectionRange(collapseIndex, collapseIndex);
      }
      return;
    }

    const suggestion = match.candidate.value;
    const typedLength = prefix.length;
    if (!suggestion || suggestion.length <= typedLength) {
      this.#currentSuggestion = null;
      this.#wrapper?.classList.add("inline-autocomplete--empty");
      if (input.value !== prefix) {
        input.value = prefix;
      }
      if (typeof input.setSelectionRange === "function") {
        input.setSelectionRange(typedLength, typedLength);
      }
      return;
    }

    this.#currentSuggestion = suggestion;
    this.#wrapper?.classList.remove("inline-autocomplete--empty");

    if (input.value !== suggestion) {
      input.value = suggestion;
    }
    if (typeof input.setSelectionRange === "function") {
      input.setSelectionRange(typedLength, suggestion.length);
    }
  }

  #clearSuggestion({ restore = false } = {}) {
    this.#currentSuggestion = null;
    this.#wrapper?.classList.add("inline-autocomplete--empty");
    this.#pendingDeletion = false;

    if (!restore) return;

    const input = this.#input;
    if (!input) return;

    const prefix = this.#typedPrefix ?? "";
    if (input.value !== prefix) {
      input.value = prefix;
    }
    if (typeof input.setSelectionRange === "function") {
      const collapseIndex = prefix.length;
      input.setSelectionRange(collapseIndex, collapseIndex);
    }
  }

  #resolveMatch(rawValue) {
    const query = sanitizeString(rawValue);
    if (!query || query.length < this.#options.minLength) {
      return null;
    }

    const normalizedQuery = this.#normalizeQuery(query);
    if (!normalizedQuery || normalizedQuery.length < this.#options.minLength) {
      return null;
    }

    let bestMatch = null;

    for (const candidate of this.#candidates) {
      if (candidate.value.length <= rawValue.length) {
        continue;
      }
      const haystack = this.#options.caseSensitive
        ? candidate.display ?? candidate.value
        : (candidate.display ?? candidate.value).toLowerCase();
      for (const token of candidate.normalizedTokens) {
        if (!token.startsWith(normalizedQuery)) {
          continue;
        }

        const tokenIndex = haystack.indexOf(token);
        const comparableIndex = tokenIndex >= 0 ? tokenIndex : Infinity;
        const bestTokenLength = bestMatch?.token?.length ?? Infinity;
        const bestComparableIndex =
          bestMatch?.tokenIndex != null && bestMatch.tokenIndex >= 0
            ? bestMatch.tokenIndex
            : Infinity;
        const matchLength = Math.min(normalizedQuery.length, token.length);
        const match = {
          candidate,
          token,
          tokenIndex,
          matchLength,
          queryLength: normalizedQuery.length,
        };

        if (
          !bestMatch ||
          token.length < bestTokenLength ||
          (token.length === bestTokenLength && comparableIndex < bestComparableIndex)
        ) {
          bestMatch = match;
        }

        if (token.length === normalizedQuery.length) {
          return bestMatch;
        }
      }
    }
    return bestMatch;
  }

  #handleKeydown(event) {
    if (!this.#currentSuggestion) return;
    if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      this.#applySuggestion("tab");
    } else if (event.key === "Enter") {
      this.#applySuggestion("enter");
    } else if (event.key === "ArrowRight") {
      const { selectionStart, selectionEnd, value } = this.#input;
      if (selectionStart === value.length && selectionEnd === value.length) {
        this.#applySuggestion("arrow");
      }
    }
  }

  #applySuggestion(trigger) {
    const input = this.#input;
    if (!input || !this.#currentSuggestion) return;
    const suggestion = this.#currentSuggestion;
    input.value = suggestion;
    this.#typedPrefix = suggestion;
    if (typeof input.setSelectionRange === "function") {
      const end = suggestion.length;
      input.setSelectionRange(end, end);
    }
    const committed = suggestion;
    this.#currentSuggestion = null;
    if (this.#options.emitInputEvent) {
      const evt = new Event("input", { bubbles: true, cancelable: false });
      input.dispatchEvent(evt);
    }
    this.#updateSuggestion();
    if (typeof this.#options.onCommit === "function") {
      try {
        this.#options.onCommit(committed, trigger);
      } catch (error) {
        // Ignore errors thrown by external commit handlers.
      }
    }
  }
}
