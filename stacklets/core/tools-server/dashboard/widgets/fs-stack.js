// <fs-stack>: what runs, and what the family can add (README.md).
//
// The page arrives with the widget rendered by the server
// (`stack_widget.py`); this element renders a refreshed view with the
// same markup, and makes Add work: it posts the job to the tools server
// (`POST /dashboard/api/jobs`), which runs `stack up` through the host API.
// Without JavaScript a tile shows its terminal command instead, which is
// what Add runs. The words come from the server, in the page's language.

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== ""));
  return node;
}

class FsStack extends HTMLElement {
  connectedCallback() {
    this.addEventListener("click", (event) => {
      const button = event.target.closest(".tile-add");
      if (button) this.add(button);
    });
  }

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

  /** A job runs for this tile now. */
  started(tile, job) {
    tile.dataset.job = job.id;
    tile.dataset.state = "adding";
  }

  /** Render an answer of `/api/stack`: the markup stack_widget.html() writes. */
  render(view) {
    this.replaceChildren(...[
      h("p", { class: "stack-glance", "data-show": view.glance.show }, view.glance.text),
      view.running.length ? h("ul", { class: "stack-list", "data-show": "medium large" },
        view.running.map((row) => this.row(row, view))) : null,
      view.more ? h("p", { class: "stack-more", "data-show": view.more.show }, view.more.text) : null,
      view.to_add.length ? h("section", { class: "stack-add", "data-show": "large" },
        h("h3", { class: "stack-heading" }, view.to_add_title),
        h("div", { class: "tiles" }, view.to_add.map((tile) => this.tile(tile)))) : null,
    ].filter(Boolean));
  }

  row(row, view) {
    return h("li", { class: "stack-row", "data-id": row.id, "data-state": row.state },
      h("span", { class: "dot", "data-state": row.state, "aria-hidden": "true" }),
      h("span", { class: "stack-name" }, row.name),
      h("span", { class: "stack-state" }, row.label),
      row.href ? h("a", { class: "stack-open", href: row.href }, row.open) : null,
      row.state === "not_answering" ? h("a", { class: "fs-retry stack-check", href: view.retry_href }, view.check_again) : null,
      row.command ? h("p", { class: "stack-command" }, h("span", {}, row.in_terminal), " ", h("code", {}, row.command)) : null);
  }

  tile(tile) {
    return h("article", { class: "tile", "data-id": tile.id, "data-add": Boolean(tile.add) },
      h("h4", { class: "tile-top" }, h("span", { class: "tile-name" }, tile.name),
        tile.beta ? [" ", h("span", { class: "tile-beta" }, tile.beta)] : null),
      h("p", { class: "tile-description" }, tile.description),
      h("div", { class: "tile-act" },
        tile.add ? h("button", { class: "tile-add", type: "button", "data-target": tile.id }, tile.add) : null,
        h("p", { class: "tile-terminal" }, h("span", {}, tile.in_terminal), " ",
          h("code", { class: "tile-command" }, tile.command))));
  }
}

customElements.define("fs-stack", FsStack);
