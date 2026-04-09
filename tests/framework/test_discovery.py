"""Stacklet discovery: finds stacklets by walking the filesystem."""


class TestDiscovery:

    def test_finds_fixture_stacklets(self, make_stack):
        stck = make_stack(stacklets=["basic", "with_secrets", "with_hooks"])
        ids = {s["id"] for s in stck.discover()}
        assert "basic" in ids
        assert "with_secrets" in ids
        assert "with_hooks" in ids

    def test_reads_manifest_fields(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        by_id = {s["id"]: s for s in stck.discover()}
        assert by_id["basic"]["name"] == "Basic"
        assert by_id["basic"]["description"] == "Minimal stacklet for framework testing"

    def test_reads_category(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        by_id = {s["id"]: s for s in stck.discover()}
        assert by_id["basic"]["category"] == "test"
