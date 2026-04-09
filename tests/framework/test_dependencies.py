"""Dependencies: stacklets declare requires, framework enforces it."""


class TestRequiresEnforcement:

    def test_fails_when_dependency_missing(self, make_stack):
        stck = make_stack(stacklets=["basic", "with_deps"])
        result = stck.up("with_deps")
        assert "error" in result
        assert "must be set up first" in result["error"]

    def test_error_names_missing_dep(self, make_stack):
        stck = make_stack(stacklets=["basic", "with_deps"])
        result = stck.up("with_deps")
        assert "basic" in result.get("missing", [])

    def test_error_includes_hint(self, make_stack):
        stck = make_stack(stacklets=["basic", "with_deps"])
        result = stck.up("with_deps")
        assert "stack up basic" in result.get("hint", "")

    def test_passes_when_dep_set_up(self, make_stack):
        stck = make_stack(stacklets=["basic", "with_deps"])
        stck.up("basic")
        result = stck.up("with_deps")
        assert "must be set up first" not in result.get("error", "")

    def test_unknown_stacklet_fails(self, make_stack):
        stck = make_stack()
        result = stck.up("nope")
        assert "error" in result
