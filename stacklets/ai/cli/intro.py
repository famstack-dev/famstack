"""stack ai intro — let the AI introduce itself via LLM + TTS."""

HELP = "AI introduces itself (LLM + TTS)"


def run(args, stacklet, config):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from speech import ask_llm, speak

    import datetime
    import os
    import tomllib
    from pathlib import Path
    from stack.prompt import nl, out, dim, orange

    repo_root = Path(config.get("repo_root", "."))
    ai_cfg = config.get("stack", {}).get("ai", {})
    url = ai_cfg.get("openai_url", "")
    key = ai_cfg.get("openai_key", "")
    model = ai_cfg.get("default", "")
    language = ai_cfg.get("language", "en")

    if not url:
        return {"error": "No LLM backend configured. Run './stack setup ai' first."}

    # Context
    admin_name = ""
    try:
        users_path = repo_root / "users.toml"
        if users_path.exists():
            with open(users_path, "rb") as f:
                users = tomllib.load(f).get("users", [])
            for u in users:
                if u.get("role") == "admin":
                    admin_name = u.get("name", "").split()[0]
                    break
    except Exception:
        pass

    now_time = datetime.datetime.now().strftime("%H:%M")
    now_date = datetime.datetime.now().strftime("%A, %B %d")
    total_ram = None
    free_ram = None
    try:
        total_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        total_ram = round(total_bytes / (1024 ** 3))
        avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        free_ram = round(avail / (1024 ** 3))
    except Exception:
        pass

    if language.startswith("de"):
        prompt = (
            f"Du bist die KI eines Familienservers namens famstack. "
            f"{'Der Admin heißt ' + admin_name + '. ' if admin_name else ''}"
            f"Es ist {now_date}, {now_time} Uhr. "
            f"{'Du läufst auf einem Mac mit ' + str(total_ram) + ' GB RAM (' + str(free_ram) + ' GB frei). ' if total_ram else ''}"
            f"Stell dich in 2-3 kurzen Sätzen vor. Sei freundlich und persönlich. "
            f"Erwähne die Uhrzeit und den Tag. "
            f"Sag, dass du jetzt einsatzbereit bist und dich auf die Zusammenarbeit freust. "
            f"Beende nicht mit einer Frage."
        )
    else:
        context = f"It is {now_date} at {now_time}. "
        if total_ram:
            context += f"You are running on a Mac with {total_ram} GB RAM ({free_ram} GB free). "
        prompt = (
            f"You are the AI of a family server called famstack. "
            f"{'The admin is ' + admin_name + '. ' if admin_name else ''}"
            f"{context}"
            f"Introduce yourself in 2-3 short sentences. Be warm and personal. "
            f"Mention the time of day. "
            f"Say you're ready to help and excited to get started. "
            f"Do not end with a question."
        )

    nl()
    orange("Let's test if everything is wired together. Hey Computer, how are you?")
    nl()

    dim("  Thinking...")
    response = ask_llm(url, key, prompt, model=model)
    if not response:
        return {"error": "LLM did not respond. Is the server running?"}

    out(f"  {response}")
    nl()

    voice = "onyx" if language.startswith("de") else "alloy"
    speak(response, voice=voice)

    return {"ok": True}
