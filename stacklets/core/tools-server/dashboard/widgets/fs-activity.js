// <fs-activity>: what changed in the memory, a sentence each (README.md).
//
// The page arrives with the entries rendered by the server
// (`activity_widget.py`); this element enhances them: today's times stay
// relative ("2 minutes ago"), "Show more" reads more in place, and a
// refresh from the shell renders the new answer with the same markup the
// server writes. The sentences come worded from the server, so the page
// and this script never say a change differently.

const WHOLE_ICON = // the same icon activity_widget.py draws for a change to the whole memory
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="m12 3 8 4.5-8 4.5-8-4.5z"/><path d="m4 12 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/></svg>';
const TICK_MS = 30_000;

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== ""));
  return node;
}

class FsActivity extends HTMLElement {
  connectedCallback() {
    this.seen = new Set(this.keys());
    this.addEventListener("click", (event) => this.more(event));
    this.tick();
    this.ticker = setInterval(() => this.tick(), TICK_MS);
  }

  disconnectedCallback() { clearInterval(this.ticker); }

  get widget() { return this.closest("fs-widget"); }

  keys() {
    return [...this.querySelectorAll(".entry time")].map((t) => t.getAttribute("datetime") + t.parentElement.textContent);
  }

  /** "Show more": at a smaller size it opens the large size; when the
   * memory holds more, it reads the next ones in place. */
  more(event) {
    const link = event.target.closest(".fs-more");
    if (!link) return;
    event.preventDefault();
    this.widget.setAttribute("size", "large");
    if ((link.dataset.show || "").includes("large")) {
      this.widget.load({ params: { limit: link.dataset.limit } });
    }
  }

  /** Today's times as how long ago, in the page's language. */
  tick() {
    const words = new Intl.RelativeTimeFormat(document.documentElement.lang || "en", { numeric: "auto" });
    for (const time of this.querySelectorAll("time[data-relative]")) {
      const minutes = Math.floor((Date.now() - Date.parse(time.getAttribute("datetime"))) / 60_000);
      time.textContent = minutes < 1 ? this.dataset.now
        : minutes < 60 ? words.format(-minutes, "minute")
        : words.format(-Math.floor(minutes / 60), "hour");
    }
  }

  /** Render an answer of `/api/activity`: the markup activity_widget.html() writes. */
  render(view) {
    this.dataset.now = view.now;
    this.replaceChildren(...[
      h("p", { class: "glance", "data-show": view.glance.show }, view.glance.text),
      ...view.days.map((day) => h("section", { class: "day", "data-show": day.show },
        h("h3", { class: "day-label", "data-show": "medium large" }, day.label),
        h("ol", { class: "entries" }, day.entries.map((entry) => this.entry(entry))))),
      view.more ? h("a", { class: "fs-more", "data-show": view.more.show, "data-limit": view.more.limit,
                           href: view.more.href }, view.more.label) : null,
    ].filter(Boolean));
    const before = this.seen;
    this.seen = new Set(this.keys());
    for (const time of this.querySelectorAll(".entry time")) {
      if (!before.has(time.getAttribute("datetime") + time.parentElement.textContent)) {
        time.parentElement.classList.add("new");
      }
    }
    this.tick();
  }

  entry(entry) {
    const avatar = h("span", { class: "avatar", "data-tone": entry.who ? entry.who.tone : "machine",
                               "aria-hidden": "true" });
    if (entry.who) avatar.textContent = entry.who.initial;
    else avatar.innerHTML = WHOLE_ICON;
    const cards = entry.cards.flatMap((card) => [
      card.sep,
      card.href ? h("a", { class: "card", "data-type": card.type, href: card.href }, card.title)
                : h("span", { class: "card", "data-type": card.type }, card.title)]);
    return h("li", { class: "entry", "data-show": entry.show }, avatar,
      h("p", { class: "sentence" },
        entry.who ? [h("b", { class: "name" }, entry.who.name), " "] : null,
        entry.text, cards.length ? [": ", cards] : null, entry.rest),
      h("time", { class: "when", datetime: entry.at, "data-relative": entry.relative }, entry.when));
  }
}

customElements.define("fs-activity", FsActivity);
