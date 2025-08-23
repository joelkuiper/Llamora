function initCalendarPopover() {
  const btn = document.getElementById('calendar-btn');
  const pop = document.getElementById('calendar-popover');
  if (!btn || !pop) return;
  let instance;

  const hide = () => {
    if (pop.hidden) return;
    pop.classList.remove('tp-open');
    btn.classList.remove('active');
    const clear = (e) => {
      if (e && e.target !== pop) return;
      pop.hidden = true;
      pop.removeEventListener('transitionend', clear);
    };
    pop.addEventListener('transitionend', clear);
    document.removeEventListener('click', outside, true);
    document.removeEventListener('keydown', onKey);
  };

  const outside = (e) => {
    if (!pop.contains(e.target) && e.target !== btn) hide();
  };

  const onKey = (e) => {
    if (e.key === 'Escape') hide();
  };

  btn.addEventListener('click', () => {
    if (!pop.hidden) {
      hide();
      return;
    }
    pop.hidden = false;
    requestAnimationFrame(() => pop.classList.add('tp-open'));
    btn.classList.add('active');
    instance = instance || Popper.createPopper(btn, pop, { placement: 'bottom' });
    instance.update();
    if (!pop.dataset.loaded) {
      htmx.trigger(pop, 'calendar-popover:show');
      pop.dataset.loaded = '1';
    }
    document.addEventListener('click', outside, true);
    document.addEventListener('keydown', onKey);
  });

  pop.addEventListener('click', (e) => {
    if (e.target.closest('.overlay-close')) {
      e.preventDefault();
      hide();
    }
  });
}

initCalendarPopover();
