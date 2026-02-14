/* global htmx */
import { getActiveDayParts } from "../entries/active-day-store.js";
import { createPopover } from "../popover.js";
import { triggerLabelFlash } from "../utils/motion.js";
import { getValue, setValue } from "../services/lockbox-store.js";
import { transitionHide, transitionShow } from "../utils/transition.js";

const SUMMARY_NAMESPACE = "summary";

const makeDaySummaryKey = (date) => `day:${String(date || "").trim()}`;

const readSummaryPayload = (payload, digest, field) => {
  if (!payload || typeof payload !== "object") return "";
  if (digest != null && String(payload.digest || "") !== String(digest)) return "";
  const value = payload[field];
  return typeof value === "string" ? value : "";
};

const getCachedDaySummary = async (date, digest) => {
  const key = makeDaySummaryKey(date);
  if (!key || key === "day:") return "";
  const payload = await getValue(SUMMARY_NAMESPACE, key);
  return readSummaryPayload(payload, digest, "text");
};

const setCachedDaySummary = async (date, summary, digest) => {
  const key = makeDaySummaryKey(date);
  if (!key || key === "day:") return false;
  if (summary == null) return false;
  return setValue(SUMMARY_NAMESPACE, key, {
    digest: String(digest ?? ""),
    text: summary,
  });
};

export class CalendarControl extends HTMLElement {
  #state = null;
  #btn = null;
  #pop = null;
  #globalListeners = null;
  #summaryRequests = new Map();
  #tooltip = null;
  #tooltipTimer = null;
  #tooltipHideTimer = null;
  #tooltipDate = null;
  #summaryCell = null;

  connectedCallback() {
    this.#btn = this.querySelector("#calendar-btn");
    this.#pop = this.querySelector("#calendar-popover");

    this.#initCalendarPopover();

    if (this.#globalListeners) {
      this.#globalListeners.abort();
    }

    this.#globalListeners = new AbortController();
    const { signal } = this.#globalListeners;

    document.addEventListener("app:rehydrate", () => this.#initCalendarPopover(), { signal });

    document.addEventListener(
      "app:teardown",
      () => {
        this.#teardownState();
        if (this.#btn) {
          this.#btn.classList.remove("active");
          this.#btn.setAttribute("aria-expanded", "false");
        }
        if (this.#pop) {
          this.#pop.hidden = true;
          this.#pop.innerHTML = "";
        }
      },
      { signal },
    );

    // Summary cache is validated via digests, no manual invalidation needed.
  }

  disconnectedCallback() {
    this.#teardownState();
    this.#globalListeners?.abort();
    this.#globalListeners = null;
    this.#btn = null;
    this.#pop = null;
  }

  #initCalendarPopover() {
    const btn = this.querySelector("#calendar-btn");
    const pop = this.querySelector("#calendar-popover");

    if (!btn || !pop) {
      this.#teardownState();
      this.#btn = null;
      this.#pop = null;
      return;
    }

    this.#btn = btn;
    this.#pop = pop;

    if (
      this.#state &&
      this.#state.btn === btn &&
      this.#state.pop === pop &&
      !this.#state.signal.aborted
    ) {
      return;
    }

    this.#teardownState();
    this.#state = this.#setupCalendar(btn, pop);
  }

  #teardownState() {
    if (!this.#state) return;
    this.#state.dispose();
    this.#state = null;
  }

  #setupCalendar(btn, pop) {
    const controller = new AbortController();
    const { signal } = controller;
    const calendarUrl = pop.dataset.calendarUrl || pop.getAttribute("hx-get") || null;

    const getActiveDateParts = () => {
      const parts = getActiveDayParts();
      if (!parts) return null;
      return { year: parts.year, month: parts.month };
    };

    const loadCalendarContent = (params = {}) => {
      if (!calendarUrl) return;
      const target = new URL(calendarUrl, window.location.origin);
      const activeDate = getActiveDateParts();
      if (activeDate) {
        target.searchParams.set("year", String(activeDate.year));
        target.searchParams.set("month", String(activeDate.month));
      }
      Object.entries(params).forEach(([key, value]) => {
        if (value === undefined || value === null) return;
        target.searchParams.set(key, value);
      });
      pop.innerHTML = "";
      htmx.ajax("GET", target.toString(), {
        target: pop,
        swap: "innerHTML",
      });
    };

    const popover = createPopover(btn, pop, {
      getPanel: () => pop.querySelector("#calendar"),
      onShow: () => {
        btn.classList.add("active");
        btn.setAttribute("aria-expanded", "true");
        htmx.trigger(pop, "calendar-popover:show");
      },
      onHide: () => {
        btn.classList.remove("active");
        btn.setAttribute("aria-expanded", "false");
        htmx.trigger(pop, "calendar-popover:hide");
        this.#hideTooltip({ immediate: true });
      },
      onHidden: () => {
        pop.innerHTML = "";
      },
    });

    this.#trapFocus(pop, popover, signal);

    const update = () => {
      popover.update();
    };

    btn.addEventListener(
      "click",
      () => {
        if (popover.isOpen) {
          popover.hide();
          return;
        }
        popover.show();
        loadCalendarContent();
      },
      { signal },
    );

    pop.addEventListener(
      "click",
      (event) => {
        if (event.target.closest(".overlay-close")) {
          event.preventDefault();
          popover.hide();
          return;
        }

        if (event.target.closest(".calendar-table a, .today-btn")) {
          popover.hide();
        }
      },
      { signal },
    );

    pop.addEventListener(
      "htmx:afterSwap",
      (event) => {
        if (!pop.contains(event.target)) return;
        update();
        if (event.target === pop) {
          popover.animateOpen();
        }
        const calendar = pop.querySelector("#calendar");
        if (!calendar) return;
        const flashLabel = pop.dataset.calendarFlashLabel;
        if (flashLabel) {
          const header = calendar.querySelector(".calendar-month-year");
          const headerText = header?.querySelector("span")?.textContent?.trim();
          if (header && headerText && headerText === flashLabel) {
            triggerLabelFlash(header);
          }
          delete pop.dataset.calendarFlashLabel;
        }
        syncCalendarHeader(calendar);
        this.#configureCalendarGrid(calendar, popover, pop, signal);
        initCalendarPicker(calendar);
      },
      { signal },
    );

    pop.addEventListener(
      "htmx:configRequest",
      (event) => {
        const target = event.target;
        if (!target?.closest?.("[data-calendar-toggle]")) {
          return;
        }
        const calendar = pop.querySelector("#calendar");
        if (!calendar) return;
        const isPicker = Boolean(calendar.querySelector("#calendar-picker"));
        const year = calendar.dataset.year;
        const month = calendar.dataset.month;
        if (!year || !month) return;
        event.detail.path = `/calendar/${year}/${parseInt(month, 10)}`;
        event.detail.parameters = event.detail.parameters || {};
        event.detail.parameters.mode = isPicker ? "calendar" : "picker";
      },
      { signal },
    );

    const initialCalendar = pop.querySelector("#calendar");
    if (initialCalendar) {
      syncCalendarHeader(initialCalendar);
      this.#configureCalendarGrid(initialCalendar, popover, pop, signal);
      initCalendarPicker(initialCalendar);
    }

    const dispose = () => {
      controller.abort();
      popover.hide().finally(() => {
        popover.destroy();
      });
    };

    return {
      btn,
      pop,
      dispose,
      signal,
    };
  }

  #ensureTooltip() {
    if (this.#tooltip && document.body.contains(this.#tooltip)) {
      return this.#tooltip;
    }
    const tooltip = document.createElement("div");
    tooltip.className = "calendar-day-tooltip";
    tooltip.hidden = true;
    document.body.appendChild(tooltip);
    this.#tooltip = tooltip;
    return tooltip;
  }

  #hideTooltip({ immediate = false } = {}) {
    if (this.#tooltipTimer) {
      clearTimeout(this.#tooltipTimer);
      this.#tooltipTimer = null;
    }
    if (this.#tooltipHideTimer) {
      this.#tooltipHideTimer();
      this.#tooltipHideTimer = null;
    }
    this.#tooltipDate = null;
    const tooltip = this.#tooltip;
    if (!tooltip) return;
    const calendar = document.querySelector("#calendar");
    if (calendar) {
      calendar.querySelectorAll("[data-calendar-cell].is-summarizing").forEach((cell) => {
        cell.classList.remove("is-summarizing");
      });
    }
    if (this.#summaryCell) {
      this.#summaryCell.classList.remove("is-summarizing");
      this.#summaryCell = null;
    }
    if (immediate) {
      tooltip.classList.remove("is-visible");
      tooltip.hidden = true;
      tooltip.textContent = "";
      return;
    }
    this.#tooltipHideTimer = transitionHide(tooltip, "is-visible", 160);
  }

  #positionTooltip(tooltip, cell) {
    const row = cell?.parentElement;
    if (!(row instanceof HTMLTableRowElement)) return;
    const calendar = cell.closest("#calendar");
    if (!calendar) return;

    tooltip.hidden = false;
    tooltip.style.visibility = "hidden";

    const rowRect = row.getBoundingClientRect();
    const calRect = calendar.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    const offset = 14;
    const margin = 12;

    let left = calRect.right + offset;
    const maxLeft = window.innerWidth - tipRect.width - margin;
    if (left > maxLeft) {
      left = maxLeft;
    }
    if (left < margin) {
      left = margin;
    }

    let top = rowRect.top + (rowRect.height - tipRect.height) / 2;
    const maxTop = window.innerHeight - tipRect.height - margin;
    if (top > maxTop) top = maxTop;
    if (top < margin) top = margin;

    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.style.visibility = "visible";
    transitionShow(tooltip, "is-visible");
  }

  async #fetchDaySummary(date) {
    if (!date) return "";
    const summaryDigest = this.#summaryCell?.dataset?.summaryDigest || "";
    const cached = await getCachedDaySummary(date, summaryDigest);
    if (cached) {
      return cached;
    }
    if (this.#summaryRequests.has(date)) {
      return this.#summaryRequests.get(date);
    }
    const request = fetch(`/d/${date}/summary`, {
      headers: { Accept: "application/json" },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const summary = typeof data?.summary === "string" ? data.summary.trim() : "";
        if (summary) {
          void setCachedDaySummary(date, summary, summaryDigest);
        }
        this.#summaryRequests.delete(date);
        return summary;
      })
      .catch(() => {
        this.#summaryRequests.delete(date);
        return "";
      });
    this.#summaryRequests.set(date, request);
    return request;
  }

  #configureCalendarGrid(calendar, popover, pop, signal) {
    const grid = calendar.querySelector(".calendar-table[data-calendar-grid]");
    if (!grid || grid.dataset.enhanced === "true") {
      return;
    }
    grid.dataset.enhanced = "true";

    const getInteractiveCells = () => Array.from(grid.querySelectorAll("[data-calendar-cell]"));

    const updateCellState = (target, { focus = false } = {}) => {
      if (!target) return;
      const cells = getInteractiveCells();
      cells.forEach((cell) => {
        const isActive = cell === target;
        cell.setAttribute("tabindex", isActive ? "0" : "-1");
        cell.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      if (focus) {
        target.focus({ preventScroll: true });
      }
    };

    const resolveInitialCell = () =>
      grid.querySelector('[data-calendar-cell][tabindex="0"]') ??
      grid.querySelector('[data-calendar-cell][data-calendar-active="true"]') ??
      getInteractiveCells()[0] ??
      null;

    const initialCell = resolveInitialCell();
    if (initialCell) {
      updateCellState(initialCell, { focus: false });
    }

    const ensureFocus = () => {
      if (!popover.isOpen) return;
      const active =
        grid.querySelector('[data-calendar-cell][tabindex="0"]') ?? resolveInitialCell();
      if (active) {
        requestAnimationFrame(() => {
          updateCellState(active, { focus: true });
        });
      }
    };

    pop.addEventListener("calendar-popover:show", ensureFocus, { signal });
    pop.addEventListener("calendar-popover:hide", () => this.#hideTooltip({ immediate: true }), {
      signal,
    });
    pop.addEventListener("mouseleave", () => this.#hideTooltip(), { signal });

    grid.addEventListener(
      "focusin",
      (event) => {
        const cell = event.target.closest("[data-calendar-cell]");
        if (!cell) return;
        updateCellState(cell);
      },
      { signal },
    );
    grid.addEventListener(
      "focusin",
      (event) => {
        const cell = event.target.closest("[data-calendar-cell]");
        if (!cell) return;
        this.#scheduleTooltip(cell);
      },
      { signal },
    );
    grid.addEventListener(
      "focusout",
      () => {
        this.#hideTooltip();
      },
      { signal },
    );
    grid.addEventListener(
      "pointerenter",
      (event) => {
        const cell = event.target.closest("[data-calendar-cell]");
        if (!cell) return;
        this.#scheduleTooltip(cell);
      },
      { signal, capture: true },
    );
    grid.addEventListener(
      "pointerleave",
      (event) => {
        if (!event.target.closest("[data-calendar-cell]")) return;
        this.#hideTooltip();
      },
      { signal, capture: true },
    );
    grid.addEventListener(
      "click",
      () => {
        this.#hideTooltip({ immediate: true });
      },
      { signal },
    );
    window.addEventListener("scroll", () => this.#hideTooltip({ immediate: true }), {
      signal,
      passive: true,
    });

    const focusCell = (cell) => {
      if (!cell) return;
      updateCellState(cell, { focus: true });
    };

    const findHorizontalCell = (origin, direction) => {
      if (!origin) return null;
      let row = origin.parentElement;
      if (!(row instanceof HTMLTableRowElement)) {
        return null;
      }
      let sibling = direction > 0 ? origin.nextElementSibling : origin.previousElementSibling;
      while (row) {
        while (sibling) {
          if (sibling.hasAttribute("data-calendar-cell")) {
            return sibling;
          }
          sibling = direction > 0 ? sibling.nextElementSibling : sibling.previousElementSibling;
        }
        row = direction > 0 ? row.nextElementSibling : row.previousElementSibling;
        if (!row) break;
        sibling = direction > 0 ? row.firstElementChild : row.lastElementChild;
      }
      return null;
    };

    const findVerticalCell = (origin, direction) => {
      if (!origin) return null;
      const columnIndex = origin.cellIndex;
      let row = origin.parentElement;
      if (!(row instanceof HTMLTableRowElement)) {
        return null;
      }
      do {
        row = direction > 0 ? row.nextElementSibling : row.previousElementSibling;
        if (!row) break;
        const candidate = row.children[columnIndex];
        if (candidate?.hasAttribute("data-calendar-cell")) {
          return candidate;
        }
      } while (row);
      return null;
    };

    const findRowBoundaryCell = (origin, direction) => {
      if (!origin) return null;
      const row = origin.parentElement;
      if (!(row instanceof HTMLTableRowElement)) {
        return null;
      }
      const cells = Array.from(row.children);
      if (direction < 0) {
        cells.reverse();
      }
      return cells.find((cell) => cell.hasAttribute("data-calendar-cell")) ?? null;
    };

    grid.addEventListener(
      "keydown",
      (event) => {
        if (!popover.isOpen) return;
        const activeCell =
          document.activeElement?.closest?.("[data-calendar-cell]") ??
          grid.querySelector('[data-calendar-cell][tabindex="0"]');
        if (!activeCell) return;

        if (
          event.key === "ArrowLeft" ||
          event.key === "ArrowRight" ||
          event.key === "ArrowUp" ||
          event.key === "ArrowDown" ||
          event.key === "Home" ||
          event.key === "End" ||
          event.key === "Enter" ||
          event.key === " "
        ) {
          event.preventDefault();
        }

        switch (event.key) {
          case "ArrowLeft": {
            const target = findHorizontalCell(activeCell, -1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "ArrowRight": {
            const target = findHorizontalCell(activeCell, 1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "ArrowUp": {
            const target = findVerticalCell(activeCell, -1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "ArrowDown": {
            const target = findVerticalCell(activeCell, 1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "Home": {
            const target = findRowBoundaryCell(activeCell, -1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "End": {
            const target = findRowBoundaryCell(activeCell, 1) ?? activeCell;
            focusCell(target);
            break;
          }
          case "Enter":
          case " ": {
            const actionable = activeCell.querySelector("a[href], button:not([disabled])");
            if (actionable) {
              actionable.click();
            }
            break;
          }
          default:
            break;
        }
      },
      { signal },
    );
  }

  #scheduleTooltip(cell) {
    const date = cell?.dataset?.date;
    if (!date) return;
    if (cell.querySelector(".no-entries")) {
      return;
    }
    if (this.#tooltipTimer) {
      clearTimeout(this.#tooltipTimer);
    }
    if (this.#summaryCell && this.#summaryCell !== cell) {
      this.#summaryCell.classList.remove("is-summarizing");
    }
    this.#summaryCell = cell;
    cell.classList.add("is-summarizing");
    this.#tooltipDate = date;
    this.#tooltipTimer = window.setTimeout(async () => {
      if (this.#tooltipDate !== date) {
        cell.classList.remove("is-summarizing");
        return;
      }
      const summary = await this.#fetchDaySummary(date);
      if (!summary || this.#tooltipDate !== date) {
        cell.classList.remove("is-summarizing");
        return;
      }
      const tooltip = this.#ensureTooltip();
      tooltip.textContent = summary;
      this.#positionTooltip(tooltip, cell);
      cell.classList.remove("is-summarizing");
      if (this.#summaryCell === cell) {
        this.#summaryCell = null;
      }
    }, 500);
  }

  #trapFocus(pop, popover, signal) {
    pop.addEventListener(
      "keydown",
      (event) => {
        if (!popover.isOpen || event.key !== "Tab") {
          return;
        }
        const focusables = CalendarControl.#getFocusableElements(pop);
        if (!focusables.length) {
          event.preventDefault();
          return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey) {
          if (!pop.contains(active) || active === first) {
            event.preventDefault();
            last.focus({ preventScroll: true });
          }
        } else if (active === last) {
          event.preventDefault();
          first.focus({ preventScroll: true });
        }
      },
      { signal },
    );

    document.addEventListener(
      "focusin",
      (event) => {
        if (!popover.isOpen) return;
        if (pop.contains(event.target)) {
          return;
        }
        const focusables = CalendarControl.#getFocusableElements(pop);
        if (!focusables.length) {
          return;
        }
        const preferred = pop.querySelector('[data-calendar-cell][tabindex="0"]') ?? focusables[0];
        preferred?.focus({ preventScroll: true });
      },
      { signal },
    );
  }

  static #getFocusableElements(root) {
    const selectors = [
      '[data-calendar-cell][tabindex="0"]',
      "button:not([disabled])",
      "a[href]:not([tabindex='-1'])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ];
    const nodes = Array.from(root.querySelectorAll(selectors.join(",")));
    const seen = new Set();
    const focusables = [];
    for (const node of nodes) {
      if (!(node instanceof HTMLElement)) continue;
      if (seen.has(node)) continue;
      if (node.closest("[hidden]")) continue;
      if (node.hasAttribute("disabled")) continue;
      if (node.getAttribute("aria-hidden") === "true") continue;
      seen.add(node);
      focusables.push(node);
    }
    return focusables;
  }
}

function clampValue(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function shiftYearMonth(year, month, delta) {
  const baseYear = Number.isFinite(year) ? year : 0;
  const baseMonth = Number.isFinite(month) ? month : 1;
  const base = baseYear * 12 + (baseMonth - 1);
  const shifted = base + delta;
  const nextYear = Math.floor(shifted / 12);
  const nextMonth = (((shifted % 12) + 12) % 12) + 1;
  return { year: nextYear, month: nextMonth };
}

function syncCalendarHeader(calendar) {
  const toggle = calendar.querySelector("[data-calendar-toggle]");
  if (!toggle) return;
  const isPicker = Boolean(calendar.querySelector("#calendar-picker"));
  calendar.dataset.calendarMode = isPicker ? "picker" : "calendar";
  toggle.setAttribute("aria-expanded", isPicker ? "true" : "false");
  const year = calendar.dataset.year ?? "";
  const month = calendar.dataset.month ?? "";
  const base = calendar.dataset.calendarUrl || `/calendar/${year}/${month}`;
  const url = new URL(base, window.location.origin);
  url.searchParams.set("mode", isPicker ? "calendar" : "picker");
  toggle.setAttribute("hx-get", `${url.pathname}${url.search}`);
}

function initCalendarPicker(calendar) {
  if (!calendar) return;
  const picker = calendar.querySelector("#calendar-picker");
  const footer = calendar.querySelector("[data-calendar-footer]");
  if (!picker || !footer) {
    return;
  }

  const yearButtons = Array.from(picker.querySelectorAll("[data-picker-year]"));
  const monthButtons = Array.from(picker.querySelectorAll("[data-picker-month]"));
  if (!yearButtons.length || !monthButtons.length) {
    return;
  }

  const toNumber = (value, fallback) => {
    const parsed = Number(value);
    return Number.isInteger(parsed) ? parsed : fallback;
  };

  const minYear = toNumber(calendar.dataset.minYear, 1900);
  const minMonth = clampValue(toNumber(calendar.dataset.minMonth, 1), 1, 12);
  const maxYear = toNumber(calendar.dataset.todayYear, minYear);
  const maxMonth = clampValue(toNumber(calendar.dataset.todayMonth, 12), 1, 12);

  let selectedYear = clampValue(toNumber(calendar.dataset.year, maxYear), minYear, maxYear);
  let selectedMonth = clampValue(toNumber(calendar.dataset.month, maxMonth), 1, 12);

  const footerLabel = footer.querySelector("[data-calendar-footer-text]");
  const headerLabel = calendar.querySelector(".calendar-month-year span");
  const monthNameMap = new Map();
  monthButtons.forEach((button) => {
    const month = Number(button.dataset.pickerMonth);
    if (!Number.isInteger(month)) return;
    const label = button.textContent?.trim() ?? "";
    if (label) {
      monthNameMap.set(month, label);
    }
  });

  const updateFooterText = () => {
    if (!footerLabel) return;
    const monthLabel = monthNameMap.get(selectedMonth) ?? "";
    footerLabel.textContent = `Set to ${monthLabel} ${selectedYear}`.trim();
  };

  const scrollOptionIntoView = (button) => {
    if (!button || typeof button.scrollIntoView !== "function") return;
    const prefersReduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    button.scrollIntoView({
      block: "center",
      inline: "nearest",
      behavior: prefersReduced ? "auto" : "smooth",
    });
  };

  const _updateHeaderAfterConfirm = () => {
    if (headerLabel) {
      const monthLabel = monthNameMap.get(selectedMonth) ?? "";
      const nextText = `${monthLabel} ${selectedYear}`.trim();
      if (headerLabel.textContent !== nextText) {
        headerLabel.textContent = nextText;
        triggerLabelFlash(calendar.querySelector(".calendar-month-year"));
      }
    }
  };

  const getMonthBounds = () => {
    const minAllowed = selectedYear === minYear ? minMonth : 1;
    const maxAllowed = selectedYear === maxYear ? maxMonth : 12;
    return { minAllowed, maxAllowed };
  };

  const refreshMonths = () => {
    const { minAllowed, maxAllowed } = getMonthBounds();
    selectedMonth = clampValue(selectedMonth, minAllowed, maxAllowed);
    monthButtons.forEach((button) => {
      const month = Number(button.dataset.pickerMonth);
      const disabled = month < minAllowed || month > maxAllowed;
      button.disabled = disabled;
      button.classList.toggle("is-disabled", disabled);
      const active = month === selectedMonth;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  };

  const refreshYears = () => {
    yearButtons.forEach((button) => {
      const year = Number(button.dataset.pickerYear);
      const active = year === selectedYear;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  };

  const handleYearClick = (event) => {
    const target = event.currentTarget;
    if (!target) return;
    const year = Number(target.dataset.pickerYear);
    if (!Number.isInteger(year)) return;
    selectedYear = clampValue(year, minYear, maxYear);
    refreshYears();
    refreshMonths();
    updateFooterText();
    scrollOptionIntoView(target);
    const activeMonth = monthButtons.find(
      (button) => button.getAttribute("aria-pressed") === "true",
    );
    if (activeMonth) {
      scrollOptionIntoView(activeMonth);
    }
  };

  const handleMonthClick = (event) => {
    const target = event.currentTarget;
    if (!target || target.disabled) return;
    const month = Number(target.dataset.pickerMonth);
    if (!Number.isInteger(month)) return;
    const { minAllowed, maxAllowed } = getMonthBounds();
    selectedMonth = clampValue(month, minAllowed, maxAllowed);
    refreshMonths();
    updateFooterText();
    scrollOptionIntoView(target);
  };

  const confirmSelection = () => {
    const baseUrl = calendar.dataset.calendarUrl;
    if (!baseUrl) return;
    const monthLabel = monthNameMap.get(selectedMonth) ?? "";
    const nextLabel = `${monthLabel} ${selectedYear}`.trim();
    const updateNavButtons = () => {
      const prevBtn = calendar.querySelector(".cal-nav-btn.prev");
      const nextBtn = calendar.querySelector(".cal-nav-btn.next");
      const minYearNum = toNumber(calendar.dataset.minYear, minYear);
      const minMonthNum = clampValue(toNumber(calendar.dataset.minMonth, minMonth), 1, 12);
      const maxYearNum = toNumber(calendar.dataset.todayYear, maxYear);
      const maxMonthNum = clampValue(toNumber(calendar.dataset.todayMonth, maxMonth), 1, 12);

      const atMin = selectedYear === minYearNum && selectedMonth === minMonthNum;
      const atMax = selectedYear === maxYearNum && selectedMonth === maxMonthNum;

      const updateButton = (button, target, disabled) => {
        if (!button) return;
        if (disabled) {
          button.setAttribute("disabled", "");
          button.setAttribute("aria-disabled", "true");
        } else {
          button.removeAttribute("disabled");
          button.removeAttribute("aria-disabled");
        }
        const href = `/calendar/${target.year}/${target.month}`;
        button.setAttribute("hx-get", href);
      };

      const prevTarget = shiftYearMonth(selectedYear, selectedMonth, -1);
      const nextTarget = shiftYearMonth(selectedYear, selectedMonth, 1);
      updateButton(prevBtn, prevTarget, atMin);
      updateButton(nextBtn, nextTarget, atMax);
    };

    const updateHeader = () => {
      if (headerLabel) {
        headerLabel.textContent = nextLabel;
        triggerLabelFlash(calendar.querySelector(".calendar-month-year"));
      }
      calendar.dataset.year = String(selectedYear);
      calendar.dataset.month = String(selectedMonth).padStart(2, "0");
      calendar.dataset.calendarMode = "calendar";
      updateNavButtons();
      syncCalendarHeader(calendar);
    };
    const pop = calendar.closest("#calendar-popover");
    if (pop) {
      const handleSwap = (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        if (!target.classList.contains("calendar-swap")) return;
        updateHeader();
      };
      pop.addEventListener("htmx:afterSwap", handleSwap, { once: true });
    } else {
      updateHeader();
    }
    const url = new URL(baseUrl, window.location.origin);
    url.searchParams.set("year", String(selectedYear));
    url.searchParams.set("month", String(selectedMonth));
    url.searchParams.set("mode", "calendar");
    htmx.ajax("GET", url.toString(), {
      target: ".calendar-swap",
      select: ".calendar-swap",
      swap: "outerHTML swap:120ms settle:120ms",
    });
  };

  yearButtons.forEach((button) => {
    button.addEventListener("click", handleYearClick);
  });
  monthButtons.forEach((button) => {
    button.addEventListener("click", handleMonthClick);
  });
  const handleFooterAction = (event) => {
    if (event) event.preventDefault();
    confirmSelection();
  };
  footer.addEventListener("click", handleFooterAction);
  footer.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      handleFooterAction(event);
    }
  });

  refreshYears();
  refreshMonths();
  updateFooterText();
  const activeYear = yearButtons.find((button) => button.getAttribute("aria-pressed") === "true");
  const activeMonth = monthButtons.find((button) => button.getAttribute("aria-pressed") === "true");
  if (activeYear) {
    scrollOptionIntoView(activeYear);
  }
  if (activeMonth) {
    scrollOptionIntoView(activeMonth);
  }
}

function registerCalendarControl() {
  if (!customElements.get("calendar-control")) {
    customElements.define("calendar-control", CalendarControl);
  }
}

registerCalendarControl();
document.addEventListener("app:rehydrate", registerCalendarControl);
