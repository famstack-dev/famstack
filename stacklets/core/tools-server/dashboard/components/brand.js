// brand.js: the famstack wordmark as an element (brand.css says how it looks).
//
//   import { wordmark } from "./brand.js";
//   header.prepend(wordmark());
//
// The mark is the product's name, drawn, not a word a page would translate,
// so it is the one text a component writes itself. The moments use it.

/** A new wordmark element: "fam", "stack" in lava, the "a" on two dots. */
export function wordmark() {
  const mark = document.createElement("span");
  mark.className = "fs-brand";
  mark.setAttribute("role", "img");
  mark.setAttribute("aria-label", "famstack");
  mark.innerHTML = 'fam<span>st<span class="fs-brand-a">a</span>ck</span>';
  return mark;
}
