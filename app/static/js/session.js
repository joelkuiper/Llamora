/* Update the active session in the sidebar */
export function updateActiveSession() {
  const chat = document.getElementById("chat");
  const profileBtn = document.getElementById("profile-btn");

  if (profileBtn && !profileBtn.dataset.backInit) {
    profileBtn.addEventListener("click", () => {
      sessionStorage.setItem("profile-return", window.location.pathname);
    });
    profileBtn.dataset.backInit = "true";
  }

  if (!chat) {
    document.querySelectorAll("#sidebar li").forEach((li) => li.classList.remove("active"));
    profileBtn?.classList.add("active");
    return;
  }

  profileBtn?.classList.remove("active");
  const newSessionId = chat.dataset.sessionId;

  document.querySelectorAll("#sidebar li").forEach((li) => {
    const link = li.querySelector("a");
    if (link?.dataset.sessionId === newSessionId) {
      li.classList.add("active");
    } else {
      li.classList.remove("active");
    }
  });
};
