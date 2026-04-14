# Messages Admin Guide

Administration reference for the Matrix/Synapse messaging stack.

## CLI Commands

```bash
stack messages room list                                 # List all rooms
stack messages room create <alias> "Name" ["topic"]      # Create room in family Space
stack messages room delete <alias>                       # Delete a room (purges messages)
stack messages users                                     # List all users
stack messages join <room> <user> [user2 ...]            # Join users to a room
stack messages send <room> "message"                     # Send a message
stack messages setup                                     # Run initial setup (rooms, users, Space)
```

## Common Admin Tasks

All examples below use the `_matrix.py` client. Run from the famstack repo root:

```bash
python3 << 'EOF'
import sys; sys.path.insert(0, 'stacklets/messages/cli'); sys.path.insert(0, 'lib')
from _matrix import MatrixClient, _get, _put, _api
client = MatrixClient('http://localhost:42031', 'example.com', '.')
client.login('admin', 'admin')  # use a Synapse admin account

# ... commands below ...
EOF
```

### Rooms

**Create a room:**
```python
room_id = client.create_room('room-alias', name='Room Name', topic='Description')
```

**Delete a room (purge all messages):**
```python
room_id = client.resolve_room('room-alias')
_api('DELETE', f'http://localhost:42031/_synapse/admin/v2/rooms/{room_id}',
     {'purge': True}, token=client.token)
```

**Rename a room:**
```python
room_id = client.resolve_room('room-alias')
_put(f'http://localhost:42031/_matrix/client/v3/rooms/{room_id}/state/m.room.name',
     {'name': 'New Name'}, token=client.token)
```

**Add a second alias to a room:**
```python
room_id = client.resolve_room('existing-alias')
_put(f'http://localhost:42031/_matrix/client/v3/directory/room/%23new-alias%3aexample.com',
     {'room_id': room_id}, token=client.token)
```

**Add a room to a Space:**
```python
client.add_space_child(space_id, room_id)
```

### Users

**Create a user:**
```python
client.create_user('username', 'password', displayname='Display Name', admin=False)
```

**List all users:**
```python
for u in client.list_users():
    print(u['name'], 'admin' if u.get('admin') else '')
```

**Promote user to Synapse admin (via database):**
```bash
docker exec stack-messages-db psql -U synapse -d synapse \
  -c "UPDATE users SET admin=1 WHERE name='@username:example.com';"
docker restart stack-messages-synapse
```

**Join a user to a room:**
```python
room_id = client.resolve_room('room-alias')
client.join_user(room_id, 'username')
```

**Update a user's password:**
```python
# create_user with PUT is idempotent — updates existing users
client.create_user('username', 'new-password', displayname='Name')
```

### Bot Accounts

Bot passwords are in `.stack/secrets.toml`. The bot runner (core stacklet)
creates Matrix accounts automatically on `stack up core`. To manually
create or reset a bot account:

```python
client.create_user('stacker-bot', '<password-from-secrets>', displayname='Stacker')
client.join_user(room_id, 'stacker-bot')
```

### Useful Direct API Calls

**Server version:**
```bash
curl -s http://localhost:42031/_matrix/client/versions
```

**Room details (admin):**
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:42031/_synapse/admin/v1/rooms/<room_id>
```

**Deactivate a user (admin):**
```python
_api('POST', f'http://localhost:42031/_synapse/admin/v1/deactivate/@user:example.com',
     {'erase': False}, token=client.token)
```
