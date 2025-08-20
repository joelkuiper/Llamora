export function renderMarkdown(text) {
  const rawHtml = marked.parse(text, { gfm: true, breaks: true });
  return DOMPurify.sanitize(rawHtml);
}

export function renderMarkdownInElement(el, text) {
  if (!el) return;
  const src = text !== undefined ? text : el.textContent || "";
  const markdownHtml = renderMarkdown(src);
  el.innerHTML = markdownHtml;
  el.dataset.rendered = "true";
}

export function renderAllMarkdown(root) {
  root.querySelectorAll('.message .markdown-body').forEach((el) => {
    if (el.dataset.rendered !== 'true' && !el.querySelector("#typing-indicator")) {
      renderMarkdownInElement(el, el.textContent);
    }
  });
}
