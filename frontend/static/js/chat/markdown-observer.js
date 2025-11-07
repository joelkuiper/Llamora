import { TYPING_INDICATOR_SELECTOR } from "../typing-indicator.js";
import { renderMarkdownInElement } from "../markdown.js";

const MARKDOWN_SELECTOR = ".message .markdown-body";

function collectTargetsFromNode(node, targets) {
  if (!node) return;

  if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
    node.querySelectorAll?.(MARKDOWN_SELECTOR).forEach((el) => {
      targets.add(el);
    });
    return;
  }

  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const element = /** @type {Element} */ (node);

  if (
    element.matches?.(MARKDOWN_SELECTOR) ||
    (element.matches?.(".markdown-body") && element.closest?.(".message"))
  ) {
    targets.add(element);
  }

  element.querySelectorAll?.(MARKDOWN_SELECTOR).forEach((el) => {
    targets.add(el);
  });
}

function shouldRender(el) {
  if (!el || !(el instanceof Element) || !el.isConnected) return false;
  if (el.dataset.rendered === "true") return false;
  if (el.querySelector(TYPING_INDICATOR_SELECTOR)) return false;
  return true;
}

export class MarkdownObserver {
  constructor({ root, onRender } = {}) {
    this.root = root || null;
    this.onRender = typeof onRender === "function" ? onRender : null;
    this.observer = null;
    this.isObserving = false;
  }

  start() {
    this.resume();
    this.renderAll();
  }

  resume(nodes = null) {
    if (!this.root) return;

    if (!this.observer) {
      this.observer = new MutationObserver((mutations) =>
        this.#handleMutations(mutations)
      );
    }

    if (!this.isObserving) {
      this.observer.observe(this.root, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      this.isObserving = true;
    }

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
  }

  stop() {
    this.disconnect();
    this.observer = null;
  }

  renderAll(nodes = null) {
    if (!this.root) return;

    const targets = new Set();

    if (!nodes) {
      collectTargetsFromNode(this.root, targets);
    } else if (nodes instanceof NodeList || Array.isArray(nodes)) {
      nodes.forEach((node) => collectTargetsFromNode(node, targets));
    } else if (nodes instanceof Node) {
      collectTargetsFromNode(nodes, targets);
    }

    targets.forEach((el) => {
      if (!shouldRender(el)) return;
      renderMarkdownInElement(el);
      if (this.onRender) {
        this.onRender(el);
      }
      if (typeof document !== "undefined") {
        document.dispatchEvent(
          new CustomEvent("markdown:rendered", { detail: { element: el } })
        );
      }
    });
  }

  #handleMutations(mutations) {
    if (!mutations || mutations.length === 0) return;

    const targets = new Set();

    mutations.forEach((mutation) => {
      if (mutation.type === "characterData") {
        const parent = mutation.target?.parentElement;
        const container = parent?.closest?.(".markdown-body");
        if (container) {
          targets.add(container);
        }
        return;
      }

      if (mutation.type !== "childList") return;

      const targetNode = mutation.target;
      if (targetNode instanceof Node) {
        collectTargetsFromNode(targetNode, targets);
      }

      mutation.addedNodes.forEach((node) => {
        collectTargetsFromNode(node, targets);
      });
    });

    if (targets.size > 0) {
      this.renderAll(Array.from(targets));
    }
  }
}
