import {
  addMarkdownRenderListener,
  removeMarkdownRenderListener,
  renderAllMarkdown,
} from "../../markdown.js";

export class MarkdownObserver {
  #listener = null;
  #listening = false;

  constructor({ root, onRender } = {}) {
    this.root = root || null;
    this.onRender = typeof onRender === "function" ? onRender : null;
    this.observer = null;
    this.isObserving = false;
    this.#listener = (element) => this.#handleRendered(element);
  }

  start() {
    this.resume();
    this.renderAll();
  }

  resume(nodes = null) {
    if (!this.root) return;

    if (!this.observer) {
      this.observer = new MutationObserver((mutations) => this.#handleMutations(mutations));
    }

    if (!this.isObserving) {
      this.observer.observe(this.root, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      this.isObserving = true;
    }

    this.#ensureListener();

    if (nodes) {
      this.renderAll(nodes);
    }
  }

  pause() {
    this.disconnect();
  }

  disconnect() {
    if (this.observer && this.isObserving) {
      this.observer.disconnect();
      this.isObserving = false;
    }
    this.#removeListener();
  }

  stop() {
    this.disconnect();
    this.observer = null;
  }

  renderAll(nodes = null) {
    if (!this.root) return;

    this.#ensureListener();
    renderAllMarkdown(this.root, nodes);
  }

  #handleMutations(mutations) {
    if (!mutations || mutations.length === 0) return;

    const targets = [];

    mutations.forEach((mutation) => {
      if (mutation.type === "characterData") {
        const parent = mutation.target?.parentElement;
        const container = parent?.closest?.(".markdown-body");
        if (container) {
          targets.push(container);
        }
        return;
      }

      if (mutation.type !== "childList") return;

      const targetNode = mutation.target;
      if (targetNode instanceof Node) {
        targets.push(targetNode);
      }

      mutation.addedNodes.forEach((node) => {
        targets.push(node);
      });
    });

    if (targets.length > 0) {
      this.renderAll(targets);
    }
  }

  #handleRendered(element) {
    if (!element || !this.root) return;
    if (element instanceof Node) {
      const rootNode = /** @type {Node} */ (this.root);
      if (rootNode !== element) {
        let contains = false;
        if (typeof rootNode.contains === "function") {
          contains = rootNode.contains(element);
        } else if (
          typeof rootNode.compareDocumentPosition === "function" &&
          element instanceof Node
        ) {
          contains = Boolean(
            rootNode.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_CONTAINED_BY,
          );
        }
        if (!contains) {
          return;
        }
      }
    }

    if (this.onRender) {
      this.onRender(element);
    }

    if (typeof document !== "undefined") {
      document.dispatchEvent(new CustomEvent("markdown:rendered", { detail: { element } }));
    }
  }

  #ensureListener() {
    if (this.#listening) return;
    addMarkdownRenderListener(this.#listener);
    this.#listening = true;
  }

  #removeListener() {
    if (!this.#listening) return;
    removeMarkdownRenderListener(this.#listener);
    this.#listening = false;
  }
}
