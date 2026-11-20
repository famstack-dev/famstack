// dom-stand-in.js: a small stand-in for a page's document, enough for a
// component that creates elements, puts them after one another, listens for
// clicks and keys and moves the focus. Plain Node, no packages.
//
//   import { page } from "./dom-stand-in.js";
//   const doc = page();
//   const pill = doc.createElement("a");
//   doc.body.append(pill);
//   pill.click();                       // runs the click listeners, returns the event
//   doc.press("Escape");                // a keydown on the document
//   doc.activeElement === pill;         // who has the focus
//
// It knows only what the dashboard's components use: no selectors, no
// layout (every box is at 0), no bubbling beyond the element itself and the
// document.

/** A new document with an empty body. */
export function page() {
  const doc = new Listening();
  doc.activeElement = null;
  doc.createElement = (tag) => new Element(tag, doc);
  doc.body = doc.createElement("body");
  /** A keydown of `key` on the document, as a key press reaches it. */
  doc.press = (key) => doc.dispatch("keydown", { key });
  return doc;
}

class Listening {
  constructor() {
    this.listeners = {};
  }

  addEventListener(type, listener) {
    (this.listeners[type] ??= []).push(listener);
  }

  /** Run the listeners of `type` with an event made of `fields`; returns it. */
  dispatch(type, fields = {}) {
    const event = { type, defaultPrevented: false, ...fields };
    event.preventDefault = () => (event.defaultPrevented = true);
    for (const listener of this.listeners[type] || []) listener(event);
    return event;
  }
}

class Element extends Listening {
  constructor(tag, doc) {
    super();
    this.tagName = tag.toUpperCase();
    this.doc = doc;
    this.children = [];
    this.parent = null;
    this.attributes = {};
    this.className = "";
    this.textContent = "";
    this.style = { props: {}, setProperty(name, value) { this.props[name] = value; } };
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }

  /** Put `node` right after this element, in the same parent. */
  after(node) {
    const siblings = this.parent.children;
    node.parent = this.parent;
    siblings.splice(siblings.indexOf(this) + 1, 0, node);
  }

  remove() {
    if (!this.parent) return;
    this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }

  focus() {
    this.doc.activeElement = this;
  }

  getBoundingClientRect() {
    return { left: 0, top: 0, width: 0, height: 0 };
  }

  /** A plain click with the main button; `extra` adds modifier keys. */
  click(extra = {}) {
    return this.dispatch("click", { button: 0, metaKey: false, ctrlKey: false, shiftKey: false,
      altKey: false, ...extra });
  }

  /** Every element under this one with `className` among its classes. */
  all(className) {
    const found = [];
    for (const child of this.children) {
      if (child.className.split(" ").includes(className)) found.push(child);
      found.push(...child.all(className));
    }
    return found;
  }
}
