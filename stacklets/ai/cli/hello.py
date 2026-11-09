"""stack ai hello: play the welcome in famstack's own voice.

    stack ai hello                   # plays it on this Mac's speakers
    stack ai hello --audio --json    # {"text": "Hello Homer. ...", "audio": "<MP3 as base64>"}

The welcome greets the admin and says that what they hear runs on the Mac
(`first_words.welcome`), in the house voice of `[ai] language`. The first
start of the AI plays it in the terminal. `--audio` plays nothing and
answers with the text and the audio instead, for the setup page, which
plays it in the browser on any device (`first_words.spoken`).
"""

HELP = "Play the welcome voice message (--audio: answer with it, play nothing)"


def run(args, stacklet, config):
    """Play the welcome on this Mac, or with `--audio` answer with it. An
    error when the speech server fails or sends no audio."""
    import sys

    from stack.ai.voice import voice_for, voices

    from ..first_words import admin_name, spoken, welcome
    from ..speech import play, server, synthesize

    # The voice follows stack.toml's [ai] language.
    ai_cfg = config.get("stack", {}).get("ai", {})
    language = ai_cfg.get("language", "en")
    url, key = server(ai_cfg)
    text = welcome(admin_name(config.get("users", [])), language)

    if "--audio" in args:
        try:
            return spoken(text, ai_cfg)
        except (OSError, ValueError) as e:
            return {"error": f"Could not make the greeting: {e}"}

    print("\n  Speaking...\n", file=sys.stderr)

    try:
        voice = voice_for(voices(ai_cfg.get("voices")), language)
        audio = synthesize(text, voice, url=url, key=key)
        if not audio:
            return {"error": f"The speech server at {url} returned no audio."}
        play(audio)
    except Exception as e:
        return {"error": f"Could not play greeting: {e}"}

    return {"ok": True}
