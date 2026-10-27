// The dashboard's family words: how each person is called, relative to
// whoever is looking (stacklets/core/tools-server/dashboard/components/family.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages; CI does not
// run it yet. The words come from the real kin-words.json.

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

import { kin, useKinWords } from "../../stacklets/core/tools-server/dashboard/components/family.js";

const WORDS = new URL("../../stacklets/core/tools-server/dashboard/components/kin-words.json", import.meta.url);
useKinWords(JSON.parse(readFileSync(WORDS, "utf8")));

const SIMPSONS = [
  { id: "marge", name: "Marge", relation: "parent", words: "she" },
  { id: "homer", name: "Homer", relation: "parent", words: "he" },
  { id: "lisa", name: "Lisa", relation: "child", words: "she" },
  { id: "bart", name: "Bart", relation: "child", words: "he" },
  { id: "abe", name: "Abe", relation: "grandparent", words: "he" },
];
const person = (id, people = SIMPSONS) => people.find((p) => p.id === id);
const as = (viewer, people = SIMPSONS) => Object.fromEntries(
  people.map((p) => [p.id, kin(viewer, p, people)]));

test("Bart sees his sister, his mom and himself", () => {
  const seen = as("bart");
  assert.equal(seen.lisa, "sister");
  assert.equal(seen.marge, "mom");
  assert.equal(seen.homer, "dad");
  assert.equal(seen.bart, "you");
  assert.equal(seen.abe, "grandpa");
});

test("Marge sees her partner and her children", () => {
  assert.deepEqual(as("marge"), { marge: "you", homer: "partner", lisa: "daughter", bart: "son", abe: "grandpa" });
});

test("without a viewer each person is their relation", () => {
  assert.deepEqual(as(null), { marge: "parent", homer: "parent", lisa: "child", bart: "child", abe: "grandparent" });
});

test("someone spoken of by name only gets the neutral word", () => {
  const plain = SIMPSONS.map((p) => ({ ...p, words: "" }));
  assert.equal(kin("bart", person("lisa", plain), plain), "sibling");
  assert.equal(kin("bart", person("marge", plain), plain), "parent");
  assert.equal(kin("marge", person("bart", plain), plain), "child");
});

test("a pair the words do not know is family, never a guess", () => {
  const abe = person("abe");
  assert.equal(kin("abe", person("marge"), SIMPSONS), "family");
  const cousin = { id: "cousin", name: "Cousin", relation: "other", words: "" };
  assert.equal(kin("bart", cousin, [...SIMPSONS, cousin]), "family");
  assert.equal(kin("abe", abe, SIMPSONS), "you");
});

test("someone looking who is not in the family sees everyone as family", () => {
  assert.equal(kin("ned", person("lisa"), SIMPSONS), "family");
});
