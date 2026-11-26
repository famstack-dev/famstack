"""old_speech: remove the speech container the ai stacklet ran before 0.5.

    from stacklets.ai import old_speech

    old_speech.remove(ctx)   # in on_start and on_stop

Before the stack's voices moved to oMLX, the ai stacklet ran Piper through
openedai-speech as the container `stack-ai-speech`. An instance that ran it
keeps it, and its image, after the update, and nothing else uses either.

Both the start and the stop hook remove it. The stop needs it most: the ai
stacklet has no compose file any more, so `stack down ai` stops only the
host services, and a container left up keeps the AI counted as up. The
restart that takes snapshots then refuses to take one.
"""

from stack import docker
from stack.prompt import dim

__all__ = ["CONTAINER", "remove"]

CONTAINER = "stack-ai-speech"


def remove(ctx) -> None:
    """Remove the old speech container and its image, when Docker says the
    container belongs to the ai stacklet's compose project. A container of
    that name in another project stays. Without it, nothing is printed.
    Raises `RuntimeError` when Docker still lists the container after the
    removal, because a container left up keeps the AI counted as up. An
    image Docker does not remove is named and stays."""
    if not _listed():
        return
    ctx.step("Removing the old speech container")
    image = docker.remove_container(CONTAINER)
    # `remove_container` does not report a failed `docker rm`, so Docker,
    # the owner of the container, is asked again.
    if _listed():
        raise RuntimeError(f"Docker did not remove {CONTAINER}")
    if image and not docker.remove_image(image):
        dim(f"Its image {image} stays: Docker did not remove it.")


def _listed() -> bool:
    """Whether Docker lists the container in the ai stacklet's project."""
    return any(c["name"] == CONTAINER for c in docker.containers_for("ai"))
