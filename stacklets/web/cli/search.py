"""stack web search <query> — search the internet from the terminal.

Queries the instance's own SearXNG, which forwards to upstream engines
and merges the results. Nothing about the family reaches Google as a
logged-in profile; the query itself still reaches an upstream engine,
and that is the honest description rather than a claim of anonymity.

    stack web search "immich vs photoprism"
      1. Immich vs PhotoPrism: which self-hosted photo manager
         https://example.com/immich-vs-photoprism
         Both index a library and serve it over the LAN, but they ...

    stack web search "immich vs photoprism" --json    # for an agent
    stack web search "mac mini idle watts" --count 5

Unlike `stack web fetch`, this needs the stacklet up: search is a
service, not a library. When it is down the command says so and points
at `stack up web` rather than printing an empty result list, because
"no results" and "nothing is running" are different problems and a
family that cannot tell them apart will retype the query.

The JSON API this reads is off in SearXNG's shipped defaults
(`search.formats` is `[html]`). The stacklet's settings overlay turns
it on; if that ever regresses the web UI keeps working perfectly and
only this command breaks, which is why there is a test for it.
"""

HELP = "Search the internet through your own SearXNG"

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "http://localhost:42080/search"
DEFAULT_COUNT = 8


def search(query: str, *, count: int = DEFAULT_COUNT, timeout: int = 20,
           base_url: str = SEARCH_URL) -> list[dict]:
    """Results as a list of `{title, url, content, engine}`. Raises OSError
    when the service cannot be reached.

    `base_url` exists because this runs from two places. On the host it
    is the published port; inside the bot-runner (where `stack web ask`
    does its work, since the host CLI is stdlib-only and the LLM client
    is not) it is the container name on the stack network.
    """
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    request = urllib.request.Request(
        f"{base_url}?{params}",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    results = []
    for item in payload.get("results", [])[:count]:
        results.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "content": (item.get("content") or "").strip(),
            "engine": item.get("engine") or "",
        })
    return results


def run(args, stacklet, config):
    parser = argparse.ArgumentParser(prog="stack web search", add_help=False)
    parser.add_argument("query", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("-h", "--help", action="store_true")
    opts = parser.parse_args(args)

    query = " ".join(opts.query).strip()
    if opts.help or not query:
        print(__doc__.strip())
        return {"ok": True}

    try:
        results = search(query, count=opts.count)
    except urllib.error.HTTPError as err:
        # 403 here is the shipped-defaults failure, and it is worth
        # naming: the web UI works, so nothing looks broken until a
        # programmatic query is tried.
        if err.code == 403:
            return {"error": (
                "SearXNG refused the JSON API (403). Its `search.formats` "
                "setting is back to html-only — check "
                "stacklets/web/config/settings.yml is mounted."
            )}
        return {"error": f"search failed: HTTP {err.code}"}
    except OSError:
        return {"error": "Search needs the web stacklet running. Run `stack up web`."}

    if opts.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return {"ok": True}

    if not results:
        print(f"\n  No results for \"{query}\".\n")
        return {"ok": True}

    print()
    for n, item in enumerate(results, 1):
        print(f"  {n}. {item['title']}")
        print(f"     {item['url']}")
        if item["content"]:
            print(f"     {_wrap(item['content'])}")
        print()
    return {"ok": True}


def _wrap(text: str, width: int = 88) -> str:
    """One-line snippet, truncated rather than reflowed — the list reads
    as a scannable column, and a three-line snippet per hit buries the
    next result."""
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
