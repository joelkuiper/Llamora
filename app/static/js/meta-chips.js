export function renderMetaChips(meta, container) {
  if (!container) return;
  container.innerHTML = "";
  const tplEmoji = document.getElementById('emoji-chip-template');
  const tplKeyword = document.getElementById('keyword-chip-template');
  if (meta && meta.emoji && tplEmoji) {
    const e = tplEmoji.content.firstElementChild.cloneNode(true);
    e.textContent = meta.emoji;
    e.classList.add('chip-enter');
    container.appendChild(e);
  }
  if (meta && Array.isArray(meta.keywords) && tplKeyword) {
    const base = tplKeyword.dataset.url || tplKeyword.content.firstElementChild.getAttribute('hx-get') || '/search';
    meta.keywords.forEach((k) => {
      const a = tplKeyword.content.firstElementChild.cloneNode(true);
      const url = `${base}?q=${encodeURIComponent(k)}`;
      a.textContent = k;
      a.href = url;
      a.setAttribute('hx-get', url);
      a.classList.add('chip-enter');
      container.appendChild(a);
    });
  }
  if (typeof htmx !== 'undefined') {
    htmx.process(container);
  }
}
