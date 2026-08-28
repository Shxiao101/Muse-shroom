from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


DIMENSIONS = ("relevance", "interesting", "evidence", "actionability", "diversity")


def reveal(payload: dict, key_payload: dict) -> dict:
    mappings = key_payload.get("mappings", {})
    revealed = []
    for item in payload.get("evaluations", []):
        prompt_id = str(item.get("prompt_id", ""))
        mapping = mappings.get(prompt_id)
        if not isinstance(mapping, dict) or set(mapping) != {"A", "B"}:
            raise ValueError(f"missing blind mapping for {prompt_id}")
        if item.get("preferred") not in {"A", "B", "tie"}:
            raise ValueError("blind preferred must be A, B, or tie")
        by_version = {mapping[label]: item[label] for label in ("A", "B")}
        preferred = "tie" if item["preferred"] == "tie" else mapping[item["preferred"]]
        revealed.append({
            "prompt_id": prompt_id, "preferred": preferred,
            "baseline": by_version["baseline"], "candidate": by_version["candidate"],
        })
    return {"evaluations": revealed}


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
    parser = argparse.ArgumentParser(description="Score a Muse-shroom blind A/B evaluation")
    parser.add_argument("ratings", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.ratings.read_text(encoding="utf-8"))
        if args.key:
            key_payload = json.loads(args.key.read_text(encoding="utf-8"))
            payload = reveal(payload, key_payload)
        result = summarize(payload)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
