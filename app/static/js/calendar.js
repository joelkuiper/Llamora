import { createPopover } from "./popover.js";

let initialized = false;
let state;

const calendarStorageKey = 'calendar:last-view';

const calendarStorage = (() => {
  try {
    if (typeof window === 'undefined' || !window.sessionStorage) {
      return null;
    }
    const probe = '__calendar__';
    window.sessionStorage.setItem(probe, probe);
    window.sessionStorage.removeItem(probe);
    return window.sessionStorage;
  } catch (err) {
    return null;
  }
})();

const readStoredCalendar = () => {
  if (!calendarStorage) return null;
  try {
    const raw = calendarStorage.getItem(calendarStorageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const year = Number(parsed.year);
    const month = Number(parsed.month);
    if (!Number.isInteger(year)) return null;
    if (!Number.isInteger(month)) return null;
    if (month < 1 || month > 12) return null;
    return { year, month };
  } catch (err) {
    return null;
  }
};

const writeStoredCalendar = (value) => {
  if (!calendarStorage) return;
  try {
    calendarStorage.setItem(calendarStorageKey, JSON.stringify(value));
  } catch (err) {
    /* no-op */
  }
};

const clearStoredCalendar = () => {
  if (!calendarStorage) return;
  try {
    calendarStorage.removeItem(calendarStorageKey);
  } catch (err) {
    /* no-op */
  }
};

function setupCalendar(btn, pop) {
  const controller = new AbortController();
  const { signal } = controller;
  const defaultUrl = pop.getAttribute('hx-get');

  const popover = createPopover(btn, pop, {
    getPanel: () => pop.querySelector('#calendar'),
    onShow: () => {
      btn.classList.add('active');
      htmx.trigger(pop, 'calendar-popover:show');
    },
    onHide: () => {
      btn.classList.remove('active');
    },
    onHidden: () => {
      pop.innerHTML = '';
    },
  });

  const update = () => {
    popover.update();
  };

  btn.addEventListener(
    'click',
    () => {
      if (popover.isOpen) {
        popover.hide();
        return;
      }
      const stored = readStoredCalendar();
      if (stored) {
        pop.setAttribute('hx-vals', JSON.stringify(stored));
      } else {
        pop.removeAttribute('hx-vals');
      }
      if (defaultUrl) {
        pop.setAttribute('hx-get', defaultUrl);
      }
      popover.show();
    },
    { signal },
  );

  pop.addEventListener(
    'click',
    (e) => {
      if (e.target.closest('.overlay-close')) {
        e.preventDefault();
        popover.hide();
        return;
      }

      const todayBtn = e.target.closest('.today-btn');
      if (todayBtn) {
        const dateValue = todayBtn.getAttribute('data-date');
        if (typeof dateValue === 'string') {
          const [yearStr, monthStr] = dateValue.split('-');
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
            pop.setAttribute('hx-vals', JSON.stringify(payload));
          } else {
            clearStoredCalendar();
            pop.removeAttribute('hx-vals');
          }
        } else {
          clearStoredCalendar();
          pop.removeAttribute('hx-vals');
        }
      }

      if (e.target.closest('.calendar-table a, .today-btn')) {
        popover.hide();
      }
    },
    { signal },
  );

  pop.addEventListener(
    'htmx:afterSwap',
    (e) => {
      if (!pop.contains(e.target)) return;
      update();
      if (e.target === pop) {
        popover.animateOpen();
      }
      const calendar = pop.querySelector('#calendar');
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
        pop.setAttribute('hx-vals', JSON.stringify(payload));
      }
    },
    { signal },
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

function initCalendarPopover() {
  const btn = document.getElementById('calendar-btn');
  const pop = document.getElementById('calendar-popover');
  if (!btn || !pop) return;

  if (state && state.btn === btn && state.pop === pop && !state.signal.aborted) {
    // HTMX history restores fire htmx:afterSwap even when the calendar nodes
    // persist, so we keep the existing bindings instead of tearing them down.
    return;
  }

  if (state) {
    state.dispose();
    state = null;
  }

  state = setupCalendar(btn, pop);
}

initCalendarPopover();
if (!initialized) {
  document.body.addEventListener('htmx:afterSwap', initCalendarPopover);
  document.body.addEventListener('htmx:historyRestore', initCalendarPopover);
  window.addEventListener('pageshow', (evt) => {
    // BFCache restores (e.g. browser back) reuse the old DOM without rerunning
    // this module, so we re-bind handlers whenever the cached page resurfaces.
    if (evt.persisted) {
      initCalendarPopover();
    }
  });
  initialized = true;
}
