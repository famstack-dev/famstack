// steps.js: the setup checklist, step by step (steps.css says how it looks).
//
//   import { steps } from "./steps.js";
//   widget.append(steps([
//     { id: "install", title: "Install famstack", sub: "Done in the terminal", done: true },
//     { id: "chat", title: "Sign in to the family chat", sub: "On this Mac", body: chatPart },
//     { id: "try", title: "Hand over your first letter", sub: "Comes with Documents" },
//   ]));
//
// One `details` per step: its tick (a check when done, else its number),
// its title and a line under it, and the step's own content (`body`, a text
// or an element) when it is opened. Above them a progress row, a segment
// per step: done ones teal, the next one half lava. The next step, the first
// not done, is open; the others open with a click, as `details` do. Every
// word is the page's. `body` may also be a DocumentFragment, such as the
// HTML a server rendered for the step.

const CHECK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
const CHEVRON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="m6 9 6 6 6-6"/></svg>';

/** The checklist for `items` (`{ id, title, sub, done, body }`), next step open. */
export function steps(items) {
  const next = items.find((item) => !item.done);
  const root = document.createElement("div");
  root.className = "fs-steps-block";

  const progress = document.createElement("div");
  progress.className = "fs-progress";
  progress.setAttribute("aria-hidden", "true");
  for (const item of items) {
    const segment = document.createElement("i");
    segment.dataset.status = item.done ? "done" : item === next ? "next" : "waiting";
    progress.append(segment);
  }

  const list = document.createElement("ol");
  list.className = "fs-steps";
  items.forEach((item, i) => {
    const step = document.createElement("li");
    step.className = "fs-step";
    step.dataset.status = item.done ? "done" : item === next ? "next" : "waiting";
    const details = document.createElement("details");
    details.dataset.step = item.id;
    details.open = item === next;
    const summary = document.createElement("summary");
    const tick = document.createElement("span");
    tick.className = "fs-step-tick";
    if (item.done) tick.innerHTML = CHECK;
    else tick.textContent = String(i + 1);
    const text = document.createElement("span");
    text.className = "fs-step-text";
    const title = document.createElement("b");
    title.className = "fs-step-title";
    title.textContent = item.title;
    text.append(title);
    if (item.sub) {
      const sub = document.createElement("span");
      sub.className = "fs-step-sub";
      sub.textContent = item.sub;
      text.append(sub);
    }
    const chevron = document.createElement("span");
    chevron.className = "fs-step-chevron";
    chevron.innerHTML = CHEVRON;
    summary.append(tick, text, chevron);
    const body = document.createElement("div");
    body.className = "fs-step-body";
    if (item.body) body.append(typeof item.body === "string" ? Object.assign(
      document.createElement("p"), { textContent: item.body }) : item.body);
    details.append(summary, body);
    step.append(details);
    list.append(step);
  });

  root.append(progress, list);
  return root;
}
