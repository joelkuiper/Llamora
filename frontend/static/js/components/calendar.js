import { createPopover } from "../popover.js";

const CALENDAR_STORAGE_KEY = "calendar:last-view";

const calendarStorage = (() => {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) {
      return null;
    }
    const probe = "__calendar__";
    window.sessionStorage.setItem(probe, probe);
    window.sessionStorage.removeItem(probe);
    return window.sessionStorage;
  } catch (err) {
    return null;
  }
})();

function readStoredCalendar() {
  if (!calendarStorage) return null;
  try {
    const raw = calendarStorage.getItem(CALENDAR_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const year = Number(parsed.year);
    const month = Number(parsed.month);
    if (!Number.isInteger(year)) return null;
    if (!Number.isInteger(month)) return null;
    if (month < 1 || month > 12) return null;
    return { year, month };
  } catch (err) {
    return null;
  }
}

function writeStoredCalendar(value) {
  if (!calendarStorage) return;
  try {
    calendarStorage.setItem(CALENDAR_STORAGE_KEY, JSON.stringify(value));
  } catch (err) {
    /* no-op */
  }
}

function clearStoredCalendar() {
  if (!calendarStorage) return;
  try {
    calendarStorage.removeItem(CALENDAR_STORAGE_KEY);
  } catch (err) {
    /* no-op */
  }
}

export class CalendarControl extends HTMLElement {
  #state = null;
  #btn = null;
  #pop = null;
  #globalListeners = null;

  connectedCallback() {
    this.#btn = this.querySelector("#calendar-btn");
    this.#pop = this.querySelector("#calendar-popover");

    this.#initCalendarPopover();

    if (this.#globalListeners) {
      this.#globalListeners.abort();
    }

    this.#globalListeners = new AbortController();
    const { signal } = this.#globalListeners;

    const handleSwap = () => this.#initCalendarPopover();
    document.body.addEventListener("htmx:afterSwap", handleSwap, { signal });
    document.body.addEventListener("htmx:historyRestore", handleSwap, { signal });
    const handleBeforeCache = () => {
      this.#teardownState();
      if (this.#btn) {
        this.#btn.classList.remove("active");
        this.#btn.setAttribute("aria-expanded", "false");
      }
      if (this.#pop) {
        this.#pop.hidden = true;
        this.#pop.innerHTML = "";
      }
    };
    document.body.addEventListener(
      "htmx:beforeHistorySave",
      handleBeforeCache,
      { signal }
    );
    window.addEventListener(
      "pageshow",
      (event) => {
        if (event.persisted) {
          this.#initCalendarPopover();
        }
      },
      { signal }
    );
    window.addEventListener("pagehide", handleBeforeCache, { signal });
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
    const defaultUrl = pop.getAttribute("hx-get");

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
        const stored = readStoredCalendar();
        if (stored) {
          pop.setAttribute("hx-vals", JSON.stringify(stored));
        } else {
          pop.removeAttribute("hx-vals");
        }
        if (defaultUrl) {
          pop.setAttribute("hx-get", defaultUrl);
        }
        popover.show();
      },
      { signal }
    );

    pop.addEventListener(
      "click",
      (event) => {
        if (event.target.closest(".overlay-close")) {
          event.preventDefault();
          popover.hide();
          return;
        }

        const todayBtn = event.target.closest(".today-btn");
        if (todayBtn) {
          const dateValue = todayBtn.getAttribute("data-date");
          if (typeof dateValue === "string") {
            const [yearStr, monthStr] = dateValue.split("-");
            const yearNum = Number(yearStr);
            const monthNum = Number(monthStr);
            if (
              Number.isInteger(yearNum) &&
              Number.isInteger(monthNum) &&
              monthNum >= 1 &&
              monthNum <= 12
            ) {
              const payload = { year: yearNum, month: monthNum };
              writeStoredCalendar(payload);
              pop.setAttribute("hx-vals", JSON.stringify(payload));
            } else {
              clearStoredCalendar();
              pop.removeAttribute("hx-vals");
            }
          } else {
            clearStoredCalendar();
            pop.removeAttribute("hx-vals");
          }
        }

        if (event.target.closest(".calendar-table a, .today-btn")) {
          popover.hide();
        }
      },
      { signal }
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
        const { year, month } = calendar.dataset;
        const monthNum = Number(month);
        const yearNum = Number(year);
        if (
          Number.isInteger(yearNum) &&
          Number.isInteger(monthNum) &&
          monthNum >= 1 &&
          monthNum <= 12
        ) {
          const payload = { year: yearNum, month: monthNum };
          writeStoredCalendar(payload);
          pop.setAttribute("hx-vals", JSON.stringify(payload));
        }
        this.#configureCalendarGrid(calendar, popover, pop, signal);
      },
      { signal }
    );

    const initialCalendar = pop.querySelector("#calendar");
    if (initialCalendar) {
      this.#configureCalendarGrid(initialCalendar, popover, pop, signal);
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

  #configureCalendarGrid(calendar, popover, pop, signal) {
    const grid = calendar.querySelector(".calendar-table[data-calendar-grid]");
    if (!grid || grid.dataset.enhanced === "true") {
      return;
    }
    grid.dataset.enhanced = "true";

    const getInteractiveCells = () =>
      Array.from(grid.querySelectorAll("[data-calendar-cell]"));

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
      getInteractiveCells()[0] ?? null;

    const initialCell = resolveInitialCell();
    if (initialCell) {
      updateCellState(initialCell, { focus: false });
    }

    const ensureFocus = () => {
      if (!popover.isOpen) return;
      const active =
        grid.querySelector('[data-calendar-cell][tabindex="0"]') ??
        resolveInitialCell();
      if (active) {
        requestAnimationFrame(() => {
          updateCellState(active, { focus: true });
        });
      }
    };

    pop.addEventListener("calendar-popover:show", ensureFocus, { signal });

    grid.addEventListener(
      "focusin",
      (event) => {
        const cell = event.target.closest("[data-calendar-cell]");
        if (!cell) return;
        updateCellState(cell);
      },
      { signal }
    );

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
      let sibling =
        direction > 0 ? origin.nextElementSibling : origin.previousElementSibling;
      while (row) {
        while (sibling) {
          if (sibling.hasAttribute("data-calendar-cell")) {
            return sibling;
          }
          sibling =
            direction > 0
              ? sibling.nextElementSibling
              : sibling.previousElementSibling;
        }
        row =
          direction > 0 ? row.nextElementSibling : row.previousElementSibling;
        if (!row) break;
        sibling =
          direction > 0 ? row.firstElementChild : row.lastElementChild;
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
            const actionable = activeCell.querySelector(
              "a[href], button:not([disabled])"
            );
            if (actionable) {
              actionable.click();
            }
            break;
          }
          default:
            break;
        }
      },
      { signal }
    );
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
      { signal }
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
        const preferred =
          pop.querySelector('[data-calendar-cell][tabindex="0"]') ??
          focusables[0];
        preferred?.focus({ preventScroll: true });
      },
      { signal }
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

function registerCalendarControl() {
  if (!customElements.get("calendar-control")) {
    customElements.define("calendar-control", CalendarControl);
  }
}

registerCalendarControl();
document.addEventListener("app:rehydrate", registerCalendarControl);
