"""Stack.is_installed / is_running / is_healthy / wait_for_healthy.

These replace the `stacklet["enabled"]` proxy (which was overloaded for
three distinct states: installed, running, healthy). Each method has a
single honest meaning and a single source of truth.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    yield


def _create_stacklet(root, sid, health=None):
    sdir = root / "stacklets" / sid
    (sdir / "hooks").mkdir(parents=True, exist_ok=True)
    lines = [
        f'id = "{sid}"',
        f'name = "{sid.title()}"',
        'version = "0.1.0"',
        'category = "test"',
    ]
    if health:
        lines.extend(["", "[health]", f'url = "{health}"'])
    (sdir / "stacklet.toml").write_text("\n".join(lines) + "\n")


def _make_stack(tmp_path, running_ids: set[str] | None = None):
    from stack import Stack
    stck = Stack(root=tmp_path, data=tmp_path / "data")
    if running_ids is not None:
        stck._running_project_ids = lambda: running_ids  # type: ignore
    return stck


# ── is_installed ─────────────────────────────────────────────────────────

class TestIsInstalled:
    def test_false_before_setup_marker(self, tmp_path):
        _create_stacklet(tmp_path, "myapp")
        stck = _make_stack(tmp_path)
        assert not stck.is_installed("myapp")

    def test_true_after_setup_marker(self, tmp_path):
        _create_stacklet(tmp_path, "myapp")
        stck = _make_stack(tmp_path)
        stck._mark_setup_done("myapp")
        assert stck.is_installed("myapp")


# ── is_running ───────────────────────────────────────────────────────────

class TestIsRunning:
    def test_false_when_no_containers(self, tmp_path):
        _create_stacklet(tmp_path, "myapp")
        stck = _make_stack(tmp_path, running_ids=set())
        assert not stck.is_running("myapp")

    def test_true_when_in_running_set(self, tmp_path):
        _create_stacklet(tmp_path, "myapp")
        stck = _make_stack(tmp_path, running_ids={"myapp", "other"})
        assert stck.is_running("myapp")


# ── is_healthy ───────────────────────────────────────────────────────────

class TestIsHealthy:
    def test_no_health_block_delegates_to_running(self, tmp_path):
        # Without a [health] declaration, healthy ≡ running.
        _create_stacklet(tmp_path, "myapp")  # no health
        stck = _make_stack(tmp_path, running_ids={"myapp"})
        assert stck.is_healthy("myapp")

        stck2 = _make_stack(tmp_path, running_ids=set())
        assert not stck2.is_healthy("myapp")

    def test_200_response_is_healthy(self, tmp_path, httpserver):
        httpserver.expect_request("/ping").respond_with_data("ok", status=200)
        _create_stacklet(tmp_path, "myapp", health=httpserver.url_for("/ping"))
        stck = _make_stack(tmp_path, running_ids={"myapp"})
        assert stck.is_healthy("myapp")

    def test_500_response_is_not_healthy(self, tmp_path, httpserver):
        httpserver.expect_request("/ping").respond_with_data("fail", status=500)
        _create_stacklet(tmp_path, "myapp", health=httpserver.url_for("/ping"))
        stck = _make_stack(tmp_path, running_ids={"myapp"})
        assert not stck.is_healthy("myapp")

    def test_401_response_is_healthy(self, tmp_path, httpserver):
        # HTTP layer is up, credentials just aren't present. The health
        # probe is a liveness check, not an auth check — treat as healthy.
        httpserver.expect_request("/ping").respond_with_data("unauth", status=401)
        _create_stacklet(tmp_path, "myapp", health=httpserver.url_for("/ping"))
        stck = _make_stack(tmp_path, running_ids={"myapp"})
        assert stck.is_healthy("myapp")

    def test_unreachable_url_is_not_healthy(self, tmp_path):
        _create_stacklet(tmp_path, "myapp", health="http://127.0.0.1:1/ping")
        stck = _make_stack(tmp_path, running_ids={"myapp"})
        assert not stck.is_healthy("myapp")


# ── wait_for_healthy ─────────────────────────────────────────────────────

class TestWaitForHealthy:
    def test_returns_immediately_when_already_healthy(self, tmp_path, httpserver):
        httpserver.expect_request("/ping").respond_with_data("ok", status=200)
        _create_stacklet(tmp_path, "myapp", health=httpserver.url_for("/ping"))
        stck = _make_stack(tmp_path, running_ids={"myapp"})

        t0 = time.monotonic()
        stck.wait_for_healthy("myapp", timeout=5.0)
        assert time.monotonic() - t0 < 1.0

    def test_raises_on_timeout_naming_the_stacklet(self, tmp_path):
        from stack.stack import StackletNotHealthyError

        _create_stacklet(tmp_path, "myapp", health="http://127.0.0.1:1/ping")
        stck = _make_stack(tmp_path, running_ids={"myapp"})

        with pytest.raises(StackletNotHealthyError) as exc:
            stck.wait_for_healthy("myapp", timeout=0.5)
        assert "myapp" in str(exc.value)

    def test_waits_until_service_flips_to_healthy(self, tmp_path, httpserver):
        # Start returning 500; switch to 200 on the second request.
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            from werkzeug.wrappers import Response
            if state["count"] < 2:
                return Response("not yet", status=500)
            return Response("ok", status=200)

        httpserver.expect_request("/ping").respond_with_handler(handler)
        _create_stacklet(tmp_path, "myapp", health=httpserver.url_for("/ping"))
        stck = _make_stack(tmp_path, running_ids={"myapp"})

        stck.wait_for_healthy("myapp", timeout=5.0)
        assert state["count"] >= 2
