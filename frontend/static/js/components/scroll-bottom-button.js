import { ReactiveElement } from "../utils/reactive-element.js";
import { animateMotion } from "../utils/transition.js";

class ScrollBottomButtonElement extends ReactiveElement {
  #button = null;
  #pulseCleanup = null;

  connectedCallback() {
    super.connectedCallback();
    this.#ensureButton();
    this.setVisible(false);
  }

  disconnectedCallback() {
    if (typeof this.#pulseCleanup === "function") {
      this.#pulseCleanup();
      this.#pulseCleanup = null;
    }
    super.disconnectedCallback();
  }

  get button() {
    return this.#button;
  }

  setVisible(visible) {
    const button = this.#button;
    if (visible) {
      this.classList.add("visible");
      if (button) {
        button.removeAttribute("aria-hidden");
        button.tabIndex = 0;
        if ("inert" in button) {
          button.inert = false;
          button.toggleAttribute("inert", false);
        } else {
          button.disabled = false;
        }
      }
    } else {
      this.classList.remove("visible");
      if (button) {
        button.setAttribute("aria-hidden", "true");
        button.tabIndex = -1;
        if ("inert" in button) {
          button.inert = true;
          button.toggleAttribute("inert", true);
        } else {
          button.disabled = true;
        }
        if (typeof this.#pulseCleanup === "function") {
          this.#pulseCleanup();
          this.#pulseCleanup = null;
        }
      }
    }
  }

  pulse() {
    const button = this.#button;
    if (!button) return;

    if (typeof this.#pulseCleanup === "function") {
      this.#pulseCleanup();
      this.#pulseCleanup = null;
    }

    this.#pulseCleanup = animateMotion(button, "motion-animate-tactile", {
      onFinish: () => {
        this.#pulseCleanup = null;
      },
      onCancel: () => {
        this.#pulseCleanup = null;
      },
    });
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
        <span class="icon-mask icon-chevron-down" aria-hidden="true"></span>
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
