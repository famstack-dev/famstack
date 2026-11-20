// The short profile panel under the family's pills
// (stacklets/core/tools-server/dashboard/components/pill-panel.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages. The answers
// are what `GET /dashboard/api/person/<id>` answers (person_profile/panel.py)
// for the fabricated Simpsons: Homer is the admin, Marge, Lisa and Bart are
// not. The controller runs on stand-ins for the pill and the panel that
// record the focus and the attributes it sets; the wiring runs on a small
// stand-in for the page's document (dom-stand-in.js), as family-bar.js
// wires it.

import assert from "node:assert/strict";
import test from "node:test";

import { PillPanel, panelView, plainClick, wirePanel } from "../../stacklets/core/tools-server/dashboard/components/pill-panel.js";
import { page } from "./dom-stand-in.js";

const CLOSE = "Close";

/** What Marge's phone reads of Homer: the header block and the line why. */
const HOMER_ON_MARGES_PHONE = {
  id: "homer", name: "Homer Simpson", relation: "Partner", admin: true,
  memory: "/memory/person/homer", profile: "/dashboard/person/homer", own: false,
  devices: null, waiting: null,
  said: { admin: "Looks after famstack", memory: "Homer's memory", heading: null, empty: null,
    why: "Homer's devices show on Homer's own phone.", waiting: null, route: null,
    this_device: "This device", close: CLOSE },
};

/** What Homer's phone (an admin) reads of Lisa, with one browser waiting. */
const LISA_ON_HOMERS_PHONE = {
  id: "lisa", name: "Lisa Simpson", relation: "Daughter", admin: false,
  memory: "/memory/person/lisa", profile: "/dashboard/person/lisa", own: false,
  devices: [
    { id: "d1", name: "ipad", label: "iPad", seen: "2026-11-18", stale: false, this: false, when: "seen 2 days ago" },
    { id: "d2", name: "iPhone", seen: "2026-10-03", stale: true, this: false, when: "not seen since 3 October" },
  ],
  waiting: 1,
  said: { admin: null, memory: "Lisa's memory", heading: "Lisa's devices", empty: "Lisa has no device yet.",
    why: null, waiting: "1 device waits to join", route: "Settings and devices",
    this_device: "This device", close: CLOSE },
};

// ── What the panel shows ────────────────────────────────────────────────

test("Marge's phone on Homer's pill: the header block and the line why, no device, no link", () => {
  const view = panelView(HOMER_ON_MARGES_PHONE);
  assert.equal(view.name, "Homer Simpson");
  assert.equal(view.relation, "Partner");
  assert.equal(view.admin, "Looks after famstack");
  assert.deepEqual(view.memory, { label: "Homer's memory", href: "/memory/person/homer" });
  assert.equal(view.devices, null);
  assert.equal(view.why, "Homer's devices show on Homer's own phone.");
  assert.equal(view.waiting, null);
  assert.equal(view.route, null);
});

test("devices the answer does not list are never drawn, whatever its words say", () => {
  const view = panelView({ ...HOMER_ON_MARGES_PHONE,
    said: { ...HOMER_ON_MARGES_PHONE.said, heading: "Homer's devices", route: "Settings and devices" } });
  assert.equal(view.devices, null);
});

test("Homer on Lisa's pill: her devices read-only, the stale one marked, the waiting line and the link", () => {
  const view = panelView(LISA_ON_HOMERS_PHONE);
  assert.equal(view.devices.heading, "Lisa's devices");
  assert.deepEqual(view.devices.rows, [
    { name: "iPad", when: "seen 2 days ago", stale: false, tag: "" },
    { name: "iPhone", when: "not seen since 3 October", stale: true, tag: "" },
  ]);
  assert.equal(view.devices.empty, "");
  assert.deepEqual(view.waiting, { line: "1 device waits to join", href: "/dashboard/person/lisa#waiting" });
  assert.deepEqual(view.route, { label: "Settings and devices", href: "/dashboard/person/lisa" });
  assert.equal(view.why, null);
});

test("no browser waiting: no waiting line, even for an admin", () => {
  const view = panelView({ ...LISA_ON_HOMERS_PHONE, waiting: 0 });
  assert.equal(view.waiting, null);
});

test("a person without a device: the empty line, no rows", () => {
  const view = panelView({ ...LISA_ON_HOMERS_PHONE, devices: [] });
  assert.deepEqual(view.devices.rows, []);
  assert.equal(view.devices.empty, "Lisa has no device yet.");
});

test("one's own device carries the This device tag", () => {
  const own = { ...LISA_ON_HOMERS_PHONE, own: true,
    devices: [{ id: "d1", name: "iPhone", seen: "2026-11-20", stale: false, this: true, when: "seen now" }] };
  assert.equal(panelView(own).devices.rows[0].tag, "This device");
});

// ── Opening and closing ─────────────────────────────────────────────────

/** A stand-in for an element: its attributes and whether it got the focus. */
function element(name) {
  const node = { name, attributes: {}, focused: 0 };
  node.setAttribute = (key, value) => (node.attributes[key] = String(value));
  node.focus = () => (node.focused += 1);
  return node;
}

/** A panel controller whose reads answer `answers` by id, and what it did. */
function controller(answers) {
  const done = { shown: [], hidden: 0, panel: element("panel") };
  const panel = new PillPanel({
    read: async (id) => answers[id] ?? null,
    show: (view, opener) => {
      done.shown.push({ view, opener: opener.name });
      return done.panel;
    },
    hide: () => (done.hidden += 1),
  });
  return { panel, done };
}

test("a click opens the panel, moves the focus into it and marks the pill expanded", async () => {
  const { panel, done } = controller({ homer: HOMER_ON_MARGES_PHONE });
  const pill = element("homer's pill");
  assert.equal(await panel.toggle("homer", pill), true);
  assert.equal(done.shown.length, 1);
  assert.equal(done.shown[0].view.name, "Homer Simpson");
  assert.equal(done.panel.focused, 1);
  assert.equal(pill.attributes["aria-expanded"], "true");
  assert.equal(panel.open, "homer");
});

test("a second click on the same pill closes it and gives the pill the focus back", async () => {
  const { panel, done } = controller({ homer: HOMER_ON_MARGES_PHONE });
  const pill = element("homer's pill");
  await panel.toggle("homer", pill);
  await panel.toggle("homer", pill);
  assert.equal(done.hidden, 1);
  assert.equal(pill.focused, 1);
  assert.equal(pill.attributes["aria-expanded"], "false");
  assert.equal(panel.open, null);
});

test("Escape closes the panel and returns the focus; with nothing open it does nothing", async () => {
  const { panel, done } = controller({ homer: HOMER_ON_MARGES_PHONE });
  assert.equal(panel.key("Escape"), false);
  const pill = element("homer's pill");
  await panel.toggle("homer", pill);
  assert.equal(panel.key("Enter"), false);
  assert.equal(panel.key("Escape"), true);
  assert.equal(done.hidden, 1);
  assert.equal(pill.focused, 1);
});

test("× closes the panel as Escape does", async () => {
  const { panel, done } = controller({ homer: HOMER_ON_MARGES_PHONE });
  const pill = element("homer's pill");
  await panel.toggle("homer", pill);
  panel.close();
  assert.equal(done.hidden, 1);
  assert.equal(pill.focused, 1);
});

test("another pill moves the panel to that person, and its pill is no longer expanded", async () => {
  const { panel, done } = controller({ homer: HOMER_ON_MARGES_PHONE, lisa: LISA_ON_HOMERS_PHONE });
  const homer = element("homer's pill");
  const lisa = element("lisa's pill");
  await panel.toggle("homer", homer);
  await panel.toggle("lisa", lisa);
  assert.deepEqual(done.shown.map((s) => s.opener), ["homer's pill", "lisa's pill"]);
  assert.equal(homer.attributes["aria-expanded"], "false");
  assert.equal(lisa.attributes["aria-expanded"], "true");
  assert.equal(panel.open, "lisa");
});

test("an answer that did not come opens nothing, so the link opens the page", async () => {
  const { panel, done } = controller({});
  assert.equal(await panel.toggle("homer", element("homer's pill")), false);
  assert.equal(done.shown.length, 0);
  assert.equal(panel.open, null);
});

test("a second click on the same pill while its profile is read closes it: nothing opens", async () => {
  const later = deferred();
  const { panel, done } = controller({});
  panel.read = () => later.promise;
  const pill = element("homer's pill");
  const first = panel.toggle("homer", pill);
  const second = panel.toggle("homer", pill);
  later.resolve(HOMER_ON_MARGES_PHONE);
  await Promise.all([first, second]);
  assert.equal(done.shown.length, 0);
  assert.equal(panel.open, null);
});

test("two pills clicked quickly open only the one clicked last, whichever answer comes first", async () => {
  const homers = deferred();
  const lisas = deferred();
  const { panel, done } = controller({});
  panel.read = (id) => (id === "homer" ? homers.promise : lisas.promise);
  const first = panel.toggle("homer", element("homer's pill"));
  const second = panel.toggle("lisa", element("lisa's pill"));
  lisas.resolve(LISA_ON_HOMERS_PHONE);
  await second;
  homers.resolve(HOMER_ON_MARGES_PHONE);
  await first;
  assert.deepEqual(done.shown.map((s) => s.opener), ["lisa's pill"]);
  assert.equal(panel.open, "lisa");
});

/** A promise and the function that resolves it, for an answer that comes later. */
function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}

// ── The panel wired to a page ───────────────────────────────────────────

/** A page with the pill row of Homer and Lisa, wired as family-bar.js wires
 * it, and the pages the links opened instead. */
function wiredPage(answers = { homer: HOMER_ON_MARGES_PHONE, lisa: LISA_ON_HOMERS_PHONE }) {
  const doc = page();
  const bar = doc.createElement("nav");
  bar.className = "family-bar";
  doc.body.append(bar);
  const opened = [];
  const wired = wirePanel(doc, { read: async (id) => answers[id] ?? null, navigate: (href) => opened.push(href) });
  const pills = {};
  for (const id of ["homer", "lisa"]) {
    pills[id] = doc.createElement("a");
    pills[id].href = `/dashboard/person/${id}`;
    bar.append(wired.attach(pills[id], id, bar));
  }
  const panels = () => doc.body.all("fs-pill-panel");
  return { doc, bar, pills, panels, opened };
}

/** Let the click's read and its drawing run. */
const settle = () => new Promise((done) => setImmediate(done));

/** This browser's storage, with Bart as the person it greets. */
function greetingBart() {
  const kept = new Map([["fs-viewer", "bart"]]);
  globalThis.localStorage = {
    getItem: (key) => kept.get(key) ?? null,
    setItem: (key, value) => kept.set(key, value),
    removeItem: (key) => kept.delete(key),
  };
  return kept;
}

test("a click on a pill opens its panel after the pill row, with the focus in it", async () => {
  const { doc, bar, pills, panels } = wiredPage();
  const click = pills.homer.click();
  await settle();
  assert.equal(click.defaultPrevented, true);
  assert.equal(panels().length, 1);
  assert.equal(doc.body.children[doc.body.children.indexOf(bar) + 1], panels()[0]);
  assert.equal(doc.activeElement, panels()[0]);
  assert.equal(pills.homer.getAttribute("aria-expanded"), "true");
});

test("a pill click leaves the person this browser greets unchanged", async () => {
  const kept = greetingBart();
  try {
    const { pills } = wiredPage();
    pills.lisa.click();
    await settle();
    pills.lisa.click();
    await settle();
    pills.homer.click();
    await settle();
    assert.equal(kept.get("fs-viewer"), "bart");
    assert.equal(kept.size, 1);
  } finally {
    delete globalThis.localStorage;
  }
});

test("Escape closes the panel and gives the focus back to the pill", async () => {
  const { doc, pills, panels } = wiredPage();
  pills.lisa.click();
  await settle();
  doc.press("Escape");
  assert.equal(panels().length, 0);
  assert.equal(doc.activeElement, pills.lisa);
  assert.equal(pills.lisa.getAttribute("aria-expanded"), "false");
});

test("× closes the panel and gives the focus back to the pill", async () => {
  const { doc, pills, panels } = wiredPage();
  pills.lisa.click();
  await settle();
  const close = panels()[0].all("fs-pill-panel-close");
  assert.equal(close.length, 1);
  assert.equal(close[0].getAttribute("aria-label"), CLOSE);
  close[0].click();
  assert.equal(panels().length, 0);
  assert.equal(doc.activeElement, pills.lisa);
});

test("a click with a modifier key is left to the browser, which opens the page", async () => {
  const { pills, panels } = wiredPage();
  const click = pills.homer.click({ metaKey: true });
  await settle();
  assert.equal(click.defaultPrevented, false);
  assert.equal(panels().length, 0);
});

test("a profile that cannot be read opens the pill's page instead", async () => {
  const { pills, panels, opened } = wiredPage({});
  pills.homer.click();
  await settle();
  assert.equal(panels().length, 0);
  assert.deepEqual(opened, ["/dashboard/person/homer"]);
});

// ── Which clicks open the panel ─────────────────────────────────────────

test("a plain click opens the panel; a new tab, a middle click or a modified click opens the page", () => {
  const click = (extra = {}) => ({ button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...extra });
  assert.equal(plainClick(click()), true);
  assert.equal(plainClick(click({ metaKey: true })), false);
  assert.equal(plainClick(click({ ctrlKey: true })), false);
  assert.equal(plainClick(click({ shiftKey: true })), false);
  assert.equal(plainClick(click({ button: 1 })), false);
});
