/* Update the active session in the sidebar */
export function updateActiveSession() {
  const marker = document.getElementById("chat-box");

  if (!marker) return;

  const newSessionId = marker.dataset.sessionId;

  document.querySelectorAll("#sidebar li").forEach((li) => {
    const link = li.querySelector("a");
    if (link?.dataset.sessionId === newSessionId) {
      li.classList.add("active");
    } else {
      li.classList.remove("active");
    }
  });
};
