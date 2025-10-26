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
  let instance;
  const defaultUrl = pop.getAttribute('hx-get');

  const update = () => {
    if (instance) instance.update();
  };

  const getPanel = () => pop.querySelector('#calendar');

  const playAnimation = (el, cls, remove = []) => {
    if (!el) return;
    remove.forEach((name) => el.classList.remove(name));
    // Force a reflow so re-adding the class retriggers the animation.
    void el.getBoundingClientRect();
    el.classList.add(cls);
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      el.classList.remove(cls);
    };
    el.addEventListener('animationend', cleanup, { once: true });
    setTimeout(cleanup, 220);
  };

  const animateOpen = () => {
    const panel = getPanel();
    if (!panel) return;
    playAnimation(pop, 'fade-enter', ['fade-enter', 'fade-exit']);
    playAnimation(panel, 'pop-enter', ['pop-enter', 'pop-exit']);
  };

  const hide = () => {
    if (pop.hidden) return;
    btn.classList.remove('active');
    const panel = getPanel();
    const finish = () => {
      if (panel) {
        panel.classList.remove('pop-enter', 'pop-exit');
      }
      pop.classList.remove('fade-enter', 'fade-exit');
      pop.hidden = true;
      pop.innerHTML = '';
    };
    let pending = 0;
    const awaitAnimation = (el, className, removeClasses = []) => {
      if (!el) return;
      pending += 1;
      removeClasses.forEach((cls) => el.classList.remove(cls));
      void el.getBoundingClientRect();
      el.classList.add(className);

      let done = false;
      const complete = () => {
        if (done) return;
        done = true;
        el.classList.remove(className);
        pending -= 1;
        if (pending === 0) {
          finish();
        }
      };

      el.addEventListener('animationend', complete, { once: true });
      setTimeout(complete, 220);
    };

    awaitAnimation(pop, 'fade-exit', ['fade-enter', 'fade-exit']);
    awaitAnimation(panel, 'pop-exit', ['pop-enter', 'pop-exit']);

    if (pending === 0) {
      finish();
    }
    document.removeEventListener('click', outside, true);
    document.removeEventListener('keydown', onKey);
  };

  const outside = (e) => {
    if (!pop.contains(e.target) && !btn.contains(e.target)) hide();
  };

  const onKey = (e) => {
    if (e.key === 'Escape') hide();
  };

  btn.addEventListener(
    'click',
    () => {
      if (!pop.hidden) {
        hide();
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
      pop.hidden = false;
      btn.classList.add('active');
      instance =
        instance ||
        Popper.createPopper(btn, pop, {
          placement: 'bottom',
        });
      update();
      if (getPanel()) {
        animateOpen();
      }
      htmx.trigger(pop, 'calendar-popover:show');
      document.addEventListener('click', outside, true);
      document.addEventListener('keydown', onKey);
    },
    { signal },
  );

  pop.addEventListener(
    'click',
    (e) => {
      if (e.target.closest('.overlay-close')) {
        e.preventDefault();
        hide();
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
        hide();
      }
    },
    { signal },
  );

  pop.addEventListener(
    'htmx:afterSwap',
    (e) => {
      if (!instance || !pop.contains(e.target)) return;
      update();
      if (e.target === pop) {
        animateOpen();
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
    hide();
    controller.abort();
    if (instance) {
      instance.destroy();
      instance = null;
    }
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
