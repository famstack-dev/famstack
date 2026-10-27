// greeting.js: the greeting by time of day, for a name or for the family.
//
//   import { greeting } from "./greeting.js";
//   const { salutation, name } = greeting(
//     { morning: "Good morning", afternoon: "Good afternoon", evening: "Good evening" },
//     { name: "Homer", family: "Simpsons" });
//   heading.innerHTML = `${salutation}, <em>${name}.</em>`;   // the page words the line
//
// `name` is whoever is looking, when the device knows them; without one the
// family is greeted; without either, `name` is "" and the page says only
// the salutation. The words are the page's, in its language. The hours the
// parts of the day begin at are here and nowhere else.

// When each part of the day begins, by the hour on this device's clock.
// The small hours belong to the evening before.
export const BEGINS = [
  [0, "evening"],
  [5, "morning"],
  [12, "afternoon"],
  [18, "evening"],
];

/** "morning", "afternoon" or "evening", for `date`. */
export function partOfDay(date = new Date()) {
  const hour = date.getHours();
  let part = BEGINS[0][1];
  for (const [begins, name] of BEGINS) if (hour >= begins) part = name;
  return part;
}

/** The salutation for now (`words` by part of the day), and who it is for. */
export function greeting(words, { name = "", family = "", date = new Date() } = {}) {
  return { salutation: words[partOfDay(date)], name: name || family || "" };
}
