// motion.js: a page's first open, and an element popping in (motion.css).
//
//   import { enter, pop, calm } from "./motion.js";
//   await enter(document.querySelector("main"));   // bloom, pills, widgets rising
//   pop(newPill);                                   // one thing added later
//
// `enter(root)` numbers the root's children and plays the entrance once:
// they rise in one after the other with a blur, the family pills inside pop
// in, widgets catch a sheen, and a bloom of teal and lava light flashes
// behind. Its promise resolves when the entrance is over. From the first
// frame every child is held at opacity 0 until its turn, so a page that
// enters under a moment's fade never shows through fully drawn (moment.js).
//
// Nothing moves unless a page asks: no timer runs while idle, and with
// reduced motion `enter` and `pop` do nothing at all.

// How long the longest part of an entrance lasts (the bloom), after which
// the classes come off again.
const ENTRANCE_MS = 3400;

/** Whether the person asked their system for less motion. */
export function calm() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Play the page's first open on `root`; `stagger` is the gap between children. */
export function enter(root, { stagger = 130, bloom = true } = {}) {
  if (calm()) return Promise.resolve();
  [...root.children].forEach((child, i) => child.style.setProperty("--i", i));
  root.querySelectorAll(".fs-pill").forEach((pill, i) => pill.style.setProperty("--i", i));
  root.style.setProperty("--fs-stagger", `${stagger}ms`);
  restart(root, "fs-entering");
  const glow = bloom ? document.body.appendChild(Object.assign(document.createElement("div"),
    { className: "fs-bloom", ariaHidden: "true" })) : null;
  return new Promise((resolve) => setTimeout(() => {
    root.classList.remove("fs-entering");
    if (glow) glow.remove();
    resolve();
  }, ENTRANCE_MS));
}

/** Let one element pop in, as a family pill added later does. */
export function pop(element) {
  if (calm()) return;
  restart(element, "fs-popped");
}

/** Take a class off and put it back, so its animation plays again. */
function restart(element, name) {
  element.classList.remove(name);
  void element.offsetWidth;
  element.classList.add(name);
}
