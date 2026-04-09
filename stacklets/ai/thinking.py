"""Disable thinking mode for Qwen3.5 models via the oMLX admin API.

Qwen3.5 defaults to thinking enabled, outputting a reasoning chain before
every response. We disable it via oMLX's per-model settings endpoint,
which sets chat_template_kwargs.enable_thinking = false at the server level.
This affects all requests to the model without callers needing to know.

This is Qwen3.5-specific. Other model families handle thinking differently
or don't have it. Only call this for Qwen3.5 models.
"""


def is_qwen35(model_id: str) -> bool:
    """Check if a model ID looks like a Qwen3.5 variant."""
    lower = model_id.lower()
    return "qwen3.5" in lower or "qwen-3.5" in lower


def disable_thinking(client, model_id: str, log=None) -> bool:
    """Disable thinking for a Qwen3.5 model via oMLX admin API.

    Args:
        client: Authenticated OMLXClient instance.
        model_id: The model name (e.g. "Qwen3.5-9B-MLX-8bit")
        log: Callable(str) for status messages. Defaults to no-op.

    Returns True if thinking is now disabled, False on failure.
    """
    if log is None:
        log = lambda msg: None

    if not is_qwen35(model_id):
        log(f"Not a Qwen3.5 model ({model_id}), skipping")
        return True

    # oMLX uses short model name, not full repo ID
    short_id = model_id.split("/")[-1] if "/" in model_id else model_id

    # Check current state
    settings = client.get_model_settings(short_id)
    if settings is None:
        log(f"Could not read settings for {model_id}")
        return False

    kwargs = settings.get("chat_template_kwargs", {})
    if kwargs.get("enable_thinking") is False:
        log(f"Thinking already disabled for {model_id}")
        return True

    # Disable thinking
    ok = client.update_model_settings(
        short_id,
        chat_template_kwargs={"enable_thinking": False},
    )
    if not ok:
        log(f"Failed to update settings for {short_id}")
        return False

    log(f"Thinking disabled for {short_id}")
    return True
