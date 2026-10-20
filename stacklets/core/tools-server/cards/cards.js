// The card browser: find cards on the left, open them side by side, walk
// their links, see the neighbourhood of the focused card as a graph.
//
// Everything is read from the memory stacklet's cards API through core
// (`/cards/api/...`). Vault text is escaped before anything else touches it;
// the small markdown renderer below only adds the tags it writes itself.

"use strict";

const API = "/cards/api";

// ── Words ─────────────────────────────────────────────────────────────────

const WORDS = {
  en: {
    memory: "Memory · Cards", search: "Search the family memory", viewas: "View as",
    everyone: "Everyone (admin)", types: "Kinds", all: "all", relevant: "relevant", newest: "newest",
    anyone: "Anyone", person: "Person", from: "From", to: "To", cards: (n) => `${n} cards`, details: "Details", history: "History",
    linksTo: "Links to", linkedFrom: "Linked from", nothing: "Nothing written here yet.",
    around: "Around this card",
    links: "links", people: "people", versions: "versions", bothWays: "Links both ways",
    nothingLinksHere: "Nothing links here yet.", linksNowhere: "It links to nothing yet.",
    welcomeA: "What the family", welcomeB: "remembers", welcomeSub:
      "Search on the left or pick a card, then walk along its links.",
    recently: "Changed lately", noGraph: "Open a card to see what it links to.",
    unreachable: "The family memory does not answer. Is the memory stacklet running? (stack up memory)",
    file: "File", id: "Card", changedBy: (who, day) => `${who} · ${day}`,
    close: "Close", view: "View", views: "Compiled from this card", openWiki: "Open in the wiki",
    behind: "behind this card, whose own text counts",
    report: "Format", toFix: (n) => `${n} to fix`, nothingToFix: "Nothing to fix: the memory meets its format.",
    reportSub: (n) => `Where the memory does not meet its format yet, over all ${n} cards (as the admin).`,
    noCardId: "Without a card_id", sharedIds: "One id, several files",
    deadLinks: "Links to nothing in the memory repo", noType: "Not a card type", notThere: "is not there",
  },
  de: {
    memory: "Gedächtnis · Karten", search: "Im Familiengedächtnis suchen", viewas: "Ansicht als",
    everyone: "Alle (Admin)", types: "Arten", all: "alle", relevant: "passend", newest: "neueste",
    anyone: "Alle Personen", person: "Person", from: "Von", to: "Bis", cards: (n) => `${n} Karten`, details: "Details", history: "Verlauf",
    linksTo: "Verweist auf", linkedFrom: "Verwiesen von", nothing: "Hier steht noch nichts.",
    around: "Rund um diese Karte",
    links: "Verweise", people: "Personen", versions: "Versionen", bothWays: "Verweist in beide Richtungen",
    nothingLinksHere: "Noch nichts verweist hierher.", linksNowhere: "Verweist noch auf nichts.",
    welcomeA: "Was die Familie", welcomeB: "erinnert", welcomeSub:
      "Links suchen oder eine Karte wählen und ihren Verweisen folgen.",
    recently: "Zuletzt geändert", noGraph: "Eine Karte öffnen, um ihre Verweise zu sehen.",
    unreachable: "Das Familiengedächtnis antwortet nicht. Läuft das memory-Stacklet? (stack up memory)",
    file: "Datei", id: "Karte", changedBy: (who, day) => `${who} · ${day}`,
    close: "Schließen", view: "Ansicht", views: "Aus dieser Karte zusammengestellt", openWiki: "Im Wiki öffnen",
    behind: "älter als diese Karte, ihr eigener Text gilt",
    report: "Format", toFix: (n) => `${n} zu beheben`, nothingToFix: "Nichts zu beheben: das Gedächtnis hat sein Format.",
    reportSub: (n) => `Wo das Gedächtnis sein Format noch nicht hat, über alle ${n} Karten (als Admin).`,
    noCardId: "Ohne card_id", sharedIds: "Eine ID, mehrere Dateien",
    deadLinks: "Verweise ins Leere", noType: "Keine Kartenart", notThere: "gibt es nicht",
  },
};
const LANG = (navigator.language || "en").slice(0, 2) === "de" ? "de" : "en";
const T = WORDS[LANG];

const TYPE_NAMES = {
  en: { note: "Note", bookmark: "Bookmark", document: "Document", diary: "Diary", list: "List",
        person: "Person", topic: "Topic", correspondent: "Correspondent", email: "Email", page: "Page",
        index: "Index" },
  de: { note: "Notiz", bookmark: "Lesezeichen", document: "Dokument", diary: "Tagebuch", list: "Liste",
        person: "Person", topic: "Thema", correspondent: "Korrespondent", email: "E-Mail", page: "Seite",
        index: "Übersicht" },
}[LANG];
const KNOWN_TYPES = ["note", "bookmark", "document", "diary", "list", "person", "topic",
                     "correspondent", "email"];
const typeColor = (type) => `var(--t-${KNOWN_TYPES.includes(type) ? type : "page"})`;
const typeName = (type) => TYPE_NAMES[type] || type;

// ── State ─────────────────────────────────────────────────────────────────

const state = {
  q: "", type: "", person: "", since: "", until: "", sort: "rank", audience: "",
  report: false,   // the format report at the front of the board
  card: "",        // the card in the middle of the walk, and the one the graph is about
  cards: new Map(), // id -> full card, as last read
};

// ── Small helpers ─────────────────────────────────────────────────────────

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) if (c != null) el.append(c);
  return el;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function audienceQuery(extra = {}) {
  const params = new URLSearchParams(extra);
  if (state.audience) params.set("audience", state.audience);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

const refPath = (ref) => String(ref).split("/").map(encodeURIComponent).join("/");

async function api(path, params = {}) {
  const r = await fetch(`${API}/${path}${audienceQuery(params)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// A `/go` link opens on core's link resolver, which sends it on to Paperless or the wiki.
const goUrl = (path) => `${location.protocol}//${location.hostname}:42000${path}`;

const DAY = new Intl.DateTimeFormat(LANG, { day: "numeric", month: "short", year: "numeric" });
function niceDay(day) {
  if (!day) return "";
  const d = new Date(`${day}T12:00:00`);
  return Number.isNaN(d.getTime()) ? day : DAY.format(d);
}

function kindLabel(type) {
  return h("span", { class: "kind label" }, h("span", { class: "dot", style: `--c:${typeColor(type)}` }),
           typeName(type));
}

// ── Markdown, safely ──────────────────────────────────────────────────────
// The text is escaped first; only the tags written here appear in the HTML.

function inline(text, card) {
  // Media is held out of the text until the end, so the markup below never
  // reads into an address (an event id's underscores are no emphasis).
  const held = [];
  const hold = (html) => `\u0000${held.push(html) - 1}\u0000`;
  let out = escapeHtml(text);
  out = out.replace(/!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/g, (whole, target) => {
    const file = mediaPath(target.trim(), card?.path || "");
    return file ? hold(mediaElement(file)) : whole;
  });
  out = out.replace(/!\[([^\]]*)\]\([^)]*\)/g, (_, alt) =>
    `<span class="figure">▣ ${alt || "image"}</span>`);
  out = out.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, target, label) =>
    `<a class="card-link" data-ref="${target.trim()}">${label || target}</a>`);
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    if (/^https?:\/\//.test(href)) {
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
    const file = mediaPath(href, card?.path || "");
    if (file) return hold(`<a href="${escapeHtml(mediaUrl(file))}" target="_blank" rel="noopener">${label}</a>`);
    const ref = resolvePath(href, card?.path || "");
    return ref ? `<a class="card-link" data-ref="${ref}">${label}</a>` : label;
  });
  // <https://...>, already escaped to &lt;...&gt;: a link showing its address.
  out = out.replace(/&lt;(https?:\/\/[^\s&]+(?:&amp;[^\s&]+)*)&gt;/g, (_, href) =>
    `<a href="${href}" target="_blank" rel="noopener noreferrer">${href.replace(/^https?:\/\//, "")}</a>`);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => `<strong>${a || b}</strong>`);
  out = out.replace(/(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => held[Number(i)]);
}

// ── Media ─────────────────────────────────────────────────────────────────
// A record embeds its photo or recording from the media archive
// (`![[/media/2026/10/<event id>.png]]`, ADR-017 §3). The cards API serves
// the file to whoever may read the card, so the address carries "view as".

const IMAGE = new Set(["png", "jpg", "jpeg", "gif", "webp", "heic"]);
const AUDIO = new Set(["m4a", "mp3", "wav", "ogg"]);
const VIDEO = new Set(["mp4", "mov", "webm"]);

// The file inside the media archive a link points at (`2026/10/x.png`), or "".
function mediaPath(href, here) {
  const path = vaultPath(href, here);
  return path.startsWith("media/") && /^[\w./-]+$/.test(path) && !path.split("/").includes("..")
    ? path.slice("media/".length) : "";
}

const mediaUrl = (file) => `${API}/media/${refPath(file)}${audienceQuery()}`;

function mediaElement(file) {
  const ext = file.split(".").pop().toLowerCase();
  const src = escapeHtml(mediaUrl(file));
  const name = escapeHtml(file.split("/").pop());
  if (IMAGE.has(ext)) return `<img class="media" src="${src}" alt="${name}" loading="lazy">`;
  if (AUDIO.has(ext)) return `<audio class="media" controls preload="metadata" src="${src}"></audio>`;
  if (VIDEO.has(ext)) return `<video class="media" controls preload="metadata" src="${src}"></video>`;
  return `<a class="file" href="${src}" target="_blank" rel="noopener">${name} ↗</a>`;
}

function resolvePath(href, here) {
  const path = vaultPath(href, here);
  if (!path) return "";
  return path.endsWith(".md") ? path : `${path}.md`;
}

// The vault path a link points at, from the card at `here`; "" for the web or an anchor.
function vaultPath(href, here) {
  if (/^[a-z]+:/i.test(href) || href.startsWith("#")) return "";
  let path = href.split("#")[0];
  if (!path) return "";
  if (!path.startsWith("/")) {
    const parts = here.split("/").slice(0, -1);
    for (const piece of path.split("/")) {
      if (piece === "..") parts.pop();
      else if (piece !== ".") parts.push(piece);
    }
    path = parts.join("/");
  }
  return path.replace(/^\/+/, "");
}

function markdown(text, card) {
  const lines = String(text || "").replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<a id="[^"]*"><\/a>/g, "").split("\n");
  const html = [];
  let list = null;
  let para = [];
  const flush = () => {
    if (para.length) html.push(`<p>${inline(para.join(" "), card)}</p>`);
    para = [];
  };
  const closeList = () => {
    if (list) html.push(`</${list}>`);
    list = null;
  };
  let table = [];
  const flushTable = () => {
    // A markdown table: its header row, the rule under it, then the rows.
    const rows = table.filter((cells) => !cells.every((c) => /^:?-{2,}:?$/.test(c)));
    if (rows.length) {
      const [head, ...body] = rows;
      html.push(`<div class="table"><table><thead><tr>${head.map((c) => `<th>${inline(c, card)}</th>`).join("")}</tr></thead>`
        + `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c, card)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`);
    }
    table = [];
  };
  let quoted = [];
  const flushQuote = () => {
    if (!quoted.length) return;
    // An Obsidian callout (`> [!quote]- Original paste`) is a labelled box: its
    // title is the label, its type only when it has none. `-` starts it folded
    // and `+` open, as in Obsidian. Any other quote is a quote.
    const callout = quoted[0].match(/^\[!(\w+)\]([-+]?)\s*(.*)$/);
    if (!callout) {
      html.push(`<blockquote>${markdown(quoted.join("\n"), card)}</blockquote>`);
      quoted = [];
      return;
    }
    const [, type, fold, title] = callout;
    const label = `<span class="label">${title.trim() ? inline(title.trim(), card) : escapeHtml(type)}</span>`;
    const inner = markdown(quoted.slice(1).join("\n"), card);
    html.push(fold
      ? `<details class="callout"${fold === "+" ? " open" : ""}><summary>${label}</summary>${inner}</details>`
      : `<div class="callout">${label}${inner}</div>`);
    quoted = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!/^>/.test(line)) flushQuote();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*+]\s+(?:\[([ xX])\]\s+)?(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    const row = /^\s*\|.*\|\s*$/.test(line);
    if (!row) flushTable();
    if (!line.trim()) { flush(); closeList(); continue; }
    if (row) {
      flush(); closeList();
      table.push(line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      continue;
    }
    if (heading) {
      flush(); closeList();
      const level = heading[1].length <= 2 ? "h3" : "h4";
      html.push(`<${level}>${inline(heading[2], card)}</${level}>`);
    } else if (bullet || numbered) {
      flush();
      const want = bullet ? "ul" : "ol";
      if (list !== want) { closeList(); html.push(`<${want}>`); list = want; }
      const text = inline(bullet ? bullet[2] : numbered[1], card);
      const box = bullet?.[1];
      html.push(box === undefined ? `<li>${text}</li>`
        : `<li class="task${box === " " ? "" : " done"}"><span class="box"></span><span class="t">${text}</span></li>`);
    } else if (quote) {
      flush(); closeList();
      quoted.push(quote[1]);
      continue;
    } else {
      closeList();
      para.push(line.trim());
    }
  }
  flush(); closeList(); flushQuote(); flushTable();
  return html.join("");
}

function withoutTitle(text, title) {
  // The card's title is its heading already; a body that starts with it says it twice.
  const lines = String(text || "").split("\n");
  const first = lines.findIndex((l) => l.trim());
  if (first >= 0 && /^#{1,3}\s+/.test(lines[first])
      && lines[first].replace(/^#+\s+/, "").trim().toLowerCase() === String(title).trim().toLowerCase()) {
    lines.splice(first, 1);
  }
  return lines.join("\n");
}

function bodyOf(card, text) {
  const div = h("div", { class: "body" });
  if (!String(text || "").trim()) {
    div.append(h("p", { class: "empty-text" }, T.nothing));
    return div;
  }
  div.innerHTML = markdown(text, card);
  div.querySelectorAll("a.card-link").forEach((a) =>
    a.addEventListener("click", () => openCard(a.dataset.ref)));
  return div;
}

// ── People ────────────────────────────────────────────────────────────────
// `persons` holds person ids (`bart`), older cards their names (`Bart`).
// Either way a person is shown by their card's name, never by the bare id.

const knownPeople = new Map(); // id, name or first name, lowercased -> the person's card

const firstName = (title) => String(title || "").trim().split(/\s+/)[0];

function learnPeople(cards) {
  knownPeople.clear();
  for (const p of cards) {
    for (const key of [p.title, firstName(p.title)]) if (key) knownPeople.set(key.toLowerCase(), p);
  }
  // A slug wins over a name another person happens to share. `name` is
  // `person/<slug>` whatever id the card carries (`person-homer`).
  for (const p of cards) knownPeople.set((p.name || p.id).split("/").pop().toLowerCase(), p);
}

function personOf(value) {
  const known = knownPeople.get(String(value).toLowerCase());
  if (known) return { id: known.id, name: firstName(known.title) || known.title, title: known.title };
  const name = String(value);
  return { id: `person/${name.toLowerCase()}`, name: name.charAt(0).toUpperCase() + name.slice(1), title: name };
}

// ── People and tags ───────────────────────────────────────────────────────

function peopleRow(card) {
  const persons = (card.persons || []).map(personOf);
  if (!persons.length) return null;
  return h("div", { class: "people" }, persons.map((p) =>
    h("span", { class: "person", title: p.title, onclick: () => openCard(p.id) },
      h("span", { class: "av" }, p.name.slice(0, 1).toUpperCase()), p.name)));
}

// Tags under the date. `Person: <name>` tags repeat `persons` and are left
// out (ADR-017 §4). A tag searches for itself.
function tagsRow(card) {
  const tags = [].concat((card.meta || {}).tags || [])
    .map(String).filter((t) => t && !/^person\s*:/i.test(t));
  if (!tags.length) return null;
  return h("div", { class: "tags" }, tags.map((t) =>
    h("button", { class: "tag", title: t, onclick: () => searchFor(t) }, `#${t}`)));
}

function searchFor(text) {
  const q = document.getElementById("q");
  q.value = text;
  q.dispatchEvent(new Event("input"));
}

// ── The card's body ───────────────────────────────────────────────────────
// Each card type has one renderer, in the memory stacklet, and it writes
// markdown (ADR-016 §4); the cards API sends it as `rendered`, and the chat
// and the agent read the same. The header above shows the title, so the
// body starts below the rendered heading.

function renderBody(card) {
  return [peopleRow(card), bodyOf(card, withoutTitle(card.rendered ?? card.text, card.title))];
}

// The pages the wiki compiled from a card (a person's page, a topic's, a
// diary month): read with the card, marked as views, never opened as cards
// of their own. One compiled from an older version of the card says so.
function viewsOf(card) {
  if (!card.views?.length) return null;
  return h("div", { class: "linkset" }, h("h4", { class: "label" }, T.views),
    card.views.map((view) => h("details", { class: "more view" },
      h("summary", {}, h("span", { class: "kind label" }, T.view), h("span", {}, view.title),
        view.behind ? h("span", { class: "label" }, " · ", T.behind) : null),
      bodyOf({ id: card.id, path: view.path }, withoutTitle(view.text, view.title)),
      view.go ? h("a", { class: "action", href: goUrl(view.go), target: "_blank",
                         rel: "noopener noreferrer" }, T.openWiki, " ↗") : null)));
}

// ── A card ────────────────────────────────────────────────────────────────

function linkChips(cards) {
  return h("div", { class: "linkchips" }, cards.map((c) =>
    h("button", { class: "linkchip", style: `--c:${typeColor(c.type)}`, title: `${typeName(c.type)} · ${c.id}`,
                  onclick: () => openCard(c.id) },
      h("span", { class: "dot", style: `--c:${typeColor(c.type)}` }), h("span", {}, c.title))));
}

function details(card) {
  const meta = Object.entries(card.meta || {}).filter(([k]) => !["title", "type"].includes(k));
  return h("details", { class: "more" },
    h("summary", {}, h("span", { class: "label" }, T.details)),
    h("dl", { class: "facts-grid", style: "margin:0 0 12px" },
      h("dt", {}, T.id), h("dd", {}, h("code", {}, card.id)),
      h("dt", {}, T.file), h("dd", {}, h("code", {}, card.path)),
      meta.map(([k, v]) => [h("dt", {}, k), h("dd", {},
        Array.isArray(v) ? v.map((x) => typeof x === "object" ? JSON.stringify(x) : x).join(", ")
          : typeof v === "object" ? JSON.stringify(v) : String(v))])),
    card.changes.length ? h("div", {},
      h("span", { class: "label" }, T.history),
      h("ul", { class: "timeline" }, card.changes.map((c) => h("li", {},
        h("div", {}, c.subject),
        h("span", { class: "when" }, T.changedBy(c.who, niceDay(c.day))), " ",
        h("span", { class: "sha" }, `@${c.sha}`))))) : null);
}

function cardElement(card) {
  const last = card.changes[0];
  return h("article", { class: "card", style: `--c:${typeColor(card.type)}`, "data-id": card.id },
    h("header", {}, kindLabel(card.type), h("span", { class: "spacer" }),
      h("button", { class: "iconbtn", title: T.close, "aria-label": T.close, onclick: closeCard }, "✕")),
    h("h2", {}, card.title),
    h("div", { class: "strip" },
      card.day ? h("span", {}, niceDay(card.day)) : null,
      card.version ? h("span", { class: "v" }, `@${card.version}`) : null,
      last ? h("span", {}, last.who) : null,
      h("span", {}, `${card.links} ${T.links}`)),
    tagsRow(card),
    renderBody(card),
    viewsOf(card),
    details(card));
}

// ── The walk ──────────────────────────────────────────────────────────────
// One card in the middle, what links to it on the left, what it links to on
// the right. Opening a card from either side is a step: the address names
// the card, so back and forward walk the path and the address can be shared.

// A kind with more cards than this starts folded, so a person's fifty notes
// do not push the other kinds out of sight.
const OPEN_UP_TO = 6;

async function openCard(ref, { step = true } = {}) {
  let card;
  try {
    card = await api(`card/${refPath(ref)}`);
  } catch {
    return showError();
  }
  if (!card) return;
  state.cards.set(card.id, card);
  state.card = card.id;
  if (step) state.report = false;
  document.getElementById("report").setAttribute("aria-pressed", String(state.report));
  renderBoard();
  renderResults();
  renderGraph();
  remember({ step });
}

function closeCard() {
  state.card = "";
  renderBoard(); renderResults(); renderGraph(); remember({ step: true });
}

function renderBoard() {
  const board = document.getElementById("board");
  board.replaceChildren();
  if (state.report && lastReport) return board.append(reportElement(lastReport));
  const card = state.cards.get(state.card);
  if (!card) return renderWelcome(board);
  board.append(h("div", { class: "walk" },
    linkSide("from", T.linkedFrom, card.linked_from, T.nothingLinksHere),
    cardElement(card),
    linkSide("to", T.linksTo, card.links_to, T.linksNowhere)));
}

function linkSide(side, title, groups, none) {
  const total = groups.reduce((n, g) => n + g.count, 0);
  return h("aside", { class: `side ${side}` },
    h("h4", { class: "label" }, h("span", {}, title), h("span", { class: "n" }, total)),
    total ? groups.map((g) => h("details", { class: "group", open: g.count <= OPEN_UP_TO,
                                             style: `--c:${typeColor(g.type)}` },
      h("summary", {}, h("span", { class: "dot" }), h("span", {}, typeName(g.type)),
        h("span", { class: "n" }, g.count)),
      h("ul", {}, g.cards.map((c) => h("li", {},
        h("button", { class: "step", title: c.both ? T.bothWays : c.id, onclick: () => openCard(c.id) },
          h("span", { class: "t" }, c.title),
          c.both ? h("span", { class: "both", "aria-label": T.bothWays }, "⇄") : null,
          h("span", { class: "d" }, niceDay(c.day))))))))
      : h("p", { class: "hint" }, none));
}

async function renderWelcome(board) {
  const feed = h("ul", { class: "feed" });
  board.append(h("div", { class: "welcome" },
    h("span", { class: "label" }, T.memory),
    h("h1", {}, `${T.welcomeA} `, h("span", { class: "hl" }, T.welcomeB)),
    h("p", { class: "sub" }, T.welcomeSub),
    h("span", { class: "label" }, T.recently), h("div", { style: "height:10px" }), feed));
  let history;
  try { history = await api("history", { limit: 12 }); } catch { return showError(); }
  for (const change of history?.changes || []) {
    feed.append(h("li", {},
      h("div", { class: "who" }, h("b", {}, change.who), ` · ${niceDay(change.day)}`),
      linkChips(change.cards.slice(0, 6)),
      h("div", { class: "subj" }, change.subject)));
  }
}

// ── The format report ─────────────────────────────────────────────────────
// Where the memory does not meet ADR-016 and ADR-017 yet. It is the admin's:
// the API answers it for the whole memory, whoever the page is viewed as.

let lastReport = null;

async function loadReport() {
  try { lastReport = await api("report"); } catch { lastReport = null; }
  const n = lastReport?.findings;
  document.getElementById("report").textContent =
    n == null ? T.report : n ? `${T.report} · ${n}` : `${T.report} ✓`;
}

async function showReport(on) {
  state.report = on;
  document.getElementById("report").setAttribute("aria-pressed", String(on));
  if (on) await loadReport();
  renderBoard();
  remember({ step: true });
}

function reportElement(report) {
  const section = (title, rows) => rows.length ? h("div", { class: "linkset" },
    h("h4", { class: "label" }, title), h("ul", { class: "findings" }, rows)) : null;
  const opens = (ref, label) => h("button", { class: "linkchip", onclick: () => openCard(ref) },
    h("span", {}, label));
  return h("article", { class: "card report", "data-id": "report" },
    h("header", {}, h("span", { class: "kind label" }, T.report), h("span", { class: "spacer" }),
      h("button", { class: "iconbtn", title: T.close, "aria-label": T.close, onclick: () => showReport(false) },
        "✕")),
    h("h2", {}, report.findings ? T.toFix(report.findings) : T.nothingToFix),
    h("p", { class: "body hint" }, T.reportSub(report.cards)),
    section(T.noCardId, Object.entries(report.without_card_id).map(([type, n]) =>
      h("li", {}, kindLabel(type), h("span", { class: "n" }, n)))),
    section(T.sharedIds, report.shared_ids.map((shared) =>
      h("li", {}, h("code", {}, shared.id), shared.paths.map((path) => opens(path, path))))),
    section(T.deadLinks, report.links_to_nothing.map((dead) =>
      h("li", {}, opens(dead.card, dead.title), h("code", {}, dead.link),
        h("span", { class: "hint" }, `${dead.target} ${T.notThere}`)))),
    section(T.noType, report.no_card_type.map((file) =>
      h("li", {}, opens(file.card, file.path), h("span", { class: "hint" }, file.problem)))));
}

// ── Finding cards ─────────────────────────────────────────────────────────

let lastFound = { cards: [], types: {}, total: 0 };
let findTimer = 0;

function scheduleFind() {
  clearTimeout(findTimer);
  findTimer = setTimeout(find, 160);
}

async function find() {
  const params = { q: state.q, limit: 80 };
  if (state.type) params.type = state.type;
  if (state.person) params.person = state.person;
  if (state.since) params.since = state.since;
  if (state.until) params.until = state.until;
  if (state.sort === "day") params.sort = "newest";
  try {
    lastFound = (await api("find", params)) || lastFound;
  } catch {
    return showError();
  }
  renderTypes();
  renderResults();
}

function renderTypes() {
  const box = document.getElementById("types");
  const all = Object.values(lastFound.types).reduce((a, b) => a + b, 0);
  const chip = (type, label, n) => h("button", {
    class: "chip", "aria-pressed": String(state.type === type),
    onclick: () => { state.type = state.type === type ? "" : type; scheduleFind(); remember(); },
  }, type ? h("span", { class: "dot", style: `--c:${typeColor(type)}` }) : null, label, h("span", { class: "n" }, n));
  box.replaceChildren(chip("", T.all, all),
    ...Object.entries(lastFound.types).map(([type, n]) => chip(type, typeName(type), n)));
}

function renderResults() {
  const list = document.getElementById("results");
  const cards = lastFound.cards;
  // Two cards with one title: the row says where each lives.
  const seen = new Map();
  cards.forEach((c) => seen.set(c.title, (seen.get(c.title) || 0) + 1));
  document.getElementById("total").textContent = T.cards(lastFound.total);
  list.replaceChildren(...cards.map((c) => h("li", {
    class: `result${state.card === c.id ? " open" : ""}`, style: `--c:${typeColor(c.type)}`,
    tabindex: 0, onclick: () => openCard(c.id), onkeydown: (e) => e.key === "Enter" && openCard(c.id),
  },
    h("span", { class: "k" }, typeName(c.type)), h("span", { class: "d" }, niceDay(c.day)),
    h("span", { class: "l", title: T.links }, c.links ? `↔ ${c.links}` : ""),
    h("span", { class: "t" }, c.title),
    seen.get(c.title) > 1 ? h("span", { class: "w" }, c.path.split("/").slice(0, -1).join("/") || c.path)
      : null)));
}

// ── The graph of the focused card ─────────────────────────────────────────

// The graph's canvas: room around the rings for the labels.
const W = 400, H = 360, CX = W / 2, CY = H / 2, R1 = 92, R2 = 142;

async function renderGraph() {
  const pane = document.getElementById("graphpane");
  const card = state.cards.get(state.card);
  if (!card) {
    pane.replaceChildren(h("span", { class: "label" }, T.around), h("p", { class: "hint" }, T.noGraph));
    return;
  }
  let around;
  try { around = await api(`links/${refPath(card.id)}`, { depth: 2 }); } catch { return; }
  if (!around || state.card !== card.id) return;

  const first = around.around.filter((a) => a.distance === 1)
    .sort((a, b) => a.card.type.localeCompare(b.card.type) || b.card.links - a.card.links).slice(0, 18);
  const firstIds = new Set(first.map((a) => a.card.id));
  const second = around.around.filter((a) => a.distance === 2 && firstIds.has(a.via))
    .sort((a, b) => b.card.links - a.card.links).slice(0, 30);

  const pos = new Map([[card.id, [CX, CY, 0]]]);
  first.forEach((a, i) => {
    const angle = (i / Math.max(first.length, 1)) * Math.PI * 2 - Math.PI / 2;
    pos.set(a.card.id, [CX + R1 * Math.cos(angle), CY + R1 * Math.sin(angle), angle]);
  });
  // The outer ring is shared out evenly, in the order of the cards it hangs off,
  // so each sits near its nearer card without piling up behind a busy one.
  const outer = [...second].sort((a, b) => (pos.get(a.via)?.[2] ?? 0) - (pos.get(b.via)?.[2] ?? 0));
  const start = outer.length ? pos.get(outer[0].via)?.[2] ?? -Math.PI / 2 : 0;
  outer.forEach((a, i) => {
    const angle = start + (i / outer.length) * Math.PI * 2;
    pos.set(a.card.id, [CX + R2 * Math.cos(angle), CY + R2 * Math.sin(angle), angle]);
  });

  const size = (links) => 4 + Math.min(Math.log2((links || 0) + 1) * 2.2, 9);
  const edges = s("g");
  const nodes = s("g");
  const edgeFor = new Map();
  const addEdge = (a, b, far) => {
    const [x1, y1] = pos.get(a), [x2, y2] = pos.get(b);
    const line = s("line", { x1, y1, x2, y2, class: `edge${far ? " far" : ""}` });
    edges.append(line);
    for (const id of [a, b]) edgeFor.set(id, [...(edgeFor.get(id) || []), line]);
  };
  first.forEach((a) => addEdge(card.id, a.card.id, false));
  second.forEach((a) => addEdge(a.via, a.card.id, true));

  const node = (c, distance) => {
    const [x, y, angle] = pos.get(c.id);
    const r = distance === 0 ? 13 : distance === 1 ? size(c.links) : Math.max(3, size(c.links) - 3);
    const showLabel = distance === 1 && first.length <= 12;
    const anchor = Math.cos(angle) > 0.2 ? "start" : Math.cos(angle) < -0.2 ? "end" : "middle";
    const dx = distance === 0 ? 0 : Math.cos(angle) * (r + 5);
    const dy = distance === 0 ? r + 14 : Math.sin(angle) * (r + 5) + 3;
    const g = s("g", {
      class: `node${distance === 0 ? " center" : ""}`, transform: `translate(${x},${y})`,
      onclick: () => distance && openCard(c.id),
      onmouseenter: () => (edgeFor.get(c.id) || []).forEach((l) => l.classList.add("hot")),
      onmouseleave: () => (edgeFor.get(c.id) || []).forEach((l) => l.classList.remove("hot")),
    },
      s("title", {}, `${typeName(c.type)} · ${c.title}`),
      s("circle", { r, fill: getComputedStyle(document.documentElement)
        .getPropertyValue(`--t-${KNOWN_TYPES.includes(c.type) ? c.type : "page"}`).trim() }));
    if (showLabel || distance === 0) {
      const label = c.title.length > 22 ? `${c.title.slice(0, 21)}…` : c.title;
      g.append(s("text", { x: dx, y: dy, "text-anchor": distance === 0 ? "middle" : anchor }, label));
    }
    nodes.append(g);
  };
  second.forEach((a) => node(a.card, 2));
  first.forEach((a) => node(a.card, 1));
  node(around.card, 0);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": T.around },
    s("circle", { cx: CX, cy: CY, r: R1, class: "ring" }),
    second.length ? s("circle", { cx: CX, cy: CY, r: R2, class: "ring" }) : null,
    edges, nodes);

  const types = [...new Set([card, ...first.map((a) => a.card), ...second.map((a) => a.card)].map((c) => c.type))];
  const persons = new Set((card.persons || []).map((p) => personOf(p).id));
  first.filter((a) => a.card.type === "person").forEach((a) => persons.add(a.card.id));
  pane.replaceChildren(
    h("span", { class: "label" }, T.around),
    h("div", { class: "focusinfo" }, h("h3", {}, card.title)),
    h("div", { class: "graphbox" }, svg),
    h("div", { class: "legend" }, types.map((t) =>
      h("span", {}, h("span", { class: "dot", style: `--c:${typeColor(t)}` }), typeName(t)))),
    h("div", { class: "stats" },
      h("div", { class: "stat" }, h("b", {}, card.links), h("span", {}, T.links)),
      h("div", { class: "stat" }, h("b", {}, persons.size), h("span", {}, T.people)),
      h("div", { class: "stat" }, h("b", {}, card.changes.length), h("span", {}, T.versions))));
}

// ── Who is looking, and the rest of the controls ──────────────────────────

async function loadPeople() {
  let people = [];
  try { people = (await api("people"))?.people || []; } catch { /* the select stays at its default */ }
  learnPeople(people);
  const slugs = people.map((p) => p.id.split("/")[1]).filter(Boolean).sort();
  const audience = document.getElementById("audience");
  audience.replaceChildren(h("option", { value: "" }, T.everyone),
    ...slugs.map((slug) => h("option", { value: slug }, slug[0].toUpperCase() + slug.slice(1))));
  audience.value = state.audience;
  const person = document.getElementById("person");
  person.replaceChildren(h("option", { value: "" }, T.anyone),
    ...people.map((p) => h("option", { value: p.id.split("/")[1] }, p.title)));
  person.value = state.person;
}

function showError() {
  const board = document.getElementById("board");
  if (!board.querySelector(".error")) board.prepend(h("div", { class: "error" }, T.unreachable));
}

// The address names the card and the search, `#card=person/homer&q=zelt`. A
// step through the walk is a new entry in the tab's history; changing the
// search or who is looking rewrites the current one.
function remember({ step = false } = {}) {
  const params = new URLSearchParams();
  if (state.card) params.set("card", state.card);
  if (state.report) params.set("report", "1");
  for (const key of ["q", "type", "person", "since", "until", "audience"]) {
    if (state[key]) params.set(key, state[key]);
  }
  const address = `#${params.toString().replace(/%2F/g, "/")}`;
  if (address === (location.hash || "#")) return;
  history[step ? "pushState" : "replaceState"](null, "", address);
}

async function restore() {
  const params = new URLSearchParams(location.hash.slice(1));
  for (const key of ["q", "type", "person", "since", "until", "audience"]) state[key] = params.get(key) || "";
  state.report = params.get("report") === "1";
  document.getElementById("report").setAttribute("aria-pressed", String(state.report));
  document.getElementById("q").value = state.q;
  document.getElementById("since").value = state.since;
  document.getElementById("until").value = state.until;
  document.getElementById("audience").value = state.audience;
  document.getElementById("person").value = state.person;
  state.card = params.get("card") || "";
  if (state.card) await openCard(state.card, { step: false });
  if (state.cards.has(state.card)) return;
  state.card = "";
  renderBoard(); renderResults(); renderGraph();
}

function wire() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T[el.dataset.i18n]; });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = T[el.dataset.i18nPlaceholder];
  });
  document.documentElement.lang = LANG;
  const q = document.getElementById("q");
  q.addEventListener("input", () => { state.q = q.value; scheduleFind(); remember(); });
  for (const key of ["since", "until"]) {
    document.getElementById(key).addEventListener("change", (e) => {
      state[key] = e.target.value; scheduleFind(); remember();
    });
  }
  document.getElementById("person").addEventListener("change", (e) => {
    state.person = e.target.value; scheduleFind(); remember();
  });
  document.getElementById("audience").addEventListener("change", async (e) => {
    state.audience = e.target.value;
    state.cards.clear();
    if (state.card) await openCard(state.card, { step: false });
    if (!state.cards.has(state.card)) {
      state.card = "";
      renderBoard(); renderGraph();
    }
    scheduleFind(); remember();
  });
  document.getElementById("report").addEventListener("click", () => showReport(!state.report));
  document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
    state.sort = b.dataset.sort;
    document.querySelectorAll("[data-sort]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    scheduleFind();
  }));
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); }
    if (e.key === "Escape" && document.activeElement === q) q.blur();
  });
}

// Back, forward, or an address pasted into this tab: show what it names.
// Both events come for a pasted address; the second finds nothing to do.
async function followAddress() {
  const params = new URLSearchParams(location.hash.slice(1));
  if ((params.get("card") || "") === state.card && (params.get("report") === "1") === state.report
      && (params.get("q") || "") === state.q) return;
  await restore();
  find();
}
window.addEventListener("popstate", followAddress);
window.addEventListener("hashchange", followAddress);

(async function start() {
  wire();
  await loadPeople();
  await loadReport();
  await restore();
  await find();
})();
