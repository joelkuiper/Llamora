import { ChatView } from "./components/chat-view.js";

if (!customElements.get("chat-view")) {
  customElements.define("chat-view", ChatView);
}
