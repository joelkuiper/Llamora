import { updateActiveDay } from "./day.js";

function initCalendarPopover() {
  const btn = document.getElementById('calendar-btn');
  const pop = document.getElementById('calendar-popover');
  if (!btn || !pop) return;
  let instance;

  const update = () => {
    instance.update();
  };

  const animateOpen = () => {
    const panel = pop.querySelector('#calendar');
    if (!panel) return;
    panel.classList.add('pop-enter');
    panel.addEventListener('animationend', () => {
      panel.classList.remove('pop-enter');
    }, { once: true });
  };

  const hide = () => {
    if (pop.hidden) return;
    btn.classList.remove('active');
    const panel = pop.querySelector('#calendar');
    if (panel) {
      panel.classList.add('pop-exit');
      panel.addEventListener('animationend', () => {
        panel.classList.remove('pop-exit');
        pop.hidden = true;
      }, { once: true });
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
    animateOpen();
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
      animateOpen();
    }
  });
}

initCalendarPopover();
