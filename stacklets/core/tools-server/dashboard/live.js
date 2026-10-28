// live.js: the page's one event stream (ADR-020).
//
// The tools server publishes what the stack does as events: jobs and their
// steps now, more later (`live_routes.py`). A page holds one stream for all
// its widgets. `connect()` opens it, or returns the one the page already
// has; a widget subscribes to what it shows by the event's subject.
//
//   import { connect } from "../live.js";
//   const live = connect();
//   const off = live.on("job/", (event) => show(event));   // every job's events
//   live.onResync(() => reload());   // the stream lost track: read your view again
//   live.status;                     // "connecting", "open" or "reconnecting"
//   live.addEventListener("status", () => mark(live.status));
//
// An event is the envelope the server sends: `id`, `type`, `time`,
// `subject` and `data`.
//
// Reconnecting is EventSource's own: it waits the server's `retry` (3 s),
// reconnects and sends the id of the last event it saw, so nothing is
// missed. A server that restarted, or one the page was away from too long,
// answers with `resync` instead, and every widget reads its view again.
// When the browser gives up for good, as it does on an error page from a
// proxy in front while core restarts, this opens a new stream after the
// same wait, from the last id it saw.
//
// One stream per page, not per tab. Over plain HTTP (port mode) a browser
// holds at most six connections to one host, and every tab with the
// dashboard open spends one of them. Sharing a stream across tabs is not
// done yet.

const STREAM = new URL("../api/events", import.meta.url);
const RETRY_MS = 3000;
const streams = new Map();

/** The page's stream from `src` (the dashboard's, by default), opened once. */
export function connect(src = STREAM) {
  const url = new URL(src, location.href).href;
  if (!streams.has(url)) streams.set(url, new Live(url));
  return streams.get(url);
}

class Live extends EventTarget {
  constructor(src) {
    super();
    this.src = src;
    this.status = "connecting";
    this.subscribers = new Set();
    this.resyncs = new Set();
    this.lastId = "";
    this.open();
  }

  /** Call `fn` with every event whose subject starts with `prefix`; returns `off`. */
  on(prefix, fn) {
    const subscriber = { prefix, fn };
    this.subscribers.add(subscriber);
    return () => this.subscribers.delete(subscriber);
  }

  /** Call `fn` when the stream lost track and views must be read again; returns `off`. */
  onResync(fn) {
    this.resyncs.add(fn);
    return () => this.resyncs.delete(fn);
  }

  open() {
    const url = new URL(this.src);
    if (this.lastId) url.searchParams.set("after", this.lastId);
    const source = new EventSource(url);
    source.onopen = () => this.mark("open");
    source.onmessage = (message) => this.receive(message);
    source.onerror = () => {
      this.mark("reconnecting");
      if (source.readyState === EventSource.CLOSED) setTimeout(() => this.open(), RETRY_MS);
    };
  }

  receive(message) {
    this.lastId = message.lastEventId || this.lastId;
    const event = JSON.parse(message.data);
    if (event.type === "resync") {
      for (const fn of this.resyncs) fn(event);
      return;
    }
    for (const { prefix, fn } of this.subscribers) {
      if ((event.subject || "").startsWith(prefix)) fn(event);
    }
  }

  mark(status) {
    if (status === this.status) return;
    this.status = status;
    this.dispatchEvent(new Event("status"));
  }
}
