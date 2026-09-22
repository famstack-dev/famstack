"""Guest stacklet on_install — proves first-run hooks run outside the repo."""


def run(ctx):
    from pathlib import Path

    data_dir = ctx.env.get("GUEST_DATA_DIR", "")
    if not data_dir:
        ctx.step("ERROR: GUEST_DATA_DIR not set")
        return

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / ".install-marker").write_text("installed")
    ctx.step("on_install complete")
