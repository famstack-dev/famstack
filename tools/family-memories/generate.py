#!/usr/bin/env python3
"""generate.py — render the Memories test corpus from spec.yaml.

Voice memos and dialogues are synthesized through the stack's local
speech service (OpenAI-compatible TTS, Piper under the hood), images
through Pillow. Everything lands in out/ next to a manifest.json that
carries the pattern annotations, durations, bursts, and relations the
ingester and the diary-pipeline tests key off.

    python tools/family-memories/generate.py            # render all
    python tools/family-memories/generate.py --list     # show the set
    python tools/family-memories/generate.py --only fragment

Rendering only WRITES LOCAL FILES. Nothing here talks to Matrix — that
is ingest.py's job, and it targets a test rig, never production.
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import sys
import urllib.request
import wave
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
TTS_URL = "http://localhost:42063/v1/audio/speech"
TURN_GAP_MS = 400


def tts(text: str, voice: str) -> bytes:
    body = json.dumps({
        "model": "tts-1", "voice": voice,
        "response_format": "wav", "input": text.strip(),
    }).encode()
    req = urllib.request.Request(
        TTS_URL, data=body, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=300).read()


def wav_params(blob: bytes):
    """Read format + ALL frames. Streamed WAVs (like the TTS output)
    carry bogus RIFF/nframes headers, so read to EOF and never trust
    the declared frame count."""
    with wave.open(io.BytesIO(blob)) as w:
        fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        frames = w.readframes(0x7FFFFFF)
    return fmt, frames


def write_wav(fmt, frames: bytes) -> bytes:
    """Re-emit with a clean, correct header."""
    nchannels, sampwidth, framerate = fmt
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(frames)
    return out.getvalue()


def concat_wavs(blobs: list[bytes]) -> bytes:
    """Join turns with a short silence gap. All clips must share the
    same format (same speech backend => same rate; refuse otherwise)."""
    first_fmt, _ = wav_params(blobs[0])
    nchannels, sampwidth, framerate = first_fmt
    silence = b"\x00" * (int(framerate * TURN_GAP_MS / 1000)
                         * sampwidth * nchannels)
    joined = b""
    for i, blob in enumerate(blobs):
        fmt, frames = wav_params(blob)
        if fmt != first_fmt:
            sys.exit(f"voice sample formats differ ({fmt} vs {first_fmt}); "
                     "use voices from the same speech backend")
        if i:
            joined += silence
        joined += frames
    return write_wav(first_fmt, joined)


def wav_duration_ms(blob: bytes) -> int:
    (nchannels, sampwidth, framerate), frames = wav_params(blob)
    return int(len(frames) / (nchannels * sampwidth) / framerate * 1000)


def render_image(label: str, path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1134, 1512), "#fdf6e3")
    d = ImageDraw.Draw(img)
    # crayon-ish scribble scene: house, sun, four blobs — enough for a
    # VLM to describe, deliberately childlike
    d.rectangle([300, 700, 830, 1200], outline="#8b4513", width=12)
    d.polygon([(260, 700), (565, 450), (870, 700)], outline="#b22222", width=12)
    d.ellipse([900, 120, 1060, 280], fill="#ffd700")
    for i, color in enumerate(["#1565c0", "#2e7d32", "#ef6c00", "#8e24aa"]):
        x = 180 + i * 220
        d.ellipse([x, 1250, x + 120, 1370], outline=color, width=10)
    d.text((80, 60), label, fill="#555555")
    img.save(path, "PNG")


def render_locale(locale: str, args) -> None:
    spec = yaml.safe_load((HERE / f"spec.{locale}.yaml").read_text())
    voices = spec["voices"]
    items = [i for i in spec["items"]
             if not args.only or args.only in i["id"]]

    if args.list:
        for it in items:
            print(f"  {locale}/{it['id']:<28} {it['kind']:<9} {it['pattern']}")
        return

    out = OUT / locale
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for it in items:
        entry = {k: it[k] for k in
                 ("id", "pattern", "kind", "sender") if k in it}
        for opt in ("burst", "fragment_of", "reply_to", "delay_after_prev"):
            if opt in it:
                entry[opt] = it[opt]
        if it["kind"] in ("voice", "dialogue"):
            if it["kind"] == "voice":
                blob = write_wav(*wav_params(tts(it["text"],
                                                 voices[it["sender"]])))
            else:
                blob = concat_wavs(
                    [tts(t["text"], voices[t["voice"]]) for t in it["turns"]])
            path = out / f"{it['id']}.wav"
            path.write_bytes(blob)
            entry["file"] = path.name
            entry["duration_ms"] = wav_duration_ms(blob)
        elif it["kind"] == "image":
            path = out / f"{it['id']}.png"
            render_image(it["image_label"], path)
            entry["file"] = path.name
        else:  # text
            entry["text"] = it["text"].strip()
            if "edit_text" in it:
                entry["edit_text"] = it["edit_text"].strip()
        manifest.append(entry)
        print(f"rendered {locale}/{it['id']} ({it['pattern']})")

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"{len(manifest)} items -> {out}/manifest.json\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", default=None, help="substring filter on item id")
    ap.add_argument("--locale", choices=["de", "en"], default=None,
                    help="render one locale (default: all)")
    args = ap.parse_args()
    locales = [args.locale] if args.locale else ["de", "en"]
    for locale in locales:
        render_locale(locale, args)


if __name__ == "__main__":
    main()
