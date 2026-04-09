"""Secrets: auto-generated, scoped to stacklets, cleaned on destroy."""


class TestSecretGeneration:

    def test_declared_secrets_are_generated(self, make_stack):
        stck = make_stack(stacklets=["with_secrets"])
        stck.env("with_secrets")  # triggers generation
        assert stck.secrets.get("with_secrets", "DB_PASSWORD") is not None

    def test_secrets_are_stable(self, make_stack):
        stck = make_stack(stacklets=["with_secrets"])
        stck.env("with_secrets")
        first = stck.secrets.get("with_secrets", "DB_PASSWORD")
        stck.env("with_secrets")
        second = stck.secrets.get("with_secrets", "DB_PASSWORD")
        assert first == second

    def test_secrets_are_random(self, make_stack):
        stck = make_stack(stacklets=["with_secrets"])
        stck.env("with_secrets")
        a = stck.secrets.get("with_secrets", "DB_PASSWORD")
        b = stck.secrets.get("with_secrets", "API_KEY")
        assert a != b


class TestSecretCleanup:

    def test_destroy_removes_stacklet_secrets(self, make_stack):
        stck = make_stack(stacklets=["with_secrets"])
        stck.up("with_secrets")
        assert stck.secrets.get("with_secrets", "DB_PASSWORD") is not None

        stck.destroy("with_secrets")
        assert stck.secrets.get("with_secrets", "DB_PASSWORD") is None

    def test_destroy_preserves_global_secrets(self, make_stack):
        stck = make_stack(stacklets=["with_secrets"])
        stck.up("with_secrets")
        stck.destroy("with_secrets")
        assert stck.secrets.get("global", "ADMIN_PASSWORD") is not None
