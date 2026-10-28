// form.js: one setup form, drawn from its schema (form.css says how it looks).
//
//   import { setupForm } from "./form.js";
//   const shown = setupForm(answer.form, answersSoFar, words);
//   sheet.update({ title: answer.form.title, body: shown.element });
//   ...
//   answersSoFar = { ...answersSoFar, ...shown.collect() };
//
// A form is what `stack configure --json` sends (`Form.to_dict`, ADR-021):
// `title`, `text`, `schema` in the setup questions' subset, `errors` by
// field, and an optional `x-docs` link. Each field is drawn by its kind
// (`fieldKind`): a yes/no and a closed choice as segmented choices, a
// secret as a password field (its default never comes, and is never
// shown), a time as a time field, a number, or text with its `examples`
// as quick picks under it; a read-only field is a line of information.
// The field's error from the server is under it. Its words are the
// form's, which are the hook's and English; the page gives the rest
// (`words`: yes, no, docs).
//
// `collect()` gives the answers to this form's fields, typed as the form
// asks: true or false, a number, text. A field left empty is no answer.
// The element is a `<form>`, so Enter in a field submits it; the caller
// listens for `submit`.

import { field } from "./field.js";
import { segmented } from "./segmented.js";

/** How a field is asked, from its spec in the schema. */
export function fieldKind(spec) {
  if (spec.readOnly) return "info";
  if (spec.type === "boolean") return "yesno";
  if (Array.isArray(spec.enum) || Array.isArray(spec.oneOf)) return "choice";
  if (spec.writeOnly) return "secret";
  if (spec.format === "time") return "time";
  if (spec.type === "integer") return "number";
  return "text";
}

/** The answer a field's raw value gives, typed; undefined for no answer. */
export function answerFrom(spec, raw) {
  if (raw === null || raw === undefined) return undefined;
  if (spec.type === "boolean") return raw === true || raw === "yes" ? true : raw === false || raw === "no" ? false : undefined;
  // A secret is taken as typed: its spaces may belong to it.
  if (spec.writeOnly) return String(raw) === "" ? undefined : String(raw);
  const text = String(raw).trim();
  if (!text) return undefined;
  if (spec.type === "integer") return /^-?\d+$/.test(text) ? Number(text) : text;
  return text;
}

/** The choices of a closed field, as [value, label] pairs. */
export function choicesOf(spec) {
  if (Array.isArray(spec.oneOf)) return spec.oneOf.map((c) => [String(c.const), c.title || String(c.const)]);
  return (spec.enum || []).map((v) => [String(v), String(v)]);
}

/** Draw `form`, with `answers` given so far filled in; returns
 * `{ element, collect() }`. */
export function setupForm(form, answers = {}, words = {}) {
  const root = document.createElement("form");
  root.className = "fs-form";
  root.noValidate = true;
  root.dataset.form = form.id;
  if (form.text) root.append(paragraphs(form.text));

  const readers = [];
  const errors = form.errors || {};
  for (const [name, spec] of Object.entries(form.schema?.properties || {})) {
    const kind = fieldKind(spec);
    const given = name in answers ? answers[name] : spec.writeOnly ? undefined : spec.default;
    const holder = document.createElement("div");
    holder.className = "fs-form-field";
    holder.dataset.field = name;
    holder.dataset.kind = kind;
    if (kind === "info") {
      holder.append(Object.assign(document.createElement("p"),
        { className: "fs-form-info", textContent: `${spec.title || name}: ${spec.default ?? ""}` }));
    } else if (kind === "yesno" || kind === "choice") {
      const options = kind === "yesno" ? [["yes", words.yes || "Yes"], ["no", words.no || "No"]] : choicesOf(spec);
      const value = kind === "yesno" ? (given === true ? "yes" : given === false ? "no" : null) : given ?? null;
      const row = segmented({ label: spec.title || name, options, value: value === null ? null : String(value) });
      holder.append(row);
      if (spec.description) holder.append(note("fs-form-help", spec.description));
      readers.push([name, spec, () => row.value]);
    } else {
      const type = { secret: "password", time: "time", number: "number" }[kind] || "text";
      const box = field({
        label: spec.title || name, type, name,
        value: given === undefined || given === null ? "" : String(given),
        placeholder: spec.examples?.[0] ? String(spec.examples[0]) : "",
        autocomplete: kind === "secret" ? "new-password" : "off",
        help: spec.description || "",
      });
      holder.append(box);
      if (kind === "text" && spec.examples?.length) holder.append(picks(spec.examples, box.input));
      readers.push([name, spec, () => box.input.value]);
    }
    if (errors[name]) holder.append(note("fs-form-error", errors[name]));
    root.append(holder);
  }
  if (form["x-docs"]) {
    root.append(Object.assign(document.createElement("a"),
      { className: "fs-form-docs", href: form["x-docs"], target: "_blank", rel: "noopener", textContent: words.docs || "Read more" }));
  }
  return {
    element: root,
    collect() {
      const collected = {};
      for (const [name, spec, read] of readers) {
        const answer = answerFrom(spec, read());
        if (answer !== undefined) collected[name] = answer;
      }
      return collected;
    },
  };
}

/** The examples a text field suggests, as buttons that fill it in. */
function picks(examples, input) {
  const row = document.createElement("div");
  row.className = "fs-form-picks";
  for (const example of examples) {
    const pick = Object.assign(document.createElement("button"),
      { type: "button", className: "fs-form-pick", textContent: String(example) });
    pick.addEventListener("click", () => {
      input.value = String(example);
      input.dispatchEvent(new Event("input"));
    });
    row.append(pick);
  }
  return row;
}

/** A form's text: paragraphs at blank lines, its line breaks kept. */
function paragraphs(text) {
  const block = document.createElement("div");
  block.className = "fs-form-text";
  for (const part of text.split(/\n\s*\n/)) block.append(note("", part));
  return block;
}

function note(className, text) {
  const p = document.createElement("p");
  if (className) p.className = className;
  p.textContent = text;
  return p;
}
