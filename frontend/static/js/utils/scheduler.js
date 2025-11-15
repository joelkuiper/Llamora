const FRAME_DURATION_MS = 16;

function getNow() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return () => performance.now();
  }
  return () => Date.now();
}

const now = getNow();

function makeCancelFn(state) {
  return () => {
    if (!state.active) {
      return;
    }
    state.active = false;
    if (state.id == null) {
      return;
    }
    if (state.type === "raf" && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(state.id);
    } else if (state.type === "timeout") {
      clearTimeout(state.id);
    }
    state.id = null;
  };
}

export function scheduleFrame(callback) {
  if (typeof callback !== "function") {
    return {
      cancel() {},
      get active() {
        return false;
      },
    };
  }

  const state = { id: null, type: null, active: true };
  const cancel = makeCancelFn(state);

  const run = () => {
    if (!state.active) {
      return;
    }
    state.id = null;
    callback();
  };

  if (typeof requestAnimationFrame === "function") {
    state.type = "raf";
    state.id = requestAnimationFrame(run);
  } else {
    state.type = "timeout";
    state.id = setTimeout(run, FRAME_DURATION_MS);
  }

  return {
    cancel,
    get active() {
      return state.active;
    },
  };
}

export function afterNextFrame(callback) {
  if (typeof callback !== "function") {
    return () => {};
  }

  let cancelled = false;
  let firstId = null;
  let secondId = null;
  let firstType = null;
  let secondType = null;

  const clear = (id, type) => {
    if (id == null) return;
    if (type === "raf" && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(id);
    } else if (type === "timeout") {
      clearTimeout(id);
    }
  };

  const cancel = () => {
    if (cancelled) return;
    cancelled = true;
    clear(firstId, firstType);
    clear(secondId, secondType);
  };

  const runSecond = () => {
    if (cancelled) return;
    secondId = null;
    callback();
  };

  const scheduleSecond = () => {
    if (cancelled) return;
    if (typeof requestAnimationFrame === "function") {
      secondType = "raf";
      secondId = requestAnimationFrame(runSecond);
    } else {
      secondType = "timeout";
      secondId = setTimeout(runSecond, FRAME_DURATION_MS);
    }
  };

  const runFirst = () => {
    if (cancelled) return;
    firstId = null;
    scheduleSecond();
  };

  if (typeof requestAnimationFrame === "function") {
    firstType = "raf";
    firstId = requestAnimationFrame(runFirst);
  } else {
    firstType = "timeout";
    firstId = setTimeout(runFirst, FRAME_DURATION_MS);
  }

  return cancel;
}

export function scheduleRafLoop({ callback, timeoutMs = Infinity } = {}) {
  if (typeof callback !== "function") {
    return {
      cancel() {},
      get active() {
        return false;
      },
    };
  }

  const limit = Number.isFinite(timeoutMs) && timeoutMs >= 0 ? timeoutMs : Infinity;
  const start = now();

  const state = { id: null, type: null, active: true };
  const cancel = makeCancelFn(state);

  const schedule = () => {
    if (!state.active) {
      return;
    }
    if (typeof requestAnimationFrame === "function") {
      state.type = "raf";
      state.id = requestAnimationFrame(tick);
    } else {
      state.type = "timeout";
      state.id = setTimeout(tick, FRAME_DURATION_MS);
    }
  };

  const tick = () => {
    if (!state.active) {
      return;
    }
    state.id = null;
    const elapsed = now() - start;
    const timedOut = elapsed >= limit;
    if (timedOut) {
      cancel();
      callback({ elapsed, timedOut: true, stop: cancel });
      return;
    }
    const result = callback({ elapsed, timedOut: false, stop: cancel });
    if (!state.active) {
      return;
    }
    if (result === false) {
      cancel();
      return;
    }
    schedule();
  };

  schedule();

  return {
    cancel,
    get active() {
      return state.active;
    },
  };
}
