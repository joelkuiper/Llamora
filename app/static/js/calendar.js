let initialized = false;
let state;

function setupCalendar(btn, pop) {
  const controller = new AbortController();
  const { signal } = controller;
  let instance;

  const update = () => {
    if (instance) instance.update();
  };

  const animateOpen = () => {
    const panel = pop.querySelector('#calendar');
    if (!panel) return;
    panel.classList.add('pop-enter');
    panel.addEventListener(
      'animationend',
      () => {
        panel.classList.remove('pop-enter');
      },
      { once: true },
    );
  };

  const hide = () => {
    if (pop.hidden) return;
    btn.classList.remove('active');
    const panel = pop.querySelector('#calendar');
    if (panel) {
      panel.classList.add('pop-exit');
      panel.addEventListener(
        'animationend',
        () => {
          panel.classList.remove('pop-exit');
          pop.hidden = true;
          pop.innerHTML = '';
        },
        { once: true },
      );
    } else {
      pop.hidden = true;
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
      pop.hidden = false;
      btn.classList.add('active');
      instance =
        instance ||
        Popper.createPopper(btn, pop, {
          placement: 'bottom',
        });
      update();
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
      if (e.target === pop && instance) {
        update();
        animateOpen();
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
