// The two clicks that allow a waiting browser, and Dismiss
// (stacklets/core/tools-server/dashboard/allow-step.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages. The
// explainers are the words the tools server puts on the page
// (devices/waiting.py), with the code filled in. The wiring runs on a
// stand-in for one row's markup: only the elements and the attributes
// devices/waiting.py draws, with their click handlers.

import assert from "node:assert/strict";
import test from "node:test";

import { ALLOW, AllowStep, DECLINE, wireAllowStep } from "../../stacklets/core/tools-server/dashboard/allow-step.js";

const EXPLAIN = "This browser will be signed in as {name} and sees what {name} may see. "
  + "Allow it only if this browser is in front of you and shows the code 5F67-ZFFA.";
const EXPLAIN_SHARED = "This browser will be a shared device and sees what the family shares, "
  + "nobody's own things. Allow it only if this browser is in front of you and shows the code 5F67-ZFFA.";

/** A step for the waiting browser 5F67-ZFFA, and the requests it sent. */
function waiting() {
  const sent = [];
  const send = async (path, body) => {
    sent.push({ path, body });
    return { line: "done" };
  };
  return { step: new AllowStep({ code: "5F67ZFFA", explain: EXPLAIN, explainShared: EXPLAIN_SHARED, send }), sent };
}

test("a tap on a person only chooses: nothing is sent, the explainer names the person and the code", () => {
  const { step, sent } = waiting();
  const view = step.choose("homer", "Homer");
  assert.equal(sent.length, 0);
  assert.equal(view.chosen, "homer");
  assert.equal(view.shared, false);
  assert.equal(view.explainer, "This browser will be signed in as Homer and sees what Homer may see. "
    + "Allow it only if this browser is in front of you and shows the code 5F67-ZFFA.");
});

test("Allow sends the person last chosen, with the code of this browser", async () => {
  const { step, sent } = waiting();
  step.choose("homer", "Homer");
  step.choose("bart", "Bart");
  assert.equal(sent.length, 0);
  await step.allow();
  assert.deepEqual(sent, [{ path: ALLOW, body: { code: "5F67ZFFA", as: "bart" } }]);
});

test("Cancel drops the choice: nothing is sent, and Allow then sends nothing", async () => {
  const { step, sent } = waiting();
  step.choose("bart", "Bart");
  assert.deepEqual(step.cancel(), { chosen: "", explainer: "", shared: false });
  assert.equal(await step.allow(), null);
  assert.equal(sent.length, 0);
});

test("Shared shows the name field and its own explainer, and Allow sends the name", async () => {
  const { step, sent } = waiting();
  const view = step.choose("shared", "Shared");
  assert.equal(view.shared, true);
  assert.equal(view.explainer, EXPLAIN_SHARED);
  await step.allow("  Kitchen ", "Living room");
  assert.deepEqual(sent, [{ path: ALLOW, body: { code: "5F67ZFFA", as: "shared", name: "Kitchen" } }]);
});

test("Shared with an empty name field is named as the field's example", async () => {
  const { step, sent } = waiting();
  step.choose("shared", "Shared");
  await step.allow("", "Living room");
  assert.equal(sent[0].body.name, "Living room");
});

test("Dismiss ends the request of this browser", async () => {
  const { step, sent } = waiting();
  await step.dismiss();
  assert.deepEqual(sent, [{ path: DECLINE, body: { code: "5F67ZFFA" } }]);
});

// ---- The wiring on one row -------------------------------------------------

/** A stand-in element: the attributes the wiring reads and sets, and its click handlers. */
class Element {
  constructor(dataset = {}, extra = {}) {
    this.dataset = dataset;
    this.hidden = false;
    this.textContent = "";
    this.attributes = {};
    this.handlers = [];
    Object.assign(this, extra);
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, handler) { if (type === "click") this.handlers.push(handler); }
  async click() { for (const handler of this.handlers) await handler(); }
}

/** One waiting browser's row as devices/waiting.py draws it, for Homer and Bart and Shared. */
function row() {
  const pills = [["homer", "Homer"], ["bart", "Bart"], ["shared", "Shared"]]
    .map(([choose, name]) => new Element({ choose, name }));
  const inside = {
    '[data-shared-name]': new Element(),
    '[data-explainer]': new Element(),
    '[data-allow-step]': new Element(),
    '[data-cancel]': new Element(),
    'input[name="name"]': new Element({}, { value: "", placeholder: "Living room" }),
  };
  const panel = new Element({ explain: EXPLAIN, explainShared: EXPLAIN_SHARED }, { hidden: true });
  panel.querySelector = (selector) => inside[selector];
  const decline = new Element();
  const part = new Element({ join: "5F67ZFFA" });
  part.querySelector = (selector) => ({ ".allow-step": panel, "[data-decline]": decline })[selector];
  part.querySelectorAll = (selector) => (selector === "[data-choose]" ? pills : []);
  const pill = (choose) => pills.find((p) => p.dataset.choose === choose);
  return { part, panel, pill, inside, decline };
}

/** The row wired, and the requests and answers it made. */
function wired() {
  const sent = [];
  const shown = [];
  const send = async (path, body) => {
    sent.push({ path, body });
    return { line: "done" };
  };
  const said = (part, answer) => shown.push(answer);
  const page = row();
  wireAllowStep(page.part, { send, said });
  return { ...page, sent, shown };
}

test("a click on a pill sends nothing: it highlights the pill and opens the explainer", async () => {
  const { pill, panel, inside, sent } = wired();
  await pill("bart").click();
  assert.equal(sent.length, 0);
  assert.equal(pill("bart").attributes["aria-pressed"], "true");
  assert.equal(pill("homer").attributes["aria-pressed"], "false");
  assert.equal(panel.hidden, false);
  assert.equal(inside["[data-explainer]"].textContent.startsWith("This browser will be signed in as Bart "), true);
});

test("Allow after the pill sends the chosen person, and the page shows the answer", async () => {
  const { pill, inside, sent, shown } = wired();
  await pill("bart").click();
  await inside["[data-allow-step]"].click();
  assert.deepEqual(sent, [{ path: ALLOW, body: { code: "5F67ZFFA", as: "bart" } }]);
  assert.deepEqual(shown, [{ line: "done" }]);
});

test("Shared shows the name field, and Cancel closes the step without a request", async () => {
  const { pill, panel, inside, sent } = wired();
  await pill("shared").click();
  assert.equal(inside["[data-shared-name]"].hidden, false);
  await inside["[data-cancel]"].click();
  assert.equal(panel.hidden, true);
  assert.equal(pill("shared").attributes["aria-pressed"], "false");
  assert.equal(sent.length, 0);
});

test("Dismiss on the row ends the request", async () => {
  const { decline, sent } = wired();
  await decline.click();
  assert.deepEqual(sent, [{ path: DECLINE, body: { code: "5F67ZFFA" } }]);
});
