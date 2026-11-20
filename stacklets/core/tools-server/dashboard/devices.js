// devices.js: the pages of pairing and joining (devices/pages.py).
//
// "Add Homer's phone" and the setup's phone step: follows the pairing code
// of a `[data-pairing]` panel until a phone opened it or it expired, and
// shows that state. The page of a browser that waits to join
// (`[data-join-state]`): asks the same way, reloads once a device
// allowed it, which then opens as that device, and says so when an admin
// dismissed it. The allow page and "Add a device" on a person's own
// device: send Allow and "Not me" for the code of their `[data-join]`
// part. An admin's allow page and the Mac's Devices page: the allow step
// (`allow-step.js`), where a tap on a person only chooses and Allow is
// the second click, and Dismiss. Devices and "Your devices":
// ask before a device is removed, then post the removal. Every write goes
// out as JSON, the only body the dashboard's writes take (server.py); the
// server decides who may make it (devices/joining.py).

import { wireAllowStep } from "./allow-step.js";

// ---- Following a code ----------------------------------------------------

// How often the panel asks: a phone opens the code within seconds.
const POLL_MS = 2000;

function show(panel, state) {
  for (const part of panel.querySelectorAll("[data-when]")) {
    part.hidden = part.dataset.when !== state;
  }
}

async function follow(panel) {
  const answer = await fetch(panel.dataset.pairing, { headers: { accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (answer && answer.state === "added") {
    panel.querySelector("[data-added]").textContent = answer.line || "";
    show(panel, "added");
    return;
  }
  if (answer && answer.state === "expired") {
    show(panel, "expired");
    return;
  }
  setTimeout(() => follow(panel), POLL_MS);
}

for (const panel of document.querySelectorAll("[data-pairing]")) {
  setTimeout(() => follow(panel), POLL_MS);
}

for (const panel of document.querySelectorAll("[data-join-state]")) {
  setTimeout(() => waitToJoin(panel), POLL_MS);
}

async function waitToJoin(panel) {
  const answer = await fetch(panel.dataset.joinState, { headers: { accept: "application/json" } })
    .then((r) => r.json()).catch(() => null);
  if (answer && answer.state === "allowed") {
    // The gate made this browser a device on that request; the page
    // opens as it now.
    location.reload();
    return;
  }
  if (answer && (answer.state === "expired" || answer.state === "dismissed")) {
    show(panel, answer.state);
    return;
  }
  setTimeout(() => waitToJoin(panel), POLL_MS);
}

// ---- Allowing a browser --------------------------------------------------

async function send(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json()).catch(() => ({ error: "" }));
}

function said(part, answer) {
  const done = part.querySelector("[data-allowed]");
  const refused = part.querySelector("[data-refused]");
  refused.hidden = !answer.error;
  refused.textContent = answer.error || "";
  if (answer.line) {
    done.textContent = answer.line;
    done.hidden = false;
    for (const act of part.querySelectorAll(".setup-act, .devices-as, .allow-step, button")) {
      act.hidden = true;
    }
  }
}

for (const part of document.querySelectorAll("[data-join]")) {
  const panel = part.querySelector(".allow-step");
  if (panel) wireAllowStep(part, { send, said });
  else allowAtOnce(part);
}

// A person's own device: Allow lets the browser in for that person, and
// "Not me" ends nothing; the answer's line says the browser keeps waiting.
function allowAtOnce(part) {
  const code = part.dataset.join;
  part.querySelector("[data-allow]")?.addEventListener("click", async () => {
    said(part, await send("/dashboard/api/devices/allow", { code }));
  });
  part.querySelector("[data-decline]")?.addEventListener("click", async () => {
    said(part, await send("/dashboard/api/devices/decline", { code }));
  });
}

// "Add a device" on a person's own device: allows at once, for that person.
for (const form of document.querySelectorAll("form[data-add-device]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    said(form, await send("/dashboard/api/devices/allow", { code: form.elements.code.value }));
  });
}

// ---- Removing a device ---------------------------------------------------

const ask = document.querySelector("dialog.devices-ask");

for (const button of document.querySelectorAll("[data-remove]")) {
  button.addEventListener("click", () => {
    ask.querySelector("[data-ask]").textContent = button.dataset.ask;
    ask.dataset.device = button.dataset.remove;
    ask.dataset.self = "this" in button.dataset ? "yes" : "";
    ask.showModal();
  });
}

if (ask) {
  ask.querySelector("[data-no]").addEventListener("click", () => ask.close());
  ask.querySelector("[data-yes]").addEventListener("click", async () => {
    // Sent as JSON: the dashboard's writes take no other body (server.py).
    await fetch("/dashboard/api/devices/remove", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: ask.dataset.device }),
    }).catch(() => null);
    // A device that removed itself is unknown from now on: the home
    // address says so on its next page.
    if (ask.dataset.self) location.href = "/dashboard?removed=1";
    else location.reload();
  });
}
