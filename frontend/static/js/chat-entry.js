import { ChatView } from "./components/chat-view.js";
import "./components/search-overlay.js";
import "./components/scroll-bottom-button.js";
import "./components/tags.js";

if (!customElements.get("chat-view")) {
  customElements.define("chat-view", ChatView);
}
