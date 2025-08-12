// Adds a simple loading indicator to auth forms
// Disables the submit button and animates a braille "dots" spinner.
// From https://www.npmjs.com/package/cli-spinners
// https://stackoverflow.com/questions/2685435/cooler-ascii-spinners
document.addEventListener('DOMContentLoaded', () => {
  const SPINNER = {
    interval: 80,
    // frames: ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    frames: ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"]
  };

  document.querySelectorAll('.form-container form').forEach(form => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('button[type="submit"]');
      if (!btn || btn.dataset.spinning === '1') return;

      const loadingText = btn.dataset.loading || 'Loading';
      const originalText = btn.textContent;

      btn.dataset.spinning = '1';
      btn.dataset.originalText = originalText;

      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');

      let i = 0;
      btn.textContent = `${SPINNER.frames[i]} ${loadingText}`;

      const id = setInterval(() => {
        i = (i + 1) % SPINNER.frames.length;
        btn.textContent = `${SPINNER.frames[i]} ${loadingText}`;
      }, SPINNER.interval);

      btn.dataset.spinnerId = String(id);
    });
  });
});

function stopButtonSpinner(btn) {
  const id = btn && btn.dataset.spinnerId;
  if (id) clearInterval(Number(id));
  if (btn && btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
  if (btn) {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    btn.removeAttribute('data-spinner-id');
  }
}
