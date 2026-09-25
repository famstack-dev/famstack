"""The family's admins are admins in every family room.

Synapse's server-admin flag grants no power inside a room, and the
`private_chat` preset gives power level 100 only to the account that
created the room, which is the tech admin. So every room the stack
creates needs its admin-role users promoted explicitly: the rooms
`setup` creates, the rooms `room create` makes later, and the bot rooms
the bot-runner creates, and `stack messages setup --room-admins` on every
start. Running it again changes nothing.
"""

from stack.users import user_id


def admin_user_ids(users):
    """Localparts of the admin-role users in users.toml."""
    return [user_id(u) for u in users if u.get("role") == "admin"]


def family_room_ids(client, space_id):
    """The family Space itself and every room it lists that the tech
    admin is still in. A deleted room can stay listed in the Space, and
    nobody manages a room nobody from the stack is in."""
    if not space_id:
        return []
    joined = client.joined_rooms()
    return [space_id, *(r for r in client.space_children(space_id) if r in joined)]


def ensure_admins(client, room_ids, admins, labels=None):
    """Give every admin power level 100 in every room. Returns one result
    per change or failure; a room already right says nothing."""
    labels = labels or {}
    results = []
    for rid in room_ids:
        for uid in admins:
            full = client._full_user(uid)
            outcome = client.ensure_user_power_level(rid, full, 100)
            if outcome == "ok":
                continue
            room = labels.get(rid) or client.room_name(rid)
            results.append({
                "ok": outcome == "set",
                "user": uid,
                "room": room,
                "item": f"{full} in {room}",
                "action": "promoted to PL 100" if outcome == "set"
                          else "could not promote (Synapse refused)",
            })
    return results


def summary(admins, room_count, results):
    """One line per admin, also when nothing changed, so every run says
    where each admin stands. `ok` is False when a room refused."""
    lines = []
    for uid in admins:
        made = [r["room"] for r in results if r["user"] == uid and r["ok"]]
        refused = [r["room"] for r in results if r["user"] == uid and not r["ok"]]
        if refused:
            lines.append({"ok": False, "line": f"{uid} could not be made admin in: {', '.join(refused)}"})
        elif made:
            lines.append({"ok": True, "line": f"{uid} is now admin in all {room_count} family rooms "
                                              f"(made admin in: {', '.join(made)})"})
        else:
            lines.append({"ok": True, "line": f"{uid} is already admin in all {room_count} family rooms"})
    return lines
