/* home.js: the memory pages' small script (memory_home/frame.py).

   Two things, both optional: without this script every link still works
   and the menu stays open on a phone.

   - A page that opens moves the focus to its heading, so a screen reader
     starts reading the new page and its title, not the header again.
   - On a phone the menu folds away behind the header's menu button; the
     button says whether the menu is open (aria-expanded). */

(function () {
  "use strict";

  const heading = document.getElementById("page-title");
  if (heading) heading.focus({preventScroll: true});

  const button = document.querySelector(".menu-button");
  if (!button) return;
  // The stylesheet folds the menu only once this class says a button can open it.
  document.documentElement.classList.add("has-menu-button");
  button.hidden = false;
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("menu-open", open);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("menu-open")) {
      button.click();
      button.focus();
    }
  });
})();
