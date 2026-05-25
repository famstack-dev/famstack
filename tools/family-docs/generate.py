#!/usr/bin/env python3
"""generate.py — render the demo family-document set from specs.

    uv run --extra demo python tools/family-docs/generate.py
    uv run --extra demo python tools/family-docs/generate.py --list
    uv run --extra demo python tools/family-docs/generate.py --only payslip

Reads ``specs/<locale>/*.yaml`` and writes rendered PDFs/PNGs to
``out/<locale>/``. The output directory is gitignored — the specs are
the source of truth, the rendered files are disposable artifacts you
regenerate (and then feed into the stack via the Matrix ingestion step).

Adding a document is "drop one YAML in specs/<locale>/"; see README.md
for the spec schema. Adding a language is "create specs/<locale>/".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import renderers  # noqa: E402  (needs the sys.path insert above)


def _specs_dir(locale: str) -> Path:
    return HERE / "specs" / locale


def _load_specs(locale: str, only: str | None) -> list[tuple[Path, dict]]:
    """Load every spec for ``locale`` (optionally filtered by ``only``)."""
    specs_dir = _specs_dir(locale)
    if not specs_dir.is_dir():
        sys.exit(f"no specs for locale {locale!r} — expected {specs_dir}")
    out = []
    for path in sorted(specs_dir.glob("*.yaml")):
        if only and only not in path.stem:
            continue
        spec = yaml.safe_load(path.read_text())
        spec.setdefault("filename", f"{path.stem}.pdf")
        out.append((path, spec))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Render the demo family-document set.")
    ap.add_argument("--locale", default="en", help="spec locale subfolder (default: en)")
    ap.add_argument("--only", help="render only specs whose name contains this substring")
    ap.add_argument("--out", help="output directory (default: out/<locale>)")
    ap.add_argument("--list", action="store_true", help="list specs and exit")
    args = ap.parse_args(argv)

    specs = _load_specs(args.locale, args.only)
    if not specs:
        sys.exit("no matching specs")

    if args.list:
        for path, spec in specs:
            exp = spec.get("expected", {})
            tail = f"  [{exp.get('document_type', '?')}]" if exp else ""
            print(f"  {path.stem:<28} {spec.get('render', '?'):<8} → {spec['filename']}{tail}")
        return 0

    out_dir = Path(args.out) if args.out else HERE / "out" / args.locale
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered = 0
    for path, spec in specs:
        try:
            filename, data = renderers.render(spec)
        except Exception as e:  # one bad spec shouldn't sink the batch
            print(f"  ✗ {path.name}: {e}", file=sys.stderr)
            continue
        (out_dir / filename).write_bytes(data)
        print(f"  ✓ {filename:<34} {len(data) // 1024:>4} KB")
        rendered += 1

    print(f"\n{rendered}/{len(specs)} documents → {out_dir}")
    return 0 if rendered == len(specs) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
