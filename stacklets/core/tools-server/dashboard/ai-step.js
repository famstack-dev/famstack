// ai-step.js: the setup page's AI step, made to work (`ai_step.py` renders it).
//
//   import { enhanceAiStep } from "./ai-step.js";
//   const stop = enhanceAiStep(stepElement, "/dashboard");   // stop() ends the polling
//
// The server renders each part of step 5 from the state alone; this script
// does what needs a request:
//
//   5b  Connect posts the key to the setup forms' route, as `stack
//       configure ai` takes it: `{answers: {use_running_server, api_key}}`.
//       The field exists only on the Mac that runs famstack. Ready reads
//       the step again; anything else shows the deck's "did not accept".
//   5c  The speech switch recomputes the total from each row's
//       `data-size`. Download posts the choice (`/api/ai/download`); when
//       nothing is left to download it starts the AI right away, as the
//       AI's tile's Start does (`FsStack.start`): the tile shows the run's
//       steps, and the run's end reads the step. A refused download or
//       start says so under the button. Only on the Mac that runs
//       famstack: another device gets the rows alone.
//   check  "I installed oMLX" posts to the same route without a key, which
//       connects the app when it answers at its default address and wants
//       none. Anything else, and any device but the Mac, follows the link
//       to `?ai=check`, where the step says what it found and shows the
//       address and key fields: the form of "Use another AI server".
//   other  "Use another AI server": Connect posts the address and the key to
//       `/api/ai/switch`, which runs `stack ai switch` on the Mac. Its
//       answer reads the step again (5e); a refusal shows the words for its
//       `reason` from `data-errors`, keeps the address, and for `model`
//       fills the list of the server's models to choose from.
//   5d  The bars read `/api/ai` every few seconds. When no model downloads
//       any more, the AI is started the same way, and its tile follows
//       that run until 5e.
//   end  When the AI's run ends, the page reads the step again, as for any
//       run (`widgets/fs-stack.js`).
//
// Without this script the step still says where it is, and the terminal's
// route ("Install oMLX for me instead") works; Connect and Download need it,
// because the dashboard's API takes JSON only.

// How often the bars ask where the downloads are.
const POLL_MS = 2000;
// A gigabyte as oMLX counts it, as `ai_step.py` does.
const GB = 1024 ** 3;

/**
 * Make the AI step under `root` work; returns a function that stops it.
 *
 * Example:
 *   const stop = enhanceAiStep(document.querySelector("fs-setup-page"), "/dashboard");
 *   stop();   // the bars stop asking
 */
export function enhanceAiStep(root, base) {
  const api = (path) => new URL(`${base}/api/${path}`, location.href);
  const stops = [];
  const connect = root.querySelector(".ai-connect");
  if (connect) connect.addEventListener("submit", (event) => { event.preventDefault(); connectApp(connect, api); });
  const check = root.querySelector(".ai-check");
  if (check) check.addEventListener("click", (event) => { event.preventDefault(); checkApp(check, api); });
  const other = root.querySelector(".ai-other");
  if (other) other.addEventListener("submit", (event) => { event.preventDefault(); connectOther(other, api); });
  // Another device gets the rows without the switch and the button.
  const models = root.querySelector(".ai-models[data-words]");
  if (models) enhanceModels(models, api, root);
  const bars = root.querySelector(".ai-bars");
  if (bars) stops.push(followBars(bars, api, root));
  return () => stops.forEach((stop) => stop());
}

// ---- 5b: the key ----------------------------------------------------------

async function connectApp(form, api) {
  const error = form.querySelector(".ai-error");
  const button = form.querySelector("button[type=submit]");
  error.hidden = true;
  button.disabled = true;
  const answer = await postJson(api("configure/ai"), {
    answers: { use_running_server: true, api_key: form.elements.api_key.value },
  });
  if (answer.ready) {
    location.href = form.dataset.done;
    return;
  }
  error.hidden = false;
  button.disabled = false;
}

// ---- "I installed oMLX" ----------------------------------------------------

async function checkApp(link, api) {
  const answer = await postJson(api("configure/ai"), { answers: { use_running_server: true } });
  location.href = answer.ready ? link.dataset.done : link.href;
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

// ---- 5c: what the AI gets --------------------------------------------------

function enhanceModels(models, api, root) {
  const words = JSON.parse(models.dataset.words);
  const button = models.querySelector(".ai-download");
  const speech = models.querySelector("input[name=speech]");
  const update = () => {
    const left = bytesLeft(models, speech.checked);
    models.querySelector(".ai-total").textContent = left
      ? words.total.replace("{gb}", gb(left, words.decimal)).replace("{min}", minutes(left, words.rate))
      : words.nothing;
    button.textContent = left ? words.download : words.continue;
  };
  speech.addEventListener("change", update);
  button.hidden = false;
  const error = models.querySelector(".ai-error");
  const failed = (said) => {
    error.textContent = words.failed.replace("{error}", said || "");
    error.hidden = false;
    button.disabled = false;
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    error.hidden = true;
    const plan = await postJson(api("ai/download"), { speech: speech.checked });
    if (!plan.models) {
      failed(plan.error);
      return;
    }
    // Downloads to follow: the step reads itself again and shows the bars.
    if (plan.models.some((m) => m.state === "downloading")) {
      await root.reload();
      return;
    }
    try {
      await root.start("ai");
    } catch (e) {
      failed(e.message);
    }
  });
}

/** The bytes the choice still needs: rows not on the Mac, speech rows only with speech. */
function bytesLeft(models, speech) {
  let sum = 0;
  for (const row of models.querySelectorAll(".ai-row")) {
    if (!speech && row.dataset.role !== "chat") continue;
    sum += Number(row.dataset.size) || 0;
  }
  return sum;
}

// ---- 5d: the bars ----------------------------------------------------------

function followBars(bars, api, root) {
  const words = JSON.parse(bars.dataset.words);
  let timer = setTimeout(tick, POLL_MS);
  async function tick() {
    let plan;
    try {
      const response = await fetch(api("ai"), { headers: { accept: "application/json" } });
      plan = response.ok ? await response.json() : null;
    } catch {
      plan = null;
    }
    if (plan?.models) {
      for (const m of plan.models) showBar(bars, m, words);
      if (!plan.models.some((m) => m.state === "downloading")) {
        // The AI starts as a tile's Start starts it; its tile follows the run.
        // A start that was not taken asks again at the next tick.
        try {
          await root.start("ai");
          return;
        } catch {
          // the next tick tries again
        }
      }
    }
    timer = setTimeout(tick, POLL_MS);
  }
  return () => clearTimeout(timer);
}

/** Move the bar of one row; a row that finished fills its bar. */
function showBar(bars, m, words) {
  const bar = bars.querySelector(`.ai-bar[data-role="${m.role}"]`);
  if (!bar) return;
  const progress = bar.querySelector("progress");
  const size = m.state === "on_mac" ? Number(progress.max) : m.size;
  const done = m.state === "on_mac" ? size : m.done;
  progress.max = Math.max(1, size);
  progress.value = done;
  let text = words.bar.replace("{name}", m.name)
    .replace("{got}", gb(done, words.decimal)).replace("{of}", gb(size, words.decimal));
  if (size > done) text += words.left.replace("{min}", minutes(size - done, words.rate));
  bar.querySelector(".ai-bar-text").textContent = text;
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

function gb(bytes, decimal) {
  return (bytes / GB).toFixed(1).replace(".", decimal);
}

function minutes(bytes, rate) {
  return String(Math.max(1, Math.round(bytes / GB / rate)));
}
