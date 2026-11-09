// Service cards that follow a run: the base the setup page and the
// dashboard's status pills share (`service_card.py`).
//
// A service card shows one service: its status, its Open, how to sign in.
// While a start goes on it shows the current step, with every step in a
// closed "Show details". This element makes that live: it is the "Your
// stack" element (`FsStack`), which follows every job on the page's event
// stream, with a service card as its tile. When a run starts, the service
// card says Starting and shows the run; each step goes into its list and
// becomes the current step. It also adds what needs a script: Copy beside
// a value, and Show for the password, masked until then.
//
//   import { FsServiceCards } from "./widgets/service-cards.js";
//   class FsSetupPage extends FsServiceCards { ... }
//
// The setup page (`setup-page.js`) shows tiles beside its service cards;
// the dashboard (`fs-status.js`) shows service cards only, in a pane its
// pills open. What happens when a run ends is theirs: each reads its own
// view again.

import { FsStack } from "./fs-stack.js";
import { copyText } from "../copy-value.js";

const MASK = "••••••••";

/** Service cards (and tiles) that start a service and follow its run,
 *  with Copy and Show in their sign-in; extended by the setup page and the
 *  dashboard's status pills.
 *
 *      class FsStatus extends FsServiceCards { render(view) { ... } }
 */
export class FsServiceCards extends FsStack {
  connectedCallback() {
    super.connectedCallback();
    this.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button?.classList.contains("setup-copy")) this.copy(button);
      else if (button?.classList.contains("setup-show")) this.show(button);
    });
    this.enhance();
  }

  /** The tiles are tiles or service cards. */
  get tileSelector() { return ".tile, .service-card"; }

  nameOf(tile) {
    return tile?.querySelector(".service-card-name")?.textContent || super.nameOf(tile);
  }

  /** A run starts for a tile or a service card: a service card shows it
   *  in its own markup, a tile as on the dashboard. */
  started(tile, job) {
    if (!tile?.classList.contains("service-card")) return super.started(tile, job);
    if (tile.dataset.job === job.id) return;
    tile.dataset.job = job.id;
    tile.dataset.status = "starting";
    const head = tile.querySelector(".service-card-head");
    head.replaceChildren(...head.querySelectorAll(".service-card-name, .service-card-app"),
      status(this.dataset.starting || this.dataset.adding));
    const steps = (job.steps || []).map((s) => item(s.text, s.state, s.id));
    const live = el("div", { class: "service-card-live" },
      el("p", { class: "service-card-run", "aria-live": "polite" },
        el("span", { class: "service-card-spin", "aria-hidden": "true" }),
        el("span", { class: "service-card-now" }, steps.at(-1)?.textContent || this.dataset.adding)),
      el("details", { class: "service-card-details" },
        el("summary", {}, tile.dataset.showDetails || ""), el("ol", { class: "tile-steps" }, ...steps)));
    const act = tile.querySelector(".service-card-act, .service-card-live");
    if (act) act.replaceWith(live);
    else head.after(live);
  }

  /** A step of a run: into the list, and on a service card the current step. */
  step(list, event) {
    super.step(list, event);
    const now = list?.closest(".service-card")?.querySelector(".service-card-now");
    const running = list?.querySelectorAll('.tile-step[data-status="running"]');
    const last = running?.length ? running[running.length - 1] : list?.lastElementChild;
    if (now && last) now.textContent = last.textContent;
  }

  /** Show the buttons only a script can make work, and mask the password. */
  enhance() {
    for (const button of this.querySelectorAll(".setup-mini, .setup-start-all")) button.hidden = false;
    for (const secret of this.querySelectorAll("[data-secret]")) {
      if (secret.textContent === MASK) continue;
      secret.dataset.secret = secret.textContent;
      secret.textContent = MASK;
    }
  }

  /** Copy the value beside the button; the button says so for a moment. */
  async copy(button) {
    const value = button.parentElement.querySelector(".setup-value");
    const text = value?.dataset.secret || value?.textContent || "";
    if (!(await copyText(text))) return;
    const label = button.textContent;
    button.textContent = button.closest("[data-copied]")?.dataset.copied || label;
    setTimeout(() => { button.textContent = label; }, 1600);
  }

  /** Show the password, or mask it again. */
  show(button) {
    const value = button.parentElement.querySelector("[data-secret]");
    if (!value) return;
    const masked = value.textContent === MASK;
    value.textContent = masked ? value.dataset.secret : MASK;
    const box = button.closest("[data-hide]");
    button.dataset.label ||= button.textContent;
    button.textContent = masked ? box?.dataset.hide || button.dataset.label : button.dataset.label;
  }
}

/** The status "Starting", with its mark, as the server writes it
 *  (`service_card.status_html`): a pill or a service card shows it when a
 *  run starts.
 *
 *      head.append(status("Starting"));
 *      // <span class="service-card-status" data-status="starting"><span class="dot" ...>Starting</span>
 */
export function status(word) {
  return el("span", { class: "service-card-status", "data-status": "starting" },
    el("span", { class: "dot", "data-status": "starting", "aria-hidden": "true" }), word);
}

// ---- Markup for a service card's run ----

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...children);
  return node;
}

function item(text, state, id) {
  const li = el("li", { class: "tile-step", "data-status": state || "" }, text);
  if (id) li.dataset.step = id;
  return li;
}
