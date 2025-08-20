const VOID_TAGS = new Set([
  'AREA','BASE','BR','COL','EMBED','HR','IMG','INPUT','LINK','META','PARAM','SOURCE','TRACK','WBR'
]);

function isVoidElement(el) {
  return el.nodeType === Node.ELEMENT_NODE && VOID_TAGS.has(el.tagName);
}

function isInlineElement(el) {
  if (!(el instanceof Element)) return false;
  const disp = getComputedStyle(el).display || '';
  return disp.startsWith('inline');
}

function getLastNonWhitespaceTextNode(root) {
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return /\S/.test(node.nodeValue || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    }
  });
  let last = null, n;
  while ((n = tw.nextNode())) last = n;
  return last;
}

function getDeepestInlineElement(root) {
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
  let lastInline = null, n = root;
  do {
    if (isInlineElement(n) && !isVoidElement(n)) lastInline = n;
  } while ((n = tw.nextNode()));
  return lastInline;
}

function insertAfterNode(node, toInsert) {
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  if (toInsert.parentNode) toInsert.parentNode.removeChild(toInsert);
  range.insertNode(toInsert);
}

function insertAtEnd(el, toInsert) {
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  if (toInsert.parentNode) toInsert.parentNode.removeChild(toInsert);
  range.insertNode(toInsert);
}

export function positionTypingIndicator(root, typingEl) {
  const lastText = getLastNonWhitespaceTextNode(root);
  if (lastText) {
    const parentEl = lastText.parentElement;
    const inPre =
      parentEl &&
      (parentEl.closest('pre') || (getComputedStyle(parentEl).whiteSpace || '').includes('pre'));

    if (inPre) {
      const v = lastText.nodeValue || '';
      const m = v.match(/[\r\n]+$/);
      if (m) {
        const tail = lastText.splitText(v.length - m[0].length);
        if (typingEl.parentNode) typingEl.parentNode.removeChild(typingEl);
        tail.parentNode.insertBefore(typingEl, tail);
        return;
      }
    }

    insertAfterNode(lastText, typingEl);
    return;
  }

  const inlineEl = getDeepestInlineElement(root);
  if (inlineEl) {
    insertAtEnd(inlineEl, typingEl);
    return;
  }

  let lastEl = root;
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
  let n;
  while ((n = tw.nextNode())) lastEl = n;
  const target = (lastEl && !isVoidElement(lastEl)) ? lastEl : root;

  const zwsp = document.createTextNode('\u200B');
  target.appendChild(zwsp);
  insertAfterNode(zwsp, typingEl);
}
