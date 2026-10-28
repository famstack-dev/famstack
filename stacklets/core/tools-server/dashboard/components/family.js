// family.js: the family as the dashboard shows it, relative to whoever is
// looking (pills.css says how a pill looks).
//
//   import { loadKinWords, kin, viewer, setViewer, pill } from "./family.js";
//   await loadKinWords();                         // kin-words.json, next to this file
//   const me = viewer();                          // "bart", or null on a new device
//   row.replaceChildren(...people.map((p) =>
//     pill(p, { label: kin(me, p, people), you: p.id === me })));
//
// A person is `{ id, name, tone, relation, words }`: `relation` is parent,
// child, grandparent or other, as in users.toml; `words` is how to speak of
// them, "she", "he" or "" for name only, never guessed from a name.
//
// `kin(viewerId, person, people)` is the word for `person` as the viewer
// would say it: Lisa is Bart's "sister", Marge his "mom", Bart himself
// "you". It is worked out from both people's relation and never stored
// (decision 0028, "it's relative to one person"). With no viewer it is the
// person's relation; with name only, the neutral word (sibling, parent,
// child). The words are data, `kin-words.json`, which a Python reader for
// the bots will share; English only for now. A page loads them once with
// `loadKinWords()`; a test hands them in with `useKinWords()`.
//
// "Who is looking on this device" is kept in localStorage, and only when
// someone taps their pill (`setViewer`). It is personalisation, not sign-in.
// Every read and write is in try/catch: a browser without storage, or with
// it switched off, simply has nobody looking.

// The key the viewer is kept under, in this device's localStorage.
const VIEWER_KEY = "fs-viewer";
// Which of a pair's three words a person gets, by how they are spoken of.
const BY_WORDS = { she: 0, he: 1 };

let table = null;
let loading = null;

/** Load the pair words once, from `kin-words.json` next to this module. */
export function loadKinWords(url = new URL("./kin-words.json", import.meta.url)) {
  loading ??= fetch(url).then((response) => response.json()).then((words) => (table = words));
  return loading;
}

/** Use these pair words (kin-words.json's content), as a test does. */
export function useKinWords(words) {
  table = words;
}

/** The word for `person` as the viewer (`viewerId`, or null) would say it. */
export function kin(viewerId, person, people) {
  const words = language();
  if (!viewerId) return words.relations[person.relation] || words.unknown;
  if (viewerId === person.id) return words.you;
  const viewing = people.find((p) => p.id === viewerId);
  const pair = viewing && (words.pairs[viewing.relation] || {})[person.relation];
  if (!pair) return words.unknown;
  return typeof pair === "string" ? pair : pair[BY_WORDS[person.words] ?? 2];
}

/** The page's language's words, English when it has none of its own. */
function language() {
  if (!table) throw new Error("family.js: load the pair words first (loadKinWords)");
  const lang = (globalThis.document?.documentElement.lang || "en").slice(0, 2);
  return table[lang] || table.en;
}

// ── Who is looking ─────────────────────────────────────────────────────────

/** Whoever this device greets, or null. */
export function viewer() {
  try {
    return globalThis.localStorage?.getItem(VIEWER_KEY) || null;
  } catch {
    return null;
  }
}

/** Remember that `id` is looking on this device (they tapped their pill). */
export function setViewer(id) {
  try {
    globalThis.localStorage?.setItem(VIEWER_KEY, id);
  } catch {
    // No storage: the device just won't remember.
  }
}

/** Forget who is looking on this device. */
export function forgetViewer() {
  try {
    globalThis.localStorage?.removeItem(VIEWER_KEY);
  } catch {
    // Nothing kept, nothing to forget.
  }
}

// ── A pill ─────────────────────────────────────────────────────────────────

/** A family pill: a button with the person's avatar, name and `label`, and
 * the "you" ring when `you`. `title` is the page's hint for the button. */
export function pill(person, { label = "", you = false, title = "" } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "fs-pill";
  button.dataset.person = person.id;
  button.setAttribute("aria-pressed", String(you));
  if (title) button.title = title;
  const avatar = document.createElement("span");
  avatar.className = "fs-avatar";
  avatar.dataset.tone = String((person.tone ?? 0) % 7);
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = person.name.slice(0, 1).toUpperCase();
  const name = document.createElement("span");
  name.className = "fs-pill-name";
  name.textContent = person.name;
  button.append(avatar, name);
  if (label) {
    const said = document.createElement("span");
    said.className = "fs-pill-label";
    said.textContent = label;
    button.append(said);
  }
  return button;
}
