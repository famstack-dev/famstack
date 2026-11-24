// The topic page's two chips and the folded rows of its main list.
// A chip opens its list and closes the other; a second tap closes it.
// "All n ›" shows the rows folded behind it. Without this script the lists stay closed.
for (const chip of document.querySelectorAll(".topic-chip")) {
  chip.addEventListener("click", () => {
    const open = chip.getAttribute("aria-expanded") !== "true";
    for (const other of document.querySelectorAll(".topic-chip")) other.setAttribute("aria-expanded", "false");
    for (const panel of document.querySelectorAll(".topic-panel")) {
      panel.hidden = !(open && panel.dataset.panel === chip.dataset.opens);
    }
    chip.setAttribute("aria-expanded", String(open));
  });
}
for (const button of document.querySelectorAll(".all-rows")) {
  button.addEventListener("click", () => {
    for (const row of button.parentElement.querySelectorAll("li[hidden]")) row.hidden = false;
    button.remove();
  });
}
