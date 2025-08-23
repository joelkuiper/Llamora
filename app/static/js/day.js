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
    const link = calendar.querySelector(`.calendar-table [data-date='${activeDate}']`);
    if (link) link.closest("td").classList.add("active");
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
