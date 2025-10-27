function combineSignals(...signals) {
  const valid = signals.filter(Boolean);
  if (!valid.length) {
    return null;
  }

  if (valid.length === 1) {
    return valid[0];
  }

  const combo = new AbortController();
  const abort = () => {
    if (!combo.signal.aborted) {
      combo.abort();
    }
  };

  for (const sig of valid) {
    if (sig.aborted) {
      combo.abort();
      break;
    }
    sig.addEventListener("abort", abort, { once: true });
  }

  return combo.signal;
}

export function createListenerBag() {
  const controller = new AbortController();
  const { signal } = controller;

  const add = (target, type, handler, options) => {
    if (!target || typeof target.addEventListener !== "function") {
      return;
    }

    if (options === undefined) {
      target.addEventListener(type, handler, { signal });
      return;
    }

    if (typeof options === "boolean") {
      target.addEventListener(type, handler, { capture: options, signal });
      return;
    }

    const finalOptions = { ...options };
    finalOptions.signal = combineSignals(signal, options.signal) || signal;
    target.addEventListener(type, handler, finalOptions);
  };

  const abort = () => {
    controller.abort();
  };

  return { add, abort };
}
