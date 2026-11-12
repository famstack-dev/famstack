// <fs-widget>: the shell every dashboard widget shares (README.md).
//
// The server sends each widget already rendered, in the state it was in,
// so the page works without this script. The shell then keeps it current:
// it reads the widget's one JSON endpoint (`src`), shows the state the
// answer is in (loading, empty, error, content) and hands the answer to
// its content element (`<fs-<name>>`, named by `data-widget`), which
// renders it. A retry reads again; `refresh` (seconds) re-reads quietly
// while the page is visible, and so does a `resync` on the page's event
// stream (`../live.js`), which says the server lost track of what changed.
// A resync read that fails, as while core starts again, tries again after
// `READ_AGAIN_MS`, up to `READ_TRIES` reads, so a widget without `refresh`
// catches up too; one whose service stays down stops asking.

import { connect } from "../live.js";

const QUIET_ERROR = "content"; // a failed refresh keeps what is shown
// How long a failed read after a resync waits before it tries again: about
// the time core takes to start again.
const READ_AGAIN_MS = 10000;
// How many reads a resync makes at most: core starts within them.
const READ_TRIES = 3;

class FsWidget extends HTMLElement {
  connectedCallback() {
    this.addEventListener("click", (event) => {
      if (!event.target.closest(".fs-retry")) return;
      event.preventDefault();
      this.load();
    });
    const every = Number(this.getAttribute("refresh") || 0);
    if (every > 0) {
      this.timer = setInterval(() => this.visible() && this.load({ quiet: true }), every * 1000);
      this.onVisible = () => this.visible() && this.load({ quiet: true });
      document.addEventListener("visibilitychange", this.onVisible);
    }
    this.offResync = connect().onResync(() => this.readAgain());
    if (this.state === "loading") this.load();
  }

  disconnectedCallback() {
    clearInterval(this.timer);
    clearTimeout(this.againTimer);
    if (this.onVisible) document.removeEventListener("visibilitychange", this.onVisible);
    if (this.offResync) this.offResync();
  }

  /** Read quietly after a resync; a failed read tries again later, up to
   *  `READ_TRIES` reads. */
  async readAgain(tries = 1) {
    clearTimeout(this.againTimer);
    if (await this.load({ quiet: true }) || tries >= READ_TRIES) return;
    this.againTimer = setTimeout(() => this.readAgain(tries + 1), READ_AGAIN_MS);
  }

  get state() { return this.getAttribute("state") || "loading"; }

  set state(value) {
    this.setAttribute("state", value);
    this.setAttribute("aria-busy", String(value === "loading"));
  }

  /** The content element the answer goes to: `<fs-activity>` for `data-widget="activity"`. */
  get content() { return this.querySelector(`fs-${this.dataset.widget}`); }

  visible() { return document.visibilityState === "visible"; }

  /** Read the endpoint again, with `params` set on its query from now on;
   *  resolves with whether the read worked. */
  async load({ quiet = false, params = {} } = {}) {
    const src = this.getAttribute("src");
    if (!src || !this.content) return true;
    const url = new URL(src, location.href);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    this.setAttribute("src", url.pathname + url.search);
    const shown = this.state === "content";
    if (!quiet && !shown) this.state = "loading";
    let answer;
    try {
      const response = await fetch(url, { headers: { accept: "application/json" } });
      answer = await response.json();
      if (!response.ok && answer.state !== "error") throw new Error(String(response.status));
    } catch {
      answer = { state: "error" };
    }
    if (answer.state === "error") {
      if (!(quiet && shown)) this.state = "error";
      else this.state = QUIET_ERROR;
      return false;
    }
    this.content.render(answer);
    this.state = answer.state;
    return true;
  }
}

customElements.define("fs-widget", FsWidget);
