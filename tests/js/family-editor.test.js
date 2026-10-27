// The family editor's line on how the others call a person, and the pair
// words in German (stacklets/core/tools-server/dashboard/components/).
//
// Run with `node --test tests/js/`. Plain Node, no packages; CI does not
// run it yet. The words come from the real kin-words.json and the page's
// own words (family_view.py), copied here as the page receives them.

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

import { kin, useKinWords } from "../../stacklets/core/tools-server/dashboard/components/family.js";
import { whatTheOthersSay } from "../../stacklets/core/tools-server/dashboard/components/family-editor.js";

const WORDS = new URL("../../stacklets/core/tools-server/dashboard/components/kin-words.json", import.meta.url);
useKinWords(JSON.parse(readFileSync(WORDS, "utf8")));

const EDITOR = { so: "So {name} is {list}.", of: "{name}'s {word}", and: "and" };
const SIMPSONS = [
  { id: "homer", name: "Homer", relation: "parent", words: "he" },
  { id: "marge", name: "Marge", relation: "parent", words: "she" },
  { id: "bart", name: "Bart", relation: "child", words: "he" },
  { id: "lisa", name: "Lisa", relation: "child", words: "she" },
];

test("the editor says what the others call a person", () => {
  const lisa = SIMPSONS[3];
  assert.equal(whatTheOthersSay(lisa, SIMPSONS, EDITOR),
               "So Lisa is Homer's daughter, Marge's daughter and Bart's sister.");
});

test("alone in the family, there is nothing to say", () => {
  assert.equal(whatTheOthersSay(SIMPSONS[0], [SIMPSONS[0]], EDITOR), "");
});

test("in German, Bart's sister is his Schwester and his mother his Mama", () => {
  globalThis.document = { documentElement: { lang: "de" } };
  try {
    assert.equal(kin("bart", SIMPSONS[3], SIMPSONS), "Schwester");
    assert.equal(kin("bart", SIMPSONS[1], SIMPSONS), "Mama");
    assert.equal(kin(null, SIMPSONS[2], SIMPSONS), "Kind");
    assert.equal(kin("bart", SIMPSONS[2], SIMPSONS), "du");
  } finally {
    delete globalThis.document;
  }
});
