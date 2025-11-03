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

    const staleGhosts = ancestor.querySelectorAll(".inline-autocomplete__ghost");
    for (const node of staleGhosts) {
      node.remove();
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

function resetGhost(wrapper) {
  if (!wrapper) return null;
  const existingGhosts = wrapper.querySelectorAll(".inline-autocomplete__ghost");
  for (const node of existingGhosts) {
    node.remove();
  }

  const ghost = document.createElement("span");
  ghost.className = "inline-autocomplete__ghost";
  ghost.setAttribute("aria-hidden", "true");
  wrapper.appendChild(ghost);
  return ghost;
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
    const ghost = resetGhost(wrapper);
    wrapper.classList.add("inline-autocomplete");
    releaseAncestorEmptyClasses(wrapper);
    input.classList.add("inline-autocomplete__input");
    return { wrapper, ghost, ownsWrapper: false };
  }

  wrapper = document.createElement("span");
  wrapper.className = "inline-autocomplete inline-autocomplete--empty";
  parent.insertBefore(wrapper, input);
  wrapper.appendChild(input);
  const ghost = resetGhost(wrapper);
  input.classList.add("inline-autocomplete__input");
  return { wrapper, ghost, ownsWrapper: true };
}

function asArray(entry) {
  if (entry == null) return [];
  if (Array.isArray(entry)) return entry;
  return [entry];
}

function sanitizeString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export class InlineAutocompleteController {
  #input;
  #options;
  #wrapper;
  #ghost;
  #candidates;
  #currentSuggestion;
  #ownsWrapper;
  #inputHandler;
  #keydownHandler;
  #blurHandler;
  #focusHandler;
  #resizeHandler;
  #rawEntries;
  #pendingStyleSync;

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


    this.#wrapInput();
    this.#attachListeners();
    this.#syncStyles();
    this.#updateSuggestion();
  }

  destroy() {
    this.#detachListeners();
    this.#unwrapInput();
    this.#candidates = [];
    this.#rawEntries = [];
    this.#currentSuggestion = null;
    this.#ghost = null;
    this.#wrapper = null;
    this.#ownsWrapper = false;
    this.#input = null;
    this.#pendingStyleSync = null;
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
    this.#ghost = prepared.ghost;
    this.#ownsWrapper = prepared.ownsWrapper;
  }

  #unwrapInput() {
    const input = this.#input;
    const wrapper = this.#wrapper;
    if (!input || !wrapper) return;

    input.classList.remove("inline-autocomplete__input");

    if (this.#ghost && this.#ghost.parentNode === wrapper) {
      wrapper.removeChild(this.#ghost);
    }

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

    this.#inputHandler = () => this.#updateSuggestion();
    this.#keydownHandler = (event) => this.#handleKeydown(event);
    this.#blurHandler = () => {
      this.#clearGhost();
      this.#scheduleStyleSync();
    };
    this.#focusHandler = () => {
      this.#updateSuggestion();
      this.#scheduleStyleSync();
    };
    this.#resizeHandler = () => this.#syncStyles();

    input.addEventListener("input", this.#inputHandler);
    input.addEventListener("keydown", this.#keydownHandler);
    input.addEventListener("blur", this.#blurHandler);
    input.addEventListener("focus", this.#focusHandler);
    if (typeof window !== "undefined") {
      window.addEventListener("resize", this.#resizeHandler);
    }

  }

  #detachListeners() {
    const input = this.#input;
    if (input) {
      input.removeEventListener("input", this.#inputHandler);
      input.removeEventListener("keydown", this.#keydownHandler);
      input.removeEventListener("blur", this.#blurHandler);
      input.removeEventListener("focus", this.#focusHandler);
    }
    if (typeof window !== "undefined") {
      window.removeEventListener("resize", this.#resizeHandler);
      if (this.#pendingStyleSync != null) {
        window.cancelAnimationFrame(this.#pendingStyleSync);
        this.#pendingStyleSync = null;
      }
    }
  }

  #scheduleStyleSync() {
    if (typeof window === "undefined") {
      this.#syncStyles();
      return;
    }

    if (this.#pendingStyleSync != null) {
      return;
    }

    this.#pendingStyleSync = window.requestAnimationFrame(() => {
      this.#pendingStyleSync = null;
      this.#syncStyles();
    });
  }

  #syncStyles() {
    const input = this.#input;
    const ghost = this.#ghost;
    if (!input || !ghost) return;

    const computed = window.getComputedStyle(input);
    const properties = [
      "fontSize",
      "fontFamily",
      "fontWeight",
      "fontStyle",
      "letterSpacing",
      "textTransform",
      "textAlign",
      "lineHeight",
      "borderRadius",
      "transform",
      "transformOrigin",
    ];

    for (const prop of properties) {
      ghost.style[prop] = computed[prop];
    }

    const assignBoxValue = (property, value) => {
      ghost.style[property] = value && value !== "auto" ? value : "0px";
    };

    ghost.style.color = computed.color;
    ghost.style.paddingTop = computed.paddingTop;
    ghost.style.paddingRight = computed.paddingRight;
    ghost.style.paddingBottom = computed.paddingBottom;
    ghost.style.paddingLeft = computed.paddingLeft;
    assignBoxValue("left", computed.borderLeftWidth);
    assignBoxValue("right", computed.borderRightWidth);
    assignBoxValue("top", computed.borderTopWidth);
    assignBoxValue("bottom", computed.borderBottomWidth);

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

  #updateSuggestion() {
    const input = this.#input;
    if (!input) return;
    const value = input.value ?? "";
    const match = this.#resolveMatch(value);

    if (!match) {
      this.#currentSuggestion = null;
      this.#renderGhost();
      this.#wrapper?.classList.add("inline-autocomplete--empty");
      return;
    }

    const suggestion = match.candidate.value;
    const displayValue = match.candidate.display ?? suggestion;
    const normalizedDisplay = this.#options.caseSensitive
      ? displayValue
      : displayValue.toLowerCase();
    const fallbackIndex = normalizedDisplay.indexOf(match.token ?? "");
    const start = Math.max(0, match.tokenIndex ?? fallbackIndex);
    const maxAvailable = Math.max(displayValue.length - start, 0);
    const maskReference = match.matchLength ?? match.queryLength ?? match.token?.length ?? 0;
    const maskBase = Math.min(maskReference, value.length);
    const maskedLength = Math.min(maxAvailable, Math.max(0, maskBase));
    let leading = displayValue.slice(0, start);
    let masked = displayValue.slice(start, start + maskedLength);
    const trailing = displayValue.slice(start + maskedLength);

    if (!leading && !trailing) {
      this.#currentSuggestion = null;
      this.#renderGhost();
      this.#wrapper?.classList.add("inline-autocomplete--empty");
      return;
    }

    this.#currentSuggestion = suggestion;
    this.#renderGhost({ leading, masked, trailing });
    this.#wrapper?.classList.remove("inline-autocomplete--empty");
  }

  #renderGhost(parts) {
    if (!this.#ghost) return;
    if (!parts) {
      this.#ghost.textContent = "";
      return;
    }

    const { leading = "", masked = "", trailing = "" } = parts;

    if (!leading && !masked && !trailing) {
      this.#ghost.textContent = "";
      return;
    }

    let html = "";
    if (leading) {
      html += escapeHtml(leading);
    }
    if (masked) {
      html += `<span class="inline-autocomplete__mask">${escapeHtml(masked)}</span>`;
    }
    if (trailing) {
      html += escapeHtml(trailing);
    }

    this.#ghost.innerHTML = html;
  }

  #clearGhost() {
    this.#currentSuggestion = null;
    this.#renderGhost();
    this.#wrapper?.classList.add("inline-autocomplete--empty");
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
    input.value = this.#currentSuggestion;
    if (this.#options.emitInputEvent) {
      const evt = new Event("input", { bubbles: true, cancelable: false });
      input.dispatchEvent(evt);
    }
    this.#updateSuggestion();
    if (typeof this.#options.onCommit === "function") {
      try {
        this.#options.onCommit(this.#currentSuggestion, trigger);
      } catch (error) {
        // Ignore errors thrown by external commit handlers.
      }
    }
  }
}
