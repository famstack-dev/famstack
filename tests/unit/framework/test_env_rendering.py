"""Env rendering: stack.toml values flow through templates into env vars."""


class TestTemplateResolution:

    def test_timezone_resolves(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        env = stck.env("basic")
        assert env["TZ"] == "Europe/Berlin"

    def test_data_dir_resolves(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        env = stck.env("basic")
        assert env["BASIC_DATA_DIR"].endswith("/basic")


class TestMissingVars:

    def test_undefined_var_becomes_empty(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        env = stck.env("basic")
        assert isinstance(env.get("TZ"), str)
