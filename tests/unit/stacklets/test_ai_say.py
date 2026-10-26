"""`stack ai say <text>`: speak any text aloud on the Mac.

The admin types a sentence and hears it. The stack speaks through oMLX,
the engine that serves its language model: Kokoro's `af_heart` reads
English, and Qwen3-TTS 0.6B Base reads German in the stack's own German
voice, `stacky-de`, cloned for every sentence from a reference clip,
because oMLX keeps no stored voices. The voice is the one `--voice`
names, else that of the text's language, else of `--language`, else of
`[ai] language`. No text, a voice the stack does not have, a speech
server that is not running, a refused key or a missing speech model each
end in an error that says which, and nothing is played.

The command runs through the same path as `./stack ai say`, against the
real ai stacklet and a throwaway instance whose `[ai]` names the AI
server and its key. A real HTTP server stands in for oMLX, and an
`afplay` on PATH that only records what it was given stands in for the
Mac's player, so no test makes a sound.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import pytest

from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]

AUDIO = b"ID3 not really an mp3"

# The instance's oMLX key, `[ai] openai_key`. oMLX answers 401 to a speech
# request without it.
KEY = "omlx-key"

# The words spoken in the German reference clip, as it was recorded on
# 2026-10-26. Qwen3-TTS clones a voice from the clip and its transcript
# together, so the two must match.
CLIP_TEXT = ("Stell dir vor, du machst einfach ein Foto von einem Dokument und "
             "schickst es in deinen privaten Messenger. Wie WhatsApp, aber lokal.")


def _instance(tmp_path, language, server, extra=""):
    """An instance whose AI server is `server`, as `stack ai switch` writes it."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
        f'[ai]\nlanguage = "{language}"\n'
        f'openai_url = "{_root(server)}/v1"\nopenai_key = "{KEY}"\n{extra}')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


def _root(server) -> str:
    return server if isinstance(server, str) else server.url_for("/").rstrip("/")


@pytest.fixture
def instance(tmp_path, speech_server):
    return _instance(tmp_path, "en", speech_server)


@pytest.fixture
def played(tmp_path, monkeypatch):
    """Where the stand-in player copies what it was asked to play."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "played.mp3"
    player = bin_dir / "afplay"
    # The file is the last argument; the arguments before it go to played.args.
    player.write_text(f'#!/bin/sh\nfor a; do last=$a; done\necho "$@" > "{out}.args"\n'
                      f'cp "$last" "{out}"\n')
    player.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return out


@pytest.fixture
def speech_server(httpserver):
    """oMLX's speech endpoint, answering every request with audio."""
    httpserver.expect_request(
        "/v1/audio/speech", method="POST").respond_with_data(
            AUDIO, content_type="audio/mpeg")
    return httpserver


def _omlx_error(message, status):
    """An error as oMLX answers on its `/v1/` routes, in OpenAI's shape."""
    kind = {401: "authentication_error", 404: "not_found_error"}.get(status, "server_error")
    return {"error": {"message": message, "type": kind, "param": None, "code": None}}


def _missing_model(server):
    """oMLX answering a speech request for Kokoro it has not downloaded."""
    server.expect_request("/v1/audio/speech", method="POST").respond_with_json(
        _omlx_error("Model 'Kokoro-82M-bf16' not found. Available: Qwen3.5-9B-MLX-8bit", 404),
        status=404)


def _say(instance, *args):
    return instance.run_cli_command("ai", "say", list(args))


def _requests(server):
    return [request.get_json() for request, _ in server.log]


class TestWhatReachesOMLX:
    """What the speech request carries is oMLX's contract: the model by its
    folder name, a named voice or a reference clip with its transcript,
    and the audio format (oMLX 0.6.4, checked by request on 2026-10-26)."""

    def test_english_is_spoken_by_kokoro_as_af_heart(self, instance, speech_server, played):
        result = _say(instance, "Bananas are on the list.")

        assert "error" not in result, result
        [request] = _requests(speech_server)
        assert request["model"] == "Kokoro-82M-bf16"
        assert request["voice"] == "af_heart"
        assert request["input"] == "Bananas are on the list."
        assert request["response_format"] == "mp3"
        assert "ref_audio" not in request
        assert played.read_bytes() == AUDIO

    def test_german_is_spoken_in_the_cloned_german_voice(self, tmp_path, speech_server, played):
        """The clip goes with every request: oMLX keeps no voices."""
        result = _say(_instance(tmp_path, "de", speech_server),
                      "Der 30-Minuten-Timer ist abgelaufen.")

        assert "error" not in result, result
        [request] = _requests(speech_server)
        assert request["model"] == "Qwen3-TTS-12Hz-0.6B-Base-8bit"
        assert request["language"] == "german"
        clip = base64.b64decode(request["ref_audio"])
        assert clip[:4] == b"RIFF" and clip[8:12] == b"WAVE"
        assert request["ref_text"] == CLIP_TEXT
        assert request["response_format"] == "mp3"
        assert played.read_bytes() == AUDIO

    def test_a_german_text_in_an_english_house_gets_the_german_voice(
            self, instance, speech_server, played):
        """Read by the English voice, German sounds foreign (#242)."""
        result = _say(instance, "Das Essen ist fertig, kommt bitte runter.")

        assert result["voice"] == "stacky-de"
        assert _requests(speech_server)[0]["model"] == "Qwen3-TTS-12Hz-0.6B-Base-8bit"

    def test_every_request_carries_the_ai_servers_key(self, instance, speech_server, played):
        """oMLX refuses a speech request without its key, as it refuses chat."""
        _say(instance, "Dinner is ready in the kitchen. Bart, please come down.")

        assert len(speech_server.log) == 2
        for request, _ in speech_server.log:
            assert request.headers["Authorization"] == f"Bearer {KEY}"

    def test_the_result_says_which_voice_spoke(self, instance, speech_server, played):
        """What `--json` prints: an agent or a script can tell what it got."""
        result = _say(instance, "Dinner is ready")

        assert result["voice"] == "af_heart"

    def test_words_without_quotes_are_one_sentence(
            self, instance, speech_server, played):
        _say(instance, "Dinner", "is", "ready")

        assert _requests(speech_server)[0]["input"] == "Dinner is ready"


class TestAChosenVoice:
    """`--voice` names one of the voices the stack has. Any other name, the
    Piper voices it used to speak with included, is not installed: an
    error that names the voices there are, sent nowhere and never spoken."""

    def test_a_voice_the_stack_has_speaks_it(self, instance, speech_server, played):
        result = _say(instance, "Dinner is ready", "--voice", "stacky-de")

        assert "error" not in result, result
        [request] = _requests(speech_server)
        assert request["model"] == "Qwen3-TTS-12Hz-0.6B-Base-8bit"
        assert request["ref_text"] == CLIP_TEXT
        assert result["voice"] == "stacky-de"
        assert played.read_bytes() == AUDIO

    def test_the_english_voice_by_name(self, tmp_path, speech_server, played):
        result = _say(_instance(tmp_path, "de", speech_server),
                      "Das Essen ist fertig", "--voice", "af_heart")

        assert result["voice"] == "af_heart"
        assert _requests(speech_server)[0]["voice"] == "af_heart"

    @pytest.mark.parametrize("name", ["alloy", "echo", "nova", "onyx", "fable", "shimmer",
                                      "bart"])
    def test_any_other_voice_is_not_installed(self, instance, speech_server, played, name):
        result = _say(instance, "Dinner is ready", "--voice", name)

        assert "ok" not in result
        assert f"'{name}' is not installed" in result["error"]
        assert "af_heart" in result["error"] and "stacky-de" in result["error"]
        assert _requests(speech_server) == []
        assert not played.exists()


class TestNothingIsPlayed:

    def test_no_text_says_how_to_use_it(self, instance, speech_server, played):
        result = _say(instance)

        assert "stack ai say" in result["error"]
        assert _requests(speech_server) == []
        assert not played.exists()

    def test_a_blank_text_is_no_text(self, instance, speech_server, played):
        result = _say(instance, "   ")

        assert "stack ai say" in result["error"]
        assert not played.exists()

    def test_no_speech_server_says_to_start_the_ai_stacklet(self, tmp_path, played):
        result = _say(_instance(tmp_path, "en", "http://127.0.0.1:9"), "Dinner is ready")

        assert "stack up ai" in result["error"]
        assert not played.exists()

    def test_a_refused_key_is_reported_as_such(self, tmp_path, httpserver, played):
        """oMLX's answer to a wrong or missing key."""
        httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_json(
            _omlx_error("Invalid API key", 401), status=401)

        result = _say(_instance(tmp_path, "en", httpserver), "Dinner is ready")

        assert "key" in result["error"]
        assert "openai_key" in result["error"]
        assert not played.exists()

    def test_a_missing_speech_model_on_the_engine_the_stack_runs_says_how_to_fetch_it(
            self, tmp_path, httpserver, played):
        """oMLX's answer to a model it has not downloaded. The stack
        downloads into the engine it runs, so it names its own command."""
        _missing_model(httpserver)

        result = _say(_instance(tmp_path, "en", httpserver, 'provider = "managed"\n'),
                      "Dinner is ready")

        assert "Kokoro-82M-bf16" in result["error"]
        assert "stack ai download mlx-community/Kokoro-82M-bf16" in result["error"]
        assert not played.exists()

    @pytest.mark.parametrize("server", ["ai server elsewhere", "speech server elsewhere"])
    def test_a_missing_speech_model_elsewhere_is_named_for_that_server(
            self, tmp_path, httpserver, played, server):
        """On a server the stack does not run, `stack ai download` fetches
        nothing, so the error names the repo to download there instead:
        the AI server after `stack ai switch <url>` (the rig's Mac Studio),
        or the one `[ai] speech_url` names."""
        _missing_model(httpserver)
        if server == "ai server elsewhere":
            instance = _instance(tmp_path, "en", httpserver, 'provider = "external"\n')
        else:
            instance = _instance(tmp_path, "en", "http://127.0.0.1:9", 'provider = "managed"\n'
                                 f'speech_url = "{_root(httpserver)}"\n')

        result = _say(instance, "Dinner is ready")

        assert "Kokoro-82M-bf16" in result["error"]
        assert "mlx-community/Kokoro-82M-bf16 on that server" in result["error"]
        assert "stack ai download" not in result["error"]
        assert not played.exists()

    def test_another_failure_quotes_the_server(self, tmp_path, httpserver, played):
        httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_json(
            _omlx_error("TTS generation produced no audio", 500), status=500)

        result = _say(_instance(tmp_path, "en", httpserver), "Dinner is ready")

        assert "500" in result["error"]
        assert "TTS generation produced no audio" in result["error"]
        assert not played.exists()


def test_say_speaks_at_the_welcome_messages_pace(instance, speech_server, played):
    """The welcome message's pace was chosen by ear, and 1.0 sounds rushed.
    Every command that speaks uses it and the same voice, so `say` sounds
    like `hello`."""
    _say(instance, "Dinner is ready")
    instance.run_cli_command("ai", "hello", [])

    say, hello = _requests(speech_server)
    assert say["speed"] == hello["speed"] == 0.8
    assert say["model"] == hello["model"] == "Kokoro-82M-bf16"
    assert say["voice"] == hello["voice"] == "af_heart"


class TestHowLoud:
    """Played at a share of the Mac's own output volume, never above it, and
    the system volume is never changed: a muted Mac stays silent (#241)."""

    def test_the_admins_volume_is_a_share_of_the_macs(self, tmp_path, speech_server, played):
        instance = _instance(tmp_path, "en", speech_server, "speech_volume = 50\n")

        _say(instance, "Dinner is ready")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "0.5"]

    def test_a_volume_for_one_sentence_wins(self, instance, speech_server, played):
        _say(instance, "Dinner is ready", "--volume", "30")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "0.3"]

    def test_never_louder_than_the_mac(self, instance, speech_server, played):
        _say(instance, "Dinner is ready", "--volume", "250")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "1.0"]


class TestASpeechServerElsewhere:
    """`[ai] speech_url` names a speech server of its own, as `whisper_url`
    does for hearing: the same requests go there, with its key and never
    the AI server's."""

    @pytest.fixture
    def elsewhere(self, tmp_path, httpserver):
        httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
            AUDIO, content_type="audio/mpeg")
        return _instance(tmp_path, "en", "http://127.0.0.1:9",
                         f'speech_url = "{_root(httpserver)}"\nspeech_key = "tts-key"\n')

    def test_it_speaks_with_the_same_voices(self, elsewhere, httpserver, played):
        result = _say(elsewhere, "Dinner is ready")

        assert "error" not in result, result
        assert _requests(httpserver)[0]["model"] == "Kokoro-82M-bf16"
        assert played.read_bytes() == AUDIO

    def test_it_gets_its_own_key(self, elsewhere, httpserver, played):
        _say(elsewhere, "Dinner is ready")

        [(request, _)] = httpserver.log
        assert request.headers["Authorization"] == "Bearer tts-key"

    def test_an_address_written_like_the_ai_servers_works_too(
            self, tmp_path, httpserver, played):
        """`[ai] openai_url` ends in `/v1`; a speech_url copied from it does too."""
        httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
            AUDIO, content_type="audio/mpeg")
        instance = _instance(tmp_path, "en", "http://127.0.0.1:9",
                             f'speech_url = "{_root(httpserver)}/v1"\n')

        result = _say(instance, "Dinner is ready")

        assert "error" not in result, result


class TestVoicesNamedInStackToml:
    """`[ai.voices.<lang>]` names a language's voice: the model and voice
    to ask for, and optionally a clip the stack ships to clone. So any
    OpenAI-compatible speech server can speak, not only oMLX. A request
    carries exactly what the entry names; a language without an entry
    keeps the stack's own voice."""

    @pytest.fixture
    def elsewhere(self, httpserver):
        """A speech server answering OpenAI's speech API, at `[ai] speech_url`."""
        httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
            AUDIO, content_type="audio/mpeg")
        return f'speech_url = "{_root(httpserver)}"\n'

    def test_a_server_that_is_not_omlx_gets_exactly_the_named_model_and_voice(
            self, tmp_path, httpserver, played, elsewhere):
        instance = _instance(tmp_path, "en", "http://127.0.0.1:9", elsewhere
                             + '\n[ai.voices.en]\nmodel = "tts-1"\nvoice = "alloy"\n')

        result = _say(instance, "Dinner is ready")

        assert "error" not in result, result
        [request] = _requests(httpserver)
        assert request == {"model": "tts-1", "voice": "alloy", "input": "Dinner is ready",
                           "speed": 0.8, "response_format": "mp3"}
        assert result["voice"] == "alloy"
        assert played.read_bytes() == AUDIO

    def test_a_language_without_an_entry_keeps_the_stacks_voice(
            self, tmp_path, httpserver, played, elsewhere):
        instance = _instance(tmp_path, "en", "http://127.0.0.1:9", elsewhere
                             + '\n[ai.voices.en]\nmodel = "tts-1"\nvoice = "alloy"\n')

        _say(instance, "Das Essen ist fertig, kommt bitte runter.")

        [request] = _requests(httpserver)
        assert request["model"] == "Qwen3-TTS-12Hz-0.6B-Base-8bit"
        assert request["ref_text"] == CLIP_TEXT

    def test_a_named_voice_replaces_the_stacks_voice_clip_and_all(
            self, tmp_path, httpserver, played, elsewhere):
        """Another server cannot clone: the German clip goes only with a
        voice that names it."""
        instance = _instance(tmp_path, "de", "http://127.0.0.1:9", elsewhere
                             + '\n[ai.voices.de]\nmodel = "tts-1"\nvoice = "onyx"\n')

        _say(instance, "Das Essen ist fertig, kommt bitte runter.")

        [request] = _requests(httpserver)
        assert (request["model"], request["voice"]) == ("tts-1", "onyx")
        assert not {"ref_audio", "ref_text", "language"} & request.keys()

    def test_a_voice_that_names_the_stacks_clip_clones_it(
            self, tmp_path, httpserver, played, elsewhere):
        """The larger Qwen3-TTS, kept for Macs with more memory, with the same clip."""
        instance = _instance(tmp_path, "de", "http://127.0.0.1:9", elsewhere
                             + '\n[ai.voices.de]\nmodel = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"'
                             '\nclip = "stacky-de"\n')

        _say(instance, "Das Essen ist fertig, kommt bitte runter.")

        [request] = _requests(httpserver)
        assert request["model"] == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
        assert request["ref_text"] == CLIP_TEXT
        assert base64.b64decode(request["ref_audio"])[8:12] == b"WAVE"
        assert "voice" not in request

    def test_voice_knows_the_named_voices_and_only_those(
            self, tmp_path, httpserver, played, elsewhere):
        instance = _instance(tmp_path, "en", "http://127.0.0.1:9", elsewhere
                             + '\n[ai.voices.en]\nmodel = "tts-1"\nvoice = "alloy"\n')

        spoken = _say(instance, "Dinner is ready", "--voice", "alloy")
        refused = _say(instance, "Dinner is ready", "--voice", "af_heart")

        assert spoken["voice"] == "alloy"
        assert "'af_heart' is not installed" in refused["error"]
        assert "alloy" in refused["error"] and "stacky-de" in refused["error"]
        assert len(_requests(httpserver)) == 1

    @pytest.mark.parametrize("entry, says", [
        ('voice = "alloy"', "model"),
        ('model = "tts-1"\nclip = "homer"', "homer"),
    ])
    def test_an_entry_the_stack_cannot_use_is_an_error_that_names_it(
            self, tmp_path, httpserver, played, elsewhere, entry, says):
        instance = _instance(tmp_path, "en", "http://127.0.0.1:9",
                             elsewhere + f"\n[ai.voices.en]\n{entry}\n")

        result = _say(instance, "Dinner is ready")

        assert "[ai.voices.en]" in result["error"] and says in result["error"]
        assert _requests(httpserver) == []
        assert not played.exists()


class TestWrittenForTheEar:
    """Markdown is read aloud literally: "**Three emails**" took 3.9 s for
    "asterisk asterisk three emails asterisk asterisk" (rig, 2026-10-16).
    What reaches the speech server is the text as a person would say it (#243)."""

    @pytest.mark.parametrize("written, spoken", [
        ("**Three emails** came in.", "Three emails came in."),
        ("_Bitte_ jetzt.", "Bitte jetzt."),
        ("## Today\nSchool at 8.", "Today. School at 8."),
        ("Lists:\n- Milk\n- Eggs", "Lists: Milk. Eggs"),
        ("1. Milk\n2. Eggs", "Milk. Eggs"),
        ("See [the list](http://home/go/list/markt).", "See the list."),
        ("It is on https://famstack.dev/docs/guide now.", "It is on famstack.dev now."),
        ("Run `stack up` first.", "Run stack up first."),
        ("Fertig 🎉!", "Fertig!"),
        ("| list | open |\n|---|---|\n| Markt | 3 |", "list: Markt, open: 3"),
        ("Pokerabend um 21:00 Uhr.", "Pokerabend um 21 Uhr."),
        ("Um 09:05 Uhr geht es los.", "Um 9 Uhr 5 geht es los."),
        ("Dinner at 6:00 pm, cake at 6:30 pm.", "Dinner at 6 pm, cake at 6 30 pm."),
    ])
    def test_what_is_spoken_is_what_a_person_would_say(self, instance, speech_server, played,
                                                       written, spoken):
        _say(instance, written)

        assert _requests(speech_server)[0]["input"] == spoken


class TestALanguageOtherThanTheHouses:
    """The agent translates, then says which language it wrote (#242)."""

    def test_the_named_language_picks_its_voice(self, tmp_path, speech_server, played):
        result = _say(_instance(tmp_path, "de", speech_server), "Dinner is ready",
                      "--language", "en")

        assert result["voice"] == "af_heart"

    def test_a_named_voice_still_wins(self, tmp_path, speech_server, played):
        result = _say(_instance(tmp_path, "de", speech_server), "Dinner is ready",
                      "--language", "en", "--voice", "stacky-de")

        assert result["voice"] == "stacky-de"

    def test_a_language_without_a_voice_falls_back_to_the_houses_and_says_so(
            self, tmp_path, speech_server, played):
        result = _say(_instance(tmp_path, "de", speech_server), "Le dîner est prêt",
                      "--language", "fr")

        assert result["voice"] == "stacky-de"
        assert "fr" in result["note"]


class TestALongerText:
    """One sentence of 58 characters took 1.8 s to synthesize and 5.8 s to
    play (rig, 2026-10-18), and the agent's turn waited for both. A text is
    spoken sentence by sentence, the next synthesized while one plays; an
    agent that should not wait for the speaker says `--no-wait`."""

    @pytest.fixture
    def slow_player(self, tmp_path, monkeypatch):
        """A player that takes a second per file and logs when it plays."""
        bin_dir = tmp_path / "slowbin"
        bin_dir.mkdir()
        log = tmp_path / "player.log"
        player = bin_dir / "afplay"
        player.write_text(f'#!/bin/sh\necho start >> "{log}"\nsleep 1\necho end >> "{log}"\n')
        player.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return log

    def test_each_sentence_is_its_own_request_in_order(self, instance, speech_server, played):
        _say(instance, "Heute Abend ist Pokerabend mit Moe und Barney. "
                       "Morgen kommt Oma zum Kaffee vorbei. Danach gehen alle schwimmen.")

        assert [r["input"] for r in _requests(speech_server)] == [
            "Heute Abend ist Pokerabend mit Moe und Barney.",
            "Morgen kommt Oma zum Kaffee vorbei.", "Danach gehen alle schwimmen."]

    def test_a_short_sentence_goes_with_the_next(self, instance, speech_server, played):
        _say(instance, "Ok. Dinner is ready in the kitchen now.")

        assert [r["input"] for r in _requests(speech_server)] == [
            "Ok. Dinner is ready in the kitchen now."]

    def test_no_wait_returns_while_the_last_sentence_still_plays(self, instance, speech_server,
                                                                 slow_player):
        started = time.monotonic()
        result = _say(instance, "Dinner is ready in the kitchen now.", "--no-wait")

        assert "error" not in result, result
        assert time.monotonic() - started < 0.9
        time.sleep(1.5)
        assert "end" in slow_player.read_text()

    def test_a_second_text_waits_until_the_first_was_heard(self, instance, speech_server,
                                                           slow_player):
        _say(instance, "Dinner is ready in the kitchen now.", "--no-wait")
        _say(instance, "Bart, please come down for dinner.")

        assert slow_player.read_text().split() == ["start", "end", "start", "end"]
