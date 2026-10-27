// family-editor.js: one person of the family, to change (family-editor.css
// says how it looks).
//
//   import { familyEditor } from "./family-editor.js";
//   panel.replaceChildren(familyEditor(lisa, people, words.editor, {
//     onChange: (person) => redraw(),       // lisa, changed in place
//     onRemove: (person) => remove(person),
//   }));
//
// A person is `{ id, name, relation, words, looks_after }`, as the family
// file has them: `relation` parent, child, grandparent or other; `words`
// "she", "he" or "" for the name only, never guessed from a name. The
// editor changes the object it is given, in place, and tells `onChange`.
//
// It shows the name with a Remove button, the relation, the words, and
// whether the person looks after the server, each a segmented choice; then
// a line saying what the others call them ("So Lisa is Bart's sister and
// Marge's daughter."), worked out with `kin()` and rewritten on every
// change. The pair words must be loaded first (`loadKinWords()` in
// family.js). Every word is the page's, in `words` (the first run's
// `editor` words: remove, relation, relations, words, word_choices,
// looks_after, yes_no, so, of, and).
//
// `askWords: false` leaves the words choice out, for a page that does not
// ask how to speak of people. The person keeps the words they have.

import { kin } from "./family.js";
import { segmented } from "./segmented.js";

// How many of the others the "So Lisa is ..." line names, before it gets long.
const SAID_BY = 3;

/** The editor for `person` among `people`, worded with `words`. */
export function familyEditor(person, people, words, { askWords = true, onChange = null,
                                                     onRemove = null } = {}) {
  const root = document.createElement("div");
  root.className = "fs-family-editor";
  root.dataset.person = person.id;

  const head = document.createElement("div");
  head.className = "fs-family-editor-head";
  const name = document.createElement("b");
  name.textContent = person.name;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "fs-link-button";
  remove.textContent = words.remove;
  remove.addEventListener("click", () => onRemove && onRemove(person));
  head.append(name, remove);

  const said = document.createElement("p");
  said.className = "fs-family-editor-said";
  const change = (key, value) => {
    person[key] = value;
    said.textContent = whatTheOthersSay(person, people, words);
    if (onChange) onChange(person);
  };

  root.append(head, segmented({
    label: words.relation, options: words.relations, value: person.relation,
    onChange: (value) => change("relation", value),
  }));
  if (askWords) {
    root.append(segmented({
      label: words.words, options: words.word_choices, value: person.words || "",
      onChange: (value) => change("words", value),
    }));
  }
  root.append(segmented({
    label: words.looks_after, options: words.yes_no, value: person.looks_after ? "yes" : "no",
    onChange: (value) => change("looks_after", value === "yes"),
  }));
  said.textContent = whatTheOthersSay(person, people, words);
  root.append(said);
  return root;
}

/** "So Lisa is Bart's sister and Marge's daughter.", or "" alone in the family. */
export function whatTheOthersSay(person, people, words) {
  const others = people.filter((p) => p.id !== person.id).slice(0, SAID_BY);
  if (!others.length) return "";
  const parts = others.map((p) => words.of.replace("{name}", p.name).replace("{word}", kin(p.id, person, people)));
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} ${words.and} ${parts[parts.length - 1]}` : parts[0];
  return words.so.replace("{name}", person.name).replace("{list}", list);
}
