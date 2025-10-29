import { computeSharedPrefixLength } from "./incremental-markdown-utils.js";

const HASH_LIMIT = 512;
const TEXT_SAMPLE_LIMIT = 256;

function hashString(input) {
  // A tiny, deterministic hash to avoid keeping full text/HTML around.
  let hash = 0;
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash << 5) - hash + input.charCodeAt(i);
    hash |= 0; // Convert to 32-bit integer
  }
  return hash.toString(36);
}

function signatureForNode(node) {
  if (!node) return "";
  const type = node.nodeType;
  if (type === Node.TEXT_NODE) {
    return `text:${node.textContent ?? ""}`;
  }
  if (type === Node.COMMENT_NODE) {
    return `comment:${node.nodeValue ?? ""}`;
  }
  if (type === Node.ELEMENT_NODE) {
    const tag = node.tagName?.toLowerCase() ?? "";
    const attrParts = [];
    const attributes = node.attributes ?? [];
    for (let i = 0; i < attributes.length; i += 1) {
      const attr = attributes[i];
      if (!attr) continue;
      const name = attr.name ?? "";
      const value = (attr.value ?? "").slice(0, HASH_LIMIT);
      attrParts.push(`${name}=${value}`);
    }
    const attributeHash = attrParts.length > 0 ? hashString(attrParts.join("|")) : "";

    // Hash a small sample of text content so nodes with identical structure but
    // very different payloads (e.g., streaming markdown paragraphs) still
    // register as distinct while avoiding full subtree serialization.
    const textSample = (node.textContent ?? "").slice(0, TEXT_SAMPLE_LIMIT);
    const textHash = textSample ? hashString(textSample) : "";

    return `el:${tag}:${node.childElementCount}:${attributeHash}:${textHash}`;
  }
  return `node:${type}`;
}

export class IncrementalMarkdownRenderer {
  constructor(container) {
    this.container = container;
    this.currentHtml = "";
    this.nodes = [];
    this.signatures = [];
  }

  update(html) {
    if (!this.container) return false;

    const normalized = typeof html === "string" ? html : "";
    if (normalized === this.currentHtml) {
      return false;
    }

    const template = document.createElement("template");
    template.innerHTML = normalized;
    const newNodes = Array.from(template.content.childNodes);
    const newSignatures = newNodes.map((node) => signatureForNode(node));

    const shared = computeSharedPrefixLength(this.signatures, newSignatures);

    const removedCount = this.nodes.length - shared;
    while (this.nodes.length > shared) {
      const node = this.nodes.pop();
      if (!node) continue;
      if (node.parentNode === this.container) {
        this.container.removeChild(node);
      } else {
        node.parentNode?.removeChild(node);
      }
      this.signatures.pop();
    }

    const anchor = this.#typingAnchor();
    let appended = 0;
    for (let i = shared; i < newNodes.length; i += 1) {
      const node = newNodes[i];
      this.container.insertBefore(node, anchor);
      this.nodes.push(node);
      this.signatures.push(newSignatures[i]);
      appended += 1;
    }

    this.currentHtml = normalized;
    this.signatures = newSignatures.slice();
    this.nodes = this.nodes.slice(0, newSignatures.length);

    return removedCount > 0 || appended > 0;
  }

  reset() {
    if (!this.container) return;
    while (this.nodes.length > 0) {
      const node = this.nodes.pop();
      if (node?.parentNode === this.container) {
        this.container.removeChild(node);
      } else {
        node?.parentNode?.removeChild(node);
      }
    }
    this.signatures = [];
    this.currentHtml = "";
  }

  #typingAnchor() {
    if (!this.container) return null;
    const typing = this.container.querySelector?.("#typing-indicator");
    if (typing && typing.parentNode === this.container) {
      return typing;
    }
    return null;
  }
}

IncrementalMarkdownRenderer.signatureForNode = signatureForNode;
