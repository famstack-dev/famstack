"""stack ai intro: the AI introduces itself, in its own words and voice.

    stack ai intro                   # prints its words and speaks them on this Mac
    stack ai intro --audio --json    # {"text": "Good evening, Homer. ...", "audio": "<MP3 as base64>"}

The language model writes two or three sentences from a prompt with the
admin's name, the date and time and the Mac's memory
(`first_words.introduction_prompt`), and the house voice of `[ai]
language` speaks them. `--audio` prints and plays nothing and answers with
the words and the audio instead, for the setup page, which shows the words
as a quote while it plays them in the browser (`first_words.spoken`).
"""

HELP = "AI introduces itself (LLM + TTS; --audio: answer with it, play nothing)"


def run(args, stacklet, config):
    """Introduce the AI: in the terminal, or with `--audio` as the page's
    answer. An error when no AI server is set or the model does not answer."""
    import datetime

    from ..first_words import admin_name, introduction_prompt

    ai = config.get("stack", {}).get("ai", {})
    if not ai.get("openai_url"):
        return {"error": "No LLM backend configured. Run './stack setup ai' first."}
    prompt = introduction_prompt(admin_name(config.get("users", [])), ai.get("language", "en"),
                                 datetime.datetime.now(), _memory_gb())
    if "--audio" in args:
        return _for_the_page(prompt, ai)
    return _in_the_terminal(prompt, ai)


def _for_the_page(prompt: str, ai: dict) -> dict:
    """The model's words and their audio; nothing printed, nothing played."""
    from ..first_words import spoken

    response = _ask(prompt, ai)
    if not response:
        return {"error": "LLM did not respond. Is the server running?"}
    try:
        return spoken(response, ai)
    except (OSError, ValueError) as e:
        return {"error": f"Could not speak the introduction: {e}"}


def _in_the_terminal(prompt: str, ai: dict) -> dict:
    """Print the model's words and speak them on this Mac's speakers."""
    from stack.ai.voice import voice_for, voices
    from stack.prompt import dim, nl, orange, out

    from ..speech import server, speak

    nl()
    orange("Let's test if everything is wired together. Hey Computer, how are you?")
    nl()
    dim("  Thinking...")
    response = _ask(prompt, ai)
    if not response:
        return {"error": "LLM did not respond. Is the server running?"}

    out(f"  {response}")
    nl()

    speech_url, speech_key = server(ai)
    speak(response, voice_for(voices(ai.get("voices")), ai.get("language", "en")),
          url=speech_url, key=speech_key)
    return {"ok": True}


def _ask(prompt: str, ai: dict) -> str | None:
    """The default model's answer to `prompt`, None when it does not answer."""
    from ..speech import ask_llm

    return ask_llm(ai["openai_url"], ai.get("openai_key", ""), prompt, model=ai.get("default", ""))


def _memory_gb() -> tuple[int, int | None] | None:
    """This Mac's memory as (total, free) GB, None when the total cannot be
    read. Free is None on macOS, which has no `SC_AVPHYS_PAGES`."""
    import os
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        total = round(page * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3))
    except (ValueError, OSError):
        return None
    try:
        free = round(page * os.sysconf("SC_AVPHYS_PAGES") / (1024 ** 3))
    except (ValueError, OSError):
        free = None
    return total, free
