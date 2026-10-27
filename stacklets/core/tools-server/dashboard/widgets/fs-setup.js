// <fs-setup>: the "Setting up" widget, step by step (README.md).
//
// The page arrives with the steps rendered by the server
// (`setup_widget.py`); this element renders a refreshed view with the
// `steps` component, each step's body as the server rendered it. Its tiles
// work as in "Your stack", because it is that widget's element with other
// markup (`FsStack`): Add posts the job, a tile shows its job's steps as
// they happen, and the widget reads its view again when a job ends, so the
// chat's step ends done and the next one opens.
//
// "Hide for now" folds the steps into "Your stack" on this device: the
// widget is hidden and a line in "Your stack" says how many steps are left
// and what is next, with Continue to bring them back. Kept in this device's
// localStorage, like who is looking (family.js); without storage nothing
// is folded. Without JavaScript there is no button, and the steps stay.

import { steps } from "../components/steps.js";
import { FsStack } from "./fs-stack.js";

// The key the fold is kept under, in this device's localStorage.
const FOLDED = "fs-setup-folded";

function stored(value) {
  try {
    if (value === undefined) return globalThis.localStorage?.getItem(FOLDED) === "1";
    if (value) globalThis.localStorage?.setItem(FOLDED, "1");
    else globalThis.localStorage?.removeItem(FOLDED);
  } catch {
    // No storage: nothing is remembered.
  }
  return false;
}

class FsSetup extends FsStack {
  connectedCallback() {
    super.connectedCallback();
    this.addEventListener("click", (event) => {
      if (event.target.closest(".setup-hide")) this.fold(true);
    });
    const hide = this.querySelector(".setup-hide");
    if (hide) hide.hidden = false;
    if (stored()) this.fold(true);
  }

  /** Fold the steps into "Your stack" (`on`), or bring them back. */
  fold(on) {
    stored(on);
    this.widget.style.display = on ? "none" : "";
    const stack = document.querySelector('fs-widget[data-widget="stack"] [data-state="content"]');
    stack?.querySelector(".setup-remainder")?.remove();
    if (!on || !stack) return;
    const go = Object.assign(document.createElement("button"),
      { className: "fs-button", type: "button", textContent: this.dataset.continue });
    go.dataset.kind = "go";
    go.addEventListener("click", () => this.fold(false));
    const line = Object.assign(document.createElement("p"), { textContent: this.dataset.left });
    const remainder = Object.assign(document.createElement("div"), { className: "setup-remainder" });
    remainder.append(line, go);
    stack.prepend(remainder);
  }

  /** Render an answer of `/api/setup`: the markup setup_widget.html() writes. */
  render(view) {
    Object.assign(this.dataset, { adding: view.adding, hide: view.hide, left: view.left,
                                  continue: view.continue });
    const head = document.createElement("div");
    head.className = "setup-head";
    const count = Object.assign(document.createElement("p"), { className: "setup-count", textContent: view.count });
    const hide = Object.assign(document.createElement("button"),
      { className: "setup-hide", type: "button", textContent: view.hide });
    head.append(count, hide);
    this.replaceChildren(head, steps(view.steps.map((step) => ({
      id: step.id, title: step.title, sub: step.sub, done: step.done, body: fragment(step.body_html),
    }))));
    if (stored()) this.fold(true);
  }
}

/** The HTML the server rendered for a step, as nodes. */
function fragment(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}

customElements.define("fs-setup", FsSetup);
