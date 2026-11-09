// <fs-status>: the dashboard's status pills, and the service card each opens
// (README.md, `status_pills.py`).
//
// The page arrives with the pills rendered by the server: one per set-up
// service, its name and its status word, then one link to what is left on
// /setup. Without a script a pill is a link to "Your stack" on /setup. This
// element turns each pill into the opener of its service card, in a pane at
// the side (a modal `<dialog>`), and keeps the pills current:
//
//   job.started   the pill and its service card say Starting; a service
//                 that had no pill yet (a first start) gets one, as the
//                 view read again shows it
//   step.*        the open service card's current step and its details
//   job.ended     the view is read again (`/dashboard/api/stack`), so a
//                 pill says how the start went and the link what is left;
//                 once the chat runs, the page's intro says it does
//
// The service cards follow a run as on /setup, through the shared base
// (`service-cards.js`). A refreshed view carries its markup already
// rendered (`nav_html`, `cards_html`), so there is one renderer, the
// server's; the pane stays open on the service it showed.
//
//   <fs-status data-starting="Starting" data-adding="Starting…">
//     <nav class="statuses">… <a class="fs-status" data-service="photos">…</a></nav>
//     <dialog class="service-card-pane">… <article class="service-card" …></dialog>
//   </fs-status>

import { FsServiceCards, status } from "./service-cards.js";

class FsStatus extends FsServiceCards {
  connectedCallback() {
    super.connectedCallback();
    this.addEventListener("click", (event) => this.clicked(event));
    this.pane.addEventListener("close", () => this.closed());
  }

  get pane() { return this.querySelector(".service-card-pane"); }

  clicked(event) {
    const pill = event.target.closest(".fs-status[data-service]");
    if (pill) {
      event.preventDefault();
      this.open(pill.dataset.service);
    } else if (event.target.closest(".service-card-close") || event.target === this.pane) {
      this.pane.close(); // the close button, or the dimmed page beside the pane
    }
  }

  /** Open the service card of `service` in the pane. */
  open(service) {
    this.shown = service;
    for (const card of this.pane.querySelectorAll(".service-card")) {
      card.hidden = card.dataset.service !== service;
    }
    const card = this.pane.querySelector(`.service-card[data-service="${CSS.escape(service)}"]`);
    if (!card) return;
    if (!this.pane.open) this.pane.showModal();
    card.querySelector(".service-card-name")?.setAttribute("id", "service-card-shown");
    this.pane.setAttribute("aria-labelledby", "service-card-shown");
  }

  /** The pane closed: the focus goes back to the pill that opened it. */
  closed() {
    const pill = this.querySelector(`.fs-status[data-service="${CSS.escape(this.shown || "")}"]`);
    this.shown = null;
    pill?.focus();
  }

  /** A run starts: its service card follows it, its pill says Starting, and
   *  a service without a pill yet gets one from the view read again. */
  started(card, job) {
    if (!card) {
      this.widget.load({ quiet: true });
      return;
    }
    super.started(card, job);
    const pill = this.querySelector(`.fs-status[data-service="${CSS.escape(card.dataset.service || "")}"]`);
    pill?.querySelector(".service-card-status")?.replaceWith(status(this.dataset.starting));
  }

  /** Render an answer of `/api/stack`: the pills, the link and the service
   *  cards, as `status_pills.html()` writes them; the pane stays as it was. */
  render(view) {
    this.dataset.starting = view.starting;
    this.dataset.adding = view.adding;
    const nav = document.createElement("template");
    nav.innerHTML = view.nav_html;
    this.querySelector(".statuses").replaceWith(nav.content);
    this.pane.querySelector(".service-card-pane-body").innerHTML = view.cards_html;
    this.enhance();
    if (view.pills.some((p) => p.id === "messages" && p.status === "running")) chatRuns();
    if (this.shown && this.pane.open) {
      if (this.pane.querySelector(`.service-card[data-service="${CSS.escape(this.shown)}"]`)) {
        this.open(this.shown);
      } else {
        this.pane.close();
      }
    } else {
      for (const card of this.pane.querySelectorAll(".service-card")) card.hidden = true;
    }
  }
}

/** The chat runs now: the page's intro says so. The page was served
 *  before it did, with that sentence kept for now (`data-once-chat-runs`,
 *  dashboard_page.welcome), so it needs no reload. */
function chatRuns() {
  const lede = document.querySelector(".lede[data-once-chat-runs]");
  if (!lede) return;
  lede.textContent = lede.dataset.onceChatRuns;
  delete lede.dataset.onceChatRuns;
}

if (!customElements.get("fs-status")) customElements.define("fs-status", FsStatus);
