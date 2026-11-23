"""A refile from the command line keeps what changed meanwhile: `stack memory reprocess` as Marge runs it.

The preview reads the card, its sidecar and its history once, with their versions, and the apply
writes on those versions. A person's edit that lands in between refuses the apply, and a history the
curator cannot give is said to be unreachable, never taken for a card that is not there. The refile
reads the thread of every message the note was made of, and a correction given with `--msg` is a
source of its own, with an id and the time it was given.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tests.memory.unit import test_memory_reprocess_surface as surface
from tests.memory.unit.test_memory_reprocess_surface import (
    ROOM, TRIP, TRIP_CARD, TRIP_SIDE, TRIP_SIDECAR, TRIP_WORDS, _event, _Model, _reprocess,
)
from tests.model import Model

# The stack of the reprocess surface tests: its memory, its model and Paperless, and the same stack
# whose refile reads the chat.
stack = surface.stack
stack_that_reads_the_chat = surface.stack_that_reads_the_chat

ANSWER = {"title": "Trip", "tags": ["trip"], "persons": ["Homer"],
          "clean": "For the trip: Homer calls the hotel about the late check-in. Lisa wants the museum "
                   "visit on Sunday. Budget: at most 250."}
LISAS_LINE = "Lisa: the hotel wants a deposit."


def test_a_line_lisa_adds_while_the_model_answers_is_not_written_over(stack, httpserver):
    """Given Marge previews a refile of the trip note with a correction, when Lisa adds a line to the card
    in Forgejo while the model answers, then applying the preview is refused as changed, and Lisa's line
    is in the card."""
    stck, memory = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
    model = Model(httpserver)

    def answers_while_lisa_edits(_messages) -> str:
        memory.edited_in_forgejo(
            {TRIP: memory.text(TRIP).replace("## Sources", LISAS_LINE + "\n\n## Sources", 1)}, by="lisa")
        return json.dumps(ANSWER)

    model.answering(answers_while_lisa_edits)
    preview = _reprocess(stck, "note-26933f6ef8", "--msg", "budget is 250 now", "--by", "marge")
    assert "error" not in preview, preview

    applied = _reprocess(stck, "--apply", preview["proposal_id"])

    assert applied.get("refusal") == "changed"
    assert LISAS_LINE in memory.text(TRIP)


def test_a_history_the_curator_cannot_give_is_not_taken_for_a_card_that_is_not_there(stack):
    """Given the trip note, when the curator answers a server fault for its history and Marge runs
    `stack memory reprocess` on it, then the refusal says the memory could not be reached, not that
    there is no such card: she is not sent to look for a card that is there."""
    stck, memory = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})

    with memory.history_fails():
        answer = _reprocess(stck, "note-26933f6ef8")

    assert answer.get("refusal") == "unreachable", answer
    assert memory.text(TRIP) == TRIP_CARD


MUSEUM = "and the museum thing is 15 each"
TWO_MESSAGES = {"role": "original", "person": "marge", "channel": "chat", "kind": "text"}


def _a_note_of_two_messages() -> tuple[str, str]:
    """The trip note made of two messages by Marge: the trip's words, then the museum's price."""
    card = TRIP_CARD + f"\n> {MUSEUM}\n"
    sidecar = json.loads(TRIP_SIDECAR)
    sidecar["sources"].append({**TWO_MESSAGES, "time": "2026-10-20T08:01:00+00:00", "id": "$museum:simpson"})
    return card, json.dumps(sidecar, indent=2) + "\n"


def _synapse_with_threads(httpserver, threads: dict[tuple[str, str], list[dict]]) -> None:
    """Synapse as the archivist sees it: one room, each message of the note (its id and words) and the
    thread under it."""
    httpserver.expect_request("/_matrix/client/v3/joined_rooms").respond_with_json({"joined_rooms": [ROOM]})
    for (event_id, words), thread in threads.items():
        message = _event(event_id, "@marge:simpson", {"msgtype": "m.text", "body": words})
        httpserver.expect_request(f"/_matrix/client/v3/rooms/{ROOM}/event/{event_id}").respond_with_json(message)
        httpserver.expect_request(f"/_matrix/client/v1/rooms/{ROOM}/relations/{event_id}/m.thread"
                                  ).respond_with_json({"chunk": thread})


def test_a_reply_under_the_second_message_of_a_note_is_read_by_the_refile(stack_that_reads_the_chat, httpserver):
    """Given the trip note made of two messages, and Lisa's reply in the thread of the second, when Marge
    runs `stack memory reprocess` on it, then the preview's sources hold Lisa's reply: a reply under a
    later message is as much the note's as one under the first."""
    card, side = _a_note_of_two_messages()
    stck, _ = stack_that_reads_the_chat({TRIP: card, TRIP_SIDE: side})
    lisas = _event("$r2", "@lisa:simpson", {"msgtype": "m.text", "body": "I booked the tickets"})
    _synapse_with_threads(httpserver, {("$trip:simpson", TRIP_WORDS): [], ("$museum:simpson", MUSEUM): [lisas]})
    _Model(httpserver, {"title": "Trip", "tags": ["trip"], "persons": ["Homer"], "summary": "The trip."})

    preview = _reprocess(stck, "note-26933f6ef8")

    assert "error" not in preview, preview
    sources = json.loads(preview["proposed"]["sidecar"])["sources"]
    assert [(s["role"], s["person"], s["id"]) for s in sources] == [
        ("original", "marge", "$trip:simpson"), ("original", "marge", "$museum:simpson"), ("reply", "lisa", "$r2")]


def test_a_correction_given_with_the_refile_is_a_source_with_its_own_id_and_the_time_it_was_given(
        stack, httpserver):
    """Given the trip note, when Marge refiles it with `--msg`, then the preview's last source is her
    note, with an id of the command line and the time she gave it: a second run with the same words is a
    second source, and the record says when the correction was made."""
    stck, _ = stack({TRIP: TRIP_CARD, TRIP_SIDE: TRIP_SIDECAR})
    _Model(httpserver, ANSWER)
    before = datetime.now(timezone.utc).replace(microsecond=0)

    preview = _reprocess(stck, "note-26933f6ef8", "--msg", "budget is 250 now", "--by", "marge")

    note = json.loads(preview["proposed"]["sidecar"])["sources"][-1]
    assert (note["role"], note["person"], note["channel"]) == ("note", "marge", "cli")
    assert note["id"].startswith("cli:") and len(note["id"]) > len("cli:")
    assert before <= datetime.fromisoformat(note["time"]) <= datetime.now(timezone.utc)
