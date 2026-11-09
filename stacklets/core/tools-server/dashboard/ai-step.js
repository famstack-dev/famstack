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
//       nothing is left to download it starts the AI right away. Only on
//       the Mac that runs famstack: another device gets the rows alone.
//   5d  The bars read `/api/ai` every few seconds. When no model downloads
//       any more, the AI is started (`stack up ai` as a job), and the page
//       reads the step again: the AI's tile follows that run until 5e.
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
  // Another device gets the rows without the switch and the button.
  const models = root.querySelector(".ai-models[data-words]");
  if (models) enhanceModels(models, api);
  const bars = root.querySelector(".ai-bars");
  if (bars) stops.push(followBars(bars, api));
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

// ---- 5c: what the AI gets --------------------------------------------------

function enhanceModels(models, api) {
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
  button.addEventListener("click", async () => {
    button.disabled = true;
    const plan = await postJson(api("ai/download"), { speech: speech.checked });
    if (!plan.models) {
      button.disabled = false;
      return;
    }
    if (!plan.models.some((m) => m.state === "downloading")) await startAi(api);
    location.reload();
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

function followBars(bars, api) {
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
        await startAi(api);
        location.reload();
        return;
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

/** Start `stack up ai` as a job, as a tile's Start does. */
function startAi(api) {
  return postJson(api("jobs"), { action: "up", target: "ai" });
}

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
