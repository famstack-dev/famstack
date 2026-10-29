"""An Immich album as a slideshow page: the share link to ask for, the page to serve.

The tools server makes a slideshow in two steps, and this module holds the
decisions of both without any I/O (`server.py` talks to Immich):

1. Asked for an album's slideshow, the server asks Immich for a share link
   over the album's timeline photos (`share_link_request`). The link expires,
   hides EXIF and downloads, and its description marks it as the stack's.
2. The link the family gets is `<home>/go/slideshow/<share key>`. Opened,
   the server reads the share link back by its key, checks the mark
   (`album_of`), reads the album's photos with their EXIF, and serves the
   page (`page`). Each photo loads from Immich's public address with the
   key, so the browser needs no login and the stack keeps no copy.

Nothing is stored between the two steps: Immich's share link holds the key,
the photos and the expiry, so a link keeps working across a restart of the
tools server and dies with the share link.

Immich sends no CORS headers, so the page cannot ask Immich for the photo
list itself: the photos and captions are written into the page's markup,
as a list of links that a browser without scripts shows as it is. The
script plays that list. It is ES5 and the CSS avoids `inset` and custom
properties, because TV browsers from 2016 run old engines (spike of
2026-10-29, tested in Chrome with 23 photos).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

# The share link's description says which album it shows. Immich shows it in
# its list of share links, and a link without it (one a family member made
# in Immich's share dialog) is refused: its photos were not chosen as a
# slideshow.
MARK = "stack slideshow of album "

# How long a slideshow link lives unless asked otherwise: an evening.
DEFAULT_HOURS = 12.0


# ── Asking for a slideshow ───────────────────────────────────────────────

def find_album(albums: list[dict], wanted: str) -> dict | None:
    """The album `wanted` names, by its id or by its name (any case)."""
    wanted = wanted.strip()
    for album in albums:
        if album.get("id") == wanted:
            return album
    named = [a for a in albums if (a.get("albumName") or "").casefold() == wanted.casefold()]
    return named[0] if named else None


def timeline_photos(assets: list[dict]) -> list[dict]:
    """The images on the timeline, in the order they were taken. Archived,
    hidden and locked photos stay in an album, so they are left out here."""
    shown = [a for a in assets if a.get("type", "IMAGE") == "IMAGE"
             and a.get("visibility", "timeline") == "timeline"]
    return sorted(shown, key=lambda a: a.get("fileCreatedAt", ""))


def share_link_request(album: dict, photos: list[dict], hours: float, now: datetime) -> dict:
    """The body of Immich's `POST /api/shared-links` for a slideshow."""
    return {
        "type": "INDIVIDUAL",
        "assetIds": [p["id"] for p in photos],
        "expiresAt": (now + timedelta(hours=hours)).astimezone(timezone.utc).isoformat(),
        "allowDownload": False,
        "showMetadata": False,
        "description": MARK + album["id"],
    }


def album_of(link: dict) -> str | None:
    """The album a share link shows as a slideshow, or None when the stack did
    not make it."""
    description = link.get("description") or ""
    return description.removeprefix(MARK) if description.startswith(MARK) else None


# ── The page ─────────────────────────────────────────────────────────────

def _taken(photo: dict) -> datetime:
    """When a photo was taken, on the clock of the place it was taken."""
    return datetime.fromisoformat((photo.get("localDateTime") or photo["fileCreatedAt"])[:19])


def caption(photo: dict) -> str:
    """The day and the place: `Mon 10 Aug · Oia`."""
    day = f"{_taken(photo):%a} {_taken(photo).day} {_taken(photo):%b}"
    place = (photo.get("exifInfo") or {}).get("city") or ""
    return f"{day} · {place}" if place else day


def date_range(photos: list[dict]) -> str:
    """`10 to 15 Aug 2026`, `30 Jul to 2 Aug 2026`, or one day."""
    first, last = _taken(photos[0]), _taken(photos[-1])
    if first.date() == last.date():
        return f"{first.day} {first:%b %Y}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} to {last.day} {last:%b %Y}"
    if first.year == last.year:
        return f"{first.day} {first:%b} to {last.day} {last:%b %Y}"
    return f"{first.day} {first:%b %Y} to {last.day} {last:%b %Y}"


def photo_url(public_url: str, photo_id: str, key: str) -> str:
    """Where the viewing browser loads a photo: Immich's 1440 px preview."""
    return f"{public_url}/api/assets/{photo_id}/thumbnail?size=preview&key={key}"


def page(title: str, photos: list[dict], *, public_url: str, key: str, ends: str) -> str:
    """The slideshow page for `photos`, in the order given."""
    shots = "\n".join(
        f'<li class="shot"><a href="{escape(photo_url(public_url, p["id"], key))}">'
        f"{escape(caption(p))}</a></li>" for p in photos)
    return (_PAGE.replace("__TITLE__", escape(title))
            .replace("__DATES__", escape(date_range(photos)))
            .replace("__ENDS__", escape(ends))
            .replace("__SHOTS__", shots))


def ended_page() -> str:
    """What a link shows once its share link has expired."""
    return _ENDED


_STYLE = """
  html, body { margin: 0; height: 100%; background: #000; overflow: hidden; cursor: none; color: #fff;
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; }
  #show { display: none; }
  .slide { position: absolute; top: 0; right: 0; bottom: 0; left: 0; opacity: 0; transition: opacity 1.2s ease-in-out; }
  .slide.on { opacity: 1; }
  /* The photo blurred and dark behind itself: portrait photos fill the screen, faces stay whole. */
  .fill { position: absolute; top: -40px; right: -40px; bottom: -40px; left: -40px; background: center / cover no-repeat;
    filter: blur(36px) brightness(.5); }
  .photo { position: absolute; top: 0; right: 0; bottom: 0; left: 0; background: center / contain no-repeat;
    will-change: transform; }
  /* Slow zoom; the direction changes per slide. Transforms run on the graphics chip. */
  .slide.on .photo { -webkit-animation: kb 7.4s linear forwards; animation: kb 7.4s linear forwards; }
  .slide.on.alt .photo { -webkit-animation-name: kb2; animation-name: kb2; }
  @-webkit-keyframes kb  { from { -webkit-transform: scale(1) translate(0, 0); } to { -webkit-transform: scale(1.07) translate(-1%, -.6%); } }
  @-webkit-keyframes kb2 { from { -webkit-transform: scale(1.07) translate(1%, .6%); } to { -webkit-transform: scale(1) translate(0, 0); } }
  @keyframes kb  { from { transform: scale(1)    translate(0, 0); } to { transform: scale(1.07) translate(-1%, -.6%); } }
  @keyframes kb2 { from { transform: scale(1.07) translate(1%, .6%); } to { transform: scale(1) translate(0, 0); } }
  .caption { position: absolute; left: 4vw; bottom: 4vh; font-size: 2.2vw; font-weight: 600;
    text-shadow: 0 2px 12px rgba(0,0,0,.8); opacity: 0; transition: opacity 1s .6s; }
  .slide.on .caption { opacity: .9; }
  .title, .ended { position: absolute; top: 0; right: 0; bottom: 0; left: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; background: radial-gradient(#1b2740, #0b1020); }
  .title h1, .ended h1 { font-size: 5vw; margin: 0 0 1vh; } .title p { font-size: 2.2vw; color: #b4c8e6; margin: 0; }
  .bar { position: absolute; left: 0; bottom: 0; height: 3px; background: rgba(255,255,255,.5); width: 0; }
  .paused .bar { background: #f5b942; }
"""

_ENDED = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slideshow ended</title>
<style>{_STYLE}</style></head>
<body><div class="ended"><h1>This slideshow has ended.</h1></div></body></html>
"""

# The page from the spike, reading its photos from the list in its markup.
# HOLD and FADE stay in step with the CSS: transition 1.2 s, animation
# 7.4 s = HOLD + 2 * FADE. The keys are those a TV remote sends: enter
# pauses, the arrows and page up and down step.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>""" + _STYLE + """</style></head>
<body data-ends="__ENDS__">
<div id="show"><h1 class="show-title">__TITLE__</h1><p class="show-dates">__DATES__</p>
<ol>
__SHOTS__
</ol></div>
<div id="stage"></div><div class="bar" id="bar"></div>
<script>
var stage = document.getElementById('stage'), bar = document.getElementById('bar');
var HOLD = 5000, FADE = 1200, ENDS = Date.parse(document.body.getAttribute('data-ends'));
var SHOW = { title: document.querySelector('.show-title').textContent,
             subtitle: document.querySelector('.show-dates').textContent };
var items = [{ title: true }], links = document.querySelectorAll('.shot a');
for (var k = 0; k < links.length; k++) items.push({ src: links[k].href, caption: links[k].textContent });
var i = -1, timer = null, paused = false, current = null;

function build(item, n) {
  var s = document.createElement('div');
  s.className = 'slide' + (n % 2 ? ' alt' : '');
  if (item.ended) {
    s.innerHTML = '<div class="ended"><h1>This slideshow has ended.</h1></div>';
  } else if (item.title) {
    s.innerHTML = '<div class="title"><h1></h1><p></p></div>';
    s.querySelector('h1').textContent = SHOW.title; s.querySelector('p').textContent = SHOW.subtitle;
  } else {
    s.innerHTML = '<div class="fill"></div><div class="photo"></div><div class="caption"></div>';
    s.querySelector('.fill').style.backgroundImage = s.querySelector('.photo').style.backgroundImage = 'url("' + item.src + '")';
    s.querySelector('.caption').textContent = item.caption;
  }
  return s;
}
function preload(n, done) {  // show a photo only once it is loaded, so it never pops in half-drawn
  var item = items[n % items.length];
  if (item.title) return done();
  var img = new Image(); img.onload = img.onerror = done; img.src = item.src;
}
function show(s) {
  var old = current;
  stage.appendChild(s);
  s.offsetWidth;  // start the fade from opacity 0
  s.className += ' on';
  current = s;
  if (old) setTimeout(function () { old.parentNode && old.parentNode.removeChild(old); }, FADE + 100);
}
function next(step) {
  clearTimeout(timer);
  if (ENDS && Date.now() > ENDS) { show(build({ ended: true }, 0)); bar.style.width = 0; return; }
  var n = (i + (step || 1) + items.length) % items.length;
  preload(n, function () {
    i = n;
    show(build(items[i], i));
    bar.style.width = (100 * (i + 1) / items.length) + '%';
    preload(i + 1, function () {});
    if (!paused) timer = setTimeout(next, HOLD + FADE);
  });
}
document.onkeydown = function (e) {
  var k = e.keyCode;
  if (k === 39 || k === 34) next(1);           // right, page down
  else if (k === 37 || k === 33) next(-1);     // left, page up
  else if (k === 32 || k === 13) {             // space, enter: pause
    paused = !paused; document.body.className = paused ? 'paused' : '';
    if (!paused) next(1); else clearTimeout(timer);
  } else if (k === 70 && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
};
document.onclick = function () { if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen(); };
next(1);
</script>
</body></html>
"""
