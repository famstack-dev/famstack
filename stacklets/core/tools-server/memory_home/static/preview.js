/* preview.js: the preview of a card link on the memory's pages (memory_home/frame.py).

   A card link (`/card/<card id>`) on a memory page shows a small box with
   the card's type, title and description when the pointer rests on it or
   when it gets the focus. The box reads `/memory/preview/<card id>`
   (memory_home/pages.py, `open_preview`), which answers only a card the
   device may read: the description is the card viewer's own
   (memory_ui/card_page.py, `description_of`).

   - Hover: after a short rest, so a pointer that crosses a list opens
     nothing. The box stays open while the pointer is on the link or on the
     box, and closes a moment after it leaves both.
   - Focus: the box opens at once and closes on focus out; Escape closes
     it. The link names the box with aria-describedby while it is open.
   - Touch: a tap opens the card, as any link. No preview, because the
     page changes at once and a box would only flash.

   Optional, like home.js: without the script every link still works. */

(function () {
  "use strict";

  const REST_MS = 400;     // how long the pointer rests on a link before the box opens
  const LEAVE_MS = 200;    // how long the box waits for the pointer to come back
  const CARD_LINK = /^\/card\/([^/?#]+)$/;

  const box = document.createElement("div");
  box.className = "card-preview";
  box.id = "card-preview";
  box.setAttribute("role", "tooltip");
  box.hidden = true;
  document.body.appendChild(box);

  const answers = new Map();    // card id -> the preview's promise, asked once per page
  let link = null;              // the link the box belongs to
  let timer = 0;
  let tapped = false;           // the last input was a finger or a pen, not a mouse or keys

  function cardOf(element) {
    const a = element && element.closest ? element.closest("a[href]") : null;
    if (!a || a.origin !== location.origin) return null;
    const found = CARD_LINK.exec(a.pathname);
    return found ? {a, id: decodeURIComponent(found[1])} : null;
  }

  function preview(id) {
    if (!answers.has(id)) {
      answers.set(id, fetch(`/memory/preview/${encodeURIComponent(id)}`, {credentials: "same-origin"})
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null));
    }
    return answers.get(id);
  }

  function draw(found) {
    box.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = found.title || found.id;
    box.appendChild(title);
    if (found.description) {
      const text = document.createElement("p");
      text.textContent = found.description;
      box.appendChild(text);
    }
    box.dataset.type = found.type || "";
  }

  function place(a) {
    const r = a.getBoundingClientRect();
    const width = Math.min(320, document.documentElement.clientWidth - 16);
    box.style.width = `${width}px`;
    box.style.left = `${Math.max(8, Math.min(r.left, document.documentElement.clientWidth - width - 8)) + scrollX}px`;
    box.style.top = `${r.bottom + 6 + scrollY}px`;
  }

  async function open(found) {
    const answer = await preview(found.id);
    // A preview the device may not read, or a memory that does not answer: no box.
    if (!answer) return;
    // The answer may come after the pointer or the focus moved on: a box
    // only for the link that is still hovered or focused.
    if (!found.a.matches(":hover") && document.activeElement !== found.a) return;
    if (link && link !== found.a) close();
    link = found.a;
    draw(answer);
    place(found.a);
    box.hidden = false;
    link.setAttribute("aria-describedby", box.id);
  }

  function close() {
    clearTimeout(timer);
    box.hidden = true;
    if (link) link.removeAttribute("aria-describedby");
    link = null;
  }

  function later(action, ms) {
    clearTimeout(timer);
    timer = setTimeout(action, ms);
  }

  document.addEventListener("pointerdown", (event) => { tapped = event.pointerType !== "mouse"; }, true);
  document.addEventListener("keydown", () => { tapped = false; }, true);

  document.addEventListener("pointerover", (event) => {
    if (event.pointerType !== "mouse") return;
    if (box.contains(event.target)) { clearTimeout(timer); return; }
    const found = cardOf(event.target);
    if (found) later(() => open(found), found.a === link ? 0 : REST_MS);
  });

  document.addEventListener("pointerout", (event) => {
    if (event.pointerType !== "mouse") return;
    const into = event.relatedTarget;
    const from = cardOf(event.target);
    if (!from && !box.contains(event.target)) return;
    if (into && (box.contains(into) || (link && link.contains(into)))) return;
    later(close, LEAVE_MS);
  });

  document.addEventListener("focusin", (event) => {
    const found = cardOf(event.target);
    // A tap focuses the link on its way to opening the card: no box.
    if (found && !tapped) open(found);
  });

  document.addEventListener("focusout", (event) => {
    if (link && event.target === link) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !box.hidden) close();
  });

})();
