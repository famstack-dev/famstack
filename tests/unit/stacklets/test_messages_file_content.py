"""A file posted from the CLI looks like one a phone posts.

The archivist reads a caption the way the spec defines it (v1.10, media
captions): with a caption, `body` carries the words and `filename` the
name; without one, `body` is the name and there is no `filename`. The
CLI's uploads drive the rig, so they must have the same shape.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "messages" / "cli"))

from _matrix import file_content  # noqa: E402


def test_a_caption_is_the_body_and_the_name_moves_to_filename():
    content = file_content("mxc://s/a", "IMG_1.jpg", "image/jpeg",
                           msgtype="m.image", caption="the new tent")
    assert content["body"] == "the new tent"
    assert content["filename"] == "IMG_1.jpg"


def test_without_a_caption_the_body_is_the_name():
    content = file_content("mxc://s/a", "IMG_1.jpg", "image/jpeg", msgtype="m.image")
    assert content["body"] == "IMG_1.jpg"
    assert "filename" not in content
