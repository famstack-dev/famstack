"""Speaking on this Mac, and a one-shot LLM call, for the ai stacklet's commands.

The request each sentence becomes, and the server it goes to, come from
`stack.ai.voice`; this module plays what comes back.
"""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

_SSL = __import__("ssl").create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = __import__("ssl").CERT_NONE

# The voices, and where speech goes, are the stack's (`stack.ai.voice`):
# the bots speak the same way.
from stack.ai.voice import Voice, speech_request, speech_server  # noqa: E402

# Text for the ear: one function for everything that speaks, the bots too.
from stack.ai.speakable import speakable  # noqa: E402,F401


def server(ai: dict) -> tuple[str, str]:
    """The speech server's address and key for a command, from `[ai]`."""
    return speech_server(ai, ai.get("openai_url", ""), ai.get("openai_key", ""))


def synthesize(text: str, voice: Voice, *, url: str, key: str = "") -> bytes:
    """The text spoken as MP3 in `voice`. Raises OSError
    when the server fails or does not answer (`urllib.error.HTTPError`
    for an answer that is not 2xx).

    `url` and `key` are the speech server's, as `server` names them.
    """
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{url}/v1/audio/speech",
        data=json.dumps(speech_request(text, voice)).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def play(audio: bytes, volume: int = 100) -> None:
    """Play MP3 audio on the Mac's speakers and wait until it is done.

    `volume` is a percentage of the Mac's own output volume, capped at 100:
    the system volume is never changed, so a muted Mac stays silent.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        tmp = f.name
    share = max(0, min(int(volume), 100)) / 100
    try:
        subprocess.run(["afplay", "-v", str(share), tmp], timeout=60)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ── Speaking a longer text ───────────────────────────────────────────────
# A text is spoken in parts, each synthesized while the one before plays,
# so the first sentence is heard after its own synthesis, not the whole
# text's (1.8 s for one sentence of 58 characters on the rig).

# A sentence ends at . ! ? or … before a space and no lowercase letter, so
# "21.00" and "z.B. mit" stay whole.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?![a-zäöüß])")
# Each part is one request to the speech server; a short sentence goes
# with the next.
PART_MIN_CHARS = 25
# One speaker at a time on this Mac: a second `say` waits for the first to
# finish rather than talking over it.
SPEAKER_LOCK = Path(tempfile.gettempdir()) / "stack-speaker.lock"


class NoAudio(Exception):
    """The speech server answered, with nothing to play."""


def parts(text: str) -> list[str]:
    """`text` as the parts it is spoken in, in order."""
    out: list[str] = []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if not sentence:
            continue
        if out and len(out[-1]) < PART_MIN_CHARS:
            out[-1] += " " + sentence
        else:
            out.append(sentence)
    return out


# ── Kept phrases ─────────────────────────────────────────────────────────
# A bot says some phrases again and again ("Moment.", "Gern geschehen.").
# German speech took 0.9 s for "Moment." and up to 2.6 s while the language
# model ran, because oMLX serves both (rig, 2026-10-30). A phrase its caller
# asked to keep is synthesized once and played from the kept file after
# that. Only such phrases are kept, so answers do not pile up on disk.


def kept_dir(data_dir: Path, language: str) -> Path:
    """Where the kept phrases of one language live, in the ai stacklet's
    data dir. Derived, so deleting it loses nothing but the time to
    synthesize them again."""
    return data_dir / "ai" / "voice-snippets" / language


# A file name is the phrase's words, cut at a word to about this length,
# so a person can find and play a phrase; the hash after it does the rest.
SLUG_MAX_CHARS = 40
# German letters spelled as German writes them without the dots, where
# stripping the accent ("a" for "ä") would read as another word.
_SPELLED_OUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(text: str) -> str:
    """The words of `text` as lowercase ASCII joined by hyphens, at most
    `SLUG_MAX_CHARS` long: "Schöne Grüße!" is "schoene-gruesse"."""
    ascii_text = unicodedata.normalize("NFKD", text.lower().translate(_SPELLED_OUT))
    ascii_text = ascii_text.encode("ascii", "ignore").decode()
    out = ""
    for word in re.findall(r"[a-z0-9]+", ascii_text):
        joined = f"{out}-{word}" if out else word
        if len(joined) > SLUG_MAX_CHARS:
            return out or word[:SLUG_MAX_CHARS]
        out = joined
    return out


def kept_file(text: str, voice: Voice, keep: Path) -> Path:
    """The kept file of `text` in `voice`: its slug, then a short hash of
    the whole speech request, so another voice, model, clip or speed gets
    a file of its own. Volume is not in the request: it is applied at play
    time, and one file serves every volume."""
    request = json.dumps(speech_request(text, voice), sort_keys=True).encode()
    digest = hashlib.sha256(request).hexdigest()[:8]
    name = slug(text)
    return keep / (f"{name}-{digest}.mp3" if name else f"{digest}.mp3")


def audio_for(text: str, voice: Voice, *, url: str, key: str = "",
              keep: Path | None = None) -> bytes:
    """`text` spoken in `voice`, from its kept file (`kept_file`) when
    `keep` holds one, else synthesized and kept there. With `keep=None`
    this is `synthesize`. Empty audio is never kept.
    """
    if keep is None:
        return synthesize(text, voice, url=url, key=key)
    file = kept_file(text, voice, keep)
    if file.is_file():
        return file.read_bytes()
    audio = synthesize(text, voice, url=url, key=key)
    if audio:
        keep.mkdir(parents=True, exist_ok=True)
        # Written beside and renamed, so a reader never sees half a file.
        with tempfile.NamedTemporaryFile(dir=keep, suffix=".part", delete=False) as f:
            f.write(audio)
        os.replace(f.name, file)
    return audio


def prepare(text_parts: list[str], voice: Voice, *, url: str, key: str = "",
            keep: Path) -> None:
    """Make and keep the audio of every part, and play nothing. Raises as
    `say_aloud` does when the speech server fails or sends no audio."""
    for part in text_parts:
        if not audio_for(part, voice, url=url, key=key, keep=keep):
            raise NoAudio(part)


def say_aloud(text_parts: list[str], voice: Voice, *, url: str, key: str = "",
              volume: int = 100, wait: bool = True, keep: Path | None = None) -> None:
    """Speak the parts on the Mac's speakers, one after the other.

    The first part is synthesized before anything plays, so a speech server
    that fails or does not answer raises here (OSError), as `synthesize`
    does, and one that sends no audio raises NoAudio. A later part that fails ends the speaking after the part before.
    With `wait=False` it returns once the last part has started playing;
    the player keeps the speaker until it is done. With `keep`, each part
    plays from its kept file, and one not kept yet is synthesized and kept
    (`audio_for`); a kept phrase still waits its turn at the speaker.
    """
    share = str(max(0, min(int(volume), 100)) / 100)
    audio = audio_for(text_parts[0], voice, url=url, key=key, keep=keep)
    if not audio:
        raise NoAudio(text_parts[0])
    with SPEAKER_LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for i, _ in enumerate(text_parts):
            file = _audio_file(audio)
            if i == len(text_parts) - 1:
                _play_last(file, share, lock, wait=wait)
                return
            player = subprocess.Popen(["afplay", "-v", share, file])
            try:
                audio = audio_for(text_parts[i + 1], voice, url=url, key=key, keep=keep)
            except OSError:
                audio = b""
            player.wait(timeout=120)
            Path(file).unlink(missing_ok=True)
            if not audio:
                return


def _audio_file(audio: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        return f.name


def _play_last(file: str, share: str, lock, *, wait: bool) -> None:
    if wait:
        subprocess.run(["afplay", "-v", share, file], timeout=120)
        Path(file).unlink(missing_ok=True)
        return
    # The player inherits the locked file, so the speaker stays taken until
    # it has finished, after this process is gone.
    subprocess.Popen(["/bin/sh", "-c", 'afplay -v "$1" "$2"; rm -f "$2"', "sh", share, file],
                     pass_fds=(lock.fileno(),), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def speak(text: str, voice: Voice, *, url: str, key: str = "") -> bool:
    """Speak `text` on the Mac's speakers. Returns True on success."""
    try:
        audio = synthesize(text, voice, url=url, key=key)
        if not audio:
            return False
        play(audio)
        return True
    except Exception:
        return False



def ask_llm(url: str, key: str, prompt: str, model: str = "") -> str | None:
    """One-shot LLM call. Returns the response text or None."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    prompt = prompt + "\nReply with only your spoken words. No markdown, no formatting."
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.7,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/chat/completions",
            data=body, headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
        return text
    except Exception:
        return None
