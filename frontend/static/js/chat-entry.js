import { ChatView } from "./components/chat-view.js";
import "./components/search-overlay.js";
import "./components/scroll-bottom-button.js";
import "./components/tags.js";

function registerChatElements() {
  if (!customElements.get("chat-view")) {
    customElements.define("chat-view", ChatView);
  }
}

registerChatElements();
document.addEventListener("app:rehydrate", registerChatElements);
