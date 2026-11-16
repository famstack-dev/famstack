"""The message group: what one person sends in one go.

These tests drive `stack.message_groups` the way a caller does: a list of
room messages in, groups out. A message here is a plain value with the
fields the `Groupable` protocol names. Times are minutes past 18:00 on a
fixed day, turned into epoch milliseconds, so a test reads like a room.
"""

from dataclasses import dataclass

from stack.message_groups import (
    GROUP_WINDOW_S,
    group_messages,
    is_close_marker,
    is_queued,
)

MINUTE_MS = 60_000
SECOND_MS = 1_000
EIGHTEEN_HUNDRED_MS = 1_700_000_000_000


@dataclass(frozen=True)
class Msg:
    event_id: str
    sender: str
    ts: int
    kind: str = "text"
    duration_ms: int | None = None
    body: str = ""
    parent: str | None = None


def at(minutes: float) -> int:
    return EIGHTEEN_HUNDRED_MS + int(minutes * MINUTE_MS)


def ids(group) -> list[str]:
    return [m.event_id for m in group.messages]


def test_the_window_is_five_minutes():
    assert GROUP_WINDOW_S == 300


def test_five_photos_and_two_texts_each_within_five_minutes_form_one_group():
    """Given Homer's seven messages, each under 5 minutes after the last,
    then they are one group, in order."""
    room = [Msg(f"$p{i}", "homer", at(i), "image") for i in range(5)]
    room += [Msg("$t1", "homer", at(6)), Msg("$t2", "homer", at(9))]

    groups = group_messages(room)

    assert len(groups) == 1
    assert ids(groups[0]) == ["$p0", "$p1", "$p2", "$p3", "$p4", "$t1", "$t2"]
    assert groups[0].sender == "homer"


def test_a_pause_of_five_minutes_starts_a_new_group():
    room = [Msg("$a", "homer", at(0)), Msg("$b", "homer", at(5))]

    groups = group_messages(room)

    assert [ids(g) for g in groups] == [["$a"], ["$b"]]


def test_a_pause_just_under_five_minutes_keeps_the_group():
    room = [Msg("$a", "homer", at(0)), Msg("$b", "homer", at(4.9))]

    assert len(group_messages(room)) == 1


def test_the_pause_is_measured_from_the_last_message_not_the_first():
    room = [Msg(f"$m{i}", "homer", at(i * 4)) for i in range(4)]

    assert len(group_messages(room)) == 1


def test_a_close_marker_ends_the_group_and_belongs_to_none():
    """Given "--" after two messages and one more a minute later, then the
    marker is in no group and names itself as the ending message of the
    group it closed."""
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$b", "homer", at(1)),
        Msg("$end", "homer", at(2), body="--"),
        Msg("$c", "homer", at(3)),
    ]

    first, second = group_messages(room)

    assert ids(first) == ["$a", "$b"]
    assert first.ended_by == "$end"
    assert ids(second) == ["$c"]
    assert all("$end" not in ids(g) for g in (first, second))


def test_a_group_without_a_marker_is_ended_by_its_last_message():
    room = [Msg("$a", "homer", at(0)), Msg("$b", "homer", at(1))]

    assert group_messages(room)[0].ended_by == "$b"


def test_the_phone_spellings_of_the_marker_count():
    for text in ("--", "---", "—", "–", " -- ", "-–"):
        assert is_close_marker(text), text
    for text in ("-", "", "  ", "-a-", "hello --", "- -", "a—b"):
        assert not is_close_marker(text), text


def test_every_phone_spelling_ends_the_group_in_a_room():
    for marker in ("--", "---", "—", "–"):
        room = [
            Msg("$a", "homer", at(0)),
            Msg("$end", "homer", at(1), body=marker),
            Msg("$b", "homer", at(2)),
        ]
        assert [ids(g) for g in group_messages(room)] == [["$a"], ["$b"]], marker


def test_a_marker_with_no_open_group_belongs_to_none():
    room = [Msg("$end", "homer", at(0), body="--"), Msg("$a", "homer", at(1))]

    groups = group_messages(room)

    assert [ids(g) for g in groups] == [["$a"]]


def test_another_persons_message_in_between_ends_nothing():
    """Given Homer at 18:00, 18:02, 18:03 and Marge at 18:01, then Homer's
    three are one group and Marge's message is her own."""
    room = [
        Msg("$h1", "homer", at(0), "image"),
        Msg("$m1", "marge", at(1)),
        Msg("$h2", "homer", at(2), "image"),
        Msg("$h3", "homer", at(3), "image"),
    ]

    groups = group_messages(room)

    by_sender = {g.sender: ids(g) for g in groups}
    assert by_sender == {"homer": ["$h1", "$h2", "$h3"], "marge": ["$m1"]}


def test_messages_are_grouped_in_time_order_whatever_the_list_order():
    room = [Msg("$b", "homer", at(1)), Msg("$a", "homer", at(0))]

    assert ids(group_messages(room)[0]) == ["$a", "$b"]


def test_a_reply_joins_the_group_of_the_message_it_answers_at_any_time():
    """A reply a day later still joins, and starts no group of its own."""
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$r", "marge", at(60 * 24), parent="$a"),
    ]

    groups = group_messages(room)

    assert len(groups) == 1
    assert ids(groups[0]) == ["$a"]
    assert [m.event_id for m in groups[0].replies] == ["$r"]


def test_a_reply_to_a_reply_joins_the_same_group():
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$r1", "marge", at(10), parent="$a"),
        Msg("$r2", "homer", at(20), parent="$r1"),
    ]

    groups = group_messages(room)

    assert len(groups) == 1
    assert [m.event_id for m in groups[0].replies] == ["$r1", "$r2"]


def test_a_reply_to_the_close_marker_joins_the_group_it_closed():
    """The archivist answers the marker, so a reply there is in the thread
    of the group's notice."""
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$end", "homer", at(1), body="--"),
        Msg("$r", "marge", at(30), parent="$end"),
    ]

    groups = group_messages(room)

    assert len(groups) == 1
    assert [m.event_id for m in groups[0].replies] == ["$r"]


def test_a_reply_does_not_keep_the_senders_own_group_open():
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$old", "marge", at(1)),
        Msg("$r", "homer", at(4), parent="$old"),
        Msg("$b", "homer", at(8)),
    ]

    groups = {g.messages[0].event_id: g for g in group_messages(room)}

    assert ids(groups["$a"]) == ["$a"]
    assert ids(groups["$b"]) == ["$b"]


def test_a_reply_to_a_message_not_in_the_room_is_an_ordinary_message():
    room = [Msg("$r", "marge", at(0), parent="$gone")]

    groups = group_messages(room)

    assert [ids(g) for g in groups] == [["$r"]]


def test_the_callers_apart_starts_a_new_group():
    """The diary's reason: a message that names another day."""
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$b", "homer", at(1), body="yesterday"),
        Msg("$c", "homer", at(2)),
    ]

    def names_another_day(group, message):
        return message.body == "yesterday"

    groups = group_messages(room, apart=names_another_day)

    assert [ids(g) for g in groups] == [["$a"], ["$b", "$c"]]


def test_apart_sees_the_open_group_and_the_message():
    seen = []

    def spy(group, message):
        seen.append((ids(group), message.event_id))
        return False

    group_messages([Msg("$a", "homer", at(0)), Msg("$b", "homer", at(1))], apart=spy)

    assert seen == [(["$a"], "$b")]


def test_a_group_already_on_a_card_comes_back_as_it_was():
    """Given the first two messages are on a card, then a third a minute
    later does not join them, and the filed group is returned unchanged."""
    first, second = Msg("$a", "homer", at(0)), Msg("$b", "homer", at(1))
    on_file = group_messages([first, second])[0]
    room = [first, second, Msg("$c", "homer", at(2))]

    groups = group_messages(
        room, filed={"$a": on_file, "$b": on_file}
    )

    assert groups[0] is on_file
    assert [ids(g) for g in groups] == [["$a", "$b"], ["$c"]]


def test_a_reply_to_a_filed_group_joins_it_and_starts_no_group():
    """Given Bart's group is on a card and Lisa replies to his message a day
    later, then the result is Bart's group with Lisa's reply, and no group of
    Lisa's. Bart's messages are as they were."""
    bart = [Msg("$b1", "bart", at(0)), Msg("$b2", "bart", at(1))]
    on_file = group_messages(bart)[0]
    room = [*bart, Msg("$l", "lisa", at(60 * 24), parent="$b2")]

    groups = group_messages(room, filed={"$b1": on_file, "$b2": on_file})

    assert len(groups) == 1
    assert groups[0].sender == "bart"
    assert groups[0].messages == on_file.messages
    assert [m.event_id for m in groups[0].replies] == ["$l"]
    assert on_file.replies == ()


def test_a_reply_to_a_reply_of_a_filed_group_joins_it_too():
    bart = Msg("$b", "bart", at(0))
    on_file = group_messages([bart])[0]
    room = [
        bart,
        Msg("$l", "lisa", at(10), parent="$b"),
        Msg("$h", "homer", at(20), parent="$l"),
    ]

    groups = group_messages(room, filed={"$b": on_file})

    assert len(groups) == 1
    assert [m.event_id for m in groups[0].replies] == ["$l", "$h"]


def test_replies_already_on_a_filed_group_are_not_added_twice():
    bart = Msg("$b", "bart", at(0))
    reply = Msg("$l", "lisa", at(10), parent="$b")
    on_file = group_messages([bart, reply])[0]

    groups = group_messages([bart, reply], filed={"$b": on_file})

    assert groups == [on_file]
    assert groups[0] is on_file


def test_a_filed_group_keeps_its_place_in_time_order():
    early = Msg("$a", "homer", at(0))
    on_file = group_messages([early])[0]
    room = [Msg("$m", "marge", at(-30)), early, Msg("$n", "marge", at(60))]

    groups = group_messages(room, filed={"$a": on_file})

    assert [g.sender for g in groups] == ["marge", "homer", "marge"]


def test_two_voice_messages_closer_than_the_second_lasts_are_queued():
    """Given a phone that flushed its queue: 40 s of voice, 2 s apart."""
    room = [
        Msg("$v1", "homer", at(0), "voice", duration_ms=40 * SECOND_MS),
        Msg("$v2", "homer", at(0) + 2 * SECOND_MS, "voice", duration_ms=40 * SECOND_MS),
    ]

    group = group_messages(room)[0]

    assert group.queued is True
    assert is_queued(group) is True


def test_voice_messages_spoken_one_after_the_other_are_not_queued():
    room = [
        Msg("$v1", "homer", at(0), "voice", duration_ms=40 * SECOND_MS),
        Msg("$v2", "homer", at(1), "voice", duration_ms=40 * SECOND_MS),
    ]

    group = group_messages(room)[0]

    assert group.queued is False
    assert is_queued(group) is False


def test_photos_videos_and_texts_never_prove_a_queue():
    room = [
        Msg("$i", "homer", at(0), "image", duration_ms=40 * SECOND_MS),
        Msg("$v", "homer", at(0) + SECOND_MS, "video", duration_ms=40 * SECOND_MS),
        Msg("$t", "homer", at(0) + 2 * SECOND_MS, "text", duration_ms=40 * SECOND_MS),
    ]

    assert group_messages(room)[0].queued is False


def test_a_voice_message_of_unknown_length_proves_nothing():
    room = [
        Msg("$v1", "homer", at(0), "voice"),
        Msg("$v2", "homer", at(0) + SECOND_MS, "voice"),
    ]

    assert group_messages(room)[0].queued is False


def test_a_group_closes_five_minutes_after_its_last_message():
    room = [Msg("$a", "homer", at(0)), Msg("$b", "homer", at(2))]

    group = group_messages(room)[0]

    assert group.closes_at_ms == at(2) + 5 * MINUTE_MS


def test_a_group_ended_by_a_marker_closes_when_the_marker_was_sent():
    room = [Msg("$a", "homer", at(0)), Msg("$end", "homer", at(1), body="--")]

    assert group_messages(room)[0].closes_at_ms == at(1)


def test_no_messages_give_no_groups():
    assert group_messages([]) == []


def test_every_message_is_in_exactly_one_group_except_close_markers():
    room = [
        Msg("$a", "homer", at(0)),
        Msg("$b", "marge", at(1)),
        Msg("$end", "homer", at(2), body="--"),
        Msg("$c", "homer", at(3), "image"),
        Msg("$r", "bart", at(90), parent="$b"),
        Msg("$d", "homer", at(60)),
    ]

    groups = group_messages(room)
    placed = [m.event_id for g in groups for m in (*g.messages, *g.replies)]

    assert sorted(placed) == ["$a", "$b", "$c", "$d", "$r"]
    assert len(placed) == len(set(placed))
