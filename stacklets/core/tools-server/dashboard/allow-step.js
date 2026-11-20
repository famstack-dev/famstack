// allow-step.js: the two clicks that allow a waiting browser, and
// Dismiss, without the page.
//
//     const step = new AllowStep({ code: "K7MP4XQD", explain, explainShared, send });
//     step.choose("bart", "Bart");   // { chosen: "bart", explainer: "... as Bart ...", shared: false }
//     await step.allow();            // POST /dashboard/api/devices/allow {"code": "K7MP4XQD", "as": "bart"}
//     step.cancel();                 // { chosen: "", explainer: "", shared: false }
//     await step.dismiss();          // POST /dashboard/api/devices/decline {"code": "K7MP4XQD"}
//
// A browser that joins as Homer sees what Homer may see. So a tap on a
// person only chooses: it sends nothing, and the explainer names the
// person and the code the browser must show. Allow is the second click,
// and it sends the person that was chosen with the code of this part.
// Cancel drops the choice and sends nothing. The waiting rows are drawn
// by the tools server (devices/waiting.py); `dashboard/devices.js` wires
// this class to them, so the steps can be tested without a browser
// (tests/js/allow-step.test.js). Who may allow or dismiss is the
// server's decision (devices/joining.py), never this script's.

// ---- The step --------------------------------------------------------------

export const ALLOW = "/dashboard/api/devices/allow";
export const DECLINE = "/dashboard/api/devices/decline";

/**
 * The choice of one waiting browser, and what Allow and Dismiss send.
 *
 * `code` is the request's code as the server takes it ("K7MP4XQD").
 * `explain` is the explainer for a person, with `{name}` where the
 * person's name goes; `explainShared` the one for a shared device. Both
 * name the code already. `send(path, body)` posts and resolves to the
 * server's answer.
 *
 * Example:
 *   const step = new AllowStep({ code: "K7MP4XQD", send,
 *     explain: "This browser will be signed in as {name} and sees what {name} may see. ...",
 *     explainShared: "This browser will be a shared device ..." });
 *   step.choose("shared", "Shared").shared            // true
 *   await step.allow("", "Living room")               // sends {"code": "K7MP4XQD", "as": "shared",
 *                                                     //        "name": "Living room"}
 */
export class AllowStep {
  constructor({ code, explain, explainShared, send }) {
    this.code = code;
    this.explain = explain;
    this.explainShared = explainShared;
    this.send = send;
    this.chosen = null;
  }

  /** Choose `choice` (a person's id, or "shared") called `name`. Sends nothing; returns the view. */
  choose(choice, name) {
    this.chosen = { choice, name };
    return this.view();
  }

  /** Drop the choice. Sends nothing; returns the view, which shows no step. */
  cancel() {
    this.chosen = null;
    return this.view();
  }

  /**
   * What the page shows: `chosen` (the choice, "" for none), the
   * `explainer` for it, and whether the name field of a shared device
   * shows (`shared`).
   */
  view() {
    if (!this.chosen) return { chosen: "", explainer: "", shared: false };
    const shared = this.chosen.choice === "shared";
    const explainer = shared ? this.explainShared : this.explain.replaceAll("{name}", this.chosen.name);
    return { chosen: this.chosen.choice, explainer, shared };
  }

  /**
   * The second click: allow the browser as the chosen person, or as a
   * shared device named `typedName` (`example` when the field is empty).
   * Resolves to the server's answer, or null when nothing is chosen, in
   * which case nothing is sent.
   */
  async allow(typedName = "", example = "") {
    if (!this.chosen) return null;
    const body = { code: this.code, as: this.chosen.choice };
    if (this.chosen.choice === "shared") body.name = typedName.trim() || example;
    return this.send(ALLOW, body);
  }

  /** End the request: the browser is not let in, and reads so. Resolves to the server's answer. */
  async dismiss() {
    return this.send(DECLINE, { code: this.code });
  }
}
