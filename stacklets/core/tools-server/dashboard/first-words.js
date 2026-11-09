// first-words.js: step 5f of the setup page, the AI's first words (`first_words_step.py` renders it).
//
//   import { enhanceFirstWords } from "./first-words.js";
//   const stop = enhanceFirstWords(stepElement, "/dashboard");   // stop() ends what plays
//
// The step plays famstack's own voice in this browser, on the Mac or a phone:
//
//   ▶ Play      asks `/api/ai/first-words/welcome` and plays its audio, then
//               asks "Did you hear it?" with Yes and Play again
//   Play again  plays the same audio again; the first time it also shows the
//               volume hint
//   Yes         asks `/api/ai/first-words/intro` and plays the AI's
//               introduction, its words shown as a quote that fills in as
//               the voice reaches them
//
// The tools server answers `{text, audio}`: the words, and the MP3 the stack's
// speech server made, as base64. The browser's built-in speech synthesis is
// never used, because it is not the voice the stack speaks with. A voice that
// does not start (no answer, or the browser refuses to play) shows why and
// offers "▶ Play" again; the way on ("Next") is always there.
//
// An automated browser (the screenshots, tests) plays nothing: it would play
// through the Mac's speakers. It moves on as if the audio had ended.

// How long an automated browser waits in place of the audio.
const SILENT_MS = 300;

/**
 * Make step 5f under `root` work; returns a function that stops what plays.
 *
 * Example:
 *   const stop = enhanceFirstWords(document.querySelector("fs-setup-page"), "/dashboard");
 *   stop();   // the audio stops, e.g. before the page reads itself again
 */
export function enhanceFirstWords(root, base) {
  const step = root.querySelector(".first-words");
  if (!step) return () => {};
  const voice = new Voice(step, (part) => new URL(`${base}/api/ai/first-words/${part}`, location.href));
  step.addEventListener("click", (event) => {
    const act = event.target.closest("[data-act]")?.dataset.act;
    if (act) voice[act]();
  });
  return () => voice.stop();
}

// ---- The step's parts ---------------------------------------------------------

class Voice {
  constructor(step, api) {
    this.api = api;
    this.words = JSON.parse(step.dataset.words || "{}");
    this.parts = {
      start: step.querySelector(".first-words-start"),
      playing: step.querySelector(".first-words-playing"),
      asked: step.querySelector(".first-words-asked"),
      hint: step.querySelector(".first-words-hint"),
      silent: step.querySelector(".first-words-silent"),
      intro: step.querySelector(".first-words-intro"),
    };
    this.welcome = null;   // the welcome's answer, kept for "Play again"
    this.replayed = false;
    this.audio = null;
  }

  /** ▶ Play: the welcome, asked for once. */
  async play() {
    this.show("playing");
    this.welcome = this.welcome || await this.fetch("welcome");
    if (this.welcome && await this.sound(this.welcome)) this.show("asked");
  }

  /** Play again: the same welcome; the volume hint stays from the first time on. */
  async again() {
    this.replayed = true;
    await this.play();
  }

  /** Yes: the AI introduces itself, its words filling in as they are spoken. */
  async yes() {
    this.show("playing");
    const intro = await this.fetch("intro");
    if (!intro) return;
    const spans = quote(this.parts.intro, intro.text);
    this.show("intro");
    await this.sound(intro, (fraction) => light(spans, Math.ceil(fraction * spans.length)));
    light(spans, spans.length);
  }

  stop() {
    if (this.audio) this.audio.pause();
  }

  /** The tools server's answer for `part`, or null after showing why there is none. */
  async fetch(part) {
    let answer;
    try {
      const response = await fetch(this.api(part), { method: "POST", headers: { accept: "application/json" } });
      answer = await response.json();
    } catch {
      answer = {};
    }
    if (answer.audio) return answer;
    this.failed();
    return null;
  }

  /** Play `answer`'s audio to its end; true when it played, false when the
   * browser refused it. `onFraction` hears how far it is. */
  sound(answer, onFraction = () => {}) {
    this.stop();
    if (navigator.webdriver) return new Promise((done) => setTimeout(() => done(true), SILENT_MS));
    const audio = new Audio(`data:audio/mpeg;base64,${answer.audio}`);
    this.audio = audio;
    return new Promise((done) => {
      audio.ontimeupdate = () => { if (audio.duration) onFraction(audio.currentTime / audio.duration); };
      const refused = () => { this.failed(); done(false); };
      audio.onended = () => done(true);
      audio.onerror = refused;
      audio.play().catch(refused);
    });
  }

  /** A voice that did not start: say so, and offer "▶ Play" again. */
  failed() {
    this.welcome = null;
    this.parts.silent.textContent = this.words.silent || "";
    this.show("start");
    this.parts.silent.hidden = false;
  }

  /** Show one of the step's parts in place of the others. */
  show(name) {
    const { start, playing, asked, hint, silent, intro } = this.parts;
    start.hidden = name !== "start";
    playing.hidden = name !== "playing";
    asked.hidden = name !== "asked";
    intro.hidden = name !== "intro";
    hint.hidden = !this.replayed;
    silent.hidden = true;
  }
}

// ---- The quote --------------------------------------------------------------

/** Put `text` into `quote` as one span per word, none said yet; returns the spans. */
function quote(element, text) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.replaceChildren(...text.split(" ").flatMap((word, i) => {
    const span = document.createElement("span");
    span.textContent = word;
    span.dataset.said = reduced ? "yes" : "no";
    return i ? [" ", span] : [span];
  }));
  return reduced ? [] : [...element.querySelectorAll("span")];
}

/** Mark the first `count` words as said. */
function light(spans, count) {
  spans.forEach((span, i) => { if (i < count) span.dataset.said = "yes"; });
}
