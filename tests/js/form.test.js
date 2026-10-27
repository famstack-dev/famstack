// A setup form's fields: how each is asked, and the answer it gives
// (stacklets/core/tools-server/dashboard/components/form.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages; CI does not
// run it yet. The specs are those of the shipped forms (stacklets/*/hooks/
// on_configure.py), in the subset ADR-021 names.

import assert from "node:assert/strict";
import test from "node:test";

import { answerFrom, choicesOf, fieldKind } from "../../stacklets/core/tools-server/dashboard/components/form.js";

const DISK = { type: "string", title: "Disk volume name", default: "backup-vault", examples: ["backup-vault"], minLength: 1 };
const PASSPHRASE = { type: "string", title: "Passphrase", writeOnly: true };
const TIME = { type: "string", format: "time", default: "02:00" };
const SETUP_OMLX = { type: "boolean", title: "Set up oMLX?", default: true };

test("each field is asked by its kind", () => {
  assert.equal(fieldKind(DISK), "text");
  assert.equal(fieldKind(PASSPHRASE), "secret");
  assert.equal(fieldKind(TIME), "time");
  assert.equal(fieldKind(SETUP_OMLX), "yesno");
  assert.equal(fieldKind({ type: "string", enum: ["a", "b"] }), "choice");
  assert.equal(fieldKind({ type: "integer" }), "number");
  assert.equal(fieldKind({ type: "string", readOnly: true }), "info");
});

test("a yes or no is a boolean, and nothing chosen is no answer", () => {
  assert.equal(answerFrom(SETUP_OMLX, "yes"), true);
  assert.equal(answerFrom(SETUP_OMLX, "no"), false);
  assert.equal(answerFrom(SETUP_OMLX, null), undefined);
});

test("text is trimmed, and an empty field is no answer", () => {
  assert.equal(answerFrom(DISK, "  backup-vault "), "backup-vault");
  assert.equal(answerFrom(DISK, "   "), undefined);
});

test("a secret is taken as typed", () => {
  assert.equal(answerFrom(PASSPHRASE, " correct horse "), " correct horse ");
  assert.equal(answerFrom(PASSPHRASE, ""), undefined);
});

test("a number is a number, when it reads as one", () => {
  assert.equal(answerFrom({ type: "integer" }, "12"), 12);
  assert.equal(answerFrom({ type: "integer" }, "twelve"), "twelve");
});

test("labelled choices keep their labels", () => {
  assert.deepEqual(choicesOf({ oneOf: [{ const: "daily", title: "Every day" }] }), [["daily", "Every day"]]);
  assert.deepEqual(choicesOf({ enum: ["red", "blue"] }), [["red", "red"], ["blue", "blue"]]);
});
