"""Creates a marker file to prove the hook ran and had access to ctx."""

from pathlib import Path


def run(ctx):
    data_dir = ctx.env.get("HOOKS_DATA_DIR", "")
    if not data_dir:
        return

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    marker = Path(data_dir) / ".on_install_ran"
    marker.write_text("ok")
    ctx.step("on_install hook executed")
