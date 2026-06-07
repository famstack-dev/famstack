"""stack.ai — model access for stacklets.

Stdlib-safe surface, importable by the host CLI: the role->model router
(`resolve_model`) and the endpoint reachability `probe`. The LLM client
lives in `stack.ai.client`, which imports the OpenAI SDK — so it is NOT
re-exported here and must be imported explicitly by container code, keeping
the host `./stack` dependency-free.
"""

from .models import resolve_model
from .probe import ProbeResult, probe

__all__ = ["resolve_model", "ProbeResult", "probe"]
