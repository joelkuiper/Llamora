const ACTIVE_DAY_CHANGED_EVENT = "active-day-changed";

const doc = typeof document !== "undefined" ? document : null;
const body = doc?.body ?? null;

let currentDay = null;
let currentLabel = null;

const normalize = (value) => {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const syncBodyDataset = () => {
  if (!body?.dataset) return;

  if (currentDay) {
    body.dataset.activeDay = currentDay;
  } else {
    delete body.dataset.activeDay;
  }

  if (currentLabel) {
    body.dataset.activeDayLabel = currentLabel;
  } else {
    delete body.dataset.activeDayLabel;
  }
};

const dispatchChange = (previousDay, previousLabel, detail = {}) => {
  if (!doc) return;

  const eventDetail = {
    activeDay: currentDay,
    activeDayLabel: currentLabel,
    previousDay,
    previousLabel,
    ...detail,
  };

  doc.dispatchEvent(
    new CustomEvent(ACTIVE_DAY_CHANGED_EVENT, {
      detail: eventDetail,
    })
  );
};

export function getActiveDay() {
  return currentDay;
}

export function getActiveDayLabel() {
  return currentLabel;
}

export function getActiveDayState() {
  return { day: currentDay, label: currentLabel };
}

export function setActiveDay(day, label, { force = false, detail = {} } = {}) {
  const nextDay = normalize(day);
  const nextLabel = normalize(label);
  const previousDay = currentDay;
  const previousLabel = currentLabel;

  const changed =
    force || nextDay !== previousDay || nextLabel !== previousLabel;

  currentDay = nextDay;
  currentLabel = nextLabel;

  syncBodyDataset();

  if (changed) {
    dispatchChange(previousDay, previousLabel, detail);
  }
}

export function clearActiveDay(options = {}) {
  setActiveDay(null, null, options);
}

export function onActiveDayChange(handler, options) {
  if (!doc || typeof handler !== "function") {
    return () => {};
  }
  doc.addEventListener(ACTIVE_DAY_CHANGED_EVENT, handler, options);
  return () => {
    doc.removeEventListener(ACTIVE_DAY_CHANGED_EVENT, handler, options);
  };
}

export { ACTIVE_DAY_CHANGED_EVENT };

const bootstrap = () => {
  if (!body?.dataset) {
    return;
  }
  const initialDay = normalize(body.dataset.activeDay);
  const initialLabel = normalize(body.dataset.activeDayLabel);
  currentDay = initialDay;
  currentLabel = initialLabel;
};

bootstrap();

