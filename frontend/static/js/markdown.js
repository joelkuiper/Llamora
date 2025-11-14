import { TYPING_INDICATOR_SELECTOR } from "./typing-indicator.js";

const escapeHtml = (value) =>
  String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

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
      src = el.textContent || "";
    }
  }

  if (el.dataset.markdownSource !== src) {
    el.dataset.markdownSource = src;
  }

  const markdownHtml = renderMarkdown(src);
  el.innerHTML = markdownHtml;
  el.dataset.rendered = "true";
}

const MARKDOWN_SELECTOR = ".message .markdown-body";

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
      (node.matches?.(".markdown-body") && node.closest?.(".message"))
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

export function renderAllMarkdown(root, nodes = null) {
  if (!root) return;

  const targets = collectMarkdownBodies(root, normalizeNodes(nodes));

  targets.forEach((el) => {
    const isStreaming =
      el.closest("llm-stream")?.dataset.streaming === "true";
    if (isStreaming) {
      // Streaming responses manage their own incremental rendering to avoid deleting the typing indicator mid-update.
      return;
    }
    if (
      el.dataset.rendered !== "true" &&
      !el.querySelector(TYPING_INDICATOR_SELECTOR)
    ) {
      renderMarkdownInElement(el);
    }
  });
}
