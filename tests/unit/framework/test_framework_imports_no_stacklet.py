"""The framework in `lib/stack` imports no stacklet.

A stacklet imports the framework, never the other way, because the
framework runs where a stacklet's code may be absent: a stacklet that
shares a reader with another puts it in `lib/stack`.
"""

import ast
from pathlib import Path

LIB = Path(__file__).resolve().parents[3] / "lib" / "stack"


def _stacklet_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        found += [f"{path.relative_to(LIB)}:{node.lineno} {n}"
                  for n in names if n == "stacklets" or n.startswith("stacklets.")]
    return found


def test_nothing_in_the_framework_imports_a_stacklet():
    offenders = [hit for path in sorted(LIB.rglob("*.py")) for hit in _stacklet_imports(path)]
    assert offenders == []
