/* search_layer.js: the memory's search as a layer over the page (memory_home/frame.py).

   The header's search shows its results over the page the person is on,
   so that after a look at them they are back where they were:

   - Typing searches after a short pause, Enter at once. The results come
     from `/memory/search/results?q=` (memory_home/pages.py,
     `open_search_results`): the search page's sheet without the page
     around it, read for the device's audience. The layer takes the place
     of the page's sheet in the content section; the header and the menu
     stay.
   - While a search runs the field shows a loading mark, and the layer says
     it is busy (aria-busy). A newer search cancels the older one, so a
     slow answer never replaces a newer one.
   - A clear button (×) shows while the field holds words, and on a phone
     while the field is slid out (search_toggle.js). Clearing the
     field (the button, deleting the words) or Escape closes the layer: the
     page underneath comes back as it was, at the same scroll position.
     Escape closes the layer wherever the focus is.
   - Enter moves the focus into the layer, to its heading; typing keeps it
     in the field, so the next letter still lands there. Closing the layer
     gives the focus back to the field.
   - A result opens as any link. The words are kept in the page's history
     entry (history.state), so Back returns to the page with the layer open
     again.

   The page's sheet is hidden while the layer is open, not removed, so its
   state (an open preview aside) stays. The card links' preview
   (preview.js) listens on the whole document, so it works in the layer.

   Optional, like home.js: without this script the search sends its words
   to `/memory/search?q=` as a page of its own. */

(function () {
  "use strict";

  const PAUSE_MS = 350;               // how long typing rests before the search runs
  const RESULTS = "/memory/search/results";
  const STATE_KEY = "memorySearch";   // the words kept in the page's history entry

  const form = document.querySelector("form.search");
  const field = form && form.querySelector("input[name=q]");
  const main = document.getElementById("main");
  const page = main && main.querySelector(":scope > .sheet");
  if (!field || !page) return;
  const clear = form.querySelector(".search-clear");
  const busy = form.querySelector(".search-busy");

  const layer = document.createElement("div");
  layer.className = "search-layer";
  layer.hidden = true;
  main.appendChild(layer);

  let timer = 0;
  let running = null;     // the AbortController of the search that runs
  let shown = "";         // the words the layer shows the results of
  let scrolled = 0;       // the page's scroll position when the layer opened

  // ── Opening and closing ──────────────────────────────────────────────

  /* Run the search for the field's words and show its results in the
     layer. `focus` moves the focus to the layer's heading once they are
     there (Enter, not typing). Empty words close the layer instead. */
  function search(focus) {
    clearTimeout(timer);
    const words = field.value.trim();
    if (!words) return close(false);
    if (words === shown && !layer.hidden && !running) {
      if (focus) focusLayer();
      return;
    }
    if (running) running.abort();
    const controller = new AbortController();
    running = controller;
    showBusy(true);
    fetch(`${RESULTS}?q=${encodeURIComponent(words)}`, {credentials: "same-origin", signal: controller.signal})
      .then((answer) => answer.text())
      .then((html) => {
        if (running !== controller) return;
        open();
        layer.innerHTML = html;
        shown = words;
        remember(words);
        if (focus) focusLayer();
      })
      .catch(() => {})                 // aborted by a newer search, or offline: the page stays as it was
      .finally(() => {
        if (running !== controller) return;
        running = null;
        showBusy(false);
      });
  }

  /* Show the layer in place of the page's sheet, at the top. */
  function open() {
    if (!layer.hidden) return;
    scrolled = window.scrollY;
    page.hidden = true;
    layer.hidden = false;
    window.scrollTo(0, 0);
  }

  /* Close the layer: the page's sheet comes back at the scroll position it
     had, and the focus goes back to the field. `empty` clears the field
     too (Escape). */
  function close(empty) {
    clearTimeout(timer);
    if (running) running.abort();
    running = null;
    showBusy(false);
    if (empty) field.value = "";
    showClear();
    remember("");
    if (layer.hidden) return;
    layer.hidden = true;
    layer.replaceChildren();
    shown = "";
    page.hidden = false;
    window.scrollTo(0, scrolled);
    field.focus({preventScroll: true});
  }

  function focusLayer() {
    const heading = layer.querySelector("h1[tabindex]");
    if (heading) heading.focus({preventScroll: true});
  }

  // ── The field's marks ────────────────────────────────────────────────

  /* The × shows while the field holds words, and on a phone while the
     field is slid out (`out`, search_toggle.js), because a phone has no
     Escape key and the × is how the empty field slides back. */
  function showClear() {
    if (clear) clear.hidden = field.value === "" && !form.classList.contains("out");
  }

  /* The loading mark: the field's class draws it, the status element
     names it for a screen reader, and the layer says it is busy. */
  function showBusy(on) {
    form.classList.toggle("busy", on);
    if (busy) busy.textContent = on ? busy.dataset.label : "";
    layer.setAttribute("aria-busy", String(on));
  }

  // ── History ──────────────────────────────────────────────────────────

  /* Keep the layer's words in the page's history entry, so Back from a
     result opens the layer again; "" forgets them. The address stays. */
  function remember(words) {
    const state = Object.assign({}, history.state);
    if (words) state[STATE_KEY] = words;
    else delete state[STATE_KEY];
    history.replaceState(state, "");
  }

  // ── Events ───────────────────────────────────────────────────────────

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    search(true);
  });
  field.addEventListener("input", () => {
    showClear();
    clearTimeout(timer);
    if (field.value.trim() === "") close(false);
    else timer = setTimeout(() => search(false), PAUSE_MS);
  });
  if (clear) {
    clear.addEventListener("click", () => close(true));
  }
  // Capture phase, so this runs before the preview's own Escape: an open
  // preview box takes the first Escape, the layer the next.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (document.querySelector(".card-preview:not([hidden])")) return;
    // An open layer closes wherever the focus is (a menu link, the page);
    // with the layer closed, Escape only clears the field it was pressed in.
    const inField = form.contains(event.target);
    if (!layer.hidden || (inField && field.value)) {
      event.preventDefault();      // a search field would clear itself; this clears it the same way
      close(true);
    }
  }, true);

  // Back from a result: a page the browser kept still shows the layer;
  // a page it loads again opens the layer for the words it remembers.
  const kept = history.state && history.state[STATE_KEY];
  if (kept) {
    field.value = kept;
    showClear();
    search(false);
  }
})();
