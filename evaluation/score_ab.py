from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


DIMENSIONS = ("relevance", "interesting", "evidence", "actionability", "diversity")


def summarize(payload: dict) -> dict:
    evaluations = payload.get("evaluations", [])
    if not isinstance(evaluations, list) or not evaluations:
        raise ValueError("evaluations must be a non-empty array")
    differences = {name: [] for name in DIMENSIONS}
    candidate_wins = 0
    for item in evaluations:
        if item.get("preferred") not in {"baseline", "candidate", "tie"}:
            raise ValueError("preferred must be baseline, candidate, or tie")
        candidate_wins += item["preferred"] == "candidate"
        for name in DIMENSIONS:
            baseline = float(item["baseline"][name])
            candidate = float(item["candidate"][name])
            if not 1 <= baseline <= 5 or not 1 <= candidate <= 5:
                raise ValueError(f"{name} scores must be from 1 to 5")
            differences[name].append(candidate - baseline)
    medians = {name: statistics.median(values) for name, values in differences.items()}
    win_rate = candidate_wins / len(evaluations)
    passed = (
        len(evaluations) == 8 and win_rate >= .6 and medians["evidence"] >= .5
        and medians["relevance"] >= 0 and medians["diversity"] >= 0
    )
    return {
        "prompt_count": len(evaluations), "candidate_win_rate": round(win_rate, 3),
        "median_differences": medians, "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1:
        print("usage: python evaluation/score_ab.py RATINGS.json", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        result = summarize(payload)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
