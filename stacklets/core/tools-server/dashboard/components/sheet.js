// sheet.js: a dialog for one task, such as adding Photos (sheet.css says
// how it looks).
//
//   import { openSheet } from "./sheet.js";
//   const sheet = openSheet({
//     label: "Add to your stack", title: "Photos",
//     body: "A private photo library. Every phone backs up its camera roll to this Mac.",
//     aside: "stack up photos",
//     actions: [{ label: "Not now", kind: "soft", close: true },
//               { label: "Add Photos", kind: "go", onClick: () => start() }],
//   });
//   sheet.update({ state: "running", steps: [{ text: "Downloading Immich", status: "now" }] });
//   sheet.update({ state: "done", done: "Photos is ready." });
//   sheet.close();
//
// Three states: `about` (what it is, the default), `running` (its `steps`,
// each `done`, `now` or waiting) and `done` (a large tick and the `done`
// sentence). `update()` changes any part; the rest stays. `body` is a text
// or an element. Every word is the page's.
//
// It is a modal `<dialog>`: focus goes into it when it opens, Esc closes
// it, and focus comes back to where it was. A closed sheet is removed.
// `sheetContent(options)` builds the same content without the dialog, for
// showing a sheet in place (the gallery).

const TICK = '<svg width="{s}" height="{s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';

/** Open a sheet; returns `{ element, update(options), close() }`. */
export function openSheet(options = {}) {
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "fs-sheet";
  if (options.wide) dialog.dataset.width = "wide";
  let current = { ...options };
  const draw = () => {
    dialog.replaceChildren(sheetContent(current, () => dialog.close()));
    dialog.setAttribute("aria-labelledby", dialog.querySelector(".fs-sheet-title").id);
  };
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (opener && typeof opener.focus === "function") opener.focus();
  });
  draw();
  document.body.append(dialog);
  dialog.showModal();
  return {
    element: dialog,
    update(more) { current = { ...current, ...more }; draw(); },
    close() { dialog.close(); },
  };
}

let sheets = 0;

/** A sheet's content: label, title, body, its state, and its foot. */
export function sheetContent({ label = "", title = "", body = "", state = "about", steps = [],
                               done = "", aside = "", actions = [] } = {}, close = () => {}) {
  const inner = h("div", "fs-sheet-in");
  if (label) inner.append(h("span", "fs-sheet-label", label));
  const heading = h("h2", "fs-sheet-title", title);
  heading.id = `fs-sheet-title-${++sheets}`;
  inner.append(heading);
  if (body) {
    const text = h("div", "fs-sheet-body");
    text.append(typeof body === "string" ? h("p", "", body) : body);
    inner.append(text);
  }
  if (state === "running") {
    const list = h("ol", "fs-sheet-steps");
    for (const step of steps) {
      const item = h("li");
      item.dataset.status = step.status || "waiting";
      const mark = h("span", "fs-sheet-mark");
      if (step.status === "done") mark.innerHTML = TICK.replaceAll("{s}", "14");
      item.append(mark, step.text);
      list.append(item);
    }
    inner.append(list);
  }
  if (state === "done") {
    const row = h("div", "fs-sheet-done");
    const tick = h("span", "fs-sheet-tick");
    tick.innerHTML = TICK.replaceAll("{s}", "28");
    row.append(tick, h("p", "", done));
    inner.append(row);
  }
  const foot = h("div", "fs-sheet-foot");
  foot.append(h("span", "fs-sheet-aside", aside));
  for (const action of actions) {
    const button = h("button", "fs-button", action.label);
    button.type = "button";
    if (action.kind) button.dataset.kind = action.kind;
    button.addEventListener("click", () => {
      if (action.onClick) action.onClick();
      if (action.close) close();
    });
    foot.append(button);
  }
  inner.append(foot);
  return inner;
}

function h(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}
