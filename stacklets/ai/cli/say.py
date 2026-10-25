"""stack ai say <text>: speak any text aloud on the Mac.

The text goes to the ai stacklet's speech server and plays on this Mac's
speakers. The voice is the default for `[ai] language`, as for
`stack ai hello`, unless `--voice` names one of the voices the speech
server loads.

    stack ai say "Dinner is ready"
    stack ai say "Dinner is ready" --voice nova
    stack ai say "Dinner is ready" --volume 40
    stack ai say "Dinner is ready" --language en
    stack ai say "Dinner is ready" --no-wait

Which voice: `--voice`, else the voice of `--language`, else of the text's
own language when it can be told, else of `[ai] language`.

How loud: `--volume`, else `[ai] speech_volume`, else 100, as a share of
the Mac's own output volume. A speech server the stack does not run is
named by `[ai] speech_url`.

A longer text is spoken sentence by sentence, each synthesized while the
one before plays. `--no-wait` returns once the last sentence has started
playing. Two at once take turns: the second waits for the first.
"""

# Also the line the agent reads for this command, so it says how to write
# the text: Piper reads markdown marks aloud ("asterisk asterisk").
HELP = ("Speak a text aloud on this Mac. Pass plain sentences written to be "
        "heard: no markdown, lists, links or emoji")

import argparse
import urllib.error

from ..speech import VOICED, NoAudio, default_voice, known_voices, parts, say_aloud, speakable

from stack.ai.language import language_code, written_in

USAGE = ('stack ai say "<text>" [--voice <name>] [--language <code>] [--volume <0-100>] '
         '[--no-wait]')


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai say", description=HELP, usage=USAGE)
    p.add_argument("text", nargs="*", help="what to say; quotes are optional")
    p.add_argument("--voice", default="", help="one of the speech server's voices")
    p.add_argument("--language", default="", help="the text's language, e.g. en, when not the house's")
    p.add_argument("--volume", type=int, default=None,
                   help="percent of the Mac's own output volume, at most 100")
    p.add_argument("--no-wait", action="store_true",
                   help="return once the last sentence has started playing")
    return p.parse_args(args)


def run(args, stacklet, config):
    opts = _parse(args)
    text = speakable(" ".join(opts.text))
    if not text:
        return {"error": f"Nothing to say. Usage: {USAGE}"}

    ai = config.get("stack", {}).get("ai", {})
    house, note = language_code(ai.get("language", "en")), ""
    if opts.language:
        language = language_code(opts.language)
        if language not in VOICED:
            note = f"No voice for '{language}'; spoken in the house's voice."
            language = house
    else:
        # The text's own language when it can be told: German read by the
        # English voice sounds foreign.
        language = written_in(text) or house
    voice = opts.voice or default_voice(language)
    voices = known_voices()
    if voice not in voices:
        return {"error": f"The speech server has no voice '{voice}'. "
                         f"Voices: {', '.join(voices)}"}

    volume = opts.volume if opts.volume is not None else int(ai.get("speech_volume", 100))
    try:
        say_aloud(parts(text), voice, volume=volume, url=ai.get("speech_url", ""),
                  wait=not opts.no_wait)
    except urllib.error.HTTPError as e:
        return {"error": f"The speech server failed ({e.code}). "
                         "Check 'docker logs stack-ai-speech'."}
    except OSError:
        return {"error": "The speech server is not reachable. "
                         "Start the ai stacklet: stack up ai"}
    except NoAudio:
        return {"error": "The speech server returned no audio. "
                         "Check 'docker logs stack-ai-speech'."}
    print(f"\n  Speaking as {voice}...\n")
    return {"ok": True, "voice": voice, **({"note": note} if note else {})}
