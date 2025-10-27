import { ChatView } from "./components/chat-view.js";
import "./components/search-overlay.js";

if (!customElements.get("chat-view")) {
  customElements.define("chat-view", ChatView);
}
