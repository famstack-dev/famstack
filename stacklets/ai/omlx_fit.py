"""omlx_fit.py: fit an oMLX on this Mac to the memory profile.

A small Mac (`stack.profile`, under 24 GB) runs oMLX with three settings
that let the default chat model work beside Docker's VM, Whisper and
macOS:

    setting           oMLX's field(s)                          value
    memory ceiling    memory_guard_tier, ..._custom_ceiling_gb "custom", 9.0
    chunked prefill   chunked_prefill                          true
    idle unload       idle_timeout_seconds                     600

The rules, for the oMLX the stack installs:

- The ceiling only replaces oMLX's default memory guard ("balanced"). A
  tier the admin chose (safe, aggressive, custom) stays.
- Chunked prefill is always on.
- The idle timeout only fills an unset one (oMLX writes null, which keeps
  the model loaded). A timeout the admin set stays, and an older oMLX
  whose settings have no idle timeout gets none, as it would not read it.
- A Mac with a larger profile gets nothing: oMLX keeps its own settings.

Why, for each setting:

- The memory ceiling. oMLX refuses or aborts what does not fit under its
  memory guard's ceiling. Its default tier, "balanced", and "aggressive"
  both compute the ceiling from the memory free at that moment: on a
  16 GB Mac "balanced" gave 4.4 to 5.3 GB, under the 5.8 GB of the
  default model, so oMLX refused it, and "aggressive" 6.9 to 7.9 GB,
  which a contract-sized request (6,000 tokens in, 4,000 out) still went
  over. A fixed 9 GB, under Apple's 10.7 GB GPU limit, held: macOS swaps
  the rest instead.
- Chunked prefill. Reading a document's prompt in one piece took the
  process from 5.8 to 7.0 GB at 2,000 tokens. Read in chunks, the peak
  stays low.
- The idle unload. oMLX unloads the model after that long without a
  request, so its memory goes back to Docker and macOS in quiet periods.

The stack writes them into the settings file of the oMLX it installs
(`fit_settings_file`), which oMLX reads at its start, and the hook
restarts the service when the file changed. An oMLX the stack does not
manage, such as the oMLX app, is never changed: the setup page shows its
admin the same settings as a recommendation.

    from stack.profile import PROFILES

    changes_for({"memory": {"memory_guard_tier": "balanced"},
                 "scheduler": {"chunked_prefill": False},
                 "idle_timeout": {"idle_timeout_seconds": None}}, PROFILES["small"])
    # {'memory_guard_tier': 'custom', 'memory_guard_custom_ceiling_gb': 9.0,
    #  'chunked_prefill': True, 'idle_timeout_seconds': 600}

"""

from __future__ import annotations

from pathlib import Path

from stack.profile import Profile

from .omlx_service import read_settings, write_settings

__all__ = ["DEFAULT_MEMORY_GUARD", "changes_for", "fit_settings_file"]

# oMLX's own default tier, the only one the profile's ceiling replaces.
DEFAULT_MEMORY_GUARD = "balanced"


def changes_for(settings: dict, profile: Profile) -> dict:
    """The fields of oMLX's global settings to change so `settings` meet
    the profile, as oMLX's `POST /admin/api/global-settings` takes them;
    empty when nothing is to change.

    `settings` is in the shape oMLX writes to `~/.omlx/settings.json` and
    answers `GET /admin/api/global-settings` with: `memory`, `scheduler`
    and `idle_timeout` sections. Nothing else of it is read.

        changes_for({"memory": {"memory_guard_tier": "aggressive"},
                     "scheduler": {"chunked_prefill": True}}, PROFILES["small"])
        # {}: the admin's tier stays, prefill is chunked, no idle timeout known
        changes_for({}, PROFILES["large"])
        # {}
    """
    ceiling = profile.omlx_ceiling_gb
    if ceiling is None:
        return {}
    wanted: dict = {}
    if _read(settings, "memory_guard_tier", DEFAULT_MEMORY_GUARD) == DEFAULT_MEMORY_GUARD:
        wanted["memory_guard_tier"] = "custom"
        wanted["memory_guard_custom_ceiling_gb"] = ceiling
    wanted["chunked_prefill"] = True
    idle = settings.get("idle_timeout")
    if (isinstance(idle, dict) and profile.omlx_idle_timeout_s is not None
            and idle.get("idle_timeout_seconds") is None):
        wanted["idle_timeout_seconds"] = profile.omlx_idle_timeout_s
    return {name: value for name, value in wanted.items() if _read(settings, name) != value}


def fit_settings_file(path: Path, profile: Profile) -> bool:
    """Write the profile's changes into the oMLX settings file at `path`,
    for the oMLX the stack installs, which reads it at its start only.
    Returns whether the file changed; the caller restarts the service.
    A missing file is left missing: oMLX's install writes it. Raises
    `omlx_service.UnreadableSettings`, writing nothing, for a file that is
    not JSON.

        fit_settings_file(omlx_service.settings_file(data_dir), PROFILES["small"])
        # True on the first start of a 16 GB Mac, False after
    """
    if not path.exists():
        return False
    settings = read_settings(path)
    changes = changes_for(settings, profile)
    if not changes:
        return False
    for name, value in changes.items():
        section, key = FIELDS[name]
        settings.setdefault(section, {})[key] = value
    write_settings(path, settings)
    return True


# ── oMLX's fields ────────────────────────────────────────────────────────

# Where each field of the admin API sits in oMLX's settings file and in
# its `GET /admin/api/global-settings` answer (oMLX 0.7.4, `admin/routes.py`).
FIELDS = {
    "memory_guard_tier": ("memory", "memory_guard_tier"),
    "memory_guard_custom_ceiling_gb": ("memory", "memory_guard_custom_ceiling_gb"),
    "chunked_prefill": ("scheduler", "chunked_prefill"),
    "idle_timeout_seconds": ("idle_timeout", "idle_timeout_seconds"),
}


def _read(settings: dict, name: str, default=None):
    """The value of the admin API's field `name` in nested `settings`."""
    section, key = FIELDS[name]
    return (settings.get(section) or {}).get(key, default)
