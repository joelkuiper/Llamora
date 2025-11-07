import { ReactiveElement } from "../utils/reactive-element.js";
import { prefersReducedMotion } from "../utils/motion.js";

class ScrollBottomButtonElement extends ReactiveElement {
  #button = null;
  #clickReset = null;

  connectedCallback() {
    super.connectedCallback();
    this.#ensureButton();
    this.setVisible(false);
  }

  disconnectedCallback() {
    if (this.#clickReset != null) {
      window.clearTimeout(this.#clickReset);
      this.#clickReset = null;
    }
    super.disconnectedCallback();
  }

  get button() {
    return this.#button;
  }

  setVisible(visible) {
    if (visible) {
      this.classList.add("visible");
    } else {
      this.classList.remove("visible");
      this.#button?.classList.remove("clicked");
    }
  }

  pulse() {
    if (!this.#button || prefersReducedMotion()) return;
    this.#button.classList.add("clicked");
    if (this.#clickReset != null) {
      window.clearTimeout(this.#clickReset);
    }
    this.#clickReset = window.setTimeout(() => {
      this.#button?.classList.remove("clicked");
      this.#clickReset = null;
    }, 300);
  }

  #ensureButton() {
    if (this.#button && this.contains(this.#button)) {
      return;
    }

    const existing = this.querySelector("button");
    if (existing) {
      this.#configureButton(existing);
      return;
    }

    this.innerHTML = `
      <button type="button" class="scroll-btn" aria-label="Scroll to bottom">
        <svg width="100%" height="100%" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M8 12L12 16M12 16L16 12M12 16V8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      </button>
    `;

    const button = this.querySelector("button");
    if (button) {
      this.#configureButton(button);
    }
  }

  #configureButton(button) {
    this.#button = button;
    if (!this.#button.classList.contains("scroll-btn")) {
      this.#button.classList.add("scroll-btn");
    }
    if (!this.#button.hasAttribute("aria-label")) {
      this.#button.setAttribute("aria-label", "Scroll to bottom");
    }
  }
}

if (!customElements.get("scroll-bottom-button")) {
  customElements.define("scroll-bottom-button", ScrollBottomButtonElement);
}

export { ScrollBottomButtonElement as ScrollBottomButton };
