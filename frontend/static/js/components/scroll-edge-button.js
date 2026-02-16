import { ReactiveElement } from "../utils/reactive-element.js";
import { normalizeEdgeDirection } from "../utils/scroll-edge.js";
import { animateMotion } from "../utils/transition.js";

class ScrollEdgeButtonElement extends ReactiveElement {
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

  get direction() {
    return normalizeEdgeDirection(this.dataset?.direction || this.dataset?.edgeDirection, "down");
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
      this.#applyDirectionConfig();
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
    this.#applyDirectionConfig();
  }

  #applyDirectionConfig() {
    const button = this.#button;
    if (!button) return;
    const direction = this.direction;
    const icon = direction === "up" ? "icon-chevron-up" : "icon-chevron-down";
    const label = direction === "up" ? "Scroll to top" : "Scroll to bottom";

    const iconEl = button.querySelector(".icon-mask");
    if (iconEl) {
      iconEl.classList.remove("icon-chevron-up", "icon-chevron-down");
      iconEl.classList.add(icon);
    }

    button.setAttribute("aria-label", label);
  }
}

if (!customElements.get("scroll-edge-button")) {
  customElements.define("scroll-edge-button", ScrollEdgeButtonElement);
}

export { ScrollEdgeButtonElement as ScrollEdgeButton };
