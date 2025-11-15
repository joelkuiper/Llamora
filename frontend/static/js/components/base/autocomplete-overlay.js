import { InlineAutocompleteController } from "../../utils/inline-autocomplete.js";
import { AutocompleteDataStore } from "../../utils/autocomplete-data-store.js";

const identityCandidates = (candidates) =>
  Array.isArray(candidates) ? candidates.filter((item) => item != null) : [];

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
      this._initializeAutocompleteStore();
    }

    connectedCallback() {
      if (typeof super.connectedCallback === "function") {
        super.connectedCallback();
      }
      this.refreshAutocompleteController({ force: true });
    }

    disconnectedCallback() {
      this.cancelAutocompleteFetch();
      this.destroyAutocompleteController();
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
        storeOptions.getCandidateKey = (candidate, context) =>
          this.normalizeAutocompleteCandidate(candidate, context);
      }
      if (storeOptions.onError == null) {
        storeOptions.onError = (error, meta) =>
          this.onAutocompleteError(error, meta);
      }

      this._autocompleteStore = new AutocompleteDataStore(storeOptions);
      this._unsubscribeAutocomplete = this._autocompleteStore.subscribe(
        (candidates) => this.applyAutocompleteCandidates(candidates),
        { immediate: false }
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
            error &&
            typeof console !== "undefined" &&
            typeof console.debug === "function"
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
      const { force = false } = options ?? {};
      const input =
        typeof this.resolveAutocompleteInput === "function"
          ? this.resolveAutocompleteInput()
          : null;
      const current = this._autocompleteInput;
      const controller = this._autocompleteController;
      const needsReinit =
        force ||
        !controller ||
        !current ||
        !current.isConnected ||
        current !== input;

      if (!input) {
        if (controller) {
          controller.destroy();
        }
        this._autocompleteController = null;
        this._autocompleteInput = null;
        return;
      }

      if (!needsReinit) {
        return;
      }

      if (controller) {
        controller.destroy();
      }

      const controllerOptions =
        typeof this.getAutocompleteControllerOptions === "function"
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
        controllerOptions
      );
      this._autocompleteInput = input;
      this.applyAutocompleteCandidates();
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
      const params =
        typeof this.buildAutocompleteFetchParams === "function"
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
      const entries =
        typeof this.transformAutocompleteCandidates === "function"
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

    onAutocompleteCommit() {}

    onAutocompleteError(error) {
      if (
        error &&
        typeof console !== "undefined" &&
        typeof console.debug === "function"
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
