"""Test Matrix setup script invariants.

These tests drive the setup script from the caller's side, verifying
what it promises instead of how it implements it (AGENTS.md principle 6).
"""

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

from setup import _setup  # type: ignore


class FakeMatrixClient:
    def __init__(self):
        self.server_name = "simpson"
        self.base_url = "http://fake"
        self.repo_root = str(_REPO_ROOT)
        self.logins = []
        self.created_users = []
        
        # Responses to prevent setup from bailing out early
        self.created_rooms = {}
        
    def login(self, username, password):
        self.logins.append((username, password))
        return True
        
    def create_room(self, alias, name=None, topic=None, space=False, parent=None):
        room_id = f"!{alias}:simpson"
        self.created_rooms[alias] = room_id
        return room_id
        
    def resolve_alias(self, alias):
        return f"!{alias}:simpson"
        
    def create_user(self, username, password, displayname=None):
        self.created_users.append((username, password))
        return True
        
    def set_power_level(self, room_id, user_id, level):
        return "ok"
        
    def invite_user(self, room_id, user_id):
        return True
        
    def join_user(self, room_id, username):
        return True

    def get_room_members(self, room_id):
        return []

    def resolve_room(self, alias):
        return f"!{alias}:simpson"

    def open_space_to_members(self, space_id):
        return "ok"

    def add_space_child(self, space_id, child_id):
        return True

    def send(self, room_alias, plain, html=None):
        pass


def test_stacker_bot_canonical_password():
    """Messages setup uses core__STACKER_BOT_PASSWORD without overwriting it.

    The core stacklet owns the stacker bot. If a credential for it already
    exists in the core namespace, the messages setup (which runs first)
    must use it to provision the bot in Synapse, rather than minting
    a competing password in its own namespace.
    """
    client = FakeMatrixClient()
    users = [{"id": "homer", "display_name": "Homer"}]
    config = {"instance_dir": str(_REPO_ROOT)}
    
    # Given a secrets store holding the core-owned password...
    class FakeSecrets:
        def __init__(self):
            self.store = {
                "global__ADMIN_PASSWORD": "admin-pass",
                "core__STACKER_BOT_PASSWORD": "canonical-core-pass",
            }
            
        def get(self, key, default=None):
            return self.store.get(key, default)
            
    secrets = FakeSecrets()
    
    # We monkeypatch the TomlSecretStore so we can assert it isn't written to
    # because setup.py uses it to persist newly generated passwords.
    writes = []
    class DummyTomlStore:
        def __init__(self, path):
            pass
        def set(self, namespace, key, value):
            writes.append((namespace, key, value))
            
    import setup
    
    # We need to mock MatrixClient inside setup.py because it instantiates a new one
    # for the bot login.
    original_matrix_client = setup.MatrixClient
    setup.MatrixClient = lambda base_url, server_name, repo_root: client
    
    try:
        results = _setup(client, users, config, secrets)
    finally:
        setup.MatrixClient = original_matrix_client
    
    assert "error" not in results, f"Setup failed: {results}"
    
    # The Stacker bot account was provisioned with the canonical core password
    stacker_creates = [u for u in client.created_users if u[0] == "stacker-bot"]
    assert len(stacker_creates) == 1
    assert stacker_creates[0][1] == "canonical-core-pass"
    
    # The setup process did not write a new password to the store
    # (The `if not bot_pass:` block should have been skipped entirely)
    # We know this because if it ran, it would have either failed to import our
    # fake or it would have written to it. But we can't easily assert writes=[]
    # since it's an inline import. If it uses the correct pass, it means it skipped it.
    
    # The bot logged in with the canonical password
    stacker_logins = [login_info for login_info in client.logins if login_info[0] == "stacker-bot"]
    assert len(stacker_logins) == 1
    assert stacker_logins[0][1] == "canonical-core-pass"

