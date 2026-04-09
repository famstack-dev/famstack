# ADR-007: 42xxx Port Convention

## Status
Accepted

## Context
Each stacklet exposes one or more services on the LAN. Without a convention, port collisions are inevitable as stacklets are added. Common ports (8080, 3000, 5000) conflict with other software people run on their Macs.

## Decision
All famstack services use ports in the `42000-42999` range. Each stacklet declares its port in `stacklet.toml`. The range is high enough to avoid conflicts with common services and low enough to be memorable.

Current allocations:
- 42000-42009: core (Caddy, API)
- 42010-42019: photos (Immich)
- 42020-42029: docs (Paperless)
- 42030-42039: messages (Element, Synapse)
- 42040-42049: chatai (Open WebUI)
- 42050-42059: code (Forgejo)
- 42060-42069: ai (oMLX, Whisper, TTS)

## Consequences
- New stacklets pick the next available block of 10
- Port numbers are predictable and documented
- No conflicts with typical dev servers, databases, or other self-hosted software
- The range is large enough for dozens of stacklets
