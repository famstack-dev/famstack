"""stack ai hello — play the famstack welcome message through TTS."""

HELP = "Play the welcome voice message"


def run(args, stacklet, config):
    import json, subprocess, sys, tempfile, urllib.request
    from pathlib import Path

    # Read language from stack.toml [ai] section, derive voice
    ai_cfg = config.get("stack", {}).get("ai", {})
    language = ai_cfg.get("language", "en")
    voice = "onyx" if language.startswith("de") else "alloy"

    # Admin name for personalisation — users come from the hook contract,
    # framework already loaded users.toml.
    admin_name = ""
    for u in config.get("users", []):
        if u.get("role") == "admin":
            admin_name = u.get("name", "").split()[0]
            break

    if language.startswith("de"):
        greeting = f"Hallo {admin_name}. " if admin_name else ""
        text = (
            f"{greeting}Willkommen bei famstack. "
            "Herzlichen Glückwunsch — deine KI ist einsatzbereit. "
            "Alles was du gerade hörst läuft auf deinem Mac. "
            "Keine Cloud, keine Server, kein Abo. "
            "Nur deine Maschine, die für sich selbst spricht. "
            "Viel Spaß mit dem Rest deiner famstack Reise."
        )
    else:
        greeting = f"Hello {admin_name}. " if admin_name else ""
        text = (
            f"{greeting}Welcome to famstack. "
            "Congratulations! Your AI is ready to serve. "
            "Everything you're hearing right now is running on your Mac. "
            "No cloud, no servers, no subscriptions. "
            "Just your machine, speaking for itself. "
            "Enjoy the rest of your famstack journey."
        )

    print(f"\n  Speaking...\n", file=sys.stderr)

    try:
        req = urllib.request.Request(
            "http://localhost:42063/v1/audio/speech",
            data=json.dumps({"model": "tts-1", "input": text, "voice": voice, "speed": 0.8}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            audio = resp.read()
        if not audio:
            return {"error": "TTS service returned empty response. Is the ai stacklet running?"}

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        subprocess.run(["afplay", tmp], timeout=60)
        Path(tmp).unlink(missing_ok=True)
    except Exception as e:
        return {"error": f"Could not play greeting: {e}"}

    return {"ok": True}
