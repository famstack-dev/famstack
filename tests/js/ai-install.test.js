// The AI step's "Install oMLX": what the page asks the tools server, in order
// (stacklets/core/tools-server/dashboard/ai-step.js, `installOmlx`).
//
// Run with `node --test tests/js/`. Plain Node, no packages and no DOM: the
// page is a stand-in that records what is asked of it.

import assert from "node:assert/strict";
import test from "node:test";

import { connectServer, goOnWithoutAi, installOmlx } from "../../stacklets/core/tools-server/dashboard/ai-step.js";

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

// ---- "Go on without an AI": the route form's third answer ----------------

function formPage(answer) {
  const calls = [];
  return {
    calls,
    post: async (path, body) => { calls.push(["post", path, body]); return answer; },
    reload: async () => { calls.push(["reload"]); },
    say: (error) => { calls.push(["say", error]); },
  };
}

test("no AI for now answers the route form, then reads the page again", async () => {
  const p = formPage({ ready: true, applied: true });
  await goOnWithoutAi(p);
  assert.deepEqual(p.calls, [["post", "configure/ai", { answers: { route: "none" } }], ["reload"]]);
});

test("a refused answer says why and reads nothing again", async () => {
  const p = formPage({ error: "The stack did not answer." });
  await goOnWithoutAi(p);
  assert.deepEqual(p.calls, [["post", "configure/ai", { answers: { route: "none" } }],
    ["say", "The stack did not answer."]]);
});

// ---- A first run answers the route form ----------------------------------

test("on a first run the install answers the route form, then starts the AI's job", async () => {
  const p = { ...page({ managed: { ready: true } }), form: true };
  await installOmlx(p);
  assert.deepEqual(p.calls, [["post", "configure/ai", { answers: { route: "managed" } }],
    ["start", "ai"], ["reload"]]);
});

test("a refused route says why and starts nothing", async () => {
  const p = { ...page({ managed: { error: "Quit the oMLX app first.", reason: "omlx_app" } }),
    form: true };
  await installOmlx(p);
  assert.deepEqual(p.calls, [["post", "configure/ai", { answers: { route: "managed" } }],
    ["say", "Quit the oMLX app first."]]);
});

// ---- "Use your own AI server": Connect -----------------------------------

function serverPage(answer, form) {
  const calls = [];
  return {
    calls, form,
    post: async (path, body) => { calls.push(["post", path, body]); return answer; },
    done: () => { calls.push(["done"]); },
    models: (ids) => { calls.push(["models", ids]); },
    refused: (reason) => { calls.push(["refused", reason]); },
  };
}

const SERVER = { address: "http://192.168.1.30:8000/v1", key: "sk-simpson", model: "" };
const PICK = { form: { id: "ai.model", schema: { properties: { model: { oneOf: [
  { const: "llama-3-8b", title: "llama-3-8b" }, { const: "mistral-7b", title: "mistral-7b" }] } } } } };

test("on a first run Connect answers the route form with the server", async () => {
  const p = serverPage({ ready: true }, true);
  await connectServer(p, SERVER);
  assert.deepEqual(p.calls, [["post", "configure/ai", { answers: {
    route: "external", address: SERVER.address, api_key: "sk-simpson" } }], ["done"]]);
});

test("a server with several models gets the list from the form, and the pick goes with the answers", async () => {
  const p = serverPage(PICK, true);
  await connectServer(p, SERVER);
  await connectServer({ ...p, post: async (path, body) => { p.calls.push(["post", path, body]); return { ready: true }; } },
    { ...SERVER, model: "mistral-7b" });
  assert.deepEqual(p.calls.slice(1, 2), [["models", ["llama-3-8b", "mistral-7b"]]]);
  assert.equal(p.calls[2][2].answers.model, "mistral-7b");
});

test("a refused server names its reason", async () => {
  const p = serverPage({ error: "Nothing answers at the address.", reason: "unreachable" }, true);
  await connectServer(p, SERVER);
  assert.deepEqual(p.calls.at(-1), ["refused", "unreachable"]);
});

test("a route change connects through the switch", async () => {
  const p = serverPage({ openai_url: SERVER.address }, false);
  await connectServer(p, SERVER);
  assert.deepEqual(p.calls, [["post", "ai/switch", SERVER], ["done"]]);
});

test("the switch's list of models is offered", async () => {
  const p = serverPage({ error: "several", reason: "model", models: ["a", "b"] }, false);
  await connectServer(p, SERVER);
  assert.deepEqual(p.calls.at(-1), ["models", ["a", "b"]]);
});
