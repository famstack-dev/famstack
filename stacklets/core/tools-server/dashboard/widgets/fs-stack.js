// FsStack: tiles that start a stacklet and follow its run (README.md).
//
// It is the one way the dashboard and the setup page start a stacklet: no
// other code posts a job. A step that needs a stacklet started, the AI
// step's Continue included, shows that stacklet's tile and calls `start`,
// so the start, the run's steps and the read at the run's end are the
// same everywhere.
//
// The page arrives with its tiles rendered by the server (`stack_widget.py`);
// this element makes Start work: it posts the job to the tools server
// (`POST /dashboard/api/jobs`), which runs `stack up` through the host API.
// Without JavaScript a tile shows its terminal command instead, which is
// what Start runs. The words come from the server, in the page's language.
//
// A tile follows its job on the page's event stream (`../live.js`): when a
// job starts for it, here or in another browser, the tile shows its steps
// as they happen; when any job ends, the element reads its view again
// (`widget.load`), so the tile shows how it went. It is the base of the
// service cards (`service-cards.js`), which the setup page and the
// dashboard's status pills extend.
//
// A tile whose stacklet still asks setup questions in forms has an Add
// marked `data-forms`: it opens a sheet with the first form (`../components/
// form.js`), posts every answer so far to `POST /dashboard/api/configure/<id>`
// on Continue, and shows the next form, or the same form with the reason
// under a field. Add is only on the Mac's address, where every answer,
// a secret included, may be given. Once the answers are applied the sheet closes and the
// job starts, as a plain Add does. Closed before that, nothing was applied.

import { setupForm } from "../components/form.js";
import { openSheet } from "../components/sheet.js";
import { connect } from "../live.js";

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== ""));
  return node;
}

export class FsStack extends HTMLElement {
  connectedCallback() {
    this.addEventListener("click", (event) => {
      const button = event.target.closest(".tile-add");
      if (button) this.add(button);
    });
    this.off = connect().on("job/", (event) => this.follow(event));
  }

  disconnectedCallback() { if (this.off) this.off(); }

  get widget() { return this.closest("fs-widget"); }

  /** What a tile is in this element's markup. The setup page's service
   *  cards follow a run as tiles do, so it adds them (`setup-page.js`). */
  get tileSelector() { return ".tile"; }

  /** The name a tile shows, for the forms' sheet. */
  nameOf(tile) { return tile?.querySelector(".tile-name")?.textContent || ""; }

  /** The tile whose `attribute` is `value`. */
  tileWhere(attribute, value) {
    return this.querySelector(`:is(${this.tileSelector})[${attribute}="${CSS.escape(value)}"]`);
  }

  /** Where jobs are started: next to the widget's own endpoint. */
  get jobs() { return new URL("jobs", new URL(this.widget.getAttribute("src"), location.href)); }

  /** A step's line in the page's language: the server's words for its key
   *  (`data-step-words`, `run_words.py`), else the text the run wrote. */
  stepText(step) {
    let words = {};
    try {
      words = JSON.parse(this.dataset.stepWords || "{}");
    } catch {
      // No words: every step reads as the run wrote it.
    }
    return words[step.key] || step.text;
  }

  /** The page's words for the forms, from the server (`data-forms-words`). */
  get formsWords() {
    try {
      return JSON.parse(this.dataset.formsWords || "{}");
    } catch {
      return {};
    }
  }

  /** Add: the forms first when the stacklet still asks, then the job. */
  add(button) {
    if (button.hasAttribute("data-forms")) this.askForms(button);
    else this.startJob(button);
  }

  /** Ask the stacklet's setup forms in a sheet; start the job once ready. */
  async askForms(button) {
    const id = button.dataset.target;
    const words = this.formsWords;
    const name = this.nameOf(button.closest(this.tileSelector)) || id;
    const url = new URL(`configure/${encodeURIComponent(id)}`, this.jobs);
    let answers = {};
    let shown = null;
    const sheet = openSheet({ label: words.label, title: name, aside: `stack up ${id}`,
                              actions: [{ label: words.not_now, kind: "soft", close: true }] });
    const ask = async () => {
      let answer;
      try {
        const response = await fetch(url, {
          method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify({ answers }),
        });
        answer = await response.json();
      } catch (error) {
        answer = { error: String(error) };
      }
      if (answer.ready) {
        answers = {};
        sheet.close();
        this.startJob(button);
      } else if (answer.form) {
        shown = setupForm(answer.form, answers, words);
        shown.element.addEventListener("submit", (event) => { event.preventDefault(); next(); });
        sheet.update({ title: answer.form.title, body: shown.element, actions: [
          { label: words.not_now, kind: "soft", close: true },
          { label: words.continue, kind: "go", onClick: next }] });
        shown.element.querySelector("input, button[role=radio]")?.focus();
      } else {
        sheet.update({ body: (words.failed || "{error}").replace("{error}", answer.error || ""),
                       actions: [{ label: words.close, kind: "soft", close: true }] });
      }
    };
    const next = () => {
      answers = { ...answers, ...shown.collect() };
      ask();
    };
    ask();
  }

  /** Start `stack up` for the tile's stacklet as a job. */
  async startJob(button) {
    button.disabled = true;
    try {
      this.started(button.closest(this.tileSelector), await this.start(button.dataset.target));
    } catch {
      button.disabled = false;
    }
  }

  /** Post `stack up <target>` as a job, and let the stacklet's tile follow
   *  it; resolves with the job, throws when the job was not taken. Other
   *  buttons of a page start a stacklet through here, as a tile's Start does. */
  async start(target) {
    const response = await fetch(this.jobs, {
      method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ action: "up", target }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || String(response.status));
    const job = await response.json();
    this.started(this.tileWhere("data-id", target), job);
    return job;
  }

  /** A job runs for this tile now: show it, once, with its steps so far. */
  started(tile, job) {
    if (!tile || tile.dataset.job === job.id) return;
    // A tile the page keeps hidden until its run starts (`data-until-started`).
    tile.closest("[data-until-started]")?.removeAttribute("hidden");
    tile.dataset.job = job.id;
    tile.dataset.status = "adding";
    // The tile's own word: a stacklet that prepares is set up, not started.
    tile.querySelector(".tile-act").replaceChildren(
      ...this.progress(tile.dataset.adding || this.dataset.adding, job.steps));
  }

  /** An event of some job: a start, a step, or the end. */
  follow(event) {
    if (event.type === "job.started") {
      if (event.data.action === "up") {
        this.started(this.tileWhere("data-id", event.data.target), event.data);
      }
      return;
    }
    if (event.type === "job.ended") {
      this.widget.load({ quiet: true });
      return;
    }
    const tile = this.tileWhere("data-job", event.subject.slice("job/".length));
    if (tile && event.type.startsWith("step.")) this.step(tile.querySelector(".tile-steps"), event);
  }

  /** A step started, ended or failed: a new line, or the line it started.
   *  A step the tile shows already, from the snapshot it was rendered
   *  with, is named by the same cursor (`data-step`) and gets no second line.
   *  A step's progress (`step.progress`, a download) sets the percent on the
   *  line it reports for. */
  step(list, event) {
    if (!list) return;
    const started = event.data.started &&
      (list.querySelector(`[data-step="${CSS.escape(String(event.data.started))}"]`) ||
       [...list.querySelectorAll('.tile-step[data-status="running"]')]
         .find((li) => li.querySelector(".tile-step-text")?.textContent === this.stepText(event.data)));
    if (event.type === "step.progress") {
      if (started) showPercent(started, event.data.percent);
      return;
    }
    const state = { "step.started": "running", "step.done": "done", "step.failed": "failed" }[event.type];
    const shown = list.querySelector(`[data-step="${CSS.escape(String(event.id))}"]`) || started;
    if (shown) {
      shown.dataset.status = state;
      // A percent belongs to a running step: one that ended at 40 % stopped there.
      if (state !== "running") dropPercent(shown);
      return;
    }
    list.append(stepLine({ state, id: event.id, text: this.stepText(event.data) }));
  }

  /** "Starting…" with the steps so far. */
  progress(label, steps = []) {
    return [
      h("p", { class: "tile-state" }, h("span", { class: "dot", "data-status": "busy", "aria-hidden": "true" }), label),
      h("ol", { class: "tile-steps" }, steps.map((step) => stepLine({ ...step, text: this.stepText(step) }))),
    ];
  }
}

/** One step's line, as the server renders it (`stack_widget._step_html`):
 *  its text, and its percent while it reports one. */
function stepLine(step) {
  const line = h("li", { class: "tile-step", "data-status": step.state, "data-step": step.id || null },
                 h("span", { class: "tile-step-text" }, step.text));
  if (step.state === "running" && typeof step.percent === "number") showPercent(line, step.percent);
  return line;
}

/** A step's line without its percent, once the step ended. */
function dropPercent(line) {
  delete line.dataset.percent;
  line.querySelector(".tile-percent")?.remove();
}

/** The percent on a step's line: `40 %` beside its text. */
function showPercent(line, percent) {
  line.dataset.percent = String(Math.round(percent));
  let shown = line.querySelector(".tile-percent");
  if (!shown) {
    shown = h("span", { class: "tile-percent" });
    line.append(" ", shown);
  }
  shown.textContent = `${Math.round(percent)} %`;
}

