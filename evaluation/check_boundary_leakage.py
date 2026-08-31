from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLDOUT = ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json"
TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")


def normalized(value: Any) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def holdout_terms(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    terms: dict[str, list[str]] = {}
    for case in payload.get("cases") or []:
        case_id = str(case.get("id") or "")
        for field in ("acceptable_new_mechanisms", "cross_mechanism_directions"):
            for concept in case.get(field) or []:
                for raw in [concept.get("term"), *(concept.get("aliases") or [])]:
                    key = normalized(raw)
                    if key:
                        terms.setdefault(key, []).append(
                            f"{case_id}.{field}.{concept.get('id')}"
                        )
    return terms


def find_leaks(path: Path = DEFAULT_HOLDOUT) -> list[dict[str, Any]]:
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from muse_shroom.boundary import DISCOVERY_PHRASE_HINTS

    expected = holdout_terms(path)
    hints = {normalized(value): str(value) for value in DISCOVERY_PHRASE_HINTS}
    return [
        {"term": hints[key], "normalized": key, "holdout_sources": expected[key]}
        for key in sorted(hints.keys() & expected.keys())
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check production discovery phrase hints against holdout answers",
    )
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    args = parser.parse_args(argv)
    try:
        leaks = find_leaks(args.holdout)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": not leaks, "leaks": leaks}, ensure_ascii=False, indent=2))
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
