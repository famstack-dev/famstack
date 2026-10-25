"""stack ai hello — play the famstack welcome message through TTS."""

HELP = "Play the welcome voice message"


def run(args, stacklet, config):
    import sys

    from ..speech import default_voice, play, synthesize

    # Read language from stack.toml [ai] section, derive voice
    ai_cfg = config.get("stack", {}).get("ai", {})
    language = ai_cfg.get("language", "en")
    voice = default_voice(language)

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

    print("\n  Speaking...\n", file=sys.stderr)

    try:
        audio = synthesize(text, voice)
        if not audio:
            return {"error": "TTS service returned empty response. Is the ai stacklet running?"}
        play(audio)
    except Exception as e:
        return {"error": f"Could not play greeting: {e}"}

    return {"ok": True}
