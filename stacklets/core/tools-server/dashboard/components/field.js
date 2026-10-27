// field.js: a text field with its label, an "optional" tag and a help line
// that can follow what is typed (field.css says how it looks).
//
//   import { field } from "./field.js";
//   const address = field({
//     label: "Your family's web address", optional: "optional",
//     help: (value) => value && !value.includes(".")
//       ? { text: "That doesn't look like a web address yet.", tone: "warn" }
//       : "Leave it empty if you don't have one.",
//   });
//   form.append(address);
//   address.input.value;                                  // what was typed
//
// Every word comes from the page: the label, the tag, the help. `help` is a
// text, or a function of the value that returns a text or `{ text, tone }`,
// called on every keystroke; `tone: "warn"` colours it. The moments use the
// `underline` variant with the label hidden from the eye (`hideLabel`).

// Fields numbered, so each help line has an id its input can point to.
let fields = 0;

/** A new field: a `<label>` holding the label, the input and the help line;
 * its input is on `.input`. */
export function field({ label, optional = "", placeholder = "", value = "", help = "",
                        variant = "box", type = "text", name = "", autocomplete = "",
                        hideLabel = false, onInput = null } = {}) {
  const root = document.createElement("label");
  root.className = "fs-field";
  root.dataset.variant = variant;

  const caption = document.createElement("span");
  caption.className = hideLabel ? "fs-sr" : "fs-field-label";
  caption.textContent = label;
  if (optional) {
    const tag = document.createElement("span");
    tag.className = "fs-field-optional";
    tag.textContent = optional;
    caption.append(" ", tag);
  }

  const input = document.createElement("input");
  input.className = "fs-field-input";
  Object.assign(input, { type, value, placeholder });
  if (name) input.name = name;
  if (autocomplete) input.autocomplete = autocomplete;

  const line = document.createElement("span");
  line.className = "fs-field-help";
  line.id = `fs-help-${++fields}`;
  input.setAttribute("aria-describedby", line.id);

  const say = () => {
    const said = typeof help === "function" ? help(input.value) : help;
    const { text = "", tone = "" } = typeof said === "string" ? { text: said } : (said || {});
    line.textContent = text;
    if (tone) line.dataset.tone = tone;
    else delete line.dataset.tone;
  };
  input.addEventListener("input", () => {
    say();
    if (onInput) onInput(input.value);
  });
  say();

  root.append(caption, input, line);
  root.input = input;
  return root;
}
