"""A filing's policy: what the place a capture comes from decides about its card.

    Policy(bucket="family/camping", room_topics=("camping",), read_in="family")
    Policy()          # the sender's own bucket, no topic, every topic of the card's place

A capture room files into its sender's bucket; a topic room into its
topic's (`family/camping`, or `homer/camping` for a personal one) and
puts the room's topic first in the card's tags. The room also limits the
topics the reading is shown to those its people may all read
(`read_in`, `stack.filing.vocabulary`): a person's private topic is
offered in their own room only. The command line has no room and is
offered the shared topics.

``keep_body`` keeps a bookmark's page text on its card; by default the
link and the summary are the card. A note always keeps its words.

The policy is the intake's to build; the core (`stack.filing.file`) only
reads it. Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Policy"]


@dataclass(frozen=True)
class Policy:
    """What a capture's place decides about its card.

    ``bucket`` is where the card files, None for the sender's own.
    ``room_topics`` are the topic ids the card carries first. ``read_in``
    is the bucket of the room the capture came from, None to show every
    topic of the card's place. ``keep_body`` keeps a bookmark's page text.

        Policy(bucket="family/camping", room_topics=("camping",), read_in="family")
    """

    bucket: str | None = None
    room_topics: tuple[str, ...] = ()
    read_in: str | None = None
    keep_body: bool = False
