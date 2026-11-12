// <fs-setup-page>: the setup page's step, kept current (`setup_page.py`).
//
// The server sends the page complete: the rail, the step and its tiles,
// so it works without this script. This element makes the step's tiles
// work as on the dashboard, because it is the "Your stack" element with
// other markup (`FsStack`): Start posts the job to the dashboard's API, the
// setup forms open in a sheet, and a tile shows the steps of a run as they
// happen, wherever the run started. When a run ends, or the event stream
// asks for a resync, it reads the page it is on again and puts the new rail
// and step in, so a done step shows done and the next one is current
// without a reload of the whole page.
//
//   <fs-setup-page data-base="/dashboard" data-adding="Starting…">
//     <section class="setup-step" data-step="chat">…tiles…</section>
//   </fs-setup-page>
//
// A phone that opens the phone page behind step 4's QR code sends the
// event `phone.reached` (subject `setup/phone`, `phone_reach.py`); the page
// reads itself again, so "Your phone reaches famstack" turns done at once.
// While it has not, step 4's likely causes stay hidden for
// `CAUSES_AFTER_MS`, because a phone still being unlocked has not failed.
//
// A sign-in in the chat app sends no event. While step 3 waits for the
// admin's sign-in, its waiting line names an address to watch
// (`data-watch`, `setup_page.SIGNED_IN`): the page asks it every
// `SIGN_IN_EVERY_MS` and reads itself again once the admin signed in, so
// the step turns done without a reload.
//
// The AI step's own parts (the key, the models, the bars, the read once
// more after the AI's run) work through `ai-step.js`, and the AI's first words through `first-words.js`. It also
// adds "Start all three" and the focus on the step's heading when a step was
// opened from the rail. A service card follows a run, and Copy
// and Show work, as the shared base makes them (`widgets/service-cards.js`).

import { AGAIN_AFTER_MS, enhanceAiStep, readsAgain } from "./ai-step.js";
import { enhanceFirstWords } from "./first-words.js";
import { connect } from "./live.js";
import { FsServiceCards } from "./widgets/service-cards.js";

// How long step 4 waits for the phone before it names the likely causes:
// scanning, opening the link and loading the page take a few seconds.
const CAUSES_AFTER_MS = 45000;
// How often step 3 asks whether the admin signed in: soon enough to feel
// immediate after the app's sign-in, and one small read of the chat each time.
const SIGN_IN_EVERY_MS = 3000;

class FsSetupPage extends FsServiceCards {
  connectedCallback() {
    super.connectedCallback();
    this.addEventListener("click", (event) => this.clicked(event));
    this.offResync = connect().onResync(() => this.reload());
    this.offReach = connect().on("setup/phone", () => this.reload());
    this.since = Date.now();
    this.showCauses();
    const url = new URL(location.href);
    if (url.searchParams.has("step")) this.querySelector(".setup-heading")?.focus();
    if (url.searchParams.has("phone")) history.replaceState(null, "", url.pathname);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this.offResync) this.offResync();
    if (this.offReach) this.offReach();
    clearTimeout(this.causesTimer);
    clearTimeout(this.signInTimer);
    clearTimeout(this.againTimer);
    if (this.stopAi) this.stopAi();
  }

  /** A run's end reads the page at once (`FsStack`); the AI's run, while
   *  the step still is not done, once more a little later (`readsAgain`). */
  follow(event) {
    super.follow(event);
    if (event.type !== "job.ended" || event.data?.target !== "ai") return;
    clearTimeout(this.againTimer);
    this.againTimer = setTimeout(() => {
      const step = this.querySelector(".setup-step");
      if (step && readsAgain(event, { step: step.dataset.step, status: step.dataset.status })) {
        this.reload();
      }
    }, AGAIN_AFTER_MS);
  }

  /** The shared base's buttons, then the AI step's, again after each reload. */
  enhance() {
    super.enhance();
    if (this.stopAi) this.stopAi();
    const stopSteps = [enhanceAiStep(this, this.dataset.base), enhanceFirstWords(this, this.dataset.base)];
    this.stopAi = () => stopSteps.forEach((stop) => stop());
    this.watchSignIn();
  }

  /** While step 3 waits, ask its watch address until the admin signed in, then read the page again. */
  watchSignIn() {
    clearTimeout(this.signInTimer);
    const watch = this.querySelector(".setup-waiting[data-watch]")?.dataset.watch;
    if (!watch) return;
    this.signInTimer = setTimeout(async () => {
      let signedIn = false;
      try {
        const response = await fetch(new URL(watch, location.href), { headers: { accept: "application/json" } });
        signedIn = response.ok && (await response.json()).signed_in === true;
      } catch {
        // the stack did not answer; the next ask tries again
      }
      // A page read again without the waiting line stops here; a read that
      // failed leaves the line, so the watch goes on.
      if (signedIn) await this.reload();
      this.watchSignIn();
    }, SIGN_IN_EVERY_MS);
  }

  /** FsStack reads its view again through its widget; here that is the page. */
  get widget() { return { load: () => this.reload() }; }

  /** Where jobs are started: the dashboard's API, as the dashboard does. */
  get jobs() { return new URL(`${this.dataset.base}/api/jobs`, location.href); }

  /** Read the page again and put its rail and step in place of these. */
  async reload() {
    let fresh;
    try {
      const response = await fetch(location.href, { headers: { accept: "text/html" } });
      if (!response.ok) return;
      fresh = new DOMParser().parseFromString(await response.text(), "text/html");
    } catch {
      return; // the page keeps what it shows; the next run's end tries again
    }
    const rail = fresh.querySelector(".setup-rail");
    const step = fresh.querySelector("fs-setup-page");
    if (rail) document.querySelector(".setup-rail")?.replaceWith(document.adoptNode(rail));
    if (step) this.replaceChildren(...[...step.childNodes].map((node) => document.adoptNode(node)));
    this.enhance();
    this.showCauses();
    const said = this.querySelector(".setup-done, .setup-waiting");
    if (said) document.getElementById("setup-announce").textContent = said.textContent;
  }

  /** Show step 4's likely causes once the page has waited long enough. */
  showCauses() {
    const causes = this.querySelector(".setup-causes");
    if (!causes) return;
    clearTimeout(this.causesTimer);
    const left = this.since + CAUSES_AFTER_MS - Date.now();
    if (left <= 0) causes.hidden = false;
    else this.causesTimer = setTimeout(() => { causes.hidden = false; }, left);
  }

  clicked(event) {
    const button = event.target.closest(".setup-start-all");
    if (button) this.startAll(button);
  }

  /** "Start all three": every tile of the step that can start now. */
  startAll(button) {
    button.disabled = true;
    for (const add of this.querySelectorAll(".tile-add:not([disabled])")) this.add(add);
  }
}

customElements.define("fs-setup-page", FsSetupPage);
