// The card browser: find cards on the left, open them side by side, walk
// their links, see the neighbourhood of the focused card as a graph.
//
// Everything is read from the memory stacklet's cards API through core
// (`/cards/api/...`). Vault text is escaped before anything else touches it;
// the small markdown renderer below only adds the tags it writes itself.

"use strict";

const API = "/cards/api";
const MAX_OPEN = 6;

// ── Words ─────────────────────────────────────────────────────────────────

const WORDS = {
  en: {
    memory: "Memory · Cards", search: "Search the family memory", viewas: "View as",
    everyone: "Everyone (admin)", types: "Kinds", all: "all", relevant: "relevant", newest: "newest",
    anyone: "Anyone", person: "Person", from: "From", to: "To", cards: (n) => `${n} cards`, details: "Details", history: "History",
    linksTo: "Links to", linkedFrom: "Linked from", nothing: "Nothing written here yet.",
    open: "open", done: "done", openPaperless: "Open in Paperless", around: "Around this card",
    links: "links", people: "people", versions: "versions", openLinked: "Open the linked cards",
    welcomeA: "What the family", welcomeB: "remembers", welcomeSub:
      "Search on the left or pick a card. Open several side by side and follow their links.",
    recently: "Changed lately", noGraph: "Open a card to see what it links to.",
    unreachable: "The family memory does not answer. Is the memory stacklet running? (stack up memory)",
    file: "File", id: "Card", changedBy: (who, day) => `${who} · ${day}`, focus: "Show its links",
    close: "Close", view: "View", views: "Compiled about this card", openWiki: "Open in the wiki",
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
    open: "offen", done: "erledigt", openPaperless: "In Paperless öffnen", around: "Rund um diese Karte",
    links: "Verweise", people: "Personen", versions: "Versionen", openLinked: "Verknüpfte Karten öffnen",
    welcomeA: "Was die Familie", welcomeB: "erinnert", welcomeSub:
      "Links suchen oder eine Karte wählen. Mehrere nebeneinander öffnen und ihren Verweisen folgen.",
    recently: "Zuletzt geändert", noGraph: "Eine Karte öffnen, um ihre Verweise zu sehen.",
    unreachable: "Das Familiengedächtnis antwortet nicht. Läuft das memory-Stacklet? (stack up memory)",
    file: "Datei", id: "Karte", changedBy: (who, day) => `${who} · ${day}`, focus: "Verweise zeigen",
    close: "Schließen", view: "Ansicht", views: "Über diese Karte zusammengestellt", openWiki: "Im Wiki öffnen",
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
  open: [],        // card ids, left to right
  focus: "",       // the card the graph is about
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
const WEEKDAY = new Intl.DateTimeFormat(LANG, { weekday: "long" });
const MONTH_YEAR = new Intl.DateTimeFormat(LANG, { month: "long", year: "numeric" });
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
    // An Obsidian callout ("> [!summary]") is a labelled box; any other quote a quote.
    const callout = quoted[0].match(/^\[!(\w+)\][-+]?\s*(.*)$/);
    const rest = callout ? [callout[2], ...quoted.slice(1)] : quoted;
    const inner = markdown(rest.join("\n"), card);
    html.push(callout
      ? `<div class="callout"><span class="label">${escapeHtml(callout[1])}</span>${inner}</div>`
      : `<blockquote>${inner}</blockquote>`);
    quoted = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!/^>/.test(line)) flushQuote();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*+]\s+(?:\[[ xX]\]\s+)?(.*)$/);
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
      html.push(`<li>${inline((bullet || numbered)[1], card)}</li>`);
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

function withoutMeta(text) {
  // A record opens with a quote of its fields (`> **About** … **Captured** …`,
  // written for Obsidian, ADR-017 §5). The header shows them already.
  const lines = String(text || "").split("\n");
  const first = lines.findIndex((l) => l.trim());
  if (first < 0 || !/^>/.test(lines[first])) return lines.join("\n");
  let end = first;
  while (end < lines.length && /^>/.test(lines[end])) end++;
  const fields = lines.slice(first, end).filter((l) => l.replace(/^>\s*/, "").trim());
  const isMeta = fields.length && fields.every((l) => /^>\s*\*\*[^*\n]+\*\*/.test(l));
  if (isMeta) lines.splice(first, end - first);
  return lines.join("\n");
}

function bodyOf(card, text = withoutMeta(withoutTitle(card.text, card.title))) {
  const div = h("div", { class: "body" });
  if (!String(text || "").trim()) {
    div.append(h("p", { class: "empty-text" }, T.nothing));
    return div;
  }
  div.innerHTML = markdown(text, card);
  div.querySelectorAll("a.card-link").forEach((a) =>
    a.addEventListener("click", () => openCard(a.dataset.ref, { after: card.id })));
  return div;
}

// ── One renderer per kind of card ─────────────────────────────────────────

function peopleRow(card) {
  const names = card.persons || [];
  if (!names.length) return null;
  return h("div", { class: "people" }, names.map((name) =>
    h("span", { class: "person", onclick: () => openCard(`person/${String(name).toLowerCase()}`,
                                                          { after: card.id }) },
      h("span", { class: "av" }, String(name).slice(0, 1).toUpperCase()), name)));
}

function factsGrid(pairs) {
  const shown = pairs.filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length));
  if (!shown.length) return null;
  return h("dl", { class: "facts-grid" }, shown.map(([k, v]) =>
    [h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.join(", ") : String(v))]));
}

const RENDER = {
  diary(card) {
    const d = card.day ? new Date(`${card.day}T12:00:00`) : null;
    return [
      d && !Number.isNaN(d.getTime()) ? h("div", { class: "hero-day" },
        h("span", { class: "big" }, d.getDate()),
        h("span", { class: "rest" }, `${WEEKDAY.format(d)}, ${MONTH_YEAR.format(d)}`)) : null,
      peopleRow(card), bodyOf(card),
    ];
  },

  note(card) {
    return [peopleRow(card), bodyOf(card)];
  },

  bookmark(card) {
    const url = card.meta.url || card.meta.resource || card.meta.source || "";
    let host = "";
    try { host = url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { host = ""; }
    return [
      url ? h("a", { class: "site", href: url, target: "_blank", rel: "noopener noreferrer" },
        h("span", { class: "fav" }, (host || "?").slice(0, 1).toUpperCase()),
        h("span", {}, h("div", {}, card.title), h("div", { class: "host" }, host))) : null,
      peopleRow(card), bodyOf(card),
    ];
  },

  document(card) {
    const m = card.meta;
    const id = m.paperless_id;
    return [
      factsGrid([
        ["From", m.correspondent], ["Kind", m.document_type || m.doctype], ["Date", niceDay(card.day)],
        ["Tags", m.tags], ["Amount", m.amount],
      ]),
      id ? h("a", { class: "action", href: goUrl(`/go/docs/${id}`), target: "_blank",
                    rel: "noopener noreferrer" }, T.openPaperless, " ↗") : null,
      bodyOf(card),
    ];
  },

  list(card) {
    const items = card.items || [];
    const open = items.filter((i) => !i.done);
    const done = items.filter((i) => i.done);
    const ul = h("ul", { class: "items" });
    let section = null;
    for (const item of [...open, ...done]) {
      if (!item.done && item.section && item.section !== section) {
        section = item.section;
        ul.append(h("li", { class: "sec" }, section));
      }
      ul.append(h("li", { class: `item${item.done ? " done" : ""}` },
        h("span", { class: "box" }), h("span", { class: "t" }, item.text),
        item.due ? h("span", { class: "due" }, niceDay(item.due)) : null));
    }
    return [
      h("div", { class: "tally" }, `${open.length} ${T.open} · ${done.length} ${T.done}`),
      items.length ? ul : bodyOf(card),
      card.meta.description ? h("p", { class: "body hint" }, card.meta.description) : null,
    ];
  },

  person(card) {
    const m = card.meta;
    return [
      factsGrid([["Also", [...(m.aliases || []), ...(m.synonyms || [])]], ["Account", m.matrix || m.account]]),
      bodyOf(card),
    ];
  },

  topic(card) {
    return [bodyOf(card)];
  },

  correspondent(card) {
    return [factsGrid([["Also", card.meta.aliases]]), bodyOf(card)];
  },

  email(card) {
    const m = card.meta;
    return [factsGrid([["From", m.from], ["To", m.to], ["Subject", m.subject]]), bodyOf(card)];
  },
};

function renderBody(card) {
  return (RENDER[card.type] || ((c) => [peopleRow(c), bodyOf(c)]))(card);
}

// The pages the wiki compiled about a card (a person's page): read with the
// card, marked as views, never opened as cards of their own.
function viewsOf(card) {
  if (!card.views?.length) return null;
  return h("div", { class: "linkset" }, h("h4", { class: "label" }, T.views),
    card.views.map((view) => h("details", { class: "more view" },
      h("summary", {}, h("span", { class: "kind label" }, T.view), h("span", {}, view.title)),
      bodyOf({ id: card.id, path: view.path }, withoutTitle(view.text, view.title)),
      view.go ? h("a", { class: "action", href: goUrl(view.go), target: "_blank",
                         rel: "noopener noreferrer" }, T.openWiki, " ↗") : null)));
}

// ── A card ────────────────────────────────────────────────────────────────

function linkChips(cards, from) {
  return h("div", { class: "linkchips" }, cards.map((c) =>
    h("button", { class: "linkchip", style: `--c:${typeColor(c.type)}`, title: `${typeName(c.type)} · ${c.id}`,
                  onclick: () => openCard(c.id, { after: from }) },
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
  const el = h("article", { class: `card${state.focus === card.id ? " focus" : ""}`,
                            style: `--c:${typeColor(card.type)}`, "data-id": card.id },
    h("header", {}, kindLabel(card.type), h("span", { class: "spacer" }),
      h("button", { class: "iconbtn", title: T.focus, "aria-label": T.focus, onclick: () => setFocus(card.id) },
        graphIcon()),
      h("button", { class: "iconbtn", title: T.close, "aria-label": T.close, onclick: () => closeCard(card.id) },
        "✕")),
    h("h2", {}, card.title),
    h("div", { class: "strip" },
      card.day ? h("span", {}, niceDay(card.day)) : null,
      card.version ? h("span", { class: "v" }, `@${card.version}`) : null,
      last ? h("span", {}, last.who) : null,
      h("span", {}, `${card.links} ${T.links}`)),
    renderBody(card),
    viewsOf(card),
    card.links_out.length ? h("div", { class: "linkset" },
      h("h4", { class: "label" }, T.linksTo), linkChips(card.links_out, card.id)) : null,
    card.links_in.length ? h("div", { class: "linkset" },
      h("h4", { class: "label" }, T.linkedFrom), linkChips(card.links_in, card.id)) : null,
    details(card));
  el.addEventListener("click", (e) => {
    if (!e.target.closest("button, a")) setFocus(card.id, { scroll: false });
  });
  return el;
}

function graphIcon() {
  return s("svg", { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
                    "stroke-width": 2 },
    s("circle", { cx: 12, cy: 12, r: 3 }), s("circle", { cx: 4, cy: 6, r: 2 }),
    s("circle", { cx: 20, cy: 6, r: 2 }), s("circle", { cx: 12, cy: 21, r: 2 }),
    s("path", { d: "M10 10.5 5.5 7.5M14 10.5l4.5-3M12 15v4" }));
}

// ── The board ─────────────────────────────────────────────────────────────

async function openCard(ref, { after = null, focus = true } = {}) {
  let card;
  try {
    card = await api(`card/${refPath(ref)}`);
  } catch {
    return showError();
  }
  if (!card) return;
  state.cards.set(card.id, card);
  state.open = state.open.filter((id) => id !== card.id);
  const at = after ? state.open.indexOf(after) + 1 : state.open.length;
  state.open.splice(at > 0 ? at : state.open.length, 0, card.id);
  while (state.open.length > MAX_OPEN) state.open.shift();
  if (focus) state.focus = card.id;
  renderBoard();
  renderResults();
  renderGraph();
  remember();
  document.querySelector(`.card[data-id="${CSS.escape(card.id)}"]`)
    ?.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
}

function closeCard(id) {
  state.open = state.open.filter((x) => x !== id);
  if (state.focus === id) state.focus = state.open[state.open.length - 1] || "";
  renderBoard(); renderResults(); renderGraph(); remember();
}

function setFocus(id, { scroll = true } = {}) {
  state.focus = id;
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("focus", c.dataset.id === id));
  renderGraph();
  remember();
  if (scroll) document.querySelector(`.card[data-id="${CSS.escape(id)}"]`)
    ?.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
}

function renderBoard() {
  const board = document.getElementById("board");
  board.replaceChildren();
  if (state.report && lastReport) board.append(reportElement(lastReport));
  if (!state.open.length) {
    if (!state.report) renderWelcome(board);
    return;
  }
  for (const id of state.open) {
    const card = state.cards.get(id);
    if (card) board.append(cardElement(card));
  }
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
      linkChips(change.cards.slice(0, 6), null),
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
  remember();
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
    class: `result${state.open.includes(c.id) ? " open" : ""}`, style: `--c:${typeColor(c.type)}`,
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
  const card = state.cards.get(state.focus);
  if (!card) {
    pane.replaceChildren(h("span", { class: "label" }, T.around), h("p", { class: "hint" }, T.noGraph));
    return;
  }
  let around;
  try { around = await api(`links/${refPath(card.id)}`, { depth: 2 }); } catch { return; }
  if (!around || state.focus !== card.id) return;

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
      onclick: () => (distance === 0 ? setFocus(c.id) : openCard(c.id, { after: card.id })),
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
  const persons = new Set(card.persons || []);
  first.filter((a) => a.card.type === "person").forEach((a) => persons.add(a.card.title));
  pane.replaceChildren(
    h("span", { class: "label" }, T.around),
    h("div", { class: "focusinfo" }, h("h3", {}, card.title)),
    h("div", { class: "graphbox" }, svg),
    h("div", { class: "legend" }, types.map((t) =>
      h("span", {}, h("span", { class: "dot", style: `--c:${typeColor(t)}` }), typeName(t)))),
    h("div", { class: "stats" },
      h("div", { class: "stat" }, h("b", {}, card.links), h("span", {}, T.links)),
      h("div", { class: "stat" }, h("b", {}, persons.size), h("span", {}, T.people)),
      h("div", { class: "stat" }, h("b", {}, card.changes.length), h("span", {}, T.versions))),
    first.length ? h("button", { class: "ghost", onclick: () => openLinked(card, first) }, T.openLinked) : null);
}

async function openLinked(card, first) {
  for (const a of first.slice(0, MAX_OPEN - 1).reverse()) {
    await openCard(a.card.id, { after: card.id, focus: false });
  }
  setFocus(card.id);
}

// ── Who is looking, and the rest of the controls ──────────────────────────

async function loadPeople() {
  let people = [];
  try { people = (await api("people"))?.people || []; } catch { /* the select stays at its default */ }
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

function remember() {
  const params = new URLSearchParams();
  if (state.open.length) params.set("open", state.open.join(","));
  if (state.focus) params.set("focus", state.focus);
  if (state.report) params.set("report", "1");
  for (const key of ["q", "type", "person", "since", "until", "audience"]) {
    if (state[key]) params.set(key, state[key]);
  }
  writing = true;
  history.replaceState(null, "", `#${params.toString()}`);
  writing = false;
}

async function restore() {
  const params = new URLSearchParams(location.hash.slice(1));
  for (const key of ["q", "type", "person", "since", "until", "audience"]) state[key] = params.get(key) || "";
  state.report = params.get("report") === "1";
  document.getElementById("report").setAttribute("aria-pressed", String(state.report));
  document.getElementById("q").value = state.q;
  document.getElementById("since").value = state.since;
  document.getElementById("until").value = state.until;
  const open = (params.get("open") || "").split(",").filter(Boolean);
  const focus = params.get("focus") || "";
  for (const id of open) await openCard(id, { focus: false });
  if (focus && state.cards.has(focus)) setFocus(focus);
  else document.getElementById("board").scrollTo({ left: 0 });
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
    const open = [...state.open];
    state.open = []; state.cards.clear();
    for (const id of open) await openCard(id, { focus: false });
    if (!state.open.length) renderBoard();
    state.focus = state.open.includes(state.focus) ? state.focus : state.open[0] || "";
    renderGraph(); scheduleFind(); remember();
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

// A link pasted into this tab, or back and forward: open what the address names.
let writing = false;
window.addEventListener("hashchange", async () => {
  if (writing) return;
  state.open = []; state.cards.clear(); state.focus = "";
  await restore();
  if (!state.open.length) renderBoard();
  renderGraph(); find();
});

(async function start() {
  wire();
  await loadPeople();
  await loadReport();
  await restore();
  if (!state.open.length) renderBoard();
  renderGraph();
  await find();
})();
