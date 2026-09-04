from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLDOUT = ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json"
DEFAULT_DEVELOPMENT = ROOT / "evaluation" / "boundary-golden-cases.json"
SKILL_ROOT = ROOT / "skills" / "muse-shroom"
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


def cross_mechanism_terms(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    terms: dict[str, list[str]] = {}
    for case in payload.get("cases") or []:
        case_id = str(case.get("id") or "")
        for concept in case.get("cross_mechanism_directions") or []:
            for raw in [concept.get("term"), *(concept.get("aliases") or [])]:
                key = normalized(raw)
                if key:
                    terms.setdefault(key, []).append(
                        f"{case_id}.cross_mechanism_directions.{concept.get('id')}"
                    )
    return terms


def skill_files() -> list[Path]:
    files = [SKILL_ROOT / "SKILL.md"]
    references = SKILL_ROOT / "references"
    if references.is_dir():
        files.extend(sorted(references.glob("*.md")))
    return [path for path in files if path.exists()]


def find_skill_leaks(expected: dict[str, list[str]]) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for path in skill_files():
        text = normalized(path.read_text(encoding="utf-8"))
        padded = f" {text} "
        for key, sources in expected.items():
            if not key or len(key) < 5:
                continue
            if f" {key} " in padded:
                leaks.append({
                    "term": key,
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "sources": sources,
                })
    return leaks


def find_leaks(path: Path = DEFAULT_HOLDOUT) -> list[dict[str, Any]]:
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from muse_shroom.boundary import PACKAGING_HEADS, PACKAGING_PHRASES

    expected = holdout_terms(path)
    hints = {
        normalized(value): str(value)
        for value in PACKAGING_HEADS | PACKAGING_PHRASES
    }
    return [
        {"term": hints[key], "normalized": key, "holdout_sources": expected[key]}
        for key in sorted(hints.keys() & expected.keys())
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check production discovery phrase hints and Skill text against Golden answers",
    )
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--development", type=Path, default=DEFAULT_DEVELOPMENT)
    args = parser.parse_args(argv)
    try:
        hint_leaks = find_leaks(args.holdout)
        skill_expected = {}
        for source in (args.development, args.holdout):
            for key, values in cross_mechanism_terms(source).items():
                skill_expected.setdefault(key, []).extend(values)
        skill_leaks = find_skill_leaks(skill_expected)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False))
        return 2
    ok = not hint_leaks and not skill_leaks
    print(json.dumps({
        "ok": ok,
        "leaks": hint_leaks,
        "skill_leaks": skill_leaks,
    }, ensure_ascii=False, indent=2))
    return 1 if not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
