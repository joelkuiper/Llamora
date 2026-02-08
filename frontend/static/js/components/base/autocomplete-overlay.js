import { AutocompleteDataStore } from "../../utils/autocomplete-data-store.js";
import { InlineAutocompleteController } from "../../utils/inline-autocomplete.js";

const identityCandidates = (candidates) => Array.isArray(candidates) ? candidates.filter((item) => item != null) : [];

const defaultNormalizeCandidate = (candidate) => {
  if (typeof candidate === "string") {
    return candidate.trim().toLowerCase();
  }
  if (candidate && typeof candidate.value === "string") {
    return candidate.value.trim().toLowerCase();
  }
  return "";
};

const noop = () => {};

export const AutocompleteOverlayMixin = (BaseClass) => {
  if (typeof BaseClass !== "function") {
    throw new TypeError("AutocompleteOverlayMixin expects a base class");
  }

  return class AutocompleteOverlay extends BaseClass {
    constructor(...args) {
      super(...args);
      this._autocompleteController = null;
      this._autocompleteInput = null;
      this._autocompleteStore = null;
      this._unsubscribeAutocomplete = null;
      this._autocompleteInputObserver = null;
      this._autocompleteInputObserverRoot = null;
      this._initializeAutocompleteStore();
    }

    connectedCallback() {
      if (typeof super.connectedCallback === "function") {
        super.connectedCallback();
      }
      this.refreshAutocompleteController({ force: true, reason: "connected" });
    }

    disconnectedCallback() {
      this.cancelAutocompleteFetch();
      this.destroyAutocompleteController();
      this._teardownAutocompleteInputObserver();
      if (typeof super.disconnectedCallback === "function") {
        super.disconnectedCallback();
      }
    }

    _initializeAutocompleteStore() {
      if (this._autocompleteStore) {
        return;
      }
      if (typeof this.getAutocompleteStoreOptions !== "function") {
        return;
      }
      const options = this.getAutocompleteStoreOptions() ?? null;
      if (!options || typeof options.fetchCandidates !== "function") {
        return;
      }

      const storeOptions = { ...options };
      if (storeOptions.getCandidateKey == null) {
        storeOptions.getCandidateKey = (candidate, context) => this.normalizeAutocompleteCandidate(candidate, context);
      }
      if (storeOptions.onError == null) {
        storeOptions.onError = (error, meta) => this.onAutocompleteError(error, meta);
      }

      this._autocompleteStore = new AutocompleteDataStore(storeOptions);
      this._unsubscribeAutocomplete = this._autocompleteStore.subscribe(
        (candidates) => this.applyAutocompleteCandidates(candidates),
        { immediate: false },
      );
    }

    reinitializeAutocompleteStore() {
      this.destroyAutocompleteStore();
      this._initializeAutocompleteStore();
      this.applyAutocompleteCandidates();
    }

    destroyAutocompleteStore() {
      if (!this._autocompleteStore) {
        return;
      }
      if (this._unsubscribeAutocomplete) {
        try {
          this._unsubscribeAutocomplete();
        } catch (error) {
          if (
            error
            && typeof console !== "undefined"
            && typeof console.debug === "function"
          ) {
            console.debug("Failed to unsubscribe autocomplete listener", error);
          }
        }
        this._unsubscribeAutocomplete = null;
      }
      this._autocompleteStore.destroy();
      this._autocompleteStore = null;
    }

    refreshAutocompleteController(options = {}) {
      const { force = false, reason = null } = options ?? {};
      const config = this._resolveAutocompleteInputConfig();
      this._ensureAutocompleteInputObserver(config);
      const input = this._resolveAutocompleteInput(config);
      const current = this._autocompleteInput;
      const controller = this._autocompleteController;
      const needsReinit = force
        || !controller
        || !current
        || !current.isConnected
        || current !== input;
      const hasForce = force === true;
      let reinitialized = false;

      if (!input) {
        if (controller) {
          controller.destroy();
        }
        this._autocompleteController = null;
        this._autocompleteInput = null;
        if (typeof this.onAutocompleteInputChanged === "function") {
          this.onAutocompleteInputChanged(null, current, {
            reason,
            initialized: false,
            force: hasForce,
          });
        }
        return reinitialized;
      }

      if (!needsReinit) {
        if (input !== current && typeof this.onAutocompleteInputChanged === "function") {
          this._autocompleteInput = input;
          this.onAutocompleteInputChanged(input, current, {
            reason,
            initialized: false,
            force: hasForce,
          });
        }
        return reinitialized;
      }

      if (controller) {
        controller.destroy();
      }

      const controllerOptions = typeof this.getAutocompleteControllerOptions === "function"
        ? { ...this.getAutocompleteControllerOptions() }
        : {};
      const originalCommit = controllerOptions.onCommit ?? noop;
      controllerOptions.onCommit = (...args) => {
        try {
          if (typeof this.onAutocompleteCommit === "function") {
            this.onAutocompleteCommit(...args);
          }
        } finally {
          if (typeof originalCommit === "function") {
            originalCommit.apply(this, args);
          }
        }
      };

      this._autocompleteController = new InlineAutocompleteController(
        input,
        controllerOptions,
      );
      this._autocompleteInput = input;
      this.applyAutocompleteCandidates();
      reinitialized = true;

      if (typeof this.onAutocompleteInputChanged === "function") {
        this.onAutocompleteInputChanged(input, current, {
          reason,
          initialized: true,
          force: hasForce,
        });
      }

      return reinitialized;
    }

    destroyAutocompleteController() {
      if (this._autocompleteController) {
        this._autocompleteController.destroy();
      }
      this._autocompleteController = null;
      this._autocompleteInput = null;
    }

    scheduleAutocompleteFetch(options = {}) {
      const store = this._autocompleteStore;
      if (!store) {
        return null;
      }
      const params = typeof this.buildAutocompleteFetchParams === "function"
        ? this.buildAutocompleteFetchParams(options)
        : { query: "", context: {} };
      if (!params) {
        store.cancel();
        return null;
      }
      const { query = "", context = {} } = params;
      const requestOptions = {
        immediate: options.immediate ?? false,
        bypassCache: options.bypassCache ?? false,
      };
      return store.scheduleFetch(query, context, requestOptions);
    }

    cancelAutocompleteFetch() {
      this._autocompleteStore?.cancel();
    }

    clearAutocompleteCache() {
      this._autocompleteStore?.clearCache();
    }

    resetAutocompleteStore(options = {}) {
      this._autocompleteStore?.reset(options);
    }

    setAutocompleteLocalEntries(sourceId, entries) {
      this._autocompleteStore?.setLocalEntries(sourceId, entries);
    }

    applyAutocompleteCandidates(candidates = null) {
      const controller = this._autocompleteController;
      if (!controller) {
        return;
      }
      const source = Array.isArray(candidates)
        ? candidates
        : this._autocompleteStore?.getCandidates() ?? [];
      const entries = typeof this.transformAutocompleteCandidates === "function"
        ? this.transformAutocompleteCandidates(source)
        : identityCandidates(source);
      if (!entries || entries.length === 0) {
        controller.clearCandidates();
        return;
      }
      controller.setCandidates(entries);
    }

    getAutocompleteCandidates() {
      return this._autocompleteStore?.getCandidates() ?? [];
    }

    normalizeAutocompleteCandidate(candidate) {
      return defaultNormalizeCandidate(candidate);
    }

    transformAutocompleteCandidates(candidates) {
      return identityCandidates(candidates);
    }

    buildAutocompleteFetchParams() {
      return { query: "", context: {} };
    }

    getAutocompleteStoreOptions() {
      return null;
    }

    getAutocompleteControllerOptions() {
      return {};
    }

    resolveAutocompleteInput() {
      return null;
    }

    getAutocompleteInputConfig() {
      return null;
    }

    onAutocompleteInputChanged() {}

    _resolveAutocompleteInputConfig() {
      if (typeof this.getAutocompleteInputConfig !== "function") {
        return null;
      }
      const raw = this.getAutocompleteInputConfig() ?? null;
      if (!raw || typeof raw !== "object") {
        return null;
      }

      const config = { ...raw };
      config.selector = typeof config.selector === "string" && config.selector.trim()
        ? config.selector.trim()
        : null;
      config.resolve = typeof config.resolve === "function" ? config.resolve : null;
      config.observe = config.observe ?? !!config.selector;
      config.mutationOptions = typeof config.mutationOptions === "object" && config.mutationOptions
        ? { ...config.mutationOptions }
        : null;
      config.rootResolver = this._createAutocompleteRootResolver(config.root);
      return config;
    }

    _createAutocompleteRootResolver(rootOption) {
      if (typeof rootOption === "function") {
        return () => {
          try {
            return rootOption.call(this) ?? null;
          } catch {
            return null;
          }
        };
      }

      if (typeof rootOption === "string") {
        const selector = rootOption.trim();
        if (!selector) {
          return () => this;
        }
        return () => this._findInScopes(selector) ?? null;
      }

      if (
        rootOption
        && (typeof rootOption.querySelector === "function"
          || rootOption instanceof Document
          || rootOption instanceof DocumentFragment)
      ) {
        return () => rootOption;
      }

      return () => this;
    }

    _findInScopes(selector) {
      if (!selector) {
        return null;
      }
      if (selector === "self") {
        return this;
      }
      if (selector === "document") {
        return this.ownerDocument ?? document;
      }
      const local = typeof this.querySelector === "function"
        ? this.querySelector(selector)
        : null;
      if (local) {
        return local;
      }
      const root = this.getRootNode?.();
      if (root && typeof root.querySelector === "function") {
        const fromRoot = root.querySelector(selector);
        if (fromRoot) {
          return fromRoot;
        }
      }
      const doc = this.ownerDocument ?? document;
      if (typeof doc.querySelector === "function") {
        const fromDoc = doc.querySelector(selector);
        if (fromDoc) {
          return fromDoc;
        }
      }
      return null;
    }

    _resolveAutocompleteInput(config) {
      let input = null;
      if (config?.resolve) {
        try {
          input = config.resolve.call(this, config);
        } catch {
          input = null;
        }
      }

      if (!(input instanceof HTMLInputElement) && config?.selector) {
        const root = config?.rootResolver ? config.rootResolver() : this;
        input = this._querySelectorInRoot(root, config.selector);
      }

      if (
        !(input instanceof HTMLInputElement)
        && typeof this.resolveAutocompleteInput === "function"
      ) {
        input = this.resolveAutocompleteInput();
      }

      return input instanceof HTMLInputElement ? input : null;
    }

    _querySelectorInRoot(root, selector) {
      if (!selector) {
        return null;
      }

      const scopes = [];
      if (root) {
        scopes.push(root);
      }
      const hostRoot = this.getRootNode?.();
      if (hostRoot && hostRoot !== root) {
        scopes.push(hostRoot);
      }
      const doc = this.ownerDocument ?? document;
      if (doc && doc !== root && doc !== hostRoot) {
        scopes.push(doc);
      }

      for (const scope of scopes) {
        try {
          if (typeof scope?.querySelector === "function") {
            const found = scope.querySelector(selector);
            if (found) {
              return found;
            }
          }
        } catch {
          /* no-op */
        }
      }

      return null;
    }

    _ensureAutocompleteInputObserver(config) {
      if (!config || !config.observe) {
        this._teardownAutocompleteInputObserver();
        return;
      }

      const root = config?.rootResolver ? config.rootResolver() : this;
      if (!(root instanceof Node)) {
        this._teardownAutocompleteInputObserver();
        return;
      }

      if (
        this._autocompleteInputObserver
        && this._autocompleteInputObserverRoot === root
      ) {
        return;
      }

      this._teardownAutocompleteInputObserver();

      const observer = new MutationObserver(() => {
        if (!this.isConnected) {
          return;
        }
        this.refreshAutocompleteController({ reason: "mutation" });
      });

      const options = config.mutationOptions ?? { childList: true, subtree: true };
      try {
        observer.observe(root, options);
        this._autocompleteInputObserver = observer;
        this._autocompleteInputObserverRoot = root;
      } catch {
        observer.disconnect();
        this._autocompleteInputObserver = null;
        this._autocompleteInputObserverRoot = null;
      }
    }

    _teardownAutocompleteInputObserver() {
      if (this._autocompleteInputObserver) {
        try {
          this._autocompleteInputObserver.disconnect();
        } catch {
          /* no-op */
        }
      }
      this._autocompleteInputObserver = null;
      this._autocompleteInputObserverRoot = null;
    }

    onAutocompleteCommit() {}

    onAutocompleteError(error) {
      if (
        error
        && typeof console !== "undefined"
        && typeof console.debug === "function"
      ) {
        console.debug("Autocomplete request failed", error);
      }
    }

    get autocompleteController() {
      return this._autocompleteController;
    }

    get autocompleteInput() {
      return this._autocompleteInput;
    }

    get autocompleteStore() {
      return this._autocompleteStore;
    }
  };
};
