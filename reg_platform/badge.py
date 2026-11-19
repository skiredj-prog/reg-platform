#!/usr/bin/env python3
"""Render configuration-state badges from a verified projection.

A badge states what the catalogue knows about one REG+ configuration. It
never says that a product conforms. Badges are only rendered from a
projection whose guards passed and whose content hash checks out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .project import content_hash

# Label, right-hand colour. Colours pass 4.5:1 with white text.
STATES = {
    "demonstrated": ("démontré", "#157a5e"),
    "partial": ("partiel", "#8a5a00"),
    "not_evaluated": ("non évalué", "#4b5563"),
    "refuted": ("réfuté", "#a1262d"),
    "inadmissible": ("non admissible", "#374151"),
}
# English labels, written to badges/en/ with the same colours.
STATES_EN = {
    "demonstrated": "demonstrated",
    "partial": "partial",
    "not_evaluated": "not evaluated",
    "refuted": "refuted",
    "inadmissible": "not admissible",
}
LEFT = "REG+ config"
LEFT_COLOR = "#13212c"


def text_width(s: str) -> int:
    # Verdana 11px approximation, enough for a static badge.
    return int(sum(7.2 if c.isupper() else 6.1 for c in s)) + 12


def svg(state: str, title: str, lang: str = "fr") -> str:
    label, color = STATES[state]
    if lang == "en":
        label = STATES_EN[state]
    lw, rw = text_width(LEFT), text_width(label)
    w = lw + rw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" '
        f'aria-label="{escape(LEFT)}: {escape(label)}">'
        f'<title>{escape(title)}</title>'
        f'<rect width="{lw}" height="20" fill="{LEFT_COLOR}"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2}" y="14" text-anchor="middle">{escape(LEFT)}</text>'
        f'<text x="{lw + rw / 2}" y="14" text-anchor="middle">{escape(label)}</text>'
        f'</g></svg>\n'
    )


def load_verified(path: Path) -> dict:
    p = json.loads(path.read_text(encoding="utf-8"))
    if p.get("contract") != "reg-projection" or not str(p.get("contract_version", "")).startswith("1."):
        raise SystemExit(f"{path}: not a reg-projection v1")
    if content_hash(p) != p.get("content_sha256"):
        raise SystemExit(f"{path}: content_sha256 does not match the content")
    if not p["guards"]["passed"]:
        raise SystemExit(f"{path}: projection guards failed; no badge is rendered")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projection", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    p = load_verified(args.projection)
    commit = p["source"]["commit"][:12]
    n = 0
    for lang, out in (("fr", args.out), ("en", args.out / "en")):
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.svg"):
            old.unlink()
        name = (lambda s: STATES[s][0]) if lang == "fr" else (lambda s: STATES_EN[s])
        for state in STATES:
            (out / f"state-{state}.svg").write_text(
                svg(state, f"REG+ configuration state: {name(state)}", lang), encoding="utf-8")
            n += 1
        for c in p["configurations"]:
            if not c["admissible_structural"]:
                continue
            title = (f"REG+ {c['id']}: {name(c['state'])} "
                     f"(reg-conformance {commit}). Not a conformance claim.")
            (out / f"{c['id']}.svg").write_text(svg(c["state"], title, lang), encoding="utf-8")
            n += 1
    print(f"{n} badges written to {args.out} and {args.out / 'en'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
