// Copy a value to the clipboard, also on a page served over plain HTTP.
//
// The clipboard API (`navigator.clipboard`) exists only in a secure
// context: HTTPS, or localhost. A phone opens famstack at the Mac's
// home-network address over plain HTTP, where the API is missing, so Copy
// falls back to the browser's copy command on a selection. Both the
// service cards (`widgets/service-cards.js`) and the sign-in guide
// (`sign-in-guide.js`) copy through it.
//
// What is copied is the text as shown, in plain characters: a value goes
// into a terminal or an app's field, where an invisible character (a soft
// hyphen, a zero-width space, a direction mark) is part of what was typed
// and makes a command or a password wrong without showing why.
//
//   import { copyText } from "./copy-value.js";
//   if (await copyText("http://192.168.1.20:42031")) button.textContent = "Copied";

/** Copy `text` as `plainText` makes it; true when the browser took it,
 *  false when it refused.
 *
 *  Example: `await copyText("homer")` is true, and the clipboard holds "homer".
 */
export async function copyText(shown) {
  const text = plainText(shown);
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

// ---- Plain characters ----

// Unicode's format characters (category Cf): soft hyphen, zero-width space
// and joiners, word joiner, direction marks, the byte-order mark. None of
// them shows, and each would be pasted.
const INVISIBLE = /\p{Cf}/gu;
// Spaces that look like a space and are not one to a shell.
const ODD_SPACES = /[\u00A0\u2007\u202F]/g;

/** `text` without invisible characters, odd spaces made plain spaces,
 *  and no space or line break at either end.
 *
 *  Example: `plainText("./stack\u00A0up ai\u200B\n")` is "./stack up ai".
 */
export function plainText(text) {
  return text.replace(INVISIBLE, "").replace(ODD_SPACES, " ").trim();
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
