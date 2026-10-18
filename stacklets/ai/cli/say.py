"""stack ai say <text>: speak any text aloud on the Mac.

The text goes to the ai stacklet's speech server and plays on this Mac's
speakers. The voice is the default for `[ai] language`, as for
`stack ai hello`, unless `--voice` names one of the voices the speech
server loads.

    stack ai say "Dinner is ready"
    stack ai say "Dinner is ready" --voice nova
    stack ai say "Dinner is ready" --volume 40

How loud: `--volume`, else `[ai] speech_volume`, else 100, as a share of
the Mac's own output volume. A speech server the stack does not run is
named by `[ai] speech_url`.
"""

# Also the line the agent reads for this command, so it says how to write
# the text: Piper reads markdown marks aloud ("asterisk asterisk").
HELP = ("Speak a text aloud on this Mac. Pass plain sentences written to be "
        "heard: no markdown, lists, links or emoji")

import argparse
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from speech import default_voice, known_voices, play, speakable, synthesize  # noqa: E402

USAGE = 'stack ai say "<text>" [--voice <name>] [--volume <0-100>]'


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai say", description=HELP, usage=USAGE)
    p.add_argument("text", nargs="*", help="what to say; quotes are optional")
    p.add_argument("--voice", default="", help="one of the speech server's voices")
    p.add_argument("--volume", type=int, default=None,
                   help="percent of the Mac's own output volume, at most 100")
    return p.parse_args(args)


def run(args, stacklet, config):
    opts = _parse(args)
    text = speakable(" ".join(opts.text))
    if not text:
        return {"error": f"Nothing to say. Usage: {USAGE}"}

    ai = config.get("stack", {}).get("ai", {})
    language = ai.get("language", "en")
    voice = opts.voice or default_voice(language)
    voices = known_voices()
    if voice not in voices:
        return {"error": f"The speech server has no voice '{voice}'. "
                         f"Voices: {', '.join(voices)}"}

    try:
        audio = synthesize(text, voice, url=ai.get("speech_url", ""))
    except urllib.error.HTTPError as e:
        return {"error": f"The speech server failed ({e.code}). "
                         "Check 'docker logs stack-ai-speech'."}
    except OSError:
        return {"error": "The speech server is not reachable. "
                         "Start the ai stacklet: stack up ai"}
    if not audio:
        return {"error": "The speech server returned no audio. "
                         "Check 'docker logs stack-ai-speech'."}

    print(f"\n  Speaking as {voice}...\n")
    volume = opts.volume if opts.volume is not None else int(ai.get("speech_volume", 100))
    play(audio, volume=volume)
    return {"ok": True, "voice": voice}
