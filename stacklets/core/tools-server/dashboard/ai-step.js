// ai-step.js: the setup page's AI step, made to work (`ai_step.py` renders it).
//
//   import { enhanceAiStep } from "./ai-step.js";
//   enhanceAiStep(stepElement, "/dashboard");
//
// The server renders each part of step 4 from the state alone; this script
// does what needs a request:
//
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
// Without this script the step still says where it is, and the terminal's
// route works; Connect and Check again need it, because the dashboard's API
// takes JSON only.

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
  const check = root.querySelector(".ai-check-again");
  if (check) check.addEventListener("click", () => checkAgain(check, api));
  return () => {};
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
