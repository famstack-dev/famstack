"""Single sign-on between stacklets through an OpenID Connect provider.

Two sides meet in the framework and never name each other:

- A client stacklet declares `[oidc]` (a display name and its callback
  URLs) and reads `{oidc_issuer}`, `{oidc_client_id}` and
  `{oidc_client_secret}` in its `[env.defaults]`.
- A provider stacklet declares `[oidc_provider]`. Its hooks ask the
  framework which clients exist, register them with the identity
  service, and hand the credentials back to the framework.

The client never learns which provider it got, and the provider never
learns a client's env schema. Without a provider, or before it has
registered a client, the three variables render empty and the client
keeps its own login.
"""

from pathlib import Path


def _write(root: Path, sid: str, body: str) -> None:
    (root / sid).mkdir(parents=True, exist_ok=True)
    (root / sid / "stacklet.toml").write_text(f'id = "{sid}"\n{body}')


CLIENT = """
port = 42020

[env.defaults]
OIDC_ISSUER        = "{oidc_issuer}"
OIDC_CLIENT_ID     = "{oidc_client_id}"
OIDC_CLIENT_SECRET = "{oidc_client_secret}"

[oidc]
name      = "Documents"
callbacks = ["{url}/accounts/oidc/sso/login/callback/"]
"""

PROVIDER = """
port = 42100

[oidc_provider]
"""


def _on_domain(stck, domain: str) -> None:
    """Switch the instance to domain mode, where every URL is predictable."""
    cfg = stck.instance_dir / "stack.toml"
    cfg.write_text(cfg.read_text().replace('domain = ""', f'domain = "{domain}"', 1))


def _login_env(stck, sid="files") -> dict:
    env = stck.env(sid)
    return {k: env[k] for k in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")}


EMPTY = {"OIDC_ISSUER": "", "OIDC_CLIENT_ID": "", "OIDC_CLIENT_SECRET": ""}


class TestClientEnvironment:
    """What a client stacklet sees in its rendered env."""

    def test_without_a_provider_the_login_settings_are_empty(self, make_stack):
        """An install without an identity service keeps each service's own
        login. Empty values are what compose files test for."""
        stck = make_stack()
        _write(stck.root / "stacklets", "files", CLIENT)
        assert _login_env(stck) == EMPTY

    def test_a_provider_that_has_not_registered_the_client_changes_nothing(
            self, make_stack):
        """Between installing the provider and its first provisioning run
        the client has no credentials, and a half-configured login would
        show a button that cannot work."""
        stck = make_stack()
        _write(stck.root / "stacklets", "files", CLIENT)
        _write(stck.mounted_extension_dir, "idp", PROVIDER)
        assert _login_env(stck) == EMPTY

    def test_registered_client_gets_issuer_and_credentials(self, make_stack):
        """The issuer is the provider's public URL, the address browsers
        are redirected to and the one the client fetches discovery from."""
        stck = make_stack()
        _on_domain(stck, "home.test")
        _write(stck.root / "stacklets", "files", CLIENT)
        _write(stck.mounted_extension_dir, "idp", PROVIDER)

        stck.store_oidc_client("files", client_id="c-123", client_secret="s-456")

        assert _login_env(stck) == {
            "OIDC_ISSUER": "http://idp.home.test",
            "OIDC_CLIENT_ID": "c-123",
            "OIDC_CLIENT_SECRET": "s-456",
        }

    def test_credentials_belong_to_the_provider(self, make_stack):
        """Destroying the provider deletes its secrets. The client must then
        fall back to its own login instead of pointing at a dead issuer."""
        stck = make_stack()
        _write(stck.root / "stacklets", "files", CLIENT)
        _write(stck.mounted_extension_dir, "idp", PROVIDER)
        stck.store_oidc_client("files", client_id="c-123", client_secret="s-456")

        stck.secrets.clear("idp")

        assert _login_env(stck) == EMPTY


class TestProviderView:
    """What a provider's hooks read to register clients."""

    def test_lists_each_client_with_callbacks_on_its_own_url(self, make_stack):
        """`{url}` in a callback is the client's public URL, not the
        provider's, because the provider redirects back to the client."""
        stck = make_stack()
        _on_domain(stck, "home.test")
        _write(stck.root / "stacklets", "files", CLIENT)
        _write(stck.root / "stacklets", "plain", "port = 42030\n")
        _write(stck.mounted_extension_dir, "idp", PROVIDER)

        assert stck.oidc_clients() == [{
            "stacklet": "files",
            "name": "Documents",
            "callbacks": ["http://files.home.test/accounts/oidc/sso/login/callback/"],
            "client_id": None,
            "client_secret": None,
        }]

    def test_shows_what_is_already_stored(self, make_stack):
        """A provider must not rotate a secret on every run: the client
        only reads it when its containers are recreated."""
        stck = make_stack()
        _write(stck.root / "stacklets", "files", CLIENT)
        _write(stck.mounted_extension_dir, "idp", PROVIDER)
        stck.store_oidc_client("files", client_id="c-123", client_secret="s-456")

        [client] = stck.oidc_clients()
        assert (client["client_id"], client["client_secret"]) == ("c-123", "s-456")

    def test_the_client_name_defaults_to_the_stacklet_name(self, make_stack):
        stck = make_stack()
        _write(stck.root / "stacklets", "files",
               'name = "Family Files"\nport = 42020\n[oidc]\ncallbacks = []\n')
        _write(stck.mounted_extension_dir, "idp", PROVIDER)

        [client] = stck.oidc_clients()
        assert client["name"] == "Family Files"
