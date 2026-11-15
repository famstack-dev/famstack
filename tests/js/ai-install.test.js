// The AI step's "Install oMLX": what the page asks the tools server, in order
// (stacklets/core/tools-server/dashboard/ai-step.js, `installOmlx`).
//
// Run with `node --test tests/js/`. Plain Node, no packages and no DOM: the
// page is a stand-in that records what is asked of it.

import assert from "node:assert/strict";
import test from "node:test";

import { installOmlx } from "../../stacklets/core/tools-server/dashboard/ai-step.js";

/** A page that records each call; `answers` are the tools server's replies. */
function page({ managed = { provider: "managed" }, start = { id: "a5c8" } } = {}) {
  const calls = [];
  return {
    calls,
    post: async (path, body) => { calls.push(["post", path, body]); return managed; },
    start: async (target) => {
      calls.push(["start", target]);
      if (start instanceof Error) throw start;
      return start;
    },
    reload: async () => { calls.push(["reload"]); },
    say: (error) => { calls.push(["say", error]); },
  };
}

test("the install writes the route, then starts the AI's job, then reads the page again", async () => {
  const p = page();
  await installOmlx(p);
  assert.deepEqual(p.calls, [["post", "ai/managed", {}], ["start", "ai"], ["reload"]]);
});

test("a refused write says why and starts nothing", async () => {
  const p = page({ managed: { error: "ai is already busy" } });
  await installOmlx(p);
  assert.deepEqual(p.calls, [["post", "ai/managed", {}], ["say", "ai is already busy"]]);
});

test("a start that fails after the write leaves a page that offers the start again", async () => {
  // The route is written, so the page read again shows the AI's tile with
  // its Start; the install's button would only write the route again.
  const p = page({ start: new Error("503") });
  await installOmlx(p);
  assert.deepEqual(p.calls, [["post", "ai/managed", {}], ["start", "ai"], ["reload"]]);
});
