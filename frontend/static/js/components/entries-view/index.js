import { EntryView } from "./entry-view.js";
import "../day-opening.js";
import "../search-overlay.js";
import "../scroll-edge-button.js";
import "../entry-tags.js";
import "../entry-edit-autosize.js";
import "../char-counter.js";

function registerEntryElements() {
  if (!customElements.get("entry-view")) {
    customElements.define("entry-view", EntryView);
  }
}

registerEntryElements();
document.addEventListener("app:rehydrate", registerEntryElements);
