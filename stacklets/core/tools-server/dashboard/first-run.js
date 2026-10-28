// first-run.js: the dashboard's first run, while nobody is in the family yet.
//
// The terminal started core and opened this page; the family is set up
// here. Three moments frame it (components/moment.js): "hello." asks your
// name, a greeting by time of day moves on by itself, and once the family
// is in, "Welcome home, Simpsons.". Between them, "Who else is at home?":
// the family's name, the people as pills (you first, a parent, the one who
// looks after the server), the family editor for a tapped pill, someone
// added by first name and Enter, and the family's own web address.
//
// Continue posts the family to `<base>/api/family`, which writes it the
// way `stack family set` does. A refusal comes back with each problem by
// its field (`name`, `domain`, `people.2.relation`, ...) and is shown at
// that field. The browser's language goes with it when the stack speaks it;
// otherwise the language the terminal guessed stays.
//
// The page underneath is the dashboard, hidden while the body has
// `data-first-run` (first-run.css). When the welcome moment starts to
// leave, the attribute comes off and the page enters under the fading
// moment: the hand-over rule in moment.js. This device then remembers the
// person who looks after the server (`setViewer` in family.js), the
// family bar draws the family (`fs-family`, family-bar.js), and every widget
// reads its view again before the welcome, so the dashboard enters with the
// setup as the family now has it.
//
// Every word is the page's (`#fs-page-data`, family_view.py).

import { familyEditor } from "./components/family-editor.js";
import { kin, loadKinWords, pill, setViewer } from "./components/family.js";
import { field } from "./components/field.js";
import { greeting } from "./components/greeting.js";
import { showMoment } from "./components/moment.js";
import { enter, pop } from "./components/motion.js";

// Whether the editor asks how to speak of each person (she, he, just the
// name). It waits for a yes; a no turns it off here and nothing else changes.
const ASK_WORDS = true;
// How long the greeting stays once written, before it moves on by itself.
const GREETING_MS = 1400;
// The languages the stack speaks (stack.family.LANGUAGES).
const SPOKEN = ["en", "de"];
// A domain name, as stack.family.HOSTNAME checks it.
const HOSTNAME = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

const data = JSON.parse(document.getElementById("fs-page-data").textContent);
const W = data.words;

// The family as it is being set up. Each person has a key of their own on
// this page (`id`); the server gives them their username.
const draft = { people: [] };
let editing = null;
let keys = 0;

run();

async function run() {
  await loadKinWords();
  const name = await showMoment({
    line: W.open.line, writeBy: "letter", italic: "all", sub: W.open.sub,
    ask: { label: W.open.ask, placeholder: W.open.ask, note: W.open.note, autocomplete: "given-name" },
    button: W.open.button, leave: false,
  });
  const you = add(name, { looksAfter: true });
  const { salutation } = greeting(W.bar.greeting, { name: you.name });
  await showMoment({ line: `${salutation}, ${you.name}.`, italic: "last", sub: W.greet.sub,
                     advanceAfter: GREETING_MS });
  const screen = setupScreen();
  document.body.append(screen);
  enter(screen);   // at once, under the fading greeting
}

// ── The people ─────────────────────────────────────────────────────────────

/** Add someone by first name: the first two are parents, the rest children. */
function add(typed, { looksAfter = false } = {}) {
  const name = capitalised(typed.trim());
  const person = { id: `p${++keys}`, name, relation: draft.people.length < 2 ? "parent" : "child",
                   words: "", looks_after: looksAfter };
  draft.people.push(person);
  return person;
}

function remove(person) {
  draft.people = draft.people.filter((p) => p !== person);
  editing = null;
}

// ── "Who else is at home?" ─────────────────────────────────────────────────

let parts = null;

function setupScreen() {
  const S = W.setup;
  const screen = h("section", "first-run");
  screen.setAttribute("aria-labelledby", "first-run-heading");
  const heading = h("h1", "first-run-heading", S.heading);
  heading.id = "first-run-heading";

  const family = field({
    label: S.family, name: "family", autocomplete: "family-name",
    help: (value) => (value.trim() ? S.family_called.replace("{called}", called(value)) : S.family_empty),
  });
  const people = h("div", "fs-pills first-run-people");
  people.setAttribute("role", "group");
  people.setAttribute("aria-label", S.people);
  const peopleHelp = h("p", "first-run-problem");
  const editor = h("div", "first-run-editor");

  const adding = h("div", "first-run-add");
  const newName = Object.assign(h("input", "fs-field-input"),
    { placeholder: S.add_placeholder, autocomplete: "off" });
  newName.setAttribute("aria-label", S.add);
  const addButton = h("button", "fs-button", S.add_button);
  Object.assign(addButton, { type: "button" });
  addButton.dataset.kind = "soft";
  const addTyped = () => {
    const typed = newName.value.trim();
    if (!typed || draft.people.some((p) => p.name.toLowerCase() === capitalised(typed).toLowerCase())) return;
    const person = add(typed);
    newName.value = "";
    drawPeople();
    pop(people.querySelector(`[data-person="${person.id}"]`));
  };
  newName.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addTyped();
    }
  });
  addButton.addEventListener("click", addTyped);
  adding.append(newName, addButton);

  const domain = field({
    label: S.domain, optional: S.optional, name: "domain", placeholder: S.domain_placeholder,
    autocomplete: "off", help: domainHelp,
  });
  domain.input.inputMode = "url";
  domain.input.spellcheck = false;

  const problem = h("p", "first-run-problem");
  problem.setAttribute("role", "alert");
  const go = h("button", "fs-button", S.continue);
  Object.assign(go, { type: "button" });
  go.dataset.kind = "go";
  go.addEventListener("click", save);
  const foot = h("div", "first-run-foot");
  foot.append(problem, go);

  const card = h("div", "first-run-card");
  card.append(family, people, peopleHelp, editor, adding, h("p", "first-run-rule", S.rule),
              h("div", "first-run-domain"), foot);
  card.querySelector(".first-run-domain").append(domain);
  screen.append(h("div", "label", S.label), heading, h("p", "lede", S.lede), card);

  parts = { family, people, peopleHelp, editor, domain, problem, go };
  drawPeople();
  return screen;
}

/** The pills, you first, and the editor of the one tapped. */
function drawPeople() {
  const { people, editor } = parts;
  people.replaceChildren(...draft.people.map((person, i) => {
    const label = (i === 0 ? `${W.setup.you} · ` : "") + kin(null, person, draft.people);
    const button = pill({ ...person, tone: i }, { label, you: editing === person.id });
    button.setAttribute("aria-expanded", String(editing === person.id));
    button.addEventListener("click", () => {
      editing = editing === person.id ? null : person.id;
      drawPeople();
    });
    return button;
  }));
  const person = draft.people.find((p) => p.id === editing);
  editor.replaceChildren(...(person ? [familyEditor(person, draft.people, W.editor, {
    askWords: ASK_WORDS,
    onChange: () => drawPills(),
    onRemove: (gone) => { remove(gone); drawPeople(); },
  })] : []));
  // The domain's help names you by username; it changes with your name.
  parts.domain.input.dispatchEvent(new Event("input"));
}

/** Only the pills' labels: the editor stays as it is while it is used. */
function drawPills() {
  parts.people.querySelectorAll(".fs-pill").forEach((button, i) => {
    const person = draft.people[i];
    button.querySelector(".fs-pill-label").textContent =
      (i === 0 ? `${W.setup.you} · ` : "") + kin(null, person, draft.people);
  });
}

function domainHelp(value) {
  const S = W.setup, typed = value.trim().toLowerCase();
  const you = username(draft.people[0]?.name || "");
  if (!typed) return S.domain_empty.replace("{you}", you);
  if (!HOSTNAME.test(typed)) return { text: S.domain_warn, tone: "warn" };
  return S.domain_ok.replace("{you}", you).replace("{domain}", typed);
}

// ── Saving ─────────────────────────────────────────────────────────────────

async function save() {
  const { go, problem } = parts;
  const S = W.setup;
  go.disabled = true;
  go.textContent = S.saving;
  problem.textContent = "";
  let answer = null, body = null;
  try {
    answer = await fetch(`${data.base}/api/family`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(familyFile()),
    });
    body = await answer.json();
  } catch (error) {
    body = { error: String(error) };
  }
  // Saved: the button keeps saying so while the dashboard reads its widgets.
  if (answer && answer.ok) return welcome(body);
  go.disabled = false;
  go.textContent = S.continue;
  if (body && body.problems) return showProblems(body.problems);
  problem.textContent = S.failed.replace("{error}", body?.error || answer?.statusText || "");
}

/** The family file, as `stack family set` reads it. */
function familyFile() {
  const language = (navigator.language || "").slice(0, 2).toLowerCase();
  return {
    name: parts.family.input.value.trim(),
    domain: parts.domain.input.value.trim().toLowerCase(),
    ...(SPOKEN.includes(language) ? { language } : {}),
    people: draft.people.map((p) => ({ name: p.name, relation: p.relation, words: p.words,
                                       looks_after: p.looks_after })),
  };
}

/** Each problem at its field: the name, the address, a person, or the foot. */
function showProblems(problems) {
  const elsewhere = [];
  let first = null;
  for (const { field: at, message } of problems) {
    const person = at.match(/^people\.(\d+)/);
    if (at === "name" || at === "domain") {
      warn(at === "name" ? parts.family : parts.domain, message);
    } else if (person && draft.people[Number(person[1])]) {
      const who = draft.people[Number(person[1])];
      first = first || { who, message };
      elsewhere.push(`${who.name}: ${message}`);
    } else if (at === "people") {
      parts.peopleHelp.textContent = message;
    } else {
      elsewhere.push(message);
    }
  }
  if (first) {
    editing = first.who.id;
    drawPeople();
    const panel = parts.editor.querySelector(".fs-family-editor");
    panel.dataset.tone = "warn";
    panel.querySelector(".fs-family-editor-said").textContent = first.message;
  }
  parts.problem.textContent = elsewhere.join(" ");
}

function warn(fieldElement, message) {
  const line = fieldElement.querySelector(".fs-field-help");
  line.textContent = message;
  line.dataset.tone = "warn";
}

// ── Welcome home ───────────────────────────────────────────────────────────

async function welcome(family) {
  const keeper = family.people.find((p) => p.role === "admin") || family.people[0];
  if (keeper) setViewer(keeper.id);
  document.dispatchEvent(new CustomEvent("fs-family", { detail: family }));
  // The widgets were drawn for the empty family the page was served with:
  // "Setting up" would enter at one step done, with the family's step open
  // and the chat still asking for its server's name. Each reads its view
  // again now, while the dashboard is still hidden, and the welcome waits.
  await Promise.all([...document.querySelectorAll("main.dashboard fs-widget")]
    .map((widget) => widget.load({ quiet: true })));
  await showMoment({ line: W.welcome.line.replace("{called}", family.called), italic: "last",
                     sub: W.welcome.sub, button: W.welcome.button });
  // The hand-over: the dashboard enters under the fading moment, at once.
  document.querySelector(".first-run")?.remove();
  document.body.removeAttribute("data-first-run");
  enter(document.querySelector("main.dashboard"));
}

// ── Small helpers ──────────────────────────────────────────────────────────

/** The household as the page addresses it, as family_view.called does. */
function called(name) {
  const n = name.trim();
  if (data.language === "de") return `Familie ${n}`;
  return n.toLowerCase().endsWith("s") ? n : `${n}s`;
}

/** A first name as a username, as stack.family.username spells it. */
function username(name) {
  const spelled = (name.trim().split(/\s+/)[0] || "").toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .normalize("NFKD");
  return spelled.replace(/[^a-z0-9._-]/g, "");
}

function capitalised(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function h(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}
