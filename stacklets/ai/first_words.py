"""first_words.py: what the AI says first, in the terminal and on the setup page.

    from ..first_words import admin_name, introduction_prompt, spoken, welcome

    text = welcome(admin_name(config["users"]), "en")
    # "Hello Homer. Welcome to famstack. Congratulations! Your AI is ..."
    spoken(text, config["stack"]["ai"])
    # {"text": "Hello Homer. Welcome to ...", "audio": "SUQzBAAAAAAA..."}

When the AI is set up, it speaks for the first time. Two things are said:

- **The welcome**, a fixed text that greets the admin by first name and
  says that everything heard runs on the Mac (`stack ai hello`).
- **The introduction**, written by the language model from a prompt with
  the admin's name, the date and time and the Mac's memory, two or three
  sentences in its own words (`stack ai intro`).

The terminal plays both on the Mac's speakers, after the AI's first
start. The setup page plays them in the browser that opens it, on any
device, so the commands can answer with the audio instead of playing it
(`--audio`): `spoken` makes the speech in the stack's own voice, as `stack
ai say` does, and returns it with the text, so the page shows the same
words it plays. The page never uses the browser's built-in speech
synthesis, because famstack speaks in the voice the stack runs.

The texts are kept here, in one place, so the terminal and the page say
the same thing. The welcome's German text is spoken, never shown.
"""

from __future__ import annotations

import base64
import datetime

from stack.ai.voice import voice_for, voices

from .speech import server, synthesize

__all__ = ["admin_name", "welcome", "introduction_prompt", "spoken"]


def admin_name(users: list[dict]) -> str:
    """The first name of the family's admin in `users` (`users.toml` as
    the hook contract gives it), "" when there is none.

        admin_name([{"name": "Homer Simpson", "role": "admin"}])    # "Homer"
    """
    for user in users:
        if user.get("role") == "admin":
            return user.get("name", "").split()[0] if user.get("name") else ""
    return ""


def welcome(admin: str, language: str) -> str:
    """The welcome of `stack ai hello`, in German for a language that
    starts with "de" and in English otherwise, greeting `admin` when there
    is one.

        welcome("Homer", "en")    # "Hello Homer. Welcome to famstack. Congratulations! ..."
        welcome("", "en")         # "Welcome to famstack. Congratulations! ..."
    """
    if language.startswith("de"):
        greeting = f"Hallo {admin}. " if admin else ""
        return (
            f"{greeting}Willkommen bei famstack. "
            "Herzlichen Glückwunsch — deine KI ist einsatzbereit. "
            "Alles was du gerade hörst läuft auf deinem Mac. "
            "Keine Cloud, keine Server, kein Abo. "
            "Nur deine Maschine, die für sich selbst spricht. "
            "Viel Spaß mit dem Rest deiner famstack Reise."
        )
    greeting = f"Hello {admin}. " if admin else ""
    return (
        f"{greeting}Welcome to famstack. "
        "Congratulations! Your AI is ready to serve. "
        "Everything you're hearing right now is running on your Mac. "
        "No cloud, no servers, no subscriptions. "
        "Just your machine, speaking for itself. "
        "Enjoy the rest of your famstack journey."
    )


def introduction_prompt(admin: str, language: str, now: datetime.datetime,
                        memory_gb: tuple[int, int | None] | None) -> str:
    """The prompt the language model introduces itself from (`stack ai
    intro`): who the admin is, the date and time `now`, and the Mac's
    memory as (total, free) GB when known (free may be None). German for a language that
    starts with "de", English otherwise.

        introduction_prompt("Homer", "en", datetime.datetime(2026, 11, 9, 20, 58), (16, 9))
        # "You are the AI of a family server called famstack. The admin is Homer.
        #  It is Monday, November 09 at 20:58. You are running on a Mac with
        #  16 GB RAM (9 GB free). Introduce yourself in 2-3 short sentences. ..."
    """
    now_time = now.strftime("%H:%M")
    now_date = now.strftime("%A, %B %d")
    total_ram, free_ram = memory_gb or (None, None)
    if language.startswith("de"):
        return (
            f"Du bist die KI eines Familienservers namens famstack. "
            f"{'Der Admin heißt ' + admin + '. ' if admin else ''}"
            f"Es ist {now_date}, {now_time} Uhr. "
            f"{'Du läufst auf einem Mac mit ' + str(total_ram) + ' GB RAM' + (' (' + str(free_ram) + ' GB frei)' if free_ram is not None else '') + '. ' if total_ram else ''}"
            f"Stell dich in 2-3 kurzen Sätzen vor. Sei freundlich und persönlich. "
            f"Duze den Admin, wie famstack alle duzt. "
            f"Erwähne die Uhrzeit und den Tag. "
            f"Sag, dass du jetzt einsatzbereit bist und dich auf die Zusammenarbeit freust. "
            f"Beende nicht mit einer Frage."
        )
    context = f"It is {now_date} at {now_time}. "
    if total_ram:
        free = f" ({free_ram} GB free)" if free_ram is not None else ""
        context += f"You are running on a Mac with {total_ram} GB RAM{free}. "
    return (
        f"You are the AI of a family server called famstack. "
        f"{'The admin is ' + admin + '. ' if admin else ''}"
        f"{context}"
        f"Introduce yourself in 2-3 short sentences. Be warm and personal. "
        f"Mention the time of day. "
        f"Say you're ready to help and excited to get started. "
        f"Do not end with a question."
    )


def spoken(text: str, ai: dict) -> dict:
    """`text` spoken in the house voice of `[ai] language`, for a page to
    play: `{"text": text, "audio": <MP3 as base64>}`. Nothing plays here.

    The speech server is the one `stack ai say` uses (`[ai] speech_url`,
    else the AI server). Raises OSError when it fails or does not answer,
    as `speech.synthesize` does, and ValueError when it sends no audio.

        spoken("Hello Homer.", {"language": "en", "openai_url": "http://localhost:8000/v1"})
        # {"text": "Hello Homer.", "audio": "SUQzBAAAAAAA..."}
    """
    url, key = server(ai)
    voice = voice_for(voices(ai.get("voices")), ai.get("language", "en"))
    audio = synthesize(text, voice, url=url, key=key)
    if not audio:
        raise ValueError(f"The speech server at {url} returned no audio.")
    return {"text": text, "audio": base64.b64encode(audio).decode("ascii")}
