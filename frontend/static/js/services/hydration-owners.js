const owners = new Map();

let listenersBound = false;
let fallbackCycle = 0;

const toCycle = (event) => {
  const raw = Number(event?.detail?.cycle);
  if (Number.isFinite(raw) && raw > 0) {
    return raw;
  }
  fallbackCycle += 1;
  return fallbackCycle;
};
const resolveContext = (event) => {
  const detail = event?.detail || {};
  const candidate = detail.context || detail.target;
  if (candidate instanceof Element || candidate instanceof DocumentFragment) {
    return candidate;
  }
  return document;
};

const matchesSelector = (node, selector) =>
  node instanceof Element && typeof node.matches === "function" && node.matches(selector);

const collectTargets = (context, selector) => {
  if (!selector) {
    return [context];
  }
  const targets = [];
  if (matchesSelector(context, selector)) {
    targets.push(context);
  }
  if (typeof context.querySelectorAll === "function") {
    context.querySelectorAll(selector).forEach((node) => {
      targets.push(node);
    });
  } else if (context !== document && typeof document.querySelectorAll === "function") {
    document.querySelectorAll(selector).forEach((node) => {
      targets.push(node);
    });
  }
  if (!targets.length && typeof document.querySelectorAll === "function") {
    document.querySelectorAll(selector).forEach((node) => {
      targets.push(node);
    });
  }
  return Array.from(new Set(targets));
};

const runHydrate = (owner, context, cycle, reason) => {
  const targets = collectTargets(context, owner.selector);
  if (!targets.length) {
    return;
  }
  targets.forEach((target) => {
    if (owner.scopeCycles.get(target) === cycle) {
      return;
    }
    owner.scopeCycles.set(target, cycle);
    owner.hydrate(target, { cycle, reason });
  });
};

const runTeardown = (owner, context, cycle, reason) => {
  if (typeof owner.teardown !== "function") {
    return;
  }
  const targets = collectTargets(context, owner.selector);
  if (!targets.length) {
    return;
  }
  targets.forEach((target) => {
    if (owner.teardownCycles.get(target) === cycle) {
      return;
    }
    owner.teardownCycles.set(target, cycle);
    owner.teardown(target, { cycle, reason });
  });
};

const onRehydrate = (event) => {
  const context = resolveContext(event);
  const cycle = toCycle(event);
  const reason = String(event?.detail?.reason || "").trim();
  owners.forEach((owner) => {
    runHydrate(owner, context, cycle, reason);
  });
};

const onTeardown = (event) => {
  const context = resolveContext(event);
  const cycle = toCycle(event);
  const reason = String(event?.detail?.reason || "").trim();
  owners.forEach((owner) => {
    runTeardown(owner, context, cycle, reason);
  });
};

const bindListeners = () => {
  if (listenersBound) {
    return;
  }
  document.addEventListener("app:rehydrate", onRehydrate);
  document.addEventListener("app:teardown", onTeardown);
  listenersBound = true;
};

export const registerHydrationOwner = ({
  id,
  selector = null,
  hydrate,
  teardown = null,
  runNow = true,
}) => {
  const ownerId = String(id || "").trim();
  if (!ownerId) {
    throw new Error("registerHydrationOwner requires a non-empty id");
  }
  if (typeof hydrate !== "function") {
    throw new Error(`registerHydrationOwner(${ownerId}) requires a hydrate function`);
  }

  bindListeners();

  const existing = owners.get(ownerId);
  if (existing) {
    existing.selector = selector;
    existing.hydrate = hydrate;
    existing.teardown = teardown;
    if (runNow) {
      fallbackCycle += 1;
      runHydrate(existing, document, fallbackCycle, "register");
    }
    return;
  }

  const owner = {
    id: ownerId,
    selector,
    hydrate,
    teardown,
    scopeCycles: new WeakMap(),
    teardownCycles: new WeakMap(),
  };
  owners.set(ownerId, owner);

  if (runNow) {
    fallbackCycle += 1;
    runHydrate(owner, document, fallbackCycle, "register");
  }
};
