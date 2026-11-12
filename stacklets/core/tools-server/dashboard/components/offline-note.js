// offline-note.js: the line that says famstack restarts, while the page's
// event stream is down (`../live.js`).
//
//   <script type="module" src="$base/static/components/offline-note.js">   // in each page
//
// Core restarts during an install and an update, and every page then loses
// its stream. The stream reconnects by itself and the views read
// themselves again once it is back (`../live.js`), so the page never reloads
// by hand: a reload while the server is gone shows the browser's own
// error page, and that page tries nothing again. Meanwhile this line says
// what happens. It shows only after `SHOW_AFTER_MS`, because a stream that
// reconnects at once is no restart, and goes as soon as the stream is open.
//
// The words are the page's (`#fs-page-data`, `family_view.py`).

import { connect } from "../live.js";

// A reconnect shorter than this is not shown.
const SHOW_AFTER_MS = 2000;

const data = JSON.parse(document.getElementById("fs-page-data")?.textContent || "{}");
const text = data.words?.offline || "";
const live = connect();
let note = null;
let timer = null;

live.addEventListener("status", () => {
  clearTimeout(timer);
  if (live.status === "open") {
    note?.remove();
    note = null;
  } else if (!note && text) {
    timer = setTimeout(show, SHOW_AFTER_MS);
  }
});

/** Put the line at the top of the page, announced to screen readers. */
function show() {
  note = Object.assign(document.createElement("p"), { className: "fs-offline", textContent: text });
  note.setAttribute("role", "status");
  document.body.append(note);
}
