import { ChatView } from "./components/chat-view.js";
import "./components/search-overlay.js";
import "./components/tags.js";

if (!customElements.get("chat-view")) {
  customElements.define("chat-view", ChatView);
}
