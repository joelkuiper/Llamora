export function initSidebarToggle() {
  const toggle = document.getElementById("sidebar-toggle");
  const container = document.querySelector(".app-container");
  if (!toggle || !container) return;

  const syncState = () => {
    const collapsed = container.classList.contains("sidebar-collapsed");
    toggle.classList.toggle("is-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
  };

  const handleResize = () => {
    if (window.innerWidth < 768) {
      container.classList.add("sidebar-collapsed");
    } else {
      container.classList.remove("sidebar-collapsed");
    }
    syncState();
  };

  toggle.addEventListener("click", () => {
    container.classList.toggle("sidebar-collapsed");
    syncState();
  });

  window.addEventListener("resize", handleResize);
  handleResize();
}
