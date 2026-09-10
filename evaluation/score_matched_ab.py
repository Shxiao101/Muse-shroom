"""Score the matched real-need A/B against the threshold registered in
`evaluation/ab-protocol.md`.

The unit of evidence is the need: each need's repetitions are pooled into one
need-level verdict by majority, `tie` never counts as a win, and the release
gate is stated over needs (at least 8 needs, Muse-shroom preferred in at least
6 verdicts). Stability and claim traceability are reported next to the verdict
and are never folded into it. Fewer than three rated repetitions per need is a
pilot: the gate lives here in code, so such a run can never read as a release
decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ARMS = ("muse-shroom", "direct")
RELEASE_MIN_REPS = 3
RELEASE_MIN_NEEDS = 8
RELEASE_MIN_WINS = 6


def _mapping_for(
    mappings: dict[str, Any], need_id: str, repetition: int,
) -> dict[str, str]:
    """Look up the A/B mapping for one (need_id, repetition).

    The key is nested `{need_id: {str(repetition): {A, B}}}`. A need-level
    `{A, B}` object (the 1-rep pilot shape) is not a mapping for any
    repetition — looking it up by need_id alone is how the three reps of a
    need would leak their assignment after the first rating.
    """
    by_need = mappings.get(need_id)
    if not isinstance(by_need, dict):
        raise ValueError(
            f"missing blind mapping for {need_id or 'an unnamed need'} repetition {repetition}"
        )
    mapping = by_need.get(str(repetition))
    if mapping is None:
        mapping = by_need.get(repetition)
    if not isinstance(mapping, dict) or set(mapping) != {"A", "B"}:
        raise ValueError(
            f"missing blind mapping for {need_id or 'an unnamed need'} repetition {repetition}"
        )
    if set(mapping.values()) != set(ARMS):
        raise ValueError(f"blind mapping for {need_id} repetition {repetition} must name both arms")
    return mapping


def reveal(payload: dict[str, Any], key_payload: dict[str, Any]) -> dict[str, Any]:
    """Translate blind A/B ratings into arm names using `blind-key.json`.

    Un-blinding by hand is where a transcription slip or hindsight would enter,
    so the mapping is applied mechanically. Only the labels change: rows stay
    keyed by need and repetition, and any per-dimension score block is carried
    over under the arm it actually belonged to.
    """
    mappings = key_payload.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise ValueError("blind key must carry a non-empty mappings object")
    revealed: list[dict[str, Any]] = []
    for item in payload.get("evaluations") or []:
        if not isinstance(item, dict):
            raise ValueError("each evaluation must be an object")
        need_id = str(item.get("need_id") or "")
        repetition = item.get("repetition")
        if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
            raise ValueError(
                f"evaluation repetition must be a positive integer for {need_id or 'an unnamed need'}"
            )
        mapping = _mapping_for(mappings, need_id, repetition)
        preferred = item.get("preferred")
        if preferred not in {"A", "B", "tie"}:
            raise ValueError("blind preferred must be A, B, or tie")
        entry: dict[str, Any] = {
            "need_id": need_id,
            "repetition": repetition,
            "preferred": "tie" if preferred == "tie" else mapping[preferred],
        }
        for label in ("A", "B"):
            if label in item:
                entry[mapping[label]] = item[label]
        revealed.append(entry)
    return {"evaluations": revealed}


def need_verdicts(evaluations: list[dict[str, Any]]) -> tuple[dict[str, str], int]:
    """Pool each need's repetitions into one verdict; returns the verdicts and
    the repetition count every need must share.

    Coverage must be complete: every need carries the same number of rows,
    numbered 1..repetitions, with no duplicates — a missing run must not be
    averaged away.
    """
    by_need: dict[str, dict[int, str]] = {}
    for item in evaluations:
        if not isinstance(item, dict):
            raise ValueError("each evaluation must be an object")
        need_id = str(item.get("need_id") or "")
        if not need_id:
            raise ValueError("evaluation need_id is required")
        repetition = item.get("repetition")
        if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
            raise ValueError(f"evaluation repetition must be a positive integer for {need_id}")
        preferred = item.get("preferred")
        if preferred not in (*ARMS, "tie"):
            raise ValueError("preferred must be muse-shroom, direct, or tie")
        rows = by_need.setdefault(need_id, {})
        if repetition in rows:
            raise ValueError(f"duplicate rating for {need_id} repetition {repetition}")
        rows[repetition] = preferred
    if len({len(rows) for rows in by_need.values()}) != 1:
        raise ValueError("every need must have the same number of rated repetitions")
    reps = len(next(iter(by_need.values())))
    for need_id, rows in by_need.items():
        if sorted(rows) != list(range(1, reps + 1)):
            raise ValueError(f"repetitions must cover 1..{reps} for {need_id}")
    verdicts: dict[str, str] = {}
    for need_id, rows in by_need.items():
        preferences = [rows[round] for round in range(1, reps + 1)]
        muse_shroom = preferences.count("muse-shroom")
        direct = preferences.count("direct")
        if muse_shroom > reps / 2:
            verdicts[need_id] = "muse-shroom"
        elif direct > reps / 2:
            verdicts[need_id] = "direct"
        else:
            verdicts[need_id] = "tie"
    return verdicts, reps


def stability(preferences_by_need: dict[str, list[str]]) -> dict[str, Any]:
    """Count how often each need flipped between its consecutive repetitions."""
    per_need = {
        need_id: sum(first != second for first, second in zip(preferences, preferences[1:]))
        for need_id, preferences in preferences_by_need.items()
    }
    return {
        "per_need": {need_id: per_need[need_id] for need_id in sorted(per_need)},
        "needs_with_flips": sum(flips > 0 for flips in per_need.values()),
    }


def _preferences_by_need(evaluations: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Group ratings by need in repetition order; call after `need_verdicts` has
    validated coverage."""
    rows_by_need: dict[str, dict[int, str]] = {}
    for item in evaluations:
        rows_by_need.setdefault(str(item["need_id"]), {})[item["repetition"]] = item["preferred"]
    return {
        need_id: [rows[round] for round in sorted(rows)]
        for need_id, rows in rows_by_need.items()
    }


def summarize(
    ratings: dict[str, Any], claim_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evaluations = ratings.get("evaluations")
    if not isinstance(evaluations, list) or not evaluations:
        raise ValueError("evaluations must be a non-empty array")
    verdicts, reps = need_verdicts(evaluations)
    wins = {
        name: sum(verdict == name for verdict in verdicts.values())
        for name in (*ARMS, "tie")
    }
    if reps < RELEASE_MIN_REPS:
        mode, release_eligible, verdict, passed = "pilot", False, "not_measured", False
    elif len(verdicts) < RELEASE_MIN_NEEDS:
        mode, release_eligible, verdict, passed = "release", False, "insufficient_data", False
    else:
        mode, release_eligible = "release", True
        verdict, passed = ("pass", True) if wins["muse-shroom"] >= RELEASE_MIN_WINS else ("fail", False)
    traceability: dict[str, dict[str, int]] = {}
    for report in claim_reports or []:
        arm = str(report.get("arm") or "")
        if not arm:
            raise ValueError("claim report must name its arm")
        bucket = traceability.setdefault(arm, {"checked": 0, "passed": 0, "failed": 0})
        for field in ("checked", "passed", "failed"):
            bucket[field] += int(report.get(field) or 0)
    return {
        "schema_version": 1,
        "mode": mode,
        "repetitions": reps,
        "needs": len(verdicts),
        "threshold": {"needs": RELEASE_MIN_NEEDS, "muse_shroom_wins": RELEASE_MIN_WINS},
        "wins": wins,
        "need_verdicts": {need_id: verdicts[need_id] for need_id in sorted(verdicts)},
        "stability": stability(_preferences_by_need(evaluations)),
        "claim_traceability": traceability,
        "release_eligible": release_eligible,
        "verdict": verdict,
        "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the matched real-need A/B against the registered protocol threshold",
    )
    parser.add_argument("ratings", type=Path)
    parser.add_argument(
        "--key", type=Path,
        help="blind-key.json; supply it to un-blind A/B ratings before scoring",
    )
    parser.add_argument("--claim-report", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.ratings.read_text(encoding="utf-8"))
        if args.key:
            payload = reveal(payload, json.loads(args.key.read_text(encoding="utf-8")))
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.claim_report]
        result = summarize(payload, reports)
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
