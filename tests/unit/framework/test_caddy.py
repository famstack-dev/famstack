"""Domain mode: one Caddyfile, assembled from the stacklets that are up.

Each stacklet that serves something in a browser ships a `caddy.snippet`
with its routes. In domain mode the framework assembles the snippets of
every stacklet that is up into `{data_dir}/infra/Caddyfile`, which the
infra stacklet's Caddy container mounts, and has that Caddy reload it
whenever a stacklet starts or stops. Port mode has no proxy, so none of
this happens there.

Whether the result is a Caddyfile Caddy accepts, and what Caddy does with
it, is checked against a real Caddy in tests/integration/test_caddy.py.
These tests pin what the framework promises around it: which snippets go
in, when the file is written, and when Caddy is told to reload.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))

from stack.caddy import assemble  # noqa: E402


PHOTOS = """\
photos.{$STACK_DOMAIN} {
    reverse_proxy stack-photos-server:2283
}
"""

DOCS = """\
docs.{$STACK_DOMAIN} {
    reverse_proxy stack-docs-paperless:8000
}
"""

CORE = """\
{$STACK_DOMAIN} {
    respond "home" 200
}
"""


def _site_addresses(caddyfile: str) -> list[str]:
    """The site addresses of every top-level block, in order.

    A top-level block opens on a line with no indentation that ends in
    `{`. The global options block has no address and is left out.
    """
    addresses = []
    for line in caddyfile.splitlines():
        if line[:1] in ("", " ", "\t", "#", "}"):
            continue
        if line.rstrip().endswith("{") and line.strip() != "{":
            addresses.append(line.rstrip()[:-1].strip())
    return addresses


# ── The assembler ─────────────────────────────────────────────────────────


class TestAssemble:

    def test_each_snippet_becomes_a_site_in_stacklet_order(self):
        caddyfile = assemble([("core", CORE), ("docs", DOCS), ("photos", PHOTOS)])

        sites = _site_addresses(caddyfile)
        assert sites.index("{$STACK_DOMAIN}") < sites.index("docs.{$STACK_DOMAIN}")
        assert sites.index("docs.{$STACK_DOMAIN}") < sites.index("photos.{$STACK_DOMAIN}")

    def test_the_wildcard_is_always_a_site(self):
        """`*.<domain>` answers hosts no running stacklet claims, a stopped
        stacklet among them, and is the site Caddy's wildcard certificate
        belongs to. It is there even when no snippet is."""
        assert "*.{$STACK_DOMAIN}" in _site_addresses(assemble([]))

    def test_the_domain_stays_a_placeholder_for_caddy(self):
        """Caddy substitutes `{$STACK_DOMAIN}` from its own environment, so
        the file does not change when the domain does and never needs to be
        regenerated for it."""
        caddyfile = assemble([("photos", PHOTOS)])
        assert "photos.{$STACK_DOMAIN}" in _site_addresses(caddyfile)

    def test_a_snippet_is_copied_verbatim(self):
        caddyfile = assemble([("photos", PHOTOS)])
        assert PHOTOS.strip() in caddyfile


# ── TLS through a DNS provider ────────────────────────────────────────────


class TestDnsProvider:
    """`[core] dns_provider` turns on HTTPS. Caddy obtains every certificate
    through a DNS-01 challenge at that provider, so nothing on the LAN has
    to be reachable from the internet. The provider's plugin is compiled
    into the infra image; the token reaches Caddy as an environment
    variable and never appears in the file."""

    @pytest.mark.parametrize("provider", ["hetzner", "cloudflare"])
    def test_every_certificate_comes_through_the_providers_dns(self, provider):
        caddyfile = assemble([("photos", PHOTOS)], dns_provider=provider)
        assert f"dns {provider} {{env.DNS_API_TOKEN}}" in caddyfile
        assert "auto_https off" not in caddyfile

    def test_hetzner_waits_before_checking_propagation(self):
        """The Hetzner plugin's README asks for a 30 second delay before
        Caddy starts checking for the TXT record."""
        caddyfile = assemble([], dns_provider="hetzner")
        assert "propagation_delay 30s" in caddyfile

    def test_without_a_provider_no_certificate_is_requested(self):
        assert "cert_issuer" not in assemble([("photos", PHOTOS)])

    def test_an_unknown_provider_is_refused_by_name(self):
        """Caddy would reject a provider it has no plugin for, and only
        once the file is already in place."""
        with pytest.raises(ValueError, match="hetzner.*cloudflare"):
            assemble([], dns_provider="route53")


def _stack(tmp_path, core: str, stacklets: dict[str, str] | None = None):
    """A Stack over throwaway stacklets whose manifests are given in full."""
    from stack import Stack
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text(
        f'[core]\n{core}\nextension_dirs = ["{tmp_path / "ext"}"]\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid, manifest in (stacklets or {}).items():
        sdir = tmp_path / "stacklets" / sid
        sdir.mkdir(parents=True)
        (sdir / "stacklet.toml").write_text(f'id = "{sid}"\n{manifest}')
    return Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())


class TestPublicUrls:
    """Browsers, phones and Element are handed the URLs the stack renders
    (`{url}`, `{photos_url}`, `{home_url}`). Once the proxy has
    certificates those have to be https: Element loaded over https refuses
    to call an http homeserver."""

    PHOTOS = 'port = 42010\n[env.defaults]\nURL = "{url}"\nHOME = "{home_url}"\n'

    def test_with_a_dns_provider_urls_are_https(self, tmp_path):
        stck = _stack(tmp_path, 'domain = "home.example.family"\ndns_provider = "hetzner"',
                      {"photos": self.PHOTOS})
        env = stck.env("photos")
        assert env["URL"] == "https://photos.home.example.family"
        assert env["HOME"] == "https://home.example.family"

    def test_without_one_they_stay_http(self, tmp_path):
        stck = _stack(tmp_path, 'domain = "home.example.family"', {"photos": self.PHOTOS})
        env = stck.env("photos")
        assert env["URL"] == "http://photos.home.example.family"
        assert env["HOME"] == "http://home.example.family"

    def test_https_true_is_for_a_proxy_in_front_of_the_stack(self, tmp_path):
        stck = _stack(tmp_path, 'domain = "home.example.family"\nhttps = true',
                      {"photos": self.PHOTOS})
        assert stck.env("photos")["URL"] == "https://photos.home.example.family"

    def test_https_false_means_http(self, tmp_path):
        stck = _stack(tmp_path, 'domain = "home.example.family"\nhttps = false',
                      {"photos": self.PHOTOS})
        assert stck.env("photos")["URL"] == "http://photos.home.example.family"


# ── Lifecycle ─────────────────────────────────────────────────────────────


@pytest.fixture
def docker(monkeypatch):
    """Docker at its boundary: which compose projects are up, and every
    command run inside a container."""

    class Docker:
        states: dict[str, str] = {}
        execs: list[tuple] = []

    fake = Docker()
    fake.states = {}
    fake.execs = []
    monkeypatch.setattr("stack.docker.project_states", lambda: dict(fake.states))
    monkeypatch.setattr("stack.docker.running_project_ids",
                        lambda: {s for s, st in fake.states.items() if st == "running"})
    monkeypatch.setattr("stack.docker.ensure_network", lambda: ("stack", None))

    def exec_in(container, *cmd):
        fake.execs.append((container, *cmd))
        return 0, ""

    monkeypatch.setattr("stack.docker.exec_in", exec_in)
    return fake


def _make_cli(tmp_path, domain="home.example.family"):
    """A stack with infra (the proxy) and two stacklets that have routes.

    None of them has a compose file, so the CLI runs the framework side of
    each lifecycle step and no container is started.
    """
    from stack import Stack
    from stack.cli import CLI
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text(
        f'[core]\ndomain = "{domain}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    for sid, snippet in (("infra", ""), ("docs", DOCS), ("photos", PHOTOS)):
        sdir = tmp_path / "stacklets" / sid
        sdir.mkdir(parents=True)
        (sdir / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid.title()}"\n')
        if snippet:
            (sdir / "caddy.snippet").write_text(snippet)

    output = CollectorOutput()
    return CLI(Stack(root=tmp_path, data=tmp_path / "data", output=output)), output


def _caddyfile(tmp_path) -> Path:
    return tmp_path / "data" / "infra" / "Caddyfile"


RELOAD = ("stack-infra-caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile")


class TestLifecycle:

    def test_up_routes_the_stacklet_it_starts(self, tmp_path, docker):
        """The Caddyfile is written before the stacklet's containers start,
        so it already counts the stacklet being started as up."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running", "docs": "running"}

        assert cli.up("photos")["ok"]

        sites = _site_addresses(_caddyfile(tmp_path).read_text())
        assert "photos.{$STACK_DOMAIN}" in sites
        assert "docs.{$STACK_DOMAIN}" in sites
        assert docker.execs == [RELOAD]

    def test_a_stacklet_that_is_not_up_gets_no_route(self, tmp_path, docker):
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running", "docs": "stopped"}

        cli.up("photos")

        assert "docs.{$STACK_DOMAIN}" not in _site_addresses(_caddyfile(tmp_path).read_text())

    def test_a_crash_looping_stacklet_keeps_its_route(self, tmp_path, docker):
        """A stacklet with one container restarting still has the others
        serving, and a proxy that dropped it would hide the rest too."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running", "docs": "failing"}

        cli.up("photos")

        assert "docs.{$STACK_DOMAIN}" in _site_addresses(_caddyfile(tmp_path).read_text())

    def test_a_stacklet_without_containers_keeps_its_route_while_up(self, tmp_path, docker):
        """A stacklet that runs host services only (the ai stacklet's LLM
        and speech-to-text) is up from `stack up` to `stack down`, though
        Docker has no container of it to report. Starting another
        stacklet rewrites the Caddyfile and keeps its route."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running"}
        cli.up("photos")

        cli.up("docs")

        assert "photos.{$STACK_DOMAIN}" in _site_addresses(_caddyfile(tmp_path).read_text())

    def test_down_removes_the_route(self, tmp_path, docker):
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running", "photos": "running"}
        cli.up("photos")
        docker.execs.clear()

        cli.down("photos")

        assert "photos.{$STACK_DOMAIN}" not in _site_addresses(_caddyfile(tmp_path).read_text())
        assert docker.execs == [RELOAD]

    def test_destroy_removes_the_route(self, tmp_path, docker):
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running", "photos": "running"}
        cli.up("photos")
        docker.states = {"infra": "running"}
        docker.execs.clear()

        cli.destroy("photos")

        assert "photos.{$STACK_DOMAIN}" not in _site_addresses(_caddyfile(tmp_path).read_text())
        assert docker.execs == [RELOAD]

    def test_starting_infra_writes_the_caddyfile_its_container_mounts(self, tmp_path, docker):
        """Caddy reads the file the moment its container starts. A bind
        mount whose source is missing is created as a directory, so the
        file has to exist before that."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"docs": "running"}

        cli.up("infra")

        assert "docs.{$STACK_DOMAIN}" in _site_addresses(_caddyfile(tmp_path).read_text())

    def test_the_admins_own_routes_come_after_the_stacklets(self, tmp_path, docker):
        """Services that are not stacklets (a dashboard, a Docker UI) get
        their sites from `Caddyfile.local` next to the assembled file. Only
        one proxy can own ports 80 and 443, so they have to live in this
        one, and the file is the admin's: it is read, never written."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"infra": "running"}
        local = tmp_path / "data" / "infra" / "Caddyfile.local"
        local.parent.mkdir(parents=True)
        local.write_text("status.{$STACK_DOMAIN} {\n\treverse_proxy homepage:3000\n}\n")

        cli.up("photos")

        sites = _site_addresses(_caddyfile(tmp_path).read_text())
        assert sites.index("photos.{$STACK_DOMAIN}") < sites.index("status.{$STACK_DOMAIN}")
        assert local.read_text() == "status.{$STACK_DOMAIN} {\n\treverse_proxy homepage:3000\n}\n"

    def test_without_infra_up_nothing_is_written_or_reloaded(self, tmp_path, docker):
        """With no proxy running there is nobody to read the file, and
        writing it would create infra's data directory for a stacklet that
        was never started."""
        cli, _ = _make_cli(tmp_path)
        docker.states = {"docs": "running"}

        cli.up("photos")

        assert not _caddyfile(tmp_path).exists()
        assert docker.execs == []

    def test_port_mode_has_no_proxy(self, tmp_path, docker):
        cli, _ = _make_cli(tmp_path, domain="")
        docker.states = {"infra": "running", "docs": "running"}

        cli.up("photos")
        cli.down("photos")

        assert not _caddyfile(tmp_path).exists()
        assert docker.execs == []

    def test_a_failed_reload_is_reported_and_does_not_fail_the_step(self, tmp_path, docker, monkeypatch):
        """Caddy keeps serving its previous config when a reload is
        rejected, so the stacklet is up either way. The admin needs to
        know the routes did not change."""
        cli, output = _make_cli(tmp_path)
        docker.states = {"infra": "running"}
        monkeypatch.setattr("stack.docker.exec_in",
                            lambda *a: (1, "Error: adapting config: bad directive\n"))

        assert cli.up("photos")["ok"]
        assert any("bad directive" in w for w in output.warnings)

    def test_a_reload_that_raises_is_reported_and_does_not_fail_the_step(
            self, tmp_path, docker, monkeypatch):
        """`docker exec` can time out, and the docker command can be
        missing. The stacklet is up either way."""
        cli, output = _make_cli(tmp_path)
        docker.states = {"infra": "running"}

        def missing(*cmd):
            raise FileNotFoundError("docker")

        monkeypatch.setattr("stack.docker.exec_in", missing)

        assert cli.up("photos")["ok"]
        assert any("previous routes" in w for w in output.warnings)

    def test_an_unreadable_local_file_leaves_the_caddyfile_as_it_was(self, tmp_path, docker):
        """`Caddyfile.local` is the admin's, and may be saved in another
        encoding. The stacklet starts; the routes stay as they were."""
        cli, output = _make_cli(tmp_path)
        docker.states = {"infra": "running"}
        cli.up("photos")
        before = _caddyfile(tmp_path).read_text()
        (tmp_path / "data" / "infra" / "Caddyfile.local").write_bytes(b"# caf\xe9\n")

        assert cli.up("docs")["ok"]

        assert _caddyfile(tmp_path).read_text() == before
        assert any("Caddyfile.local" in w for w in output.warnings)

    def test_an_unknown_dns_provider_leaves_the_caddyfile_as_it_was(self, tmp_path, docker):
        """A typo in stack.toml must not replace a working proxy config
        with one Caddy cannot load. Infra's own start refuses the value
        outright; for every other stacklet the routes stay as they were."""
        cli, output = _make_cli(tmp_path)
        docker.states = {"infra": "running", "docs": "running"}
        cli.up("photos")
        before = _caddyfile(tmp_path).read_text()

        config = tmp_path / "stack.toml"
        config.write_text(config.read_text() + 'dns_provider = "route53"\n')
        cli.down("docs")

        assert _caddyfile(tmp_path).read_text() == before
        assert any("route53" in w for w in output.warnings)


# ── Snippets filled from the stack's settings ─────────────────────────────


AI_SNIPPET = (Path(__file__).parents[3] / "stacklets" / "ai" / "caddy.snippet").read_text()


def _make_ai_cli(tmp_path, ai: str, domain="home.example.family"):
    """A stack with the proxy, photos, and the ai stacklet's shipped
    snippet, with this `[ai]` section in stack.toml."""
    cli, output = _make_cli(tmp_path, domain=domain)
    config = tmp_path / "stack.toml"
    config.write_text(config.read_text() + f"\n[ai]\n{ai}\n")
    sdir = tmp_path / "stacklets" / "ai"
    sdir.mkdir()
    (sdir / "stacklet.toml").write_text('id = "ai"\nname = "AI"\n')
    (sdir / "caddy.snippet").write_text(AI_SNIPPET)
    return cli, output


def _site(caddyfile: str, address: str) -> str:
    """The body of the top-level site block at `address`, or ''."""
    lines = caddyfile.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == f"{address} {{":
            body = []
            for inner in lines[i + 1:]:
                if inner.startswith("}"):
                    return "\n".join(body)
                body.append(inner)
    return ""


def _upstream(caddyfile: str, address: str) -> str:
    """The first `reverse_proxy` upstream of the site at `address`."""
    for line in _site(caddyfile, address).splitlines():
        tokens = line.split()
        if tokens[:1] == ["reverse_proxy"]:
            return tokens[1]
    return ""


OPENAI = "openai.{$STACK_DOMAIN}"
STT = "stt.{$STACK_DOMAIN}"


class TestTheAiRoutes:
    """`openai.<domain>` and `stt.<domain>` go to the AI server the stack
    uses, `[ai] openai_url` and `[ai] whisper_url`, whoever serves it."""

    def _assembled(self, tmp_path, docker, ai: str):
        cli, output = _make_ai_cli(tmp_path, ai)
        docker.states = {"infra": "running", "ai": "running"}
        cli.up("photos")
        return _caddyfile(tmp_path).read_text(), output

    def test_an_app_on_this_mac_is_reached_through_the_host_gateway(self, tmp_path, docker):
        caddyfile, _ = self._assembled(
            tmp_path, docker, 'openai_url = "http://localhost:8888/v1"')

        assert _upstream(caddyfile, OPENAI) == "http://host.docker.internal:8888"
        # No speech server: voice goes to the AI server.
        assert _upstream(caddyfile, STT) == "http://host.docker.internal:8888"

    def test_a_speech_server_of_its_own_gets_stt(self, tmp_path, docker):
        caddyfile, _ = self._assembled(
            tmp_path, docker,
            'openai_url = "http://localhost:8888/v1"\nwhisper_url = "http://127.0.0.1:9000/v1"')

        assert _upstream(caddyfile, STT) == "http://host.docker.internal:9000"

    def test_another_host_is_forwarded_with_its_own_name(self, tmp_path, docker):
        """A server behind its own proxy answers only for its own name."""
        caddyfile, _ = self._assembled(
            tmp_path, docker, 'openai_url = "https://ai.example.lan/v1"')

        assert _upstream(caddyfile, OPENAI) == "https://ai.example.lan"
        assert "header_up Host {upstream_hostport}" in _site(caddyfile, OPENAI)

    def test_the_macs_ipv6_loopback_is_reached_through_the_host_gateway(self, tmp_path, docker):
        caddyfile, _ = self._assembled(
            tmp_path, docker, 'openai_url = "http://[::1]:8888/v1"')

        assert _upstream(caddyfile, OPENAI) == "http://host.docker.internal:8888"

    @pytest.mark.parametrize("url", [
        "http://ai.lan:abc/v1", "http://ai.lan:70000/v1", "http://[::1/v1", "localhost:8000/v1",
        "http://ai lan/v1", "http://a{env.X}b/v1", "http://ai.lan}/v1"])
    def test_an_unusable_address_leaves_only_the_ai_out(self, tmp_path, docker, url):
        """A hand-edited `[ai]` must not stop the stack, nor cost the
        family its other routes."""
        caddyfile, output = self._assembled(tmp_path, docker, f'openai_url = "{url}"')

        sites = _site_addresses(caddyfile)
        assert OPENAI not in sites
        assert "photos.{$STACK_DOMAIN}" in sites
        assert any(w.startswith("ai ") for w in output.warnings)

    def test_no_ai_address_leaves_the_ai_out_without_a_warning(self, tmp_path, docker):
        """An empty `[ai] openai_url` is not set: there is no server to
        route to, and nothing to warn about."""
        caddyfile, output = self._assembled(tmp_path, docker, 'openai_url = ""')

        sites = _site_addresses(caddyfile)
        assert OPENAI not in sites and STT not in sites
        assert "photos.{$STACK_DOMAIN}" in sites
        assert not [w for w in output.warnings if w.startswith("ai ")]

    def test_no_speech_server_sends_stt_to_the_ai_server_without_a_warning(self, tmp_path, docker):
        caddyfile, output = self._assembled(
            tmp_path, docker, 'openai_url = "http://localhost:8888/v1"\nwhisper_url = ""')

        assert _upstream(caddyfile, STT) == "http://host.docker.internal:8888"
        assert not [w for w in output.warnings if w.startswith("ai ")]

    def test_containers_get_the_same_address_as_the_proxy(self, tmp_path):
        """`{ai_openai_url_docker}` is what a container calls; a server on
        the Mac's loopback is reached through the host gateway, whichever
        name for the loopback `[ai]` uses."""
        stck = _stack(tmp_path, 'domain = ""',
                      {"docs": '[env.defaults]\nAI = "{ai_openai_url_docker}"\n'})
        config = tmp_path / "stack.toml"
        config.write_text(config.read_text() + '[ai]\nopenai_url = "http://127.0.0.1:9000/v1"\n')

        assert stck.env("docs")["AI"] == "http://host.docker.internal:9000/v1"

    def test_back_on_the_managed_engine_the_routes_return_to_its_ports(self, tmp_path, docker):
        """The routes follow `[ai]` both ways: to a server elsewhere and back
        to the engine the stack manages on 42060 and 42062."""
        cli, _ = _make_ai_cli(tmp_path, 'openai_url = "https://ai.example.lan/v1"')
        docker.states = {"infra": "running", "ai": "running"}
        cli.up("photos")
        assert _upstream(_caddyfile(tmp_path).read_text(), OPENAI) == "https://ai.example.lan"

        config = tmp_path / "stack.toml"
        config.write_text(config.read_text().replace(
            'openai_url = "https://ai.example.lan/v1"',
            'provider = "managed"\nopenai_url = "http://localhost:42060/v1"\n'
            'whisper_url = "http://localhost:42062/v1"'))
        cli.up("docs")

        caddyfile = _caddyfile(tmp_path).read_text()
        assert _upstream(caddyfile, OPENAI) == "http://host.docker.internal:42060"
        assert _upstream(caddyfile, STT) == "http://host.docker.internal:42062"

    def test_an_upstream_that_is_the_proxy_itself_leaves_only_the_ai_out(self, tmp_path, docker):
        """Forwarding `openai.<domain>` to itself would loop, and Caddy
        refuses a whole file for one bad site, so the other routes have to
        stay."""
        caddyfile, output = self._assembled(
            tmp_path, docker, 'openai_url = "http://openai.home.example.family/v1"')

        sites = _site_addresses(caddyfile)
        assert OPENAI not in sites and STT not in sites
        assert "photos.{$STACK_DOMAIN}" in sites
        assert any("openai.home.example.family" in w for w in output.warnings)

    def test_the_proxys_own_name_is_found_in_any_case(self, tmp_path, docker):
        """Host names do not depend on case: `OpenAI.home.example.family`
        is the proxy's own site on the domain `Home.Example.Family`."""
        cli, output = _make_ai_cli(tmp_path, 'openai_url = "http://OpenAI.home.example.family/v1"',
                                   domain="Home.Example.Family")
        docker.states = {"infra": "running", "ai": "running"}

        cli.up("photos")

        assert OPENAI not in _site_addresses(_caddyfile(tmp_path).read_text())
        assert any("openai.home.example.family" in w.lower() for w in output.warnings)

    def test_the_proxys_own_name_is_found_with_a_trailing_dot(self, tmp_path, docker):
        """`openai.home.example.family.` is the same host, written as a
        fully qualified name."""
        caddyfile, output = self._assembled(
            tmp_path, docker, 'openai_url = "http://openai.home.example.family./v1"')

        assert OPENAI not in _site_addresses(caddyfile)

    def test_comments_keep_the_names_they_mention(self):
        """Only `{name}` of a template variable outside comments is filled;
        Caddy's own placeholders stay for Caddy."""
        from stack.caddy import fill

        snippet = ("# serves {domain}\nx.{$STACK_DOMAIN} {\n"
                   "\trespond \"{host} {domain}\" 200\n}\n")
        filled = fill(snippet, {"domain": "home.example.family"})

        assert filled == ("# serves {domain}\nx.{$STACK_DOMAIN} {\n"
                          "\trespond \"{host} home.example.family\" 200\n}\n")

    def test_a_snippet_without_variables_is_written_as_before(self, tmp_path, docker):
        cli, _ = _make_ai_cli(tmp_path, 'openai_url = "http://localhost:8888/v1"')
        docker.states = {"infra": "running"}

        cli.up("photos")

        assert PHOTOS.strip() in _caddyfile(tmp_path).read_text()
