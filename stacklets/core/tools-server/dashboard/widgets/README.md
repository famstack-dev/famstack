# Dashboard widgets

A widget shows one thing at a glance and more when it has room, like a
widget on a phone's home screen. The activity widget (`fs-activity`) is
the first, "Your stack" (`fs-stack`, `stack_widget.py`) the second; the
next one copies their shape and leaves the shell alone.

Plain HTML, CSS and JavaScript modules, no framework and no build step.

## What a widget is

| Part | Where | Does |
|---|---|---|
| A view | `<name>_widget.py` beside `server.py` | Builds what the widget shows from its source, worded for people, as JSON. Pure. |
| Its HTML | the same module, `html(view)` | Renders that view as the large size's markup, so the page works without JavaScript. |
| One endpoint | `GET /dashboard/api/<name>` in `server.py` | Answers the view. 200 with `state` `content` or `empty`; 503 with `state` `error`. |
| Its element | `widgets/fs-<name>.js` | `render(view)`: builds the same markup from the view. Enhancements live here. |
| Its CSS | `widgets/fs-<name>.css` | Lays out its content at each size. |
| The shell | `widgets/fs-widget.js`, `fs-widget.css`, `dashboard_page.shell()` | Shared by every widget: the glass, the title and link, the states, the sizes, refresh and retry. |

The page sends every widget already rendered. The script then keeps it
current: the shell reads the endpoint and hands the answer to the
element; the element renders it. Words are chosen once, on the server, in
the browser's language: the script never words anything itself.

## The view (the data contract)

Every view has these fields; the rest is the widget's own.

```json
{
  "state": "content",
  "title": "Activity",
  "src": "/dashboard/api/activity?limit=12",
  "empty": "Nothing has happened yet.",
  "error": "The memory does not answer right now.",
  "retry": "Try again",
  "retry_href": "/dashboard",
  "see_all": {"label": "See all", "href": "/dashboard?size=large", "show": "small"}
}
```

`see_all` is optional. The activity view adds `glance`, `days` (each with
its `label` and `entries`; an entry has `who`, `text`, `cards` with their
`href` and the words before each (`sep`), `rest` ("and 4 more"), `when`,
`at`) and `more`. A run of the same change by the same person is one entry
(`changes` says how many it stands for), and work the memory did on its
own names no one (`who` is null): "Note refreshed at Marge's request".

## States

The shell has an attribute `state`, and shows the part with that
`data-state`:

- `loading`: a calm skeleton of three lines. Only before there is
  anything to show; a refresh never goes back to it.
- `empty`: one sentence (`view.empty`). Never an empty box.
- `error`: one plain sentence and a "Try again" link. A failed quiet
  refresh keeps what is shown.
- `content`: the widget's element.

The server sends the state it found, so a page without JavaScript shows
the right one too.

`data-state` belongs to the shell: `fs-widget.css` hides every element
inside a widget that carries it, except the part for the current state.
A widget's own content names its states otherwise; `fs-stack` uses
`data-status` for a stacklet's and a tile's.

## Sizes

The slot a widget sits in decides how much it shows, not the screen: a
tile on the dashboard, a wide panel, a phone, later the menu bar app.

| Size | Widget width (content box) | The activity widget shows |
|---|---|---|
| small | below 280px | how many things changed today, and the latest one in a line |
| medium | 280 to 479px | the latest day's first four entries, and "Show more" |
| large | 480px and up | the feed by day, and "Show more" when there is more |

The shell measures its own width with a container query, so a widget
adapts wherever it is placed. `size="small|medium|large"` on
`<fs-widget>` fixes the size instead (the page passes `?size=large`).

The server renders the large size's content once. A part says which sizes
show it with `data-show` (`data-show="small"`, `data-show="medium large"`);
a part without it shows at every size. `fs-widget.css` hides the rest, so
a smaller size never asks for other data. Each size must read complete on
its own, not as a cropped large one: give the small size its own glance
(`glance` here) rather than the first line of the large one.

"Show more" in a smaller size opens the large size; in the large size it
reads more (`?limit=`). Both are links, so they work without JavaScript.

## Live events

A page holds one event stream, `dashboard/live.js` over
`GET /dashboard/api/events` (ADR-020). A widget that shows something
while it happens subscribes to it by subject instead of re-reading on a
timer:

```js
import { connect } from "../live.js";
const off = connect().on("job/", (event) => this.show(event));
```

`connect()` returns the page's one stream; `on` returns the function that
ends the subscription, for `disconnectedCallback`. The shell already
listens for `resync`, which says the server lost track (it restarted, or
the page was away too long), and reads every widget's endpoint again. A
widget that keeps state from events reads its view again the same way.

## Components

A widget builds what it shows from the components in `dashboard/components/`,
not from CSS copied out of a prototype: the wordmark (`brand`), buttons
(`button`), motion (`motion`: a page's first open, a pill popping in), the
full-screen moments (`moment`), family pills and their words (`pills`,
`family.js`), the greeting by time of day (`greeting.js`), a segmented choice
(`segmented`), a text field (`field`), a sheet for one task (`sheet`) and the
setup checklist (`steps`). Each file's header says what it is, its API and
who uses it. They style themselves from `tokens.css` only and never word
anything: the page passes every text in.

`dashboard/gallery.html` shows every component in each of its states, light
and dark. It is not served; open it through a local server from the
repository's root (`python3 -m http.server`), as its header says.

## Adding the next widget

1. Write `<name>_widget.py`: `view(...)` and `html(view)`, with words in
   English and German. Mark each part with `data-show`.
2. Add `GET /dashboard/api/<name>` and put
   `dashboard_page.shell(view, html(view), name="<name>")` on the page in
   `server.py`.
3. Write `widgets/fs-<name>.js` with `render(view)` and
   `widgets/fs-<name>.css`, and add both to `dashboard_page._ASSETS` and
   `dashboard/index.html`.
4. Style only from `tokens.css`; never a colour or a font of its own.
5. Add the new files to the Dockerfile and to core's compose mounts.
6. Test from the caller's side: the served page holds the content and its
   links without JavaScript; the endpoint answers the same view. The sizes
   are CSS: check them on a screenshot of a page with three slots.
