"""stack.ai — model access for stacklets.

Stdlib-safe surface, importable by the host CLI: the role->model router
(`resolve_model`) and the endpoint reachability `probe`. The LLM client
lives in `stack.ai.client`, which imports the OpenAI SDK — so it is NOT
re-exported here and must be imported explicitly by container code, keeping
the host `./stack` dependency-free.
"""

from .models import resolve_model

__all__ = ["resolve_model", "ProbeResult", "probe"]


def __getattr__(name):
    # The probe imports the HTTP and TLS modules, ~40 ms; every `stack`
    # command imports this package for `resolve_model`, few probe.
    if name in ("ProbeResult", "probe"):
        import importlib
        module = importlib.import_module(f"{__name__}.probe")
        # The import binds the submodule to this package's `probe`; the
        # package's `probe` is the function, as it was when imported eagerly.
        globals().update(probe=module.probe, ProbeResult=module.ProbeResult)
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
