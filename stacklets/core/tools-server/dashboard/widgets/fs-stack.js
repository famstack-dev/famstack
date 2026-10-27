// <fs-stack>: what runs, and what the family can add (README.md).
//
// The page arrives with the widget rendered by the server
// (`stack_widget.py`); this element renders a refreshed view with the
// same markup, and makes Add work: it posts the job to the tools server
// (`POST /dashboard/api/jobs`), which runs `stack up` through the host API.
// Without JavaScript a tile shows its terminal command instead, which is
// what Add runs. The words come from the server, in the page's language.
//
// A tile follows its job on the page's event stream (`../live.js`): when a
// job starts for it, here or in another browser, the tile shows its steps
// as they happen; when any job ends, the widget reads its view again, so
// the tile shows how it went and the list what runs now. The "Setting up"
// widget is this element with other markup (`fs-setup.js` extends it).

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

  /** Where jobs are started: next to the widget's own endpoint. */
  get jobs() { return new URL("jobs", new URL(this.widget.getAttribute("src"), location.href)); }

  /** Start `stack up` for the tile's stacklet as a job. */
  async add(button) {
    button.disabled = true;
    try {
      const response = await fetch(this.jobs, {
        method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ action: "up", target: button.dataset.target }),
      });
      if (!response.ok) throw new Error(String(response.status));
      this.started(button.closest(".tile"), await response.json());
    } catch {
      button.disabled = false;
    }
  }

  /** A job runs for this tile now: show it, once, with its steps so far. */
  started(tile, job) {
    if (!tile || tile.dataset.job === job.id) return;
    tile.dataset.job = job.id;
    tile.dataset.status = "adding";
    tile.querySelector(".tile-act").replaceChildren(...this.progress(this.dataset.adding, job.steps));
  }

  /** An event of some job: a start, a step, or the end. */
  follow(event) {
    if (event.type === "job.started") {
      if (event.data.action === "up") {
        this.started(this.querySelector(`.tile[data-id="${CSS.escape(event.data.target)}"]`), event.data);
      }
      return;
    }
    if (event.type === "job.ended") {
      this.widget.load({ quiet: true });
      return;
    }
    const tile = this.querySelector(`.tile[data-job="${CSS.escape(event.subject.slice("job/".length))}"]`);
    if (tile && event.type.startsWith("step.")) this.step(tile.querySelector(".tile-steps"), event);
  }

  /** A step started, ended or failed: a new line, or the line it started. */
  step(list, event) {
    if (!list) return;
    const state = { "step.started": "running", "step.done": "done", "step.failed": "failed" }[event.type];
    const text = event.data.text;
    const started = event.data.started &&
      (list.querySelector(`[data-step="${CSS.escape(String(event.data.started))}"]`) ||
       [...list.querySelectorAll('.tile-step[data-status="running"]')].find((li) => li.textContent === text));
    if (started) {
      started.dataset.status = state;
      return;
    }
    list.append(h("li", { class: "tile-step", "data-status": state, "data-step": event.id }, text));
  }

  /** "Adding…" with the steps so far. */
  progress(label, steps = []) {
    return [
      h("p", { class: "tile-state" }, h("span", { class: "dot", "data-status": "busy", "aria-hidden": "true" }), label),
      h("ol", { class: "tile-steps" }, steps.map((s) => h("li", { class: "tile-step", "data-status": s.state }, s.text))),
    ];
  }

  /** Render an answer of `/api/stack`: the markup stack_widget.html() writes. */
  render(view) {
    this.dataset.adding = view.adding;
    this.replaceChildren(...[
      h("p", { class: "stack-glance", "data-show": view.glance.show }, view.glance.text),
      view.latest ? h("p", { class: "stack-latest", "data-show": view.latest.show }, view.latest.text) : null,
      view.running.length ? h("ul", { class: "stack-list", "data-show": "medium large" },
        view.running.map((row) => this.row(row, view))) : null,
      view.more ? h("p", { class: "stack-more", "data-show": view.more.show }, view.more.text) : null,
      view.to_add.length ? h("section", { class: "stack-add", "data-show": "large" },
        h("h3", { class: "stack-heading" }, view.to_add_title),
        h("div", { class: "tiles" }, view.to_add.map((tile) => this.tile(tile, view)))) : null,
    ].filter(Boolean));
  }

  row(row, view) {
    return h("li", { class: "stack-row", "data-id": row.id, "data-status": row.state },
      h("span", { class: "dot", "data-status": row.state, "aria-hidden": "true" }),
      h("span", { class: "stack-name" }, row.name),
      h("span", { class: "stack-state" }, row.label),
      row.href ? h("a", { class: "stack-open", href: row.href }, row.open) : null,
      row.state === "not_answering" ? h("a", { class: "fs-retry stack-check", href: view.retry_href }, view.check_again) : null,
      row.command ? h("p", { class: "stack-command" }, h("span", {}, row.in_terminal), " ", h("code", {}, row.command)) : null);
  }

  tile(tile, view) {
    return h("article", { class: "tile", "data-id": tile.id, "data-status": tile.state, "data-job": tile.job || null },
      h("h4", { class: "tile-top" }, h("span", { class: "tile-name" }, tile.name),
        tile.beta ? [" ", h("span", { class: "tile-beta" }, tile.beta)] : null),
      h("p", { class: "tile-description" }, tile.description),
      h("div", { class: "tile-act" }, this.act(tile, view)));
  }

  /** What a tile offers or shows, by its state: stack_widget._tile_act_html(). */
  act(tile, view) {
    if (tile.state === "adding") return this.progress(tile.label, tile.steps);
    return [
      tile.label ? h("p", { class: "tile-state" },
        h("span", { class: "dot", "data-status": tile.state, "aria-hidden": "true" }), tile.label) : null,
      tile.add ? h("button", { class: "tile-add", type: "button", "data-target": tile.id }, tile.add) : null,
      tile.href ? h("a", { class: "stack-open", href: tile.href }, tile.open) : null,
      tile.state === "not_answering" ? h("a", { class: "fs-retry stack-check", href: view.retry_href }, view.check_again) : null,
      tile.state === "running" ? null : h("p", { class: "tile-terminal" }, h("span", {}, tile.in_terminal), " ",
        h("code", { class: "tile-command" }, tile.command)),
    ];
  }
}

if (!customElements.get("fs-stack")) customElements.define("fs-stack", FsStack);
