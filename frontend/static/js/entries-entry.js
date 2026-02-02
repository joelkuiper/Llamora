import { EntryView } from "./components/entry-view.js";
import "./components/day-opening.js";
import "./components/search-overlay.js";
import "./components/scroll-bottom-button.js";
import "./components/entry-tags.js";

function registerEntryElements() {
  if (!customElements.get("entry-view")) {
    customElements.define("entry-view", EntryView);
  }
}

registerEntryElements();
document.addEventListener("app:rehydrate", registerEntryElements);
