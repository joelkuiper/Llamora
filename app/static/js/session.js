/* Update the active session in the sidebar */
export function updateActiveSession() {
  const chat = document.getElementById("chat");

  if (!chat) return;

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
