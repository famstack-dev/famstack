// Copy a value to the clipboard, also on a page served over plain HTTP.
//
// The clipboard API (`navigator.clipboard`) exists only in a secure
// context: HTTPS, or localhost. A phone opens famstack at the Mac's
// home-network address over plain HTTP, where the API is missing, so Copy
// falls back to the browser's copy command on a selection. Both the
// service cards (`widgets/service-cards.js`) and the sign-in guide
// (`sign-in-guide.js`) copy through it.
//
//   import { copyText } from "./copy-value.js";
//   if (await copyText("http://192.168.1.20:42031")) button.textContent = "Copied";

/** Copy `text`; true when the browser took it, false when it refused.
 *
 *  Example: `await copyText("homer")` is true, and the clipboard holds "homer".
 */
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Refused (no permission): try the copy command below.
    }
  }
  return copyBySelection(text);
}

// ---- The fallback for plain HTTP ----

/** The copy command on a hidden text field holding `text`. */
function copyBySelection(text) {
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.append(field);
  field.select();
  field.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  field.remove();
  return copied;
}
