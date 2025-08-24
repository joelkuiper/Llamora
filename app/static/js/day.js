/* Update UI state for the active day */
export function updateActiveDay() {
  const chat = document.getElementById("chat");
  const profileBtn = document.getElementById("profile-btn");
  const calendarLabel = document.getElementById("calendar-label");

  if (profileBtn && !profileBtn.dataset.backInit) {
    profileBtn.addEventListener("click", () => {
      sessionStorage.setItem("profile-return", window.location.pathname);
    });
    profileBtn.dataset.backInit = "true";
  }

  if (!chat) {
    profileBtn?.classList.add("active");
    return;
  }

  profileBtn?.classList.remove("active");

  const activeDate = chat.dataset.date;

  if (calendarLabel && activeDate) {
    calendarLabel.textContent = formatLongDate(activeDate);
  }

  const calendar = document.getElementById("calendar");
  if (!calendar || !activeDate) return;

  const [year, month] = activeDate.split("-");

  const highlight = () => {
    calendar.querySelectorAll("td.active").forEach((td) => td.classList.remove("active"));
    const cell = calendar.querySelector(`.calendar-table td[data-date='${activeDate}']`);
    if (cell) cell.classList.add("active");
  };

  if (calendar.dataset.year !== year || calendar.dataset.month !== month) {
    const url = `/calendar/${year}/${parseInt(month, 10)}`;
    const handler = (e) => {
      if (e.target.id === "calendar") {
        highlight();
        document.body.removeEventListener("htmx:afterSwap", handler);
      }
    };
    document.body.addEventListener("htmx:afterSwap", handler);
    htmx.ajax("GET", url, "#calendar");
  } else {
    highlight();
  }
}

function formatLongDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const day = dt.getDate();
  let suffix = "th";
  if (day % 100 < 11 || day % 100 > 13) {
    suffix = { 1: "st", 2: "nd", 3: "rd" }[day % 10] || "th";
  }
  const month = dt.toLocaleString(undefined, { month: "long" });
  return `${day}${suffix} of ${month} ${dt.getFullYear()}`;
}

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
