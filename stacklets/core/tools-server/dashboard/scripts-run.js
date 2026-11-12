// scripts-run.js: marks the page as one whose scripts run, before it is drawn.
//
//   <script src="$base/static/scripts-run.js"></script>    in <head>, before any stylesheet that reads it
//
// It sets the class `fs-js` on <html>, which the stylesheets read to hide
// what only a page without scripts shows (`first-run.css`). A classic script,
// not a module: it runs while the head is parsed, so nothing flashes.
//
// A file and not an inline script, because core's pages run only their own
// script files (the Content Security Policy in `browser_checks.py`).
document.documentElement.classList.add("fs-js");
