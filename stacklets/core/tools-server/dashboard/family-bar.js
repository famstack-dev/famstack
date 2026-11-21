// family-bar.js: the family at the top of every dashboard page, and the
// greeting by time of day for whoever this device remembers.
//
// The page is served with the pills already there, each with the plain
// word for the person's relation, and greets whoever the server knows is
// looking ("Welcome home, Homer." on Homer's paired phone, "Welcome home,
// Simpsons." elsewhere): that is what a browser without JavaScript shows.
// This script reads the family (`GET <base>/api/family`) and draws the
// pills again, each labelled as whoever looks would say it ("sister",
// "mom", "you"), and greets that person by time of day ("Good morning,
// Bart."), or the household when nobody looks.
//
// Who looks comes from the page (`data.device`, from the request's paired
// device, family_view.py), the one source:
//
//   { person: "homer" }   Homer's phone: Homer looks
//   { shared: true }      a shared device: the household
//   null                  the Mac's address: whoever this browser remembers
//
// On the Mac's address the person is picked with "This is me" on their
// profile page (profile.js) and kept in this browser (family.js). That is
// personalisation, not sign-in. On the home address a person a browser
// remembered before it was paired no longer counts, so a phone paired as
// Homer never greets Bart.
//
// Each pill links the person's profile page. A plain click opens the short
// panel of that profile under the pills instead (components/pill-panel.js);
// the name top right does the same for one's own profile, under the top
// bar. Neither changes who looks. On the Mac, the person picked gets that
// name too, which the server cannot know.
//
// After the first run the family arrives as an `fs-family` event on the
// document (first-run.js), and the bar draws it without asking again.
// Every word is the page's (`#fs-page-data`, family_view.py).

import { kin, loadKinWords, pill, viewer } from "./components/family.js";
import { greeting } from "./components/greeting.js";
import { wirePanel } from "./components/pill-panel.js";

const data = JSON.parse(document.getElementById("fs-page-data").textContent);
const words = data.words.bar;
// The page's device, or null on the Mac's address, where the picker works.
const device = data.device || null;
// The one panel of the page: under the pills, or under the top bar.
const panel = wirePanel(document, { read: readProfile });

start();

async function start() {
  await loadKinWords();
  document.addEventListener("fs-family", (event) => draw(event.detail));
  const own = document.querySelector(".topbar .who-link");
  if (own && device?.person) panel.attach(own, device.person, document.querySelector(".topbar"));
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
  const looking = lookingId();
  const me = people.some((p) => p.id === looking) ? looking : null;
  const bar = barElement(family);
  panel.close();
  bar.replaceChildren(...people.map((person) => panel.attach(
    pill(person, { label: kin(me, person, people), you: person.id === me,
      href: `${data.base}/person/${encodeURIComponent(person.id)}` }),
    person.id, bar)));
  if (!device && !me) bar.append(Object.assign(document.createElement("span"),
    { className: "fs-pills-hint", textContent: words.hint }));
  if (!device) macName(family.people.find((p) => p.id === me));
  greet(people.find((p) => p.id === me)?.name || "", family.called);
}

// ---- The panel ---------------------------------------------------------------

/** The profile's JSON as this device may see it, or null. */
async function readProfile(id) {
  const answer = await fetch(`${data.base}/api/person/${encodeURIComponent(id)}`,
    { headers: { accept: "application/json" } });
  return answer.ok ? answer.json() : null;
}

/** On the Mac's address, the name top right is the person picked in this
 * browser, linking their profile; with nobody picked there is none. */
function macName(person) {
  document.querySelector(".topbar .who-link")?.remove();
  if (!person) return;
  const link = Object.assign(document.createElement("a"), { className: "who-link", href: "/dashboard/me" });
  link.append(Object.assign(document.createElement("span"), { className: "who", textContent: person.name }));
  const top = document.querySelector(".topbar");
  top?.append(panel.attach(link, person.id, top));
}

// ---- Who looks and the greeting ---------------------------------------------

/** Who looks: the paired device's person, nobody on a shared device, and
 * whoever this browser remembers on the Mac's address. */
function lookingId() {
  if (device) return device.person || null;
  return viewer();
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
