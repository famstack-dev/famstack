// segmented.js: a small choice between a few options, as one rounded group
// (segmented.css says how it looks).
//
//   import { segmented } from "./segmented.js";
//   form.append(segmented({
//     label: "In the family", value: "child",
//     options: [["parent", "Parent"], ["child", "Child"], ["grandparent", "Grandparent"], ["other", "Other"]],
//     onChange: (value) => save({ relation: value }),
//   }));
//
// It is a radio group as WAI-ARIA describes one: the buttons are radios,
// one of them checked; Tab reaches the group once, on the checked choice,
// and the arrow keys move the choice (Home and End to the first and last),
// selecting as they go. Every label is the page's. In a narrow container
// the label sits above and the choices share the width.

let groups = 0;

/** A new labelled group of choices; `onChange(value)` hears every change. */
export function segmented({ label, options, value = null, onChange = null } = {}) {
  const row = document.createElement("div");
  row.className = "fs-seg-row";
  const line = document.createElement("div");
  line.className = "fs-seg-line";
  const caption = document.createElement("span");
  caption.className = "fs-seg-label";
  caption.id = `fs-seg-${++groups}`;
  caption.textContent = label;
  const group = document.createElement("div");
  group.className = "fs-seg";
  group.setAttribute("role", "radiogroup");
  group.setAttribute("aria-labelledby", caption.id);

  const radios = options.map(([choice, text]) => {
    const radio = document.createElement("button");
    radio.type = "button";
    radio.setAttribute("role", "radio");
    radio.dataset.value = choice;
    radio.textContent = text;
    return radio;
  });
  const pick = (radio, { focus = false } = {}) => {
    for (const r of radios) {
      const on = r === radio;
      r.setAttribute("aria-checked", String(on));
      r.tabIndex = on ? 0 : -1;
    }
    if (focus) radio.focus();
    row.value = radio.dataset.value;
    if (onChange) onChange(radio.dataset.value);
  };
  radios.forEach((radio, i) => {
    radio.addEventListener("click", () => pick(radio));
    radio.addEventListener("keydown", (event) => {
      const to = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1,
                   Home: 0, End: radios.length - 1 }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      pick(radios[(to + radios.length) % radios.length], { focus: true });
    });
  });

  // Nothing checked yet: the first is the one Tab reaches.
  const checked = radios.find((r) => r.dataset.value === value);
  radios.forEach((r) => {
    r.setAttribute("aria-checked", String(r === checked));
    r.tabIndex = r === (checked || radios[0]) ? 0 : -1;
  });
  row.value = checked ? value : null;

  group.append(...radios);
  line.append(caption, group);
  row.append(line);
  return row;
}
