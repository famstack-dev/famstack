"""Test stacklet on_install — creates data directory and marker file."""


def run(ctx):
    from pathlib import Path

    data_dir = ctx.env.get("TEST_DATA_DIR", "")
    if not data_dir:
        ctx.step("ERROR: TEST_DATA_DIR not set")
        return

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / ".install-marker").write_text("installed")
    ctx.step("on_install complete")
