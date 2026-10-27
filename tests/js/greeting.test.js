// The greeting by time of day (stacklets/core/tools-server/dashboard/components/greeting.js).
//
// Run with `node --test tests/js/`. Plain Node, no packages; CI does not run it yet.

import assert from "node:assert/strict";
import test from "node:test";

import { greeting, partOfDay } from "../../stacklets/core/tools-server/dashboard/components/greeting.js";

const WORDS = { morning: "Good morning", afternoon: "Good afternoon", evening: "Good evening" };
const at = (hour, minute = 0) => new Date(2026, 9, 27, hour, minute);

test("each part of the day begins on its hour", () => {
  assert.deepEqual(
    [[0, 0], [4, 59], [5, 0], [11, 59], [12, 0], [17, 59], [18, 0], [23, 59]].map(([h, m]) => partOfDay(at(h, m))),
    ["evening", "evening", "morning", "morning", "afternoon", "afternoon", "evening", "evening"]);
});

test("someone known is greeted by name", () => {
  assert.deepEqual(greeting(WORDS, { name: "Homer", family: "Simpsons", date: at(8) }),
                   { salutation: "Good morning", name: "Homer" });
});

test("nobody known: the family is greeted", () => {
  assert.deepEqual(greeting(WORDS, { family: "Simpsons", date: at(14) }),
                   { salutation: "Good afternoon", name: "Simpsons" });
});

test("no name and no family: only the salutation", () => {
  assert.deepEqual(greeting(WORDS, { date: at(21) }), { salutation: "Good evening", name: "" });
});
