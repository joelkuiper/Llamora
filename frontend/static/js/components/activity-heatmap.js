/**
 * When a heatmap's months don't fit on one row, they carry on in the next
 * row from the left, on the same columns as the rows above (so the last row
 * isn't spread across the width). Without this script the months simply wrap
 * (the stylesheet's fallback).
 */
export class ActivityHeatmapElement extends HTMLElement {
  #observer = null;
  #frame = 0;
  #columns = 0;

  connectedCallback() {
    this.#observer = new ResizeObserver(() => this.#schedule());
    this.#observer.observe(this);
    this.#schedule();
  }

  disconnectedCallback() {
    this.#observer?.disconnect();
    this.#observer = null;
    cancelAnimationFrame(this.#frame);
  }

  #schedule() {
    cancelAnimationFrame(this.#frame);
    this.#frame = requestAnimationFrame(() => this.#layout());
  }

  #layout() {
    const months = Array.from(this.querySelectorAll(":scope > .activity-heatmap__month"));
    if (!months.length) return;

    const gap = Number.parseFloat(getComputedStyle(this).columnGap) || 0;
    const widths = months.map((month) => month.offsetWidth);
    const oneRow = widths.reduce((sum, width) => sum + width, 0) + gap * (widths.length - 1);
    // All on one row when they fit as they are; otherwise as many columns as
    // fit the widest month, so every column can hold any month.
    const fit = Math.floor((this.clientWidth + gap) / (Math.max(...widths) + gap));
    const columns =
      oneRow <= this.clientWidth ? months.length : Math.max(1, Math.min(months.length, fit));
    if (columns === this.#columns) return;
    this.#columns = columns;

    const wrapped = columns < months.length;
    this.classList.toggle("is-wrapped", wrapped);
    this.style.setProperty("--heatmap-columns", String(columns));
    months.forEach((month, index) => {
      month.classList.toggle("is-row-start", wrapped && index % columns === 0);
    });
  }
}

if (!customElements.get("activity-heatmap")) {
  customElements.define("activity-heatmap", ActivityHeatmapElement);
}
