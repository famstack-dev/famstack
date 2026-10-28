// moment.js: a full-screen moment, such as "hello." on the first run
// (moment.css says how it looks).
//
//   import { showMoment } from "./moment.js";
//   import { enter } from "./motion.js";
//
//   const name = await showMoment({
//     line: "hello.", writeBy: "letter", italic: "all",
//     sub: "Let's set up your family's own server. What's your name?",
//     ask: { label: "Your first name", placeholder: "Your first name",
//            note: "You'll be the one who looks after it." },
//     button: "Let's begin", leave: false,
//   });
//   await showMoment({ line: `Good morning, ${name}.`, italic: "last",
//                      sub: "Nice to meet you.", advanceAfter: 1400 });
//   enter(page);                       // at once: see the hand-over rule
//
// `showMoment(options)` shows the moment and returns a promise of the
// typed answer ("" when it asks nothing), resolved when the moment is
// answered (its button, or Enter in its field) or, with `advanceAfter`,
// that many milliseconds after its line has been written.
//
//   line          the big line, written in by letter or by word (`writeBy`)
//   italic        "none", "last" (the last word, a name) or "all"
//   sub           the sentence under it
//   ask           { label, placeholder, note }: a question answered on an
//                 underline; the button waits until there is an answer
//   button        its label; none means the moment leaves by `advanceAfter`
//   leave         false keeps the screen up when answered, so the next
//                 moment writes in where this one was, wordmark and all
//
// Every word is the page's; the wordmark is the only thing the moment
// draws itself.
//
// The hand-over rule: the next page's entrance starts with the moment's
// fade, never after it. The promise resolves the instant the moment starts
// to leave, so `await showMoment(...)` followed by `enter(page)` starts
// the entrance under the fading moment, every element of the page held at
// opacity 0 until its turn. Started after the fade instead, the page shows
// through fully drawn, vanishes into its entrance, and appears twice. That
// was a real bug in the prototype.
//
// With reduced motion the moment stands still, words and button there,
// and leaves at once. Nothing runs while no moment is shown.

import { wordmark } from "./brand.js";
import { field } from "./field.js";
import { calm } from "./motion.js";

// Pacing, as the prototype tuned it. A letter or a word every `step` ms,
// the first after `lead` ms, or after `chained` ms when the screen is
// already up from the moment before.
const PACE = { letter: { step: 170, lead: 1100 }, word: { step: 280, lead: 700 } };
const CHAINED_LEAD = 250;
// After the last unit: the sentence comes in, then the button.
const SETTLE_MS = 900;
const BUTTON_AFTER_MS = 700;
// The question going up and away, and the whole moment leaving.
const ANSWERED_MS = 650;
const LEAVE_MS = 1100;

let screen = null;   // the moment on screen, kept up between chained moments

/** Show a moment; resolves with the answer as it starts to leave. */
export function showMoment({ line, writeBy = "word", italic = "none", sub = "", ask = null,
                             button = null, advanceAfter = null, leave = true } = {}) {
  const chained = Boolean(screen);
  screen = screen || build();
  const root = screen;
  root.classList.remove("fs-answered", "fs-leaving");
  root.toggleAttribute("data-chained", chained);
  root.dataset.write = writeBy;

  const pace = PACE[writeBy] || PACE.word;
  const lead = chained ? CHAINED_LEAD : pace.lead;
  const units = writeBy === "letter" ? [...line] : line.split(" ");
  const written = lead + units.length * pace.step + SETTLE_MS;
  root.style.setProperty("--step", `${pace.step}ms`);
  root.style.setProperty("--lead", `${lead}ms`);
  root.style.setProperty("--sub-at", `${written}ms`);
  root.style.setProperty("--go-at", `${written + BUTTON_AFTER_MS}ms`);

  const heading = h("h1", "fs-moment-line");
  heading.id = "fs-moment-line";
  heading.dataset.italic = italic;
  units.forEach((unit, i) => {
    const part = h("span");
    part.style.setProperty("--i", i);
    if (italic === "last" && i === units.length - 1) part.append(h("em", "", unit));
    else part.textContent = unit === " " ? "\u00a0" : unit;   // a space would collapse to nothing
    if (i && writeBy !== "letter") heading.append(" ");
    heading.append(part);
  });

  const parts = [heading];
  if (sub) parts.push(h("p", "fs-moment-sub", sub));
  let answer = null;
  if (ask) {
    answer = field({ label: ask.label, placeholder: ask.placeholder || "", variant: "underline",
                     hideLabel: true, autocomplete: ask.autocomplete || "" });
    const form = h("form", "fs-moment-ask");
    form.append(answer);
    parts.push(form);
    if (ask.note) parts.push(h("p", "fs-moment-note", ask.note));
  }
  let go = null;
  if (button) {
    go = h("button", "fs-button fs-moment-go", button);
    go.type = "button";
    go.dataset.kind = "go";
    go.disabled = Boolean(answer);
    parts.push(go);
  }
  root.querySelectorAll(":scope > :not(.fs-brand)").forEach((old) => old.remove());
  root.append(...parts);
  if (!root.isConnected) document.body.append(root);

  return new Promise((resolve) => {
    let answered = false;
    const done = () => {
      const value = answer ? answer.input.value.trim() : "";
      if (answered || (answer && !value)) return;
      answered = true;
      if (leave) {
        away(root);
        resolve(value);
      } else {
        stepAside(root).then(() => resolve(value));
      }
    };
    if (answer) {
      answer.input.addEventListener("input", () => { if (go) go.disabled = !answer.input.value.trim(); });
      answer.closest("form").addEventListener("submit", (event) => { event.preventDefault(); done(); });
    }
    if (go) go.addEventListener("click", done);
    const ready = calm() ? 0 : written + BUTTON_AFTER_MS;
    const focus = answer ? answer.input : go;
    if (focus) setTimeout(() => focus.focus({ preventScroll: true }), ready);
    if (advanceAfter !== null) setTimeout(done, calm() ? advanceAfter : written + advanceAfter);
  });
}

/** The screen a moment is shown on: the wordmark, and room for the rest. */
function build() {
  const root = h("div", "fs-moment");
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-labelledby", "fs-moment-line");
  root.append(wordmark());
  return root;
}

/** The answered question goes up and away; the screen stays for the next. */
function stepAside(root) {
  if (calm()) return Promise.resolve();
  root.classList.add("fs-answered");
  return new Promise((resolve) => setTimeout(resolve, ANSWERED_MS));
}

/** The moment leaves: it fades out over the page now entering under it. */
function away(root) {
  screen = null;
  if (calm()) {
    root.remove();
    return;
  }
  root.classList.add("fs-leaving");
  setTimeout(() => root.remove(), LEAVE_MS);
}

function h(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}
