"""A stacklet listing and a family for the dashboard's surface tests.

`listing` is what `stack list --json` answers for a throwaway instance in
which some stacklets were set up and run, as the real `Stack.list()`
reports it over the repo's own stacklets, with Docker stood in for.
`FAMILY` is what `stack family --json` answers for the Simpsons.

    answer = listing(tmp_path, monkeypatch, running=("core", "messages"))
    monkeypatch.setattr(client, "call", lambda request, timeout: answer)
"""

from __future__ import annotations

import json

from .conftest import REPO

__all__ = ["FAMILY", "listing"]

FAMILY = {"name": "Simpson", "domain": "", "language": "en", "people": [
    {"id": "homer", "name": "Homer Simpson", "relation": "parent", "words": "he",
     "role": "admin", "email": "homer@home.local"},
    {"id": "marge", "name": "Marge Simpson", "relation": "parent", "words": "she",
     "role": "member", "email": "marge@home.local"},
    {"id": "bart", "name": "Bart Simpson", "relation": "child", "words": "he",
     "role": "member", "email": "bart@home.local"},
    {"id": "lisa", "name": "Lisa Simpson", "relation": "child", "words": "she",
     "role": "member", "email": "lisa@home.local"},
]}


def listing(tmp_path, monkeypatch, running: tuple[str, ...], server_name: str = "simpson",
            others: tuple[str, ...] = (), domain: str = "", ai_url: str = "") -> dict:
    """What `stack list --json` answers for an instance where `running`
    were set up and run, as the host API hands it over. `others` run on
    this Mac for another instance: Docker knows them by the same names.
    `domain` puts it in domain mode, with certificates. `ai_url` is an AI
    server the stack uses and does not manage (`stack ai switch <url>`)."""
    import stack.docker
    from stack import Stack

    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n'
        + (f'domain = "{domain}"\ndns_provider = "cloudflare"\n' if domain else "")
        + f'\n[messages]\nserver_name = "{server_name}"\n'
        + (f'\n[ai]\nprovider = "external"\nopenai_url = "{ai_url}"\n' if ai_url else ""))
    (tmp_path / ".stack").mkdir(exist_ok=True)
    for sid in running:
        (tmp_path / ".stack" / f"{sid}.setup-done").touch()
    stck = Stack(REPO, tmp_path / "data", instance_dir=tmp_path)
    with monkeypatch.context() as docker:
        docker.setattr(stack.docker, "project_states",
                       lambda: {sid: "running" for sid in (*running, *others)})
        docker.setattr(stack.docker, "check_health", lambda url, headers=None: True)
        answer = stck.list()
    return json.loads(json.dumps(answer, default=str))
