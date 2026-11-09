// The sign-in guide's one script (`sign_in_guide.py`, /go/app/sign-in):
// shows Copy beside the server address and the username, and copies the
// value beside the button. The page reads complete without it.
//
//   <script type="module" src="/dashboard/static/sign-in-guide.js"></script>

import { copyText } from "./copy-value.js";

/** Show every Copy button of `root` and make it copy its value.
 *
 *  Example: `enableCopy(document)`; a tap on Copy beside "homer" copies
 *  "homer" and the button says "Copied" for a moment.
 */
export function enableCopy(root) {
  for (const button of root.querySelectorAll(".setup-copy")) button.hidden = false;
  root.addEventListener("click", async (event) => {
    const button = event.target.closest(".setup-copy");
    if (!button) return;
    const value = button.parentElement.querySelector(".setup-value")?.textContent || "";
    if (!(await copyText(value))) return;
    const label = button.textContent;
    button.textContent = button.closest("[data-copied]")?.dataset.copied || label;
    setTimeout(() => { button.textContent = label; }, 1600);
  });
}

enableCopy(document);
