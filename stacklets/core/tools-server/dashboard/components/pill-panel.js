// pill-panel.js: the short, read-only profile of one person that opens
// under the family's pills when a pill is clicked (pill-panel.css says how
// it looks).
//
//   import { PillPanel, drawPanel, panelView, plainClick } from "./pill-panel.js";
//   const panel = new PillPanel({
//     read: (id) => fetch(`/dashboard/api/person/${id}`).then((r) => r.ok ? r.json() : null),
//     show: (view, pill) => { const el = drawPanel(view, { onClose }); pill.after(el); return el; },
//     hide: () => document.querySelector(".fs-pill-panel")?.remove(),
//   });
//   link.addEventListener("click", (event) => {
//     if (!plainClick(event)) return;          // a new tab opens the page itself
//     event.preventDefault();
//     panel.toggle("homer", link);
//   });
//   document.addEventListener("keydown", (event) => panel.key(event.key));
//
// Each pill is a link to the person's profile page, so a long-press, a new
// tab or a browser without JavaScript opens the page. A plain click opens
// this panel instead: the header block (name, relation, "Looks after
// famstack", the memory link), "1 device waits to join" for an admin and
// the Mac, the person's devices without any button, and "Settings and
// devices" to the page when it holds more for this viewer.
//
// The panel draws only what `GET /dashboard/api/person/<id>` answers
// (person_profile/panel.py), words included. That answer already holds only
// what the profile page's rule lets this viewer see (person_profile/sees.py),
// so the panel can never show more than the page: Marge's phone on Homer's
// pill reads `devices: null` and gets the header block and "Homer's devices
// show on Homer's own phone.", no device and no link.
//
//   click pill ──► read(id) ──► panelView(answer) ──► show(view, pill)
//                                                     focus moves into it
//   click pill again, ×, Escape ──► hide() ──► focus back on the pill
//
// Opening the panel changes nothing else. Picking whom the Mac greets is the
// profile page's "This is me", never a click here, so a pill click leaves
// the remembered viewer (family.js) as it was.

// ---- What the panel shows ----------------------------------------------------

/** What the panel draws for `answer`, the profile's JSON read; pure.
 *
 * Returns `{ name, relation, admin, memory: {label, href}, waiting, devices,
 * why, route, close }`. `devices` is null whenever the answer lists none
 * (`answer.devices` is null), whatever its words say; otherwise `{ heading,
 * rows: [{ name, when, stale, tag }], empty }`. `waiting` is `{ line, href }`
 * only when at least one browser waits and the answer words it; `route` is
 * `{ label, href }` only when the answer words it.
 *
 * Example, Marge's phone on Homer's pill:
 *
 *     panelView({ name: "Homer Simpson", relation: "Partner", devices: null, ...,
 *                 said: { why: "Homer's devices show on Homer's own phone.", route: null, ... } })
 *     // { name: "Homer Simpson", relation: "Partner", devices: null,
 *     //   why: "Homer's devices show on Homer's own phone.", route: null, waiting: null, ... }
 */
export function panelView(answer) {
  const said = answer.said || {};
  return {
    name: answer.name,
    relation: answer.relation,
    admin: answer.admin ? said.admin || "" : "",
    memory: { label: said.memory || "", href: answer.memory },
    waiting: answer.waiting > 0 && said.waiting
      ? { line: said.waiting, href: `${answer.profile}#waiting` } : null,
    devices: devicesOf(answer, said),
    why: said.why || null,
    route: said.route ? { label: said.route, href: answer.profile } : null,
    close: said.close || "",
  };
}

/** True for a click that should open the panel: the main button with no
 * modifier key. Every other click (a new tab, a middle click) is left to
 * the browser, which opens the profile page the pill links to.
 *
 *     plainClick({ button: 0 })                  // true
 *     plainClick({ button: 0, metaKey: true })   // false: a new tab
 */
export function plainClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

// ---- Opening and closing ---------------------------------------------------

/** The panel's state: which person it shows and the element that opened it.
 * It reads, shows and hides through the three functions it is given, so it
 * runs without a page; `drawPanel` and family-bar.js give it the page.
 *
 *     const panel = new PillPanel({ read, show, hide });
 *     await panel.toggle("homer", homersPill);   // true: open, focus inside
 *     panel.key("Escape");                       // true: closed, focus on the pill
 */
export class PillPanel {
  /** `read(id)` resolves to the profile's JSON or null; `show(view, opener)`
   * draws the panel and returns its element; `hide()` removes it. */
  constructor({ read, show, hide }) {
    this.read = read;
    this.show = show;
    this.hide = hide;
    /** The id of the person shown, or null when the panel is closed. */
    this.open = null;
    this.opener = null;
  }

  /** Open the panel of `id` under `opener`, or close it when it shows `id`
   * already. Resolves to false when the profile could not be read: then
   * nothing opens, and the caller lets the link open the page. */
  async toggle(id, opener) {
    if (this.open === id) {
      this.close();
      return true;
    }
    const answer = await this.read(id).catch(() => null);
    if (!answer) return false;
    if (this.open) this.close({ refocus: false });
    const element = this.show(panelView(answer), opener);
    this.open = id;
    this.opener = opener;
    opener.setAttribute("aria-expanded", "true");
    element.focus();
    return true;
  }

  /** Close the panel and give the focus back to what opened it, so a
   * keyboard user goes on from where they were. Does nothing when closed. */
  close({ refocus = true } = {}) {
    if (!this.open) return;
    this.hide();
    this.opener.setAttribute("aria-expanded", "false");
    if (refocus) this.opener.focus();
    this.open = null;
    this.opener = null;
  }

  /** A key pressed anywhere on the page: Escape closes an open panel.
   * True when the key closed it. */
  key(name) {
    if (name !== "Escape" || !this.open) return false;
    this.close();
    return true;
  }
}

// ---- Drawing ---------------------------------------------------------------

/** The panel's element for `view` (from `panelView`), read-only: links and
 * the × button, no other control. `onClose` runs when × is clicked. The
 * element takes the focus itself (`tabindex="-1"`), so the reader starts
 * at its name. Colours come from tokens.css through pill-panel.css.
 *
 *     drawPanel(panelView(homerOnMargesPhone), { onClose })
 *     // <section class="fs-pill-panel" tabindex="-1" aria-label="Homer Simpson">
 *     //   × · Homer Simpson · Partner · Looks after famstack · Homer's memory
 *     //   <p class="fs-pill-panel-why">Homer's devices show on Homer's own phone.</p>
 *     // </section>
 */
export function drawPanel(view, { onClose }) {
  const panel = el("section", "fs-pill-panel");
  panel.tabIndex = -1;
  panel.setAttribute("aria-label", view.name);
  const close = el("button", "fs-pill-panel-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", view.close);
  close.addEventListener("click", onClose);
  panel.append(close, header(view));
  if (view.waiting) panel.append(link("fs-pill-panel-waiting", view.waiting.line, view.waiting.href));
  if (view.devices) panel.append(devices(view.devices));
  if (view.why) panel.append(el("p", "fs-pill-panel-why", view.why));
  if (view.route) panel.append(link("fs-pill-panel-route", view.route.label, view.route.href));
  return panel;
}

// ---- Details -----------------------------------------------------------------

/** The rows of the devices part, or null when the answer lists none. */
function devicesOf(answer, said) {
  if (!Array.isArray(answer.devices)) return null;
  return {
    heading: said.heading || "",
    rows: answer.devices.map((device) => ({
      name: device.label || device.name, when: device.when || "", stale: Boolean(device.stale),
      tag: device.this ? said.this_device || "" : "",
    })),
    empty: answer.devices.length ? "" : said.empty || "",
  };
}

/** The header block, as on the page: the avatar, the name, the relation,
 * the admin badge and the memory link. */
function header(view) {
  const head = el("div", "fs-pill-panel-head");
  const avatar = el("span", "fs-avatar", view.name.slice(0, 1).toUpperCase());
  avatar.setAttribute("aria-hidden", "true");
  const text = el("div", "");
  text.append(el("p", "fs-pill-panel-name", view.name));
  const meta = el("p", "fs-pill-panel-meta");
  meta.append(el("span", "", view.relation));
  if (view.admin) meta.append(el("span", "fs-pill-panel-badge", view.admin));
  if (view.memory.label) meta.append(link("fs-pill-panel-memory", view.memory.label, view.memory.href));
  text.append(meta);
  head.append(avatar, text);
  return head;
}

/** The devices part: its heading and one line per device, green; the
 * when of a device not seen for 30 days in red. */
function devices(part) {
  const box = el("div", "fs-pill-panel-devices");
  box.append(el("p", "fs-pill-panel-heading", part.heading));
  if (part.empty) box.append(el("p", "fs-pill-panel-empty", part.empty));
  const list = el("ul", "");
  for (const row of part.rows) {
    const item = el("li", "");
    item.append(el("b", "", row.name));
    if (row.tag) item.append(el("span", "fs-pill-panel-tag", row.tag));
    item.append(el("span", row.stale ? "fs-pill-panel-stale" : "", row.when));
    list.append(item);
  }
  if (part.rows.length) box.append(list);
  return box;
}

function link(className, text, href) {
  const a = el("a", className, text);
  a.href = href;
  return a;
}

function el(tag, className, text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}
