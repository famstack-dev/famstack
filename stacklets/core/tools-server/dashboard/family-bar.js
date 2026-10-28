// family-bar.js: the family at the top of every dashboard page, and the
// greeting by time of day for whoever this device remembers.
//
// The page is served with the pills already there, each with the plain
// word for the person's relation, and greets the household ("Welcome home,
// Simpsons."): that is what a browser without JavaScript shows. This script
// reads the family (`GET <base>/api/family`) and draws the pills again,
// each labelled as whoever this device remembers would say it ("sister",
// "mom", "you"), and greets that person by time of day ("Good morning,
// Bart."), or the household when nobody is remembered. Tapping a pill
// remembers that person on this device; tapping it again forgets them.
// That is personalisation, not sign-in (family.js).
//
// After the first run the family arrives as an `fs-family` event on the
// document (first-run.js), and the bar draws it without asking again.
// Every word is the page's (`#fs-page-data`, family_view.py).

import { forgetViewer, kin, loadKinWords, pill, setViewer, viewer } from "./components/family.js";
import { greeting } from "./components/greeting.js";

const data = JSON.parse(document.getElementById("fs-page-data").textContent);
const words = data.words.bar;

start();

async function start() {
  await loadKinWords();
  document.addEventListener("fs-family", (event) => draw(event.detail));
  if (document.body.hasAttribute("data-first-run")) return;   // first-run.js sends the family
  try {
    const answer = await fetch(`${data.base}/api/family`, { headers: { accept: "application/json" } });
    if (answer.ok) draw(await answer.json());
  } catch {
    // The stack did not answer: the served pills and greeting stay.
  }
}

/** Draw the pills for `family` (as the API answers it) and greet. */
function draw(family) {
  const people = family.people.map((p, i) => ({ ...p, name: firstName(p.name), tone: i }));
  const me = people.some((p) => p.id === viewer()) ? viewer() : null;
  const bar = barElement(family);
  bar.replaceChildren(...people.map((person) => {
    const button = pill(person, { label: kin(me, person, people), you: person.id === me });
    button.addEventListener("click", () => {
      if (person.id === viewer()) forgetViewer();
      else setViewer(person.id);
      draw(family);
    });
    return button;
  }));
  if (!me) bar.append(Object.assign(document.createElement("span"),
    { className: "fs-pills-hint", textContent: words.hint }));
  greet(people.find((p) => p.id === me)?.name || "", family.called);
}

/** The page's pill row, made when the page was served with nobody in it. */
function barElement() {
  let bar = document.querySelector(".family-bar");
  if (!bar) {
    bar = document.createElement("nav");
    bar.className = "fs-pills family-bar";
    bar.setAttribute("aria-label", words.label);
    document.querySelector("main.dashboard").prepend(bar);
  }
  return bar;
}

/** "Good morning, Bart.", or the household by time of day. */
function greet(name, household) {
  const heading = document.querySelector(".greeting");
  if (!heading) return;
  const { salutation, name: who } = greeting(words.greeting, { name, family: household });
  if (!who) {
    heading.textContent = `${salutation}.`;
    return;
  }
  const em = document.createElement("em");
  em.textContent = `${who}.`;
  heading.replaceChildren(`${salutation}, `, em);
}

function firstName(name) {
  return name.trim().split(/\s+/)[0] || name;
}
