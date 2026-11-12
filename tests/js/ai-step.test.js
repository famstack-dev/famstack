// When the setup page reads itself once more after the AI's run
// (stacklets/core/tools-server/dashboard/ai-step.js, `readsAgain`).
//
// Run with `node --test tests/js/`. Plain Node, no packages; CI does not run it yet.

import assert from "node:assert/strict";
import test from "node:test";

import { readsAgain } from "../../stacklets/core/tools-server/dashboard/ai-step.js";

const ended = (target) => ({ type: "job.ended", data: { action: "up", target, state: "done" } });

test("the AI's run ended and the AI step still is not done: read again", () => {
  assert.equal(readsAgain(ended("ai"), { step: "ai", status: "current" }), true);
});

test("the AI step already shows done: nothing more to read", () => {
  assert.equal(readsAgain(ended("ai"), { step: "ai", status: "done" }), false);
});

test("another stacklet's run, another step, or a run still going: no", () => {
  assert.equal(readsAgain(ended("photos"), { step: "ai", status: "current" }), false);
  assert.equal(readsAgain(ended("ai"), { step: "services", status: "current" }), false);
  assert.equal(readsAgain({ type: "job.started", data: { action: "up", target: "ai" } },
                          { step: "ai", status: "current" }), false);
});
