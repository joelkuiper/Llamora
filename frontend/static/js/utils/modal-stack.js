const DEFAULT_BASE = 1200;

export function nextModalZ() {
  const body = document.body;
  if (!body) return DEFAULT_BASE;
  const current = Number(body.dataset.modalZ || DEFAULT_BASE);
  const next = Number.isFinite(current) ? current + 2 : DEFAULT_BASE + 2;
  body.dataset.modalZ = String(next);
  return next;
}
