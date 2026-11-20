// The two clicks that allow a waiting browser, and Dismiss
// (stacklets/core/tools-server/dashboard/allow-step.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages. The
// explainers are the words the tools server puts on the page
// (devices/waiting.py), with the code filled in.

import assert from "node:assert/strict";
import test from "node:test";

import { ALLOW, AllowStep, DECLINE } from "../../stacklets/core/tools-server/dashboard/allow-step.js";

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
