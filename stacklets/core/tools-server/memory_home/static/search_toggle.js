/* search_toggle.js: the phone's search button (memory_home/frame.py).

   On a phone (600 px and narrower) the header is one row: the mark, a
   search button and the menu button. The search field waits out of sight
   and slides out over the space between the mark and the menu button:

   - The search button slides the field out and puts the focus in it, so
     the keyboard opens at once.
   - × with the search layer open clears the field and closes the layer,
     as on a computer (search_layer.js); the field stays out. × with no
     layer open slides the field back. While the field is out, × shows even
     when it is empty, because a phone has no Escape key.
   - Escape with no layer open slides the field back; with the layer open
     it closes the layer first (search_layer.js) and the field stays out.
   - The field that slides back is emptied, wherever its words came from,
     so a field out of sight never holds a search.
   - A field that holds words when the page opens (the search page, Back
     from a result) is out from the start, so the words can be seen.

   The stylesheet draws all of it from three classes: `has-search-button`
   on the root says the button works, `search-motion` on the root that the
   page has drawn and the field may slide, `out` on the form that the field
   is out. On a computer the stylesheet hides the button and shows the field,
   so these classes change nothing there.

   Optional, like home.js: without this script the button stays hidden and
   the field stays on its own line under the mark. Loaded after
   search_layer.js, which creates the layer and restores kept words. */

(function () {
  "use strict";

  const button = document.querySelector(".search-open");
  const form = document.querySelector("form.search");
  const field = form && form.querySelector("input[name=q]");
  if (!button || !field) return;
  const clear = form.querySelector(".search-clear");

  document.documentElement.classList.add("has-search-button");
  button.hidden = false;

  /* Whether the phone's layout is in use: the stylesheet shows the button
     only there. */
  function onPhone() {
    return getComputedStyle(button).display !== "none";
  }

  /* Whether the search's layer shows results (search_layer.js). */
  function layerOpen() {
    const layer = document.querySelector(".search-layer");
    return Boolean(layer && !layer.hidden);
  }

  /* The × shows while the field is out, also when it is empty; while it
     is in, only with words (search_layer.js, `showClear`). */
  function showClear() {
    if (clear) clear.hidden = field.value === "" && !form.classList.contains("out");
  }

  function slideOut(focus) {
    form.classList.add("out");
    showClear();
    button.setAttribute("aria-expanded", "true");
    // The form's visibility turns at once on the way out, so the field can
    // take the focus in the same tap and the phone opens its keyboard.
    if (focus) field.focus();
  }

  /* Slide the field back and empty it: a field out of sight holds no
     words, so the next tap opens an empty field, not an old search. */
  function slideBack() {
    form.classList.remove("out");
    field.value = "";
    showClear();
    button.setAttribute("aria-expanded", "false");
    button.focus({preventScroll: true});
  }

  button.addEventListener("click", () => slideOut(true));

  // Capture on the form, so this runs before search_layer.js closes the
  // layer on the same click and the layer's state is still the one the
  // person saw.
  if (clear) {
    form.addEventListener("click", (event) => {
      if (event.target === clear && form.classList.contains("out") && !layerOpen()) slideBack();
    }, true);
  }

  // Capture on the window runs before search_layer.js's capture on the
  // document, so the layer is still open when it is the layer this Escape
  // closes. An open preview box takes the Escape for itself (preview.js).
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !form.classList.contains("out") || !onPhone()) return;
    if (layerOpen() || document.querySelector(".card-preview:not([hidden])")) return;
    slideBack();
  }, true);

  if (field.value) slideOut(false);
  // The slide starts to move only after the first frame, so the page
  // opens with the field where it belongs, not sliding there (home.css).
  requestAnimationFrame(() => requestAnimationFrame(
    () => document.documentElement.classList.add("search-motion")));
})();
