#!/usr/bin/env bash
# Bump the famstack version everywhere.
# Usage: ./scripts/bump-version.sh 0.3.0

set -euo pipefail

NEW_VERSION="${1:-}"
if [[ -z "$NEW_VERSION" ]]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.3.0"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Product version
sed -i '' "s/^VERSION = \".*\"/VERSION = \"$NEW_VERSION\"/" "$REPO_ROOT/lib/stack/cli.py"

# All stacklet versions
for f in "$REPO_ROOT"/stacklets/*/stacklet.toml; do
    sed -i '' "s/^version[[:space:]]*= \".*\"/version     = \"$NEW_VERSION\"/" "$f"
done

# Tools server
sed -i '' "s/version=\".*\",/version=\"$NEW_VERSION\",/" "$REPO_ROOT/stacklets/core/tools-server/server.py"

# README badge
sed -i '' "s/version-[0-9]*\.[0-9]*\.[0-9]*/version-$NEW_VERSION/" "$REPO_ROOT/README.md"

echo "Bumped to $NEW_VERSION"
grep -rn "$NEW_VERSION" \
    "$REPO_ROOT/lib/stack/cli.py" \
    "$REPO_ROOT"/stacklets/*/stacklet.toml \
    "$REPO_ROOT/stacklets/core/tools-server/server.py" \
    "$REPO_ROOT/README.md" \
    | head -20
