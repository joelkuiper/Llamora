import {
  DOMPurify as DOMPurifyGlobal,
  marked as markedGlobal,
} from "./vendor/setup-globals.js";
import { TYPING_INDICATOR_SELECTOR } from "./typing-indicator.js";

const escapeHtml = (value) =>
  String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const globalScope =
  typeof globalThis !== "undefined"
    ? globalThis
    : typeof window !== "undefined"
      ? window
      : {};

const marked = markedGlobal ?? globalScope.marked;
const DOMPurify = DOMPurifyGlobal ?? globalScope.DOMPurify;

const renderer = new marked.Renderer();
renderer.html = (html) => {
  const raw = typeof html === "string" ? html : html?.text ?? "";
  return `<pre class="code-block"><code>${escapeHtml(raw)}</code></pre>`;
};

export function renderMarkdown(text) {
  const rawHtml = marked.parse(text, {
    gfm: true,
    breaks: true,
    renderer,
  });
  return DOMPurify.sanitize(rawHtml);
}

function normalizeMarkdownSource(value) {
  const normalized = String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
  const lines = normalized.split("\n");
  const nonEmpty = lines.filter((line) => line.trim().length > 0);
  if (nonEmpty.length === 0) {
    return "";
  }
  const indent = Math.min(
    ...nonEmpty.map((line) => {
      const match = line.match(/^\s*/);
      return match ? match[0].length : 0;
    }),
  );
  const stripped = lines.map((line) => line.slice(indent));
  while (stripped.length && stripped[0].trim() === "") stripped.shift();
  while (stripped.length && stripped[stripped.length - 1].trim() === "")
    stripped.pop();
  return stripped.join("\n");
}

export function renderMarkdownInElement(el, text) {
  if (!el) return;

  const hasPreRenderedHtml =
    el.dataset.rendered === "true" &&
    el.dataset.markdownSource === undefined &&
    (text === undefined || text === null);

  if (hasPreRenderedHtml) {
    return;
  }

  let src = text;
  if (src === undefined || src === null) {
    if (el.dataset.markdownSource !== undefined) {
      src = el.dataset.markdownSource;
    } else {
      src = normalizeMarkdownSource(el.textContent || "");
    }
  }

  if (el.dataset.markdownSource !== src) {
    el.dataset.markdownSource = src;
  }

  const markdownHtml = renderMarkdown(src);
  const prevDisplay = el.style.display;
  const prevVisibility = el.style.visibility;
  el.style.visibility = "hidden";
  el.innerHTML = markdownHtml;
  el.dataset.rendered = "true";
  void el.offsetHeight;
  el.style.visibility = prevVisibility;
  el.style.display = prevDisplay;
}

const MARKDOWN_SELECTOR = ".entry .markdown-body";

const markdownRenderListeners = new Set();

export function addMarkdownRenderListener(listener) {
  if (typeof listener !== "function") return () => {};
  markdownRenderListeners.add(listener);
  return () => removeMarkdownRenderListener(listener);
}

export function removeMarkdownRenderListener(listener) {
  if (typeof listener !== "function") return;
  markdownRenderListeners.delete(listener);
}

function notifyMarkdownRendered(element) {
  markdownRenderListeners.forEach((listener) => {
    try {
      listener(element);
    } catch (error) {
      // Swallow listener errors to avoid breaking markdown rendering flows.
      if (typeof console !== "undefined" && typeof console.error === "function") {
        console.error(error);
      }
    }
  });
}

function normalizeNodes(nodes) {
  if (!nodes) return [];

  if (nodes instanceof NodeList || Array.isArray(nodes)) {
    return Array.from(nodes).filter((node) => node instanceof Node);
  }

  return nodes instanceof Node ? [nodes] : [];
}

function collectMarkdownBodies(root, nodes) {
  const markdownNodes = new Set();

  const addIfMarkdown = (node) => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

    if (
      node.matches?.(MARKDOWN_SELECTOR) ||
      (node.matches?.(".markdown-body") && node.closest?.(".entry"))
    ) {
      markdownNodes.add(node);
    }

    node.querySelectorAll?.(MARKDOWN_SELECTOR).forEach((el) => {
      markdownNodes.add(el);
    });
  };

  if (nodes.length > 0) {
    nodes.forEach((node) => {
      if (!node) return;

      if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
        node.querySelectorAll?.(MARKDOWN_SELECTOR).forEach((el) => {
          markdownNodes.add(el);
        });
        return;
      }

      addIfMarkdown(node);
    });
  } else if (root) {
    root.querySelectorAll?.(MARKDOWN_SELECTOR).forEach((el) => {
      markdownNodes.add(el);
    });
  }

  return markdownNodes;
}

export function renderAllMarkdown(root, nodes = null, options = {}) {
  if (!root) return;

  const targets = collectMarkdownBodies(root, normalizeNodes(nodes));

  targets.forEach((el) => {
    const isStreaming =
      el.closest("response-stream")?.dataset.streaming === "true";
    if (isStreaming) {
      // Streaming responses manage their own incremental rendering to avoid deleting the typing indicator mid-update.
      return;
    }
    if (
      el.dataset.rendered !== "true" &&
      !el.querySelector(TYPING_INDICATOR_SELECTOR)
    ) {
      renderMarkdownInElement(el);
      if (typeof options.onRender === "function") {
        options.onRender(el);
      }
      notifyMarkdownRendered(el);
    }
  });
}
