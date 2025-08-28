/* Initialize previous/next day navigation buttons */
export function initDayNav() {
  const chat = document.getElementById("chat");
  const prevBtn = document.getElementById("prev-day");
  const nextBtn = document.getElementById("next-day");
  if (!chat || !prevBtn || !nextBtn) return;

  const activeDate = chat.dataset.date;
  if (!activeDate) return;

  const [y, m, d] = activeDate.split("-").map(Number);
  const current = new Date(y, m - 1, d);

  const label = document.getElementById("calendar-label");
  if (label) {
    const day = current.getDate();
    const suffix =
      day % 10 === 1 && day % 100 !== 11
        ? "st"
        : day % 10 === 2 && day % 100 !== 12
        ? "nd"
        : day % 10 === 3 && day % 100 !== 13
        ? "rd"
        : "th";
    const month = current.toLocaleDateString(undefined, {
      month: "long",
    });
    const year = current.getFullYear();
    label.textContent = `${day}${suffix} of ${month} ${year}`;
  }

  const format = (dt) =>
    `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;

  const prev = new Date(current);
  prev.setDate(current.getDate() - 1);
  const next = new Date(current);
  next.setDate(current.getDate() + 1);

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);

  const prevStr = format(prev);
  const nextStr = format(next);

  prevBtn.disabled = false;
  prevBtn.dataset.tooltipTitle = prevStr === format(yesterday) ? "Yesterday" : "Previous day";
  prevBtn.onclick = () => navigate(prevStr);

  if (next > today) {
    nextBtn.disabled = true;
    nextBtn.removeAttribute("data-tooltip-title");
    nextBtn.onclick = null;
  } else {
    nextBtn.disabled = false;
    nextBtn.dataset.tooltipTitle = "Next day";
    nextBtn.onclick = () => navigate(nextStr);
  }

  function navigate(dateStr) {
    htmx.ajax("GET", `/c/${dateStr}`, {
      target: "#content-wrapper",
      swap: "outerHTML",
      pushUrl: `/d/${dateStr}`,
    });
  }
}
