"""Bot runner — discovers and runs bots from across all enabled stacklets.

Convention-based discovery:
  1. Scans /stacklets/*/bot/bot.toml for enabled stacklets
  2. Waits for Matrix to be available
  3. Creates Matrix accounts and rooms
  4. Launches all bots concurrently in one async process

A stacklet ships a bot by adding a bot/ directory with bot.toml and
a Python file. The runner discovers it automatically on next restart.

Bot ID convention: always ends with -bot (e.g. archivist-bot).
Module convention: strip -bot suffix -> archivist.py -> ArchivistBot.
"""

import asyncio
import importlib.util
import os
import signal
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    # Python 3.9/3.10 fallback — tomli vendored in lib/stack/
    sys.path.insert(0, "/app")
    from stack._vendor import tomli as tomllib

from loguru import logger


# ── Logging ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
    level=os.environ.get("LOG_LEVEL", "INFO"),
)

# ── Config from environment ──────────────────────────────────────────────
MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://stack-messages-synapse:8008")
MATRIX_SERVER_NAME = os.environ.get("MATRIX_SERVER_NAME", "")
DATA_DIR = Path("/data")
STACKLETS_DIR = Path("/stacklets")
SETUP_STATE_DIR = Path("/setup-state")


def _read_secrets():
    """Read secrets from the mounted .stack/secrets.toml."""
    secrets_path = SETUP_STATE_DIR / "secrets.toml"
    if not secrets_path.exists():
        return {}
    try:
        with open(secrets_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to read secrets: {}", e)
        return {}


def _enabled_stacklets():
    """Find enabled stacklets by checking setup-done markers."""
    enabled = set()
    for marker in SETUP_STATE_DIR.glob("*.setup-done"):
        enabled.add(marker.stem)
    # Core is always enabled (always_on), may not have a marker
    enabled.add("core")
    return enabled


def discover_bots():
    """Scan enabled stacklets for bot/bot.toml declarations.

    Returns a list of bot configs ready for instantiation.
    """
    enabled = _enabled_stacklets()
    logger.info("Enabled stacklets: {}", ", ".join(sorted(enabled)))

    secrets = _read_secrets()
    logger.debug("Loaded {} secrets", len(secrets))

    bots = []

    for stacklet_id in sorted(enabled):
        bot_toml = STACKLETS_DIR / stacklet_id / "bot" / "bot.toml"
        if not bot_toml.exists():
            continue

        logger.info("Found bot declaration: {}", bot_toml)

        try:
            with open(bot_toml, "rb") as f:
                decl = tomllib.load(f)
        except Exception as e:
            logger.warning("Failed to parse {}: {}", bot_toml, e)
            continue

        bot_id = decl.get("id", "")
        if not bot_id:
            logger.warning("Bot in {} has no 'id' field, skipping", bot_toml)
            continue

        if not MATRIX_SERVER_NAME:
            logger.error("MATRIX_SERVER_NAME not set — run 'stack up messages' first")
            continue

        # Password resolution: {stacklet}__{BOT_ID}_PASSWORD
        secret_key = f"{stacklet_id}__{bot_id.upper().replace('-', '_')}_PASSWORD"
        password = secrets.get(secret_key, "")

        if not password:
            logger.warning("Bot {} has no password (expected {} in secrets.toml), skipping", bot_id, secret_key)
            continue

        # Session dir: per-stacklet under data
        session_dir = DATA_DIR / stacklet_id / "bot"
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Bot {} session dir: {}", bot_id, session_dir)

        # Module resolution: strip -bot suffix for the Python file
        module_stem = bot_id.removesuffix("-bot") if bot_id.endswith("-bot") else bot_id
        class_name = module_stem.capitalize() + "Bot"
        bot_dir = STACKLETS_DIR / stacklet_id / "bot"
        logger.info("Bot {} -> {}/{}.py -> class {}", bot_id, bot_dir, module_stem, class_name)

        bots.append({
            "bot_id": bot_id,
            "stacklet_id": stacklet_id,
            "bot_dir": str(bot_dir),
            "module_stem": module_stem,
            "class_name": class_name,
            "homeserver": MATRIX_HOMESERVER,
            "user_id": f"@{bot_id}:{MATRIX_SERVER_NAME}",
            "password": password,
            "session_dir": str(session_dir),
            "display_name": decl.get("name", bot_id),
            "room": decl.get("room"),
            "room_topic": decl.get("room_topic"),
            "settings": decl.get("settings", {}),
        })

    return bots


async def wait_for_matrix(timeout=None):
    """Wait for Matrix homeserver to be reachable."""
    import urllib.request
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"{MATRIX_HOMESERVER}/_matrix/client/versions"
    elapsed = 0

    while True:
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(req, timeout=5, context=ctx)
            logger.info("Matrix is available at {}", MATRIX_HOMESERVER)
            return True
        except Exception:
            if elapsed == 0:
                logger.info("Waiting for Matrix at {}...", MATRIX_HOMESERVER)
            await asyncio.sleep(10)
            elapsed += 10
            if timeout and elapsed >= timeout:
                return False


def _needs_account_creation(configs):
    """Check which bots don't have a saved session (likely no account yet)."""
    needs = []
    for cfg in configs:
        session_file = Path(cfg["session_dir"]) / f"{cfg['bot_id']}.session.json"
        if not session_file.exists():
            needs.append(cfg)
    return needs


def _ensure_bot_accounts(configs):
    """Create Matrix accounts for new bots and ensure rooms + joins for all.

    Account creation is skipped for bots with saved sessions. Room setup
    and admin user joins always run so new stacklets get their rooms
    immediately.
    """
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")

    if not admin_user or not admin_password:
        logger.warning("No admin credentials — can't create accounts or rooms")
        return

    from accounts import setup_bot_accounts, ensure_rooms

    # Bots with saved sessions already have accounts — only new bots need one
    needs = _needs_account_creation(configs)
    if needs:
        logger.info("Bots needing accounts: {}", ", ".join(c["bot_id"] for c in needs))
        bot_accounts = [{
            "id": c["bot_id"],
            "name": c["display_name"],
            "password": c["password"],
        } for c in needs]
        ok = setup_bot_accounts(
            bot_accounts, MATRIX_HOMESERVER, MATRIX_SERVER_NAME,
            admin_user, admin_password,
        )
        if not ok:
            logger.warning("Account creation failed — bots without sessions won't start")
    else:
        logger.info("All bots have sessions — skipping account creation")

    # Rooms and joins must run every time — a new stacklet may have been
    # enabled since last boot, and its admin users need to be joined
    admin_user_ids = [
        uid.strip() for uid in
        os.environ.get("STACK_ADMIN_USER_IDS", "").split(",")
        if uid.strip()
    ]
    all_bots = [{
        "id": c["bot_id"],
        "room": c.get("room"),
        "room_topic": c.get("room_topic"),
    } for c in configs]
    ensure_rooms(
        all_bots, MATRIX_HOMESERVER, MATRIX_SERVER_NAME,
        admin_user, admin_password, admin_user_ids,
    )


async def main():
    """Discover bots, wait for Matrix, ensure accounts, run."""
    configs = discover_bots()
    if not configs:
        logger.info("No bots discovered — waiting for stacklets to be enabled")
        # Keep running so the container doesn't restart-loop
        await asyncio.Event().wait()

    logger.info("Discovered {} bot(s): {}", len(configs),
                ", ".join(c["bot_id"] for c in configs))

    # Wait for Matrix
    await wait_for_matrix()

    # Ensure bot accounts exist (best-effort)
    _ensure_bot_accounts(configs)

    # Import and instantiate bots
    tasks = []
    bot_instances = []

    for cfg in configs:
        try:
            # Add the bot's directory to sys.path so imports work
            bot_dir = cfg["bot_dir"]
            if bot_dir not in sys.path:
                sys.path.insert(0, bot_dir)

            # Load the module from the bot's directory
            module_path = Path(bot_dir) / f"{cfg['module_stem']}.py"
            if not module_path.exists():
                logger.error("Bot module not found: {}", module_path)
                continue

            spec = importlib.util.spec_from_file_location(cfg["module_stem"], module_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            bot_class = getattr(mod, cfg["class_name"])
        except Exception as e:
            logger.error("Failed to load {}: {}", cfg["bot_id"], e)
            continue

        bot = bot_class(
            homeserver=cfg["homeserver"],
            user_id=cfg["user_id"],
            password=cfg["password"],
            session_dir=cfg["session_dir"],
            **cfg["settings"],
        )
        bot_instances.append(bot)
        tasks.append(asyncio.create_task(bot.start()))
        logger.info("Launching {} as {}", bot.name, cfg["user_id"])

    if not tasks:
        logger.error("No bots launched — check logs above")
        await asyncio.Event().wait()

    # Signal handling for clean shutdown
    def shutdown(*_):
        logger.info("Shutting down...")
        for bot in bot_instances:
            asyncio.create_task(bot.stop())

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Bot runner active ({} bot{})", len(tasks), "s" if len(tasks) != 1 else "")
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All bots stopped")


if __name__ == "__main__":
    asyncio.run(main())
