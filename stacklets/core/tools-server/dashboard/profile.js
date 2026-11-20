// profile.js: the person's profile page (person_profile/page.py).
//
// The page reads complete without a script. This one adds what needs the
// browser:
//
//   - the password in Settings (one's own device only): masked, with Show,
//     Hide and Copy (copy-value.js, which also works over plain HTTP);
//   - "On this Mac": who this Mac's browser greets is kept in the browser
//     (components/family.js), so This is me and Not me set and forget it
//     here, and the section says whom it greets;
//   - /dashboard/me on the Mac: opens the profile of the person picked in
//     this browser, at the same section (#devices), or the dashboard.
//
// Allow, Dismiss, Remove and Not me on a device row are devices.js's.

import { copyText } from "./copy-value.js";
import { forgetViewer, setViewer, viewer } from "./components/family.js";

// ---- The password ----------------------------------------------------------

const MASK = "•••• •••• •••• ••••";
// How long Copy says Copied.
const COPIED_MS = 1600;

for (const row of document.querySelectorAll(".p-password")) {
  const value = row.querySelector("[data-secret]");
  const password = value.textContent;
  const show = row.querySelector("[data-show]");
  const copy = row.querySelector("[data-copy]");
  const showLabel = show.textContent;
  const copyLabel = copy.textContent;
  value.textContent = MASK;
  show.hidden = copy.hidden = false;
  show.addEventListener("click", () => {
    const masked = value.textContent === MASK;
    value.textContent = masked ? password : MASK;
    show.textContent = masked ? show.dataset.hide : showLabel;
  });
  copy.addEventListener("click", async () => {
    if (!(await copyText(password))) return;
    copy.textContent = copy.dataset.copied;
    setTimeout(() => (copy.textContent = copyLabel), COPIED_MS);
  });
}

// ---- On this Mac -----------------------------------------------------------

for (const section of document.querySelectorAll("[data-pick]")) {
  const person = section.dataset.pick;
  const draw = () => {
    const picked = viewer() === person;
    section.querySelector('[data-when="nobody"]').hidden = picked;
    section.querySelector('[data-when="picked"]').hidden = !picked;
    section.querySelector("[data-pick-me]").hidden = picked;
    section.querySelector("[data-not-picked]").hidden = !picked;
  };
  section.querySelector("[data-pick-me]").addEventListener("click", () => {
    setViewer(person);
    draw();
  });
  section.querySelector("[data-not-picked]").addEventListener("click", () => {
    forgetViewer();
    draw();
  });
  draw();
}

// ---- /dashboard/me on the Mac ----------------------------------------------

if (document.querySelector("[data-me]")) {
  const picked = viewer();
  location.replace(picked ? `/dashboard/person/${encodeURIComponent(picked)}${location.hash}`
    : "/dashboard");
}
