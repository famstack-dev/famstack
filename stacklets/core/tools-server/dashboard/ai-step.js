// ai-step.js: the setup page's AI step, made to work (`ai_step.py` renders it).
//
//   import { enhanceAiStep } from "./ai-step.js";
//   enhanceAiStep(stepElement, "/dashboard");
//
// The server renders each part of step 4 from the state alone; this script
// does what needs a request:
//
//   install  "famstack installs oMLX": before a route (`data-form="ai"`)
//       Install answers the ai stacklet's route form, `{answers: {route:
//       "managed"}}` to `/api/configure/ai`, as the installer does; as a
//       change of route it posts to `/api/ai/managed`. Either writes `[ai]`
//       for famstack's oMLX and starts nothing; the page then starts the
//       AI's run as its tile does (`root.start`, a job), and reads the step
//       again: the tile follows the run, and the admin goes on. A refusal
//       (the oMLX app runs, no Homebrew) shows the words for its `reason`
//       from `data-errors` beside the button. A start that fails after the
//       write reads the step again too: the route is written, so the AI's
//       tile offers its Start there.
//   none  "Go on without an AI": posts `{answers: {route: "none"}}` to
//       `/api/configure/ai`, the ai stacklet's route form, which writes
//       `[ai] provider = "none"`, then reads the step again. A refusal is
//       said beside the button.
//   other  "Use your own AI server": before a route (`data-form="ai"`)
//       Connect answers the route form with `route: "external"`, the
//       address and the key; as a change of route it posts them to
//       `/api/ai/switch`. Both run `stack ai switch` on the Mac. A ready
//       answer reads the step again; a refusal shows the words for its
//       `reason` from `data-errors` and keeps the address; several models
//       fill the list to choose from (the form `ai.model`, or the switch's
//       `models`), and Connect sends the pick with the rest.
//   check  "Check again" for a server the stack does not run posts to
//       `/api/ai/check`, which runs `stack ai check` on the Mac, then reads
//       the step again, whatever the answer: the step shows it.
//   end  When the AI's run ends, the page reads the step again, as for any
//       run (`widgets/fs-stack.js`).
//
// Without this script the step still says where it is; Install, no AI,
// Connect and Check again need it, because the dashboard's API takes JSON only.

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
    const errors = JSON.parse(install.dataset.errors || "{}");
    install.disabled = true;
    installOmlx({
      form: Boolean(install.dataset.form),
      post: (path, body) => postJson(api(path), body),
      start: (target) => root.start(target),
      reload: () => root.reload(),
      say: (error, reason) => sayBeside(install, errors[reason] || error),
    });
  });
  const noAi = root.querySelector(".ai-no-ai");
  if (noAi) noAi.addEventListener("click", () => {
    noAi.disabled = true;
    goOnWithoutAi({
      post: (path, body) => postJson(api(path), body),
      reload: () => root.reload(),
      say: (error) => sayBeside(noAi, error),
    });
  });
  const check = root.querySelector(".ai-check-again");
  if (check) check.addEventListener("click", () => checkAgain(check, api));
  return () => {};
}

// ---- famstack installs oMLX ----------------------------------------------

/**
 * Hand the AI to famstack's oMLX and start its run, through `page`: its
 * `form` (true before a route), `post(path, body)` to the dashboard's API,
 * `start(target)` (the tile's job, throws when it was not taken),
 * `reload()` and `say(error, reason)`.
 *
 * Writes the route: with `form` it answers the route form
 * (`configure/ai`), else it posts `ai/managed`. A refusal is said with its
 * reason, and nothing starts. Then starts the AI's job and reads the page
 * again, also when the start failed: the route is written then, so the
 * page offers the AI's Start.
 *
 * Example:
 *   await installOmlx({ ...page, form: true });  // post configure/ai {answers: {route: "managed"}}, start "ai", reload
 *   await installOmlx({ ...page, form: false }); // post ai/managed, start "ai", reload
 */
export async function installOmlx(page) {
  const answer = page.form
    ? await page.post("configure/ai", { answers: { route: "managed" } })
    : await page.post("ai/managed", {});
  if (!(page.form ? answer.ready : answer.provider === "managed")) {
    page.say(answer.error || "The stack did not answer.", answer.reason);
    return;
  }
  try {
    await page.start("ai");
  } catch {
    // The reload shows the AI's tile with its Start, for another try.
  }
  await page.reload();
}

// ---- No AI for now -------------------------------------------------------

/**
 * Answer the ai stacklet's route form with "no AI for now", through `page`:
 * its `post(path, body)`, `reload()` and `say(error)`. Reads the page again
 * once the form is ready; a refusal is said, and nothing changes.
 *
 * Example:
 *   await goOnWithoutAi(page);   // post configure/ai {answers: {route: "none"}}, reload
 */
export async function goOnWithoutAi(page) {
  const answer = await page.post("configure/ai", { answers: { route: "none" } });
  if (!answer.ready) {
    page.say(answer.error || "The stack did not answer.");
    return;
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

/**
 * Connect the server the admin typed, through `page`: its `form` (true
 * before a route), `post(path, body)`, `done()` (read the step again),
 * `models(ids)` (offer these to choose from) and `refused(reason)`.
 * `server` is `{address, key, model}`, `model` "" until one is picked.
 *
 * With `form` it answers the route form (`configure/ai`, `route:
 * "external"`), else it posts `ai/switch`. A ready answer is done; the
 * form `ai.model`, or the switch's `models`, offers the server's models;
 * anything else is refused with its reason.
 *
 * Example:
 *   await connectServer({ ...page, form: true }, { address: "http://192.168.1.30:8000/v1",
 *                                                  key: "", model: "" });
 *   // post configure/ai {answers: {route: "external", address: …, api_key: ""}}, done
 */
export async function connectServer(page, server) {
  const answer = page.form
    ? await page.post("configure/ai", { answers: routeAnswers(server) })
    : await page.post("ai/switch", server);
  if (page.form ? answer.ready : answer.openai_url) {
    page.done();
    return;
  }
  const offered = page.form ? modelsOfTheForm(answer.form) : answer.models;
  if (offered && (answer.form || answer.reason === "model")) {
    page.models(offered);
    return;
  }
  page.refused(answer.reason);
}

/** The route form's answers for `server`; the model only once one is picked. */
function routeAnswers({ address, key, model }) {
  return { route: "external", address, api_key: key, ...(model ? { model } : {}) };
}

/** The model ids the form `ai.model` offers, or undefined for any other form. */
function modelsOfTheForm(form) {
  if (!form || form.id !== "ai.model") return undefined;
  return form.schema.properties.model.oneOf.map((choice) => choice.const);
}

async function connectOther(form, api) {
  const errors = JSON.parse(form.dataset.errors);
  const error = form.querySelector(".ai-error");
  const button = form.querySelector("button[type=submit]");
  const models = form.querySelector(".ai-other-models");
  const shown = (text) => { error.textContent = text; error.hidden = false; button.disabled = false; };
  error.hidden = true;
  button.disabled = true;
  await connectServer({
    form: Boolean(form.dataset.form),
    post: (path, body) => postJson(api(path), body),
    done: () => { location.href = form.dataset.done; },
    models: (ids) => { showModels(models, ids); shown(errors.model); },
    refused: (reason) => shown(errors[reason] || errors.failed),
  }, {
    address: form.elements.address.value,
    key: form.elements.api_key.value,
    model: models.hidden ? "" : form.elements.model.value,
  });
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
