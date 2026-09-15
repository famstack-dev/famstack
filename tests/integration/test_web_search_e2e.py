"""Search, against the real SearXNG container.

One assertion carries this file: that the JSON API is on.

SearXNG ships `search.formats: [html]`, so every programmatic query
returns 403 out of the box. The stacklet's settings overlay adds
`json`, and that single line is load-bearing for `stack web search`
and for `stack web ask`. It is also the quietest thing in the stacklet
that can break: the web UI keeps working perfectly when the overlay
stops being applied, so nothing looks wrong until an agent asks a
question and gets a refusal.

A container upgrade, a rename of the mount path, or a future
`use_default_settings` change would all do it. Hence a lane rather
than a comment.

Run via the rig:

    tests/integration/stacktests up web
    tests/integration/stacktests pytest \
        tests/integration/test_web_search_e2e.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "web" / "cli"))

pytestmark = pytest.mark.smoke

SEARCH_BASE = "http://localhost:42080"


@pytest.fixture(scope="module", autouse=True)
def require_search():
    """Skip rather than fail when the optional stacklet is not up.

    `web` is opt-in by design -- a family that never searches never
    installs it -- so its absence is a valid instance state, not a
    broken one.
    """
    try:
        with urllib.request.urlopen(f"{SEARCH_BASE}/healthz", timeout=5) as response:
            if response.status != 200:
                pytest.skip("web stacklet is not healthy")
    except OSError:
        pytest.skip("web stacklet is not running (`stack up web`)")


class TestTheJsonApiIsEnabled:
    """The overlay's whole job, asserted directly."""

    def test_a_json_query_is_not_refused(self):
        """403 here means `search.formats` is back to html-only and the
        settings overlay is not reaching the container."""
        request = urllib.request.Request(
            f"{SEARCH_BASE}/search?q=famstack&format=json",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                assert response.status == 200
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 403:
                pytest.fail(
                    "SearXNG refused format=json (403). `search.formats` is "
                    "html-only -- the settings overlay at "
                    "stacklets/web/config/settings.yml is not mounted."
                )
            raise

        assert "results" in payload

    def test_the_overlay_kept_the_shipped_engines(self):
        """`use_default_settings: true` means the overlay is a patch.
        Without it the file would *replace* the image's settings and
        take every engine with it -- which looks like "search returns
        nothing" rather than like a config error."""
        request = urllib.request.Request(
            f"{SEARCH_BASE}/search?q=immich&format=json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        engines = {r.get("engine") for r in payload.get("results", [])}
        assert engines, "no engine answered; the default engine set is gone"


class TestTheCommandReturnsUsableResults:
    """Driving the CLI's own function, so the test cannot pass while the
    command is broken."""

    def test_results_carry_a_title_and_a_url(self):
        from search import search

        results = search("immich vs photoprism", count=5)

        assert results, "no results for a query that certainly has them"
        for item in results:
            assert item["title"].strip(), f"result with no title: {item}"
            assert item["url"].startswith("http"), f"result with no url: {item}"

    def test_the_count_limit_is_honoured(self):
        """An agent budgets its context by this number, so it is a
        contract rather than a hint."""
        from search import search

        assert len(search("self-hosted photos", count=3)) <= 3
