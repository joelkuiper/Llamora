/* Update UI state for the active day */
export function updateActiveDay() {
  const chat = document.getElementById("chat");
  const profileBtn = document.getElementById("profile-btn");

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
}
