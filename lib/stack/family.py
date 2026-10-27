"""`stack family`: the people an instance is for, read and written as one object.

The data already has homes. users.toml holds the people, one `[[users]]`
table each; stack.toml `[core]` holds the household's name (`stack_owner`)
and, optionally, a web address of its own (`family_domain`). This module
reads them as one object for `stack family`, and writes one back for
`stack family set --from <file>`. Every writer goes through `set_family`:
the install from a file (`stack install --family`), the dashboard's first
run through the host API, and tests. So the defaults and the refusals
below hold wherever the data comes from.

The file, as TOML, or as JSON with the same keys:

    name   = "Simpson"          # [core] stack_owner
    domain = "simpsons.com"     # optional: [core] family_domain

    [[people]]
    name        = "Homer Simpson"
    relation    = "parent"      # parent, child, grandparent or other
    words       = "he"          # she or he; left out: the name only
    looks_after = true          # looks after the server: role "admin"

`SCHEMA` is the one statement of that shape: a JSON Schema object in the
small subset `_check` reads (`type`, `required`, `properties`,
`additionalProperties`, `items`, `minItems`, `minLength`, `enum`,
`format: hostname`, and `x-error`, the sentence shown when a value breaks
its field's rules). The host CLI is stdlib-only, so this is the validator,
not the `jsonschema` package. It reads the same subset as the validator of
the setup questions (ADR-021, PR #339); once both are on one branch they
can share one. An empty string is no answer, as in a form: an optional
field sent as "" counts as left out.

Rules the shape cannot say:

- Defaults. The first two people are parents and the rest children. The
  first person looks after the server, unless the file sets `looks_after`
  on anyone; then exactly those set to true do.
- A username comes from the first name, lowercased and spelled in a to z
  (Jürgen is `juergen`), because it is a Matrix user id and a login.
  Two people with one name, or one username, are refused.
- What is there stays. A person already in users.toml is matched by name,
  then by username, and keeps their id, email, services and every other
  key. A new person gets `<id>@<family_domain>` as email (`@home.local`
  without one), the services the installer gives, and their username as
  first password, as the installer sets it.
- The chat's server name is permanent. `[messages] server_name` is written
  only while it is empty and messages has never been set up: the web
  address if there is one, else the name (`simpson`). `[core] domain`,
  which is domain mode, is neither read nor written here.
- Once messages is set up, the people cannot change through this command:
  a new person needs accounts it cannot make yet, and a renamed or removed
  one would leave the chat behind. The refusal says what to do by hand.

A refusal names every problem it finds and writes nothing. The same file
twice writes nothing the second time.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from .users import load_users, password_key, user_id

RELATIONS = ["parent", "child", "grandparent", "other"]
WORDS = ["she", "he"]

# Email for a person when there is no web address. Nothing receives it;
# services that sign in by email need one.
LOCAL_MAIL = "home.local"

# The services a new person gets accounts on, as the installer writes them.
# A member without "messages" here gets no chat account.
NEW_PERSON_SERVICES = ["photos", "docs", "messages"]

# users.toml's keys in the order a person reads them; any other key follows.
USER_KEYS = ["id", "name", "email", "role", "relation", "words", "stacklets"]

# A domain name: labels of a to z, digits and inner hyphens, at least two,
# the last starting with a letter. That refuses a bare word, a URL with a
# scheme or a path, and an IP address.
HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# What a Matrix user id's local part may hold, narrowed to what also works
# as a login and a file name in the other services.
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# The JSON Schema types the file uses, as Python reads them.
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}

# German spellings before the general rule: NFKD alone makes Jürgen "jurgen".
_SPELLED_OUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

SCHEMA = {
    "type": "object",
    "x-error": "The file holds one object: a name, people, and optionally a domain.",
    "required": ["name", "people"],
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string", "title": "Family name", "minLength": 2,
            "x-error": "The family name needs at least two letters.",
        },
        "domain": {
            "type": "string", "title": "Web address", "format": "hostname",
            "x-error": "The web address is a domain name such as example.com, "
                       "without https:// and without a path.",
        },
        "people": {
            "type": "array", "title": "People", "minItems": 1,
            "x-error": "People is a list with at least one person.",
            "items": {
                "type": "object",
                "x-error": "Each person is an object with at least a name.",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string", "title": "Name", "minLength": 1,
                        "x-error": "Every person needs a name.",
                    },
                    "relation": {
                        "type": "string", "title": "Relation", "enum": RELATIONS,
                        "x-error": "The relation is parent, child, grandparent or other.",
                    },
                    "words": {
                        "type": "string", "title": "Words", "enum": WORDS,
                        "x-error": "The words are she or he; leave them out for the name only.",
                    },
                    "looks_after": {
                        "type": "boolean", "title": "Looks after the server",
                        "x-error": "looks_after is true or false.",
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class Problem:
    """One reason a family file was refused.

    `field` is a dotted path into the file, list positions counted from 0
    (`people.2.relation`), so a screen can show the message at its field;
    "" is the file as a whole. `message` is a sentence that reads on its
    own beside that field.
    """
    field: str
    message: str


# ── Reading the file ─────────────────────────────────────────────────────

def load(text: str, name: str = "") -> tuple[dict | None, list[Problem]]:
    """A family file's content and everything wrong with it.

    TOML for a `.toml` name, JSON for a `.json` one; anything else (stdin)
    is read as JSON when it starts with `{`, else as TOML. The problems
    are the shape's and the rules', not the instance's: `set_family`
    adds those.
    """
    as_json = name.endswith(".json") or (not name.endswith(".toml") and text.lstrip().startswith("{"))
    try:
        data = json.loads(text) if as_json else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
        return None, [Problem("", f"The file is not valid {'JSON' if as_json else 'TOML'}: {e}")]
    return data, problems(data)


def problems(data) -> list[Problem]:
    """Everything wrong with a family file's content. Empty when it can be set."""
    return _check(SCHEMA, data, "", required=True) + _rules(data)


def _check(schema: dict, value, field: str, required: bool) -> list[Problem]:
    """The problems of `value` against `schema`, the subset the module header lists."""
    if isinstance(value, str) and not value.strip() and not required:
        return []
    kind = schema.get("type")
    if kind and not isinstance(value, _TYPES[kind]):
        return [Problem(field, schema["x-error"])]
    if isinstance(value, dict):
        return _check_object(schema, value, field)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return [Problem(field, schema["x-error"])]
        return [p for i, item in enumerate(value)
                for p in _check(schema["items"], item, _at(field, i), required=True)]
    if isinstance(value, str) and not _fits(schema, value.strip()):
        return [Problem(field, schema["x-error"])]
    return []


def _check_object(schema: dict, value: dict, field: str) -> list[Problem]:
    found = []
    props, needed = schema["properties"], schema.get("required", [])
    for key in needed:
        if key not in value:
            found.append(Problem(_at(field, key), props[key]["x-error"]))
    for key, item in value.items():
        if key in props:
            found += _check(props[key], item, _at(field, key), required=key in needed)
        elif schema.get("additionalProperties") is False:
            found.append(Problem(_at(field, key),
                                 f"{key} is not a field here. The fields are {_listed(list(props))}."))
    return found


def _fits(schema: dict, text: str) -> bool:
    if len(text) < schema.get("minLength", 0):
        return False
    if "enum" in schema and text not in schema["enum"]:
        return False
    return schema.get("format") != "hostname" or bool(HOSTNAME.match(text.lower()))




def _rules(data) -> list[Problem]:
    """What the shape cannot say: names and usernames are each taken once,
    and someone looks after the server. Checked on whatever people have a
    name, so a file with a bad field still hears about these too."""
    entries = data.get("people") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    found = []
    names: set[str] = set()
    usernames: dict[str, str] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        name = _clean(entry["name"])
        uid = username(name)
        if name.casefold() in names:
            found.append(Problem(f"people.{i}.name", "This name is in the list twice."))
        elif not USERNAME.match(uid):
            found.append(Problem(f"people.{i}.name", "This name gives no username: the first name "
                                                     "needs a letter from a to z or a digit."))
        elif uid in usernames:
            found.append(Problem(f"people.{i}.name", f"This name signs in as {uid}, like "
                                                     f"{usernames[uid]}. Give one of them another first name."))
        names.add(name.casefold())
        usernames.setdefault(uid, name)
    # Only a true or false names anyone; a wrong value is the shape's problem.
    said = [e["looks_after"] for e in entries if isinstance(e, dict) and isinstance(e.get("looks_after"), bool)]
    if said and not any(said):
        found.append(Problem("people", "Nobody looks after the server. Set looks_after "
                                       "to true for at least one person."))
    return found


def username(name: str) -> str:
    """The username for a person's name: the first name, lowercased, in a to z."""
    first = name.split()[0].lower() if name.split() else ""
    ascii_ = unicodedata.normalize("NFKD", first.translate(_SPELLED_OUT))
    return "".join(c for c in ascii_ if c.isascii() and (c.isalnum() or c in "._-"))


def people(data: dict) -> list[dict]:
    """The people of a valid file with the defaults in place: id, name,
    relation, words (None for the name only) and looks_after."""
    entries = data["people"]
    named = any("looks_after" in p for p in entries)
    return [{
        "id": username(_clean(p["name"])),
        "name": _clean(p["name"]),
        "relation": _given(p, "relation") or ("parent" if i < 2 else "child"),
        "words": _given(p, "words"),
        "looks_after": p.get("looks_after") is True if named else i == 0,
    } for i, p in enumerate(entries)]


def describe(found: list, data) -> list[str]:
    """Each problem as a line for the terminal, led by whom or what it is about."""
    lines = []
    for p in found:
        field, message = (p.field, p.message) if isinstance(p, Problem) else (p["field"], p["message"])
        lines.append(f"{_label(field, data)}: {message}")
    return lines


def _label(field: str, data) -> str:
    parts = field.split(".")
    if parts[0] == "people" and len(parts) > 1:
        i = int(parts[1])
        entry = data["people"][i] if isinstance(data, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        return _clean(name) if isinstance(name, str) and name.strip() else f"Person {i + 1}"
    if field:
        return SCHEMA["properties"].get(parts[0], {}).get("title", parts[0])
    return "The file"


# ── Reading the instance ─────────────────────────────────────────────────

def show(stck) -> dict:
    """What `stack family` prints: the name, the web address, and each person
    with id, name, relation, words, role and email. A key users.toml does
    not have reads as None (relation, words) or as today's default."""
    core = stck.config.get("core", {})
    return {
        "name": core.get("stack_owner", ""),
        "domain": core.get("family_domain", ""),
        "people": [{
            "id": user_id(u),
            "name": u.get("name", ""),
            "relation": u.get("relation"),
            "words": u.get("words"),
            "role": u.get("role", "member"),
            "email": u.get("email", ""),
        } for u in load_users(stck.instance_dir)],
    }


# ── Writing it ───────────────────────────────────────────────────────────

def set_family(stck, data) -> dict:
    """Write a family file's content into users.toml, stack.toml and the
    secrets, and return the family as `show` reads it, with `changed`
    naming the files written. `{"error", "problems"}` when it is refused,
    with nothing written."""
    found = problems(data)
    if found:
        return refusal(found)
    if not (stck.instance_dir / "stack.toml").exists():
        return refusal([Problem("", "There is no stack.toml here yet. "
                                    "`stack install --family <file> --yes` writes it.")])
    current, found = _users_toml(stck.instance_dir)
    if found:
        return refusal(found)
    chat_exists = stck.is_installed("messages")
    domain = (_given(data, "domain") or "").lower()
    users, found = _merge(people(data), current, domain, chat_exists)
    if found:
        return refusal(found)

    changed = []
    if users != current:
        (stck.instance_dir / "users.toml").write_text(users_toml(users))
        changed.append("users.toml")
    settings = _settings(stck.config, _clean(data["name"]), domain, chat_exists)
    for section, key, value in settings:
        stck._set_cfg(section, key, value)
    if settings:
        changed.append("stack.toml")
    without = [u for u in users if stck.secrets.get("global", password_key(u)) is None]
    for u in without:
        stck.secrets.set("global", password_key(u), user_id(u))
    if without:
        changed.append(".stack/secrets.toml")
    return {"ok": True, "changed": changed, **show(stck)}


def refusal(found: list[Problem]) -> dict:
    count = f"{len(found)} problem" + ("" if len(found) == 1 else "s")
    return {"error": f"Nothing was written: {count}", "problems": [asdict(p) for p in found]}


def _users_toml(root: Path) -> tuple[list[dict], list[Problem]]:
    """users.toml's people. A file that does not parse is refused rather than
    written over: it is the admin's, maybe half edited."""
    path = root / "users.toml"
    if not path.exists():
        return [], []
    try:
        return tomllib.loads(path.read_text()).get("users", []), []
    except tomllib.TOMLDecodeError as e:
        return [], [Problem("", f"users.toml does not parse ({e}). Fix it, or move it away, first.")]


def _merge(wanted: list[dict], current: list[dict], domain: str,
           chat_exists: bool) -> tuple[list[dict], list[Problem]]:
    """users.toml as it will be: the people of the file, each kept from
    `current` where they are already there (see the module header)."""
    by_name = {_clean(u.get("name", "")).casefold(): u for u in current}
    by_id = {user_id(u): u for u in current if u.get("id") or u.get("name")}
    users, found, kept = [], [], []
    for i, person in enumerate(wanted):
        old = by_name.get(person["name"].casefold()) or by_id.get(person["id"])
        if old is None:
            if chat_exists:
                found.append(Problem(f"people.{i}.name", _NEW_AFTER_CHAT.format(
                    key=password_key(person))))
            users.append(_ordered(_new_user(person, domain)))
            continue
        kept.append(old)
        if chat_exists and _clean(old.get("name", "")) != person["name"]:
            found.append(Problem(f"people.{i}.name", _RENAMED_AFTER_CHAT.format(old=old.get("name"))))
        users.append(_ordered(_kept_user(old, person)))
    if chat_exists:
        for u in current:
            if not any(u is k for k in kept):
                found.append(Problem("people", _REMOVED_AFTER_CHAT.format(name=u.get("name") or user_id(u))))
    return users, found


_NEW_AFTER_CHAT = (
    "The chat already exists, and adding people to it is not possible here yet. "
    "Add them to users.toml with the password global__{key} in .stack/secrets.toml, "
    "then run `stack messages setup`.")
_RENAMED_AFTER_CHAT = (
    "The chat already exists, and renaming people is not possible here yet "
    "(users.toml has {old}). Change the name in users.toml by hand.")
_REMOVED_AFTER_CHAT = (
    "{name} is not in the file. The chat already exists, and removing people is "
    "not possible here yet: remove them from users.toml by hand.")


def _new_user(person: dict, domain: str) -> dict:
    return {
        "id": person["id"],
        "name": person["name"],
        "email": f"{person['id']}@{domain or LOCAL_MAIL}",
        "role": "admin" if person["looks_after"] else "member",
        "relation": person["relation"],
        **({"words": person["words"]} if person["words"] else {}),
        "stacklets": list(NEW_PERSON_SERVICES),
    }


def _kept_user(old: dict, person: dict) -> dict:
    """`old` with what the file says about them. A role other than admin
    (`restricted`) survives as long as they do not look after the server."""
    entry = {**old, "id": user_id(old), "name": person["name"], "relation": person["relation"]}
    if person["looks_after"]:
        entry["role"] = "admin"
    elif old.get("role") in (None, "admin"):
        entry["role"] = "member"
    if person["words"]:
        entry["words"] = person["words"]
    else:
        entry.pop("words", None)
    return entry


def _settings(config: dict, name: str, domain: str, chat_exists: bool) -> list[tuple[str, str, str]]:
    """The stack.toml values that differ from what the file says, as
    (section, key, value). See the module header for the server name."""
    core = config.get("core", {})
    settings = []
    if core.get("stack_owner", "") != name:
        settings.append(("core", "stack_owner", name))
    if core.get("family_domain", "") != domain:
        settings.append(("core", "family_domain", domain))
    if not chat_exists and not config.get("messages", {}).get("server_name"):
        settings.append(("messages", "server_name", domain or sanitize_server_name(name)))
    return settings


def sanitize_server_name(name: str) -> str:
    """Turn a household name into a Matrix server name, as the installer does."""
    clean = "".join(c for c in name.lower() if c.isalnum() or c in "-_.")
    return clean or "home"


def users_toml(users: list[dict]) -> str:
    """users.toml for `users`. Comments are not kept: the file is written whole."""
    lines = [
        "# users.toml: who has accounts, one [[users]] table each.",
        "#",
        "# Written by `stack family set`, which keeps every key it does not",
        "# manage but not comments. Edit by hand, or set the whole list again.",
        "",
    ]
    for u in users:
        width = max(len(k) for k in u)
        lines.append("[[users]]")
        lines += [f"{key:<{width}} = {_toml(value)}" for key, value in u.items()]
        lines.append("")
    return "\n".join(lines)


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    # A JSON string is a valid TOML basic string, escapes included.
    return json.dumps(str(value), ensure_ascii=False)


# ── Small helpers ────────────────────────────────────────────────────────

def _ordered(entry: dict) -> dict:
    first = {k: entry[k] for k in USER_KEYS if k in entry}
    return {**first, **{k: v for k, v in entry.items() if k not in first}}


def _clean(text: str) -> str:
    return " ".join(text.split())


def _given(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _at(field: str, key) -> str:
    return f"{field}.{key}" if field else str(key)


def _listed(words: list[str]) -> str:
    return ", ".join(words[:-1]) + " and " + words[-1] if len(words) > 1 else "".join(words)
