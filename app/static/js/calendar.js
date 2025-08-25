import { updateActiveDay } from "./day.js";

function initCalendarPopover() {
  const btn = document.getElementById('calendar-btn');
  const pop = document.getElementById('calendar-popover');
  if (!btn || !pop) return;
  let instance;

  const update = () => {
    pop.style.transition = 'none';
    instance.update();
    requestAnimationFrame(() => {
      pop.style.transition = '';
    });
  };

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
    if (!pop.contains(e.target) && !btn.contains(e.target)) hide();
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
    btn.classList.add('active');
    instance =
      instance ||
      Popper.createPopper(btn, pop, {
        placement: 'bottom',
      });
    update();
    requestAnimationFrame(() => pop.classList.add('tp-open'));
    htmx.trigger(pop, 'calendar-popover:show');
    document.addEventListener('click', outside, true);
    document.addEventListener('keydown', onKey);
  });

  pop.addEventListener('click', (e) => {
    if (e.target.closest('.overlay-close')) {
      e.preventDefault();
      hide();
    }
    if (e.target.closest('.calendar-table a, .today-btn')) {
      hide();
    }
  });

  pop.addEventListener('htmx:afterSwap', (e) => {
    if (e.target === pop && instance) {
      update();
      updateActiveDay();
    }
  });
}

initCalendarPopover();
