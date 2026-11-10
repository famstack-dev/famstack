// The memory pages' one script: a card's history and a note's full text,
// each loaded when it is opened.
//
// "History" and "Full text" are closed <details> whose `data-src` names
// the server's fragment: the card's timeline (`/card/<card id>/history`,
// memory_ui/history.py) or the text of the file a note was made from
// (`/card/<card id>/text`, memory_ui/card_page.py). Opening one the first
// time asks for that fragment and puts it in place of its loading line;
// closing and opening again keeps it. Git is the slowest read of a card,
// and a PDF's text can run to many pages, so a visit that never opens
// them never pays for them. "Show all" unfolds the events past the latest
// three.
//
// A diary entry's sections (memory_ui/entry.py) play and step here: a
// voice message's "▶ 1:12" plays its recording, a video's "▶ 0:12" plays
// the video, and the photos' mosaic opens one photo full size with
// "3 of 7", the arrows, the arrow keys and a swipe; "All photos" leads
// back. Without the script each tile links its photo.
//
// Everything else on the pages works without this script: they are
// rendered on the server.
//
//     <details class="more history" data-src="/card/note-0b6e2d91aa/history">
//     <details class="more fulltext" data-src="/card/note-4e1a9b7c2d/text">

// ---- Loading what waits behind a closed part ----

/** Load `details`' fragment from its `data-src`, once. */
async function loadPart(details) {
  if (details.dataset.loaded) return;
  details.dataset.loaded = "1";
  const status = details.querySelector(".loading");
  try {
    const r = await fetch(details.dataset.src, { headers: { accept: "text/html" } });
    const part = document.createElement("div");
    part.className = `${details.classList.contains("history") ? "history" : "fulltext"}-body`;
    part.innerHTML = await r.text();   // the server's own HTML: it escapes every name and word
    status?.replaceWith(part);
  } catch {
    delete details.dataset.loaded;     // the next opening tries again
  }
}

document.querySelectorAll("details[data-src]").forEach((details) => {
  details.addEventListener("toggle", () => { if (details.open) loadPart(details); });
});

// ---- Show all ----

document.addEventListener("click", (event) => {
  const button = event.target.closest?.(".showall");
  if (!button) return;
  button.parentElement.querySelectorAll(".timeline li[hidden]").forEach((li) => { li.hidden = false; });
  button.remove();
});

// ---- A diary entry's photos: the mosaic and one photo full size ----

// A swipe shorter than this is a tap.
const SWIPE_PX = 40;

/** Show photo `n` (1-based) of `slides` full size; 0 shows the mosaic. */
function showPhoto(slides, n) {
  const tiles = [...slides.querySelectorAll(".tile")];
  const viewer = slides.querySelector(".viewer");
  const wrap = slides.querySelector(".mosaic-wrap");
  slides.dataset.at = String(n);
  wrap.hidden = n !== 0;
  viewer.hidden = n === 0;
  if (n === 0) return;
  const tile = tiles[n - 1];
  const shown = viewer.querySelector("img.shown");
  const gone = viewer.querySelector(".gone");
  const src = tile.getAttribute("href") || "";
  shown.hidden = !src;
  gone.hidden = Boolean(src);
  if (src) shown.src = src;
  viewer.querySelector(".count").textContent = slides.dataset.of.replace("{n}", String(n));
  viewer.querySelector(".nav.prev").disabled = n === 1;
  viewer.querySelector(".nav.next").disabled = n === tiles.length;
}

/** Step `slides` by `step` photos, within the first and the last. */
function stepPhoto(slides, step) {
  const count = slides.querySelectorAll(".tile").length;
  const at = Number(slides.dataset.at || 0);
  const next = Math.min(Math.max(at + step, 1), count);
  if (next !== at) showPhoto(slides, next);
}

document.addEventListener("click", (event) => {
  const slides = event.target.closest?.(".slides");
  if (!slides) return;
  const tile = event.target.closest(".tile");
  if (tile) {
    event.preventDefault();
    showPhoto(slides, [...slides.querySelectorAll(".tile")].indexOf(tile) + 1);
    slides.querySelector(".allphotos").focus();
  } else if (event.target.closest(".allphotos")) {
    const at = Number(slides.dataset.at || 1);
    showPhoto(slides, 0);
    slides.querySelectorAll(".tile")[at - 1]?.focus();
  } else if (event.target.closest(".mosaic-wrap .nav.next")) {
    showPhoto(slides, 1);
    slides.querySelector(".viewer .nav.next").focus();
  } else if (event.target.closest(".viewer .nav")) {
    stepPhoto(slides, event.target.closest(".nav").classList.contains("next") ? 1 : -1);
  }
});

document.addEventListener("keydown", (event) => {
  const slides = event.target.closest?.(".slides");
  if (!slides || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
  event.preventDefault();
  if (Number(slides.dataset.at || 0) === 0) {
    if (event.key === "ArrowRight") showPhoto(slides, 1);
    return;
  }
  stepPhoto(slides, event.key === "ArrowRight" ? 1 : -1);
});

document.querySelectorAll(".slides .swipe").forEach((frame) => {
  let startX = null;
  frame.addEventListener("pointerdown", (e) => { startX = e.clientX; });
  frame.addEventListener("pointerup", (e) => {
    if (startX === null) return;
    const dx = e.clientX - startX;
    startX = null;
    if (Math.abs(dx) > SWIPE_PX) stepPhoto(frame.closest(".slides"), dx < 0 ? 1 : -1);
  });
});

// ---- A diary entry's recordings and videos ----

document.addEventListener("click", (event) => {
  const play = event.target.closest?.(".player .play");
  if (play) {
    const audio = play.parentElement.querySelector("audio");
    if (audio.paused) audio.play(); else audio.pause();
    audio.onplay = () => { play.classList.add("playing"); play.firstChild.textContent = play.textContent.replace("▶", "❚❚"); };
    audio.onpause = audio.onended = () => { play.classList.remove("playing"); play.firstChild.textContent = play.textContent.replace("❚❚", "▶"); };
    return;
  }
  const over = event.target.closest?.(".playover");
  if (over) {
    const video = over.parentElement.querySelector("video");
    video.controls = true;
    video.play();
    over.remove();
  }
});
