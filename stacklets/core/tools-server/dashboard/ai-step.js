// ai-step.js: the setup page's AI step, made to work (`ai_step.py` renders it).
//
//   import { enhanceAiStep } from "./ai-step.js";
//   enhanceAiStep(stepElement, "/dashboard");
//
// The server renders each part of step 4 from the state alone; this script
// does what needs a request:
//
//   install  "famstack installs oMLX": Install posts to `/api/ai/managed`,
//       which writes `[ai]` for famstack's oMLX and starts nothing, then
//       starts the AI's run as its tile does (`root.start`, a job), and
//       reads the step again: the tile follows the run, and the admin goes
//       on. A refused write shows its reason beside the button. A start
//       that fails after the write reads the step again too: the route is
//       written, so the AI's tile offers its Start there.
//   other  "Use your own AI server": Connect posts the address and the key to
//       `/api/ai/switch`, which runs `stack ai switch` on the Mac. Its
//       answer reads the step again; a refusal shows the words for its
//       `reason` from `data-errors`, keeps the address, and for `model`
//       fills the list of the server's models to choose from.
//   check  "Check again" for a server the stack does not run posts to
//       `/api/ai/check`, which runs `stack ai check` on the Mac, then reads
//       the step again, whatever the answer: the step shows it.
//   end  When the AI's run ends, the page reads the step again, as for any
//       run (`widgets/fs-stack.js`).
//
// Without this script the step still says where it is; Install, Connect and
// Check again need it, because the dashboard's API takes JSON only.

/**
 * Make the AI step under `root` work; returns a function that stops it.
 *
 * Example:
 *   const stop = enhanceAiStep(document.querySelector("fs-setup-page"), "/dashboard");
 *   stop();   // nothing runs on its own, so nothing stops
 */
export function enhanceAiStep(root, base) {
  const api = (path) => new URL(`${base}/api/${path}`, location.href);
  const other = root.querySelector(".ai-other");
  if (other) other.addEventListener("submit", (event) => { event.preventDefault(); connectOther(other, api); });
  const install = root.querySelector(".ai-install");
  if (install) install.addEventListener("click", () => {
    install.disabled = true;
    installOmlx({
      post: (path, body) => postJson(api(path), body),
      start: (target) => root.start(target),
      reload: () => root.reload(),
      say: (error) => sayBeside(install, error),
    });
  });
  const check = root.querySelector(".ai-check-again");
  if (check) check.addEventListener("click", () => checkAgain(check, api));
  return () => {};
}

// ---- famstack installs oMLX ----------------------------------------------

/**
 * Hand the AI to famstack's oMLX and start its run, through `page`: its
 * `post(path, body)` to the dashboard's API, `start(target)` (the tile's
 * job, throws when it was not taken), `reload()` and `say(error)`.
 *
 * Writes the route (`ai/managed`); a refusal is said, and nothing starts.
 * Then starts the AI's job and reads the page again, also when the start
 * failed: the route is written then, so the page offers the AI's Start.
 *
 * Example:
 *   await installOmlx(page);   // post ai/managed, start "ai", reload
 */
export async function installOmlx(page) {
  const answer = await page.post("ai/managed", {});
  if (answer.provider !== "managed") {
    page.say(answer.error || "The stack did not answer.");
    return;
  }
  try {
    await page.start("ai");
  } catch {
    // The reload shows the AI's tile with its Start, for another try.
  }
  await page.reload();
}

/** `error` in a note after `button`, which is enabled again. */
function sayBeside(button, error) {
  let said = button.parentElement.querySelector(".ai-error");
  if (!said) {
    said = document.createElement("p");
    said.className = "setup-note ai-error";
    button.after(said);
  }
  said.textContent = error;
  button.disabled = false;
}

// ---- Another AI server ----------------------------------------------------

async function connectOther(form, api) {
  const errors = JSON.parse(form.dataset.errors);
  const error = form.querySelector(".ai-error");
  const button = form.querySelector("button[type=submit]");
  const models = form.querySelector(".ai-other-models");
  error.hidden = true;
  button.disabled = true;
  const answer = await postJson(api("ai/switch"), {
    address: form.elements.address.value,
    key: form.elements.api_key.value,
    model: models.hidden ? "" : form.elements.model.value,
  });
  if (answer.openai_url) {
    location.href = form.dataset.done;
    return;
  }
  if (answer.reason === "model" && answer.models) showModels(models, answer.models);
  error.textContent = errors[answer.reason] || errors.failed;
  error.hidden = false;
  button.disabled = false;
}

/** Fill the list of the server's models and show it; the first is chosen. */
function showModels(label, models) {
  const select = label.querySelector("select");
  select.replaceChildren(...models.map((id) => new Option(id, id)));
  label.hidden = false;
}

// ---- Check again ---------------------------------------------------------

async function checkAgain(button, api) {
  button.disabled = true;
  await postJson(api("ai/check"), {});
  location.href = button.dataset.done;
}

// ---- Small helpers ---------------------------------------------------------

async function postJson(url, body) {
  try {
    const response = await fetch(url, {
      method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
    return await response.json();
  } catch (error) {
    return { error: String(error) };
  }
}
