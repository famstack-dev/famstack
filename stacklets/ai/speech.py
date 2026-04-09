"""Shared TTS and LLM utilities for the AI stacklet."""

import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

_SSL = __import__("ssl").create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = __import__("ssl").CERT_NONE


def speak(text: str, voice: str = "alloy", speed: float = 0.8) -> bool:
    """Send text to TTS and play it. Returns True on success."""
    try:
        req = urllib.request.Request(
            "http://localhost:42063/v1/audio/speech",
            data=json.dumps({
                "model": "tts-1", "input": text,
                "voice": voice, "speed": speed,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            audio = resp.read()
        if not audio:
            return False
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        subprocess.run(["afplay", tmp], timeout=60)
        Path(tmp).unlink(missing_ok=True)
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
