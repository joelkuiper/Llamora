export function initSidebarToggle() {
  const toggle = document.getElementById("sidebar-toggle");
  const container = document.querySelector(".app-container");
  if (!toggle || !container) return;

  const handleResize = () => {
    if (window.innerWidth < 768) {
      container.classList.add("sidebar-collapsed");
    } else {
      container.classList.remove("sidebar-collapsed");
    }
  };

  toggle.addEventListener("click", () => {
    container.classList.toggle("sidebar-collapsed");
  });

  window.addEventListener("resize", handleResize);
  handleResize();
}
