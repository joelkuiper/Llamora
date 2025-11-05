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
      },
      { signal }
    );

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
}

if (!customElements.get("calendar-control")) {
  customElements.define("calendar-control", CalendarControl);
}
