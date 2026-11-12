// The card browser's old address, /cards#card=<id>&q=...: the part after
// the # never reaches the server, so this script reads it and goes on to
// the card's page, /card/<id>#q=... (cards_page.forwarding_page). A file
// of its own, because core's pages run no inline script (browser_checks).
const params = new URLSearchParams(location.hash.slice(1));
const card = params.get("card") || "";
params.delete("card");
const rest = params.toString();
if (card) {
  const path = "/card/" + card.split("/").map(encodeURIComponent).join("/");
  location.replace(path + (rest ? "#" + rest : ""));
}
