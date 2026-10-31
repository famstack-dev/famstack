"""stack ai say <text>: speak any text aloud on the Mac.

The text goes to the speech server, oMLX unless `[ai] speech_url` names
another, and plays on this Mac's speakers. English is read by Kokoro's
`af_heart`, German by the stack's own German voice, `stacky-de`, unless
`[ai.voices.<lang>]` names another voice for the language; the voices
are chosen in `stack.ai.voice`.

    stack ai say "Dinner is ready"
    stack ai say "Dinner is ready" --voice stacky-de
    stack ai say "Dinner is ready" --volume 40
    stack ai say "Dinner is ready" --language en
    stack ai say "Dinner is ready" --no-wait
    stack ai say "Moment." --keep
    stack ai say "Moment." --prepare

Which voice: the one `--voice` names, else that of `--language`, else of
the text's own language when it can be told, else of `[ai] language`. A
name that is not one of these voices, the Piper voices the stack used to
speak with included, is answered as not installed. `stack up ai`
downloads only the house voice's model, so a language's voice whose
model the server does not have gives way to the house voice, with a
note that says how to download it.

How loud: `--volume`, else `[ai] speech_volume`, else 100, as a share of
the Mac's own output volume. The speech goes to the AI server, `[ai]
openai_url`, with its key, unless `[ai] speech_url` names a server of
its own (with `[ai] speech_key`).

A longer text is spoken sentence by sentence, each synthesized while the
one before plays. `--no-wait` returns once the last sentence has started
playing. Two at once take turns: the second waits for the first.

A phrase said again and again, such as a bot's "Moment.", is kept with
`--keep`: the first time it is synthesized, played and kept, after that
it plays from the kept audio with no request to the speech server, also
while the server is busy or not running. `--prepare` makes and keeps it
and plays nothing, for a bot that prepares its phrases at start. Kept
audio belongs to the voice and its model; another voice, or a changed
model, is synthesized again. It lives in the ai stacklet's data dir, one
folder per language and one file per phrase, named so a person can find
and play it: `voice-snippets/de/gern-geschehen-91b0e4aa.mp3`, the
phrase's words and a short hash of the speech request. Deleting it
loses nothing but time. Without either flag nothing is kept.
"""

# Also the line the agent reads for this command, so it says how to write
# the text: a speech model reads markdown marks aloud ("asterisk asterisk").
HELP = ("Speak a text aloud on this Mac. Pass plain sentences written to be "
        "heard: no markdown, lists, links or emoji")

import argparse
import functools
import json
import urllib.error
from pathlib import Path

from ..omlx import OMLXClient
from ..speech import NoAudio, kept_dir, parts, prepare, say_aloud, server, speakable
from ..voices import omlx_too_old

from stack.ai.language import language_code, written_in
from stack.ai.voice import Voice, voice_for, voice_named, voices

USAGE = ('stack ai say "<text>" [--voice <name>] [--language <code>] [--volume <0-100>] '
         '[--no-wait] [--keep | --prepare]')


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai say", description=HELP, usage=USAGE)
    p.add_argument("text", nargs="*", help="what to say; quotes are optional")
    p.add_argument("--voice", default="", help="one of the stack's voices, af_heart or "
                   "stacky-de unless [ai.voices] names others")
    p.add_argument("--language", default="", help="the text's language, e.g. en, when not the house's")
    p.add_argument("--volume", type=int, default=None,
                   help="percent of the Mac's own output volume, at most 100")
    p.add_argument("--no-wait", action="store_true",
                   help="return once the last sentence has started playing")
    kept = p.add_mutually_exclusive_group()
    kept.add_argument("--keep", action="store_true",
                      help="keep the audio and play it from there the next time")
    kept.add_argument("--prepare", action="store_true",
                      help="make and keep the audio, and play nothing")
    return p.parse_args(args)


def run(args, stacklet, config):
    opts = _parse(args)
    text = speakable(" ".join(opts.text))
    if not text:
        return {"error": f"Nothing to say. Usage: {USAGE}"}

    ai = config.get("stack", {}).get("ai", {})
    try:
        table = voices(ai.get("voices"))
    except ValueError as e:
        return {"error": str(e)}
    house, note = language_code(ai.get("language", "en")), ""
    if opts.language:
        language = language_code(opts.language)
        if language not in table:
            note = f"No voice for '{language}'; spoken in the house's voice."
            language = house
    else:
        # The text's own language when it can be told: German read by the
        # English voice sounds foreign.
        language = written_in(text) or house
    voice = voice_named(table, opts.voice) if opts.voice else voice_for(table, language)
    if voice is None:
        return {"error": f"The voice '{opts.voice}' is not installed. "
                         f"Voices: {', '.join(v.name for v in table.values())}"}

    url, key = server(ai)
    volume = opts.volume if opts.volume is not None else int(ai.get("speech_volume", 100))
    # Kept by the language the phrase is spoken in, as resolved above, so
    # a person finds "de/moment-<hash>.mp3" where they would look for it.
    kept = kept_dir(Path(config["data_dir"]), language)
    if opts.prepare:
        speak = functools.partial(prepare, parts(text), url=url, key=key, keep=kept)
    else:
        speak = functools.partial(say_aloud, parts(text), url=url, key=key, volume=volume,
                                  wait=not opts.no_wait, keep=kept if opts.keep else None)
    house_voice, missing = None if opts.voice else voice_for(table, house), None
    try:
        try:
            speak(voice)
        except urllib.error.HTTPError as e:
            # `stack up ai` downloads only the house voice's model, so a
            # language's voice the server lacks gives way to it. A voice
            # asked for by name does not.
            if e.code != 404 or house_voice in (None, voice):
                raise
            missing, voice = voice, house_voice
            speak(voice)
    except urllib.error.HTTPError as e:
        return {"error": _refused(e, voice, url, ai)}
    except OSError:
        return {"error": f"The speech server at {url} is not reachable. "
                         "Start the ai stacklet: stack up ai"}
    except NoAudio:
        return {"error": f"The speech server at {url} returned no audio."}
    if missing:
        note = (f"The speech server has no model {missing.model}, so the house's voice "
                f"spoke. {_fetch(missing, ai)}")
    if opts.prepare:
        print(f"\n  Kept in {voice.name}'s voice.\n")
    else:
        print(f"\n  Speaking as {voice.name}...\n")
    return {"ok": True, "voice": voice.name, **({"note": note} if note else {})}


def _refused(error: urllib.error.HTTPError, voice: Voice, url: str, ai: dict) -> str:
    """What a speech server's refusal means for the admin, in one sentence.

    oMLX answers 401 for a missing or wrong key and 404 for a model it
    has not downloaded; anything else is quoted as it came. An engine the
    stack runs that is too old to speak is named first, with the command
    that updates it. A missing model is fetched with `stack ai download`
    only on the engine the stack runs: on any other server that command
    fetches nothing.
    """
    if _managed(ai) and (too_old := omlx_too_old(OMLXClient(url).version())):
        return too_old
    if error.code == 401:
        setting = "speech_key" if ai.get("speech_url") else "openai_key"
        return f"The speech server at {url} refused the key. Check [ai] {setting} in stack.toml."
    if error.code == 404:
        return f"The speech server at {url} has no model {voice.model}. {_fetch(voice, ai)}"
    return f"The speech server at {url} failed ({error.code}): {_detail(error)}"


def _fetch(voice: Voice, ai: dict) -> str:
    """How to get a voice's missing model onto the speech server."""
    if not _managed(ai):
        return f"Download {voice.repo or voice.model} on that server; the stack does not run it."
    if voice.repo:
        return f"Download it: stack ai download {voice.repo}"
    return "Download it into oMLX, or name it in [ai.voices] by its Hugging Face repo."


def _managed(ai: dict) -> bool:
    """Whether speech goes to the engine the stack runs."""
    return ai.get("provider", "managed") == "managed" and not ai.get("speech_url")


def _detail(error: urllib.error.HTTPError) -> str:
    """The reason in an error's body, `{"error": {"message": ...}}` as oMLX
    and OpenAI send it, else the status line's."""
    try:
        return str(json.loads(error.read())["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return str(error.reason)
