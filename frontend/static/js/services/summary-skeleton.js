const metricsCache = new Map();

const buildSampleText = (wordCount) => {
  const count = Math.max(1, Number.isFinite(wordCount) ? wordCount : 12);
  const words = Array.from({ length: count }, () => "word");
  return words.join(" ");
};

const buildMeasurement = (wrapperClass, width, wordCount) => {
  if (!document.body) return null;
  const wrapper = document.createElement("div");
  wrapper.className = wrapperClass;
  wrapper.style.position = "absolute";
  wrapper.style.visibility = "hidden";
  wrapper.style.pointerEvents = "none";
  wrapper.style.left = "-9999px";
  wrapper.style.top = "0";
  wrapper.style.width = `${width}px`;
  wrapper.style.whiteSpace = "normal";

  const paragraph = document.createElement("p");
  paragraph.className = "tag-detail-summary";
  paragraph.style.margin = "0";
  paragraph.style.whiteSpace = "normal";
  paragraph.textContent = buildSampleText(wordCount);

  const lineProbe = document.createElement("p");
  lineProbe.className = "tag-detail-summary";
  lineProbe.style.margin = "0";
  lineProbe.style.whiteSpace = "nowrap";
  lineProbe.textContent = "word";

  wrapper.appendChild(paragraph);
  wrapper.appendChild(lineProbe);
  document.body.appendChild(wrapper);

  const rect = paragraph.getBoundingClientRect();
  const lineRect = lineProbe.getBoundingClientRect();
  const computed = getComputedStyle(paragraph);
  const fontSize = Number.parseFloat(computed.fontSize || "0") || 0;
  let lineHeight = Number.parseFloat(computed.lineHeight || "");
  if (!Number.isFinite(lineHeight) || lineHeight <= 0) {
    lineHeight = lineRect.height > 0 ? lineRect.height : fontSize || 12;
  }
  const lineGap = Math.max(0, lineHeight - fontSize);

  wrapper.remove();

  const lineCount = Math.max(1, Math.round(rect.height / lineHeight));

  if (window.__llamoraSummaryDebug) {
    window.__llamoraSummaryDebug.push({
      wrapperClass,
      width,
      wordCount,
      fontSize,
      lineHeight,
      lineGap,
      rectHeight: rect.height,
      lineRectHeight: lineRect.height,
      lineCount,
    });
  }

  return {
    fontSize,
    lineHeight,
    lineGap,
    lineCount,
  };
};

const getMetricsForContext = (wrapperClass, width, wordCount) => {
  const cacheKey = `${wrapperClass}:${Math.round(width)}:${wordCount}`;
  if (metricsCache.has(cacheKey)) {
    return metricsCache.get(cacheKey);
  }
  const metrics = buildMeasurement(wrapperClass, width, wordCount);
  if (metrics) {
    metricsCache.set(cacheKey, metrics);
  }
  return metrics;
};

const applyMetrics = (skeleton, metrics) => {
  if (!metrics || !(skeleton instanceof HTMLElement)) return;
  skeleton.style.setProperty("--tag-summary-line-height", `${metrics.fontSize}px`);
  skeleton.style.setProperty("--tag-summary-line-gap", `${metrics.lineGap}px`);
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const getLineCountForSummary = (summaryEl) => {
  const overrideRaw = summaryEl?.dataset?.summaryLines ?? "";
  const override = Number.parseInt(overrideRaw, 10);
  if (Number.isFinite(override) && override > 0) {
    return clamp(override, 1, 8);
  }
  const wordsRaw = summaryEl?.dataset?.summaryWords ?? "";
  const words = Number.parseInt(wordsRaw, 10);
  if (!Number.isFinite(words) || words <= 0) {
    return null;
  }
  const isTagsView = summaryEl.classList.contains("tags-view__summary");
  const maxLines = isTagsView ? 6 : 4;
  const width = Math.max(160, summaryEl.getBoundingClientRect().width || 0, 320);
  const metrics = getMetricsForContext(
    isTagsView ? "tags-view__summary" : "tag-detail__summary",
    width,
    words,
  );
  if (!metrics) return null;
  return clamp(metrics.lineCount || 0, 2, maxLines);
};

const buildLineWidths = (lineCount, summaryEl) => {
  if (!lineCount) return [];
  const wordsRaw = summaryEl?.dataset?.summaryWords ?? "";
  const words = Number.parseInt(wordsRaw, 10);
  const isTagsView = summaryEl.classList.contains("tags-view__summary");
  const wordsPerLine = isTagsView ? 13 : 10;
  const widths = [];
  for (let index = 0; index < lineCount; index += 1) {
    if (index === lineCount - 1 && Number.isFinite(words) && words > 0) {
      const remainder = words - (lineCount - 1) * wordsPerLine;
      const ratio = clamp(remainder / wordsPerLine, 0.4, 0.9);
      widths.push(Math.round(ratio * 100));
    } else {
      widths.push(index % 2 === 0 ? 100 : 92);
    }
  }
  return widths;
};

const syncSkeletonLines = (summaryEl, skeleton) => {
  const lineCount = getLineCountForSummary(summaryEl);
  if (!lineCount) return;
  const lineWidths = buildLineWidths(lineCount, summaryEl);
  const existing = Array.from(skeleton.querySelectorAll(".tag-detail-skeleton__line"));
  if (existing.length !== lineCount) {
    existing.forEach((node) => {
      node.remove();
    });
    for (let index = 0; index < lineCount; index += 1) {
      const line = document.createElement("span");
      line.className = "tag-detail-skeleton__line";
      skeleton.appendChild(line);
      existing.push(line);
    }
  }
  existing.slice(0, lineCount).forEach((line, index) => {
    const width = lineWidths[index];
    if (width) {
      line.style.width = `${width}%`;
    }
  });
};

export const syncSummarySkeletons = (root = document) => {
  const skeletons = root.querySelectorAll?.(".tag-detail-skeleton") || [];
  if (!skeletons.length) return;

  if (!window.__llamoraSummaryDebug) {
    window.__llamoraSummaryDebug = [];
  }

  skeletons.forEach((skeleton) => {
    const summaryEl =
      skeleton.closest?.(".tags-view__summary") || skeleton.closest?.(".tag-detail__summary");
    if (!summaryEl) return;
    const width = Math.max(160, summaryEl.getBoundingClientRect().width || 0, 320);
    const wordsRaw = summaryEl.dataset?.summaryWords ?? "";
    const words = Number.parseInt(wordsRaw, 10) || 12;
    const metrics = getMetricsForContext(
      summaryEl.classList.contains("tags-view__summary")
        ? "tags-view__summary"
        : "tag-detail__summary",
      width,
      words,
    );
    if (window.__llamoraSummaryDebug) {
      window.__llamoraSummaryDebug.push({
        context: summaryEl.className,
        width,
        words,
        metrics,
      });
    }
    applyMetrics(skeleton, metrics);
    syncSkeletonLines(summaryEl, skeleton);
  });
};
